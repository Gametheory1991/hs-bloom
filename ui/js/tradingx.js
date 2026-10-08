// ui/js/tradingx.js — trading-day-only x-axis for uPlot
/** Ordinal x positions for trading-day-only axes.
 *  dateStrs: ["2026-10-05", ...] in display order.
 *  Returns { x, values } — x goes in the uPlot data array,
 *  values goes on the x-axis config. */
export function tradingX(dateStrs, fmt = (d) => d.slice(5).replace("-", "/")) {
  const n = dateStrs.length;
  return {
    x: Array.from({ length: n }, (_, i) => i),
    values: (u, vals) =>
      vals.map((v) => {
        const i = Math.round(v);
        return i >= 0 && i < n ? fmt(dateStrs[i]) : "";
      }),
  };
}

/** Map recession-band timestamp ranges to ordinal index ranges
 *  for use with tradingX axes. bands: [[tsA, tsB], ...] (unix seconds).
 *  Bands lying entirely outside the data window are skipped (not clamped
 *  to the full window — clamping stacked every historical recession into
 *  a solid blue field that buried the data line). */
export function bandsToIndices(bands, dateStrs) {
  const ts = dateStrs.map((d) => Date.parse(d) / 1000);
  const out = [];
  for (const [a, b] of bands) {
    const fwd = ts.findIndex((t) => t >= a);
    const rev = [...ts].reverse().findIndex((t) => t <= b);
    if (fwd < 0 || rev < 0) continue; // no overlap with the window
    const i0 = fwd;
    const i1 = ts.length - 1 - rev;
    if (i1 >= i0) out.push([i0, i1]);
  }
  return out;
}
