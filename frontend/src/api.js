const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_KEY = import.meta.env.VITE_API_KEY || "";

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = data?.detail?.error || data?.error || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

export const api = {
  ask: (question, sessionId) =>
    request("/ask", {
      method: "POST",
      body: JSON.stringify({ question, session_id: sessionId }),
    }),

  listTickets: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/tickets${qs ? `?${qs}` : ""}`);
  },

  updateTicketStatus: (id, status) =>
    request(`/tickets/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  exportTicketsCsv: async () => {
    const headers = API_KEY ? { "X-API-Key": API_KEY } : {};
    const res = await fetch(`${BASE_URL}/tickets/export`, { headers });
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    return res.blob();
  },
  getTicketComments: (id) => request(`/tickets/${id}/comments`),

  addTicketComment: (id, author, comment) =>
    request(`/tickets/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ author, comment }),
    }),
};
