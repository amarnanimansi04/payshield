"use client";

/**
 * PAYSHIELD — simulation controls
 *
 * DEMO-ONLY panel. Every button here writes source="constructed" data
 * through /api/demo/scenario, or clears it through /api/demo/reset —
 * never touches real Razorpay ("live") events.
 *
 * Deliberately de-emphasized and placed at the bottom of the page —
 * the product's own dashboard is what a judge should see first; these
 * are the levers used to demonstrate it, not the product itself.
 */

const SCENARIOS: { id: string; label: string; description: string }[] = [
  { id: "normal", label: "Inject normal activity", description: "Ordinary, varied traffic at baseline rates — nothing should be flagged." },
  { id: "isolated-degradation", label: "Inject isolated degradation", description: "One segment (upi) degrades; others stay healthy." },
  { id: "systemic-degradation", label: "Inject systemic degradation", description: "Two segments degrade simultaneously — the hero demo moment." },
  { id: "recovery", label: "Inject recovery", description: "Backfills healthy windows so a degraded segment resolves." },
];

export function DemoControls({
  busy,
  message,
  onInject,
  onReset,
}: {
  busy: boolean;
  message: string | null;
  onInject: (scenario: string) => void;
  onReset: () => void;
}) {
  return (
    <div className="card px-6 py-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">
          Simulation controls — for demonstrating PayShield behaviour. Writes disclosed, constructed
          test data only, never real Razorpay traffic.
        </p>
        <button
          onClick={onReset}
          disabled={busy}
          className="shrink-0 rounded border hairline px-2.5 py-1 text-xs text-[var(--text-tertiary)] transition-colors hover:border-[var(--flow-coral-dim)] hover:text-[var(--flow-coral)] disabled:opacity-50"
        >
          Reset demo
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            onClick={() => onInject(s.id)}
            disabled={busy}
            title={s.description}
            className="rounded border hairline px-2.5 py-1 text-xs text-[var(--text-tertiary)] transition-colors hover:border-[var(--flow-teal-dim)] hover:text-[var(--text-secondary)] disabled:opacity-50"
          >
            {busy ? "Working…" : s.label}
          </button>
        ))}
      </div>
      {message && <p className="mt-2 text-xs text-[var(--text-tertiary)]">{message}</p>}
    </div>
  );
}
