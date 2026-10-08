// USAGE ANALYTICS panel (desk/usage) — built-in, no third-party service.
// IPs are hashed daily server-side and never stored raw; sessions are
// tab-scoped client UUIDs. Includes all traffic (yours included).
import { getUsage } from "../api.js";

let curDays = 30;
let chart = null;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function kpi(label, val, sub = "") {
  return `<div class="kpi"><div class="lbl">${esc(label)}</div>` +
    `<div class="val">${val}</div>` +
    (sub ? `<div class="chg flat">${sub}</div>` : "") + `</div>`;
}

function fmtDur(sec) {
  if (sec == null || sec <= 0) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m}m ${Math.round(sec % 60)}s` : `${(m / 60).toFixed(1)}h`;
}

function bar(pct, color = "#2563eb") {
  const w = Math.max(2, Math.min(100, pct * 100));
  return `<span style="display:inline-block;width:90px;height:8px;background:#eef2f7;border-radius:4px;vertical-align:middle;margin-right:8px">` +
    `<span style="display:block;width:${w}%;height:100%;background:${color};border-radius:4px"></span></span>`;
}

function drawChart(el, dailyV, dailyP) {
  if (chart) { try { chart.destroy(); } catch { /* noop */ } chart = null; }
  const byDay = new Map();
  dailyV.forEach((r) => byDay.set(r.day, { v: r.visitors, p: 0 }));
  dailyP.forEach((r) => {
    const e = byDay.get(r.day) ?? { v: 0, p: 0 };
    e.p = r.n; byDay.set(r.day, e);
  });
  const days = [...byDay.keys()].sort();
  if (days.length < 1) { el.innerHTML = `<span class="muted">no traffic yet</span>`; return; }
  const data = [days.map((d) => Date.parse(d + "T12:00:00Z") / 1000),
    days.map((d) => byDay.get(d).v), days.map((d) => byDay.get(d).p)];
  const axis = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
  chart = new uPlot({
    width: Math.max(300, el.clientWidth || 720), height: 240,
    series: [{},
      { label: "Visitors", stroke: "#2563eb", width: 2, fill: "rgba(37,99,235,.12)" },
      { label: "Pageviews", stroke: "#0891b2", width: 1.4, dash: [5, 4] }],
    axes: [axis, { ...axis }],
  }, data, el);
}

async function load() {
  const body = document.querySelector("#panel-usage .panel-body");
  if (!body) return;
  body.querySelectorAll("[data-days]").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.days) === curDays));
  const host = body.querySelector("#usage-content");
  host.innerHTML = `<span class="muted">loading…</span>`;
  let u;
  try {
    u = await getUsage(curDays);
  } catch (err) {
    host.innerHTML = `<div class="muted">usage data unavailable (${esc(err.message)})</div>`;
    return;
  }
  const k = u.kpis;
  const bounce = k.bounce_rate == null ? "—" : `${(k.bounce_rate * 100).toFixed(1)}%`;
  const maxRoute = Math.max(1, ...u.top_routes.map((r) => r.pageviews));
  const maxHub = Math.max(1, ...u.hub_clicks.map((h) => h.clicks));
  const maxRef = Math.max(1, ...u.referrers.map((r) => r.hits));
  const devTotal = u.devices.reduce((a, d) => a + d.visitors, 0) || 1;

  host.innerHTML = `
    <div class="kpis" style="margin-bottom:12px">
      ${kpi("UNIQUE VISITORS", k.unique_visitors.toLocaleString(), `${u.days}d`)}
      ${kpi("PAGEVIEWS", k.pageviews.toLocaleString(), `${u.days}d`)}
      ${kpi("SESSIONS", k.sessions.toLocaleString(), `${u.days}d`)}
      ${kpi("BOUNCE RATE", bounce, "1-page sessions")}
      ${kpi("AVG SESSION", fmtDur(k.avg_session_seconds), "")}
    </div>
    <div class="panel-title" style="margin:6px 0">DAILY TRAFFIC</div>
    <div id="usage-chart" style="min-height:240px"></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:12px">
      <div><div class="panel-title" style="margin-bottom:6px">TOP ROUTES</div>
        <table data-sortable><tr><th>Route</th><th>Pageviews</th></tr>
        ${u.top_routes.map((r) => `<tr><td>${esc(r.route)}</td><td class="num">${bar(r.pageviews / maxRoute)}${r.pageviews.toLocaleString()}</td></tr>`).join("") || `<tr><td colspan="2" class="muted">no data</td></tr>`}
        </table></div>
      <div><div class="panel-title" style="margin-bottom:6px">HUB CLICKS</div>
        <table data-sortable><tr><th>Hub</th><th>Clicks</th><th>Share</th></tr>
        ${u.hub_clicks.map((h) => `<tr><td>${esc(h.hub.toUpperCase())}</td><td class="num">${bar(h.clicks / maxHub, "#0891b2")}${h.clicks.toLocaleString()}</td><td class="num">${(h.share * 100).toFixed(1)}%</td></tr>`).join("") || `<tr><td colspan="3" class="muted">no data</td></tr>`}
        </table></div>
      <div><div class="panel-title" style="margin-bottom:6px">REFERRERS</div>
        <table data-sortable><tr><th>Referrer</th><th>Hits</th></tr>
        ${u.referrers.map((r) => `<tr><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis">${esc(r.referrer)}</td><td class="num">${bar(r.hits / maxRef, "#7c3aed")}${r.hits.toLocaleString()}</td></tr>`).join("") || `<tr><td colspan="2" class="muted">direct / bookmark only</td></tr>`}
        </table></div>
      <div><div class="panel-title" style="margin-bottom:6px">DEVICE SPLIT</div>
        <table data-sortable><tr><th>Device</th><th>Visitors</th><th>Share</th></tr>
        ${u.devices.map((d) => `<tr><td>${esc(d.device)}</td><td class="num">${bar(d.visitors / devTotal, "#16a34a")}${d.visitors.toLocaleString()}</td><td class="num">${(d.visitors / devTotal * 100).toFixed(1)}%</td></tr>`).join("") || `<tr><td colspan="3" class="muted">no data</td></tr>`}
        </table></div>
    </div>
    <p class="muted" style="margin-top:10px;font-size:11px">Includes all traffic (yours included). IPs are SHA256-hashed with a daily salt and never stored raw; no cookies.</p>`;
  drawChart(host.querySelector("#usage-chart"), u.daily_visitors, u.daily_pageviews);
}

export function renderUsage() {
  const body = document.querySelector("#panel-usage .panel-body");
  if (!body || body.dataset.init) return;
  body.dataset.init = "1";
  body.innerHTML = `
    <div style="margin-bottom:10px;display:flex;gap:6px;align-items:center">
      <span class="muted">Range:</span>
      ${[7, 30, 90].map((d) => `<button type="button" class="header-btn${d === curDays ? " active" : ""}" data-days="${d}">${d}D</button>`).join("")}
    </div>
    <div id="usage-content"></div>`;
  body.querySelectorAll("[data-days]").forEach((b) =>
    b.addEventListener("click", () => { curDays = Number(b.dataset.days); load(); }));
  load();
}
