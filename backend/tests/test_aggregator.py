"""
PAYSHIELD — aggregation tests (port of scripts/verify-aggregation.ts)

Covers canonical epoch-aligned windows, empty-window handling (NO DATA,
never a synthetic 0%), data-source disclosure, EWMA baseline
adaptation, and the (payment_id, outcome) vs razorpay_event_id
uniqueness distinction for different lifecycle events on the same
payment.
"""

from datetime import datetime, timedelta, timezone

from app.models.schemas import RazorpayPaymentEvent
from app.services.aggregator import bucket_into_canonical_windows, canonical_window_end
from app.services.detector import ewma
from app.services.segment import normalize_event

WINDOW_MINUTES = 5


def make_event(**overrides):
    base = dict(
        payment_id="pay_1", order_id=None, outcome="captured", method="upi",
        amount=50000, currency="INR", created_at=0, bank=None, vpa="user@okhdfcbank",
        card_issuer=None, card_network=None, error_code=None, error_description=None,
        error_source=None, error_step=None, error_reason=None,
    )
    base.update(overrides)
    raw = RazorpayPaymentEvent(**base)
    return raw


def test_canonical_window_alignment():
    arbitrary = datetime(2026, 1, 1, 12, 7, 32, tzinfo=timezone.utc)
    aligned = canonical_window_end(arbitrary, WINDOW_MINUTES)
    assert aligned.isoformat() == "2026-01-01T12:05:00+00:00"

    again = canonical_window_end(datetime(2026, 1, 1, 12, 9, 59, tzinfo=timezone.utc), WINDOW_MINUTES)
    assert again == aligned, "any timestamp within the same slot must floor to the identical boundary"


def _normalized(created_at_iso: str, outcome: str = "captured", amount: int = 10000):
    ts = int(datetime.fromisoformat(created_at_iso.replace("Z", "+00:00")).timestamp())
    raw = make_event(outcome=outcome, created_at=ts, amount=amount)
    return normalize_event(raw, "live", "evt", None)


def test_empty_window_is_none_not_zero():
    canonical_end = datetime(2026, 1, 1, 12, 20, tzinfo=timezone.utc)
    history_start = canonical_end - timedelta(minutes=20)

    events = [
        _normalized("2026-01-01T12:01:00.000Z", outcome="captured"),
        _normalized("2026-01-01T12:02:00.000Z", outcome="failed", amount=10000),
        _normalized("2026-01-01T12:16:00.000Z", outcome="captured"),
        _normalized("2026-01-01T12:17:00.000Z", outcome="captured"),
    ]

    buckets = bucket_into_canonical_windows(events, history_start, canonical_end, WINDOW_MINUTES)
    assert len(buckets) == 4
    assert buckets[0].rate == 0.5
    assert buckets[1].rate is None, "empty window must be None, not 0"
    assert buckets[1].total_attempts == 0
    assert buckets[2].rate is None
    assert buckets[3].rate == 0.0  # has data, just no failures

    non_empty_rates = [b.rate for b in buckets if b.rate is not None]
    assert non_empty_rates == [0.5, 0.0], "empty windows must be excluded entirely, never injected as zeros"


def test_data_source_disclosure():
    canonical_end = datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc)
    history_start = canonical_end - timedelta(minutes=5)

    live_only = bucket_into_canonical_windows(
        [_normalized("2026-01-01T12:06:00.000Z")], history_start, canonical_end, WINDOW_MINUTES
    )
    assert live_only[0].data_source == "live"

    e1 = _normalized("2026-01-01T12:06:00.000Z")
    e1.source = "live"
    raw2 = make_event(created_at=int(datetime(2026, 1, 1, 12, 7, tzinfo=timezone.utc).timestamp()))
    e2 = normalize_event(raw2, "constructed", "evt2", None)
    mixed = bucket_into_canonical_windows([e1, e2], history_start, canonical_end, WINDOW_MINUTES)
    assert mixed[0].data_source == "mixed"


def test_ewma_baseline_adaptation():
    flat = ewma([0.05, 0.05, 0.05, 0.05])
    assert round(flat, 4) == 0.05

    after_one_high_window = ewma([0.05, 0.05, 0.05, 0.2])
    assert 0.05 < after_one_high_window < 0.2, "baseline shifts toward new data but doesn't fully jump to it"


def test_different_lifecycle_events_for_same_payment():
    """The SAME payment_id can legitimately produce two rows - one
    payment.failed, later one payment.captured (e.g. a retried
    attempt) - two DIFFERENT outcomes for the same payment_id, which
    is exactly why the unique index is on (payment_id, outcome), not
    payment_id alone."""
    raw = make_event(
        payment_id="pay_retry_example", outcome="failed", error_code="BAD_REQUEST_ERROR",
        error_source="customer", error_step="payment_authorization", error_reason="insufficient_funds",
    )
    failed_row = normalize_event(raw, "live", "evt_1", "razorpay_evt_1")

    raw2 = make_event(
        payment_id="pay_retry_example", outcome="captured", error_code=None,
        error_source=None, error_step=None, error_reason=None,
    )
    captured_row = normalize_event(raw2, "live", "evt_2", "razorpay_evt_2")

    assert failed_row.payment_id == captured_row.payment_id
    assert failed_row.outcome != captured_row.outcome
    assert failed_row.razorpay_event_id != captured_row.razorpay_event_id
