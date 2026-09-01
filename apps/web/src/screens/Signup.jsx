import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setSession } from "../api/client";

export default function Signup() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { token, organization } = await api.signup(name, email, password);
      setSession(token, organization);
      navigate("/");
    } catch (err) {
      setError(err.detail || "Signup failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <h1>ControlPlane.ai</h1>
      <p className="sub">Create your organization</p>
      <div className="card">
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="name">Organization name</label>
            <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
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
              minLength={8}
              required
            />
          </div>
          <button className="primary" type="submit" disabled={loading} style={{ width: "100%" }}>
            {loading ? "Creating…" : "Sign up"}
          </button>
          {error && <div className="banner error">{error}</div>}
        </form>
      </div>
      <p className="hint">
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </div>
  );
}
