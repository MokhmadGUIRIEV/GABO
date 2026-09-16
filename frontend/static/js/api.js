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
    apiRequest("/api/rooms", { method: "POST", body: { player_names } }),
  getRoom: (code) => apiRequest(`/api/rooms/${code}`),
  history: () => apiRequest("/api/rooms/history/mine"),
};

function wsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
