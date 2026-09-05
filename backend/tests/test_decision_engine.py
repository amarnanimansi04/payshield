"""
PAYSHIELD — decision engine tests (port of scripts/verify-decision-engine.ts)

Exercises all five cases (A-E) of the state machine with hand-crafted
DetectionResult inputs, plus the idempotency property: calling decide()
twice with an IDENTICAL DetectionResult and previous_state must
produce an IDENTICAL output (since sustained_significant_windows /
consecutive_normal_windows are data-derived, not operational counters).
"""

from datetime import datetime, timedelta, timezone

from app.models.schemas import AgentStateRecord, DetectionResult
from app.services.decision_engine import decide

WINDOW_END = datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc).isoformat()
WINDOW_START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat()


def make_detection(**overrides) -> DetectionResult:
    base = dict(
        segment="upi:bank",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        current_rate=0.05,
        ewma_baseline=0.05,
        sigma_deviation=0.0,
        significance_score=0.5,
        sample_size=1000,
        is_significant=False,
        sustained_significant_windows=0,
        consecutive_normal_windows=1,
        dominant_error_source={"source": "bank", "share": 0.87},
        trend_projection=None,
    )
    base.update(overrides)
    return DetectionResult(**base)


def test_case_a_no_deviation():
    det = make_detection(is_significant=False, sustained_significant_windows=0)
    out = decide("upi:bank", det, [det], None)
    assert out.case_classification == "A"
    assert out.new_state.state == "NORMAL"
    assert out.actions_this_cycle == []


def test_case_d_significant_but_not_sustained():
    det = make_detection(is_significant=True, sigma_deviation=4.2, sustained_significant_windows=1)
    out = decide("upi:bank", det, [det], None)
    assert out.case_classification == "D"
    assert out.new_state.state == "MONITORING"
    assert out.actions_this_cycle == []


def test_case_b_sustained_isolated():
    prior = make_detection(is_significant=True, sigma_deviation=4.2, sustained_significant_windows=1)
    prior_out = decide("upi:bank", prior, [prior], None)

    det = make_detection(is_significant=True, sigma_deviation=4.5, sustained_significant_windows=2)
    out = decide("upi:bank", det, [det], prior_out.new_state)
    assert out.case_classification == "B"
    assert out.new_state.state == "ISOLATED_DEGRADATION"
    assert "SUPPRESS_OWN_RECOVERY_ACTIONS" in out.actions_this_cycle
    assert "RECOMMEND_ALTERNATIVE_METHOD_TEXT_ONLY" in out.actions_this_cycle  # bank-side

    # Idempotency: identical inputs -> identical output
    out_repeat = decide("upi:bank", det, [det], prior_out.new_state)
    assert out_repeat.case_classification == out.case_classification
    assert out_repeat.new_state.state == out.new_state.state
    assert out_repeat.new_state.consecutive_significant_windows == out.new_state.consecutive_significant_windows


def test_case_c_systemic():
    prior = make_detection(is_significant=True, sustained_significant_windows=1)
    prior_out = decide("upi:bank", prior, [prior], None)

    this_det = make_detection(is_significant=True, sigma_deviation=4.5, sustained_significant_windows=2)
    other_det = make_detection(segment="card:bank", is_significant=True, sigma_deviation=3.8, sustained_significant_windows=2)
    out = decide("upi:bank", this_det, [this_det, other_det], prior_out.new_state)
    assert out.case_classification == "C"
    assert out.new_state.state == "SYSTEMIC_DEGRADATION"


def test_case_e_escalation():
    old_entered = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
    old_state = AgentStateRecord(
        segment="upi:bank", state="ISOLATED_DEGRADATION", entered_at=old_entered,
        evidence=None, consecutive_significant_windows=2, good_streak=0, action_taken=[], last_window_end=None,
    )
    det = make_detection(is_significant=True, sigma_deviation=4.5, sustained_significant_windows=6)
    out = decide("upi:bank", det, [det], old_state)
    assert out.case_classification == "E"
    assert out.new_state.state == "ESCALATED"
    assert "ESCALATE" in out.actions_this_cycle


def test_resolution_after_two_consecutive_normal_windows():
    degraded_entered = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    degraded_state = AgentStateRecord(
        segment="upi:bank", state="ISOLATED_DEGRADATION", entered_at=degraded_entered,
        evidence=None, consecutive_significant_windows=2, good_streak=0, action_taken=[], last_window_end=None,
    )
    det1 = make_detection(is_significant=False, sustained_significant_windows=0, consecutive_normal_windows=1)
    out1 = decide("upi:bank", det1, [det1], degraded_state)
    assert out1.new_state.state == "ISOLATED_DEGRADATION", "must not flap back to normal after only 1 good window"

    det2 = make_detection(is_significant=False, sustained_significant_windows=0, consecutive_normal_windows=2)
    out2 = decide("upi:bank", det2, [det2], out1.new_state)
    assert out2.new_state.state == "RESOLVED"
    assert "RESTORE_NORMAL_POLICY" in out2.actions_this_cycle
