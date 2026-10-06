// FLOW → STAR sub-tab: FINRA-IDS Structured Trading Activity Reports.
// Full 28-series daily dataset with CSV/XLSX download, a dedicated chart
// (product + metric + date-range selectors), and the delta/sparkline table.
// Moved out of the FINRA panel 2026-10-06 per Harry (own sub-tab, downloads,
// chart controls).
import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";
import { RANGES, rangeById } from "./trace_grid.js";
import { matrixToCSV, exportTablesXLSX, todayStamp } from "../export.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";

const big = (x) =>
  x == null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const pct1 = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const esc = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// ---- Full 28-series dataset (from finra_ids_star.py SERIES_IDS) ----
export const STAR_EXPORT_SERIES = [
  ["star-tba-par", "TBA Total — Par ($)"],
  ["star-tba-trades", "TBA Total — Trades"],
  ["star-tba-umbs-par", "TBA UMBS — Par ($)"],
  ["star-tba-fnma-par", "TBA FNMA — Par ($)"],
  ["star-tba-fhlmc-par", "TBA FHLMC — Par ($)"],
  ["star-tba-gnma-par", "TBA GNMA — Par ($)"],
  ["star-spec-par", "Specified Pools — Par ($)"],
  ["star-spec-trades", "Specified Pools — Trades"],
  ["star-agcmo-par", "Agency CMO — Par ($)"],
  ["star-agcmo-trades", "Agency CMO — Trades"],
  ["star-nagcmo-ig-par", "Non-Agency CMO IG — Par ($)"],
  ["star-nagcmo-nonig-par", "Non-Agency CMO HY — Par ($)"],
  ["star-nagcmo-par", "Non-Agency CMO Total — Par ($)"],
  ["star-nagcmo-trades", "Non-Agency CMO Total — Trades"],
  ["star-nagcmbs-ig-par", "Non-Agency CMBS IG — Par ($)"],
  ["star-nagcmbs-nonig-par", "Non-Agency CMBS HY — Par ($)"],
  ["star-nagcmbs-par", "Non-Agency CMBS Total — Par ($)"],
  ["star-nagcmbs-trades", "Non-Agency CMBS Total — Trades"],
  ["star-agcmbs-par", "Agency CMBS — Par ($)"],
  ["star-agcmbs-trades", "Agency CMBS — Trades"],
  ["star-abs-ig-par", "ABS IG — Par ($)"],
  ["star-abs-nonig-par", "ABS HY — Par ($)"],
  ["star-abs-par", "ABS Total — Par ($)"],
  ["star-abs-trades", "ABS Total — Trades"],
  ["star-clo-ig-par", "CLO IG — Par ($)"],
  ["star-clo-nonig-par", "CLO HY — Par ($)"],
  ["star-clo-par", "CLO Total — Par ($)"],
  ["star-clo-trades", "CLO Total — Trades"],
  // Sub-breakdowns (maturity / P&I vs IO/PO)
  ["star-tba-15y-par", "TBA 15Y — Par ($)"],
  ["star-tba-30y-par", "TBA 30Y — Par ($)"],
  ["star-tba-other-par", "TBA Other — Par ($)"],
  ["star-tba-other-agency-par", "TBA Other Agency — Par ($)"],
  ["star-spec-15y-par", "Specified 15Y — Par ($)"],
  ["star-spec-30y-par", "Specified 30Y — Par ($)"],
  ["star-spec-adj-par", "Specified Adj/Hybrid — Par ($)"],
  ["star-spec-other-par", "Specified Other — Par ($)"],
  ["star-agcmo-pi-par", "Agency CMO P&I — Par ($)"],
  ["star-agcmo-iopo-par", "Agency CMO IO/PO — Par ($)"],
  ["star-nagcmo-pi-par", "Non-Agency CMO P&I — Par ($)"],
  ["star-nagcmo-pi-ig-par", "Non-Agency CMO P&I IG — Par ($)"],
  ["star-nagcmo-pi-nonig-par", "Non-Agency CMO P&I HY — Par ($)"],
  ["star-nagcmo-iopo-par", "Non-Agency CMO IO/PO — Par ($)"],
  ["star-nagcmo-iopo-ig-par", "Non-Agency CMO IO/PO IG — Par ($)"],
  ["star-nagcmo-iopo-nonig-par", "Non-Agency CMO IO/PO HY — Par ($)"],
  ["star-nagcmbs-pi-par", "Non-Agency CMBS P&I — Par ($)"],
  ["star-nagcmbs-pi-ig-par", "Non-Agency CMBS P&I IG — Par ($)"],
  ["star-nagcmbs-pi-nonig-par", "Non-Agency CMBS P&I HY — Par ($)"],
  ["star-nagcmbs-iopo-par", "Non-Agency CMBS IO/PO — Par ($)"],
  ["star-nagcmbs-iopo-ig-par", "Non-Agency CMBS IO/PO IG — Par ($)"],
  ["star-nagcmbs-iopo-nonig-par", "Non-Agency CMBS IO/PO HY — Par ($)"],
  ["star-agcmbs-pi-par", "Agency CMBS P&I — Par ($)"],
  ["star-agcmbs-pi-ig-par", "Agency CMBS P&I IG — Par ($)"],
  ["star-agcmbs-pi-nonig-par", "Agency CMBS P&I HY — Par ($)"],
  ["star-agcmbs-iopo-par", "Agency CMBS IO/PO — Par ($)"],
  ["star-agcmbs-iopo-ig-par", "Agency CMBS IO/PO IG — Par ($)"],
  ["star-agcmbs-iopo-nonig-par", "Agency CMBS IO/PO HY — Par ($)"],
  // Unique security IDs (distinct CUSIPs traded)
  ["star-tba-secids", "TBA — Unique Sec IDs"],
  ["star-spec-secids", "Specified — Unique Sec IDs"],
  ["star-agcmo-secids", "Agency CMO — Unique Sec IDs"],
  ["star-nagcmo-secids", "Non-Agency CMO — Unique Sec IDs"],
  ["star-nagcmbs-secids", "Non-Agency CMBS — Unique Sec IDs"],
  ["star-agcmbs-secids", "Agency CMBS — Unique Sec IDs"],
  ["star-abs-secids", "ABS — Unique Sec IDs"],
  ["star-clo-secids", "CLO — Unique Sec IDs"],
];

