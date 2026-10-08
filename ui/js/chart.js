import { getRecessions, getSeries } from "./api.js";
import { tradingX, bandsToIndices } from "./tradingx.js";

let plot = null;
let recessionsPromise = null; // fetched once per page load, shared by all charts
let activeRequestId = 0;

function destroyPlot() {
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
    dates,
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
  document.getElementById("chart-title").textContent = title;
  overlay.classList.remove("hidden");
  try {
    const [series, second, bands] = await Promise.all([
      getSeries(seriesId, "10y"),
      overlayId ? getSeries(overlayId, "10y") : null,
      loadRecessions(),
    ]);
    if (requestId !== activeRequestId) return;
    root.innerHTML = "";
    destroyPlot();
    const axisStyle = { stroke: "#a89a83", grid: { stroke: "#38312a" } };
    const opts = {
      width: Math.min(820, root.clientWidth || 820), height: 320,
      series: [{}, { label: series.name ?? series.unit, stroke: "#e8c96a", width: 1.5, spanGaps: true }],
      axes: [axisStyle, { ...axisStyle }],
      hooks: { drawClear: [bandsHook(bands)] },
    };
    let data;
    let dates;
    if (second) {
      document.getElementById("chart-title").textContent = `${title} vs ${second.name}`;
      opts.series.push({
        label: second.name, stroke: "#5f9ea0", width: 1.2, scale: "y2", spanGaps: true,
      });
      opts.axes.push({ ...axisStyle, scale: "y2", side: 1, grid: { show: false } });
      const merged = mergeSeries(series, second);
      dates = merged[0];
      const tx = tradingX(dates);
      data = [tx.x, merged[1], merged[2]];
      opts.axes[0] = { ...axisStyle, values: tx.values };
      opts.hooks = { drawClear: [bandsHook(bandsToIndices(bands, dates))] };
    } else {
      dates = series.points.map(([d]) => d);
      const tx = tradingX(dates);
      data = [tx.x, series.points.map(([, v]) => v)];
      opts.axes[0] = { ...axisStyle, values: tx.values };
      opts.hooks = { drawClear: [bandsHook(bandsToIndices(bands, dates))] };
    }
    plot = new uPlot(opts, data, root);
  } catch (err) {
    if (requestId !== activeRequestId) return;
    // a failed fetch must not leave the previous chart silently mislabeled
    destroyPlot();
    root.textContent = `Failed to load chart — ${err.message}`;
  }
}

document.getElementById("chart-close").addEventListener("click", () => {
  activeRequestId += 1;
  document.getElementById("chart-overlay").classList.add("hidden");
  destroyPlot();
});
