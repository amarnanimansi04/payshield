"""
PAYSHIELD — detector tests (port of scripts/verify-detector.ts)

Covers the frozen spec's Part 7 two-segment threshold-attack scenario,
idempotency, the sample-size floor, and the baseline-freeze regression
(the real bug found via integration testing in the TypeScript version:
a sustained, never-improving anomaly must NOT eventually read as "back
to normal" just because the adaptive baseline caught up to it).
"""

from datetime import datetime, timezone

from app.services.decision_engine import decide
from app.services.detector import detect_segment
from app.services.detector import WindowedSegmentSeries
from app.models.schemas import AgentStateRecord

WINDOW_END = datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _lcg(seed: int):
    state = {"s": seed}

    def rng() -> float:
        state["s"] = (state["s"] * 1103515245 + 12345) & 0x7FFFFFFF
        return state["s"] / 0x7FFFFFFF

    return rng


def _binomial_rate(rng, p: float, n: int) -> float:
    failures = sum(1 for _ in range(n) if rng() < p)
    return failures / n


def _series(segment: str, rates: list[float], n: int, failed_amount: int = 0) -> WindowedSegmentSeries:
    return WindowedSegmentSeries(
        segment=segment,
        window_start=WINDOW_START.isoformat(),
        window_end=WINDOW_END.isoformat(),
        rates=rates,
        sample_sizes=[n] * len(rates),
        final_window_failed_amount=failed_amount,
        final_window_total_amount=n * 50000,
        final_window_failure_count=round(rates[-1] * n),
        error_source_distribution={"bank": 0.87, "customer": 0.13},
        data_source="constructed",
    )


def test_part7_threshold_attack_scenario():
    """Segment A: high volume, tight baseline, REAL injected anomaly.
    Segment B: low volume, naturally noisy, NO real anomaly - this
    window just happens to land high by chance."""
    rng_a = _lcg(42)
    n_windows = 8
    rates_a = [_binomial_rate(rng_a, 0.05, 1000) for _ in range(n_windows - 1)]
    rates_a.append(_binomial_rate(rng_a, 0.09, 1000))  # injected anomaly

    rng_b = _lcg(1337)
    rates_b = [_binomial_rate(rng_b, 0.08, 50) for _ in range(n_windows - 1)]
    rates_b.append(_binomial_rate(rng_b, 0.08, 50))  # same true rate, no anomaly

    result_a = detect_segment(_series("upi:bank", rates_a, 1000))
    result_b = detect_segment(_series("card:customer", rates_b, 50))

    assert result_a.is_significant is True, "real high-volume anomaly must be flagged"
    assert result_b.is_significant is False, "low-volume noise must NOT be flagged"

    # Naive baselines should get this wrong (the whole point of the scenario)
    global_fixed_flagged_a = rates_a[-1] > 0.20
    segment_fixed_flagged_a = rates_a[-1] > 0.12
    assert global_fixed_flagged_a is False, "global 20% threshold misses segment A's real anomaly"
    # (segment_fixed may or may not catch A depending on the random
    # draw - the important, deterministic claim is PayShield's own
    # correctness above)


def test_idempotency_repeated_evaluation():
    """Re-running detect_segment on the IDENTICAL series must return
    the IDENTICAL sustained-window count, never an incremented one."""
    rates = [0.05] * 6 + [0.19, 0.19, 0.19]
    series = _series("upi", rates, 200)
    r1 = detect_segment(series)
    r2 = detect_segment(series)
    assert r1.sustained_significant_windows == r2.sustained_significant_windows
    assert r1.is_significant == r2.is_significant


def test_sample_size_floor():
    """An extreme rate in a too-small sample must NOT be flagged
    significant, regardless of how large the deviation looks."""
    rates = [0.05] * 7 + [0.9]  # n=10, 9 of 10 failed
    series = _series("emi:tiny", rates, 10)
    result = detect_segment(series)
    assert result.is_significant is False


def test_baseline_freeze_never_auto_resolves_a_constant_unrecovering_incident():
    """Regression test for a real bug found via integration testing: a
    CONSTANT, never-improving 28% failure rate (baseline 8%) must stay
    flagged indefinitely, not read as "back to normal" after 2-3
    windows just because the adaptive baseline caught up to it."""
    baseline8 = [0.08] * 8
    rates = list(baseline8)
    state: AgentStateRecord | None = None
    for _ in range(5):
        rates.append(0.28)
        det = detect_segment(_series("card", rates, 50))
        outcome = decide("card", det, [det], state)
        state = outcome.new_state
    assert state.state in ("ISOLATED_DEGRADATION", "SYSTEMIC_DEGRADATION"), (
        f"a sustained, unrecovering incident must never resolve on its own — got {state.state}"
    )


def test_baseline_freeze_still_detects_genuine_recovery():
    """The same fix must NOT prevent a genuine recovery from being
    detected once the rate actually returns to baseline."""
    baseline8 = [0.08] * 8
    rates = list(baseline8)
    state: AgentStateRecord | None = None
    for r in [0.28, 0.28, 0.28, 0.08, 0.08, 0.08]:
        rates.append(r)
        det = detect_segment(_series("card", rates, 50))
        outcome = decide("card", det, [det], state)
        state = outcome.new_state
    assert state.state in ("NORMAL", "RESOLVED"), f"genuine recovery must resolve — got {state.state}"
