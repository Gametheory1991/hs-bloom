// TFF (OFR Traders in Financial Futures) tab — POSITIONING.
// Big trend chart (leveraged-funds Treasury net = the -$799B basis-trade
// short) + heatmap grid of deltas across trader types/contracts.
// Data: /api/dashboard "tff" panel (doc 'tff' — all 153 OFR mnemonics,
// curated groups with rolling ~300-week history per row).
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
import { rangeCells, statsFromValues, RANGE_TH, RANGE_LEGEND } from "../rangeviz.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const RANGES = [["1m", "1M", 4], ["3m", "3M", 13], ["1y", "1Y", 52], ["5y", "5Y", 260], ["max", "MAX", 1e9]];
const HORIZONS = [["1w", "1W", 1], ["1m", "1M", 4], ["1q", "1Q", 13], ["1y", "1Y", 52]];
const METRICS = [["net", "Net $"], ["dv01", "DV01"], ["eqv", "10Y-equiv"]];
const CHART_GROUPS = { net: "treas_net", dv01: "treas_dv01", eqv: "treas_10y" };
const LINE_COLORS = ["#e8c96a", "#7fc9b5", "#7c3aed"];

const state = { metric: "net", range: "1y", horizon: "1w", overlay: { ai: true, di: true } };
let tffData = null;

function fmtBig(v) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}K`;
  return `${s}$${a.toFixed(0)}`;
}

function fmtNom(v) {
  if (v == null || !isFinite(v)) return "—";
  const s = v >= 0 ? "+" : "−";
  return s + fmtBig(Math.abs(v)).replace(/^−?\$/, "$");
}

function pctTxt(p) {
  if (p == null || !isFinite(p)) return "—";
  return `${p >= 0 ? "+" : ""}${(p * 100).toFixed(1)}%`;
}

/** row.hist: [[date, val], ...] ascending. value `h` weeks back or null. */
function refBack(hist, h) {
  if (!hist || hist.length <= h) return null;
  return hist[hist.length - 1 - h][1];
}

function deltaCell(row, hz) {
  const hist = row.hist ?? [];
  const now = hist.length ? hist[hist.length - 1][1] : null;
  const ref = refBack(hist, hz[2]);
  if (now == null) return `<td class="num muted">—</td>`;
  const main = `<b>${fmtBig(now)}</b>`;
  if (ref == null || ref === 0)
    return `<td class="num" title="History too short — accumulating">${main}<br><span class="muted" style="font-size:11px">accumulating</span></td>`;
  const nom = now - ref, pct = nom / Math.abs(ref);
  return `<td class="num"${heatStyle({ pct })} title="${esc(fmtNom(nom))} vs ${hz[1]} (w/e ${esc(hist[hist.length - 1][0])} vs ${esc(hist[hist.length - 1 - hz[2]][0])})">${main}<br>` +
    `<span style="font-size:11px" class="${nom > 0 ? "up" : nom < 0 ? "down" : "flat"}">${pctTxt(pct)}</span> ` +
    `<span class="muted" style="font-size:11px">(${esc(fmtNom(nom))})</span></td>`;
}

function groupById(id) {
  return (tffData?.groups ?? []).find((g) => g.id === id);
}

function renderTffChart() {
  const host = document.getElementById("tff-chart");
  if (!host || !tffData) return;
  const g = groupById(CHART_GROUPS[state.metric]);
  const rows = (g?.rows ?? []).filter((r) => (r.hist ?? []).length > 1);
  if (!rows.length) { host.innerHTML = `<span class="muted">No TFF data yet — first pull pending.</span>`; return; }
  const n = Math.min(RANGES.find((r) => r[0] === state.range)[2], rows[0].hist.length);
  const dates = rows[0].hist.slice(-n).map((p) => p[0]);
  // align each row to the chart dates
  const series = rows.map((r) => {
    const m = new Map(r.hist.map((p) => [p[0], p[1]]));
    return dates.map((d) => m.get(d) ?? null);
  });
  const labels = rows.map((r) => r.label);
  const show = labels.map((l, i) =>
    i === 0 || (l === "Asset managers" && state.overlay.ai) || (l === "Dealers" && state.overlay.di));
  const axis = { stroke: "#a89a83", grid: { stroke: "#38312a" } };
  const data = [dates.map((d) => Date.parse(d + "T12:00:00") / 1000), ...series];
  new uPlot({
    width: Math.max(300, host.clientWidth || 720), height: 300,
    series: [{},
      ...series.map((_, i) => ({
        label: labels[i], stroke: LINE_COLORS[i % 3], width: i === 0 ? 2.2 : 1.6,
        show: show[i], spanGaps: true,
      }))],
    axes: [axis, {
      ...axis,
      values: (u, vals) => vals.map((v) => {
        const a = Math.abs(v);
        if (a >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
        if (a >= 1e9) return `$${(v / 1e9).toFixed(0)}B`;
        return `$${(v / 1e6).toFixed(0)}M`;
      }),
    }],
  }, data, host);
}

function renderTffKpis() {
  const host = document.getElementById("tff-kpis");
  if (!host || !tffData) return;
  const g = groupById("treas_net");
  const rows = g?.rows ?? [];
  const dv = (groupById("treas_dv01")?.rows ?? [])[0];
  const tiles = rows.map((r) => {
    const hist = r.hist ?? [];
    const now = hist.length ? hist[hist.length - 1][1] : null;
    const ref = refBack(hist, 1);
    const nom = now != null && ref != null ? now - ref : null;
    const pct = nom != null && ref ? nom / Math.abs(ref) : null;
    return `<div class="kpi"><div class="kpi-label">${esc(r.label)} net (Treas)</div>` +
      `<div class="kpi-val">${fmtBig(now)}</div>` +
      `<div class="kpi-sub ${nom > 0 ? "up" : nom < 0 ? "down" : ""}">${pctTxt(pct)} 1W <span class="muted">(${fmtNom(nom)})</span></div></div>`;
  });
  if (dv) {
    const h = dv.hist ?? [];
    const now = h.length ? h[h.length - 1][1] : null;
    tiles.push(`<div class="kpi"><div class="kpi-label">LF net DV01</div>` +
      `<div class="kpi-val">${fmtBig(now)}</div>` +
      `<div class="kpi-sub muted">risk-adjusted short</div></div>`);
  }
  host.innerHTML = tiles.join("");
}

function renderTffGrid() {
  const host = document.getElementById("tff-grid");
  if (!host || !tffData) return;
  const hz = HORIZONS.find((x) => x[0] === state.horizon);
  const groups = tffData.groups ?? [];
  let html = `<div style="margin-bottom:6px">${HEAT_LEGEND}</div>`;
  for (const g of groups) {
    html += `<div class="subhead" style="margin:14px 0 4px"><b>${esc(g.label)}</b></div>`;
    html += `<div class="tbl-wrap"><table class="tbl"><thead><tr>` +
      `<th style="text-align:left">Position</th><th>Latest</th>` +
      `<th title="Change vs ${hz[1]} ago">Δ ${hz[1]}</th>${RANGE_TH}</tr></thead><tbody>`;
    for (const r of g.rows) {
      const hist = r.hist ?? [];
      const vals = hist.map((p) => p[1]);
      const s = statsFromValues(vals.slice(-52));
      const asof = hist.length ? hist[hist.length - 1][0] : "";
      html += `<tr><td style="text-align:left" title="${esc(r.name || r.mnemonic)}${r.derived ? " — derived (long − short)" : ""}">${esc(r.label)}${r.derived ? ' <span class="muted" title="Derived: long − short (OFR publishes legs only)">Δ</span>' : ""}</td>` +
        `<td class="num"><b>${fmtBig(vals[vals.length - 1])}</b><br><span class="muted" style="font-size:11px">${esc(asof)}</span></td>` +
        deltaCell(r, hz) + rangeCells(s, "52w") + `</tr>`;
    }
    html += `</tbody></table></div>`;
  }
  html += `<div class="muted" style="margin-top:8px; font-size:11px">${RANGE_LEGEND} · ` +
    `Deltas are weekly (TFF is a Tuesday print). ` +
    `LF = leveraged funds (hedge-fund basis-trade proxy) · AI = asset managers · DI = dealers. ` +
    `<a href="${esc(tffData.source_url || "https://www.financialresearch.gov/hedge-fund-monitor/")}" target="_blank" rel="noopener">OFR Hedge Fund Monitor</a></div>`;
  host.innerHTML = html;
}

