"""
PAYSHIELD — segmentation (port of the former src/lib/segment.ts)

The primary segmentation axis for detection is `method x error_source`
- both fields are always populated by Razorpay directly, zero
inference required. The `bank` field is used as-is (real, unmodified)
as a secondary axis, only for netbanking events. The VPA-derived label
exists but is never used as a primary detection axis - only shown,
clearly flagged, as an enrichment.
"""

from datetime import datetime, timezone

from app.models.schemas import NormalizedEvent, RazorpayPaymentEvent

_KNOWN_VPA_HANDLE_TO_LABEL: dict[str, str] = {
    "okhdfcbank": "HDFC Bank (inferred from VPA)",
    "okicici": "ICICI Bank (inferred from VPA)",
    "oksbi": "State Bank of India (inferred from VPA)",
    "okaxis": "Axis Bank (inferred from VPA)",
    "ybl": "PhonePe / Yes Bank rail (inferred from VPA)",
    "paytm": "Paytm Payments Bank (inferred from VPA)",
    "apl": "Amazon Pay (inferred from VPA)",
}


def infer_vpa_bank_label(vpa: str | None) -> str | None:
    """[INFERRED] - never treat this as ground truth, never use it as
    the primary detection segment. Disclose it as an inference
    wherever it's rendered."""
    if not vpa or "@" not in vpa:
        return None
    handle = vpa.split("@")[1].lower()
    return _KNOWN_VPA_HANDLE_TO_LABEL.get(handle, f'Unknown handle "{handle}" (inferred from VPA)')


def primary_segment_key(method: str, bank: str | None) -> str:
    """[DERIVED], zero inference. Must be a field present on BOTH
    captured and failed payments, or current_rate has no real
    denominator - `method` is the correct primary key for exactly this
    reason. For netbanking specifically, `bank` is real and present on
    both outcomes, so a bank-level segment is valid, fully-real."""
    if method == "netbanking" and bank:
        return f"netbanking:{bank}"
    return method


def normalize_event(
    raw: RazorpayPaymentEvent,
    source: str,
    event_id: str,
    razorpay_event_id: str | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        razorpay_event_id=razorpay_event_id,
        payment_id=raw.payment_id,
        outcome=raw.outcome,
        created_at=datetime.fromtimestamp(raw.created_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        method=raw.method,
        bank=raw.bank,
        vpa=raw.vpa,
        vpa_derived_bank_label=infer_vpa_bank_label(raw.vpa),
        card_issuer=raw.card_issuer,
        card_network=raw.card_network,
        error_code=raw.error_code,
        error_source=raw.error_source,
        error_reason=raw.error_reason,
        error_step=raw.error_step,
        amount=raw.amount,
        segment_primary=primary_segment_key(raw.method, raw.bank),
        segment_bank=raw.bank if raw.method == "netbanking" else None,
        ingested_at=datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        source=source,
    )
