"""
PAYSHIELD — decision engine (port of the former src/lib/decisionEngine.ts)

Entirely deterministic - action selection stays rule-based on purpose,
not as a compromise - it's more auditable for a financial-adjacent
action than an ML policy would be.

State diagram:
  NORMAL -> MONITORING -> (ISOLATED_DEGRADATION | SYSTEMIC_DEGRADATION)
         -> (RESOLVED -> NORMAL) | ESCALATED

This module has no I/O - it's a pure state-transition function over
DetectionResult inputs and the previous AgentStateRecord, which makes
it independently testable (see tests/test_decision_engine.py) exactly
like the detector was.

Idempotency: the "how many consecutive windows has this been
significant" count is READ directly from
detection.sustained_significant_windows, which the detector derives
fresh from the data every time - it is NOT an operational counter this
module increments per invocation. Calling decide() twice with the
identical DetectionResult and previous_state therefore always produces
the identical output. Actual write-skipping for a window that's
already been fully processed happens one layer up, in services/agent.py,
using AgentStateRecord.last_window_end.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from app.core.time_utils import now_iso
from app.models.schemas import AgentAction, AgentState, AgentStateRecord, DetectionResult
from app.services.detector import SUSTAINED_WINDOWS_FOR_ACTIONABLE

ESCALATION_DURATION_SECONDS = 20 * 60  # 20 minutes sustained without
# resolving triggers ESCALATED - a duration threshold, not a vibe.

HYSTERESIS_GOOD_WINDOWS_TO_RESOLVE = 2  # require TWO consecutive
# non-significant windows before declaring RESOLVED, not one - the
# anti-flapping guard ("oscillating right at the threshold").

CaseClassification = Literal["A", "B", "C", "D", "E"]


class DecisionEngineOutput:
    def __init__(
        self,
        new_state: AgentStateRecord,
        actions_this_cycle: list[AgentAction],
        case_classification: CaseClassification,
        reasoning: str,
    ):
        self.new_state = new_state
        self.actions_this_cycle = actions_this_cycle
        self.case_classification = case_classification
        self.reasoning = reasoning


def decide(
    segment: str,
    detection: DetectionResult,
    all_segment_detections: list[DetectionResult],
    previous_state: AgentStateRecord | None,
) -> DecisionEngineOutput:
    other_significant_segments = [
        d for d in all_segment_detections if d.segment != detection.segment and d.is_significant
    ]
    is_systemic = detection.is_significant and len(other_significant_segments) > 0

    # Data-derived, not an operational counter - see module doc comment.
    sustained_significant_windows = detection.sustained_significant_windows

    # ---- Case A: no deviation at all ----
    if not detection.is_significant and (previous_state is None or previous_state.state == "NORMAL"):
        return _terminal(
            "A", "NORMAL", [], "No deviation from this segment's own baseline — nothing to do.",
            segment, detection, previous_state, sustained_significant_windows,
        )

    # ---- Currently in a degraded/monitoring state: check for resolution first ----
    if previous_state and previous_state.state not in ("NORMAL", "RESOLVED"):
        if not detection.is_significant:
            consecutive_good_windows = detection.consecutive_normal_windows
            if consecutive_good_windows >= HYSTERESIS_GOOD_WINDOWS_TO_RESOLVE:
                return _terminal(
                    "A", "RESOLVED", ["RESTORE_NORMAL_POLICY"],
                    f"Rate has returned to baseline for {consecutive_good_windows} consecutive windows — "
                    f"resolving and restoring normal recovery policy.",
                    segment, detection, previous_state, 0, consecutive_good_windows,
                )
            return _terminal(
                "E" if previous_state.state == "ESCALATED" else ("C" if is_systemic else "B"),
                previous_state.state, [],
                f"Rate improved this window, but only {consecutive_good_windows}/"
                f"{HYSTERESIS_GOOD_WINDOWS_TO_RESOLVE} consecutive good windows — "
                f"holding current state to avoid flapping.",
                segment, detection, previous_state, sustained_significant_windows, consecutive_good_windows,
            )

        # Still significant. Check for escalation by duration.
        entered_at = datetime.fromisoformat(previous_state.entered_at.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - entered_at).total_seconds()
        if elapsed >= ESCALATION_DURATION_SECONDS and previous_state.state != "ESCALATED":
            return _terminal(
                "E", "ESCALATED", ["ESCALATE", "GENERATE_MERCHANT_ALERT"],
                f"Degradation has persisted for over {round(elapsed / 60)} minutes without "
                f"resolving — escalating severity.",
                segment, detection, previous_state, sustained_significant_windows,
            )

    # ---- Case D: significant but not yet sustained long enough to act ----
    if detection.is_significant and sustained_significant_windows < SUSTAINED_WINDOWS_FOR_ACTIONABLE:
        return _terminal(
            "D", "MONITORING", [],
            f"Deviation detected ({detection.sigma_deviation:.1f} sigma) but only "
            f"{sustained_significant_windows} window(s) so far — watching, not yet actionable.",
            segment, detection, previous_state, sustained_significant_windows,
        )

    # ---- Case C: systemic (multiple segments simultaneously) ----
    if is_systemic:
        return _terminal(
            "C", "SYSTEMIC_DEGRADATION",
            ["SUPPRESS_OWN_RECOVERY_ACTIONS", "GENERATE_MERCHANT_ALERT", "FLAG_TRANSACTIONS"],
            f"Sustained deviation in this segment ({sustained_significant_windows} windows) AND "
            f"{len(other_significant_segments)} other segment(s) simultaneously — classifying as "
            f"systemic, not isolated.",
            segment, detection, previous_state, sustained_significant_windows,
        )

    # ---- Case B: isolated, sustained, actionable ----
    if detection.is_significant:
        actions: list[AgentAction] = [
            "SUPPRESS_OWN_RECOVERY_ACTIONS", "GENERATE_MERCHANT_ALERT", "FLAG_TRANSACTIONS",
        ]
        if detection.dominant_error_source and detection.dominant_error_source.get("source") == "bank":
            actions.append("RECOMMEND_ALTERNATIVE_METHOD_TEXT_ONLY")
        return _terminal(
            "B", "ISOLATED_DEGRADATION", actions,
            f"Sustained {detection.sigma_deviation:.1f}-sigma deviation for "
            f"{sustained_significant_windows} consecutive window(s), isolated to this segment "
            f"(no other segments affected) — suppressing own recovery actions here and alerting "
            f"the merchant.",
            segment, detection, previous_state, sustained_significant_windows,
        )

    return _terminal(
        "A", "NORMAL", [], "No deviation.", segment, detection, previous_state, sustained_significant_windows
    )


def _terminal(
    case_classification: CaseClassification,
    state: AgentState,
    actions: list[AgentAction],
    reasoning: str,
    segment: str,
    detection: DetectionResult,
    previous_state: AgentStateRecord | None,
    consecutive_significant_windows: int,
    good_streak: int = 0,
) -> DecisionEngineOutput:
    state_changed = (previous_state.state if previous_state else None) != state
    record = AgentStateRecord(
        segment=segment,
        state=state,
        entered_at=now_iso() if state_changed else previous_state.entered_at,  # type: ignore[union-attr]
        evidence=detection,
        consecutive_significant_windows=consecutive_significant_windows,
        good_streak=good_streak,
        action_taken=actions if state_changed else (previous_state.action_taken if previous_state else []),
        last_window_end=detection.window_end,
    )
    return DecisionEngineOutput(
        new_state=record,
        actions_this_cycle=actions if state_changed else [],
        case_classification=case_classification,
        reasoning=reasoning,
    )
