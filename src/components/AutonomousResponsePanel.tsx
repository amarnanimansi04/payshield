import { FlaggedSegment } from "./IncidentCard";

const ACTION_LABEL: Record<string, string> = {
  SUPPRESS_OWN_RECOVERY_ACTIONS: "Paused PayShield's own recovery workflow for the affected segment(s)",
  GENERATE_MERCHANT_ALERT: "Generated a merchant alert",
  FLAG_TRANSACTIONS: "Flagged affected transactions for review",
  ESCALATE: "Escalated for manual review",
  RESTORE_NORMAL_POLICY: "Restored normal recovery policy",
  RECOMMEND_ALTERNATIVE_METHOD_TEXT_ONLY: "Suggested an alternative payment method (recommendation only, not executed)",
};

/** This is the "agent" moment — recovery_policy is a real
 * database-backed table (see supabase/schema.sql), not a UI-only
 * state. Every line here reflects an actual row PayShield wrote. */
export function AutonomousResponsePanel({ flagged }: { flagged: FlaggedSegment }) {
  const suppressed = flagged.recoveryPolicyStatus === "SUPPRESSED";

  return (
    <div className="card px-6 py-5">
      <h3 className="font-display text-sm font-semibold text-[var(--text-primary)]">Autonomous response</h3>

      <div className="mt-4 flex items-center gap-3">
        <span className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Recovery policy</span>
        <span
          className={`rounded px-2 py-0.5 text-xs font-semibold ${
            suppressed
              ? "bg-[var(--flow-coral-dim)]/20 text-[var(--flow-coral)]"
              : "bg-[var(--flow-teal-dim)]/20 text-[var(--flow-teal)]"
          }`}
        >
          {flagged.recoveryPolicyStatus}
        </span>
      </div>

      <div className="mt-3">
        <p className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Why?</p>
        <p className="mt-1 text-sm leading-relaxed text-[var(--text-secondary)]">
          {suppressed
            ? "During infrastructure degradation, retrying failed payments one at a time doesn't fix a systemic cause — it just adds load without improving outcomes. PayShield pauses its own retry-recommendation workflow for the affected segment(s) until the underlying issue clears, and alerts the merchant instead."
            : "No active degradation for this segment right now, so PayShield's own recovery workflow runs normally."}
        </p>
      </div>

      <div className="mt-3">
        <p className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Action taken</p>
        <ul className="mt-1 space-y-1">
          {flagged.actionsTaken.map((a) => (
            <li key={a} className="flex items-start gap-2 text-sm text-[var(--text-primary)]">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-[var(--flow-coral)]" />
              {ACTION_LABEL[a] ?? a}
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-3 border-t hairline pt-3 text-xs text-[var(--text-tertiary)]">
        Audit: decision recorded in the audit trail below, with the exact evidence that triggered it.
      </p>
      <p className="mt-2 text-xs text-[var(--text-tertiary)]">
        PayShield controls its <span className="text-[var(--text-secondary)]">own</span> downstream
        recovery workflow only — it does not pause or modify Razorpay&apos;s native retry engine.
      </p>

      {flagged.alertText && (
        <div className="mt-4 border-t hairline pt-3">
          <div className="mb-1.5 flex items-center gap-2">
            <p className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Merchant alert</p>
            <span className="rounded bg-[var(--ink-800)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--text-tertiary)]">
              {flagged.alertSource === "llm" ? "LLM-authored" : "template fallback"}
            </span>
          </div>
          <p className="text-sm leading-relaxed text-[var(--text-primary)]">{flagged.alertText}</p>
        </div>
      )}
    </div>
  );
}
