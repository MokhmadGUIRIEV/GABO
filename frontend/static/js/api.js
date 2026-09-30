const API_BASE = "";

async function apiRequest(path, { method = "GET", body } = {}) {
  const res = await fetch(API_BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    credentials: "same-origin",
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok) {
    const message = (data && data.detail) || `Erreur ${res.status}`;
    throw new Error(message);
  }
  return data;
}

const Api = {
  register: (email, display_name, password) =>
    apiRequest("/api/auth/register", { method: "POST", body: { email, display_name, password } }),
  login: (email, password) =>
    apiRequest("/api/auth/login", { method: "POST", body: { email, password } }),
  logout: () => apiRequest("/api/auth/logout", { method: "POST" }),
  me: () => apiRequest("/api/auth/me"),
  createRoom: (player_names) =>
    apiRequest("/api/rooms", { method: "POST", body: { mode: "local", player_names } }),
  createOnlineRoom: () => apiRequest("/api/rooms", { method: "POST", body: { mode: "online" } }),
  joinRoom: (code) => apiRequest(`/api/rooms/${encodeURIComponent(code)}/join`, { method: "POST" }),
  getRoom: (code) => apiRequest(`/api/rooms/${encodeURIComponent(code)}`),
  history: () => apiRequest("/api/rooms/history/mine"),
  leaderboard: () => apiRequest("/api/rooms/leaderboard"),
};

function wsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

// Player names come from other people's accounts in online games: always
// escape them (and any other untrusted text) before putting them in HTML.
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Only follow same-site relative paths after login (no open redirect).
function safeNextPath(raw) {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return null;
  return raw;
}
