// RISK MAP tab: world choropleth of per-country market risk.
// Data comes from the /api/dashboard "riskmap" panel (country_risk doc).
// Buckets are colorblind-safe: green STABLE / yellow IMPROVING /
// orange DETERIORATING / red RISK ZONE. Tap a country for its breakdown.
import { fmtAge, isStale } from "../fmt.js";

let _worldCache = null;
async function getWorld() {
  if (!_worldCache) {
    const mod = await import("../world110m.js");
    _worldCache = mod.WORLD;
  }
  return _worldCache;
}

const COLORS = {
  green: "#8ab86f",
  yellow: "#e8c96a",
  orange: "#e07b39",
  red: "#d9736a",
  nodata: "#3a332a",
  land: "#26211a",
};
export { COLORS };
const STROKE = "#a89a83";
const STALE_MINUTES = 2880; // 2x the daily country_risk cadence

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TREND = {
  improving: { arrow: "\u2193", cls: "up", label: "improving" },
  deteriorating: { arrow: "\u2191", cls: "down", label: "deteriorating" },
  flat: { arrow: "\u2192", cls: "flat", label: "flat" },
  unknown: { arrow: "?", cls: "flat", label: "unknown" },
};

const INPUT_LABELS = {
  yield_pct: "10Y yield vs 5Y",
  equity_dd_pct: "Equity drawdown",
  fx_dep_pct: "FX depreciation",
};

// Equirectangular projection into a 1000x500 viewBox.
export const project = (lon, lat) => [
  ((lon + 180) / 360) * 1000,
  ((90 - lat) / 180) * 500,
];

function ringPath(ring) {
  return "M" + ring.map(([lon, lat]) => {
    const [x, y] = project(lon, lat);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join("L") + "Z";
}

function featurePath(g) {
  if (g.type === "Polygon") return g.coordinates.map(ringPath).join("");
  if (g.type === "MultiPolygon")
    return g.coordinates.map((poly) => poly.map(ringPath).join("")).join("");
  return "";
}
export { featurePath };

function detailCard(c) {
  if (!c) return `<div class="map-detail empty">TAP A COUNTRY FOR ITS BREAKDOWN</div>`;
  const t = TREND[c.trend] ?? TREND.unknown;
  const rows = Object.entries(INPUT_LABELS).map(([k, label]) => {
    const v = c.inputs?.[k];
    return `<tr><td>${label}</td><td>${v == null ? "\u2014" : v.toFixed(1)}</td></tr>`;
  }).join("");
  return `<div class="map-detail">
    <div class="map-detail-head"><span class="map-dot" style="background:${COLORS[c.bucket] ?? COLORS.nodata}"></span>
    <strong>${esc(c.name)}</strong>
    <span class="bucket-tag" style="color:${COLORS[c.bucket] ?? COLORS.nodata}">${esc(c.bucket_label)}</span></div>
    <table>
      <tr><td>Risk score</td><td>${c.score == null ? "\u2014" : c.score.toFixed(1) + " / 100"}</td></tr>
      <tr><td>Trend (21d)</td><td class="${t.cls}">${t.arrow} ${t.label}${c.trend_delta == null ? "" : ` (${c.trend_delta > 0 ? "+" : ""}${c.trend_delta.toFixed(1)})`}</td></tr>
      ${rows}
      <tr><td>Inputs used</td><td>${c.inputs_available} of 3</td></tr>
    </table></div>`;
}

export async function renderRiskMap(riskmap) {
  const body = document.querySelector("#panel-riskmap .panel-body");
  const footEl = document.querySelector("#panel-riskmap .panel-foot");
  if (!body) return;
  const countries = riskmap.countries ?? [];
  if (!countries.length) {
    body.innerHTML = `<div class="empty-state">NO DATA — country_risk job has not run yet</div>`;
    if (footEl) footEl.textContent = "DATA: \u2014";
    return;
  }
  const byCode = Object.fromEntries(countries.map((c) => [c.code, c]));
  const counts = { green: 0, yellow: 0, orange: 0, red: 0, nodata: 0 };
  for (const c of countries) counts[c.bucket] = (counts[c.bucket] ?? 0) + 1;

  const WORLD = await getWorld();
  const paths = WORLD.features.map((f) => {
    const c = byCode[f.i];
    // GB in the geometry set maps to the UK country code used by the backend
    const country = c ?? (f.i === "GB" ? byCode["UK"] : null);
    const fill = country ? (COLORS[country.bucket] ?? COLORS.nodata) : COLORS.land;
    const code = country ? country.code : "";
    return `<path d="${featurePath(f.g)}" fill="${fill}" stroke="${STROKE}" stroke-width="0.8"` +
      (code ? ` data-code="${code}" class="map-country"` : ` class="map-land"`) + "/>";
  }).join("");

  const legend = ["green", "yellow", "orange", "red"].map((b) =>
    `<span class="legend-item"><span class="map-dot" style="background:${COLORS[b]}"></span>` +
    `${b.toUpperCase()} ${counts[b]}</span>`).join("");

  body.innerHTML = `
    <div class="map-wrap">
      <svg viewBox="0 0 1000 500" class="worldmap" role="img" aria-label="World risk map">${paths}</svg>
      <div class="map-legend muted">${legend}<span class="legend-item"><span class="map-dot" style="background:${COLORS.nodata}"></span>NO DATA ${counts.nodata}</span></div>
      <div id="map-detail">${detailCard(null)}</div>
      <div class="muted" style="font-size:10px">AS OF ${esc(riskmap.asof ?? "\u2014")} · TAP A COLORED COUNTRY</div>
    </div>`;
  body.querySelectorAll("path.map-country").forEach((p) => {
    p.addEventListener("click", () => {
      const c = byCode[p.dataset.code];
      const detail = document.getElementById("map-detail");
      if (detail && c) detail.innerHTML = detailCard(c);
    });
  });
  if (footEl) {
    const src = (riskmap.source ?? "\u2014").toUpperCase();
    footEl.textContent = `DATA: ${src} \u00b7 ${fmtAge(riskmap.updated_at)}`;
    footEl.classList.toggle("stale", isStale(riskmap.updated_at, STALE_MINUTES));
  }
}
