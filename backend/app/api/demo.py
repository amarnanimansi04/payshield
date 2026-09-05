"""
PAYSHIELD — demo scenario injection + reset (port of the former
src/app/api/demo/scenario/route.ts and demo/reset/route.ts)

DEMO-ONLY. Writes a controlled, clearly-disclosed batch of
source="constructed" events into the SAME `events` table real Razorpay
webhooks write to, structurally distinguished by the `source` column -
never pretending to be real traffic, and never touching the evaluation
harness's ground-truth tables.

POST /api/demo/scenario body: {"scenario": "normal" |
"systemic-degradation" | "isolated-degradation" | "recovery"}

After injecting, call /api/dashboard/run-cycle to have PayShield actually
react to it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import get_args

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.core.auth import get_current_merchant
from app.core.supabase_client import get_supabase
from app.models.schemas import DemoScenario
from app.services.demo_scenarios import build_demo_scenario

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_SCENARIOS = set(get_args(DemoScenario))
INSERT_CHUNK_SIZE = 500

DERIVED_TABLES = ["segment_windows", "agent_state", "agent_state_history", "alerts", "recovery_policy"]


@router.post("/api/demo/scenario")
async def demo_scenario(request: Request, merchant_id: str = Depends(get_current_merchant)) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid json"}, status_code=400)

    scenario = body.get("scenario") if isinstance(body, dict) else None
    if scenario not in VALID_SCENARIOS:
        return JSONResponse(
            {"error": f"scenario must be one of: {', '.join(sorted(VALID_SCENARIOS))}"}, status_code=400
        )

    try:
        # A fresh salt on every call guarantees non-colliding synthetic
        # payment_ids even if the same scenario is triggered repeatedly
        # without a reset in between - safe to click during a live demo.
        seed_salt = int(time.time()) % 1_000_000 + random.randint(0, 999_999)
        plan = build_demo_scenario(scenario, datetime.now(timezone.utc), seed_salt)

        supabase = await get_supabase()

        async def _upsert_chunk(chunk: list[dict]) -> int:
            await (
                supabase.table("events")
                .upsert(chunk, on_conflict="payment_id,outcome", ignore_duplicates=True)
                .execute()
            )
            return len(chunk)

        # Chunks are independent (distinct, uniquely-id'd rows; the
        # on_conflict target is per-row, not cross-chunk), so they're
        # safe to send concurrently rather than one round-trip at a
        # time - a plain latency win for larger scenarios (e.g.
        # "recovery"'s high-volume batch, see demo_scenarios.py), not a
        # correctness-sensitive change.
        chunks = [
            [{**e.model_dump(), "merchant_id": merchant_id} for e in plan.events[i : i + INSERT_CHUNK_SIZE]]
            for i in range(0, len(plan.events), INSERT_CHUNK_SIZE)
        ]
        inserted = sum(await asyncio.gather(*(_upsert_chunk(c) for c in chunks))) if chunks else 0

        return JSONResponse(
            {
                "ok": True,
                "scenario": scenario,
                "eventsInjected": inserted,
                "affectedSegments": plan.affected_segments,
                "summary": plan.summary,
                "note": (
                    "All injected rows are marked source='constructed' — disclosed demo data, "
                    "structurally distinct from real Razorpay test-mode traffic. Run the "
                    "detection cycle next to see PayShield react."
                ),
            }
        )
    except APIError as e:
        logger.error("Demo scenario insert failed: %s", e.message)
        return JSONResponse({"error": "storage failure"}, status_code=500)
    except Exception as exc:  # noqa: BLE001 - never let an unexpected
        # throw (e.g. Supabase not configured) fall through to a
        # generic framework error page - the client always expects
        # JSON back from this route.
        logger.exception("Demo scenario injection failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/demo/reset")
async def demo_reset(merchant_id: str = Depends(get_current_merchant)) -> JSONResponse:
    """Deletes every source="constructed" row from `events` - real
    Razorpay ("live") events are NEVER touched. Also clears the
    DERIVED state tables, because those are computed FROM events
    (often blending live+constructed data within the same
    segment/window) and would otherwise show a stale, misleading
    picture after the events that produced them are gone. This is
    safe: any real historical detection state regenerates
    automatically the next time the detection cycle runs against the
    (untouched) real events."""
    try:
        supabase = await get_supabase()
        errors: list[str] = []

        events_deleted: int | None = None
        try:
            resp = (
                await supabase.table("events")
                .delete(count="exact")
                .eq("source", "constructed")
                .eq("merchant_id", merchant_id)
                .execute()
            )
            events_deleted = resp.count
        except APIError as e:
            errors.append(f"events: {e.message}")

        for table in DERIVED_TABLES:
            try:
                await (
                    supabase.table(table)
                    .delete()
                    .eq("merchant_id", merchant_id)
                    .neq("segment", "__never_matches__")
                    .execute()
                )
            except APIError as e:
                errors.append(f"{table}: {e.message}")

        if errors:
            return JSONResponse({"ok": False, "errors": errors}, status_code=500)

        return JSONResponse(
            {
                "ok": True,
                "constructedEventsDeleted": events_deleted,
                "note": (
                    "Deleted all source='constructed' events, plus all derived agent "
                    "state/history/alerts/recovery-policy rows (they're meaningless without the "
                    "events that produced them and will regenerate from real data on the next "
                    "detection cycle). Real Razorpay ('live') events were left untouched."
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Demo reset failed")
        return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=500)
