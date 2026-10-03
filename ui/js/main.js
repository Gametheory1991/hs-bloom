import { getDashboard, getScorecard } from "./api.js";
import { fmtAge, fmtClock, isStale } from "./fmt.js";
import { defiFootData, initDefiViewToggle, renderDefi, renderMidnight } from "./panels/defi.js";
import { renderBonds, renderEquity } from "./panels/equity.js";
import { renderMacro } from "./panels/macro.js";
import { renderNews } from "./panels/news.js";
import { renderCycle } from "./panels/cycle.js";
import { renderEarningsProfit, renderFiscalEcon } from "./panels/fiscal.js";
import { renderFigiLookup } from "./panels/figi.js";
import { renderRiskMap } from "./panels/riskmap.js";
import { renderRadar } from "./panels/radar.js";
import { renderHyper } from "./panels/hyper.js";
import { renderUniverseSelector } from "./panels/ai_flow.js";
import { renderTsv } from "./panels/tsv.js";
import { renderXcorr } from "./panels/xcorr.js";
import { renderVol } from "./panels/vol.js";
import { renderMovers } from "./panels/movers.js";
import { renderFutures, FUTURES } from "./panels/futures.js";
import { renderFlows } from "./panels/flows.js";
import { renderScorecard } from "./panels/scorecard.js";
import { renderCentral } from "./panels/central.js";
import { renderAlerts } from "./panels/alerts.js";
import { renderBriefcheck } from "./panels/briefcheck.js";
import { initPalette, updateIndex } from "./palette.js";
import { initHealth } from "./health.js";
import { renderRefs } from "./panels/refs.js";
import { renderInsights } from "./panels/insights.js";
import { initNotifications, notifyInsights } from "./notifications.js";
import { initChat } from "./chat.js";
import { initTabs } from "./tabs.js";

const POLL_MS = 60_000;
const STALE_MINUTES = { equity: 20, bonds: 130, macro: 390, news: 40, defi: 35, midnight: 35, refs: 35, insights: 70, riskmap: 2880, xcorr: 2880, gse: 86400, vol: 2880, movers: 10080, radar: 2880, hyper: 10080, tsv: 10080, usaspending: 20160, finnhub: 2880, worldbank: 20160, coingecko: 2880 };  // ~2x cadence

const EMPTY = { rows: [], updated_at: null, source: null };

let lastDash = null; // last successful payload, for the view-toggle re-render (no re-fetch)

// Command-palette index: tabs + panels + series, rebuilt from live payloads
// (never hardcoded). Series entries deep-link to their tab.
const PANEL_ENTRIES = [
  ["MARKET RADAR", "mkt"], ["ALERTS / NEWSLETTER", "mkt"], ["ALERT TUNING", "mkt"],
  ["BRIEFING × TERMINAL CHECK", "mkt"], ["TOP NEWS", "mkt"],
  ["FUTURES — FRONT-MONTH", "futures"], ["FLOWS — 13F NET FLOWS", "flows"],
  ["SCORECARD — 1D/1M/3M/1Y + 1Y Z", "scorecard"], ["CENTRAL — FED WATCH", "central"],
];

function buildIndex(dash) {
  const idx = [];
  document.querySelectorAll("[data-tab-link]").forEach((a) =>
    idx.push({ label: `${a.textContent.trim()} tab`, sub: "tab", hash: `#/${a.dataset.tabLink}` }));
  for (const [label, tab] of PANEL_ENTRIES)
    idx.push({ label, sub: "panel", hash: `#/${tab}` });
  for (const t of dash?.panels?.cycle?.tabs ?? [])
    for (const p of t.panels ?? [])
      for (const r of p.rows ?? [])
        if (r.name) idx.push({ label: r.name, sub: `series · ${t.id}`, hash: `#/${t.id}` });
  return idx;
}

async function refreshSearchIndex() {
  const base = buildIndex(lastDash);
  try {
    const sc = await getScorecard();
    for (const r of sc.rows ?? [])
      if (r.name) base.push({ label: r.name, sub: "series · scorecard", hash: "#/scorecard" });
  } catch { /* scorecard down — tabs/series index still works */ }
  for (const [, sym, label] of FUTURES)
    base.push({ label: `${sym} — ${label}`, sub: "futures", hash: "#/futures" });
  updateIndex(base);
}

function foot(panelId, name, data) {
  const el = document.querySelector(`#panel-${panelId} .panel-foot`);
  const src = (data.source ?? "—").toUpperCase();
  el.textContent = `DATA: ${src} · ${fmtAge(data.updated_at)}`;
  el.classList.toggle("stale", isStale(data.updated_at, STALE_MINUTES[name]));
}

