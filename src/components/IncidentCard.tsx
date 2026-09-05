export interface FlaggedSegment {
  segment: string;
  state: string;
  /** every segment currently in a degraded/escalated state, including
   * `segment` itself — real, derived by scanning agent_state rows, not
   * invented. Length > 1 is what actually makes something "systemic". */
  affectedSegments: string[];
  currentRate: number;
  baseline: number;
  sigma: number;
  /** 0..1, a normal-CDF mapping of the sigma deviation — a
   * STATISTICAL SIGNIFICANCE score, not a calibrated probability that
   * the anomaly is "real". Never render this as "confidence" or
   * "probability this is real". */
  significanceScore: number;
  sustainedWindows: number;
  dominantErrorSource: { source: string; share: number } | null;
  affectedTransactions: number;
  observedAffectedFailedVolumeRupees: number;
  /** share of TOTAL system transaction volume (captured + failed,
   * every segment) that passed through this segment this window — a
   * volume-share metric, never a "% at risk" claim. */
  pctOfSystemVolume: number;
  /** share of THIS segment's own volume that failed. */
  pctOfSegmentVolumeFailed: number;
  projection: { assumption: string; projectedAdditionalRupees: number } | null;
  reasoning: string;
  caseClassification: string | null;
  actionsTaken: string[];
  alertText: string | null;
  alertSource: "llm" | "template" | null;
  recoveryPolicyStatus: "ACTIVE" | "SUPPRESSED";
}

const SEGMENT_DISPLAY: Record<string, string> = {
  upi: "UPI",
  card: "Cards",
  wallet: "Wallets",
  emi: "EMI",
};

function displaySegment(segment: string): string {
  const [method, bank] = segment.split(":");
  if (method === "netbanking" && bank) return `${bank} Net Banking`;
  return SEGMENT_DISPLAY[method] ?? method;
}

export function IncidentCard({ flagged }: { flagged: FlaggedSegment }) {
  const isSystemic = flagged.state === "SYSTEMIC_DEGRADATION" || flagged.affectedSegments.length > 1;
  const isEscalated = flagged.state === "ESCALATED";

  return (
    <div className="card border-[var(--flow-coral-dim)] px-6 py-6">
      <div className="flex items-center gap-2">
        <span className="text-lg">{isEscalated ? "🆘" : "🚨"}</span>
        <h2 className="font-display text-lg font-semibold text-[var(--flow-coral)]">
          {isEscalated
            ? "Payment Incident Escalated"
            : isSystemic
              ? "Systemic Payment Degradation Detected"
              : "Isolated Payment Degradation Detected"}
        </h2>
      </div>

      <dl className="mt-5 grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Problem</dt>
          <dd className="mt-1 text-sm text-[var(--text-primary)]">
            {flagged.dominantErrorSource?.source === "bank"
              ? "Bank-side payment degradation detected"
              : flagged.dominantErrorSource?.source === "gateway"
                ? "Gateway-side payment degradation detected"
                : "Payment degradation detected"}
          </dd>
        </div>

        <div>
          <dt className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Affected segments</dt>
          <dd className="mt-1 flex flex-wrap gap-1.5">
            {flagged.affectedSegments.map((s) => (
              <span
                key={s}
                className="rounded border border-[var(--flow-coral-dim)] px-2 py-0.5 text-xs text-[var(--flow-coral)]"
              >
                {displaySegment(s)}
              </span>
            ))}
          </dd>
        </div>

        <div>
          <dt className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Failure rate</dt>
          <dd className="mt-1 font-tabular text-sm text-[var(--text-primary)]">
            <span className="text-[var(--text-tertiary)]">baseline </span>
            {(flagged.baseline * 100).toFixed(1)}%
            <span className="mx-2 text-[var(--text-tertiary)]">→</span>
            <span className="text-[var(--flow-coral)]">now {(flagged.currentRate * 100).toFixed(1)}%</span>
          </dd>
        </div>

        <div>
          <dt className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">
            Estimated impact <span className="normal-case text-[var(--text-tertiary)]">(observed, this incident)</span>
          </dt>
          <dd className="mt-1 font-tabular text-sm text-[var(--text-primary)]">
            ₹{flagged.observedAffectedFailedVolumeRupees.toLocaleString("en-IN")} affected volume ·{" "}
            {(flagged.pctOfSystemVolume * 100).toFixed(1)}% of total system volume this window
          </dd>
        </div>

        <div>
          <dt className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">
            Statistical significance
          </dt>
          <dd className="mt-1 font-tabular text-sm text-[var(--text-primary)]">
            {(flagged.significanceScore * 100).toFixed(0)}%{" "}
            <span className="text-xs text-[var(--text-tertiary)]">
              ({flagged.sigma.toFixed(1)}σ deviation from this segment&apos;s own baseline — a statistical
              score, not a claim of certainty)
            </span>
          </dd>
        </div>

        <div>
          <dt className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Persisted for</dt>
          <dd className="mt-1 font-tabular text-sm text-[var(--text-primary)]">
            {flagged.sustainedWindows} consecutive detection window{flagged.sustainedWindows === 1 ? "" : "s"}
          </dd>
        </div>
      </dl>

      {flagged.projection && (
        <p className="mt-4 border-t hairline pt-3 text-xs text-[var(--text-tertiary)]">
          <span className="text-[var(--flow-amber)]">
            Estimated projection: +₹{flagged.projection.projectedAdditionalRupees.toLocaleString("en-IN")}
          </span>{" "}
          — {flagged.projection.assumption}
        </p>
      )}
    </div>
  );
}
