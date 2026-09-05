"""
PAYSHIELD — full-stack integration test (port of
scripts/verify-integration.ts)

This is the "actually run it" verification the other test files can't
provide: they each test ONE pure module in isolation. This test drives
the REAL, unmodified FastAPI route handlers (webhook ingestion, demo
injection, the cron detection cycle, dashboard state, demo reset) via
FastAPI's TestClient, backed by an in-memory fake Supabase client
(tests/fake_supabase.py) that enforces the same unique constraints
supabase/schema.sql declares.

No live Supabase/Razorpay credentials are used or required. This does
NOT replace real Razorpay/Supabase verification - it proves the
application's OWN logic is correct end-to-end, which static reading
and single-module unit tests cannot.
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_webhook_secret"
os.environ["CRON_SECRET"] = "test_cron_secret"
# GROQ_API_KEY intentionally left UNSET - this run exercises the
# templated-fallback alert path on purpose.

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.supabase_client import set_supabase_client_for_testing  # noqa: E402
from tests.fake_supabase import FakeSupabaseClient  # noqa: E402

get_settings.cache_clear()

fake_client = FakeSupabaseClient()
set_supabase_client_for_testing(fake_client)

from app.main import app  # noqa: E402 - imported AFTER the fake client is installed

client = TestClient(app)


def _sign(body: bytes) -> str:
    return hmac.new(b"test_webhook_secret", body, hashlib.sha256).hexdigest()


def _payment_failed_payload(payment_id: str) -> bytes:
    return json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": f"order_{payment_id}",
                        "method": "upi",
                        "amount": 25000,
                        "currency": "INR",
                        "created_at": int(time.time()),
                        "bank": None,
                        "vpa": "customer@okhdfcbank",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_description": "Payment failed due to insufficient funds in the customer's account.",
                        "error_source": "customer",
                        "error_step": "payment_authorization",
                        "error_reason": "insufficient_funds",
                    }
                }
            },
        }
    ).encode()


def _payment_captured_payload(payment_id: str) -> bytes:
    return json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": f"order_{payment_id}",
                        "method": "upi",
                        "amount": 25000,
                        "currency": "INR",
                        "created_at": int(time.time()),
                        "bank": None,
                        "vpa": "customer@okhdfcbank",
                        "error_code": None,
                        "error_description": None,
                        "error_source": None,
                        "error_step": None,
                        "error_reason": None,
                    }
                }
            },
        }
    ).encode()


def _webhook_headers(body: bytes, event_id: str | None = "evt_default", bad_signature: bool = False) -> dict:
    headers = {
        "content-type": "application/json",
        "x-razorpay-signature": "0" * 64 if bad_signature else _sign(body),
    }
    if event_id is not None:
        headers["x-razorpay-event-id"] = event_id
    return headers


def test_full_stack_flow():
    # ============================================================
    # SECTION 1 — webhook ingestion, signature verification, event-id idempotency
    # ============================================================
    body1 = _payment_failed_payload("pay_real_001")

    res1 = client.post("/api/webhooks/razorpay", content=body1, headers=_webhook_headers(body1, "evt_001"))
    assert res1.status_code == 200
    assert res1.json()["ok"] is True
    events_table = fake_client.db.tables["events"]
    assert len(events_table) == 1
    stored = events_table[0]
    assert stored["source"] == "live"
    assert stored["error_source"] == "customer"
    assert stored["error_step"] == "payment_authorization"
    assert stored["error_reason"] == "insufficient_funds"
    assert stored["error_code"] == "BAD_REQUEST_ERROR"
    assert stored["method"] == "upi"
    assert stored["razorpay_event_id"] == "evt_001"

    # Exact same delivery redelivered — must be a no-op.
    res2 = client.post("/api/webhooks/razorpay", content=body1, headers=_webhook_headers(body1, "evt_001"))
    assert res2.status_code == 200
    assert res2.json().get("duplicate") is True
    assert len(events_table) == 1

    # Different event id, SAME (payment_id, outcome) — secondary safeguard.
    res3 = client.post("/api/webhooks/razorpay", content=body1, headers=_webhook_headers(body1, "evt_002_different"))
    assert res3.json().get("duplicate") is True
    assert len(events_table) == 1

    # Bad signature.
    res4 = client.post("/api/webhooks/razorpay", content=body1, headers=_webhook_headers(body1, "evt_003", bad_signature=True))
    assert res4.status_code == 401
    assert len(events_table) == 1

    # Malformed JSON (signature computed over the SAME malformed bytes).
    malformed = b"{not valid json"
    res5 = client.post("/api/webhooks/razorpay", content=malformed, headers=_webhook_headers(malformed, "evt_004"))
    assert res5.status_code == 400

    # Irrelevant event type — acknowledged, ignored, nothing stored.
    irrelevant = json.dumps({"event": "payment.authorized", "payload": {}}).encode()
    res6 = client.post("/api/webhooks/razorpay", content=irrelevant, headers=_webhook_headers(irrelevant, "evt_005"))
    assert res6.status_code == 200
    assert res6.json()["ignored"] == "payment.authorized"
    assert len(events_table) == 1

    # payment.captured — the rate denominator.
    captured_body = _payment_captured_payload("pay_real_002")
    res7 = client.post("/api/webhooks/razorpay", content=captured_body, headers=_webhook_headers(captured_body, "evt_006"))
    assert res7.status_code == 200
    assert len([e for e in events_table if e["source"] == "live"]) == 2

    # ============================================================
    # SECTION 2 — demo scenario injection via the real route
    # ============================================================
    scenario_res = client.post("/api/demo/scenario", json={"scenario": "systemic-degradation"})
    assert scenario_res.status_code == 200
    scenario_json = scenario_res.json()
    assert scenario_json["eventsInjected"] > 0
    assert scenario_json["affectedSegments"] == ["upi", "netbanking:HDFC"]
    constructed_count = len([e for e in events_table if e["source"] == "constructed"])
    assert constructed_count > 0
    assert len([e for e in events_table if e["source"] == "live"]) == 2, "real events must be untouched"

    # ============================================================
    # SECTION 3 — detection cycle via the real cron route
    # ============================================================
    unauthed = client.post("/api/cron/detect")
    assert unauthed.status_code == 401

    def authed_cron():
        return client.post("/api/cron/detect", headers={"authorization": f"Bearer {os.environ['CRON_SECRET']}"})

    cycle1 = authed_cron().json()
    assert cycle1["segmentsEvaluated"] > 0
    assert cycle1["errors"] == []

    upi_transition = next((t for t in cycle1["stateTransitions"] if t["segment"] == "upi"), None)
    nb_transition = next((t for t in cycle1["stateTransitions"] if t["segment"] == "netbanking:HDFC"), None)
    assert upi_transition and upi_transition["to"] == "SYSTEMIC_DEGRADATION"
    assert nb_transition and nb_transition["to"] == "SYSTEMIC_DEGRADATION"

    card_state = next((s for s in fake_client.db.tables["agent_state"] if s["segment"] == "card"), None)
    assert card_state is None or card_state["state"] == "NORMAL"

    upi_policy = next(p for p in fake_client.db.tables["recovery_policy"] if p["segment"] == "upi")
    nb_policy = next(p for p in fake_client.db.tables["recovery_policy"] if p["segment"] == "netbanking:HDFC")
    assert upi_policy["status"] == "SUPPRESSED"
    assert nb_policy["status"] == "SUPPRESSED"

    upi_history = [h for h in fake_client.db.tables["agent_state_history"] if h["segment"] == "upi"]
    assert len(upi_history) > 0
    assert upi_history[-1]["case_classification"] == "C"
    assert upi_history[-1]["previous_state"] is None
    assert upi_history[-1]["window_end"]

    upi_alert = next((a for a in fake_client.db.tables["alerts"] if a["segment"] == "upi"), None)
    assert upi_alert is not None
    assert upi_alert["source"] == "template"  # no GROQ_API_KEY set
    pct = upi_alert["blast_radius"]["pct_of_total_system_volume_affected"]
    assert 0 < pct <= 1, f"blast radius volume share must be a plausible fraction, got {pct}"

    alerts_count_after_cycle1 = len(fake_client.db.tables["alerts"])
    assert alerts_count_after_cycle1 == 2, "both newly-degraded segments should each get an alert"

    history_count_after_cycle1 = len(fake_client.db.tables["agent_state_history"])
    cycle2 = authed_cron().json()
    assert cycle2["segmentsEvaluated"] == 0
    assert cycle2["segmentsSkippedIdempotent"] > 0
    assert cycle2["stateTransitions"] == []
    assert len(fake_client.db.tables["agent_state_history"]) == history_count_after_cycle1, (
        "re-running the same cycle must not write duplicate audit rows"
    )
    assert len(fake_client.db.tables["alerts"]) == alerts_count_after_cycle1, (
        "re-running the same cycle must not generate duplicate/extra alerts"
    )

    # ============================================================
    # SECTION 4 — dashboard state reflects backend reality
    # ============================================================
    dash = client.get("/api/dashboard/state").json()
    dash_upi_state = next(s for s in dash["states"] if s["segment"] == "upi")
    assert dash_upi_state["state"] == "SYSTEMIC_DEGRADATION"
    dash_upi_policy = next(p for p in dash["recoveryPolicies"] if p["segment"] == "upi")
    assert dash_upi_policy["status"] == "SUPPRESSED"
    assert len(dash["recentWindows"]) > 0
    assert len(dash["recentAlerts"]) > 0

    # ============================================================
    # SECTION 5 — recovery: anomaly clears, state resolves, policy restores
    # ============================================================
    # The HTTP route intentionally does NOT accept a client-supplied
    # "now" — so this section calls the service function directly with
    # a simulated future timestamp, far enough ahead that fresh
    # baseline-only data is the ONLY thing in its 8-window history
    # range, while still reusing the REAL run_agent_loop_cycle /
    # decision_engine / aggregator code, unmocked.
    import asyncio

    from app.services.agent import run_agent_loop_cycle
    from app.services.demo_scenarios import build_demo_scenario

    future_now = datetime.now(timezone.utc) + timedelta(minutes=45)
    recovery_plan = build_demo_scenario("recovery", future_now, 999)
    events_table.extend({**e.model_dump(), "merchant_id": "merchant_demo_001"} for e in recovery_plan.events)

    cycle3 = asyncio.run(run_agent_loop_cycle("merchant_demo_001", future_now))
    upi_recovery_transition = next((t for t in cycle3.state_transitions if t["segment"] == "upi"), None)
    assert upi_recovery_transition and upi_recovery_transition["to"] == "RESOLVED"

    upi_policy_after = next(p for p in fake_client.db.tables["recovery_policy"] if p["segment"] == "upi")
    assert upi_policy_after["status"] == "ACTIVE"

    upi_history_after = [h for h in fake_client.db.tables["agent_state_history"] if h["segment"] == "upi"]
    last_upi_history = upi_history_after[-1]
    assert last_upi_history["previous_state"] == "SYSTEMIC_DEGRADATION"
    assert "RESTORE_NORMAL_POLICY" in last_upi_history["actions"]

    # ============================================================
    # SECTION 6 — demo reset via the real route
    # ============================================================
    reset_res = client.post("/api/demo/reset")
    assert reset_res.status_code == 200
    reset_json = reset_res.json()
    assert reset_json["ok"] is True
    assert len([e for e in events_table if e["source"] == "constructed"]) == 0
    assert len([e for e in events_table if e["source"] == "live"]) == 2, "real events must survive a reset"
    assert len(fake_client.db.tables["agent_state"]) == 0
    assert len(fake_client.db.tables["agent_state_history"]) == 0
    assert len(fake_client.db.tables["alerts"]) == 0
    assert len(fake_client.db.tables["recovery_policy"]) == 0
    assert len(fake_client.db.tables["segment_windows"]) == 0
