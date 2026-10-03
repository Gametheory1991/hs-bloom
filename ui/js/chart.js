import { getRecessions, getSeries } from "./api.js";

let plot = null;
let recessionsPromise = null; // fetched once per page load, shared by all charts
let activeRequestId = 0;
let resizeObserver = null;
let previousFocus = null;

export function enableTouchCursor(chart) {
  const move = (e) => {
    if (e.pointerType !== "touch") return;
    const rect = chart.over.getBoundingClientRect();
    chart.setCursor({ left: e.clientX - rect.left, top: e.clientY - rect.top });
  };
  chart.over.addEventListener("pointerdown", move);
  chart.over.addEventListener("pointermove", move);
}

function chartSize(root) {
  return { width: Math.max(1, Math.min(820, root.clientWidth)),
    height: Math.max(160, Math.min(320, window.innerHeight * 0.55)) };
}

function destroyPlot() {
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (plot) {
    plot.destroy();
    plot = null;
  }
}

async function loadRecessions() {
  recessionsPromise ??= getRecessions()
    .then((r) => r.bands.map(([a, b]) => [Date.parse(a) / 1000, Date.parse(b) / 1000]))
    .catch(() => []); // bands are decoration — a failed fetch must not kill the chart
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

export async function openChart(seriesId, title, overlayId = null) {
  const requestId = ++activeRequestId;
  const overlay = document.getElementById("chart-overlay");
  const root = document.getElementById("chart-root");
  if (overlay.classList.contains("hidden")) previousFocus = document.activeElement;
  document.getElementById("chart-title").textContent = title;
  overlay.classList.remove("hidden");
  document.getElementById("chart-close").focus();
  try {
    const [series, second, bands] = await Promise.all([
      getSeries(seriesId, "10y"),
      overlayId ? getSeries(overlayId, "10y") : null,
      loadRecessions(),
    ]);
    if (requestId !== activeRequestId) return;
    root.innerHTML = "";
    destroyPlot();
    const axisStyle = { stroke: "#6a746a", grid: { stroke: "#1e261e" } };
    const opts = {
      ...chartSize(root),
      series: [{}, { label: series.name ?? series.unit, stroke: "#f5a623", width: 1.5, spanGaps: true }],
      axes: [axisStyle, { ...axisStyle }],
      hooks: { drawClear: [bandsHook(bands)] },
    };
    let data;
    if (second) {
      document.getElementById("chart-title").textContent = `${title} vs ${second.name}`;
      opts.series.push({
        label: second.name, stroke: "#5f9ea0", width: 1.2, scale: "y2", spanGaps: true,
      });
      opts.axes.push({ ...axisStyle, scale: "y2", side: 1, grid: { show: false } });
      data = mergeSeries(series, second);
    } else {
      data = [
        series.points.map(([d]) => Date.parse(d) / 1000),
        series.points.map(([, v]) => v),
      ];
    }
    plot = new uPlot(opts, data, root);
    enableTouchCursor(plot);
    resizeObserver = new ResizeObserver(() => {
      if (plot) plot.setSize(chartSize(root));
    });
    resizeObserver.observe(root);
  } catch (err) {
    if (requestId !== activeRequestId) return;
    // a failed fetch must not leave the previous chart silently mislabeled
    destroyPlot();
    root.textContent = `Failed to load chart — ${err.message}`;
  }
}

function closeChart() {
  if (document.getElementById("chart-overlay").classList.contains("hidden")) return;
  activeRequestId += 1;
  document.getElementById("chart-overlay").classList.add("hidden");
  destroyPlot();
  previousFocus?.focus();
  previousFocus = null;
}

document.getElementById("chart-close").addEventListener("click", closeChart);
document.getElementById("chart-overlay").addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    document.getElementById("chart-close").focus();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeChart();
});
