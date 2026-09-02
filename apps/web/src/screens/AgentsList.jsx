import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

export default function AgentsList() {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  function refresh() {
    return api.listAgents().then(setAgents);
  }

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setError(null);
    setCreating(true);
    try {
      await api.createAgent(newName.trim());
      setNewName("");
      await refresh();
    } catch (err) {
      setError(err.detail || "Failed to create agent");
    } finally {
      setCreating(false);
    }
  }

  if (loading) return <p className="hint">Loading…</p>;

  return (
    <div>
      <h1>Agents</h1>
      <p className="sub">
        Each agent is one AI use case (a support bot, an internal copilot, …). Its policy is resolved
        against your org policy — set the org policy first.
      </p>

      <div className="card">
        <label htmlFor="agent-name">New agent name</label>
        <form onSubmit={handleCreate} className="row" style={{ marginTop: 0 }}>
          <input
            id="agent-name"
            type="text"
            placeholder="support-bot-eu"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button className="primary" type="submit" disabled={creating}>
            {creating ? "Creating…" : "+ New agent"}
          </button>
        </form>
        {error && <div className="banner error">{error}</div>}
      </div>

      {agents.length === 0 ? (
        <div className="empty-state">No agents yet — create one above.</div>
      ) : (
        <div className="agent-list">
          {agents.map((a) => (
            <div className="agent-card" key={a.id}>
              <div>
                <div className="name">{a.name}</div>
                <div className="meta">created {new Date(a.created_at).toLocaleDateString()}</div>
              </div>
              <div className="actions">
                <Link to={`/agents/${a.id}/policy`}>Policy</Link>
                <Link to={`/agents/${a.id}/chat`}>Chat demo</Link>
                <Link to={`/agents/${a.id}/learning`}>Learning plane</Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
