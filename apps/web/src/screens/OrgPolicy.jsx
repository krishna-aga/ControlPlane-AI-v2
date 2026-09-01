import { useEffect, useState } from "react";
import * as yaml from "js-yaml";
import { api, ApiError } from "../api/client";

const PLACEHOLDER = `input_checks_enabled:
  secrets: true
  injection: true
  pii: true

output_t0_checks_enabled:
  secrets: true
  canary: true

output_t1_checks_enabled:
  pii: true
  toxicity: false

input_pii_review_threshold: 0.4
input_pii_redact_threshold: 0.8
output_pii_review_threshold: 0.4
output_pii_redact_threshold: 0.8
toxicity_regenerate_threshold: 0.5
toxicity_block_threshold: 0.85
toxicity_mild_action: flag_visible
fail_mode: open

locks:
  - input_checks_enabled.secrets
  - output_t0_checks_enabled.canary
  - output_pii_redact_threshold`;

export default function OrgPolicy() {
  const [text, setText] = useState(PLACEHOLDER);
  const [hasPolicy, setHasPolicy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState(null);
  const [recompiled, setRecompiled] = useState([]);

  useEffect(() => {
    api
      .getOrgPolicy()
      .then((layer) => {
        setText(yaml.dump(layer.content));
        setHasPolicy(true);
      })
      .catch((err) => {
        if (!(err instanceof ApiError && err.status === 404)) {
          setBanner({ type: "error", text: "Failed to load org policy: " + err.message });
        }
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    setBanner(null);
    setRecompiled([]);
    let content;
    try {
      content = yaml.load(text);
    } catch (e) {
      setBanner({ type: "error", text: "YAML parse error: " + e.message });
      return;
    }

    setSaving(true);
    try {
      const result = await api.postOrgPolicy(content);
      setHasPolicy(true);
      setRecompiled(result.recompiled_agents || []);
      setBanner({
        type: "ok",
        text:
          result.recompiled_agents?.length > 0
            ? `Org policy saved (v${result.layer.version}). Recompiled ${result.recompiled_agents.length} agent(s).`
            : `Org policy saved (v${result.layer.version}). No agents yet to recompile.`,
      });
    } catch (err) {
      setBanner({ type: "error", text: err.detail || "Failed to save org policy" });
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="hint">Loading…</p>;

  return (
    <div>
      <h1>Organization policy</h1>
      <p className="sub">
        The org-baseline layer. Locked fields here constrain every agent your organization creates —
        agents can tighten a lock, never loosen it.
      </p>
      <div className="card">
        <label htmlFor="org-yaml">org policy (YAML)</label>
        <textarea id="org-yaml" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        <div className="row">
          <button className="primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : hasPolicy ? "Save changes" : "Save org policy"}
          </button>
        </div>
        {banner && <div className={`banner ${banner.type}`}>{banner.text}</div>}
        {recompiled.map((r) => (
          <div key={r.agent_id} className="hint">
            {r.agent_name}: {r.clamp_events?.length > 0 ? `${r.clamp_events.length} clamp event(s)` : "no clamp events"}
          </div>
        ))}
      </div>
    </div>
  );
}
