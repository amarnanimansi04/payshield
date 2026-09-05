"use client";

import { useState } from "react";

export interface HistoryRow {
  segment: string;
  state: string;
  previousState: string | null;
  case_classification: string;
  reasoning: string;
  actions: string[];
  created_at: string;
}

export interface EvaluationSummary {
  precision: number | null;
  recall: number | null;
  falsePositiveRate: number | null;
  meanTimeToDetectionSeconds: number | null;
  baselineComparison: {
    label: string;
    correct: boolean;
  }[];
}

const HEADLINE: Record<string, string> = {
  NORMAL: "Operating normally",
  MONITORING: "Anomaly under observation",
  ISOLATED_DEGRADATION: "Isolated degradation detected",
  SYSTEMIC_DEGRADATION: "Systemic degradation detected",
  ESCALATED: "Incident escalated",
  RESOLVED: "Incident resolved",
};

const SEVERITY_DOT: Record<string, string> = {
  NORMAL: "bg-[var(--text-tertiary)]",
  RESOLVED: "bg-[var(--flow-teal)]",
  MONITORING: "bg-[var(--flow-amber)]",
  ISOLATED_DEGRADATION: "bg-[var(--flow-coral)]",
  SYSTEMIC_DEGRADATION: "bg-[var(--flow-coral)]",
  ESCALATED: "bg-[var(--flow-coral)]",
};

const DEGRADED_OR_ESCALATED = ["ISOLATED_DEGRADATION", "SYSTEMIC_DEGRADATION", "ESCALATED"];

/** A row where the state didn't actually change this cycle (still the
 * same segment re-evaluated, nothing new happened) has an empty
 * `actions` list by design (see backend/app/services/decision_engine.py's
 * `actions_this_cycle=actions if state_changed else []`) — that's
 * correct for a NORMAL segment ("nothing to do"), but misleading for a
 * still-degraded one: without this check it read as "No action —
 * within baseline" right next to "Systemic degradation detected",
 * contradicting the Autonomous Response panel's own SUPPRESSED status
 * for the same segment. Caught from a real screenshot, not inspection. */
function isOngoing(row: HistoryRow): boolean {
  return row.previousState === row.state;
}

function decisionFor(row: HistoryRow): string {
  if (row.actions.includes("RESTORE_NORMAL_POLICY")) return "Recovery policy restored";
  if (row.actions.includes("SUPPRESS_OWN_RECOVERY_ACTIONS")) return "Recovery policy suppressed";
  if (row.actions.includes("ESCALATE")) return "Escalated for manual review";
  if (row.state === "MONITORING") return "Watching — not yet actionable";
  if (isOngoing(row) && DEGRADED_OR_ESCALATED.includes(row.state)) {
    return "No new action — recovery policy remains suppressed";
  }
  return "No action — within baseline";
}

function statusFor(row: HistoryRow): string {
  switch (row.state) {
    case "SYSTEMIC_DEGRADATION":
    case "ISOLATED_DEGRADATION":
      return "Monitoring for recovery.";
    case "ESCALATED":
      return "Awaiting manual attention.";
    case "RESOLVED":
      return "Normal operations resumed.";
    case "MONITORING":
      return "Continuing to monitor.";
    default:
      return "Operating normally.";
  }
}

function headlineFor(row: HistoryRow): string {
  if (isOngoing(row) && DEGRADED_OR_ESCALATED.includes(row.state)) {
    return `Still degraded — ${(HEADLINE[row.state] ?? row.state).toLowerCase()}`;
  }
  return HEADLINE[row.state] ?? row.state;
}

