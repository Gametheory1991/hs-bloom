// REFINANCING WALL — bond maturity x rating matrix, refi-stress flags, and
// index-level OAS/YTW by rating and maturity (ICE BofA via FRED).
//
// Two data layers (labeled honestly everywhere):
//  1. CUSIP sample — the terminal's tracked most-active issues (cusip_registry
//     accumulated by finra_corp). Maturity x rating matrix, refi deltas,
//     yearly maturity schedule. A sample, not the full universe.
//  2. Full universe — ICE BofA index OAS / effective yield by rating bucket
//     and by maturity bucket from FRED (macro series). Index-level, daily.
//
// Harry's standing visuals: 1D/1W/1M/1Q/1Y horizons on every comparison,
// Bloomberg dotted range sparklines (percentile + z-score), light theme.
import { getSeries } from "../api.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";

const HY_BUCKETS = new Set(["BB", "B", "CCC"]);

// ---- Bloomberg-style dotted range (local copy of the trace_grid pattern) ----
function rangeDotted(pct, z, zlo, zhi) {
  const w = 120, p = 8;
  const dot = (val, lo, hi, avgVal, avgTip, nowTip) => {
    const span = (hi - lo) || 1;
    const X = (v) => p + Math.max(0, Math.min(1, (v - lo) / span)) * (w - 2 * p);
    const cx = X(val), ax = X(avgVal);
    return `<svg width="${w}" height="20" viewBox="0 0 ${w} 20">` +
      `<line x1="${p}" y1="10" x2="${w - p}" y2="10" style="stroke:var(--line)" stroke-width="2" stroke-dasharray="2,3" stroke-linecap="round"/>` +
      `<polygon points="${ax.toFixed(1)},5 ${(ax + 4.5).toFixed(1)},10 ${ax.toFixed(1)},15 ${(ax - 4.5).toFixed(1)},10" fill="#f5a623"><title>${avgTip}</title></polygon>` +
      `<circle cx="${cx.toFixed(1)}" cy="10" r="5" fill="#2563eb" stroke="#fff" stroke-width="1.5"><title>${nowTip}</title></circle></svg>`;
  };
  const pctSvg = pct == null ? `<span class="muted">—</span>`
    : dot(pct, 0, 100, 50, "50th percentile", `Now: ${pct.toFixed(0)}th percentile`);
  const zSvg = (z == null || zlo == null) ? `<span class="muted">—</span>`
    : dot(z, Math.min(zlo, -0.5), Math.max(zhi, 0.5), 0, "Mean (z = 0)", `Now: z = ${z.toFixed(2)}`);
  return [pctSvg, zSvg];
}

function statsOf(points) {
  // points: [[iso, v], ...] sorted. Returns {now, deltas, pct, z, ...}.
  if (!points || points.length < 2) return null;
  const last = points[points.length - 1];
  const now = last[1];
  const refBack = (days) => {
    const target = Date.parse(last[0]) - days * 864e5;
    let ref = null;
    for (const [d, v] of points) if (Date.parse(d) <= target) ref = v;
    return ref;
  };
  const vs = points.map((p) => p[1]).filter((v) => v != null && isFinite(v));
  if (!vs.length) return null;
  const mean = vs.reduce((a, b) => a + b, 0) / vs.length;
  const sd = Math.sqrt(vs.reduce((a, b) => a + (b - mean) ** 2, 0) / vs.length) || 1e-9;
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const below = vs.filter((v) => v <= now).length;
  return {
    now,
    asof: last[0],
    d1: refBack(1), d7: refBack(7), d30: refBack(30), d91: refBack(91), d365: refBack(365), d1095: refBack(1095),
    pct: (below / vs.length) * 100,
    z: (now - mean) / sd,
    zlo: (lo - mean) / sd, zhi: (hi - mean) / sd,
    lo, hi, mean, n: vs.length,
  };
}

const bp = (x) => x == null || !isFinite(x) ? "—" : `${(x * 100).toFixed(0)}`;
// Nominal (bp) + % change cell for OAS/yield tables.
const bpDelta = (now, ref) => {
  if (now == null || ref == null || !isFinite(now) || !isFinite(ref)) return `<td class="num muted">—</td>`;
  const d = (now - ref) * 100; // bp
  const pct = ref !== 0 ? d / Math.abs(ref * 100) : null; // % of |ref|
  const cls = d > 0.5 ? "up" : d < -0.5 ? "down" : "";
  const s = d > 0 ? "+" : "";
  const bpTxt = `${s}${d.toFixed(0)}bp`;
  const pctTxt = pct == null || !isFinite(pct) ? "—" : `(${s}${(pct * 100).toFixed(1)}%)`;
  return `<td class="num ${cls}"${heatStyle({ pct })}><b>${bpTxt}</b> <span class="muted">${pctTxt}</span></td>`;
};

