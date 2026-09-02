import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import * as yaml from "js-yaml";
import { api, ApiError, getOrg } from "../api/client";
import CalibrationChart from "../components/CalibrationChart";

// Required pages per .agents/Learning_Plane_PRD_Draft.md §0 — a literal
// checklist, not just backend mechanics: Audit Ledger, Reviewer Queue,
// Calibration + Metrics (combined), Policy Versions.
//
// Reviewer Queue is temporarily pulled from the tab list (still built,
// just not wired into the nav) — re-add "Reviewer Queue" to both arrays
// below to bring it back.
//
// "Calibration + Metrics" only applies to the auto-seeded Demo Org — the
// mock 100-case dataset is authored against one specific policy (see
// learning_plane/demo_seed.py) and would misrepresent a real agent's own
// numbers, so it's hidden entirely (not just empty) for every other org.
const BASE_TABS = ["Audit Ledger", "Policy Versions"];

export default function LearningPlane() {
  const { agentId } = useParams();
  const isDemo = !!getOrg()?.is_demo;
  const tabs = isDemo ? ["Audit Ledger", "Calibration + Metrics", "Policy Versions"] : BASE_TABS;
  const [agentName, setAgentName] = useState("");
  const [tab, setTab] = useState(tabs[0]);
  const [bundles, setBundles] = useState([]);
  const [loading, setLoading] = useState(true);

  function refreshBundles() {
    return api.listBundles(agentId).then(setBundles);
  }

  useEffect(() => {
    Promise.all([api.getAgent(agentId).then((d) => setAgentName(d.agent.name)), refreshBundles()]).finally(() =>
      setLoading(false)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  if (loading) return <p className="hint">Loading…</p>;

  return (
    <div>
      <h1>{agentName} — learning plane</h1>
      <p className="sub">
        Offline analysis over this agent's ledger
        {isDemo ? " and the mock calibration dataset (Demo Org only)." : "."}
      </p>

      <div className="lp-tabs">
        {tabs.map((t) => (
          <button key={t} className={`lp-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {tab === "Audit Ledger" && <AuditLedgerTab agentId={agentId} />}
      {tab === "Calibration + Metrics" && isDemo && (
        <CalibrationMetricsTab agentId={agentId} onPolicyCreated={refreshBundles} />
      )}
      {tab === "Policy Versions" && <PolicyVersionsTab agentId={agentId} bundles={bundles} onChanged={refreshBundles} />}
    </div>
  );
}

// ---- 1. Audit Ledger (PRD §0.1) ----

function AuditLedgerTab({ agentId }) {
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState(null);
  const [verify, setVerify] = useState(null);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    api.ledgerSummary(agentId).then(setSummary);
    api.listLedger({ agent_id: agentId, limit: 50 }).then(setRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  async function runVerify() {
    setVerifying(true);
    try {
      setVerify(await api.verifyLedger());
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div>
      {summary && (
        <div className="metric-cards">
          <div className="metric-card">
            <div className="value">{summary.total}</div>
            <div className="label">Total logs</div>
          </div>
          <div className="metric-card">
            <div className="value">
              {Object.entries(summary.by_action).map(([a, c]) => (
                <span key={a} className="mod" style={{ display: "block" }}>
                  {a}: {c}
                </span>
              ))}
            </div>
            <div className="label">By action</div>
          </div>
          <div className="metric-card">
            <div className="value">
              {Object.entries(summary.by_stage).map(([s, c]) => (
                <span key={s} className="mod" style={{ display: "block" }}>
                  {s}: {c}
                </span>
              ))}
            </div>
            <div className="label">By stage</div>
          </div>
          <div className="metric-card">
            <div className="value" style={{ fontSize: 14 }}>
              {summary.last_entry_at ? new Date(summary.last_entry_at).toLocaleString() : "—"}
            </div>
            <div className="label">Last entry</div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="row" style={{ marginTop: 0 }}>
          <button className="secondary" onClick={runVerify} disabled={verifying}>
            {verifying ? "Verifying…" : "Verify hash chain"}
          </button>
          {verify && (
            <span className={verify.ok ? "badge allow" : "badge block"}>
              {verify.ok ? `ok — ${verify.rows_checked} rows checked` : `broken at row ${verify.broken_at_id}`}
            </span>
          )}
        </div>

        {!rows ? (
          <p className="hint">Loading…</p>
        ) : rows.length === 0 ? (
          <div className="empty-state">No ledger rows yet — send a chat message to this agent first.</div>
        ) : (
          <table className="lp-table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Request</th>
                <th>Stage</th>
                <th>Action</th>
                <th>Modifiers / review</th>
                <th>Signals</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontFamily: "var(--font-mono)" }}>{r.request_id.slice(0, 8)}…</td>
                  <td>{r.stage}</td>
                  <td>{r.action}</td>
                  <td>{r.stage === "output" ? (r.modifiers || []).join(", ") || "—" : String(r.review_needed)}</td>
                  <td>
                    {r.contributing_signals.map((s, i) => (
                      <span key={i} className={`sig ${s.status}`}>
                        {s.type}:{s.score ?? "—"}
                      </span>
                    ))}
                  </td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---- 2. Reviewer Queue (PRD §0.2 / §2) ----

function ReviewerQueueTab({ agentId }) {
  const [queue, setQueue] = useState(null);
  const [busyId, setBusyId] = useState(null);

  function refresh() {
    return api.reviewerQueue(agentId).then(setQueue);
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  async function review(ledgerId, verdict) {
    setBusyId(ledgerId);
    try {
      await api.reviewLedgerRow(ledgerId, verdict);
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  if (!queue) return <p className="hint">Loading…</p>;

  const rows = [...queue.input_review_needed, ...queue.output_flagged];
  const pending = rows.filter((r) => !r.review_verdict).length;
  const approved = rows.filter((r) => r.review_verdict === "approved").length;
  const rejected = rows.filter((r) => r.review_verdict === "rejected").length;

  return (
    <div>
      <div className="metric-cards">
        <div className="metric-card">
          <div className="value">{rows.length}</div>
          <div className="label">Total flagged</div>
        </div>
        <div className="metric-card">
          <div className="value">{pending}</div>
          <div className="label">Pending</div>
        </div>
        <div className="metric-card">
          <div className="value">{approved}</div>
          <div className="label">Approved</div>
        </div>
        <div className="metric-card">
          <div className="value">{rejected}</div>
          <div className="label">Rejected</div>
        </div>
      </div>

      <div className="card">
        {rows.length === 0 ? (
          <div className="empty-state">Nothing needs review right now.</div>
        ) : (
          <table className="lp-table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Stage</th>
                <th>Action</th>
                <th>Modifiers / review</th>
                <th>Verdict</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontFamily: "var(--font-mono)" }}>{r.request_id.slice(0, 8)}…</td>
                  <td>{r.stage}</td>
                  <td>{r.action}</td>
                  <td>{r.stage === "output" ? (r.modifiers || []).join(", ") : "review_needed"}</td>
                  <td>
                    {r.review_verdict ? (
                      <span className={`badge ${r.review_verdict === "approved" ? "allow" : "block"}`}>{r.review_verdict}</span>
                    ) : (
                      <span className="hint">pending</span>
                    )}
                  </td>
                  <td>
                    {!r.review_verdict && (
                      <div className="row" style={{ marginTop: 0, gap: 6 }}>
                        <button className="secondary" disabled={busyId === r.id} onClick={() => review(r.id, "approved")}>
                          Approve
                        </button>
                        <button className="danger" disabled={busyId === r.id} onClick={() => review(r.id, "rejected")}>
                          Reject
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---- 3. Calibration + Metrics (PRD §0.3 / §3 / §4 / §6) ----
// Pure recomputation from the static 100-case mock dataset against this
// agent's production bundle — no button to "run" anything, no live
// execution, no waiting: it's just arithmetic, so it loads on mount.

function CalibrationMetricsTab({ agentId, onPolicyCreated }) {
  const [metrics, setMetrics] = useState(null);
  const [calibration, setCalibration] = useState(null);
  const [cases, setCases] = useState(null);
  const [error, setError] = useState(null);
  const [selectedThreshold, setSelectedThreshold] = useState(null);
  const [category, setCategory] = useState("all");
  const [mismatchesOnly, setMismatchesOnly] = useState(false);

  useEffect(() => {
    setError(null);
    Promise.all([api.getMockEvalMetrics(agentId), api.getMockEvalCalibration(agentId), api.getMockEval(agentId)])
      .then(([m, c, e]) => {
        setMetrics(m);
        setCalibration(c);
        setCases(e.cases);
      })
      .catch((e) => setError(e.detail || "Failed to compute metrics"));
  }, [agentId]);

  const categories = cases ? [...new Set(cases.map((c) => c.category_planted))].sort() : [];
  const visibleCases = (cases || [])
    .filter((c) => category === "all" || c.category_planted === category)
    .filter((c) => !mismatchesOnly || !caseMatches(c));

  return (
    <div className="card">
      <p className="sub" style={{ marginBottom: 12 }}>
        Computed directly from the 100-case hand-authored mock dataset (pre-authored responses and detector
        signals) via the real fusion logic — no live pipeline execution, no LLM calls, no detector calls.
      </p>
      {error && <div className="banner error">{error}</div>}

      {metrics && (
        <div className="metric-cards">
          <div className="metric-card">
            <div className="value">{formatPct(metrics.fp_rate.fp_rate)}</div>
            <div className="label">FP rate ({metrics.fp_rate.flagged_count} flagged)</div>
          </div>
          <div className="metric-card">
            <div className="value">{formatPct(metrics.fn_rate.fn_rate)}</div>
            <div className="label">
              Est. FN rate on hand-authored mock set ({metrics.fn_rate.eligible_count} true-allow cases)
            </div>
          </div>
        </div>
      )}
      <p className="hint">
        False-negative rate on a hand-authored mock test set — not measured via an independent judge, and not
        measured on live traffic.
      </p>

      {cases && (
        <div style={{ marginTop: 20 }}>
          <h2>Test cases ({visibleCases.length} of {cases.length})</h2>
          <div className="row" style={{ marginTop: 0, marginBottom: 12 }}>
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="all">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <label style={{ marginBottom: 0, display: "flex", alignItems: "center", gap: 6, fontWeight: 400 }}>
              <input type="checkbox" checked={mismatchesOnly} onChange={(e) => setMismatchesOnly(e.target.checked)} />
              Mismatches only
            </label>
          </div>
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="lp-table">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Category</th>
                  <th>Prompt</th>
                  <th>Ground truth</th>
                  <th>Expected</th>
                  <th>Actual</th>
                  <th>Match</th>
                </tr>
              </thead>
              <tbody>
                {visibleCases.map((c) => (
                  <tr key={c.case_id} className={caseMatches(c) ? "" : "differs"}>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{c.case_id}</td>
                    <td>{c.category_planted}</td>
                    <td title={c.prompt} style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {c.prompt}
                    </td>
                    <td>{c.ground_truth_risky ? "risky" : "clean"}</td>
                    <td>
                      {c.correct_base_action} <span className="mod">{c.correct_modifiers.join(", ")}</span>
                    </td>
                    <td>
                      {c.actual_action} <span className="mod">{c.actual_modifiers.join(", ")}</span>
                    </td>
                    <td>{caseMatches(c) ? "✓" : "✗"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {calibration && (
        <div style={{ marginTop: 20 }}>
          <h2>Calibration — per check</h2>
          <p className="hint">
            Each swept check compares its own mock signal at each threshold directly against that check's own
            ground truth (PRD §6) — not the whole-row decision. Checks with a fixed cutoff (no tunable threshold)
            get a single FN/FP number instead of a curve.
          </p>

          {CONTINUOUS_CHECKS.map((check) => (
            <CheckCalibrationSection
              key={check}
              check={check}
              sweep={calibration.continuous[check]}
              selectedThreshold={check === "output_pii" ? selectedThreshold : null}
              onSelect={check === "output_pii" ? setSelectedThreshold : undefined}
            />
          ))}

          {selectedThreshold != null && (
            <CreatePolicyFlow
              agentId={agentId}
              field={calibration.continuous.output_pii.field}
              value={selectedThreshold}
              onCreated={() => setSelectedThreshold(null)}
            />
          )}

          <div style={{ marginTop: 20 }}>
            <h3>Fixed-cutoff checks</h3>
            <div className="metric-cards">
              {BINARY_CHECKS.map((check) => (
                <BinaryCheckRow key={check} check={check} rates={calibration.binary[check]} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function formatPct(rate) {
  return rate === null || rate === undefined ? "n/a (untested)" : `${Math.round(rate * 100)}%`;
}

// Fixed display order for the 3 continuous checks — output_pii first since
// it's the only one wired into the Create New Policy flow (PRD §6.1).
const CONTINUOUS_CHECKS = ["output_pii", "input_pii", "output_toxicity"];
const BINARY_CHECKS = ["input_secrets", "output_secrets", "input_injection", "output_canary"];

function CheckCalibrationSection({ check, sweep, selectedThreshold, onSelect }) {
  return (
    <div style={{ marginTop: 20 }}>
      <h3>
        {check} — {sweep.field} ({sweep.points[0].threshold}–{sweep.points[sweep.points.length - 1].threshold})
        {check === "output_pii" && <span className="hint"> — locked tighten-only at 0.8</span>}
      </h3>
      <div className="row" style={{ marginTop: 0, gap: 24, flexWrap: "wrap" }}>
        <CalibrationChart
          points={sweep.points}
          metricKey="fn_rate"
          color="#2a6ebb"
          label="FN rate"
          selectedThreshold={selectedThreshold}
          onSelect={onSelect}
        />
        <CalibrationChart
          points={sweep.points}
          metricKey="fp_rate"
          color="#d9534f"
          label="FP rate"
          selectedThreshold={selectedThreshold}
          onSelect={onSelect}
        />
      </div>
    </div>
  );
}

function BinaryCheckRow({ check, rates }) {
  return (
    <div className="metric-card">
      <div className="value" style={{ fontSize: 14 }}>
        FN {formatPct(rates.fn_rate)} / FP {formatPct(rates.fp_rate)}
      </div>
      <div className="label">{check} (fixed cutoff — no tunable threshold)</div>
    </div>
  );
}

function caseMatches(c) {
  return (
    c.actual_action === c.correct_base_action &&
    c.actual_modifiers.length === c.correct_modifiers.length &&
    c.actual_modifiers.every((m) => c.correct_modifiers.includes(m))
  );
}

// ---- Calibration -> new policy version confirm screen (PRD §6.1) ----

function CreatePolicyFlow({ agentId, field, value, onCreated }) {
  const [open, setOpen] = useState(false);
  const [orgContent, setOrgContent] = useState(null);
  const [agentText, setAgentText] = useState("");
  const [preview, setPreview] = useState(null);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState(null);

  async function openConfirm() {
    const [org, agentPolicy, previewResult] = await Promise.all([
      api.getOrgPolicy(),
      api.getAgentPolicy(agentId),
      api.calibrationPreview(agentId, field, value),
    ]);
    setOrgContent(org.content);
    const candidate = { ...agentPolicy.content, [field]: value };
    setAgentText(yaml.dump(candidate));
    setPreview(previewResult);
    setOpen(true);
  }

  async function confirm() {
    setSaving(true);
    setBanner(null);
    try {
      const content = yaml.load(agentText);
      const label = `calibrated ${field} = ${value}`;
      await api.postAgentPolicy(agentId, content, { promote: false, label });
      setBanner({ type: "ok", text: `New version compiled as "${label}" — not yet production. Promote it from Policy Versions when ready.` });
      onCreated();
    } catch (e) {
      setBanner({ type: "error", text: e instanceof ApiError ? e.detail : e.message });
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <div className="row">
        <button className="primary" onClick={openConfirm}>
          Create New Policy from threshold {value}
        </button>
      </div>
    );
  }

  return (
    <div className="card" style={{ marginTop: 12 }}>
      <h2>Confirm new policy version</h2>
      {preview?.would_be_clamped && (
        <div className="banner warn">
          {field} = {value} would be clamped to {preview.effective_value} by org-baseline (locked by{" "}
          {preview.clamp_event.locked_by}).
        </div>
      )}
      <label>Org-baseline policy (read-only)</label>
      <pre>{yaml.dump(orgContent)}</pre>
      <label style={{ marginTop: 12 }}>Agent policy — editable, pending change pre-filled</label>
      <textarea value={agentText} onChange={(e) => setAgentText(e.target.value)} spellCheck={false} />
      <div className="row">
        <button className="primary" disabled={saving} onClick={confirm}>
          {saving ? "Compiling…" : "Confirm"}
        </button>
        <button className="secondary" onClick={() => setOpen(false)} disabled={saving}>
          Cancel
        </button>
      </div>
      {banner && <div className={`banner ${banner.type}`}>{banner.text}</div>}
    </div>
  );
}

// ---- 4. Policy Versions (PRD §0.4 / §1.1) — list, production picker, ----
// ---- shadow-deploy trigger + diff table, one page                    ----

function PolicyVersionsTab({ agentId, bundles, onChanged }) {
  const [promoting, setPromoting] = useState(null);
  const [versionA, setVersionA] = useState(bundles[0]?.version);
  const [versionB, setVersionB] = useState(bundles[1]?.version ?? bundles[0]?.version);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  async function promote(bundleId) {
    setPromoting(bundleId);
    try {
      await api.promoteBundle(agentId, bundleId);
      await onChanged();
    } finally {
      setPromoting(null);
    }
  }

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setResult(await api.shadowDeploy(agentId, Number(versionA), Number(versionB)));
    } catch (e) {
      setError(e.detail || "Shadow deploy failed");
    } finally {
      setRunning(false);
    }
  }

  if (bundles.length === 0) return <div className="empty-state">No compiled bundles yet — set a policy first.</div>;

  return (
    <div>
      <div className="card">
        <label>Versions</label>
        <table className="lp-table">
          <thead>
            <tr>
              <th>Version</th>
              <th>Label</th>
              <th>Hash</th>
              <th>Compiled</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {bundles.map((b) => (
              <tr key={b.id}>
                <td>v{b.version}</td>
                <td>{b.label || <span className="hint">—</span>}</td>
                <td style={{ fontFamily: "var(--font-mono)" }}>{b.hash.slice(0, 18)}…</td>
                <td>{new Date(b.compiled_at).toLocaleString()}</td>
                <td>{b.is_production ? <span className="badge allow">production</span> : "—"}</td>
                <td>
                  {!b.is_production && (
                    <button className="secondary" disabled={promoting === b.id} onClick={() => promote(b.id)}>
                      {promoting === b.id ? "Promoting…" : "Promote"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <label>Shadow deploy</label>
        <p className="sub" style={{ marginBottom: 12 }}>
          Replays every stored output-stage decision under both versions' thresholds — no live traffic, no
          detectors re-run. Neither side is guaranteed to be production.
        </p>
        {bundles.length < 2 ? (
          <div className="empty-state">Need at least two compiled versions to compare — compile another one first.</div>
        ) : (
          <>
            <div className="row" style={{ marginTop: 0 }}>
              <label style={{ marginBottom: 0 }}>Version A</label>
              <select value={versionA} onChange={(e) => setVersionA(e.target.value)}>
                {bundles.map((b) => (
                  <option key={b.id} value={b.version}>
                    v{b.version} {b.label ? `— ${b.label}` : ""} {b.is_production ? "(production)" : ""}
                  </option>
                ))}
              </select>
              <label style={{ marginBottom: 0 }}>Version B</label>
              <select value={versionB} onChange={(e) => setVersionB(e.target.value)}>
                {bundles.map((b) => (
                  <option key={b.id} value={b.version}>
                    v{b.version} {b.label ? `— ${b.label}` : ""} {b.is_production ? "(production)" : ""}
                  </option>
                ))}
              </select>
              <button className="primary" disabled={running} onClick={run}>
                {running ? "Running…" : "Run comparison"}
              </button>
            </div>
            {error && <div className="banner error">{error}</div>}

            {result && (
              <div style={{ marginTop: 16 }}>
                <p>
                  <strong>
                    {result.changed_count} out of {result.total_rows}
                  </strong>{" "}
                  stored decisions would change between v{result.version_a} and v{result.version_b}.
                </p>
                <table className="lp-table">
                  <thead>
                    <tr>
                      <th>Request</th>
                      <th>Version A</th>
                      <th>Version B</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((r) => (
                      <tr key={r.request_id} className={r.differs ? "differs" : ""}>
                        <td style={{ fontFamily: "var(--font-mono)" }}>{r.request_id.slice(0, 8)}…</td>
                        <td>
                          {r.version_a.action} <span className="mod">{r.version_a.modifiers.join(", ")}</span>
                        </td>
                        <td>
                          {r.version_b.action} <span className="mod">{r.version_b.modifiers.join(", ")}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
