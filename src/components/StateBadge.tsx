const STATE_LABEL: Record<string, string> = {
  NORMAL: "Normal",
  RESOLVED: "Resolved",
  MONITORING: "Monitoring",
  ISOLATED_DEGRADATION: "Isolated degradation",
  SYSTEMIC_DEGRADATION: "Systemic degradation",
  ESCALATED: "Escalated",
};

const STATE_STYLE: Record<string, string> = {
  NORMAL: "text-[var(--flow-teal)] border-[var(--flow-teal-dim)]",
  RESOLVED: "text-[var(--flow-teal)] border-[var(--flow-teal-dim)]",
  MONITORING: "text-[var(--flow-amber)] border-[var(--flow-amber-dim)]",
  ISOLATED_DEGRADATION: "text-[var(--flow-coral)] border-[var(--flow-coral-dim)]",
  SYSTEMIC_DEGRADATION: "text-[var(--flow-coral)] border-[var(--flow-coral-dim)]",
  ESCALATED: "text-[var(--flow-coral)] border-[var(--flow-coral-dim)]",
};

export function StateBadge({ state }: { state: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
        STATE_STYLE[state] ?? "text-[var(--text-secondary)] border-[var(--line-700)]"
      }`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {STATE_LABEL[state] ?? state}
    </span>
  );
}
