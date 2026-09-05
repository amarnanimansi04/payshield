"""
PAYSHIELD — detector (port of the former src/lib/detector.ts)

Method: EWMA-adaptive per-segment baseline + a sample-size-aware
z-score significance test + a normal-CDF "significance score" + a
disclosed linear trend projection. Deliberately NOT Isolation Forest /
deep learning / changepoint methods - over-engineering for this data
shape and timeline.

Terminology: the 0..1 number this module produces is a SIGNIFICANCE
SCORE, not a "confidence" or a "probability the anomaly is real". It's
a standard, disclosed normal-CDF mapping of how many standard
deviations the current rate sits from the adaptive baseline - a
measure of how extreme the deviation is, not an empirically-calibrated
probability.

Statistical assumptions, stated plainly: the baseline (EWMA) is itself
estimated FROM the same rolling window used to test significance, and
consecutive windows are not independent draws - this is a practical,
explainable heuristic ("sample-size-aware deviation from an adaptive
baseline"), not a formally calibrated hypothesis test with a
guaranteed false-positive rate. The 3-sigma bar is a conventional,
disclosed threshold chosen for explainability, not derived from a
formal power analysis. The evaluation harness measures the REALIZED
false-positive rate empirically.

Baseline freezing during an ongoing anomaly: the baseline only ever
updates using a window that was NOT itself flagged significant. This
guards against a real, reproduced bug (see the project's engineering
history): a naive baseline that keeps updating on every prior window,
including ones already flagged anomalous, drifts toward an ongoing,
never-improving incident and can eventually mark it "back to normal"
purely because the baseline learned to treat the bad rate as normal.
Freezing the baseline the moment a window is flagged, and only
resuming updates once windows are genuinely back near the frozen "last
known healthy" level, fixes this while keeping the method exactly as
simple (still just EWMA + a z-score).

This module has ZERO dependency on Razorpay, Supabase, FastAPI, or any
external service. Pure functions over lists of numbers - deterministic,
independently testable (see tests/test_detector.py).
"""

from __future__ import annotations

from scipy.stats import norm

from app.models.schemas import DetectionResult, TrendProjection

EWMA_ALPHA = 0.3  # weight on the most recent window; higher = more
# reactive to recent data, lower = smoother. Not tuned to flatter any
# specific evaluation run.

MIN_SAMPLE_SIZE_FOR_SIGNIFICANCE = 20  # segments with fewer events than
# this in a window are explicitly NOT scored for significance -
# below this floor we return is_significant=False regardless of how
# extreme the observed rate looks.

SIGNIFICANCE_SIGMA_THRESHOLD = 3  # a standard, explainable bar (roughly
# a 99.7% two-sided threshold UNDER NORMALITY - see the module-level
# note above about what this bar does and doesn't prove).

SUSTAINED_WINDOWS_FOR_ACTIONABLE = 2  # a single anomalous window is
# MONITORING, not yet ISOLATED/SYSTEMIC_DEGRADATION - see decision_engine.py


class WindowedSegmentSeries:
    """Not persisted - the internal shape the aggregator hands to the
    detector for one segment, one evaluation point."""

    def __init__(
        self,
        segment: str,
        window_start: str,
        window_end: str,
        rates: list[float],
        sample_sizes: list[int],
        final_window_failed_amount: int,
        final_window_total_amount: int,
        final_window_failure_count: int,
        error_source_distribution: dict[str, float],
        data_source: str = "live",
    ):
        self.segment = segment
        self.window_start = window_start
        self.window_end = window_end
        # rates: failure rate per window, oldest first, LAST element is
        # the one being tested. Windows with zero attempts are EXCLUDED
        # entirely by the aggregator - never included as a synthetic
        # zero.
        self.rates = rates
        self.sample_sizes = sample_sizes
        self.final_window_failed_amount = final_window_failed_amount
        self.final_window_total_amount = final_window_total_amount
        self.final_window_failure_count = final_window_failure_count
        self.error_source_distribution = error_source_distribution
        self.data_source = data_source


def ewma(prior_rates: list[float], alpha: float = EWMA_ALPHA) -> float:
    """Exponentially-weighted moving average over a sequence of
    per-window failure rates."""
    if not prior_rates:
        return 0.0
    value = prior_rates[0]
    for r in prior_rates[1:]:
        value = alpha * r + (1 - alpha) * value
    return value


def proportion_std_dev(baseline_rate: float, sample_size: int) -> float:
    """Standard deviation of a binomial proportion - the correct,
    sample-size-aware way to ask "how much noise should I expect
    around this baseline rate, given this many observations?"."""
    if sample_size <= 0:
        return 0.0
    p = min(max(baseline_rate, 0.0001), 0.9999)
    return (p * (1 - p) / sample_size) ** 0.5


def compute_clean_baselines(rates: list[float], sample_sizes: list[int]) -> list[float]:
    """Builds a "clean" EWMA baseline for every index in `rates` - one
    that only ever updates using a window NOT itself flagged
    significant against the baseline at that point.
    `baselines[i]` is the baseline window `i` is tested against
    (formed from windows `[0, i)`)."""
    baselines: list[float] = [0.0] * len(rates)
    if not rates:
        return baselines
    baseline = rates[0]
    baselines[0] = rates[0]  # index 0 is never tested (no prior window)
    for i in range(1, len(rates)):
        baselines[i] = baseline
        sigma = proportion_std_dev(baseline, sample_sizes[i])
        deviation = (rates[i] - baseline) / sigma if sigma > 0 else 0.0
        enough_data = sample_sizes[i] >= MIN_SAMPLE_SIZE_FOR_SIGNIFICANCE
        was_significant = (
            enough_data
            and abs(deviation) >= SIGNIFICANCE_SIGMA_THRESHOLD
            and rates[i] > baseline
        )
        if not was_significant:
            baseline = EWMA_ALPHA * rates[i] + (1 - EWMA_ALPHA) * baseline
        # else: baseline stays frozen - an already-flagged anomalous
        # window never gets folded into "what normal looks like".
    return baselines


