// STRESS MONITOR tab: 8 heatmaps (Harry's spec 2026-10-08).
// Fed by /api/stress/heatmaps (matrices cached daily by the scheduler job).
// H1/H2: level today vs episode peaks (native units) — 7D acute / 30D sustained
// H3/H4: velocity today vs episode peaks (raw, signed) — 7D / 30D
// H5/H6: velocity z-score (sigmas vs trailing 1Y) — 7D / 30D
// H7/H8: level z-score vs trailing history — 1Y / full
// Warm-dark theme: bg #171410, amber #e8c96a, soft teal #7fc9b5.
// Heatmap cells use a light theme per Harry 2026-10-08: white cells,
// green text = calm, red text = stressed.
import { getStressHeatmaps } from "../api.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PANELS = [
  { key: "lvl7", title: "H1 · Level — 7-day acute peak by episode (native units)", kind: "seq", texts: "lvl7" },
  { key: "lvl30", title: "H2 · Level — 30-day sustained peak by episode (native units)", kind: "seq", texts: "lvl30" },
  { key: "vel7", title: "H3 · Velocity — 7-day shock, signed", kind: "div" },
  { key: "vel30", title: "H4 · Velocity — 30-day grind, signed", kind: "div" },
  { key: "velz7", title: "H5 · Velocity z-score — 7-day (σ vs trailing 1Y)", kind: "zdiv" },
  { key: "velz30", title: "H6 · Velocity z-score — 30-day (σ vs trailing 1Y)", kind: "zdiv" },
  { key: "lvlz1y", title: "H7 · Level z-score vs trailing 1Y", kind: "zdiv" },
  { key: "lvlzfull", title: "H8 · Level z-score vs full history", kind: "zdiv" },
];

// Light theme: filled color blocks (Harry 2026-10-08).
// Green = calm, orange = elevated, red = stressed.
// Text is bold white on saturated fills, bold black on light fills (by luminance).
function cellStyle(kind, v, rowMin, rowMax, absMax) {
  if (v == null || !isFinite(v)) return { bg: "#f5f5f5", fg: "#b0b0b0" };
  let t; // 0 = calm → 1 = stressed
  if (kind === "seq") {
    t = rowMax > rowMin ? (v - rowMin) / (rowMax - rowMin) : 0.5;
  } else {
    const m = kind === "zdiv" ? 3 : (absMax || 1);
    t = Math.min(Math.abs(v) / m, 1);
  }
  t = Math.max(0, Math.min(1, t));
  let r, g, b;
  if (t < 0.5) {           // green → amber
    const k = t / 0.5;
    r = Math.round(34 + (230 - 34) * k);
    g = Math.round(139 + (140 - 139) * k);
    b = Math.round(34 + (0 - 34) * k);
  } else {                 // amber → red
    const k = (t - 0.5) / 0.5;
    r = Math.round(230 + (200 - 230) * k);
    g = Math.round(140 + (30 - 140) * k);
    b = Math.round(0 + (30 - 0) * k);
  }
  const lum = 0.299 * r + 0.587 * g + 0.114 * b;
  return { bg: `rgb(${r},${g},${b})`, fg: lum < 140 ? "#ffffff" : "#1a1a1a" };
}

function fmtCell(panel, v, text) {
  if (v == null || !isFinite(v)) return "n/a";
  if (text != null) return esc(text);
  if (panel.kind === "zdiv") return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}σ`;
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}`;
}

