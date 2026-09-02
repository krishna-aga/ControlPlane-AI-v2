// Small inline-SVG single-series line chart for a per-check calibration
// sweep (PRD §6) — one metric (FN rate or FP rate) plotted against a
// candidate threshold. No charting library: the project has none installed,
// and one line over a dozen points doesn't need one. Parameterized by
// metricKey/color/label so the same component renders both the FN and FP
// chart for any swept check, each with its own threshold range.

const WIDTH = 360;
const HEIGHT = 200;
const PAD = { top: 16, right: 16, bottom: 32, left: 40 };

function scaleX(threshold, min, max) {
  const innerWidth = WIDTH - PAD.left - PAD.right;
  return PAD.left + ((threshold - min) / (max - min || 1)) * innerWidth;
}

function scaleY(rate) {
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;
  return PAD.top + innerHeight * (1 - (rate ?? 0));
}

function toPath(points, key, min, max) {
  return points
    .filter((p) => p[key] !== null && p[key] !== undefined)
    .map((p, i) => `${i === 0 ? "M" : "L"} ${scaleX(p.threshold, min, max)} ${scaleY(p[key])}`)
    .join(" ");
}

export default function CalibrationChart({ points, metricKey, color, label, selectedThreshold, onSelect }) {
  if (!points || points.length === 0) return null;

  const min = points[0].threshold;
  const max = points[points.length - 1].threshold;
  const path = toPath(points, metricKey, min, max);

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" style={{ maxWidth: WIDTH }}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={scaleY(t)}
              y2={scaleY(t)}
              stroke="var(--border, #e2e2e2)"
              strokeWidth={1}
            />
            <text x={PAD.left - 8} y={scaleY(t) + 4} fontSize="10" textAnchor="end" fill="currentColor">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}

        {points.map((p) => (
          <text
            key={p.threshold}
            x={scaleX(p.threshold, min, max)}
            y={HEIGHT - PAD.bottom + 16}
            fontSize="10"
            textAnchor="middle"
            fill="currentColor"
          >
            {p.threshold}
          </text>
        ))}

        <path d={path} fill="none" stroke={color} strokeWidth={2} />

        {points.map((p) => (
          <g key={p.threshold}>
            {p[metricKey] !== null && p[metricKey] !== undefined && (
              <circle
                cx={scaleX(p.threshold, min, max)}
                cy={scaleY(p[metricKey])}
                r={selectedThreshold === p.threshold ? 5 : 3}
                fill={color}
                style={{ cursor: onSelect ? "pointer" : "default" }}
                onClick={() => onSelect?.(p.threshold)}
              />
            )}
            {selectedThreshold === p.threshold && (
              <line
                x1={scaleX(p.threshold, min, max)}
                x2={scaleX(p.threshold, min, max)}
                y1={PAD.top}
                y2={HEIGHT - PAD.bottom}
                stroke="currentColor"
                strokeDasharray="3,3"
                strokeWidth={1}
              />
            )}
          </g>
        ))}
      </svg>
      <div className="row" style={{ gap: 8, fontSize: 12, marginTop: 4 }}>
        <span>
          <span style={{ color }}>■</span> {label}
        </span>
      </div>
    </div>
  );
}
