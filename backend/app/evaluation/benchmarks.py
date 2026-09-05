"""
PAYSHIELD — evaluation harness (port of the former scripts/evaluate.ts)

Implements the frozen spec's methodology: multiple anomaly magnitudes,
multiple durations, multiple affected-segment counts, different
baseline volumes, plus no-anomaly false-positive control periods -
all with KNOWN, injected ground truth, kept structurally separate from
the detector's actual input (the detector never sees the "is this
anomalous" flag; this script only uses it AFTER THE FACT to score the
detector's output).

Run with: python -m app.evaluation.benchmarks
Optionally writes a summary row to Supabase's evaluation_runs table if
configured; runs fully standalone (prints to console) if not.

Baseline naming is deliberately precise (do not call something
"global" if it's actually segment-specific, and vice versa):

  Baseline A - global fixed threshold (20%, same number for every segment)
  Baseline B - segment rolling average, fixed z-score, NOT
               sample-size-aware (isolates exactly what sample-size
               awareness adds - each scenario here represents one
               segment in isolation, so there is no cross-segment
               population to blend into a genuinely "global" rolling
               average; what this baseline isolates is precisely
               "adaptive baseline WITHOUT sample-size awareness",
               the direct comparison against PayShield)
  Baseline C - segment fixed threshold (12%, not derived from history)
  PayShield    - segment-adaptive EWMA baseline + sample-size-aware z-score
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from app.services.detector import detect_segment, WindowedSegmentSeries
from app.evaluation.generator import seeded_rng

logger = logging.getLogger(__name__)

MAGNITUDE_RATE_MULTIPLIER = {"none": 1.0, "small": 1.6, "medium": 2.2, "large": 3.5}


@dataclass
class Scenario:
    label: str
    segment_name: str
    sample_size: int
    baseline_rate: float
    num_prior_windows: int
    anomaly_magnitude: str  # "none" | "small" | "medium" | "large"
    anomaly_duration_windows: int
    seed: int


SCENARIOS: list[Scenario] = [
    Scenario("high-vol / small magnitude", "upi", 1000, 0.05, 8, "small", 2, 1),
    Scenario("high-vol / medium magnitude", "upi", 1000, 0.05, 8, "medium", 2, 2),
    Scenario("high-vol / large magnitude", "upi", 1000, 0.05, 8, "large", 2, 3),
    Scenario("low-vol / no anomaly (control) A", "card", 50, 0.08, 8, "none", 0, 11),
    Scenario("low-vol / no anomaly (control) B", "card", 50, 0.08, 8, "none", 0, 12),
    Scenario("low-vol / no anomaly (control) C", "card", 50, 0.08, 8, "none", 0, 13),
    Scenario("low-vol / no anomaly (control) D", "card", 50, 0.08, 8, "none", 0, 14),
    Scenario("low-vol / no anomaly (control) E", "card", 50, 0.08, 8, "none", 0, 15),
    Scenario("high-vol / no anomaly (control) A", "netbanking", 1000, 0.05, 8, "none", 0, 21),
    Scenario("high-vol / no anomaly (control) B", "netbanking", 1000, 0.05, 8, "none", 0, 22),
    Scenario("high-vol / large magnitude / 1-window blip", "wallet", 800, 0.04, 8, "large", 1, 31),
    Scenario("high-vol / large magnitude / 3-window sustained", "wallet", 800, 0.04, 8, "large", 3, 32),
    Scenario("medium-vol (n=200) / medium magnitude", "emi", 200, 0.06, 8, "medium", 2, 41),
    Scenario("very-low-vol (n=25) / medium magnitude", "emi", 25, 0.06, 8, "medium", 2, 42),
    Scenario("very-high-vol (n=3000) / small magnitude", "upi", 3000, 0.05, 8, "small", 2, 51),
    Scenario("low-vol (n=50) / large magnitude (real, not just noise)", "card", 50, 0.08, 8, "large", 3, 52),
    Scenario("low-vol / no anomaly (control) F", "card", 50, 0.08, 8, "none", 0, 16),
]


@dataclass
class ScenarioOutcome:
    scenario: Scenario
    final_window_truth: bool
    payshield_flagged: bool
    baseline_a_flagged: bool
    baseline_b_flagged: bool
    baseline_c_flagged: bool
    windows_to_first_detection: int | None


def _binomial_rate(rng, p: float, n: int) -> float:
    draws = rng.random(n)
    failures = int((draws < p).sum())
    return failures / n


def run_scenario(scenario: Scenario) -> ScenarioOutcome:
    rng = seeded_rng(scenario.seed)
    rates: list[float] = []
    sample_sizes: list[int] = []

    for _ in range(scenario.num_prior_windows):
        rates.append(_binomial_rate(rng, scenario.baseline_rate, scenario.sample_size))
        sample_sizes.append(scenario.sample_size)

    anomalous_rate = scenario.baseline_rate * MAGNITUDE_RATE_MULTIPLIER[scenario.anomaly_magnitude]

    windows_to_first_detection: int | None = None
    payshield_flagged_final = False
    final_result = None

    span = max(scenario.anomaly_duration_windows, 1)
    for i in range(span):
        is_anomalous_window = i < scenario.anomaly_duration_windows
        rate = _binomial_rate(rng, anomalous_rate if is_anomalous_window else scenario.baseline_rate, scenario.sample_size)
        rates.append(rate)
        sample_sizes.append(scenario.sample_size)

        series = WindowedSegmentSeries(
            segment=scenario.segment_name,
            window_start="2026-01-01T00:00:00.000Z",
            window_end="2026-01-01T00:05:00.000Z",
            rates=list(rates),
            sample_sizes=list(sample_sizes),
            final_window_failed_amount=round(rate * scenario.sample_size * 50000),
            final_window_total_amount=scenario.sample_size * 50000,
            final_window_failure_count=round(rate * scenario.sample_size),
            error_source_distribution={"bank": 0.8, "customer": 0.2},
            data_source="constructed",
        )
        result = detect_segment(series)
        final_result = result
        if result.is_significant and windows_to_first_detection is None and is_anomalous_window:
            windows_to_first_detection = i + 1
        if i == span - 1:
            payshield_flagged_final = result.is_significant

    final_rate = rates[-1]
    final_window_truth = scenario.anomaly_duration_windows > 0

    baseline_a_flagged = final_rate > 0.20  # global fixed 20%
    prior = rates[:-1]
    prior_mean = sum(prior) / len(prior)
    prior_var = sum((r - prior_mean) ** 2 for r in prior) / max(len(prior) - 1, 1)
    prior_std = prior_var ** 0.5
    baseline_b_flagged = abs(final_rate - prior_mean) / prior_std >= 3 if prior_std > 0 else False
    baseline_c_flagged = final_rate > 0.12  # segment fixed 12%

    return ScenarioOutcome(
        scenario=scenario,
        final_window_truth=final_window_truth,
        payshield_flagged=payshield_flagged_final,
        baseline_a_flagged=baseline_a_flagged,
        baseline_b_flagged=baseline_b_flagged,
        baseline_c_flagged=baseline_c_flagged,
        windows_to_first_detection=windows_to_first_detection,
    )


@dataclass
class Scores:
    precision: float
    recall: float
    f1: float
    false_positive_rate: float


def score_approach(outcomes: list[ScenarioOutcome], pick: Callable[[ScenarioOutcome], bool]) -> Scores:
    tp = fp = fn = tn = 0
    for o in outcomes:
        predicted = pick(o)
        truth = o.final_window_truth
        if predicted and truth:
            tp += 1
        elif predicted and not truth:
            fp += 1
        elif not predicted and truth:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) > 0 else float("nan")
    return Scores(precision, recall, f1, fpr)


def _fmt(v: float) -> str:
    return "n/a" if v != v else f"{v:.2f}"  # v != v is the NaN check


def run_evaluation() -> dict:
    print("=== PAYSHIELD evaluation harness ===")
    print(f"Running {len(SCENARIOS)} scenarios (magnitude/duration/volume/control sweep)\n")

    outcomes = [run_scenario(s) for s in SCENARIOS]

    print("--- Per-scenario results ---\n")
    for o in outcomes:
        correct = o.payshield_flagged == o.final_window_truth
        detected = f" (detected after {o.windows_to_first_detection} window(s))" if o.windows_to_first_detection else ""
        print(f"{'OK  ' if correct else 'MISS'} {o.scenario.label:<55} truth={o.final_window_truth} payshield={o.payshield_flagged}{detected}")

    detection_delays = [o.windows_to_first_detection for o in outcomes if o.final_window_truth and o.windows_to_first_detection is not None]
    mean_windows = sum(detection_delays) / len(detection_delays) if detection_delays else None

    print("\n--- Aggregate scores ---\n")
    approaches: list[tuple[str, Callable[[ScenarioOutcome], bool]]] = [
        ("Baseline A — global fixed threshold (20%, all segments)", lambda o: o.baseline_a_flagged),
        ("Baseline B — segment rolling average (fixed z-score, NOT sample-size-aware)", lambda o: o.baseline_b_flagged),
        ("Baseline C — segment fixed threshold (12%, not history-derived)", lambda o: o.baseline_c_flagged),
        ("PayShield — segment-adaptive EWMA baseline + sample-size-aware z-score", lambda o: o.payshield_flagged),
    ]
    scores: dict[str, Scores] = {}
    for label, pick in approaches:
        s = score_approach(outcomes, pick)
        scores[label] = s
        print(f"{label:<70} precision={_fmt(s.precision)} recall={_fmt(s.recall)} f1={_fmt(s.f1)} FPR={_fmt(s.false_positive_rate)}")

    print(f"\nMean windows-to-detection for real anomalies (PayShield): {mean_windows:.2f}" if mean_windows else "\nMean windows-to-detection: n/a")

    payshield_score = scores["PayShield — segment-adaptive EWMA baseline + sample-size-aware z-score"]
    print("\n=== SUMMARY ===")
    print(f"PayShield: precision={_fmt(payshield_score.precision)} recall={_fmt(payshield_score.recall)} FPR={_fmt(payshield_score.false_positive_rate)}")
    if payshield_score.false_positive_rate == payshield_score.false_positive_rate and payshield_score.false_positive_rate > 0.15:
        print("WARNING: false-positive rate exceeds the stop-condition guidance — retune before treating this as demo-ready.")

    return {
        "scores": {k: v.__dict__ for k, v in scores.items()},
        "mean_windows_to_detection": mean_windows,
    }


async def _write_to_supabase(result: dict) -> None:
    from app.core.config import get_settings
    from app.core.supabase_client import get_supabase

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print(
            "\n(SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set — skipping write, dashboard's "
            "Evaluation tab will show the 'run evaluation' prompt until this is configured)"
        )
        return
    try:
        supabase = await get_supabase()
        payshield = result["scores"]["PayShield — segment-adaptive EWMA baseline + sample-size-aware z-score"]
        mean_windows = result["mean_windows_to_detection"]
        import datetime as _dt

        def _nz(v):
            return None if v != v else v

        await supabase.table("evaluation_runs").insert(
            {
                "batch_label": f"eval_{_dt.datetime.now(_dt.timezone.utc).isoformat()}",
                "precision": _nz(payshield["precision"]),
                "recall": _nz(payshield["recall"]),
                "f1": _nz(payshield["f1"]),
                "false_positive_rate": _nz(payshield["false_positive_rate"]),
                "mean_time_to_detection_seconds": mean_windows * 5 * 60 if mean_windows else None,
                "baseline_a_correct": result["scores"]["Baseline A — global fixed threshold (20%, all segments)"]["f1"] > 0,
                "baseline_b_correct": result["scores"]["Baseline B — segment rolling average (fixed z-score, NOT sample-size-aware)"]["f1"] > 0,
                "baseline_c_correct": result["scores"]["Baseline C — segment fixed threshold (12%, not history-derived)"]["f1"] > 0,
                "payshield_correct": payshield["f1"] > 0,
                "notes": f"{len(SCENARIOS)} scenarios, magnitude/duration/volume/control sweep",
            }
        ).execute()
        print("\nWrote evaluation summary to Supabase (evaluation_runs).")
    except Exception:  # noqa: BLE001 - best-effort, never fail the script
        logger.exception("Failed to write evaluation summary to Supabase (non-fatal)")


if __name__ == "__main__":
    result = run_evaluation()
    asyncio.run(_write_to_supabase(result))
