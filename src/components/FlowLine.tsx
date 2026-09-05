"use client";

/**
 * PAYSHIELD — flow line
 *
 * The signature visual element, tied directly to the product name
 * ("PayShield" = flow). A segment's recent failure-rate history is
 * drawn as a smooth line — calm and level in normal operation,
 * visibly spiking when a segment degrades. State color is the only
 * thing that changes; the line itself always tells the truth about
 * the data, never decorated for effect.
 */

const STATE_COLORS: Record<string, string> = {
  NORMAL: "var(--flow-teal)",
  RESOLVED: "var(--flow-teal)",
  MONITORING: "var(--flow-amber)",
  ISOLATED_DEGRADATION: "var(--flow-coral)",
  SYSTEMIC_DEGRADATION: "var(--flow-coral)",
  ESCALATED: "var(--flow-coral)",
};

export function FlowLine({
  rates,
  state,
  width = 220,
  height = 40,
}: {
  rates: number[];
  state: string;
  width?: number;
  height?: number;
}) {
  const color = STATE_COLORS[state] ?? "var(--flow-teal)";
  const isAlert = state.includes("DEGRADATION") || state === "ESCALATED";

  if (rates.length < 2) {
    return (
      <svg width={width} height={height} className="opacity-40">
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="var(--text-tertiary)"
          strokeWidth={1.5}
          strokeDasharray="2 3"
        />
      </svg>
    );
  }

  const max = Math.max(...rates, 0.001);
  const min = Math.min(...rates, 0);
  const range = Math.max(max - min, 0.001);
  const pad = 4;

  const points = rates.map((r, i) => {
    const x = (i / (rates.length - 1)) * width;
    const y = height - pad - ((r - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const lastX = width;
  const lastY = parseFloat(points[points.length - 1].split(",")[1]);

  return (
    <svg width={width} height={height} className={isAlert ? "animate-pulse" : ""}>
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lastX} cy={lastY} r={2.5} fill={color} />
    </svg>
  );
}
