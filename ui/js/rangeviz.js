// rangeviz.js — shared Bloomberg-style dotted range sparklines (Harry's standing
// visual, 2026-10-05). Horizontal dotted line spanning the historical range,
// blue dot = current value, orange diamond = historical average.
// Two versions: percentile scale (0-100) and z-score scale.
//
// Usage:
//   import { rangeCells, rangePlotDotted, zToPct, statsFromValues } from "../rangeviz.js";
//   // From a z-score you already have:
//   `<tr>...${rangeCells({ z: r.z_1y })}...</tr>`
//   // From a percentile you already have:
//   `${rangeCells({ pct: r.pctile_1y, z: r.z_1y })}`
//   // From a raw value array (computes pct + z):
//   const s = statsFromValues(vals); ... ${rangeCells(s)}
//
// Headers (place inside <tr>):  RANGE_TH
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Standard normal CDF — converts a z-score to a percentile (0-100).
export function zToPct(z) {
  if (z == null || !isFinite(z)) return null;
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp(-z * z / 2);
  let p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  p = 1 - p;
  if (z < 0) p = 1 - p;
  return Math.max(0, Math.min(100, p * 100));
}

// Compute {pct, z, avg, lo, hi, zlo, zhi, n} from an array of numbers.
// pct = percentile rank of the last value vs the full array.
// z = (last - mean) / stdev. zlo/zhi = observed z extremes (for axis scaling).
export function statsFromValues(vals) {
  const vs = (vals ?? []).filter((v) => v != null && isFinite(v));
  const n = vs.length;
  if (!n) return { pct: null, z: null, avg: null, lo: null, hi: null, zlo: -3, zhi: 3, n: 0 };
  const last = vs[n - 1];
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const avg = vs.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(vs.reduce((a, b) => a + (b - avg) * (b - avg), 0) / n);
  const z = sd > 0 ? (last - avg) / sd : 0;
  const pct = (vs.filter((v) => v <= last).length / n) * 100;
  const zs = sd > 0 ? vs.map((v) => (v - avg) / sd) : vs.map(() => 0);
  return { pct, z, avg, lo, hi, zlo: Math.min(...zs, -0.5), zhi: Math.max(...zs, 0.5), n };
}

// Dotted range SVG. s = {pct, z, zlo, zhi}. mode = "pct" | "z".
export function rangePlotDotted(s, mode) {
  const w = 130, p = 8;
  const isPct = mode === "pct";
  const pct = s.pct ?? zToPct(s.z);
  const lo = isPct ? 0 : Math.min(s.zlo ?? -3, -0.5);
  const hi = isPct ? 100 : Math.max(s.zhi ?? 3, 0.5);
  const span = (hi - lo) || 1;
  const X = (v) => p + Math.max(0, Math.min(1, (v - lo) / span)) * (w - 2 * p);
  const curPos = isPct ? (pct ?? 50) : (s.z ?? 0);
  const avgPos = isPct ? 50 : 0;
  const cx = X(curPos), ax = X(avgPos);
  const tip = isPct
    ? `Now: ${pct == null ? "—" : pct.toFixed(0) + "th percentile"}`
    : `Now: z = ${s.z == null ? "—" : s.z.toFixed(2)}`;
  const missing = (isPct ? pct : s.z) == null;
  return `<svg class="trng" width="${w}" height="20" viewBox="0 0 ${w} 20" role="img" aria-label="${esc(tip)}">` +
    `<line x1="${p}" y1="10" x2="${w - p}" y2="10" style="stroke:var(--line,#cbd5e1)" stroke-width="2" stroke-dasharray="2,3" stroke-linecap="round"/>` +
    `<polygon points="${ax.toFixed(1)},5 ${(ax + 4.5).toFixed(1)},10 ${ax.toFixed(1)},15 ${(ax - 4.5).toFixed(1)},10" fill="#f59e0b"><title>${isPct ? "50th percentile" : "Mean (z = 0)"}</title></polygon>` +
    (missing
      ? `<text x="${(w / 2).toFixed(0)}" y="14" text-anchor="middle" font-size="10" fill="#94a3b8">—</text>`
      : `<circle cx="${cx.toFixed(1)}" cy="10" r="5" fill="#e8c96a" stroke="#171410" stroke-width="1.5"><title>${esc(tip)}</title></circle>`) +
    `</svg>`;
}

// Both range <td>s for a table row. Accepts {z, pct} — pct falls back to zToPct(z).
// Pass winLabel to customize the tooltip window description (default "history").
export function rangeCells(s, winLabel) {
  const wl = winLabel ? ` (${winLabel})` : "";
  const pct = s.pct ?? zToPct(s.z);
  const pctTxt = pct == null ? "—" : `${pct.toFixed(0)}`;
  const zTxt = s.z == null ? "—" : `${s.z > 0 ? "+" : ""}${s.z.toFixed(2)}`;
  const pctCls = pct == null ? "" : pct >= 90 ? "up strong" : pct >= 75 ? "up" : pct <= 10 ? "down strong" : pct <= 25 ? "down" : "flat";
  const zCls = s.z == null ? "" : Math.abs(s.z) >= 2 ? (s.z > 0 ? "up strong" : "down strong") : Math.abs(s.z) >= 1 ? (s.z > 0 ? "up" : "down") : "flat";
  return `<td data-sort="off" title="Dotted range${esc(wl)}: blue dot = now">${rangePlotDotted({ ...s, pct }, "pct")}</td>` +
    `<td data-sort="off" title="Dotted z-range${esc(wl)}: blue dot = now">${rangePlotDotted(s, "z")}</td>` +
    `<td class="${pctCls} num" title="Percentile of now vs ${esc(winLabel || "history")}">${pctTxt}</td>` +
    `<td class="${zCls} num" title="Z-score of now vs ${esc(winLabel || "history")}">z ${zTxt}</td>`;
}

// Compact visible legend for the dotted range sparklines: blue dot = Now
// (current value), gold diamond = Avg (historical average / 50th percentile).
// Place once per table — e.g. inside the "Range" column header — not per row.
export const RANGE_LEGEND =
  `<span class="range-legend" title="Blue dot = current value (Now) · Gold diamond = historical average (Avg)">` +
  `<span style="color:#e8c96a">●</span> Now <span style="color:#f59e0b">◆</span> Avg</span>`;

// Standard <th> set for the range columns. Place inside the header <tr>.
export const RANGE_TH =
  `<th data-sort="off" title="Dotted historical range: blue dot = now (percentile), ◆ = 50th">Range %ile<br>${RANGE_LEGEND}</th>` +
  `<th data-sort="off" title="Dotted historical range: blue dot = now (z-score), ◆ = mean">Range z</th>` +
  `<th title="Percentile of current value vs history">%ile</th>` +
  `<th title="Z-score of current value vs history">z</th>`;
