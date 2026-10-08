// FINRA ATS Transparency — dark-pool venue leaderboard + security lookup.
// Sub-tab of the EQUITY hub ("ATS Transparency"). Two data modes:
//   weekly  — ATS_W_FIRM / ATS_W_SMBL / ATS_W_SMBL_FIRM via OAuth2 key
//             (FINRA_CLIENT_ID/FINRA_CLIENT_SECRET on Render)
//   monthly — keyless blocksSummary (2016 -> present), used when no key
//             is set or the keyed pull hasn't populated weekly data yet.
// Every delta cell shows nominal + % (Harry's rule); deltas sort off the
// % value. Bloomberg-style dotted range sparklines via rangeCells.
import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";
import { RANGES, rangeById } from "./trace_grid.js";
import { matrixToCSV, exportTablesXLSX, todayStamp } from "../export.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";

const esc = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const big = (x) =>
  x == null || !isFinite(x) ? "—" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 });
const bigC = (x) => // compact: 1.2B / 340M
  x == null || !isFinite(x) ? "—" :
  Math.abs(x) >= 1e9 ? (x / 1e9).toFixed(2) + "B" :
  Math.abs(x) >= 1e6 ? (x / 1e6).toFixed(1) + "M" :
  Math.abs(x) >= 1e3 ? (x / 1e3).toFixed(1) + "K" : String(Math.round(x));
const pct1 = (x) =>
  x == null || !isFinite(x) ? "—" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
// Nominal+% delta cell: "+1.2B sh (+3.4%)", heatmapped on the % value.
const dCell = (nom, pct, unit) => {
  if ((nom == null || !isFinite(nom)) && (pct == null || !isFinite(pct))) return `<td class="num">—</td>`;
  const n = nom == null || !isFinite(nom) ? "—"
    : `${nom >= 0 ? "+" : "−"}${bigC(Math.abs(nom))}${unit ? " " + unit : ""}`;
  const p = pct1(pct);
  return `<td class="num" data-sort-val="${pct ?? ""}"${heatStyle({ pct })}>${n} <span class="muted">(${p})</span></td>`;
};

const atState = { range: "1y", reqId: 0, plot: null, lookupReq: 0 };

function atFilterRange(points) {
  if (atState.range === "max") return points;
  const r = rangeById(atState.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-Math.max(4, Math.round(r.months * 4.345)));
}

async function drawAtChart(prefix, statusEl) {
  const reqId = ++atState.reqId;
  const el = document.getElementById("ats-chart");
  if (!el) return;
  if (statusEl) statusEl.textContent = "Loading…";
  try {
    const [shares, trades] = await Promise.all([
      getSeries(`ats-${prefix}total-shares`, "max"),
      getSeries(`ats-${prefix}total-trades`, "max"),
    ]);
    if (reqId !== atState.reqId) return;
    const sp = atFilterRange(shares.points ?? []), tp = atFilterRange(trades.points ?? []);
    const byDate = new Map();
    for (const [d, v] of sp) byDate.set(d, [v, null]);
    for (const [d, v] of tp) { const c = byDate.get(d) ?? [null, null]; c[1] = v; byDate.set(d, c); }
    const dates = [...byDate.keys()].sort();
    const xs = dates.map((d) => Date.parse(d) / 1000);
    const y1 = dates.map((d) => byDate.get(d)[0]);
    const y2 = dates.map((d) => byDate.get(d)[1]);
    if (atState.plot) { atState.plot.destroy(); atState.plot = null; }
    el.innerHTML = "";
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      atState.plot = new uPlot({
        width: Math.max(300, el.clientWidth || 900), height: 340,
        scales: { x: { time: true }, y: {}, y2: {} },
        series: [{}, { label: "Shares", stroke: "#2563eb", width: 2, spanGaps: true },
          { label: "Trades", stroke: "#f59e0b", width: 1.5, spanGaps: true, scale: "y2" }],
        axes: [
          { ...axisStyle },
          { ...axisStyle, scale: "y", values: (u, v) => v.map((x) => x == null ? "" : bigC(x)) },
          { ...axisStyle, scale: "y2", side: 1, values: (u, v) => v.map((x) => x == null ? "" : bigC(x)) },
        ],
        legend: { show: true },
      }, [xs, y1, y2], el);
      if (statusEl) statusEl.textContent =
        `${shares.name ?? "ATS shares"} vs ${trades.name ?? "ATS trades"} · ${dates.length} points`;
    } else if (statusEl) statusEl.textContent = "No history yet";
  } catch (e) {
    if (statusEl) statusEl.textContent = `Chart unavailable: ${esc(e?.message ?? e)}`;
  }
}

