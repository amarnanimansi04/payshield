import { FlaggedSegment, IncidentCard } from "./IncidentCard";
import { ReasoningPanel } from "./ReasoningPanel";
import { AutonomousResponsePanel } from "./AutonomousResponsePanel";

export type { FlaggedSegment };

/** Orchestrates the incident-vs-healthy presentation. When nothing is
 * flagged, shows a reassurance card instead of a bare "nothing to see
 * here" line — the healthy state should communicate value too, not
 * just absence of alarm. When something IS flagged, composes the
 * three panels a judge should read in order: what happened (incident
 * card), why PayShield concluded that (reasoning panel), and what
 * PayShield did about it (autonomous response panel). */
export function AlertHero({
  flagged,
  allRecoveryPoliciesActive,
}: {
  flagged: FlaggedSegment | null;
  allRecoveryPoliciesActive: boolean;
}) {
  if (!flagged) {
    return (
      <div className="card px-6 py-10 text-center">
        <span className="text-2xl">🟢</span>
        <p className="mt-2 font-display text-xl text-[var(--text-primary)]">Payment system healthy</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-[var(--text-secondary)]">
          PayShield is continuously monitoring payment infrastructure across every segment against
          its own recent baseline.
        </p>
        <p className="mt-3 text-sm text-[var(--text-tertiary)]">
          No anomalies detected.{" "}
          {allRecoveryPoliciesActive
            ? "All recovery policies operating normally."
            : "Some segments still have a suppressed recovery policy from a recent incident — see Payment Health Overview above."}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <IncidentCard flagged={flagged} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ReasoningPanel flagged={flagged} />
        <AutonomousResponsePanel flagged={flagged} />
      </div>
    </div>
  );
}