function overallScoreBar(data, panelKey) {
  // Mean z-score per column, using the z-scored matrix that matches each panel:
  // H1/H2 (native levels) → H8; H3/H5 (7d vel) → H5; H4/H6 (30d vel) → H6;
  // H7 → H7; H8 → H8. Native-unit matrices can't be averaged across units.
  const srcKey = { lvl7: "lvlzfull", lvl30: "lvlzfull", vel7: "velz7", vel30: "velz30",
                   velz7: "velz7", velz30: "velz30", lvlz1y: "lvlz1y", lvlzfull: "lvlzfull" }[panelKey] || "lvlzfull";
  const m = data.matrices[srcKey];
  if (!m) return "";
  const cols = data.columns;
  const perCol = cols.map((_, j) => {
    const vals = m.map((row) => row[j]).filter((v) => v != null && isFinite(v));
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  });
  // reuse the green→amber→red ramp: |z| 0 → 2.5σ maps to t 0 → 1
  const srcLabel = { lvl7: "H8", lvl30: "H8", vel7: "H5", vel30: "H6",
                   velz7: "H5", velz30: "H6", lvlz1y: "H7", lvlzfull: "H8" }[panelKey] || "H8";
  let html = `<div class="stress-overall"><div class="stress-overall-title">OVERALL STRESS SCORE — mean z-score across all series (${srcLabel})</div><div class="stress-overall-bar">`;
  perCol.forEach((s, j) => {
    const now = cols[j] === "NOW";
    if (s == null) {
      html += `<div class="so-cell${now ? " now-col" : ""}" style="background:#f5f5f5;color:#b0b0b0">n/a</div>`;
      return;
    }
    const t = Math.min(Math.abs(s) / 2.5, 1);
    const st = cellStyle("zdiv", s >= 0 ? t * 3 : -t * 3, 0, 0, 3);
    const sign = s >= 0 ? "+" : "−";
    html += `<div class="so-cell${now ? " now-col" : ""}" style="background:${st.bg};color:${st.fg}" title="${esc(cols[j])}">${sign}${Math.abs(s).toFixed(2)}σ</div>`;
  });
  return html + `</div></div>`;
}

function renderOne(panel, data) {
  const mat = data.matrices[panel.key];
  const texts = panel.texts ? data.texts[panel.texts] : null;
  const cols = data.columns;
  const rows = data.rows;
  // per-row min/max for sequential panels; global abs-max for diverging
  let absMax = 0;
  const rowStats = mat.map((row) => {
    const vals = row.filter((v) => v != null && isFinite(v));
    if (panel.kind === "div") for (const v of vals) absMax = Math.max(absMax, Math.abs(v));
    if (!vals.length) return null;
    return { min: Math.min(...vals), max: Math.max(...vals) };
  });
  let html = `<div class="stress-hm"><div class="stress-hm-title">${esc(panel.title)}</div>`;
  html += `<div class="stress-hm-scroll"><table class="stress-hm-table"><thead><tr><th></th>`;
  for (const c of cols) {
    const now = c === "NOW";
    html += `<th${now ? ' class="now-col"' : ""}>${esc(c)}</th>`;
  }
  html += `</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    html += `<tr><td class="row-label">${esc(r.name)}</td>`;
    mat[i].forEach((v, j) => {
      const st = cellStyle(panel.kind, v, rowStats[i]?.min, rowStats[i]?.max, absMax || 1);
      const now = cols[j] === "NOW";
      const txt = fmtCell(panel, v, texts ? texts[i][j] : null);
      html += `<td class="hm-cell${now ? " now-col" : ""}" style="background:${st.bg};color:${st.fg}">${txt}</td>`;
    });
    html += `</tr>`;
  });
  html += `</tbody></table></div></div>`;
  return html;
}

export async function renderStressHeatmaps() {
  const body = document.querySelector("#panel-stress-heatmaps .panel-body");
  const foot = document.querySelector("#panel-stress-heatmaps .panel-foot");
  if (!body) return;
  let data;
  try {
    data = await getStressHeatmaps();
  } catch {
    body.innerHTML = `<div class="empty-state">STRESS HEATMAPS UNAVAILABLE</div>`;
    return;
  }
  if (!data || !data.columns?.length || !data.matrices?.lvl7) {
    body.innerHTML = `<div class="empty-state">STRESS HEATMAPS COMPUTING — CHECK BACK AFTER THE NEXT DAILY RUN</div>`;
    return;
  }
  const asof = data.asof ? ` · as of ${esc(data.asof)}` : "";
  body.innerHTML =
    `<div class="stress-intro muted">Levels in native units · velocity signed · z-scores in σ — columns: 8 stress episodes + NOW. n/a = no history.${asof}</div>` +
    `<div class="stress-legend"><span class="sl-sw" style="background:#d42a1e"></span>stressed` +
    ` <span class="sl-sw" style="background:#c78a00"></span>elevated` +
    ` <span class="sl-sw" style="background:#1a8f3c"></span>calm</div>` +
    PANELS.map((p) => overallScoreBar(data, p.key) + renderOne(p, data)).join("");
  if (foot) {
    const upd = data.updated_at ? new Date(data.updated_at).toLocaleString() : "—";
    foot.textContent = `DATA: STRESS_HEATMAPS · UPDATED ${upd}`;
  }
}
