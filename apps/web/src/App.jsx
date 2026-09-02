import { Navigate, Route, Routes } from "react-router-dom";
import { getToken } from "./api/client";
import Layout from "./components/Layout";
import Login from "./screens/Login";
import Signup from "./screens/Signup";
import OrgPolicy from "./screens/OrgPolicy";
import AgentsList from "./screens/AgentsList";
import AgentPolicy from "./screens/AgentPolicy";
import Chat from "./screens/Chat";
import LearningPlane from "./screens/LearningPlane";

function RequireAuth({ children }) {
  return getToken() ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<OrgPolicy />} />
        <Route path="/agents" element={<AgentsList />} />
        <Route path="/agents/:agentId/policy" element={<AgentPolicy />} />
        <Route path="/agents/:agentId/chat" element={<Chat />} />
        <Route path="/agents/:agentId/learning" element={<LearningPlane />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
