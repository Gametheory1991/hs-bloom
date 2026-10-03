// Same-origin by default (nginx proxies /api). Set window.OSBLOOM_API to point elsewhere.
const BASE = window.OSBLOOM_API ?? "";

async function getJson(path) {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
  return resp.json();
}

export const getDashboard = () => getJson("/api/dashboard");
export const getInsights = () => getJson("/api/insights");
export const getSeries = (id, range = "10y") => getJson(`/api/series/${encodeURIComponent(id)}?range=${range}`);
export const getRecessions = () => getJson("/api/recessions");

export async function postChat(message, history = []) {
  const resp = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail ?? `/api/chat: HTTP ${resp.status}`);
  }
  return resp.json();
}
