"""
PAYSHIELD — demo scenario tests (port of scripts/verify-demo-scenarios.ts)

Confirms each scenario produces the right SHAPE of data: every event
is disclosed as source="constructed", the right segments are (or
aren't) affected, and feeding the output straight into the real
detector/decision engine produces the expected classification.
"""

from datetime import datetime, timezone

from app.services.aggregator import (
    WINDOW_MINUTES,
    WINDOWS_OF_HISTORY,
    bucket_into_canonical_windows,
    canonical_window_end,
)
from app.services.demo_scenarios import build_demo_scenario
from app.services.detector import build_error_source_distribution, detect_segment, WindowedSegmentSeries

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def _detect_from_events(events, segment: str):
    seg_events = [e for e in events if e.segment_primary == segment]
    canonical_end = canonical_window_end(NOW, WINDOW_MINUTES)
    from datetime import timedelta

    history_start = canonical_end - timedelta(minutes=WINDOWS_OF_HISTORY * WINDOW_MINUTES)
    buckets = [
        b for b in bucket_into_canonical_windows(seg_events, history_start, canonical_end, WINDOW_MINUTES) if b.rate is not None
    ]
    final_bucket = buckets[-1]
    return detect_segment(
        WindowedSegmentSeries(
            segment=segment,
            window_start=final_bucket.window_start.isoformat(),
            window_end=final_bucket.window_end.isoformat(),
            rates=[b.rate for b in buckets],
            sample_sizes=[b.total_attempts for b in buckets],
            final_window_failed_amount=final_bucket.failed_amount_sum,
            final_window_total_amount=final_bucket.total_amount_sum,
            final_window_failure_count=final_bucket.failure_count,
            error_source_distribution=build_error_source_distribution(
                [e for e in final_bucket.events if e.outcome == "failed"]
            ),
            data_source=final_bucket.data_source or "constructed",
        )
    )


def test_normal_scenario():
    plan = build_demo_scenario("normal", NOW)
    assert all(e.source == "constructed" for e in plan.events)
    assert all(e.razorpay_event_id is None for e in plan.events)
    assert plan.affected_segments == []
    upi = _detect_from_events(plan.events, "upi")
    assert upi.is_significant is False


def test_isolated_degradation_scenario():
    plan = build_demo_scenario("isolated-degradation", NOW)
    assert plan.affected_segments == ["upi"]
    upi = _detect_from_events(plan.events, "upi")
    netbanking = _detect_from_events(plan.events, "netbanking:HDFC")
    card = _detect_from_events(plan.events, "card")
    assert upi.is_significant is True
    assert upi.sustained_significant_windows >= 2
    assert netbanking.is_significant is False
    assert card.is_significant is False


def test_systemic_degradation_scenario():
    plan = build_demo_scenario("systemic-degradation", NOW)
    assert plan.affected_segments == ["upi", "netbanking:HDFC"]
    upi = _detect_from_events(plan.events, "upi")
    netbanking = _detect_from_events(plan.events, "netbanking:HDFC")
    card = _detect_from_events(plan.events, "card")
    assert upi.is_significant is True
    assert netbanking.is_significant is True
    assert card.is_significant is False


def test_recovery_scenario():
    plan = build_demo_scenario("recovery", NOW)
    upi = _detect_from_events(plan.events, "upi")
    assert upi.is_significant is False
    assert upi.consecutive_normal_windows >= 2


def test_determinism_same_seed_same_output():
    plan_a = build_demo_scenario("systemic-degradation", NOW, 0)
    plan_b = build_demo_scenario("systemic-degradation", NOW, 0)
    assert len(plan_a.events) == len(plan_b.events)
    assert plan_a.events[0].payment_id == plan_b.events[0].payment_id

    plan_c = build_demo_scenario("systemic-degradation", NOW, 12345)
    assert plan_a.events[0].payment_id != plan_c.events[0].payment_id, (
        "a different seed_salt must produce a different first payment_id "
        "(no collision on repeated live injection)"
    )
