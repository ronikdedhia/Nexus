const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_KEY = import.meta.env.VITE_API_KEY || "";

function authHeaders(extra = {}) {
  return API_KEY ? { ...extra, "X-API-Key": API_KEY } : extra;
}

function errorMessage(res, data) {
  if (res.status === 401) return "The app isn't authorised to reach the server (API key missing or wrong).";
  if (res.status === 429) return data?.detail?.error || "Too many requests. Please wait a moment.";
  return data?.detail?.error || data?.error || `Request failed (${res.status})`;
}

async function request(path, options = {}) {
  const headers = authHeaders({ "Content-Type": "application/json", ...options.headers });
  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  } catch {
    throw new Error("Can't reach the server. It may be starting up; try again in a minute.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorMessage(res, data));
  return data;
}

function query(params) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  const qs = new URLSearchParams(clean).toString();
  return qs ? `?${qs}` : "";
}

export const api = {
  ask: (question, sessionId) =>
    request("/ask", {
      method: "POST",
      body: JSON.stringify({ question, session_id: sessionId }),
    }),
  listTickets: (params = {}) => request(`/tickets${query(params)}`),
  updateTicketStatus: (id, status) =>
    request(`/tickets/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  exportTicketsCsv: async () => {
    const res = await fetch(`${BASE_URL}/tickets/export`, { headers: authHeaders() });
    if (!res.ok) throw new Error(errorMessage(res, null));
    return res.blob();
  },
  getTicketComments: (id) => request(`/tickets/${id}/comments`),
  addTicketComment: (id, author, comment) =>
    request(`/tickets/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ author, comment }),
    }),
  listEmployees: (params = {}) => request(`/employees${query(params)}`),
  listDepartments: () => request("/departments"),
};