// ---- index OAS / yield tables (full universe, ICE BofA via FRED) ----
const RATING_ROWS = [
  ["aaa-oas", "aaa-yield", "AAA"], ["aa-oas", "aa-yield", "AA"],
  ["a-oas", "a-yield", "A"], ["bbb-oas", "bbb-yield", "BBB"],
  ["bb-oas", "bb-yield", "BB"], ["b-oas", "b-yield", "B"],
  ["ccc-oas", "ccc-yield", "CCC & lower"],
];
const MAT_ROWS = [
  ["corp-13y-oas", "corp-13y-yield", "1-3Y"], ["corp-35y-oas", "corp-35y-yield", "3-5Y"],
  ["corp-57y-oas", "corp-57y-yield", "5-7Y"], ["corp-710y-oas", "corp-710y-yield", "7-10Y"],
  ["corp-1015y-oas", "corp-1015y-yield", "10-15Y"], ["corp-15py-oas", "corp-15py-yield", "15Y+"],
];

async function oasTable(rows, title, note) {
  const ids = [...new Set(rows.flatMap(([o, y]) => [o, y]))];
  const fetched = await Promise.all(ids.map((id) => getSeries(id, "max").catch(() => null)));
  const byId = Object.fromEntries(ids.map((id, i) => [id, fetched[i]]));
  const pts = (id) => (byId[id]?.points ?? []).slice().sort((a, b) => (a[0] < b[0] ? -1 : 1));
  const body = rows.map(([oasId, yldId, label]) => {
    const so = statsOf(pts(oasId)), sy = statsOf(pts(yldId));
    const [pctSvg, zSvg] = rangeDotted(so?.pct, so?.z, so?.zlo, so?.zhi);
    const yld = sy?.now == null ? "—" : `${sy.now.toFixed(2)}%`;
    const hy = ["BB", "B", "CCC & lower"].includes(label);
    return `<tr><td><b>${label}</b>${hy ? ` <span class="risk-hi-tag">HY</span>` : ""}</td>` +
      `<td class="num"><b>${bp(so?.now)}</b><br><span class="muted">${so?.asof ?? "—"}</span></td>` +
      `<td class="num">${yld}</td>` +
      bpDelta(so?.now, so?.d1) + bpDelta(so?.now, so?.d7) + bpDelta(so?.now, so?.d30) +
      bpDelta(so?.now, so?.d91) + bpDelta(so?.now, so?.d365) + bpDelta(so?.now, so?.d1095) +
      `<td class="num">${so?.pct == null ? "—" : so.pct.toFixed(0)}</td>` +
      `<td>${pctSvg}</td><td>${zSvg}</td></tr>`;
  }).join("");
  return `<h4>${title}</h4><div>${HEAT_LEGEND}</div><div class="tbl-wrap"><table class="wall-tbl" data-sortable>` +
    `<tr><th>Bucket</th><th>OAS bp<br><span class="muted">as of</span></th><th>YTW</th>` +
    `<th>1D Δ</th><th>1W Δ</th><th>1M Δ</th><th>1Q Δ</th><th>1Y Δ</th><th>3Y Δ</th>` +
    `<th>%ile</th><th data-sort="off">Range %ile</th><th data-sort="off">Range z</th></tr>${body}</table></div>` +
    `<p class="muted">${note} Δ cells show bp change (bold) + % change (muted).</p>`;
}

export async function renderOasIndexes() {
  const host = document.getElementById("oas-indexes");
  if (!host) return;
  try {
    host.innerHTML =
      (await oasTable(RATING_ROWS, "DEFAULT RISK — OAS & YTW BY RATING (ICE BofA, FULL UNIVERSE)",
        "Option-adjusted spread in bps; Δ columns are bp changes over 1D/1W/1M/1Q/1Y. Wide OAS = market pricing default risk. " +
        "FRED ICE BofA series carry ~3Y of history (ICE truncated April 2026); percentiles computed over available window.")) +
      (await oasTable(MAT_ROWS, "TERM STRUCTURE OF SPREADS — OAS & YTW BY MATURITY (ICE BofA, FULL UNIVERSE)",
        "Same index family, cut by remaining maturity. Front-end spread widening = near-term refinancing stress."));
  } catch (err) {
    host.innerHTML = `<p class="muted">Index OAS failed to load — ${err.message}</p>`;
  }
}

// ---- CUSIP-sample maturity x rating matrix ----
function riskClass(rb, deltaBps) {
  const hy = HY_BUCKETS.has(rb);
  if (deltaBps == null) return "";
  if (hy && deltaBps > 200) return "risk-hi";
  if (deltaBps > 200 || (hy && deltaBps > 100)) return "risk-mid";
  if (deltaBps <= 50) return "risk-lo";
  return "";
}

