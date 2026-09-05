"""
PAYSHIELD — blast radius (port of the former computeBlastRadius in
src/lib/agentLoop.ts)

Every number here is backward-looking/observed except
estimated_projection, which is always separately labeled and never
given the same visual weight as an observed figure.
"""

from __future__ import annotations

from app.models.schemas import DetectionResult
from app.services.detector import WindowedSegmentSeries


def compute_blast_radius(detection: DetectionResult, series: list[WindowedSegmentSeries]) -> dict:
    s = next((x for x in series if x.segment == detection.segment), None)
    affected_segment_transaction_volume_paise = s.final_window_total_amount if s else 0
    affected_segment_failed_volume_paise = s.final_window_failed_amount if s else 0

    # The correct denominator for a "share of total system volume"
    # metric is TOTAL transaction volume (captured + failed) across
    # every segment observed this cycle - never the sum of FAILED
    # amounts alone (that would silently answer a different question:
    # "what share of all failures happened here", not "what share of
    # all payment volume passed through this degraded segment").
    total_system_transaction_volume_paise = sum(x.final_window_total_amount for x in series)

    return {
        "affected_transactions_observed": s.final_window_failure_count if s else 0,
        "observed_affected_failed_volume_paise": affected_segment_failed_volume_paise,
        "affected_segment_transaction_volume_paise": affected_segment_transaction_volume_paise,
        "total_system_transaction_volume_paise": total_system_transaction_volume_paise,
        "pct_of_total_system_volume_affected": (
            affected_segment_transaction_volume_paise / total_system_transaction_volume_paise
            if total_system_transaction_volume_paise > 0
            else 0
        ),
        "pct_of_segment_volume_failed": (
            affected_segment_failed_volume_paise / affected_segment_transaction_volume_paise
            if affected_segment_transaction_volume_paise > 0
            else 0
        ),
        "deviation_sigma": detection.sigma_deviation,
        "estimated_projection": detection.trend_projection.model_dump() if detection.trend_projection else None,
    }
