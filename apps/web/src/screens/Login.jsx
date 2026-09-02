import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setSession } from "../api/client";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { token, organization } = await api.login(email, password);
      setSession(token, organization);
      navigate("/");
    } catch (err) {
      setError(err.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleViewDemo() {
    setError(null);
    setDemoLoading(true);
    try {
      const { token, organization } = await api.demoLogin();
      setSession(token, organization);
      navigate("/agents");
    } catch (err) {
      setError(err.detail || "Demo is not available right now");
    } finally {
      setDemoLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <h1>ControlPlane.ai</h1>
      <p className="sub">Log in to your organization</p>
      <div className="card">
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <button className="primary" type="submit" disabled={loading} style={{ width: "100%" }}>
            {loading ? "Logging in…" : "Log in"}
          </button>
          {error && <div className="banner error">{error}</div>}
        </form>
      </div>
      <p className="hint">
        No organization yet? <Link to="/signup">Sign up</Link>
      </p>
      <div className="card" style={{ marginTop: 8 }}>
        <p className="sub" style={{ marginBottom: 10 }}>
          Just want to see the learning plane's calibration + metrics demo? It runs against a fixed sample policy
          and a 100-case mock dataset — no signup needed.
        </p>
        <button className="secondary" onClick={handleViewDemo} disabled={demoLoading} style={{ width: "100%" }}>
          {demoLoading ? "Loading demo…" : "View demo"}
        </button>
      </div>
    </div>
  );
}
