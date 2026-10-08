// Same-origin by default (nginx proxies /api). Set window.OSBLOOM_API to point elsewhere.
const BASE = window.OSBLOOM_API ?? "";

async function getJson(path) {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
  return resp.json();
}

export const getDashboard = (hub) => getJson(hub ? `/api/dashboard?hub=${encodeURIComponent(hub)}` : "/api/dashboard");
export const getInsights = () => getJson("/api/insights");
export const getSeries = (id, range = "10y") => getJson(`/api/series/${encodeURIComponent(id)}?range=${range}`);
export const getRecessions = () => getJson("/api/recessions");
export const getHealth = () => getJson("/healthz");
export const getThirteenF = () => getJson("/api/thirteenf");
export const getEtfHolders = (ticker) => getJson(`/api/etf-holders/${encodeURIComponent(ticker)}`);
export const getShortInterestTable = (q = "") => getJson(`/api/equity/short-interest${q}`);
export const getShortInterestSettlements = () => getJson("/api/equity/short-interest/settlements");
export const getRegshoTopTable = (q = "") => getJson(`/api/equity/regsho-top${q}`);
export const getThresholdHistDates = () => getJson("/api/equity/threshold-history/dates");
export const getThresholdHist = (q = "") => getJson(`/api/equity/threshold-history${q}`);
export const getScorecard = () => getJson("/api/scorecard");
export const getAuctions = () => getJson("/api/auctions");
export const getEconCalendar = () => getJson("/api/econ-calendar");
export const getAlertConfig = () => getJson("/api/alerts/config");
export const getUsage = (days = 30) => getJson(`/api/usage?days=${days}`);
export const getOtcTop100 = (month = "") => getJson(`/api/otc/top100${month ? `?month=${month}` : ""}`);
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
