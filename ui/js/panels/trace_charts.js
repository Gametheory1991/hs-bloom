// TRACE volume charts with overlay capability.
// Inline uPlot chart (not the modal) with product/metric/range/overlay selectors.
// Mounted by renderFinra() into #trace-charts-root.
import { getSeries, getRecessions } from "../api.js";
import { toMetric, METRICS, RANGES, rangeById, metricById, capTotalVals } from "./trace_grid.js";

// TRACE monthly products. `trades` is null where FINRA only publishes par.
// NOTE: /api/series takes bare ids (no cycle: prefix) — the backend prepends it.
const PRODUCTS = [
  { id: "total", label: "TOTAL (Treasury + TRACE)", synthetic: true },
  { id: "ust", label: "Treasury Total", par: "trace-ust-par", trades: "trace-ust-trades", monthly: false },
  { id: "ust-bills", label: "Treasury — Bills", par: "trace-ust-bills-par", trades: null, monthly: false },
  { id: "ust-coupons", label: "Treasury — Nom Coupons", par: "trace-ust-coupons-par", trades: null, monthly: false },
  { id: "ust-tips", label: "Treasury — TIPS", par: "trace-ust-tips-par", trades: null, monthly: false },
  { id: "ust-frns", label: "Treasury — FRNs", par: "trace-ust-frns-par", trades: null, monthly: false },
  { id: "tba", label: "TBA", par: "trace-tba-par", trades: null, monthly: true },
  { id: "corp", label: "Corporate", par: "trace-corp-par", trades: "trace-corp-trades", monthly: true },
  { id: "mbs", label: "MBS (Spec Pools)", par: "trace-mbs-par", trades: null, monthly: true },
  { id: "cmo", label: "CMO", par: "trace-cmo-par", trades: null, monthly: true },
  { id: "absx", label: "ABSX (CLO/CMBS)", par: "trace-absx-par", trades: null, monthly: true },
  { id: "agcy", label: "Agency", par: "trace-agcy-par", trades: null, monthly: true },
  { id: "conv", label: "Convertibles", par: "trace-conv-par", trades: "trace-conv-trades", monthly: true },
  { id: "abs", label: "ABS", par: "trace-abs-par", trades: null, monthly: true },
  { id: "eln", label: "ELN", par: "trace-eln-par", trades: "trace-eln-trades", monthly: true },
  { id: "chrc", label: "Church Plans", par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true },
  { id: "onrun", label: "Treasury On-the-Run", par: "trace-ust-onrun-par", trades: null, monthly: false },
  { id: "offrun", label: "Treasury Off-the-Run", par: "trace-ust-offrun-par", trades: null, monthly: false },
  // STAR: FINRA IDS Structured Trading Activity Reports (daily)
  { id: "star-total", label: "STAR — Total", synthetic: true, star: true },
  { id: "star-tba", label: "STAR — TBA Total", par: "star-tba-par", trades: "star-tba-trades", monthly: false, daily: true },
  { id: "star-tba-umbs", label: "STAR — TBA UMBS", par: "star-tba-umbs-par", trades: null, monthly: false, daily: true },
  { id: "star-tba-fnma", label: "STAR — TBA FNMA", par: "star-tba-fnma-par", trades: null, monthly: false, daily: true },
  { id: "star-tba-fhlmc", label: "STAR — TBA FHLMC", par: "star-tba-fhlmc-par", trades: null, monthly: false, daily: true },
  { id: "star-tba-gnma", label: "STAR — TBA GNMA", par: "star-tba-gnma-par", trades: null, monthly: false, daily: true },
  { id: "star-spec", label: "STAR — Specified Pools", par: "star-spec-par", trades: "star-spec-trades", monthly: false, daily: true },
  { id: "star-agcmo", label: "STAR — Agency CMO", par: "star-agcmo-par", trades: "star-agcmo-trades", monthly: false, daily: true },
  { id: "star-nagcmo", label: "STAR — Non-Agency CMO", par: "star-nagcmo-par", trades: "star-nagcmo-trades", monthly: false, daily: true },
  { id: "star-nagcmo-ig", label: "STAR — Non-Agency CMO IG", par: "star-nagcmo-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-nagcmo-hy", label: "STAR — Non-Agency CMO HY", par: "star-nagcmo-nonig-par", trades: null, monthly: false, daily: true },
  { id: "star-nagcmbs", label: "STAR — Non-Agency CMBS", par: "star-nagcmbs-par", trades: "star-nagcmbs-trades", monthly: false, daily: true },
  { id: "star-nagcmbs-ig", label: "STAR — Non-Agency CMBS IG", par: "star-nagcmbs-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-nagcmbs-hy", label: "STAR — Non-Agency CMBS HY", par: "star-nagcmbs-nonig-par", trades: null, monthly: false, daily: true },
  { id: "star-agcmbs", label: "STAR — Agency CMBS", par: "star-agcmbs-par", trades: "star-agcmbs-trades", monthly: false, daily: true },
  { id: "star-abs", label: "STAR — ABS Total", par: "star-abs-par", trades: "star-abs-trades", monthly: false, daily: true },
  { id: "star-abs-ig", label: "STAR — ABS IG", par: "star-abs-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-abs-hy", label: "STAR — ABS HY", par: "star-abs-nonig-par", trades: null, monthly: false, daily: true },
  { id: "star-clo", label: "STAR — CLO Total", par: "star-clo-par", trades: "star-clo-trades", monthly: false, daily: true },
  { id: "star-clo-ig", label: "STAR — CLO IG", par: "star-clo-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-clo-hy", label: "STAR — CLO HY", par: "star-clo-nonig-par", trades: null, monthly: false, daily: true },
  { id: "si-total", label: "Short Interest — Total", par: "finra-short-total", trades: null, unit: "shares" },
  { id: "si-msft", label: "Short Interest — MSFT", par: "short-MSFT", trades: null, unit: "shares" },
  { id: "si-nvda", label: "Short Interest — NVDA", par: "short-NVDA", trades: null, unit: "shares" },
  { id: "si-aapl", label: "Short Interest — AAPL", par: "short-AAPL", trades: null, unit: "shares" },
  { id: "si-amzn", label: "Short Interest — AMZN", par: "short-AMZN", trades: null, unit: "shares" },
  { id: "si-googl", label: "Short Interest — GOOGL", par: "short-GOOGL", trades: null, unit: "shares" },
  { id: "si-meta", label: "Short Interest — META", par: "short-META", trades: null, unit: "shares" },
  // Market breadth (FINRA, daily)
  { id: "br-corp-all-spr", label: "Breadth — Corp All A/D Spread", par: "finra-breadth-corp-all-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-corp-ig-spr", label: "Breadth — Corp IG A/D Spread", par: "finra-breadth-corp-ig-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-corp-hy-spr", label: "Breadth — Corp HY A/D Spread", par: "finra-breadth-corp-hy-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-agcy-all-spr", label: "Breadth — Agency All A/D Spread", par: "finra-breadth-agency-all-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-144a-all-spr", label: "Breadth — 144A All A/D Spread", par: "finra-breadth-144a-all-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-144a-ig-spr", label: "Breadth — 144A IG A/D Spread", par: "finra-breadth-144a-ig-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-144a-hy-spr", label: "Breadth — 144A HY A/D Spread", par: "finra-breadth-144a-hy-adspread", trades: null, unit: "ct", daily: true },
  // Market sentiment (FINRA, daily)
  { id: "se-corp-all-flow", label: "Sentiment — Corp All Net Flow", par: "finra-sent-corp-all-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-corp-ig-flow", label: "Sentiment — Corp IG Net Flow", par: "finra-sent-corp-ig-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-corp-hy-flow", label: "Sentiment — Corp HY Net Flow", par: "finra-sent-corp-hy-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-agcy-all-flow", label: "Sentiment — Agency All Net Flow", par: "finra-sent-agency-all-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-144a-all-flow", label: "Sentiment — 144A All Net Flow", par: "finra-sent-144a-all-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-144a-ig-flow", label: "Sentiment — 144A IG Net Flow", par: "finra-sent-144a-ig-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-144a-hy-flow", label: "Sentiment — 144A HY Net Flow", par: "finra-sent-144a-hy-netflow", trades: null, unit: "$M", daily: true },
  // Short interest top tickers (Reg SHO daily; ADT/TRADES → short ratio)
  { id: "sit-msft", label: "Short — MSFT", par: "regsho-top-MSFT-shortvol", trades: "regsho-top-MSFT-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-nvda", label: "Short — NVDA", par: "regsho-top-NVDA-shortvol", trades: "regsho-top-NVDA-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-aapl", label: "Short — AAPL", par: "regsho-top-AAPL-shortvol", trades: "regsho-top-AAPL-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-amzn", label: "Short — AMZN", par: "regsho-top-AMZN-shortvol", trades: "regsho-top-AMZN-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-tsla", label: "Short — TSLA", par: "regsho-top-TSLA-shortvol", trades: "regsho-top-TSLA-totalvol", unit: "sh", daily: true, siTop: true },
  // Capped volume report (FINRA, monthly). Total capped par by grade;
  // ADT/TRADES shows avg capped trade size ($000s, nodiv). 144A grades
  // publish total par only.
  { id: "cap-total", label: "Capped — Total", synthetic: true, capped: true },
  { id: "cap-ig", label: "Capped — Investment Grade", par: "finra-cap-ig-total", trades: "finra-cap-ig-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-hy", label: "Capped — High Yield", par: "finra-cap-hy-total", trades: "finra-cap-hy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-agcy", label: "Capped — Agency", par: "finra-cap-agcy-total", trades: "finra-cap-agcy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-144a-ig", label: "Capped — 144A IG", par: "finra-cap-144a-ig-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
  { id: "cap-144a-hy", label: "Capped — 144A HY", par: "finra-cap-144a-hy-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
];

// Components summed into the synthetic TOTAL chart product (same set as the grid).
const TOTAL_PARTS = [
  { par: "trace-ust-par", trades: "trace-ust-trades", monthly: false },
  { par: "trace-tba-par", trades: null, monthly: true },
  { par: "trace-corp-par", trades: "trace-corp-trades", monthly: true },
  { par: "trace-eln-par", trades: "trace-eln-trades", monthly: true },
  { par: "trace-conv-par", trades: "trace-conv-trades", monthly: true },
  { par: "trace-agcy-par", trades: null, monthly: true },
  { par: "trace-abs-par", trades: null, monthly: true },
  { par: "trace-absx-par", trades: null, monthly: true },
  { par: "trace-cmo-par", trades: null, monthly: true },
  { par: "trace-mbs-par", trades: null, monthly: true },
  { par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true },
];

// STAR Total components: 8 top-level categories (daily). Issuer/grade
// breakdowns excluded to avoid double-counting.
const STAR_TOTAL_PARTS = [
  { par: "star-tba-par", trades: "star-tba-trades", monthly: false, daily: true },
  { par: "star-spec-par", trades: "star-spec-trades", monthly: false, daily: true },
  { par: "star-agcmo-par", trades: "star-agcmo-trades", monthly: false, daily: true },
  { par: "star-nagcmo-par", trades: "star-nagcmo-trades", monthly: false, daily: true },
  { par: "star-nagcmbs-par", trades: "star-nagcmbs-trades", monthly: false, daily: true },
  { par: "star-agcmbs-par", trades: "star-agcmbs-trades", monthly: false, daily: true },
  { par: "star-abs-par", trades: "star-abs-trades", monthly: false, daily: true },
  { par: "star-clo-par", trades: "star-clo-trades", monthly: false, daily: true },
];

// Synthetic TOTAL series: monthly values summed over Treasury + all 10
// TRACE products, by calendar month, normalized to the selected metric.
async function totalSeries(metric) {
  const isCount = metric === "adt" || metric === "trades";
  const all = (await Promise.all(TOTAL_PARTS.map(async (c) => {
    const sid = isCount ? c.trades : c.par;
    if (!sid) return null;
    const s = await getSeries(sid, "max");
    return toMetric(c, s.points, metric);
  }))).filter(Boolean);
  const sums = new Map(); // "YYYY-MM" -> {d, v}
  for (const vals of all)
    for (const { d, v } of vals) {
      const key = d.slice(0, 7);
      const e = sums.get(key);
      if (e) { e.v += v; if (d > e.d) e.d = d; }
      else sums.set(key, { d, v });
    }
  const rows = [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  const mu = metricById(metric);
  return {
    id: "total", unit: mu.unit,
    name: `TOTAL ${mu.label} — Treasury + TRACE (${mu.unit})`,
    points: rows.map(({ d, v }) => [d, v]),
  };
}

// Synthetic STAR Total series: daily values summed over the 8 top-level
// STAR categories, normalized to the selected metric.
async function starTotalSeries(metric) {
  const isCount = metric === "adt" || metric === "trades";
  const all = (await Promise.all(STAR_TOTAL_PARTS.map(async (c) => {
    const sid = isCount ? c.trades : c.par;
    if (!sid) return null;
    const s = await getSeries(sid, "max");
    return toMetric(c, s.points, metric);
  }))).filter(Boolean);
  const sums = new Map(); // "YYYY-MM-DD" -> {d, v} (daily granularity)
  for (const vals of all)
    for (const { d, v } of vals) {
      const e = sums.get(d);
      if (e) e.v += v;
      else sums.set(d, { d, v });
    }
  const rows = [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  const mu = metricById(metric);
  return {
    id: "star-total", unit: mu.unit,
    name: `STAR Total ${mu.label} — structured products (${mu.unit})`,
    points: rows.map(({ d, v }) => [d, v]),
  };
}

// Capped Total components: the 5 grades (monthly). Not part of the main
// TOTAL — capped volume is large-trade activity already in TRACE volumes.
const CAP_TOTAL_PARTS = [
  { id: "cap-ig", par: "finra-cap-ig-total", trades: "finra-cap-ig-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-hy", par: "finra-cap-hy-total", trades: "finra-cap-hy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-agcy", par: "finra-cap-agcy-total", trades: "finra-cap-agcy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-144a-ig", par: "finra-cap-144a-ig-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
  { id: "cap-144a-hy", par: "finra-cap-144a-hy-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
];

// Synthetic Capped Total series. ADV/PAR: sum of grades' total par by month.
// ADT/TRADES: par-weighted avg capped trade size (reuses capTotalVals from
// the grid so chart and grid always agree).
async function capTotalSeries(metric) {
  const isCount = metric === "adt" || metric === "trades";
  const data = await Promise.all(CAP_TOTAL_PARTS.map(async (c) => {
    const [par, tr] = await Promise.all([
      getSeries(c.par, "max"),
      c.trades ? getSeries(c.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    return { p: c, par: par.points, tr: tr ? tr.points : null };
  }));
  const rows = capTotalVals(data, metric, CAP_TOTAL_PARTS);
  if (isCount) {
    return {
      id: "cap-total", unit: "$000s",
      name: "Capped — Total — Avg capped trade size ($000s, par-weighted)",
      points: (rows || []).map(({ d, v }) => [d, v]),
    };
  }
  const mu = metricById(metric);
  return {
    id: "cap-total", unit: mu.unit,
    name: `Capped — Total ${mu.label} — capped par (${mu.unit})`,
    points: (rows || []).map(({ d, v }) => [d, v]),
  };
}

const OVERLAYS = [
  { id: "", label: "No overlay" },
  { id: "us10y", label: "US 10Y Yield (FRED)" },
  { id: "us-mortgage-30y", label: "30Y Mortgage Rate (FRED)" },
  { id: "ig-oas", label: "IG OAS" },
  { id: "hy-oas", label: "HY OAS" },
  { id: "tlt", label: "TLT (20Y+ Treasury ETF)" },
  { id: "lqd", label: "LQD (IG ETF)" },
  { id: "hyg", label: "HYG (HY ETF)" },
  { id: "mbb-us", label: "MBB (MBS ETF)" },
  { id: "vix", label: "VIX" },
  { id: "vvix", label: "VVIX" },
];

const state = { product: "total", metric: "adv", range: "3y", customStart: null, customEnd: null, overlay: "", plot: null, reqId: 0 };
let recessionsPromise = null;

function loadRecessions() {
  recessionsPromise ??= getRecessions()
    .then((r) => r.bands.map(([a, b]) => [Date.parse(a) / 1000, Date.parse(b) / 1000]))
    .catch(() => []);
  return recessionsPromise;
}

// Merge two [date, value] series onto one x-axis (union of dates, null-filled).
function mergeSeries(main, overlay) {
  const byDate = new Map();
  for (const [d, v] of main.points) byDate.set(d, [v, null]);
  for (const [d, v] of overlay.points) {
    const cur = byDate.get(d) ?? [null, null];
    cur[1] = v;
    byDate.set(d, cur);
  }
  const dates = [...byDate.keys()].sort();
  return [
    dates.map((d) => Date.parse(d) / 1000),
    dates.map((d) => byDate.get(d)[0]),
    dates.map((d) => byDate.get(d)[1]),
  ];
}

function bandsHook(bands) {
  return (u) => {
    const ctx = u.ctx;
    ctx.save();
    ctx.fillStyle = "rgba(214, 84, 84, 0.10)";
    for (const [a, b] of bands) {
      const x0 = Math.max(u.valToPos(a, "x", true), u.bbox.left);
      const x1 = Math.min(u.valToPos(b, "x", true), u.bbox.left + u.bbox.width);
      if (x1 > x0) ctx.fillRect(x0, u.bbox.top, x1 - x0, u.bbox.height);
    }
    ctx.restore();
  };
}

function destroyPlot() {
  if (state.plot) {
    state.plot.destroy();
    state.plot = null;
  }
}

function currentSeriesId() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  const isCount = state.metric === "adt" || state.metric === "trades";
  return (isCount && p.trades ? p.trades : p.par);
}

function currentTitle() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  if (p.raw) return `${p.label} — short shares (biweekly)`;
  if (p.capped && (state.metric === "adt" || state.metric === "trades"))
    return `${p.label} — Avg capped trade size ($000s)`;
  const mu = metricById(state.metric);
  return `${p.label} — ${mu.label} (${mu.unit})`;
}

// Filter [d, v] points to the selected range (client-side; we fetch "max").
function filterRange(points) {
  if (state.range === "custom" && state.customStart && state.customEnd)
    return points.filter(([d]) => d >= state.customStart && d <= state.customEnd);
  if (state.range === "max") return points;
  const r = rangeById(state.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-r.months);
}

async function loadMain() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  if (p.id === "star-total") {
    const s = await starTotalSeries(state.metric);
    return { ...s, points: filterRange(s.points) };
  }
  if (p.id === "cap-total") {
    const s = await capTotalSeries(state.metric);
    return { ...s, points: filterRange(s.points) };
  }
  if (p.synthetic) {
    const s = await totalSeries(state.metric);
    return { ...s, points: filterRange(s.points) };
  }
  if (p.raw) { // short interest: biweekly levels, metric toggle n/a
    const s = await getSeries(p.par, "max");
    return { id: p.id, unit: "shares", name: `${p.label} — short shares`, points: filterRange(s.points) };
  }
  if (p.siTop) {
    // Short interest top tickers: ADV/PAR → short volume; ADT/TRADES → short ratio.
    const isCount = state.metric === "adt" || state.metric === "trades";
    const [sv, tv] = await Promise.all([
      getSeries(p.par, "max"),
      p.trades ? getSeries(p.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    let pts = sv.points;
    let unit = "sh", name = `${p.label} — short volume (sh)`;
    if (isCount && tv) {
      const tvByDate = new Map(tv.points.map(([d, v]) => [d, v]));
      pts = sv.points.map(([d, v]) => {
        const t = tvByDate.get(d);
        return t ? [d, v / t] : null;
      }).filter(Boolean);
      unit = "ratio"; name = `${p.label} — short ratio`;
    }
    return { id: p.id, unit, name, points: filterRange(pts) };
  }
  const s = await getSeries(currentSeriesId(), "max");
  const vals = toMetric(p, s.points, state.metric).map(({ d, v }) => [d, v]);
  const mu = metricById(state.metric);
  const name = (p.capped && (state.metric === "adt" || state.metric === "trades"))
    ? `${p.label} — Avg capped trade size ($000s)`
    : `${p.label} — ${mu.label} (${mu.unit})`;
  return { id: p.id, unit: mu.unit, name, points: filterRange(vals) };
}

async function drawChart() {
  const reqId = ++state.reqId;
  const chartDiv = document.getElementById("trace-chart");
  const statusDiv = document.getElementById("trace-chart-status");
  if (!chartDiv) return;
  const p = PRODUCTS.find((x) => x.id === state.product);
  statusDiv.textContent = "Loading…";
  try {
    const [series, secondRaw, bands] = await Promise.all([
      loadMain(),
      state.overlay ? getSeries(state.overlay, "max") : Promise.resolve(null),
      loadRecessions(),
    ]);
    if (reqId !== state.reqId) return; // superseded
    const second = secondRaw ? { ...secondRaw, points: filterRange(secondRaw.points) } : null;
    destroyPlot();
    chartDiv.innerHTML = "";
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    const opts = {
      width: Math.max(300, chartDiv.clientWidth || 760),
      height: 400,
      series: [{}, { label: series.name ?? currentTitle(), stroke: "#2563eb", width: 1.5, spanGaps: true }],
      axes: [axisStyle, { ...axisStyle }],
      hooks: { drawClear: [bandsHook(bands)] },
    };
    const rangeLbl = state.range === "custom"
      ? `${state.customStart}→${state.customEnd}` : (rangeById(state.range)?.label || state.range);
    let data;
    if (second) {
      opts.series.push({ label: second.name, stroke: "#0891b2", width: 1.2, scale: "y2", spanGaps: true });
      opts.axes.push({ ...axisStyle, scale: "y2", side: 1, grid: { show: false } });
      data = mergeSeries(series, second);
      statusDiv.textContent = `${series.points.length} pts (${rangeLbl}) · overlay: ${second.name} (${second.points.length} pts)`;
    } else {
      data = [
        series.points.map(([d]) => Date.parse(d) / 1000),
        series.points.map(([, v]) => v),
      ];
      const note = p.synthetic
        ? `${series.points.length} monthly ${metricById(state.metric).label} points (${rangeLbl}) — Treasury + TRACE products${(state.metric === "adt" || state.metric === "trades") ? " with trade-count data" : ""}`
        : p.raw
          ? `${series.points.length} biweekly settlement points (${rangeLbl}, shares)`
          : `${series.points.length} monthly points (${rangeLbl})`;
      statusDiv.textContent = note;
    }
    state.plot = new uPlot(opts, data, chartDiv);
  } catch (err) {
    if (reqId !== state.reqId) return;
    destroyPlot();
    chartDiv.innerHTML = "";
    statusDiv.textContent = `Failed to load — ${err.message}`;
  }
}

function syncControls() {
  const prodSel = document.getElementById("trace-prod");
  const ovSel = document.getElementById("trace-overlay");
  if (prodSel) prodSel.value = state.product;
  if (ovSel) ovSel.value = state.overlay;
  const p = PRODUCTS.find((x) => x.id === state.product);
  const isCount = state.metric === "adt" || state.metric === "trades";
  // Metric buttons: ADT/TRADES need trade-count data; SI rows are levels-only.
  document.querySelectorAll("#trace-metric button").forEach((b) => {
    const mid = b.dataset.metric;
    const needTrades = mid === "adt" || mid === "trades";
    const ok = p.raw ? false : !needTrades || !!p.trades || p.synthetic;
    b.disabled = !ok;
    b.title = p.raw ? "Metric n/a — biweekly share levels"
      : ok ? metricById(mid).title : "Trade counts not published for this product";
    b.classList.toggle("on", state.metric === mid);
  });
  if (p.raw && state.metric !== "adv") {
    // SI rows ignore the metric toggle (levels only) — keep "adv" selected visually.
  }
  document.querySelectorAll("#trace-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === state.range));
  const customBox = document.getElementById("trace-range-custom");
  if (customBox) customBox.style.display = state.range === "custom" ? "" : "none";
}

// Programmatic product selection (used by the grid's row click).
export function setTraceChartProduct(id) {
  if (!PRODUCTS.some((p) => p.id === id)) return;
  state.product = id;
  syncControls();
  drawChart();
}

export function renderTraceCharts() {
  const root = document.getElementById("trace-chart-wrap");
  if (!root || root.dataset.init) return;
  root.dataset.init = "1";

  const prodOpts = PRODUCTS.map((p) => `<option value="${p.id}">${p.label}</option>`).join("");
  const ovOpts = OVERLAYS.map((o) => `<option value="${o.id}">${o.label}</option>`).join("");
  const metricBtns = METRICS.map((m) =>
    `<button data-metric="${m.id}" title="${m.title}">${m.label}</button>`).join("");
  const rangeBtns = RANGES.map((r) => `<button data-range="${r.id}">${r.label}</button>`).join("");

  root.innerHTML = `
    <div class="trace-controls">
      <label>Product
        <select id="trace-prod">${prodOpts}</select>
      </label>
      <span class="seg" id="trace-metric">${metricBtns}</span>
      <span class="seg" id="trace-range">${rangeBtns}</span>
      <span id="trace-range-custom" class="muted" style="display:none">
        <input type="date" id="trace-chart-start" aria-label="Start date"> →
        <input type="date" id="trace-chart-end" aria-label="End date">
        <button id="trace-chart-apply" class="mini-btn">Apply</button>
      </span>
      <label>Overlay
        <select id="trace-overlay">${ovOpts}</select>
      </label>
    </div>
    <div id="trace-chart" class="trace-chart"></div>
    <div id="trace-chart-status" class="muted"></div>`;

  document.getElementById("trace-prod").addEventListener("change", (e) => {
    state.product = e.target.value;
    syncControls();
    drawChart();
  });
  root.querySelectorAll("#trace-metric button").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.disabled || state.metric === b.dataset.metric) return;
      state.metric = b.dataset.metric;
      syncControls();
      drawChart();
    }));
  document.getElementById("trace-overlay").addEventListener("change", (e) => {
    state.overlay = e.target.value;
    drawChart();
  });
  root.querySelectorAll("#trace-range button").forEach((b) =>
    b.addEventListener("click", () => {
      state.range = b.dataset.range;
      syncControls();
      if (state.range !== "custom") drawChart();
    }));
  document.getElementById("trace-chart-apply").addEventListener("click", () => {
    const s = document.getElementById("trace-chart-start").value;
    const e = document.getElementById("trace-chart-end").value;
    if (!s || !e || s > e) return;
    state.customStart = s;
    state.customEnd = e;
    state.range = "custom";
    syncControls();
    drawChart();
  });

  syncControls();
  drawChart();
}