def _evaluate_at(
    rates: list[float], sample_sizes: list[int], baselines: list[float], index: int
) -> tuple[float, float, bool]:
    """Evaluates ONE window (by index) against a precomputed clean
    baseline. One source of truth for "what counts as significant" -
    used by the final-window test AND the sustained/normal-window
    scans, which is what makes re-evaluating the same data idempotent."""
    baseline = baselines[index]
    sample_size = sample_sizes[index]
    sigma = proportion_std_dev(baseline, sample_size)
    sigma_deviation = (rates[index] - baseline) / sigma if sigma > 0 else 0.0
    enough_data = sample_size >= MIN_SAMPLE_SIZE_FOR_SIGNIFICANCE
    is_significant = (
        enough_data
        and abs(sigma_deviation) >= SIGNIFICANCE_SIGMA_THRESHOLD
        and rates[index] > baseline
    )
    return baseline, sigma_deviation, is_significant


def compute_sustained_significant_windows(rates: list[float], sample_sizes: list[int]) -> int:
    """Counts consecutive trailing windows (including the final one)
    that are ALSO significant, recomputed fresh from the data every
    call - NOT an operational counter. Calling this twice on the
    identical series always returns the identical number."""
    baselines = compute_clean_baselines(rates, sample_sizes)
    count = 0
    for i in range(len(rates) - 1, 0, -1):
        _, _, is_significant = _evaluate_at(rates, sample_sizes, baselines, i)
        if not is_significant:
            break
        count += 1
    return count


def compute_consecutive_normal_windows(rates: list[float], sample_sizes: list[int]) -> int:
    """Symmetric counterpart - consecutive trailing windows (including
    the final one) that are NOT significant. Because the baseline is
    FROZEN during an ongoing anomaly, a window only counts as "normal"
    if the rate has genuinely moved back near the last known-healthy
    level."""
    baselines = compute_clean_baselines(rates, sample_sizes)
    count = 0
    for i in range(len(rates) - 1, 0, -1):
        _, _, is_significant = _evaluate_at(rates, sample_sizes, baselines, i)
        if is_significant:
            break
        count += 1
    return count


def _dominant_error_source(dist: dict[str, float]) -> dict | None:
    if not dist:
        return None
    source, share = max(dist.items(), key=lambda kv: kv[1])
    return {"source": source, "share": share}


def _project_trend(
    current_rate: float, baseline_rate: float, final_window_failed_amount: int
) -> TrendProjection:
    """Disclosed, labeled projection - NEVER rendered with the same
    visual weight as an observed number."""
    assumed_future_windows = 3
    excess_rate = max(current_rate - baseline_rate, 0.0)
    projected_additional = (
        final_window_failed_amount
        * assumed_future_windows
        * (excess_rate / max(current_rate, 0.0001))
    )
    return TrendProjection(
        assumption=(
            f"Estimated projection, assumes the current elevated rate "
            f"({current_rate * 100:.1f}%) persists for {assumed_future_windows} "
            f"more windows - not a guarantee, not an observed value."
        ),
        additional_windows_assumed=assumed_future_windows,
        projected_additional_amount=round(projected_additional),
    )


def build_error_source_distribution(events: list) -> dict[str, float]:
    """Utility used by the aggregator to turn a list of failed events
    into an error_source distribution, e.g. {"bank": 0.87, "customer":
    0.13} - a real, deterministic frequency count, not an ML ranking."""
    counts: dict[str, int] = {}
    for e in events:
        key = getattr(e, "error_source", None) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    total = len(events) or 1
    return {k: v / total for k, v in counts.items()}


def detect_segment(series: WindowedSegmentSeries) -> DetectionResult:
    """Core detection function. Pure, deterministic given its inputs."""
    final_index = len(series.rates) - 1
    current_rate = series.rates[final_index]
    current_sample_size = series.sample_sizes[final_index]

    clean_baselines = compute_clean_baselines(series.rates, series.sample_sizes)
    baseline, sigma_deviation, is_significant = _evaluate_at(
        series.rates, series.sample_sizes, clean_baselines, final_index
    )
    significance_score = float(norm.cdf(abs(sigma_deviation)))
    sustained_significant_windows = compute_sustained_significant_windows(
        series.rates, series.sample_sizes
    )
    consecutive_normal_windows = compute_consecutive_normal_windows(
        series.rates, series.sample_sizes
    )

    dominant = _dominant_error_source(series.error_source_distribution)
    trend = (
        _project_trend(current_rate, baseline, series.final_window_failed_amount)
        if is_significant
        else None
    )

    return DetectionResult(
        segment=series.segment,
        window_start=series.window_start,
        window_end=series.window_end,
        current_rate=current_rate,
        ewma_baseline=baseline,
        sigma_deviation=sigma_deviation,
        significance_score=significance_score,
        sample_size=current_sample_size,
        is_significant=is_significant,
        sustained_significant_windows=sustained_significant_windows,
        consecutive_normal_windows=consecutive_normal_windows,
        dominant_error_source=dominant,
        trend_projection=trend,
    )