// ---- Chart products (22): STAR Total + 21 granular breakdowns ----
const STAR_TOTAL_PARTS = [
  { par: "star-tba-par", trades: "star-tba-trades" },
  { par: "star-spec-par", trades: "star-spec-trades" },
  { par: "star-agcmo-par", trades: "star-agcmo-trades" },
  { par: "star-nagcmo-par", trades: "star-nagcmo-trades" },
  { par: "star-nagcmbs-par", trades: "star-nagcmbs-trades" },
  { par: "star-agcmbs-par", trades: "star-agcmbs-trades" },
  { par: "star-abs-par", trades: "star-abs-trades" },
  { par: "star-clo-par", trades: "star-clo-trades" },
];
export const STAR_PRODUCTS = [
  { id: "star-total", label: "STAR — Total", synthetic: true },
  { id: "star-tba", label: "STAR — TBA Total", par: "star-tba-par", trades: "star-tba-trades" },
  { id: "star-umbs", label: "STAR — TBA UMBS", par: "star-tba-umbs-par", trades: null },
  { id: "star-fnma", label: "STAR — TBA FNMA", par: "star-tba-fnma-par", trades: null },
  { id: "star-fhlmc", label: "STAR — TBA FHLMC", par: "star-tba-fhlmc-par", trades: null },
  { id: "star-gnma", label: "STAR — TBA GNMA", par: "star-tba-gnma-par", trades: null },
  { id: "star-spec", label: "STAR — Specified Pools", par: "star-spec-par", trades: "star-spec-trades" },
  { id: "star-agcmo", label: "STAR — Agency CMO", par: "star-agcmo-par", trades: "star-agcmo-trades" },
  { id: "star-nagcmo", label: "STAR — Non-Agency CMO", par: "star-nagcmo-par", trades: "star-nagcmo-trades" },
  { id: "star-nagcmo-ig", label: "STAR — Non-Agency CMO IG", par: "star-nagcmo-ig-par", trades: null },
  { id: "star-nagcmo-hy", label: "STAR — Non-Agency CMO HY", par: "star-nagcmo-nonig-par", trades: null },
  { id: "star-nagcmbs", label: "STAR — Non-Agency CMBS", par: "star-nagcmbs-par", trades: "star-nagcmbs-trades" },
  { id: "star-nagcmbs-ig", label: "STAR — Non-Agency CMBS IG", par: "star-nagcmbs-ig-par", trades: null },
  { id: "star-nagcmbs-hy", label: "STAR — Non-Agency CMBS HY", par: "star-nagcmbs-nonig-par", trades: null },
  { id: "star-agcmbs", label: "STAR — Agency CMBS", par: "star-agcmbs-par", trades: "star-agcmbs-trades" },
  { id: "star-abs", label: "STAR — ABS Total", par: "star-abs-par", trades: "star-abs-trades" },
  { id: "star-abs-ig", label: "STAR — ABS IG", par: "star-abs-ig-par", trades: null },
  { id: "star-abs-hy", label: "STAR — ABS HY", par: "star-abs-nonig-par", trades: null },
  { id: "star-clo", label: "STAR — CLO Total", par: "star-clo-par", trades: "star-clo-trades" },
  { id: "star-clo-ig", label: "STAR — CLO IG", par: "star-clo-ig-par", trades: null },
  { id: "star-clo-hy", label: "STAR — CLO HY", par: "star-clo-nonig-par", trades: null },
  { id: "star-tba-15y", label: "STAR — TBA 15Y", par: "star-tba-15y-par", trades: "star-tba-15y-trades" },
  { id: "star-tba-30y", label: "STAR — TBA 30Y", par: "star-tba-30y-par", trades: "star-tba-30y-trades" },
  { id: "star-spec-15y", label: "STAR — Specified 15Y", par: "star-spec-15y-par", trades: "star-spec-15y-trades" },
  { id: "star-spec-30y", label: "STAR — Specified 30Y", par: "star-spec-30y-par", trades: "star-spec-30y-trades" },
  { id: "star-agcmo-pi", label: "STAR — Agency CMO P&I", par: "star-agcmo-pi-par", trades: "star-agcmo-pi-trades" },
  { id: "star-agcmo-iopo", label: "STAR — Agency CMO IO/PO", par: "star-agcmo-iopo-par", trades: "star-agcmo-iopo-trades" },
  { id: "star-nagcmo-pi", label: "STAR — Non-Agency CMO P&I", par: "star-nagcmo-pi-par", trades: "star-nagcmo-pi-trades" },
  { id: "star-nagcmo-iopo", label: "STAR — Non-Agency CMO IO/PO", par: "star-nagcmo-iopo-par", trades: "star-nagcmo-iopo-trades" },
  { id: "star-nagcmbs-pi", label: "STAR — Non-Agency CMBS P&I", par: "star-nagcmbs-pi-par", trades: "star-nagcmbs-pi-trades" },
  { id: "star-nagcmbs-iopo", label: "STAR — Non-Agency CMBS IO/PO", par: "star-nagcmbs-iopo-par", trades: "star-nagcmbs-iopo-trades" },
  { id: "star-agcmbs-pi", label: "STAR — Agency CMBS P&I", par: "star-agcmbs-pi-par", trades: "star-agcmbs-pi-trades" },
  { id: "star-agcmbs-iopo", label: "STAR — Agency CMBS IO/PO", par: "star-agcmbs-iopo-par", trades: "star-agcmbs-iopo-trades" },
];

