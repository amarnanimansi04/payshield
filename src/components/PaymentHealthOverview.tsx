import { FlowLine } from "./FlowLine";
import { StateBadge } from "./StateBadge";

export interface SegmentHealthRow {
  segment: string;
  state: string;
  currentRate: number;
  rateHistory: number[];
  /** disclosed provenance of the segment's most recent window — never
   * blur "constructed" (demo) data with real Razorpay traffic. */
  dataSource: "live" | "constructed" | "mixed";
  recoveryPolicyStatus: "ACTIVE" | "SUPPRESSED";
}

const DATA_SOURCE_LABEL: Record<string, string> = {
  live: "live",
  constructed: "demo data",
  mixed: "live + demo",
};

/** Friendly, judge-facing health label — the technical StateBadge is
 * still shown alongside (smaller) so the underlying classification
 * stays visible, not hidden. */
function friendlyHealth(state: string): { label: string; dot: string; tone: string } {
  if (state === "NORMAL" || state === "RESOLVED") {
    return { label: "Healthy", dot: "bg-[var(--flow-teal)]", tone: "text-[var(--flow-teal)]" };
  }
  if (state === "MONITORING") {
    return { label: "Watching", dot: "bg-[var(--flow-amber)]", tone: "text-[var(--flow-amber)]" };
  }
  return { label: "Degraded", dot: "bg-[var(--flow-coral)]", tone: "text-[var(--flow-coral)]" };
}

/** upi -> "UPI", "netbanking:HDFC" -> "Net Banking (HDFC)", "card" -> "Cards" */
function friendlySegmentName(segment: string): string {
  const [method, bank] = segment.split(":");
  const base: Record<string, string> = {
    upi: "UPI",
    card: "Cards",
    netbanking: "Net Banking",
    wallet: "Wallets",
    emi: "EMI",
  };
  const name = base[method] ?? method;
  return bank ? `${name} (${bank})` : name;
}

export function PaymentHealthOverview({ rows }: { rows: SegmentHealthRow[] }) {
  return (
    <div className="card px-6 py-5">
      <h2 className="font-display text-sm font-semibold text-[var(--text-primary)]">Payment Health Overview</h2>
      <p className="mt-1 text-xs text-[var(--text-tertiary)]">
        PayShield continuously monitors every payment segment against its own recent baseline —
        not a single global rule for the whole system.
      </p>

      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-[var(--text-tertiary)]">
          No segments observed yet. Once payment events start arriving, each segment will appear
          here as a live line.
        </p>
      ) : (
        <div className="mt-4 divide-y divide-[var(--line-700)]">
          {rows.map((row) => {
            const health = friendlyHealth(row.state);
            return (
              <div key={row.segment} className="flex flex-wrap items-center justify-between gap-4 py-3">
                <div className="flex min-w-[220px] items-center gap-3">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${health.dot}`} />
                  <span className="text-sm font-medium text-[var(--text-primary)]">
                    {friendlySegmentName(row.segment)}
                  </span>
                  <span className={`text-xs font-medium ${health.tone}`}>{health.label}</span>
                </div>

                <FlowLine rates={row.rateHistory} state={row.state} width={160} height={32} />

                <div className="flex items-center gap-3">
                  <span className="font-tabular w-14 text-right text-sm text-[var(--text-secondary)]">
                    {(row.currentRate * 100).toFixed(1)}%
                  </span>
                  <span
                    className={`text-xs ${row.recoveryPolicyStatus === "SUPPRESSED" ? "text-[var(--flow-coral)]" : "text-[var(--text-tertiary)]"}`}
                    title="PayShield's own downstream recovery-policy status for this segment — does not control Razorpay's retry engine"
                  >
                    recovery: {row.recoveryPolicyStatus.toLowerCase()}
                  </span>
                  <StateBadge state={row.state} />
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                      row.dataSource === "live" ? "text-[var(--text-tertiary)]" : "text-[var(--flow-amber)]"
                    }`}
                    title="Disclosed data provenance for this segment's most recent window"
                  >
                    {DATA_SOURCE_LABEL[row.dataSource] ?? row.dataSource}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
