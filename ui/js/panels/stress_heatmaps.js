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

// Light theme: white cells, text color carries the signal.
// Green = calm, red = stressed. Returns {bg, fg}.
function cellStyle(kind, v, rowMin, rowMax, absMax) {
  if (v == null || !isFinite(v)) return { bg: "#ffffff", fg: "#b0b0b0" };
  let t; // 0 = calm (green) → 1 = stressed (red)
  if (kind === "seq") {
    // per-row min → max
    t = rowMax > rowMin ? (v - rowMin) / (rowMax - rowMin) : 0.5;
  } else {
    // diverging: intensity by |v|; sign kept in the +/- prefix
    const m = kind === "zdiv" ? 3 : (absMax || 1);
    t = Math.min(Math.abs(v) / m, 1);
  }
  // green (#1a8f3c) → amber (#c78a00) → red (#d42a1e)
  let r, g, b;
  if (t < 0.5) {
    const k = t / 0.5;
    r = Math.round(26 + (199 - 26) * k);
    g = Math.round(143 + (138 - 143) * k);
    b = Math.round(60 + (0 - 60) * k);
  } else {
    const k = (t - 0.5) / 0.5;
    r = Math.round(199 + (212 - 199) * k);
    g = Math.round(138 + (42 - 138) * k);
    b = Math.round(0 + (30 - 0) * k);
  }
  // faint tint behind the text so the grid still reads as a heatmap
  const tint = Math.round(t * 22);
  return { bg: `rgb(${255 - tint},${255 - tint},${255 - tint})`, fg: `rgb(${r},${g},${b})` };
}

function fmtCell(panel, v, text) {
  if (v == null || !isFinite(v)) return "n/a";
  if (text != null) return esc(text);
  if (panel.kind === "zdiv") return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}σ`;
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}`;
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
    PANELS.map((p) => renderOne(p, data)).join("");
  if (foot) {
    const upd = data.updated_at ? new Date(data.updated_at).toLocaleString() : "—";
    foot.textContent = `DATA: STRESS_HEATMAPS · UPDATED ${upd}`;
  }
}