export function AuditAndEvaluation({
  history,
  evaluation,
}: {
  history: HistoryRow[];
  evaluation: EvaluationSummary | null;
}) {
  const [tab, setTab] = useState<"audit" | "evaluation">("audit");
  const [showTechnical, setShowTechnical] = useState(false);

  return (
    <div className="card px-0 py-0">
      <div className="flex items-center justify-between gap-2 border-b hairline px-6 pt-4">
        <div className="flex gap-1">
          <TabButton active={tab === "audit"} onClick={() => setTab("audit")}>
            Incident history
          </TabButton>
          <TabButton active={tab === "evaluation"} onClick={() => setTab("evaluation")}>
            Evaluation
          </TabButton>
        </div>
        {tab === "audit" && history.length > 0 && (
          <label className="mb-2 flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
            <input
              type="checkbox"
              checked={showTechnical}
              onChange={(e) => setShowTechnical(e.target.checked)}
              className="accent-[var(--flow-teal)]"
            />
            Show technical detail
          </label>
        )}
      </div>

      {tab === "audit" ? (
        <div className="max-h-96 overflow-y-auto px-6 py-4">
          {history.length === 0 ? (
            <p className="text-sm text-[var(--text-tertiary)]">No state transitions recorded yet.</p>
          ) : (
            <ul className="space-y-4">
              {history.map((h, i) => (
                <li key={i} className="border-b hairline pb-4 last:border-b-0 last:pb-0">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[h.state] ?? "bg-[var(--text-tertiary)]"}`} />
                    <span className="font-tabular text-xs text-[var(--text-tertiary)]">
                      {new Date(h.created_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </span>
                    <span className="text-sm font-semibold text-[var(--text-primary)]">
                      {headlineFor(h)}
                    </span>
                    <span className="font-tabular text-xs text-[var(--text-tertiary)]">— {h.segment}</span>
                  </div>

                  <dl className="mt-2 space-y-1 pl-4 text-sm">
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-[var(--text-tertiary)]">Reason:</dt>
                      <dd className="text-[var(--text-secondary)]">{h.reasoning}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-[var(--text-tertiary)]">Decision:</dt>
                      <dd className="text-[var(--text-primary)]">{decisionFor(h)}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-[var(--text-tertiary)]">Status:</dt>
                      <dd className="text-[var(--text-secondary)]">{statusFor(h)}</dd>
                    </div>
                    {showTechnical && (
                      <div className="flex gap-2 pt-1">
                        <dt className="shrink-0 text-[var(--text-tertiary)]">Technical:</dt>
                        <dd className="font-tabular text-xs text-[var(--text-tertiary)]">
                          case {h.case_classification} · {h.previousState ?? "—"} → {h.state} · actions:{" "}
                          {h.actions.length > 0 ? h.actions.join(", ") : "none"}
                        </dd>
                      </div>
                    )}
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="px-6 py-4">
          {!evaluation ? (
            <p className="text-sm text-[var(--text-tertiary)]">
              Run{" "}
              <code className="font-tabular rounded bg-[var(--ink-800)] px-1.5 py-0.5">
                python -m app.evaluation.benchmarks
              </code>{" "}
              to populate this panel with precision/recall against a held-out, injected-anomaly batch.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
              <Metric label="Precision" value={fmtPct(evaluation.precision)} />
              <Metric label="Recall" value={fmtPct(evaluation.recall)} />
              <Metric label="False positive rate" value={fmtPct(evaluation.falsePositiveRate)} />
              <Metric
                label="Mean time to detection"
                value={evaluation.meanTimeToDetectionSeconds != null ? `${Math.round(evaluation.meanTimeToDetectionSeconds)}s` : "—"}
              />
              <div className="col-span-2 md:col-span-4">
                <h4 className="mb-2 text-sm font-medium text-[var(--text-secondary)]">
                  PayShield vs. naive threshold approaches
                </h4>
                <ul className="space-y-1">
                  {evaluation.baselineComparison.map((b) => (
                    <li key={b.label} className="flex items-center gap-2 text-sm">
                      <span className={`h-1.5 w-1.5 rounded-full ${b.correct ? "bg-[var(--flow-teal)]" : "bg-[var(--flow-coral)]"}`} />
                      <span className="text-[var(--text-primary)]">{b.label}</span>
                      <span className="text-[var(--text-tertiary)]">{b.correct ? "correct" : "wrong"}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`border-b-2 px-3 pb-2 text-sm font-medium transition-colors ${
        active ? "border-[var(--flow-teal)] text-[var(--text-primary)]" : "border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
      }`}
    >
      {children}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-tabular text-2xl text-[var(--text-primary)]">{value}</p>
      <p className="text-xs text-[var(--text-tertiary)]">{label}</p>
    </div>
  );
}

function fmtPct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(0)}%`;
}
