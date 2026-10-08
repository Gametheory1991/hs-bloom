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
 *  for use with tradingX axes. bands: [[tsA, tsB], ...] (unix seconds). */
export function bandsToIndices(bands, dateStrs) {
  const ts = dateStrs.map((d) => Date.parse(d) / 1000);
  const out = [];
  for (const [a, b] of bands) {
    let i0 = ts.findIndex((t) => t >= a);
    let i1 = ts.length - 1 - [...ts].reverse().findIndex((t) => t <= b);
    if (i0 < 0) i0 = 0;
    if (i1 < 0) i1 = ts.length - 1;
    if (i1 >= i0) out.push([i0, i1]);
  }
  return out;
}
