"""
PAYSHIELD — demo scenario injector (pure) (port of the former
src/lib/demoScenarios.ts)

Builds a batch of NormalizedEvent rows for one of the four demo
scenarios, entirely offline - no Supabase, no network. The API route
(api/demo.py) is the only thing that writes these rows to the
database. Keeping the SHAPE of each scenario a pure function means
it's independently unit-testable (tests/test_demo_scenarios.py),
exactly like the detector and decision engine.

Every event produced here has source="constructed" - NEVER "live".
This is demo-only, structurally disclosed data. It must never be
confused with real Razorpay test-mode traffic.

Timestamps are backdated to align with canonical windows that are
already COMPLETE relative to "now", so a single detection cycle run
immediately after injection sees a full, data-derived sustained-window
(or consecutive-normal-window) count - no need to wait multiple real
5-minute cron cycles for a demo to reach an actionable state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.evaluation.generator import GenerateBatchOptions, generate_synthetic_batch, seeded_rng
from app.models.schemas import DemoScenario, ErrorSource, NormalizedEvent
from app.services.aggregator import WINDOW_MINUTES, canonical_window_end
from app.services.segment import normalize_event

DEMO_SEED = 20260101  # fixed seed - reproducible demo batches
BASELINE_WINDOWS = 6  # healthy history before the interesting part
ANOMALY_WINDOWS = 3  # comfortably past SUSTAINED_WINDOWS_FOR_ACTIONABLE (2)
SAMPLE_SIZE_PER_WINDOW = 200  # well above MIN_SAMPLE_SIZE_FOR_SIGNIFICANCE (20)

# The "recovery" scenario is deliberately layered ON TOP of whatever
# constructed events already exist (it never resets first — the real
# demo flow injects recovery right after an incident, no reset in
# between, to keep the SAME audit trail/incident-history visible
# through the resolution). Since events only ever accumulate (each
# injection adds new, uniquely-id'd rows; nothing replaces the
# still-present anomalous rows in the same canonical windows), a
# recovery batch the same size as the incident it's resolving only
# DILUTES the failure rate, it doesn't clear it — and dilution alone
# isn't enough to cross back under the 3-sigma significance bar at
# n≈400. Found via live testing (reset → inject systemic → run cycle →
# inject recovery → run cycle never resolved), not by inspection. A
# much larger clean batch, mixed into the same windows, statistically
# dominates the leftover anomalous rows instead of merely diluting
# them - this is a synthetic-data-volume tweak, not a detection-logic
# change; detector.py/decision_engine.py are untouched. Only applied to
# the 3 final-phase windows (see only_final_phase below) - the 6
# baseline-phase windows a prior incident left behind are already
# clean and don't need re-touching, which keeps the total batch (and
# injection latency) well below a naive "boost all 9 windows" approach.
RECOVERY_SAMPLE_SIZE_PER_WINDOW = 2500

DEMO_SECONDARY_SEGMENT_BANK = "HDFC"

BASELINE_RATES = {"upi": 0.05, "netbanking": 0.04, "card": 0.06}
# NOTE: because the EWMA baseline freezes during a flagged anomaly
# (see services/detector.py's compute_clean_baselines), these
# magnitudes just need to clear the 3-sigma bar comfortably on window
# 1 - they no longer need extra margin to survive baseline drift on
# windows 2/3, since the baseline no longer drifts during an ongoing,
# already-flagged incident.
ANOMALOUS_RATE = {"upi": 0.19, "netbanking": 0.17}
BANK_SIDE_FAILURE_MIX: dict[ErrorSource, float] = {"bank": 0.87, "customer": 0.13}
ORDINARY_FAILURE_MIX: dict[ErrorSource, float] = {"customer": 0.6, "bank": 0.4}


@dataclass
class SegmentSpec:
    method: str
    baseline_rate: float
    final_rate: float
    mix: dict[ErrorSource, float]
    bank: str | None = None


@dataclass
class DemoScenarioPlan:
    scenario: DemoScenario
    events: list[NormalizedEvent]
    affected_segments: list[str]
    summary: str


def _hash_string(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def _build_events_across_windows(
    segments: list[SegmentSpec],
    final_phase_windows: int,
    now: datetime,
    seed_salt: int,
    sample_size: int = SAMPLE_SIZE_PER_WINDOW,
    only_final_phase: bool = False,
) -> list[NormalizedEvent]:
    """`only_final_phase=True` skips generating the BASELINE_WINDOWS
    history prefix entirely, producing events only for the most recent
    `final_phase_windows` — same window boundaries as a normal call
    (still aligned off the same canonical_end/total_windows math), just
    without redundantly re-populating windows that a prior injection in
    the same range already filled. Used by the "recovery" scenario: it
    layers onto an existing incident's windows rather than establishing
    fresh history, so regenerating the untouched, already-clean
    baseline windows would only add cost, not correctness."""
    window_minutes = WINDOW_MINUTES
    canonical_end = canonical_window_end(now, window_minutes)
    window_delta = timedelta(minutes=window_minutes)
    total_windows = BASELINE_WINDOWS + final_phase_windows
    history_start = canonical_end - total_windows * window_delta

    events: list[NormalizedEvent] = []
    rng_counter = 0
    window_range = range(BASELINE_WINDOWS, total_windows) if only_final_phase else range(total_windows)
    for seg in segments:
        rng = seeded_rng(DEMO_SEED + seed_salt + _hash_string(seg.method + (seg.bank or "") + str(rng_counter)))
        rng_counter += 1
        for w in window_range:
            window_start = history_start + w * window_delta
            window_end = window_start + window_delta
            is_final_phase = w >= BASELINE_WINDOWS
            rate = seg.final_rate if is_final_phase else seg.baseline_rate

            raw = generate_synthetic_batch(
                GenerateBatchOptions(
                    method=seg.method,
                    bank=seg.bank,
                    total_attempts=sample_size,
                    failure_rate=rate,
                    failure_source_mix=seg.mix if is_final_phase else ORDINARY_FAILURE_MIX,
                    rng=rng,
                    window_start=window_start,
                    window_end=window_end,
                    mode="constructed",
                )
            )
            for r in raw:
                events.append(normalize_event(r, "constructed", str(uuid4()), None))
    return events


def build_demo_scenario(
    scenario: DemoScenario, now: datetime | None = None, seed_salt: int = 0
) -> DemoScenarioPlan:
    now = now or datetime.now(timezone.utc)

    if scenario == "normal":
        events = _build_events_across_windows(
            [
                SegmentSpec("upi", BASELINE_RATES["upi"], BASELINE_RATES["upi"], ORDINARY_FAILURE_MIX),
                SegmentSpec(
                    "netbanking", BASELINE_RATES["netbanking"], BASELINE_RATES["netbanking"],
                    ORDINARY_FAILURE_MIX, bank=DEMO_SECONDARY_SEGMENT_BANK,
                ),
                SegmentSpec("card", BASELINE_RATES["card"], BASELINE_RATES["card"], ORDINARY_FAILURE_MIX),
            ],
            ANOMALY_WINDOWS, now, seed_salt,
        )
        return DemoScenarioPlan(
            scenario, events, [],
            f"Injected {len(events)} constructed events across upi/netbanking:"
            f"{DEMO_SECONDARY_SEGMENT_BANK}/card, all at their ordinary baseline rates — "
            f"nothing should be flagged.",
        )

    if scenario == "isolated-degradation":
        events = _build_events_across_windows(
            [
                SegmentSpec("upi", BASELINE_RATES["upi"], ANOMALOUS_RATE["upi"], BANK_SIDE_FAILURE_MIX),
                SegmentSpec(
                    "netbanking", BASELINE_RATES["netbanking"], BASELINE_RATES["netbanking"],
                    ORDINARY_FAILURE_MIX, bank=DEMO_SECONDARY_SEGMENT_BANK,
                ),
                SegmentSpec("card", BASELINE_RATES["card"], BASELINE_RATES["card"], ORDINARY_FAILURE_MIX),
            ],
            ANOMALY_WINDOWS, now, seed_salt,
        )
        return DemoScenarioPlan(
            scenario, events, ["upi"],
            f"Injected a {ANOMALOUS_RATE['upi'] * 100:.0f}% failure rate in \"upi\" "
            f"(baseline ~{BASELINE_RATES['upi'] * 100:.0f}%) for {ANOMALY_WINDOWS} consecutive "
            f"canonical windows, predominantly bank-side. netbanking:{DEMO_SECONDARY_SEGMENT_BANK} "
            f"and card stay at their ordinary baseline — this should localize to ONE segment "
            f"(ISOLATED_DEGRADATION).",
        )

    if scenario == "systemic-degradation":
        events = _build_events_across_windows(
            [
                SegmentSpec("upi", BASELINE_RATES["upi"], ANOMALOUS_RATE["upi"], BANK_SIDE_FAILURE_MIX),
                SegmentSpec(
                    "netbanking", BASELINE_RATES["netbanking"], ANOMALOUS_RATE["netbanking"],
                    BANK_SIDE_FAILURE_MIX, bank=DEMO_SECONDARY_SEGMENT_BANK,
                ),
                SegmentSpec("card", BASELINE_RATES["card"], BASELINE_RATES["card"], ORDINARY_FAILURE_MIX),
            ],
            ANOMALY_WINDOWS, now, seed_salt,
        )
        return DemoScenarioPlan(
            scenario, events, ["upi", f"netbanking:{DEMO_SECONDARY_SEGMENT_BANK}"],
            f"Injected simultaneous elevated failure rates in BOTH \"upi\" "
            f"(~{ANOMALOUS_RATE['upi'] * 100:.0f}%) and \"netbanking:{DEMO_SECONDARY_SEGMENT_BANK}\" "
            f"(~{ANOMALOUS_RATE['netbanking'] * 100:.0f}%) for {ANOMALY_WINDOWS} consecutive windows, "
            f"both predominantly bank-side. \"card\" stays healthy throughout as a control — this "
            f"should classify as SYSTEMIC_DEGRADATION, not two unrelated isolated incidents.",
        )

    # "recovery"
    recovery_windows = 3
    events = _build_events_across_windows(
        [
            SegmentSpec("upi", BASELINE_RATES["upi"], BASELINE_RATES["upi"], ORDINARY_FAILURE_MIX),
            SegmentSpec(
                "netbanking", BASELINE_RATES["netbanking"], BASELINE_RATES["netbanking"],
                ORDINARY_FAILURE_MIX, bank=DEMO_SECONDARY_SEGMENT_BANK,
            ),
            SegmentSpec("card", BASELINE_RATES["card"], BASELINE_RATES["card"], ORDINARY_FAILURE_MIX),
        ],
        recovery_windows, now, seed_salt,
        sample_size=RECOVERY_SAMPLE_SIZE_PER_WINDOW,
        only_final_phase=True,
    )
    return DemoScenarioPlan(
        scenario, events, [],
        f"Injected {recovery_windows} consecutive windows of high-volume, ordinary-baseline traffic "
        f"for upi/netbanking:{DEMO_SECONDARY_SEGMENT_BANK}/card — enough clean volume to outweigh any "
        f"still-present incident data in the same windows, not just dilute it. Run the detection "
        f"cycle now to see any currently-degraded segment resolve back to NORMAL and its recovery "
        f"policy restore to ACTIVE.",
    )
