const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export function getToken() {
  return localStorage.getItem("cp_token");
}

export function setSession(token, organization) {
  localStorage.setItem("cp_token", token);
  localStorage.setItem("cp_org", JSON.stringify(organization));
}

export function getOrg() {
  const raw = localStorage.getItem("cp_org");
  return raw ? JSON.parse(raw) : null;
}

export function clearSession() {
  localStorage.removeItem("cp_token");
  localStorage.removeItem("cp_org");
}

class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await res.json() : null;

  if (!res.ok) {
    throw new ApiError(res.status, payload?.detail ?? res.statusText);
  }
  return payload;
}

export const api = {
  signup: (name, email, password) =>
    request("/auth/signup", { method: "POST", body: { name, email, password }, auth: false }),
  login: (email, password) =>
    request("/auth/login", { method: "POST", body: { email, password }, auth: false }),

  getOrgPolicy: () => request("/policies/org"),
  postOrgPolicy: (content) => request("/policies/org", { method: "POST", body: { content } }),

  listAgents: () => request("/agents"),
  createAgent: (name) => request("/agents", { method: "POST", body: { name } }),
  getAgent: (agentId) => request(`/agents/${agentId}`),
  getAgentPolicy: (agentId) => request(`/agents/${agentId}/policy`),
  postAgentPolicy: (agentId, content) =>
    request(`/agents/${agentId}/policy`, { method: "POST", body: { content } }),

  check: (agentId, prompt) => request("/check", { method: "POST", body: { agent_id: agentId, prompt } }),
};

export { ApiError };