export function refiWallSection(wall) {
  if (!wall || !wall.issues) {
    return `<h3>REFINANCING WALL</h3><p class="muted">No tracked issues yet — the registry builds from daily most-active pulls.</p>
      <div id="oas-indexes"><p class="muted">Loading index OAS…</p></div>`;
  }
  const { maturities, ratings, buckets, yearly, issues, as_of } = wall;
  // stress summary
  let stressN = 0, worst = null;
  for (const mb of maturities) for (const rb of ratings) {
    const c = (buckets[mb] || {})[rb];
    if (!c) continue;
    if (c.refi_delta_bps != null && c.refi_delta_bps > 200) {
      stressN += c.n;
      if (!worst || c.refi_delta_bps > worst.d) worst = { mb, rb, d: c.refi_delta_bps, n: c.n };
    }
  }
  const head = `<tr><th>Maturity</th>${ratings.map((r) =>
    `<th>${r}${HY_BUCKETS.has(r) ? ` <span class="risk-hi-tag">HY</span>` : ""}</th>`).join("")}</tr>`;
  const rows = maturities.map((mb) => {
    const tds = ratings.map((rb) => {
      const c = (buckets[mb] || {})[rb];
      if (!c) return `<td class="muted">—</td>`;
      const rc = riskClass(rb, c.refi_delta_bps);
      const d = c.refi_delta_bps;
      const dHtml = d == null ? "—"
        : `<span class="${d > 200 ? "down" : d > 100 ? "warn" : "up"}">${d > 0 ? "+" : ""}${d}bp</span>`;
      return `<td class="${rc}" title="${c.n} issues · avg coupon ${c.avg_coupon}% · avg YTW ${c.avg_ytw}% · G-spread ${c.avg_spread_bps ?? "—"}bp">` +
        `<b>${c.n}</b> <span class="muted">iss</span><br>` +
        `<span class="num">${c.avg_ytw?.toFixed(2) ?? "—"}%</span><br>${dHtml}</td>`;
    }).join("");
    return `<tr><td><b>${mb}</b></td>${tds}</tr>`;
  }).join("");
  // yearly stacked bar: issues maturing per calendar year, IG vs HY
  const ymax = Math.max(1, ...yearly.map((y) => y.ig + y.hy));
  const bw = 44, gap = 10, H = 150;
  const bars = yearly.map((y, i) => {
    const tot = y.ig + y.hy;
    const h = Math.max(2, (tot / ymax) * H);
    const hig = (y.ig / tot) * h, hhy = h - hig;
    const x = i * (bw + gap);
    return `<g><title>${y.year}: ${tot} issues (${y.ig} IG / ${y.hy} HY)</title>` +
      `<rect x="${x}" y="${(H - h).toFixed(1)}" width="${bw}" height="${hig.toFixed(1)}" fill="#2563eb"/>` +
      `<rect x="${x}" y="${(H - hhy).toFixed(1)}" width="${bw}" height="${hhy.toFixed(1)}" fill="#dc2626"/>` +
      `<text x="${x + bw / 2}" y="${H + 14}" text-anchor="middle" font-size="10" fill="var(--muted)">${y.year}</text>` +
      `<text x="${x + bw / 2}" y="${(H - h - 4).toFixed(1)}" text-anchor="middle" font-size="10" font-weight="bold">${tot}</text></g>`;
  }).join("");
  const chartW = yearly.length * (bw + gap);
  return `<h3>REFINANCING WALL <span class="muted">as of ${as_of} · ${issues} tracked issues (most-active sample — not the full universe)</span></h3>
    <p>${stressN ? `<span class="risk-hi-tag">REFI STRESS: ${stressN} issues</span> would refinance &gt;200bp above their coupon` : `<span class="risk-lo-tag">no bucket &gt;200bp over coupon</span>`}
    ${worst ? ` · worst: <b>${worst.rb} ${worst.mb}</b> at +${worst.d}bp (${worst.n} issues)` : ""}</p>
    <div class="tbl-wrap"><table class="wall-tbl"><thead>${head}</thead><tbody>${rows}</tbody></table></div>
    <p class="muted">Each cell: <b>issue count</b> · avg YTW · <b>refi Δ</b> = avg YTW − avg coupon (what maturing bonds would pay to refinance today vs what they pay now). ` +
    `Cell color: <span class="risk-hi-tag">red</span> = HY + &gt;200bp (refinance wall / likely shut out), ` +
    `<span class="risk-mid-tag">amber</span> = &gt;200bp or HY &gt;100bp, <span class="risk-lo-tag">green</span> = ≤50bp. Hover a cell for coupon/spread detail.</p>
    <h4>MATURITY SCHEDULE — TRACKED ISSUES MATURING PER YEAR (IG <span style="color:#2563eb">■</span> / HY <span style="color:#dc2626">■</span>)</h4>
    <svg width="${chartW}" height="${H + 22}" viewBox="0 0 ${chartW} ${H + 22}" role="img">${bars}</svg>
    <p class="muted">Issue counts from the tracked-issue registry (accumulates from daily most-active pulls; grows over time). Full-universe par-by-maturity is not in any free feed.</p>
    <div id="oas-indexes"><p class="muted">Loading index OAS…</p></div>`;
}