const chartState = { product: "star-total", metric: "vol", range: "1y", customStart: null, customEnd: null, reqId: 0, plot: null };

// ---- Price series (PXTABLES, parsed by finra_ids_px.py) ----
// Curated avg/wtd-avg price series; full 1,696-series set in the export.
const PX_BASES = [
  ["tba", "30y-dec-umbs-5", "TBA 30Y UMBS 5s"],
  ["tba", "30y-dec-umbs-5_5", "TBA 30Y UMBS 5.5s"],
  ["tba", "30y-dec-umbs-6", "TBA 30Y UMBS 6s"],
  ["mbs", "30y-umbs-5", "Specified 30Y UMBS 5s"],
  ["mbs", "30y-gnma-5", "Specified 30Y GNMA 5s"],
  ["agcmo", "pi-fnma-post2016", "Agency CMO P&I FNMA post-2016"],
  ["agcmo", "pi-fhlmc-post2016", "Agency CMO P&I FHLMC post-2016"],
  ["nag", "ig-nonagency-cmo-pi", "Non-Ag CMO P&I IG"],
  ["nag", "ig-abs", "ABS IG"],
  ["cmbs", "agcmbs-agency-cmbs-pi", "Agency CMBS P&I"],
  ["cmbs", "agcmbs-agency-cmbs-pi-pre2021", "Agency CMBS P&I pre-2021"],
  ["cmbs", "agcmbs-agency-cmbs-pi-2021-2023", "Agency CMBS P&I 2021-23"],
  ["cmbs", "conduit-non-agency-cmbs-pi", "CMBS Conduit P&I"],
  ["cboclo", "cbo-cdo-clo", "CBO/CDO/CLO"],
  ["cboclo", "cbo-cdo-clo-2023-2026", "CBO/CDO/CLO 2023-26"],
  ["cboclo", "aaa-2023-2026", "CLO AAA 2023-26"],
  ["cboclo", "aaa-pre2023", "CLO AAA pre-2023"],
];
const PX_METRICS = [
  ["avgpx", "Avg price"], ["wavgpx", "Wtd avg price"],
  ["bot5", "Bottom-5 avg"], ["top5", "Top-5 avg"],
  ["q2", "2nd quartile"], ["q3", "3rd quartile"], ["q4", "4th quartile"],
  ["stdev", "Std dev"],
];
const pxState = { base: "tba|30y-dec-umbs-5", metric: "avgpx", range: "1y", customStart: null, customEnd: null, reqId: 0, plot: null };
const pxSeriesId = () => {
  const [sheet, base] = pxState.base.split("|");
  return `starpx-${sheet}-${base}-${pxState.metric}`;
};

