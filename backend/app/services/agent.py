"""
PAYSHIELD — agent loop (port of the former src/lib/agentLoop.ts)

The wiring that connects the already-independently-tested pieces
(aggregator, detector, decision engine, alert-text generator) into the
full DETECT -> DIAGNOSE -> DECIDE -> ACT -> OBSERVE -> ADAPT cycle.

Invoked on a schedule by GitHub Actions calling /api/cron/detect, NOT
per-webhook.

Idempotency: each segment's agent_state row tracks last_window_end,
the canonical window it was last fully evaluated against. If this
cycle's detection for a segment produces the SAME window_end (meaning
no new canonical window has completed since last time), that segment
is skipped entirely: no new history row, no re-classification, no
duplicate alert/LLM call. Running the detection cycle twice in a row
on unchanged data is therefore a safe no-op.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Coroutine

from postgrest.exceptions import APIError

from app.core.supabase_client import get_supabase
from app.models.schemas import AgentStateRecord, DetectionResult
from app.services.aggregator import build_segment_series_for_all_segments, persist_segment_windows
from app.services.alert_text import generate_alert_text
from app.services.blast_radius import compute_blast_radius
from app.services.decision_engine import decide
from app.services.detector import WindowedSegmentSeries, detect_segment


class AgentLoopResult:
    def __init__(self) -> None:
        self.segments_evaluated = 0
        self.segments_skipped_idempotent = 0
        self.state_transitions: list[dict] = []
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "segmentsEvaluated": self.segments_evaluated,
            "segmentsSkippedIdempotent": self.segments_skipped_idempotent,
            "stateTransitions": self.state_transitions,
            "errors": self.errors,
        }


async def _try(coro: Coroutine[Any, Any, Any], label: str, errors: list[str]) -> None:
    try:
        await coro
    except APIError as e:
        errors.append(f"{label}: {e.message}")


async def run_agent_loop_cycle(merchant_id: str, now: datetime | None = None) -> AgentLoopResult:
    now = now or datetime.now(timezone.utc)
    supabase = await get_supabase()
    bundle = await build_segment_series_for_all_segments(merchant_id, now)

    # Persist every canonical window computed this cycle BEFORE running
    # detection, so the historical record exists even if something
    # downstream throws.
    persist_error = await persist_segment_windows(bundle.buckets_by_segment, merchant_id)

    detections: list[DetectionResult] = [detect_segment(s) for s in bundle.series]

    result = AgentLoopResult()
    if persist_error:
        result.errors.append(f"segment_windows persistence: {persist_error}")

    try:
        resp = await supabase.table("agent_state").select("*").eq("merchant_id", merchant_id).execute()
        existing_states = {row["segment"]: row for row in (resp.data or [])}
    except APIError as e:
        raise RuntimeError(f"Failed to load existing agent_state: {e.message}") from e

    for detection in detections:
        existing_row = existing_states.get(detection.segment)
        previous_state: AgentStateRecord | None = (
            AgentStateRecord(
                segment=existing_row["segment"],
                state=existing_row["state"],
                entered_at=existing_row["entered_at"],
                evidence=existing_row.get("evidence"),
                consecutive_significant_windows=existing_row.get("consecutive_significant_windows", 0),
                good_streak=existing_row.get("good_streak", 0),
                action_taken=existing_row.get("action_taken") or [],
                last_window_end=existing_row.get("last_window_end"),
            )
            if existing_row
            else None
        )

        # ---- Idempotency guard: skip if this exact canonical window
        # was already the last one fully processed for this segment. ----
        if previous_state and previous_state.last_window_end == detection.window_end:
            result.segments_skipped_idempotent += 1
            continue

        result.segments_evaluated += 1

        try:
            outcome = decide(detection.segment, detection, detections, previous_state)
            from_state = previous_state.state if previous_state else None
            state_changed = from_state != outcome.new_state.state

            await _try(
                supabase.table("agent_state").upsert(
                    {
                        "segment": outcome.new_state.segment,
                        "merchant_id": merchant_id,
                        "state": outcome.new_state.state,
                        "entered_at": outcome.new_state.entered_at,
                        "consecutive_significant_windows": outcome.new_state.consecutive_significant_windows,
                        "good_streak": outcome.new_state.good_streak,
                        "evidence": outcome.new_state.evidence.model_dump() if outcome.new_state.evidence else None,
                        "action_taken": outcome.new_state.action_taken,
                        "last_window_end": outcome.new_state.last_window_end,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).execute(),
                f"agent_state write failed for {detection.segment}",
                result.errors,
            )

            # Audit trail - every cycle a segment is actually
            # (re-)evaluated appends one row, including the previous
            # state and the canonical window as a correlation ID.
            await _try(
                supabase.table("agent_state_history").insert(
                    {
                        "segment": detection.segment,
                        "merchant_id": merchant_id,
                        "state": outcome.new_state.state,
                        "previous_state": previous_state.state if previous_state else None,
                        "case_classification": outcome.case_classification,
                        "reasoning": outcome.reasoning,
                        "actions": outcome.actions_this_cycle,
                        "detection": detection.model_dump(),
                        "window_end": detection.window_end,
                    }
                ).execute(),
                f"agent_state_history write failed for {detection.segment}",
                result.errors,
            )

            # Enrich the specific segment_windows row that was
            # actually tested this cycle - a targeted UPDATE, never
            # part of the bulk upsert, so it never clobbers enrichment
            # written for an older window in a previous cycle.
            await _try(
                supabase.table("segment_windows")
                .update(
                    {
                        "baseline_rate": detection.ewma_baseline,
                        "sigma_deviation": detection.sigma_deviation,
                        "is_significant": detection.is_significant,
                    }
                )
                .eq("segment", detection.segment)
                .eq("window_end", detection.window_end)
                .eq("merchant_id", merchant_id)
                .execute(),
                f"segment_windows enrichment failed for {detection.segment}",
                result.errors,
            )

            # Internal recovery-policy layer - NOT Razorpay's retry
            # engine. PayShield's own downstream decision about whether
            # to initiate new recovery workflows for this segment.
            if previous_state is None:
                await _try(
                    supabase.table("recovery_policy")
                    .upsert(
                        {
                            "segment": detection.segment,
                            "merchant_id": merchant_id,
                            "status": "ACTIVE",
                            "reason": "Segment newly observed — default recovery policy.",
                            "related_state": outcome.new_state.state,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .execute(),
                    f"recovery_policy init failed for {detection.segment}",
                    result.errors,
                )
            if "SUPPRESS_OWN_RECOVERY_ACTIONS" in outcome.actions_this_cycle:
                await _try(
                    supabase.table("recovery_policy")
                    .upsert(
                        {
                            "segment": detection.segment,
                            "merchant_id": merchant_id,
                            "status": "SUPPRESSED",
                            "reason": outcome.reasoning,
                            "related_state": outcome.new_state.state,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .execute(),
                    f"recovery_policy suppress failed for {detection.segment}",
                    result.errors,
                )
            elif "RESTORE_NORMAL_POLICY" in outcome.actions_this_cycle:
                await _try(
                    supabase.table("recovery_policy")
                    .upsert(
                        {
                            "segment": detection.segment,
                            "merchant_id": merchant_id,
                            "status": "ACTIVE",
                            "reason": outcome.reasoning,
                            "related_state": outcome.new_state.state,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .execute(),
                    f"recovery_policy restore failed for {detection.segment}",
                    result.errors,
                )

            # LLM call (the ONLY place in the codebase) - only on a
            # genuine new state transition into an alert-worthy state.
            if state_changed and "GENERATE_MERCHANT_ALERT" in outcome.actions_this_cycle:
                s = next((x for x in bundle.series if x.segment == detection.segment), None)
                observed_amount_affected = s.final_window_failed_amount if s else 0
                alert_text, alert_source = await generate_alert_text(
                    detection.segment, outcome.new_state.state, detection, observed_amount_affected
                )
                await _try(
                    supabase.table("alerts")
                    .insert(
                        {
                            "segment": detection.segment,
                            "merchant_id": merchant_id,
                            "state": outcome.new_state.state,
                            "alert_text": alert_text,
                            "source": alert_source,
                            "blast_radius": compute_blast_radius(detection, bundle.series),
                        }
                    )
                    .execute(),
                    f"alerts write failed for {detection.segment}",
                    result.errors,
                )

            if state_changed:
                result.state_transitions.append(
                    {
                        "segment": detection.segment,
                        "from": from_state,
                        "to": outcome.new_state.state,
                        "caseClassification": outcome.case_classification,
                        "reasoning": outcome.reasoning,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one segment's failure
            # must not stop the others from being evaluated.
            result.errors.append(f"Segment {detection.segment} failed: {exc}")

    return result