function renderDefiPanel(p) {
  const defi = p.defi ?? EMPTY;      // ?? EMPTY: tolerate an old collector during rollout
  const morpho = p.morpho ?? EMPTY;
  renderDefi(defi, morpho);
  foot("defi", "defi", defiFootData(defi, morpho));
}

async function tick() {
  const banner = document.getElementById("banner");
  try {
    const dash = await getDashboard();
    lastDash = dash;
    const p = dash.panels;
    renderEquity(p.equity);
    renderBonds(p.bonds);
    renderMacro(p.macro);
    renderNews(p.news);
    renderDefiPanel(p);
    renderMidnight(p.midnight ?? EMPTY);
    renderRefs(p.refs ?? EMPTY);
    renderCycle(p.cycle ?? { tabs: [], updated_at: null });
    renderFiscalEcon(p.usaspending ?? { monthly: [], top_recipients: [], top_agencies: [], updated_at: null });
    renderEarningsProfit(p.finnhub ?? { earnings: [], insider: [], key_configured: false, updated_at: null });
    renderFigiLookup();
    renderRiskMap(p.riskmap ?? { countries: [], asof: null, updated_at: null, source: null });
    renderXcorr(p.xcorr ?? { labels: [], matrix_60d: [], pairs: [], rvol: [] },
                p.gse ?? { series: [] });
    renderVol(p.voldash ?? { rows: [], vix_hist: [], vvix_hist: [], beta: {}, beta_hist: {} });
    renderMovers(p.movers ?? { indexes: {}, asof: null, updated_at: null, source: null });
    renderRadar(p.radar ?? { indicators: [], regime: "UNKNOWN", as_of: null, updated_at: null, source: null },
                p.riskmap ?? { countries: [], asof: null, updated_at: null, source: null });
    renderHyper(p.hyper ?? { issuances: [], equities: [], note: null, updated_at: null, source: null });
    renderUniverseSelector(p.ai_flow ?? { universe_id: "ai_buildout", verticals: [], edges: [], rollups: {}, capex_stack: {}, risk_notes: [], updated_at: null, source: null },
                           p.ms_flow ?? { universe_id: "market_structure", verticals: [], edges: [], rollups: {}, capex_stack: {}, risk_notes: [], updated_at: null, source: null });
    renderTsv(p.tsv ?? { verticals: [], edges: [], risk_notes: [], order: {}, watch: null, updated_at: null, source: null });
    renderInsights(p.insights ?? { alerts: [], trends: [], newsletter: { headline: "No digest yet", bullets: [] } });
    foot("equity", "equity", { ...p.equity, source: p.equity.rows[0]?.source });
    foot("bonds", "bonds", p.bonds);
    foot("macro", "macro", p.macro);
    foot("news", "news", p.news);
    foot("midnight", "midnight", p.midnight ?? EMPTY);
    foot("refs", "refs", p.refs ?? EMPTY);
    foot("insights", "insights", p.insights ?? { updated_at: null, source: null });
    foot("riskmap", "riskmap", p.riskmap ?? { updated_at: null, source: null });
    foot("radar", "radar", p.radar ?? { updated_at: null, source: null });
    foot("hyper", "hyper", p.hyper ?? { updated_at: null, source: null });
    foot("tsv", "tsv", p.tsv ?? { updated_at: null, source: null });
    await notifyInsights(p.insights);
    document.getElementById("clock").textContent = `as of ${fmtClock(dash.as_of)} UTC`;
    updateIndex(buildIndex(dash));
    banner.classList.add("hidden");
  } catch (err) {
    banner.textContent = `COLLECTOR UNREACHABLE — ${err.message}`;
    banner.classList.remove("hidden");
  }
}

initTabs();
initNotifications();
initChat();
initHealth();
initPalette();
renderFutures();
renderFlows();
renderScorecard();
renderCentral();
renderAlerts();
renderBriefcheck();
refreshSearchIndex();
setInterval(() => { renderFutures(); renderFlows(); renderScorecard(); renderCentral(); renderBriefcheck(); refreshSearchIndex(); }, 15 * 60_000);
initDefiViewToggle(() => {
  if (lastDash) renderDefiPanel(lastDash.panels);
});
// A chart drawn while its tab is hidden sees clientWidth 0 and falls back to
// 300px; redraw on tab switch so it sizes to the now-visible panel.
window.addEventListener("hashchange", () => {
  if (lastDash) renderMidnight(lastDash.panels.midnight ?? EMPTY);
});
tick();
setInterval(tick, POLL_MS);
