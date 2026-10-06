// heatmap.js — shared Bloomberg-style delta heatmap for the light Signal theme.
// Diverging scale: RED = positive move (hot 🔥), BLUE = negative move (cold ❄️),
// symmetric, intensity = magnitude. Prefers a z-score when the caller has one;
// otherwise falls back to the % change ratio (0.25 = full intensity).
// Light tints keep dark text readable on the light theme.
//
// Usage:
//   import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
//   `<td class="num"${heatStyle({ pct: 0.168 })}>…</td>`
//   `<td class="num"${heatStyle({ z: 2.4 })}>…</td>`
// Place ${HEAT_LEGEND} above any heatmapped table.
// Background color value only (for programmatic styling): "rgba(...)" or "".
export function heatBg(o = {}) {
  const z = o.z, pct = o.pct;
  let v = null, full = 1;
  if (z != null && isFinite(z)) { v = z; full = 3; }
  else if (pct != null && isFinite(pct)) { v = pct; full = 0.25; }
  else return "";
  if (v === 0) return "";
  const a = (0.05 + Math.min(Math.abs(v) / full, 1) * 0.25).toFixed(2);
  const rgb = v > 0 ? "220,38,38" : "37,99,235";
  return `rgba(${rgb},${a})`;
}

export function heatStyle(o = {}) {
  const bg = heatBg(o);
  return bg ? ` style="background:${bg}"` : "";
}

// Compact legend for heatmapped delta tables.
export const HEAT_LEGEND =
  `<span class="heat-legend" title="Cell background: red = positive move (hot), blue = negative move (cold); deeper color = larger move">` +
  `<span class="hl-sw" style="background:rgba(220,38,38,.30)"></span>🔥 hot = large + ` +
  `<span class="hl-sw" style="background:rgba(37,99,235,.30)"></span>❄️ cold = large −</span>`;
