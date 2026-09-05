"""
PAYSHIELD — webhook ingestion (port of the former
src/app/api/webhooks/razorpay/route.ts)

Event-driven: reacts immediately to each incoming payment.failed /
payment.captured event. Does NOT run the detector here - that's
deliberately scheduled separately (see api/dashboard.py's
/api/cron/detect), because a rolling-window statistic shouldn't
recompute on every single event.

Idempotency: the PRIMARY key is Razorpay's own `x-razorpay-event-id`
header, which identifies a single webhook DELIVERY - this is what
protects against Razorpay's documented retry-on-non-2xx behavior. A
duplicate delivery is detected via a unique-constraint violation on
`events.razorpay_event_id` and short-circuited with a 200 (so Razorpay
doesn't keep retrying it) - we do NOT reprocess it. As a secondary
safeguard, a unique (payment_id, outcome) index also prevents
double-counting if an event ever arrives without an event-id header,
or if the SAME logical outcome is somehow delivered under two
different event ids. Delivery order is never assumed: aggregation
windows on created_at, not on ingestion order.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.security import verify_razorpay_signature
from app.core.supabase_client import get_supabase
from app.models.schemas import RazorpayPaymentEvent
from app.services.segment import normalize_event

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    event_id = request.headers.get("x-razorpay-event-id")  # REAL - Razorpay's
    # per-delivery idempotency key. MANUAL VERIFICATION REQUIRED: confirm
    # the exact header name/casing against a live test-mode webhook
    # delivery - header lookup here is case-insensitive so casing
    # differences alone won't break this, but the header's actual
    # presence should still be confirmed by hand once.

    settings = get_settings()
    if not settings.razorpay_webhook_secret:
        logger.error("RAZORPAY_WEBHOOK_SECRET is not configured")
        return JSONResponse({"error": "server misconfigured"}, status_code=500)

    if not verify_razorpay_signature(raw_body, signature, settings.razorpay_webhook_secret):
        # Deliberately vague error message - don't help an attacker
        # figure out *why* verification failed.
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    event_type = body.get("event")
    if event_type not in ("payment.failed", "payment.captured"):
        # We only care about these two events (ingest both so we have
        # a real rate denominator). Acknowledge anything else with 200
        # so Razorpay doesn't retry it forever.
        return JSONResponse({"ignored": event_type}, status_code=200)

    payment = (body.get("payload") or {}).get("payment", {}).get("entity")
    if not payment:
        return JSONResponse({"error": "malformed payload"}, status_code=400)

    # Only read fields Razorpay actually documents on the Payment
    # entity - never invent a field. Several are sparse/method-specific
    # (bank/vpa/card.* only populate for the relevant method; error_*
    # fields are null on a captured payment) - every access below is
    # defensive, never assumed present.
    card = payment.get("card") or {}
    raw = RazorpayPaymentEvent(
        payment_id=payment["id"],
        order_id=payment.get("order_id"),
        outcome="failed" if event_type == "payment.failed" else "captured",
        method=payment["method"],
        amount=payment["amount"],
        currency=payment["currency"],
        created_at=payment["created_at"],
        bank=payment.get("bank"),
        vpa=payment.get("vpa"),
        card_issuer=card.get("issuer"),
        card_network=card.get("network"),
        error_code=payment.get("error_code"),
        error_description=payment.get("error_description"),
        error_source=payment.get("error_source"),
        error_step=payment.get("error_step"),
        error_reason=payment.get("error_reason"),
    )

    normalized = normalize_event(raw, "live", str(uuid4()), event_id)
    supabase = await get_supabase()

    if event_id:
        # Primary idempotency path: a plain INSERT keyed on the unique
        # razorpay_event_id index. A unique-violation here means this
        # exact delivery was already processed - short-circuit, don't
        # reprocess, acknowledge with 200 so Razorpay stops retrying.
        try:
            await supabase.table("events").insert(normalized.model_dump()).execute()
        except APIError as e:
            if e.code == "23505":
                return JSONResponse({"ok": True, "duplicate": True}, status_code=200)
            logger.error("Failed to write event to Supabase: %s", e.message)
            return JSONResponse({"error": "storage failure"}, status_code=500)
        return JSONResponse({"ok": True}, status_code=200)

    # Fallback path (no event-id header on this delivery, unexpected
    # for real Razorpay traffic but handled defensively): fall back to
    # the secondary (payment_id, outcome) safeguard.
    logger.warning("Webhook delivery missing x-razorpay-event-id — falling back to payment_id+outcome idempotency only.")
    try:
        await (
            supabase.table("events")
            .upsert(normalized.model_dump(), on_conflict="payment_id,outcome", ignore_duplicates=True)
            .execute()
        )
    except APIError as e:
        logger.error("Failed to write event to Supabase: %s", e.message)
        return JSONResponse({"error": "storage failure"}, status_code=500)

    return JSONResponse({"ok": True}, status_code=200)
