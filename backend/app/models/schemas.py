"""
PAYSHIELD — shared schemas (Pydantic port of the former src/lib/types.ts)

Every field is tagged with its provenance, exactly as it was in the
TypeScript version — this is not decoration, it's what keeps synthetic
evaluation/demo data structurally out of the live detector's input:

  [REAL]      - a field Razorpay's own Payment API/webhook returns
  [DERIVED]   - computed by us from REAL fields, zero invented data
  [INFERRED]  - derived, but involves a judgment call - must be
                disclosed wherever shown to a user/judge
  [SYNTHETIC] - data generated for evaluation/demo purposes only,
                never fed into the live detector's input except via
                the same code path real data takes (so the pipeline
                itself cannot tell it apart mid-computation - only the
                `source` tag discloses provenance afterward)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PaymentMethod = Literal["card", "upi", "netbanking", "wallet", "emi"]

# Razorpay's own error_source taxonomy - the single most important REAL
# field in the system: Razorpay's own attribution of where a failure
# originated, with zero inference required on our part.
ErrorSource = Literal["business", "customer", "bank", "gateway", "internal"]

DataSource = Literal["live", "constructed"]
WindowDataSource = Literal["live", "constructed", "mixed"]

AgentState = Literal[
    "NORMAL",
    "MONITORING",
    "ISOLATED_DEGRADATION",
    "SYSTEMIC_DEGRADATION",
    "ESCALATED",
    "RESOLVED",
]

AgentAction = Literal[
    "SUPPRESS_OWN_RECOVERY_ACTIONS",
    "GENERATE_MERCHANT_ALERT",
    "FLAG_TRANSACTIONS",
    "ESCALATE",
    "RESTORE_NORMAL_POLICY",
    # never executed via API - PayShield cannot force a payment-method
    # switch, no such Razorpay API exists.
    "RECOMMEND_ALTERNATIVE_METHOD_TEXT_ONLY",
]

RecoveryPolicyStatus = Literal["ACTIVE", "SUPPRESSED"]

DemoScenario = Literal[
    "normal", "systemic-degradation", "isolated-degradation", "recovery"
]


class RazorpayPaymentEvent(BaseModel):
    """Raw Razorpay payment webhook payload - the fields PayShield
    actually reads, for EITHER a payment.failed or payment.captured
    event. Names match Razorpay's real Payment entity schema. We
    ingest BOTH outcomes because a failure count alone has no
    denominator."""

    payment_id: str  # [REAL] e.g. "pay_..."
    order_id: Optional[str] = None  # [REAL]
    outcome: Literal["failed", "captured"]  # [DERIVED] which webhook event this came from
    method: PaymentMethod  # [REAL]
    amount: int  # [REAL] paise
    currency: str  # [REAL]
    created_at: int  # [REAL] unix timestamp
    bank: Optional[str] = None  # [REAL] 4-char bank code, netbanking-specific
    vpa: Optional[str] = None  # [REAL] UPI VPA, e.g. "gaurav@okhdfcbank"
    card_issuer: Optional[str] = None  # [REAL] sparse in test mode
    card_network: Optional[str] = None  # [REAL] e.g. "Visa"
    error_code: Optional[str] = None  # [REAL] null for captured payments
    error_description: Optional[str] = None  # [REAL]
    error_source: Optional[ErrorSource] = None  # [REAL] null for captured payments
    error_step: Optional[str] = None  # [REAL] e.g. "payment_authorization"
    error_reason: Optional[str] = None  # [REAL]


class NormalizedEvent(BaseModel):
    """Normalized event, as stored in Supabase. One row per payment."""

    event_id: str  # [DERIVED] our own idempotency key (uuid)
    razorpay_event_id: Optional[str] = None  # [REAL] x-razorpay-event-id -
    # the PRIMARY webhook idempotency key. Null for constructed/demo
    # events, which have no real Razorpay delivery to key off of.
    payment_id: str  # [REAL]
    outcome: Literal["failed", "captured"]  # [DERIVED]
    created_at: str  # [REAL] ISO timestamp
    method: PaymentMethod  # [REAL]
    bank: Optional[str] = None  # [REAL]
    vpa: Optional[str] = None  # [REAL]
    vpa_derived_bank_label: Optional[str] = None  # [INFERRED] - see services/segment.py
    card_issuer: Optional[str] = None  # [REAL]
    card_network: Optional[str] = None  # [REAL]
    error_code: Optional[str] = None  # [REAL]
    error_source: Optional[ErrorSource] = None  # [REAL]
    error_reason: Optional[str] = None  # [REAL]
    error_step: Optional[str] = None  # [REAL]
    amount: int  # [REAL] paise
    segment_primary: str  # [DERIVED] method x error_source, zero inference
    segment_bank: Optional[str] = None  # [REAL, used directly] netbanking bank code
    ingested_at: str  # [DERIVED]
    source: DataSource  # [DERIVED] disclosed generation method - "live" if
    # triggered through a real Razorpay test-mode transaction,
    # "constructed" if demo/eval-generated


class SegmentWindow(BaseModel):
    """One persisted, canonical-window aggregation row for one segment."""

    segment: str  # [DERIVED]
    window_start: str  # [DERIVED] canonical, epoch-aligned
    window_end: str  # [DERIVED] canonical, epoch-aligned - the stable
    # identifier for "which window is this", used for idempotency
    event_count: int  # [DERIVED] total attempts (captured + failed)
    failure_count: int  # [DERIVED]
    current_rate: Optional[float] = None  # [DERIVED] null = NO DATA, never a
    # silent 0% ("healthy") reading
    total_transaction_amount: int  # [DERIVED] paise, ALL attempts - the
    # correct denominator for any "% of total system volume" metric
    failed_transaction_amount: int  # [DERIVED] paise, failed only
    baseline_rate: Optional[float] = None  # [DERIVED/STATISTICAL]
    sigma_deviation: Optional[float] = None  # [DERIVED/STATISTICAL]
    is_significant: Optional[bool] = None  # [DERIVED/STATISTICAL]
    data_source: WindowDataSource = "live"  # [DERIVED] disclosed


class TrendProjection(BaseModel):
    assumption: str  # human-readable disclosure of what's assumed
    additional_windows_assumed: int
    projected_additional_amount: int  # paise - explicitly an estimate


class DetectionResult(BaseModel):
    """Detector output for one segment, one evaluation point."""

    segment: str
    window_start: str
    window_end: str  # canonical, epoch-aligned - doubles as the stable
    # per-window identifier used for idempotent re-evaluation
    current_rate: float
    ewma_baseline: float  # [DERIVED/STATISTICAL]
    sigma_deviation: float  # [DERIVED/STATISTICAL]
    significance_score: float = Field(
        description=(
            "[DERIVED/STATISTICAL] 0..1, a normal-CDF mapping of the "
            "sigma deviation. This is NOT a calibrated probability that "
            "the anomaly is 'real' - we have not established that "
            "mapping empirically. Always render as 'statistical "
            "significance', never as 'confidence' or 'probability this "
            "is real'."
        )
    )
    sample_size: int
    is_significant: bool  # [DERIVED/STATISTICAL]
    sustained_significant_windows: int = Field(
        description=(
            "how many consecutive trailing windows (including this "
            "one) were ALSO significant, recomputed fresh from the "
            "data every time - NOT an operational counter. Calling the "
            "detector twice on the same series always returns the "
            "same number."
        )
    )
    consecutive_normal_windows: int = Field(
        description="symmetric counterpart - consecutive trailing windows NOT significant"
    )
    dominant_error_source: Optional[dict] = None  # {"source": str, "share": float}
    trend_projection: Optional[TrendProjection] = None


class AgentStateRecord(BaseModel):
    segment: str
    state: AgentState
    entered_at: str
    evidence: Optional[DetectionResult] = None
    consecutive_significant_windows: int = 0
    good_streak: int = 0
    action_taken: list[AgentAction] = []
    last_window_end: Optional[str] = None


class RecoveryPolicyRecord(BaseModel):
    """PayShield's OWN internal recovery-policy layer. Does NOT control,
    pause, or touch Razorpay's native retry engine - no such API
    exists. ACTIVE = new PayShield recovery workflows may be initiated
    for this segment. SUPPRESSED = PayShield has stopped initiating new
    recovery workflows for this segment because a degradation is
    active."""

    segment: str
    status: RecoveryPolicyStatus
    reason: str
    related_state: AgentState
    updated_at: str
