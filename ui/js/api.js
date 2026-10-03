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
export const getHealth = () => getJson("/healthz");
export const getThirteenF = () => getJson("/api/thirteenf");
export const getScorecard = () => getJson("/api/scorecard");
export const getAlertConfig = () => getJson("/api/alerts/config");
export const putAlertConfig = (body) => fetch("/api/alerts/config", {
  method: "PUT", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).then(async (r) => {
  if (!r.ok) throw new Error(`alerts/config ${r.status}`);
  return r.json();
});
export async function getBriefcheck() {
  const r = await fetch("/api/briefcheck");
  if (r.status === 404) { const e = new Error("no briefcheck report yet"); e.code = 404; throw e; }
  if (!r.ok) throw new Error(`briefcheck ${r.status}`);
  return r.json();
}

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
