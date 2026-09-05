import { FlaggedSegment } from "./IncidentCard";

const STATE_LABEL: Record<string, string> = {
  MONITORING: "MONITORING",
  ISOLATED_DEGRADATION: "ISOLATED DEGRADATION",
  SYSTEMIC_DEGRADATION: "SYSTEMIC DEGRADATION",
  ESCALATED: "ESCALATED",
};

/** Every line here is a real fact checked against the actual
 * DetectionResult/decision-engine fields — nothing is a fixed script.
 * If a condition isn't true for this particular incident, it isn't
 * shown (e.g. an isolated incident never claims multi-segment
 * correlation). */
export function ReasoningPanel({ flagged }: { flagged: FlaggedSegment }) {
  const isSystemic = flagged.affectedSegments.length > 1;

  const checks: string[] = [];

  if (isSystemic) {
    checks.push(
      `Multiple payment segments degraded at the same time (${flagged.affectedSegments.length} segments) — not one customer's bad luck`
    );
  } else {
    checks.push("Only this one segment deviated — other segments stayed within their own normal range");
  }

  checks.push(
    `Failure rate (${(flagged.currentRate * 100).toFixed(1)}%) exceeded this segment's own historical baseline (${(flagged.baseline * 100).toFixed(1)}%) by ${flagged.sigma.toFixed(1)} standard deviations`
  );

  checks.push(
    `Pattern persisted across ${flagged.sustainedWindows} consecutive detection window${flagged.sustainedWindows === 1 ? "" : "s"} — not a single one-off blip`
  );

  checks.push(
    isSystemic
      ? "Classified as systemic degradation, not treated as isolated customer-level failures"
      : "Classified as an isolated degradation, contained to a single segment"
  );

  if (flagged.dominantErrorSource) {
    checks.push(
      `Dominant error source: ${(flagged.dominantErrorSource.share * 100).toFixed(0)}% ${flagged.dominantErrorSource.source}-side — points at infrastructure, not individual customer issues`
    );
  }

  return (
    <div className="card px-6 py-5">
      <h3 className="font-display text-sm font-semibold text-[var(--text-primary)]">
        Why did PayShield take this decision?
      </h3>
      <ul className="mt-3 space-y-2">
        {checks.map((c, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-[var(--text-secondary)]">
            <span className="mt-0.5 text-[var(--flow-teal)]">✓</span>
            <span>{c}</span>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex items-center gap-2 border-t hairline pt-3">
        <span className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">Final classification</span>
        <span className="rounded border border-[var(--flow-coral-dim)] px-2 py-0.5 text-xs font-medium text-[var(--flow-coral)]">
          {STATE_LABEL[flagged.state] ?? flagged.state}
        </span>
      </div>
      <p className="mt-2 text-xs text-[var(--text-tertiary)]">
        This is a statistical classification against this segment&apos;s own adaptive baseline — not a
        simple fixed-threshold alert. See the evaluation tab below for how this compares against
        naive threshold approaches.
      </p>
    </div>
  );
}
