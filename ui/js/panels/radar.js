// MARKET RADAR — the HOME tab hero. A Bloomberg-style mission-control view:
// regime strip on top, world choropleth in the center (reuses the risk-map
// geometry and country buckets), and a heatmap grid of multi-indicator
// historical-percentile cells below. Tap a cell for its 1Y sparkline.
// Data comes from the /api/dashboard "radar" panel (home_radar doc) plus
// the "riskmap" panel for country colors.
import { WORLD } from "../world110m.js";
import { COLORS, featurePath } from "./riskmap.js";
import { fmtAge, isStale } from "../fmt.js";

const STROKE = "#a89a83";
const STALE_MINUTES = 2880;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const REGIME_CLS = {
  CALM: "green", LATE_CYCLE: "yellow", RISK_OFF: "orange",
  STRESS: "orange", CRISIS: "red", UNKNOWN: "nodata",
};

const GROUP_ORDER = ["VOLATILITY", "STRESS", "RETURNS", "BREADTH", "INFLATION", "FUNDING"];

function regimeBadge(regime) {
  const cls = REGIME_CLS[regime] ?? "nodata";
  return `<span class="radar-regime" style="background:${COLORS[cls]}">${esc(regime)}</span>`;
}

function mapSvg(riskmap) {
  const countries = riskmap.countries ?? [];
  const byCode = Object.fromEntries(countries.map((c) => [c.code, c]));
  const paths = WORLD.features.map((f) => {
    const c = byCode[f.i] ?? (f.i === "GB" ? byCode["UK"] : null);
    const fill = c ? (COLORS[c.bucket] ?? COLORS.nodata) : COLORS.land;
    return `<path d="${featurePath(f.g)}" fill="${fill}" stroke="${STROKE}" stroke-width="0.8"/>`;
  }).join("");
  return `<svg viewBox="0 0 1000 500" class="worldmap radar-map" role="img" aria-label="World market risk">${paths}</svg>`;
}

function sparkSvg(hist) {
  if (!hist || hist.length < 2) return "";
  const W = 120, H = 30;
  const vals = hist.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const rng = hi - lo || 1;
  const pts = hist.map((p, i) =>
    `${(i / (hist.length - 1) * W).toFixed(1)},${(H - 2 - ((p[1] - lo) / rng) * (H - 4)).toFixed(1)}`);
  return `<svg viewBox="0 0 ${W} ${H}" class="radar-spark" aria-hidden="true">` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
}

function cellHtml(ind, i) {
  const pct = ind.percentile_1y == null ? "—" : `${ind.percentile_1y.toFixed(0)}th`;
  return `<button type="button" class="radar-cell color-${ind.color}" data-i="${i}" aria-label="${esc(ind.label)}">` +
    `<span class="radar-cell-label">${esc(ind.label)}</span>` +
    `<span class="radar-cell-value">${esc(ind.value_fmt)}</span>` +
    `<span class="radar-cell-pct">${pct} %ile 1Y</span></button>`;
}

function detailHtml(ind) {
  if (!ind) return `<div class="radar-detail empty">TAP A CELL FOR ITS 1Y HISTORY</div>`;
  const pct = ind.percentile_1y == null ? "n/a" : `${ind.percentile_1y.toFixed(1)}th percentile (1Y)`;
  return `<div class="radar-detail color-${ind.color}">
    <div class="radar-detail-head"><strong>${esc(ind.label)}</strong>
    <span class="muted">${esc(ind.group)}</span></div>
    <div class="radar-detail-row"><span class="radar-cell-value big">${esc(ind.value_fmt)}</span>${sparkSvg(ind.hist)}</div>
    <div class="muted">${pct}${ind.note ? ` · ${esc(ind.note)}` : ""}</div></div>`;
}

export function renderRadar(radar, riskmap) {
  const body = document.querySelector("#panel-radar .panel-body");
  const footEl = document.querySelector("#panel-radar .panel-foot");
  if (!body) return;
  const indicators = radar.indicators ?? [];
  if (!indicators.length) {
    body.innerHTML = `<div class="empty-state">NO DATA — home_radar job has not run yet</div>`;
    if (footEl) footEl.textContent = "DATA: —";
    return;
  }
  const groups = GROUP_ORDER.filter((g) => indicators.some((x) => x.group === g));
  const grid = groups.map((g) => {
    const cells = indicators
      .map((ind, i) => ({ ind, i }))
      .filter(({ ind }) => ind.group === g)
      .map(({ ind, i }) => cellHtml(ind, i))
      .join("");
    return `<div class="radar-group"><div class="radar-group-title muted">${g}</div>` +
      `<div class="radar-cells">${cells}</div></div>`;
  }).join("");

  body.innerHTML = `
    <div class="radar-strip">
      ${regimeBadge(radar.regime)}
      <span class="radar-verdict">${esc(radar.verdict ?? "—")}</span>
    </div>
    <div class="radar-map-wrap">${mapSvg(riskmap)}</div>
    <div class="radar-grid">${grid}</div>
    <div id="radar-detail">${detailHtml(null)}</div>
    <div class="muted" style="font-size:10px">AS OF ${esc(radar.as_of ?? "—")} · CELLS = 1Y PERCENTILE VS HISTORY</div>`;
  body.querySelectorAll(".radar-cell").forEach((btn) => {
    btn.addEventListener("click", () => {
      const detail = document.getElementById("radar-detail");
      if (detail) detail.innerHTML = detailHtml(indicators[Number(btn.dataset.i)]);
    });
  });
  if (footEl) {
    const src = (radar.source ?? "—").toUpperCase();
    footEl.textContent = `DATA: ${src} · ${fmtAge(radar.updated_at)}`;
    footEl.classList.toggle("stale", isStale(radar.updated_at, STALE_MINUTES));
  }
}