function pxFilterRange(points) {
  if (pxState.range === "custom" && pxState.customStart && pxState.customEnd)
    return points.filter(([d]) => d >= pxState.customStart && d <= pxState.customEnd);
  if (pxState.range === "max") return points;
  const r = rangeById(pxState.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-Math.round(r.months * 21));
}

async function drawPxChart() {
  const reqId = ++pxState.reqId;
  const el = document.getElementById("star-px-chart");
  const statusEl = document.getElementById("star-px-status");
  if (!el) return;
  if (statusEl) statusEl.textContent = "Loading…";
  try {
    const sid = pxSeriesId();
    const s = await getSeries(sid, "max");
    if (reqId !== pxState.reqId) return;
    const pts = pxFilterRange(s.points ?? []).sort((a, b) => (a[0] < b[0] ? -1 : 1));
    if (pxState.plot) { pxState.plot.destroy(); pxState.plot = null; }
    el.innerHTML = "";
    const mlabel = (PX_METRICS.find((m) => m[0] === pxState.metric) || [])[1] || pxState.metric;
    const blabel = (PX_BASES.find((b) => `${b[0]}|${b[1]}` === pxState.base) || [])[2] || sid;
    if (typeof uPlot !== "undefined" && pts.length > 1) {
      const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
      pxState.plot = new uPlot({
        width: Math.max(300, el.clientWidth || 900),
        height: 340,
        series: [{}, { label: `${blabel} — ${mlabel}`, stroke: "#7c3aed", width: 1.5, spanGaps: true }],
        axes: [axisStyle, { ...axisStyle,
          values: (u, vs) => vs.map((v) => v == null ? "—" : v.toFixed(1)) }],
      }, [pts.map(([d]) => Date.parse(d) / 1000), pts.map(([, v]) => v)], el);
    }
    const rLbl = pxState.range === "custom" ? `${pxState.customStart}→${pxState.customEnd}` : (rangeById(pxState.range)?.label || pxState.range);
    if (statusEl) statusEl.textContent = pts.length
      ? `${blabel} — ${mlabel}: ${pts.length} daily points (${rLbl}) · FINRA-IDS PXTABLES`
      : `No data for ${sid} yet — PXTABLES backfill in progress.`;
  } catch (e) {
    if (statusEl) statusEl.textContent = `Chart unavailable — ${e.message}`;
  }
}