export function renderATS(ats) {
  const body = document.querySelector('#panel-ats .panel-body');
  if (!body) return;
  const a = ats ?? {};
  const lb = a.leaderboard ?? [];
  const horizons = a.horizons ?? [];
  const weekly = !!a.weekly;
  const prefix = weekly ? "" : "m-";

  const scopeLine = weekly
    ? `FINRA ATS Transparency · <b>weekly</b>, 2–4-week delayed (Tier 1 NMS: 2 wk · others: 4 wk)`
    : `FINRA ATS block summary · <b>monthly</b>, 2016–present · keyless`;
  const notices = [];
  if (!lb.length) notices.push(
    `<div class="notice">No ATS data yet — the <code>finra_ats</code> job hasn't run.</div>`);
  if (lb.length && !a.configured) notices.push(
    `<div class="notice warn">Weekly dark-pool detail needs a free FINRA API key — ` +
    `get one at <b>gateway.finra.org/app/api-console</b> and set ` +
    `<code>FINRA_CLIENT_ID</code> / <code>FINRA_CLIENT_SECRET</code> as Render env vars. ` +
    `Showing the keyless monthly ATS block summary (2016–present).</div>`);
  if ((a.backfill_pending ?? 0) > 0) notices.push(
    `<div class="notice">Weekly backfill in progress: ${a.backfill_pending} weeks remaining ` +
    `(history to May 2014 lands over successive runs).</div>`);

  const rangeBtns = RANGES.filter((r) => ["1m", "3m", "1y", "max"].includes(r.id))
    .map((x) => `<button data-range="${x.id}" class="${x.id === atState.range ? "on" : ""}">${x.label}</button>`).join("");

  const hTh = horizons.map((h) => `<th title="Nominal and % change vs ${h} ago">${h.toUpperCase()} Δ</th>`).join("");
  const rows = lb.map((r) => {
    const s = statsFromValues(r.spark ?? []);
    const dts = horizons.map((h) => dCell(r.deltas?.[h]?.nom, r.deltas?.[h]?.pct, "sh")).join("");
    return `<tr><td><b>${esc(r.mpid)}</b><br><span class="muted">${esc(r.name)}</span></td>` +
      `<td class="num" data-sort-val="${r.shares ?? ""}">${bigC(r.shares)}</td>` +
      `<td class="num" data-sort-val="${r.trades ?? ""}">${big(r.trades)}</td>` +
      `<td class="num" data-sort-val="${r.share_of_ats ?? ""}">${pct1(r.share_of_ats)}</td>` +
      dts + rangeCells(s, "venue history") + `</tr>`;
  }).join("");
  const span = horizons.length + 8;

  body.innerHTML =
    `<h3>FINRA ATS TRANSPARENCY — DARK POOLS <span class="muted">${scopeLine} · latest ${esc(a.as_of ?? "—")}</span></h3>` +
    notices.join("") +
    `<div class="star-dl-row">
       <button id="ats-dl-csv" class="mini-btn">⤓ CSV (venue leaderboard)</button>
       <button id="ats-dl-xlsx" class="mini-btn">⤓ XLSX (venue leaderboard)</button>
     </div>
     <p class="muted">Weekly ATS (dark pool) share/trade volume by venue. ` +
     `Delays: Tier 1 NMS stocks 2 weeks, all others 4 weeks. ` +
     `Venue rows with &lt;200 avg daily trades are aggregated non-attributed by FINRA.</p>
     <h3>ALL-ATS VOLUME <span class="muted">shares (blue) vs trades (gold, right axis)</span></h3>
     <div class="trace-controls"><span class="seg" id="ats-range">${rangeBtns}</span>
       <span id="ats-chart-status" class="muted"></span></div>
     <div id="ats-chart" class="trace-chart"></div>
     <h3>VENUE LEADERBOARD <span class="muted">${lb.length} ATS venues · sorted by latest volume</span></h3>
     <div>${HEAT_LEGEND}</div>
     <div class="table-scroll"><table data-sortable><tr><th>Venue</th><th title="Shares, latest ${weekly ? "week" : "month"}">Shares</th>` +
     `<th title="Trades, latest ${weekly ? "week" : "month"}">Trades</th>` +
     `<th title="Share of all ATS volume">Share of ATS</th>${hTh}${RANGE_TH}</tr>` +
     (rows || `<tr data-sort-row="off"><td colspan="${span}" class="muted">No venue data yet.</td></tr>`) +
     `</table></div>
     <h3>SECURITY LOOKUP <span class="muted">weekly ATS volume per ticker + top dark pools</span></h3>
     <div class="trace-controls">
       <input id="ats-sym" type="text" placeholder="Ticker, e.g. NVDA" aria-label="Ticker"
              style="text-transform:uppercase" maxlength="8">
       <button id="ats-lookup" class="mini-btn">Look up</button>
       <span id="ats-lookup-status" class="muted"></span>
     </div>
     <div id="ats-sym-chart" class="trace-chart"></div>
     <div id="ats-sym-venues"></div>`;

  // --- trend chart ---
  drawAtChart(prefix, document.getElementById("ats-chart-status"));
  document.getElementById("ats-range")?.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-range]");
    if (!b) return;
    atState.range = b.dataset.range;
    document.querySelectorAll("#ats-range button").forEach((x) =>
      x.classList.toggle("on", x === b));
    drawAtChart(prefix, document.getElementById("ats-chart-status"));
  });

  // --- exports ---
  const dlRows = () => lb.map((r) => {
    const o = { MPID: r.mpid, Name: r.name, Shares: r.shares, Trades: r.trades,
                ShareOfATS: r.share_of_ats };
    for (const h of horizons) {
      o[`${h.toUpperCase()}Δ_nom_sh`] = r.deltas?.[h]?.nom ?? "";
      o[`${h.toUpperCase()}Δ_pct`] = r.deltas?.[h]?.pct ?? "";
    }
    return o;
  });
  document.getElementById("ats-dl-csv")?.addEventListener("click", () => {
    const rowsX = dlRows();
    if (!rowsX.length) return;
    const csv = matrixToCSV(Object.keys(rowsX[0]), rowsX.map(Object.values));
    const aEl = document.createElement("a");
    aEl.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    aEl.download = `finra-ats-leaderboard-${todayStamp()}.csv`;
    aEl.click();
  });
  document.getElementById("ats-dl-xlsx")?.addEventListener("click", () => {
    const rowsX = dlRows();
    if (!rowsX.length) return;
    exportTablesXLSX([{ name: "venues", headers: Object.keys(rowsX[0]),
      rows: rowsX.map(Object.values) }], `finra-ats-leaderboard-${todayStamp()}`);
  });

  // --- security lookup ---
  const doLookup = async () => {
    const inp = document.getElementById("ats-sym");
    const status = document.getElementById("ats-lookup-status");
    const chartEl = document.getElementById("ats-sym-chart");
    const venEl = document.getElementById("ats-sym-venues");
    const sym = (inp?.value ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!sym) { if (status) status.textContent = "Enter a ticker."; return; }
    const reqId = ++atState.lookupReq;
    if (status) status.textContent = "Loading…";
    if (venEl) venEl.innerHTML = "";
    try {
      const [sh, tr] = await Promise.all([
        getSeries(`ats-${prefix}sym-${sym}-shares`, "max"),
        getSeries(`ats-${prefix}sym-${sym}-trades`, "max"),
      ]);
      if (reqId !== atState.lookupReq) return;
      const sp = sh.points ?? [];
      if (!sp.length) {
        if (status) status.textContent = `${sym}: no ATS history (outside the top-500 weekly symbols, or no key).`;
        if (chartEl) chartEl.innerHTML = "";
        return;
      }
      const byDate = new Map();
      for (const [d, v] of sp) byDate.set(d, [v, null]);
      for (const [d, v] of (tr.points ?? [])) { const c = byDate.get(d) ?? [null, null]; c[1] = v; byDate.set(d, c); }
      const dates = [...byDate.keys()].sort();
      const xs = dates.map((d) => Date.parse(d) / 1000);
      const y1 = dates.map((d) => byDate.get(d)[0]);
      const y2 = dates.map((d) => byDate.get(d)[1]);
      chartEl.innerHTML = "";
      const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
      if (typeof uPlot !== "undefined" && xs.length > 1) {
        new uPlot({
          width: Math.max(300, chartEl.clientWidth || 900), height: 280,
          scales: { x: { time: true }, y: {}, y2: {} },
          series: [{}, { label: `${sym} shares`, stroke: "#2563eb", width: 2, spanGaps: true },
            { label: `${sym} trades`, stroke: "#f59e0b", width: 1.5, spanGaps: true, scale: "y2" }],
          axes: [{ ...axisStyle },
            { ...axisStyle, scale: "y", values: (u, v) => v.map((x) => x == null ? "" : bigC(x)) },
            { ...axisStyle, scale: "y2", side: 1, values: (u, v) => v.map((x) => x == null ? "" : bigC(x)) }],
          legend: { show: true },
        }, [xs, y1, y2], chartEl);
      }
      const cur = sp[sp.length - 1][1];
      if (status) status.textContent =
        `${sym}: ${bigC(cur)} shares, latest ${weekly ? "week" : "month"} ${esc(a.as_of ?? "")}`;
      // Top venues for the symbol (from the latest keyed weekly snapshot).
      const det = (a.latest_week_detail?.symbols ?? []).find((s) => s.symbol === sym);
      if (det?.top_venues?.length && venEl) {
        const tot = det.top_venues.reduce((s2, v) => s2 + (v.shares ?? 0), 0);
        venEl.innerHTML = `<p class="muted">Top dark pools for ${esc(sym)} — latest keyed week ` +
          `${esc(a.latest_week_detail?.week ?? "")}:</p><div class="table-scroll"><table><tr>` +
          `<th>Venue</th><th>Shares</th><th>Share of symbol's top venues</th></tr>` +
          det.top_venues.map((v) =>
            `<tr><td><b>${esc(v.mpid)}</b> <span class="muted">${esc(v.name)}</span></td>` +
            `<td class="num">${bigC(v.shares)}</td>` +
            `<td class="num">${pct1(tot ? v.shares / tot : null)}</td></tr>`).join("") +
          `</table></div>`;
      } else if (venEl) {
        venEl.innerHTML = `<p class="muted">Top-venue detail needs the keyed weekly feed ` +
          `(top venues are tracked for the top-50 weekly symbols).</p>`;
      }
    } catch (e) {
      if (status) status.textContent = `${sym}: ${esc(e?.message ?? e)}`;
    }
  };
  document.getElementById("ats-lookup")?.addEventListener("click", doLookup);
  document.getElementById("ats-sym")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLookup();
  });
}
