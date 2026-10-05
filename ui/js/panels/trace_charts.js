// TRACE volume charts with overlay capability.
// Inline uPlot chart (not the modal) with product/metric/range/overlay selectors.
// Mounted by renderFinra() into #trace-charts-root.
import { getSeries, getRecessions } from "../api.js";

// TRACE monthly products. `trades` is null where FINRA only publishes par.
// NOTE: /api/series takes bare ids (no cycle: prefix) — the backend prepends it.
const PRODUCTS = [
  { id: "ust", label: "Treasury Total", par: "trace-ust-par", trades: "trace-ust-trades" },
  { id: "tba", label: "TBA", par: "trace-tba-par", trades: null },
  { id: "corp", label: "Corporate", par: "trace-corp-par", trades: "trace-corp-trades" },
  { id: "mbs", label: "MBS (Spec Pools)", par: "trace-mbs-par", trades: null },
  { id: "cmo", label: "CMO", par: "trace-cmo-par", trades: null },
  { id: "absx", label: "ABSX (CLO/CMBS)", par: "trace-absx-par", trades: null },
  { id: "agcy", label: "Agency", par: "trace-agcy-par", trades: null },
  { id: "conv", label: "Convertibles", par: "trace-conv-par", trades: "trace-conv-trades" },
  { id: "abs", label: "ABS", par: "trace-abs-par", trades: null },
  { id: "eln", label: "ELN", par: "trace-eln-par", trades: "trace-eln-trades" },
  { id: "chrc", label: "Church Plans", par: "trace-chrc-par", trades: "trace-chrc-trades" },
  { id: "onrun", label: "Treasury On-the-Run", par: "trace-ust-onrun-par", trades: null },
  { id: "offrun", label: "Treasury Off-the-Run", par: "trace-ust-offrun-par", trades: null },
];

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

const RANGES = [
  { id: "1y", label: "1Y" },
  { id: "5y", label: "5Y" },
  { id: "10y", label: "10Y" },
  { id: "max", label: "Max" },
];

const state = { product: "tba", metric: "par", range: "5y", overlay: "", plot: null, reqId: 0 };
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
  return state.metric === "trades" && p.trades ? p.trades : p.par;
}

function currentTitle() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  const m = state.metric === "trades" && p.trades ? "Trades" : "Par volume ($)";
  return `${p.label} — ${m}`;
}

async function drawChart() {
  const reqId = ++state.reqId;
  const chartDiv = document.getElementById("trace-chart");
  const statusDiv = document.getElementById("trace-chart-status");
  if (!chartDiv) return;
  const sid = currentSeriesId();
  statusDiv.textContent = "Loading…";
  try {
    const [series, second, bands] = await Promise.all([
      getSeries(sid, state.range),
      state.overlay ? getSeries(state.overlay, state.range) : Promise.resolve(null),
      loadRecessions(),
    ]);
    if (reqId !== state.reqId) return; // superseded
    destroyPlot();
    chartDiv.innerHTML = "";
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    const opts = {
      width: Math.max(300, chartDiv.clientWidth || 760),
      height: 340,
      series: [{}, { label: series.name ?? currentTitle(), stroke: "#2563eb", width: 1.5, spanGaps: true }],
      axes: [axisStyle, { ...axisStyle }],
      hooks: { drawClear: [bandsHook(bands)] },
    };
    let data;
    if (second) {
      opts.series.push({ label: second.name, stroke: "#0891b2", width: 1.2, scale: "y2", spanGaps: true });
      opts.axes.push({ ...axisStyle, scale: "y2", side: 1, grid: { show: false } });
      data = mergeSeries(series, second);
      statusDiv.textContent = `${series.points.length} pts · overlay: ${second.name} (${second.points.length} pts)`;
    } else {
      data = [
        series.points.map(([d]) => Date.parse(d) / 1000),
        series.points.map(([, v]) => v),
      ];
      statusDiv.textContent = `${series.points.length} monthly points`;
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
  const metricPar = document.getElementById("trace-metric-par");
  const metricTr = document.getElementById("trace-metric-tr");
  const ovSel = document.getElementById("trace-overlay");
  if (prodSel) prodSel.value = state.product;
  if (ovSel) ovSel.value = state.overlay;
  // metric toggle: disable trades button when product has no trades series
  const p = PRODUCTS.find((x) => x.id === state.product);
  if (metricTr) {
    metricTr.disabled = !p.trades;
    metricTr.title = p.trades ? "" : "Trade counts not published for this product";
    if (!p.trades && state.metric === "trades") state.metric = "par";
  }
  if (metricPar) metricPar.classList.toggle("on", state.metric === "par");
  if (metricTr) metricTr.classList.toggle("on", state.metric === "trades");
  document.querySelectorAll("#trace-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === state.range));
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
  const rangeBtns = RANGES.map((r) => `<button data-range="${r.id}">${r.label}</button>`).join("");

  root.innerHTML = `
    <div class="trace-controls">
      <label>Product
        <select id="trace-prod">${prodOpts}</select>
      </label>
      <span class="seg">
        <button id="trace-metric-par">Par $</button><button id="trace-metric-tr">Trades</button>
      </span>
      <span class="seg" id="trace-range">${rangeBtns}</span>
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
  document.getElementById("trace-metric-par").addEventListener("click", () => {
    state.metric = "par";
    syncControls();
    drawChart();
  });
  document.getElementById("trace-metric-tr").addEventListener("click", () => {
    if (state.metric !== "trades") {
      state.metric = "trades";
      syncControls();
      drawChart();
    }
  });
  document.getElementById("trace-overlay").addEventListener("change", (e) => {
    state.overlay = e.target.value;
    drawChart();
  });
  root.querySelectorAll("#trace-range button").forEach((b) =>
    b.addEventListener("click", () => {
      state.range = b.dataset.range;
      syncControls();
      drawChart();
    }));

  syncControls();
  drawChart();
}
