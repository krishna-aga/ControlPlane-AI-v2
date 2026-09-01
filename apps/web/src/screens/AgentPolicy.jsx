import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import * as yaml from "js-yaml";
import { api, ApiError } from "../api/client";

const PLACEHOLDER = `# Fields left out here simply inherit the org policy unchanged.
# Only list what this agent overrides.
output_t1_checks_enabled:
  toxicity: true

toxicity_mild_action: regenerate`;

export default function AgentPolicy() {
  const { agentId } = useParams();
  const [agentName, setAgentName] = useState("");
  const [text, setText] = useState(PLACEHOLDER);
  const [bundle, setBundle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState(null);
  const [clampEvents, setClampEvents] = useState([]);

  useEffect(() => {
    api.getAgent(agentId).then((detail) => {
      setAgentName(detail.agent.name);
      if (detail.layer) setText(yaml.dump(detail.layer.content));
      if (detail.bundle) setBundle(detail.bundle);
      setLoading(false);
    });
  }, [agentId]);

  async function handleCompile() {
    setBanner(null);
    setClampEvents([]);
    let content;
    try {
      content = yaml.load(text);
    } catch (e) {
      setBanner({ type: "error", text: "YAML parse error: " + e.message });
      return;
    }

    setSaving(true);
    try {
      const result = await api.postAgentPolicy(agentId, content);
      setBundle(result.bundle);
      setClampEvents(result.clamp_events || []);
      setBanner({ type: "ok", text: `Compiled bundle v${result.bundle.version}.` });
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 422
          ? err.detail
          : err.detail || "Failed to compile";
      setBanner({ type: "error", text: msg });
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="hint">Loading…</p>;

  return (
    <div>
      <h1>{agentName} — policy</h1>
      <p className="sub">Overrides on top of the org policy. Locked fields can only be tightened.</p>

      <div className="card">
        <label htmlFor="agent-yaml">agent policy (YAML)</label>
        <textarea id="agent-yaml" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        <div className="row">
          <button className="primary" onClick={handleCompile} disabled={saving}>
            {saving ? "Compiling…" : "Compile bundle"}
          </button>
        </div>
        {banner && <div className={`banner ${banner.type}`}>{banner.text}</div>}
        {clampEvents.map((e, i) => (
          <div key={i} className="banner warn">
            {e.field} — requested {JSON.stringify(e.requested)}, enforced {JSON.stringify(e.enforced)} (locked by{" "}
            {e.locked_by})
          </div>
        ))}
      </div>

      {bundle && (
        <div className="card">
          <label>Compiled bundle</label>
          <pre>{JSON.stringify(bundle, null, 2)}</pre>
          <div className="row">
            <Link to={`/agents/${agentId}/chat`}>
              <button className="primary">Open chat demo</button>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