function syncPxControls() {
  const sel = document.getElementById("star-px-base");
  if (sel) sel.value = pxState.base;
  const msel = document.getElementById("star-px-metric");
  if (msel) msel.value = pxState.metric;
  document.querySelectorAll("#star-px-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === pxState.range));
  const customBox = document.getElementById("star-px-range-custom");
  if (customBox) customBox.style.display = pxState.range === "custom" ? "" : "none";
}

// ---- Full-dataset download ----
async function fetchStarMatrix() {
  const cols = await Promise.all(
    STAR_EXPORT_SERIES.map(async ([sid]) => {
      try {
        const s = await getSeries(sid, "max");
        return new Map((s.points ?? []).map(([d, v]) => [d, v]));
      } catch { return new Map(); }
    })
  );
  const dates = [...new Set(cols.flatMap((m) => [...m.keys()]))].sort();
  const rows = dates.map((d) => [d, ...cols.map((m) => {
    const v = m.get(d);
    return v == null ? "" : v;
  })]);
  return { headers: ["Date", ...STAR_EXPORT_SERIES.map(([, label]) => label)], rows, dates };
}

function downloadCSV() {
  const btn = document.getElementById("star-dl-csv");
  if (btn) btn.textContent = "Loading…";
  fetchStarMatrix().then(({ headers, rows, dates }) => {
    const csv = matrixToCSV(headers, rows);
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `star-daily-${todayStamp()}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    const st = document.getElementById("star-dl-status");
    if (st) st.textContent = `${dates.length} trading days × ${STAR_EXPORT_SERIES.length} series exported.`;
  }).catch((e) => {
    const st = document.getElementById("star-dl-status");
    if (st) st.textContent = `Export failed — ${e.message}`;
  }).finally(() => { if (btn) btn.textContent = "⤓ CSV (all series)"; });
}

function downloadXLSX() {
  const btn = document.getElementById("star-dl-xlsx");
  if (btn) btn.textContent = "Loading…";
  fetchStarMatrix().then(({ headers, rows, dates }) => {
    // exportTablesXLSX takes {name, table, meta}; build a minimal table-like
    // via a hidden DOM table so tableToMatrix handles it.
    const t = document.createElement("table");
    const thead = document.createElement("tr");
    headers.forEach((h) => { const th = document.createElement("th"); th.textContent = h; thead.appendChild(th); });
    t.appendChild(thead);
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      r.forEach((v) => { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); });
      t.appendChild(tr);
    });
    exportTablesXLSX(
      [{ name: "STAR daily", table: t, meta: { title: "STAR — Structured Trading Activity Reports (daily)" } }],
      `star-daily-${todayStamp()}`
    );
    const st = document.getElementById("star-dl-status");
    if (st) st.textContent = `${dates.length} trading days × ${STAR_EXPORT_SERIES.length} series exported.`;
  }).catch((e) => {
    const st = document.getElementById("star-dl-status");
    if (st) st.textContent = `Export failed — ${e.message}`;
  }).finally(() => { if (btn) btn.textContent = "⤓ XLSX (all series)"; });
}

// ---- Chart ----
function filterRange(points) {
  if (chartState.range === "custom" && chartState.customStart && chartState.customEnd)
    return points.filter(([d]) => d >= chartState.customStart && d <= chartState.customEnd);
  if (chartState.range === "max") return points;
  const r = rangeById(chartState.range);
  if (!r || !isFinite(r.months)) return points;
  // RANGES are in months; STAR is daily — approximate months as 21 trading days.
  return points.slice(-Math.round(r.months * 21));
}

async function loadStarSeries() {
  const p = STAR_PRODUCTS.find((x) => x.id === chartState.product);
  const isTrades = chartState.metric === "trades";
  if (p.synthetic) {
    const sums = new Map();
    await Promise.all(STAR_TOTAL_PARTS.map(async (c) => {
      const sid = isTrades ? c.trades : c.par;
      try {
        const s = await getSeries(sid, "max");
        for (const [d, v] of s.points ?? []) {
          if (v == null) continue;
          const e = sums.get(d);
          if (e) e.v += v; else sums.set(d, { d, v });
        }
      } catch { /* skip */ }
    }));
    const rows = [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
    return { name: `STAR Total — ${isTrades ? "trades/day" : "par ($/day)"}`, points: rows.map(({ d, v }) => [d, v]) };
  }
  const sid = isTrades ? p.trades : p.par;
  const s = await getSeries(sid, "max");
  return { name: `${p.label} — ${isTrades ? "trades/day" : "par ($/day)"}`, points: filterRange(s.points ?? []) };
}

async function drawStarChart() {
  const reqId = ++chartState.reqId;
  const chartDiv = document.getElementById("star-chart");
  const statusDiv = document.getElementById("star-chart-status");
  if (!chartDiv) return;
  const p = STAR_PRODUCTS.find((x) => x.id === chartState.product);
  if (statusDiv) statusDiv.textContent = "Loading…";
  try {
    const series = await loadStarSeries();
    if (reqId !== chartState.reqId) return;
    if (chartState.plot) { chartState.plot.destroy(); chartState.plot = null; }
    chartDiv.innerHTML = "";
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    const isTrades = chartState.metric === "trades";
    const fmtY = (u, vs) => vs.map((v) => {
      if (v == null) return "—";
      if (isTrades) return v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : `${(v / 1e3).toFixed(0)}k`;
      return v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;
    });
    chartState.plot = new uPlot({
      width: Math.max(300, chartDiv.clientWidth || 900),
      height: 380,
      series: [{}, { label: series.name, stroke: "#2563eb", width: 1.5, spanGaps: true }],
      axes: [axisStyle, { ...axisStyle, values: fmtY }],
    }, [
      series.points.map(([d]) => Date.parse(d) / 1000),
      series.points.map(([, v]) => v),
    ], chartDiv);
    const rLbl = chartState.range === "custom"
      ? `${chartState.customStart}→${chartState.customEnd}`
      : (rangeById(chartState.range)?.label || chartState.range);
    if (statusDiv) statusDiv.textContent = `${series.points.length} daily points (${rLbl}) · FINRA-IDS STAR`;
  } catch (err) {
    if (reqId !== chartState.reqId) return;
    if (chartState.plot) { chartState.plot.destroy(); chartState.plot = null; }
    chartDiv.innerHTML = "";
    if (statusDiv) statusDiv.textContent = `Failed to load — ${err.message}`;
  }
}

function syncStarControls() {
  const p = STAR_PRODUCTS.find((x) => x.id === chartState.product);
  const sel = document.getElementById("star-prod");
  if (sel) sel.value = chartState.product;
  const isTrades = chartState.metric === "trades";
  document.querySelectorAll("#star-metric button").forEach((b) => {
    const mid = b.dataset.metric;
    const ok = mid === "vol" || !!p.trades;
    b.disabled = !ok;
    b.title = ok ? (mid === "vol" ? "Par volume ($/day)" : "Trade count (trades/day)")
                 : "Trade counts not published for this breakdown";
    b.classList.toggle("on", chartState.metric === mid);
  });
  document.querySelectorAll("#star-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === chartState.range));
  const customBox = document.getElementById("star-range-custom");
  if (customBox) customBox.style.display = chartState.range === "custom" ? "" : "none";
}

// ---- Delta table (moved from the FINRA panel) ----
const STAR_ROWS = [
  ["star-tba-par", "star-tba-trades", "TBA (all issuers)", "star-tba"],
  ["star-tba-umbs-par", null, "TBA — UMBS", "star-umbs"],
  ["star-tba-fnma-par", null, "TBA — FNMA", "star-fnma"],
  ["star-tba-fhlmc-par", null, "TBA — FHLMC", "star-fhlmc"],
  ["star-tba-gnma-par", null, "TBA — GNMA", "star-gnma"],
  ["star-spec-par", "star-spec-trades", "Specified pools", "star-spec"],
  ["star-agcmo-par", "star-agcmo-trades", "Agency CMO", "star-agcmo"],
  ["star-nagcmo-par", "star-nagcmo-trades", "Non-agency CMO", "star-nagcmo"],
  ["star-nagcmo-ig-par", null, "Non-agency CMO — IG", "star-nagcmo-ig"],
  ["star-nagcmo-nonig-par", null, "Non-agency CMO — HY", "star-nagcmo-hy"],
  ["star-nagcmbs-par", "star-nagcmbs-trades", "Non-agency CMBS", "star-nagcmbs"],
  ["star-nagcmbs-ig-par", null, "Non-agency CMBS — IG", "star-nagcmbs-ig"],
  ["star-nagcmbs-nonig-par", null, "Non-agency CMBS — HY", "star-nagcmbs-hy"],
  ["star-agcmbs-par", "star-agcmbs-trades", "Agency CMBS", "star-agcmbs"],
  ["star-abs-par", "star-abs-trades", "ABS", "star-abs"],
  ["star-abs-ig-par", null, "ABS — IG", "star-abs-ig"],
  ["star-abs-nonig-par", null, "ABS — HY", "star-abs-hy"],
  ["star-clo-par", "star-clo-trades", "CLO", "star-clo"],
  ["star-clo-ig-par", null, "CLO — IG", "star-clo-ig"],
  ["star-clo-nonig-par", null, "CLO — HY", "star-clo-hy"],
  ["star-tba-15y-par", "star-tba-15y-trades", "TBA — 15Y", "star-tba-15y"],
  ["star-tba-30y-par", "star-tba-30y-trades", "TBA — 30Y", "star-tba-30y"],
  ["star-agcmo-pi-par", "star-agcmo-pi-trades", "Agency CMO — P&I", "star-agcmo-pi"],
  ["star-agcmo-iopo-par", "star-agcmo-iopo-trades", "Agency CMO — IO/PO", "star-agcmo-iopo"],
  ["star-nagcmo-pi-par", "star-nagcmo-pi-trades", "Non-Ag CMO — P&I", "star-nagcmo-pi"],
  ["star-nagcmo-iopo-par", "star-nagcmo-iopo-trades", "Non-Ag CMO — IO/PO", "star-nagcmo-iopo"],
  ["star-agcmbs-pi-par", "star-agcmbs-pi-trades", "Agency CMBS — P&I", "star-agcmbs-pi"],
];
const starDeltaCell = (nom, pct, fmtNom) => {
  if ((nom == null || !isFinite(nom)) && (pct == null || !isFinite(pct))) return "—";
  const n = fmtNom(nom);
  const p = pct1(pct);
  if (n === "—") return p;
  if (p === "—") return n;
  return `${n} <span class="muted">(${p})</span>`;
};
const fmtNomB$ = (v) => v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}$${Math.abs(v) >= 1e9 ? (Math.abs(v) / 1e9).toFixed(2) + "B" : (Math.abs(v) / 1e6).toFixed(1) + "M"}`;
const fmtNomCt = (v) => v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}${Math.round(Math.abs(v)).toLocaleString("en-US")}`;
const starDayDiff = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
function starDeltas(pts) {
  if (!pts || pts.length < 2) return null;
  const cur = pts[pts.length - 1];
  const refBack = (days) => {
    for (let i = pts.length - 2; i >= 0; i--) {
      if (starDayDiff(pts[i].d, cur.d) >= days) return pts[i];
    }
    return null;
  };
  const out = {};
  for (const [k, days] of [["d1", 1], ["w1", 7], ["m1", 30], ["q1", 91], ["y1", 365], ["y3", 1095]]) {
    const ref = refBack(days);
    if (!ref || !ref.v) { out[k] = { nom: null, pct: null }; continue; }
    out[k] = { nom: cur.v - ref.v, pct: (cur.v - ref.v) / ref.v };
  }
  return { cur: cur.v, asof: cur.d, ...out };
}

async function fillStarTable() {
  const tbody = document.getElementById("star-tbody");
  if (!tbody) return;
  const toggle = document.getElementById("star-metric-toggle");
  let metric = "vol";
  const render = async () => {
    tbody.innerHTML = `<tr data-sort-row="off"><td colspan="10" class="muted">Loading daily history…</td></tr>`;
    const rows = await Promise.all(STAR_ROWS.map(async ([parId, trId, label, chartId]) => {
      const sid = metric === "vol" ? parId : trId;
      if (!sid) return null;
      try {
        const s = await getSeries(sid, "max");
        const pts = (s.points ?? []).map((p) => ({ d: p[0], v: p[1] })).sort((a, b) => a.d < b.d ? -1 : 1);
        const st = starDeltas(pts);
        if (!st) return `<tr data-sort-row="off"><td><b>${esc(label)}</b></td><td colspan="9" class="muted">no history</td></tr>`;
        const fmtNom = metric === "vol" ? fmtNomB$ : fmtNomCt;
        const fmtVal = metric === "vol"
          ? (v) => "$" + (v / 1e9).toFixed(2) + "B"
          : (v) => Math.round(v).toLocaleString("en-US");
        const stats = statsFromValues(pts.map((p) => p.v));
        const dc = (k) => `<td class="num"${heatStyle({ pct: st[k].pct })}>${starDeltaCell(st[k].nom, st[k].pct, fmtNom)}</td>`;
        return `<tr data-star-chart="${chartId}" title="Click to chart ${esc(label)}">` +
          `<td><b>${esc(label)}</b></td><td class="num"><b>${fmtVal(st.cur)}</b> <span class="muted">${st.asof}</span></td>` +
          dc("d1") + dc("w1") + dc("m1") + dc("q1") + dc("y1") + dc("y3") +
          rangeCells(stats, "full history") + `</tr>`;
      } catch { return `<tr data-sort-row="off"><td><b>${esc(label)}</b></td><td colspan="9" class="muted">load failed</td></tr>`; }
    }));
    tbody.innerHTML = rows.filter(Boolean).join("");
    tbody.querySelectorAll("tr[data-star-chart]").forEach((tr) => {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", () => {
        const sel = document.getElementById("star-prod");
        if (sel) { sel.value = tr.dataset.starChart; }
        chartState.product = tr.dataset.starChart;
        syncStarControls();
        drawStarChart();
        document.getElementById("star-chart-wrap")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  };
  if (toggle) {
    toggle.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.dataset.m === metric) return;
        metric = b.dataset.m;
        toggle.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        render();
      });
    });
  }
  await render();
}

export function renderStar(p) {
  const body = document.querySelector("#panel-star .panel-body");
  if (!body || body.dataset.init) return;
  body.dataset.init = "1";
  const f = p ?? {};
  const s = f.star ?? {};
  const prodOpts = STAR_PRODUCTS.map((x) => `<option value="${x.id}">${esc(x.label)}</option>`).join("");
  const rangeBtns = RANGES.map((r) => `<button data-range="${r.id}" class="${r.id === chartState.range ? "on" : ""}">${r.label}</button>`).join("");
  body.innerHTML =
    `<h3>STRUCTURED PRODUCT ACTIVITY — STAR <span class="muted">daily · as of ${esc(s.as_of ?? "—")} · ${s.days ?? "—"} trading days</span></h3>
    <div class="star-dl-row">
      <button id="star-dl-csv" class="mini-btn">⤓ CSV (all series)</button>
      <button id="star-dl-xlsx" class="mini-btn">⤓ XLSX (all series)</button>
      <span id="star-dl-status" class="muted"></span>
    </div>
    <p class="muted">FINRA-ICE Data Services Structured Trading Activity Reports — the public equivalent of the ` +
    `login-walled ICE Vantage structured aggregates. Daily TBA/specified/CMO/CMBS/ABS/CLO activity by issuer and ` +
    `investment grade. Downloads carry the full 28-series history (par in $, trades in counts).</p>
    <h3>STAR CHART <span class="muted">choose product, stat and dates</span></h3>
    <div id="star-chart-wrap">
      <div class="trace-controls">
        <label>Product <select id="star-prod">${prodOpts}</select></label>
        <span class="seg" id="star-metric">
          <button data-metric="vol" class="on" title="Par volume ($/day)">$ Volume</button><button data-metric="trades" title="Trade count (trades/day)">Trades</button>
        </span>
        <span class="seg" id="star-range">${rangeBtns}</span>
        <span id="star-range-custom" class="muted" style="display:none">
          <input type="date" id="star-chart-start" aria-label="Start date"> →
          <input type="date" id="star-chart-end" aria-label="End date">
          <button id="star-chart-apply" class="mini-btn">Apply</button>
        </span>
      </div>
      <div id="star-chart" class="trace-chart star-chart"></div>
      <div id="star-chart-status" class="muted"></div>
    </div>
    <h3>STAR PRICE CHART <span class="muted">PXTABLES — avg/quartile prices by coupon & vintage</span></h3>
    <div id="star-px-wrap">
      <div class="trace-controls">
        <label>Product <select id="star-px-base"></select></label>
        <label>Metric <select id="star-px-metric"></select></label>
        <span class="seg" id="star-px-range"></span>
        <span id="star-px-range-custom" class="muted" style="display:none">
          <input type="date" id="star-px-start" aria-label="Start date"> →
          <input type="date" id="star-px-end" aria-label="End date">
          <button id="star-px-apply" class="mini-btn">Apply</button>
        </span>
      </div>
      <div id="star-px-chart" class="trace-chart star-chart"></div>
      <div id="star-px-status" class="muted"></div>
    </div>
    <h3>STAR TABLE <span class="muted">click a row for its chart</span></h3>
    <div class="seg" id="star-metric-toggle" role="tablist"><button data-m="vol" class="on">$ Volume</button><button data-m="trades">Trades</button></div>
    <div>${HEAT_LEGEND}</div>
    <table class="star-table" data-sortable><thead><tr><th>Product</th><th>Latest</th><th>1D Δ</th><th>1W Δ</th><th>1M Δ</th><th>1Q Δ</th><th>1Y Δ</th><th>3Y Δ</th>${RANGE_TH}</tr></thead>
    <tbody id="star-tbody"><tr data-sort-row="off"><td colspan="10" class="muted">Loading daily history…</td></tr></tbody></table>`;

  document.getElementById("star-dl-csv").addEventListener("click", downloadCSV);
  document.getElementById("star-dl-xlsx").addEventListener("click", downloadXLSX);
  document.getElementById("star-prod").addEventListener("change", (e) => {
    chartState.product = e.target.value;
    // If the new product has no trade counts, fall back to $ volume.
    const pr = STAR_PRODUCTS.find((x) => x.id === chartState.product);
    if (chartState.metric === "trades" && !pr.trades) chartState.metric = "vol";
    syncStarControls();
    drawStarChart();
  });
  body.querySelectorAll("#star-metric button").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.disabled || chartState.metric === b.dataset.metric) return;
      chartState.metric = b.dataset.metric;
      syncStarControls();
      drawStarChart();
    }));
  body.querySelectorAll("#star-range button").forEach((b) =>
    b.addEventListener("click", () => {
      chartState.range = b.dataset.range;
      syncStarControls();
      if (chartState.range !== "custom") drawStarChart();
    }));
  document.getElementById("star-chart-apply").addEventListener("click", () => {
    const st = document.getElementById("star-chart-start").value;
    const en = document.getElementById("star-chart-end").value;
    if (!st || !en || st > en) return;
    chartState.customStart = st;
    chartState.customEnd = en;
    chartState.range = "custom";
    syncStarControls();
    drawStarChart();
  });

  const pxBase = document.getElementById("star-px-base");
  pxBase.innerHTML = PX_BASES.map(([sh, b, label]) =>
    `<option value="${sh}|${b}">${esc(label)}</option>`).join("");
  pxBase.value = pxState.base;
  const pxMetric = document.getElementById("star-px-metric");
  pxMetric.innerHTML = PX_METRICS.map(([id, label]) =>
    `<option value="${id}">${esc(label)}</option>`).join("");
  pxMetric.value = pxState.metric;
  document.getElementById("star-px-range").innerHTML = RANGES.map((r) =>
    `<button data-range="${r.id}" class="${r.id === pxState.range ? "on" : ""}">${r.label}</button>`).join("");
  pxBase.addEventListener("change", (e) => { pxState.base = e.target.value; drawPxChart(); });
  pxMetric.addEventListener("change", (e) => { pxState.metric = e.target.value; drawPxChart(); });
  body.querySelectorAll("#star-px-range button").forEach((b) =>
    b.addEventListener("click", () => {
      pxState.range = b.dataset.range;
      syncPxControls();
      if (pxState.range !== "custom") drawPxChart();
    }));
  document.getElementById("star-px-apply").addEventListener("click", () => {
    const st = document.getElementById("star-px-start").value;
    const en = document.getElementById("star-px-end").value;
    if (!st || !en || st > en) return;
    pxState.customStart = st; pxState.customEnd = en; pxState.range = "custom";
    syncPxControls();
    drawPxChart();
  });

  syncStarControls();
  drawStarChart();
  syncPxControls();
  drawPxChart();
  fillStarTable().catch(() => {});
}
