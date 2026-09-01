import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearSession, getOrg } from "../api/client";

export default function Layout() {
  const navigate = useNavigate();
  const org = getOrg();

  function logout() {
    clearSession();
    navigate("/login");
  }

  return (
    <div>
      <div className="navbar">
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <span className="brand">ControlPlane.ai</span>
          <nav>
            <NavLink to="/" end>
              Org policy
            </NavLink>
            <NavLink to="/agents">Agents</NavLink>
          </nav>
        </div>
        <div className="right">
          <span className="org-name">{org?.name}</span>
          <button className="secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </div>
      <div className="app">
        <Outlet />
      </div>
    </div>
  );
}
