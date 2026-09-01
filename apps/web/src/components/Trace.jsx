function SigChip({ s }) {
  const cls = s.status === "disabled" ? "disabled" : s.status === "timeout" ? "timeout" : "";
  const score = s.score === null || s.score === undefined ? "null" : Number(s.score).toFixed(3);
  return (
    <span className={`sig ${cls}`}>
      {s.type}: {score} ({s.status})
    </span>
  );
}

export function TraceStage({ stage }) {
  return (
    <div className="trace-stage">
      <h4>{stage.name}</h4>
      {stage.signals?.map((s, i) => (
        <SigChip key={i} s={s} />
      ))}
      {stage.action && (
        <div>
          <span className={`badge ${stage.action}`}>{stage.action}</span>
          {stage.modifiers?.length > 0 && <span className="mod">modifiers: [{stage.modifiers.join(", ")}]</span>}
        </div>
      )}
    </div>
  );
}

export default function Trace({ stages }) {
  return (
    <div className="card trace">
      {stages.map((stage, i) => (
        <TraceStage key={i} stage={stage} />
      ))}
    </div>
  );
}
