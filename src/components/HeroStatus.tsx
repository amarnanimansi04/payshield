/**
 * PAYSHIELD — hero status
 *
 * The first thing a judge sees. Positions the product as an agent,
 * not a monitoring screen: a system-wide status line plus three real,
 * derived stats — nothing here is invented for effect. Every number
 * comes straight from segment_windows/agent_state rows already
 * computed by the detector/decision engine (see page.tsx's
 * deriveHeroStats), just presented at a glance instead of buried in a
 * table.
 */

export type OverallStatus = "HEALTHY" | "MONITORING" | "ISOLATED" | "SYSTEMIC" | "ESCALATED";

export interface HeroStats {
  overallStatus: OverallStatus;
  /** how many distinct segments are currently in a degraded/escalated state */
  activeIncidentCount: number;
  /** sum of event_count across every persisted canonical window observed —
   * a real count of individual payment attempts PayShield has aggregated */
  transactionsAnalyzed: number;
  /** distinct segments PayShield currently has any state for */
  segmentsMonitored: number;
  /** sum of total_transaction_amount (captured+failed, paise->rupees)
   * across every persisted window — real observed payment volume that
   * has passed through PayShield's monitoring, not a claim about what
   * was "saved" */
  paymentVolumeMonitoredRupees: number;
  /** only set while an incident is active: the observed affected
   * failed volume for the flagged segment, in rupees — real, from the
   * same blast-radius calculation shown in the incident card below */
  flaggedVolumeRupees: number | null;
}

const STATUS_COPY: Record<OverallStatus, { label: string; dot: string; tone: string; note: string }> = {
  HEALTHY: {
    label: "Payment Infrastructure Healthy",
    dot: "bg-[var(--flow-teal)]",
    tone: "text-[var(--flow-teal)]",
    note: "No intervention required",
  },
  MONITORING: {
    label: "Anomaly Under Observation",
    dot: "bg-[var(--flow-amber)]",
    tone: "text-[var(--flow-amber)]",
    note: "Watching — not yet actionable",
  },
  ISOLATED: {
    label: "Isolated Payment Degradation Detected",
    dot: "bg-[var(--flow-coral)]",
    tone: "text-[var(--flow-coral)]",
    note: "Recovery policy engaged for the affected segment",
  },
  SYSTEMIC: {
    label: "Systemic Payment Degradation Detected",
    dot: "bg-[var(--flow-coral)]",
    tone: "text-[var(--flow-coral)]",
    note: "Recovery policy engaged across affected segments",
  },
  ESCALATED: {
    label: "Payment Incident Escalated",
    dot: "bg-[var(--flow-coral)]",
    tone: "text-[var(--flow-coral)]",
    note: "Awaiting manual attention",
  },
};

export function HeroStatus({
  stats,
  lastRefreshedLabel,
  triggering,
  bootstrapping,
  onRunCycle,
}: {
  stats: HeroStats;
  lastRefreshedLabel: string;
  triggering: boolean;
  bootstrapping?: boolean;
  onRunCycle: () => void;
}) {
  const status = STATUS_COPY[stats.overallStatus];
  const isCritical =
    stats.overallStatus === "ISOLATED" || stats.overallStatus === "SYSTEMIC" || stats.overallStatus === "ESCALATED";

  return (
    <div className={`card border-b-0 px-6 py-6 ${isCritical ? "incident-glow" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-baseline gap-3">
            <h1 className="font-display text-xl font-semibold text-[var(--text-primary)]">PayShield</h1>
            <span className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">
              Autonomous Payment Reliability Agent
            </span>
          </div>
          <p className="mt-1 max-w-lg text-sm text-[var(--text-secondary)]">
            Detects when payment failures become systemic incidents, explains why, and helps merchants
            respond before revenue is impacted.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <span className={`h-2.5 w-2.5 rounded-full ${status.dot} ${isCritical ? "animate-pulse" : ""}`} />
            <span className={`font-display text-lg ${status.tone}`}>{status.label}</span>
            <span className="text-xs text-[var(--text-tertiary)]">· {status.note}</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="font-tabular text-xs text-[var(--text-tertiary)]">
            {bootstrapping ? "preparing demo baseline…" : lastRefreshedLabel}
          </span>
          <button
            onClick={onRunCycle}
            disabled={triggering || bootstrapping}
            className="rounded border hairline-strong px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--flow-teal-dim)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            {triggering ? "Running…" : "Run detection cycle now"}
          </button>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Transactions analyzed" value={stats.transactionsAnalyzed.toLocaleString("en-IN")} />
        <StatTile label="Payment segments monitored" value={String(stats.segmentsMonitored)} />
        <StatTile
          label="Active incidents"
          value={String(stats.activeIncidentCount)}
          emphasis={stats.activeIncidentCount > 0 ? "critical" : undefined}
        />
        {stats.flaggedVolumeRupees != null ? (
          <StatTile
            label="Payment volume flagged (this incident)"
            value={`₹${stats.flaggedVolumeRupees.toLocaleString("en-IN")}`}
            emphasis="critical"
          />
        ) : (
          <StatTile
            label="Payment volume monitored"
            value={`₹${stats.paymentVolumeMonitoredRupees.toLocaleString("en-IN")}`}
          />
        )}
      </div>

      <p className="mt-4 text-xs text-[var(--text-tertiary)]">
        Figures reflect this environment&apos;s disclosed demo/synthetic payment data
        (<code className="font-tabular">source=&quot;constructed&quot;</code>) unless real Razorpay
        traffic has been received — see Payment Health Overview below for the exact source per segment.
      </p>
    </div>
  );
}

function StatTile({ label, value, emphasis }: { label: string; value: string; emphasis?: "critical" }) {
  return (
    <div className="rounded-lg border hairline bg-[var(--ink-900)] px-4 py-3">
      <p
        className={`font-tabular text-2xl font-semibold ${
          emphasis === "critical" ? "text-[var(--flow-coral)]" : "text-[var(--text-primary)]"
        }`}
      >
        {value}
      </p>
      <p className="mt-1 text-xs text-[var(--text-tertiary)]">{label}</p>
    </div>
  );
}