function drawTff() {
  renderTffChart();
  renderTffKpis();
  renderTffGrid();
}

export function renderTff(p) {
  const body = document.querySelector('[data-hub="positioning"][data-sub="tff"] .panel-body');
  if (!body) return;
  tffData = p && p.groups ? p : null;
  if (!tffData) {
    body.innerHTML = `<span class="muted">TFF data pending — first OFR pull runs on the next scheduler cycle.</span>`;
    return;
  }
  body.innerHTML =
    `<div class="chart-controls" style="margin:2px 0 8px; display:flex; gap:12px; flex-wrap:wrap; align-items:center">` +
    `<span><b>Metric:</b> ${METRICS.map(([k, l]) => `<button type="button" class="pill${state.metric === k ? " on" : ""}" data-tff-metric="${k}">${l}</button>`).join(" ")}</span>` +
    `<span><b>Range:</b> ${RANGES.map(([k, l]) => `<button type="button" class="pill${state.range === k ? " on" : ""}" data-tff-range="${k}">${l}</button>`).join(" ")}</span>` +
    `<span><b>Overlay:</b> <label><input type="checkbox" data-tff-ov="ai"${state.overlay.ai ? " checked" : ""}> Asset mgrs</label> ` +
    `<label><input type="checkbox" data-tff-ov="di"${state.overlay.di ? " checked" : ""}> Dealers</label></span>` +
    `<span><b>Horizon:</b> ${HORIZONS.map(([k, l]) => `<button type="button" class="pill${state.horizon === k ? " on" : ""}" data-tff-hz="${k}">${l}</button>`).join(" ")}</span>` +
    `<span class="muted" style="margin-left:auto">w/e ${esc(tffData.asof ?? "")} · ${tffData.mnemonic_count ?? 0} mnemonics · OFR TFF, weekly, keyless</span></div>` +
    `<div id="tff-kpis" class="kpi-row" style="margin-bottom:8px"></div>` +
    `<div id="tff-chart" style="min-height:300px"></div>` +
    `<div id="tff-grid" style="margin-top:8px"></div>`;
  body.querySelectorAll("[data-tff-metric]").forEach((b) =>
    b.addEventListener("click", () => { state.metric = b.dataset.tffMetric; renderTff(tffData); }));
  body.querySelectorAll("[data-tff-range]").forEach((b) =>
    b.addEventListener("click", () => { state.range = b.dataset.tffRange; renderTff(tffData); }));
  body.querySelectorAll("[data-tff-hz]").forEach((b) =>
    b.addEventListener("click", () => { state.horizon = b.dataset.tffHz; renderTff(tffData); }));
  body.querySelectorAll("[data-tff-ov]").forEach((c) =>
    c.addEventListener("change", () => { state.overlay[c.dataset.tffOv] = c.checked; renderTff(tffData); }));
  drawTff();
  // re-draw chart on resize (debounced)
  clearTimeout(renderTff._rz);
  renderTff._rz = setTimeout(() => {}, 0);
}
