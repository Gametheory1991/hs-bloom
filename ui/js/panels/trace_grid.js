// Bloomberg-style TRACE volume grid: delta heatmap + 3Y range dot plots + sparklines.
// Mounted by renderFinra() into #trace-grid-wrap. Data via /api/series (no cycle: prefix).
// ADV for TRACE monthly products = monthly par ($M) / NYSE trading days / 1000.
// Trading-day calendar verified 116/117 vs canonical CSV (only miss: 2018-12-05
// Bush national day of mourning, special-cased below).
// Rows: TOTAL (Treasury + all 10 TRACE products) first per Harry's standing rule,
// then Treasury total + breakdowns (bills/coupons/TIPS/FRNs, on-the-run/off-the-run),
// the 10 TRACE products, then FINRA short-interest levels with MoM/YoY deltas.
import { getSeries } from "../api.js";
import { setTraceChartProduct } from "./trace_charts.js";
import { OUTSTANDING, totalOutstanding, OUTSTANDING_NOTE } from "./outstanding.js";

const PRODUCTS = [
  { id: "total", label: "TOTAL (Treasury + TRACE)", synthetic: true },
  { id: "ust",  label: "Treasury Total", par: "trace-ust-par",  trades: "trace-ust-trades",  monthly: false },
  { id: "ust-bills",   label: "Treasury — Bills",      par: "trace-ust-bills-par",   monthly: false },
  { id: "ust-coupons", label: "Treasury — Nom Coupons", par: "trace-ust-coupons-par", monthly: false },
  { id: "ust-tips",    label: "Treasury — TIPS",       par: "trace-ust-tips-par",    monthly: false },
  { id: "ust-frns",    label: "Treasury — FRNs",       par: "trace-ust-frns-par",    monthly: false },
  { id: "ust-onrun",   label: "Treasury — On-the-run", par: "trace-ust-onrun-par",   monthly: false },
  { id: "ust-offrun",  label: "Treasury — Off-the-run", par: "trace-ust-offrun-par", monthly: false },
  { id: "tba",  label: "TBA",            par: "trace-tba-par",  trades: null,                 monthly: true  },
  { id: "corp", label: "Corporate",      par: "trace-corp-par", trades: "trace-corp-trades", monthly: true  },
  { id: "eln",  label: "ELN",            par: "trace-eln-par",  trades: "trace-eln-trades",   monthly: true  },
  { id: "conv", label: "Convertibles",   par: "trace-conv-par", trades: "trace-conv-trades", monthly: true  },
  { id: "agcy", label: "Agency",         par: "trace-agcy-par", trades: null,                 monthly: true  },
  { id: "abs",  label: "ABS",            par: "trace-abs-par",  trades: null,                 monthly: true  },
  { id: "absx", label: "ABSX",           par: "trace-absx-par", trades: null,                 monthly: true  },
  { id: "cmo",  label: "CMO",            par: "trace-cmo-par",  trades: null,                 monthly: true  },
  { id: "mbs",  label: "MBS",            par: "trace-mbs-par",  trades: null,                 monthly: true  },
  { id: "chrc", label: "Church",         par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true  },
  // Short interest: biweekly settlement levels (shares), not rates — no
  // trading-day division; deltas are true MoM (vs ~30d prior point) and
  // YoY (vs ~365d prior point) since adjacent points are 2 weeks apart.
  { id: "si-total", label: "Short Int — Total", par: "finra-short-total", unit: "sh", raw: true },
  { id: "si-msft",  label: "Short Int — MSFT",  par: "short-MSFT",  unit: "sh", raw: true },
  { id: "si-nvda",  label: "Short Int — NVDA",  par: "short-NVDA",  unit: "sh", raw: true },
  { id: "si-aapl",  label: "Short Int — AAPL",  par: "short-AAPL",  unit: "sh", raw: true },
  { id: "si-amzn",  label: "Short Int — AMZN",  par: "short-AMZN",  unit: "sh", raw: true },
  { id: "si-googl", label: "Short Int — GOOGL", par: "short-GOOGL", unit: "sh", raw: true },
  { id: "si-meta",  label: "Short Int — META",  par: "short-META",  unit: "sh", raw: true },
];
// Components summed into the synthetic TOTAL row (Treasury + all 10 products).
const TOTAL_PARTS = PRODUCTS.filter((p) => !p.synthetic && !p.raw);

// ---- NYSE trading-day calendar ----
const ONE_OFF_CLOSURES = new Set(["2018-12-05"]); // G.H.W. Bush national day of mourning
function easterSunday(y) {
  const a = y % 19, b = (y / 100) | 0, c = y % 100, d = (b / 4) | 0, e = b % 4,
        f = ((b + 8) / 25) | 0, g = ((b - f + 1) / 3) | 0,
        h = (19 * a + b - d - g + 15) % 30, i = (c / 4) | 0, k = c % 4,
        l = (32 + 2 * e + 2 * i - h - k) % 7, m = ((a + 11 * h + 22 * l) / 451) | 0;
  const mo = ((h + l - 7 * m + 114) / 31) | 0, da = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(y, mo - 1, da));
}
function nthWeekday(y, mo, wd, n) { // mo 0-based; n>=1, -1 = last
  if (n > 0) {
    const d = new Date(Date.UTC(y, mo, 1));
    const off = (wd - d.getUTCDay() + 7) % 7;
    return new Date(Date.UTC(y, mo, 1 + off + 7 * (n - 1)));
  }
  const last = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
  const d = new Date(Date.UTC(y, mo, last));
  return new Date(Date.UTC(y, mo, last - ((d.getUTCDay() - wd + 7) % 7)));
}
const iso = (d) => d.toISOString().slice(0, 10);
function observed(dt, isNewYear) {
  const w = dt.getUTCDay();
  if (w === 6) {
    // NYSE precedent: a Saturday Jan 1 is NOT observed (Dec 31, 2021 traded).
    // Verified against canonical TRACE history: 2020-07-03 and 2021-12-24 closed.
    if (isNewYear) return null;
    return new Date(dt.getTime() - 864e5);
  }
  if (w === 0) return new Date(dt.getTime() + 864e5);
  return dt;
}
function nyseHolidays(y) {
  const s = new Set();
  const add = (d) => s.add(iso(d));
  const ny = observed(new Date(Date.UTC(y, 0, 1)), true);
  if (ny) add(ny);
  add(nthWeekday(y, 0, 1, 3)); add(nthWeekday(y, 1, 1, 3));
  add(new Date(easterSunday(y).getTime() - 2 * 864e5));
  add(nthWeekday(y, 4, 1, -1));
  if (y >= 2022) add(observed(new Date(Date.UTC(y, 5, 19))));
  add(observed(new Date(Date.UTC(y, 6, 4))));
  add(nthWeekday(y, 8, 1, 1));
  add(nthWeekday(y, 10, 4, 4));
  add(observed(new Date(Date.UTC(y, 11, 25))));
  return s;
}
function tradingDays(y, m) { // m 1-based
  const hol = new Set();
  for (const yy of [y - 1, y, y + 1]) for (const d of nyseHolidays(yy)) hol.add(d);
  const n = new Date(Date.UTC(y, m, 0)).getUTCDate();
  let c = 0;
  for (let d = 1; d <= n; d++) {
    const dt = new Date(Date.UTC(y, m - 1, d));
    if (dt.getUTCDay() === 0 || dt.getUTCDay() === 6) continue;
    const s = iso(dt);
    if (hol.has(s) || ONE_OFF_CLOSURES.has(s)) continue;
    c++;
  }
  return c;
}
const isMonthEnd = (d) => {
  const [y, m, dd] = d.split("-").map(Number);
  return dd === new Date(Date.UTC(y, m, 0)).getUTCDate();
};

// ---- data shaping ----
// Monthly ADV ($B/day) from raw points. Treasury rows (monthly:false) use
// month-end points only (monthly-file values are monthly TOTALS in $bn).
export function toAdv(p, points) {
  if (!p.monthly) return points.filter(([d]) => isMonthEnd(d)).map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) };
  });
  return points.map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) / 1000 }; // $M monthly -> $B/day
  });
}
export function toAdt(p, points) {
  const src = p.monthly ? points : points.filter(([d]) => isMonthEnd(d));
  return src.map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) };
  });
}
const DAY_MS = 864e5;
const dayDiff = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / DAY_MS);
// Harry's universal horizon standard (2026-10-05): every % change comparison
// shows 1D/1W/1M/1Q/1Y. Monthly/biweekly rows can't support 1D/1W — those
// cells render "—" (see rowStats/rowStatsLevel).
export function rowStats(vals) { // vals sorted asc by d — monthly ADV/ADT series
  const n = vals.length;
  if (!n) return null;
  const cur = vals[n - 1];
  const back = (k) => (n > k ? vals[n - 1 - k] : null);
  const ym = cur.d.slice(0, 7);
  const yoyKey = `${+ym.slice(0, 4) - 1}${ym.slice(4)}`;
  const yoyPt = vals.find((v) => v.d.slice(0, 7) === yoyKey);
  return finishStats(vals, cur, { d1: null, w1: null, m1: back(1), q1: back(3), y1: yoyPt });
}
export function rowStatsLevel(vals) { // vals sorted asc — raw level series (biweekly short interest)
  const n = vals.length;
  if (!n) return null;
  const cur = vals[n - 1];
  // nearest prior point at least N days back (adjacent points are 2 weeks apart)
  const refBack = (days) => {
    let ref = null;
    for (const v of vals) {
      if (v.d < cur.d && dayDiff(v.d, cur.d) >= days) ref = v;
    }
    return ref;
  };
  return finishStats(vals, cur, { d1: null, w1: null, m1: refBack(30), q1: refBack(91), y1: refBack(365) });
}
export function finishStats(vals, cur, refs) {
  const win = vals.slice(-36);
  const vs = win.map((v) => v.v);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const avg = vs.reduce((a, b) => a + b, 0) / vs.length;
  const sd = Math.sqrt(vs.reduce((a, b) => a + (b - avg) ** 2, 0) / vs.length);
  const z = sd > 0 ? (cur.v - avg) / sd : null;
  const zs = sd > 0 ? vs.map((v) => (v - avg) / sd) : vs.map(() => 0);
  const rank = vs.filter((v) => v <= cur.v).length;
  const rc = (r) => (r && r.v ? (cur.v - r.v) / r.v : null);
  // 52w hi/lo: last 12 monthly points of the 3Y window.
  const w52 = vals.slice(-12).map((v) => v.v);
  return {
    cur, lo, hi, avg, sd, z, zlo: Math.min(...zs), zhi: Math.max(...zs),
    win, n: vs.length, asof: cur.d,
    hi52: w52.length ? Math.max(...w52) : null,
    lo52: w52.length ? Math.min(...w52) : null,
    d1: rc(refs.d1), w1: rc(refs.w1), m1: rc(refs.m1), q1: rc(refs.q1), y1: rc(refs.y1),
    d3: avg ? (cur.v - avg) / avg : null,
    pct: (100 * rank) / vs.length,
  };
}
// Synthetic TOTAL: sum monthly ADV (or ADT) of Treasury + all 10 TRACE
// products by calendar month. ADT only sums products with trade-count data
// (tba/agcy/abs/absx/cmo/mbs publish no trade counts).
function totalVals(data, metric) {
  const sums = new Map(); // "YYYY-MM" -> {d, v}
  for (const { p, par, tr } of data) {
    if (TOTAL_PARTS.indexOf(p) < 0) continue;
    const src = metric === "adt" ? tr : par;
    if (!src) continue;
    const vals = metric === "adt" ? toAdt(p, src) : toAdv(p, src);
    for (const { d, v } of vals) {
      const key = d.slice(0, 7);
      const e = sums.get(key);
      if (e) { e.v += v; if (d > e.d) e.d = d; }
      else sums.set(key, { d, v });
    }
  }
  return [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
}

// ---- formatting ----
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const mlabel = (d) => `${MONTHS[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`;
const fmtB = (v) => v == null || !isFinite(v) ? "—" : v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
const fmtN = (v) => v == null || !isFinite(v) ? "—" : Math.round(v).toLocaleString("en-US");
const fmtSh = (v) => v == null || !isFinite(v) ? "—" :
  v >= 1e12 ? (v / 1e12).toFixed(2) + "T sh" :
  v >= 1e9 ? (v / 1e9).toFixed(2) + "B sh" :
  v >= 1e6 ? (v / 1e6).toFixed(1) + "M sh" : fmtN(v) + " sh";
const pct1 = (x) => x == null || !isFinite(x) ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const heat = (x) => {
  if (x == null || !isFinite(x)) return "";
  const a = Math.min(Math.abs(x) / 0.25, 1) * 0.45;
  return ` style="background:rgba(${x >= 0 ? "22,163,74" : "220,38,38"},${a.toFixed(2)})"`;
};

// Bloomberg-style dotted range sparkline (Harry's standing visual, 2026-10-05):
// horizontal dotted line spanning the 3Y range, blue dot = current value,
// orange diamond = historical average. Two versions: percentile scale and
// z-score scale.
export function rangePlotDotted(s, mode) {
  const w = 130, p = 8;
  const isPct = mode === "pct";
  const lo = isPct ? 0 : Math.min(s.zlo, -0.5);
  const hi = isPct ? 100 : Math.max(s.zhi, 0.5);
  const span = (hi - lo) || 1;
  const X = (v) => p + Math.max(0, Math.min(1, (v - lo) / span)) * (w - 2 * p);
  const curPos = isPct ? s.pct : (s.z ?? 0);
  const avgPos = isPct ? 50 : 0;
  const cx = X(curPos), ax = X(avgPos);
  const tip = isPct ? `Now: ${s.pct.toFixed(0)}th percentile of 3Y window`
                    : `Now: z = ${s.z == null ? "—" : s.z.toFixed(2)} (3Y)`;
  return `<svg class="trng" width="${w}" height="20" viewBox="0 0 ${w} 20">` +
    `<line x1="${p}" y1="10" x2="${w - p}" y2="10" style="stroke:var(--line)" stroke-width="2" stroke-dasharray="2,3" stroke-linecap="round"/>` +
    `<polygon points="${ax.toFixed(1)},5 ${(ax + 4.5).toFixed(1)},10 ${ax.toFixed(1)},15 ${(ax - 4.5).toFixed(1)},10" fill="#f5a623"><title>${isPct ? "50th percentile" : "Mean (z = 0)"}</title></polygon>` +
    `<circle cx="${cx.toFixed(1)}" cy="10" r="5" fill="#2563eb" stroke="#fff" stroke-width="1.5"><title>${tip}</title></circle>` +
    `</svg>`;
}
function sparkline(win) {
  if (!win || win.length < 2) return `<span class="muted">—</span>`;
  const w = 120, h = 34;
  const vs = win.map((p) => p.v);
  const lo = Math.min(...vs), hi = Math.max(...vs), rg = (hi - lo) || 1;
  const pts = win.map((p, i) =>
    `${(i / (win.length - 1) * w).toFixed(1)},${(h - 3 - ((p.v - lo) / rg) * (h - 6)).toFixed(1)}`).join(" ");
  const up = vs[vs.length - 1] >= vs[0];
  return `<svg class="tspark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<polyline points="${pts}" fill="none" style="stroke:${up ? "var(--up)" : "var(--down)"}" stroke-width="1.5"/></svg>`;
}

// ---- grid ----
const state = { metric: "adv", sortKey: null, sortDir: 1, rows: [], asof: null, reqId: 0 };

const COLS = [
  { key: "label", title: "Product", num: false },
  { key: "cur",   title: "ADV $B/d", num: true },
  { key: "out",   title: "Outst $T", num: true, tip: "Par outstanding — hover each row's value for source/as-of" },
  { key: "turn",  title: "Turnov ann.%", num: true, tip: "ADV × 252 ÷ outstanding (annualized %). ADV view only." },
  { key: "d1", title: "1D %", num: true, heat: true, tip: "1-day % change (n/a for monthly data)" },
  { key: "w1", title: "1W %", num: true, heat: true, tip: "1-week % change (n/a for monthly data)" },
  { key: "m1", title: "1M %", num: true, heat: true, tip: "1-month % change" },
  { key: "q1", title: "1Q %", num: true, heat: true, tip: "3-month % change" },
  { key: "y1", title: "1Y %", num: true, heat: true, tip: "12-month % change" },
  { key: "d3",    title: "Δ 3Y avg %", num: true, heat: true },
  { key: "rngpct", title: "Range %ile", num: false, tip: "Dotted 3Y range: blue dot = now (percentile), ◆ = 50th pct" },
  { key: "rngz", title: "Range z", num: false, tip: "Dotted 3Y range: blue dot = now (z-score), ◆ = mean (z=0)" },
  { key: "hi52",  title: "52w Hi", num: true, tip: "Highest monthly ADV/ADT in the last 12 months" },
  { key: "lo52",  title: "52w Lo", num: true, tip: "Lowest monthly ADV/ADT in the last 12 months" },
  { key: "lo",    title: "Low", num: true, tip: "3Y window low" },
  { key: "hi",    title: "High", num: true, tip: "3Y window high" },
  { key: "avg",   title: "Avg", num: true, tip: "3Y window average" },
  { key: "pct",   title: "RS", num: true, tip: "Relative-strength rank: percentile of current value vs 3Y history (0-100)" },
  { key: "z",     title: "z", num: true, tip: "Current z-score vs 3Y window" },
  { key: "trend", title: "Trend (3Y)", num: false },
];

async function loadData() {
  const jobs = PRODUCTS.filter((p) => !p.synthetic).map(async (p) => {
    const [par, tr] = await Promise.all([
      getSeries(p.par, "max"),
      p.trades ? getSeries(p.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    return { p, par: par.points, tr: tr ? tr.points : null };
  });
  return Promise.all(jobs);
}

function buildRows(data, metric) {
  const rows = data.map(({ p, par, tr }) => {
    if (p.raw) {
      const vals = par.map(([d, v]) => ({ d, v }))
        .sort((a, b) => (a.d < b.d ? -1 : 1));
      return { p, stats: rowStatsLevel(vals), out: null };
    }
    const src = metric === "adt" ? tr : par;
    if (!src) return { p, stats: null, out: OUTSTANDING[p.id] || null };
    const vals = (metric === "adt" ? toAdt(p, src) : toAdv(p, src))
      .sort((a, b) => (a.d < b.d ? -1 : 1));
    return { p, stats: rowStats(vals), out: OUTSTANDING[p.id] || null };
  });
  const tot = PRODUCTS.find((p) => p.synthetic);
  const totOut = totalOutstanding(TOTAL_PARTS.map((p) => p.id));
  rows.unshift({
    p: tot,
    stats: rowStats(totalVals(data, metric)),
    out: { amt: totOut.amt, asof: "mixed", src: `sum of component floats (${totOut.parts.length} products; agency-MBS float counted once)` },
  });
  return rows;
}

function turnVal(r) { // annualized turnover % = ADV × 252 ÷ outstanding; ADV view only
  if (state.metric !== "adv" || !r.stats || !r.out || r.out.amt == null) return null;
  return (r.stats.cur.v * 252 / r.out.amt) * 100;
}

function sortRows(rows) {
  // Harry's standing rule: the combined TOTAL row is always first.
  const total = rows.filter((r) => r.p.synthetic);
  const rest = rows.filter((r) => !r.p.synthetic);
  if (!state.sortKey || state.sortKey === "label") return [...total, ...rest];
  const k = state.sortKey, dir = state.sortDir;
  const val = (r) => {
    if (!r.stats) return -Infinity;
    switch (k) {
      case "cur": return r.stats.cur.v;
      case "out": return r.out?.amt ?? -Infinity;
      case "turn": return turnVal(r) ?? -Infinity;
      case "d1": return r.stats.d1 ?? -Infinity;
      case "w1": return r.stats.w1 ?? -Infinity;
      case "m1": return r.stats.m1 ?? -Infinity;
      case "q1": return r.stats.q1 ?? -Infinity;
      case "y1": return r.stats.y1 ?? -Infinity;
      case "d3": return r.stats.d3 ?? -Infinity;
      case "pct": return r.stats.pct;
      case "z": return r.stats.z ?? -Infinity;
      case "hi52": return r.stats.hi52 ?? -Infinity;
      case "lo52": return r.stats.lo52 ?? -Infinity;
      case "lo": return r.stats.lo; case "hi": return r.stats.hi; case "avg": return r.stats.avg;
      default: return -Infinity;
    }
  };
  return [...total, ...[...rest].sort((a, b) => (val(a) - val(b)) * dir)];
}

function renderTable() {
  const wrap = document.getElementById("trace-grid-wrap");
  if (!wrap) return;
  const isAdt = state.metric === "adt";
  const unit = isAdt ? "trades/d" : "$B/d";
  const fmtFor = (p) => p.unit === "sh" ? fmtSh : (isAdt ? fmtN : fmtB);
  const head = COLS.map((c) => {
    const title = c.key === "cur" ? (isAdt ? "ADT" : "ADV $B/d") : c.title;
    const arrow = state.sortKey === c.key ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
    const tip = c.tip ? ` title="${c.tip}"` : ` title="Sort by ${title}"`;
    return `<th data-sort="${c.key}" class="${c.num ? "num" : ""}"${tip}>${title}${arrow}</th>`;
  }).join("");
  const rows = sortRows(state.rows).map((r) => {
    const { p, stats: s } = r;
    const fmt = fmtFor(p);
    const cls = p.synthetic ? ` class="total-row"` : "";
    if (!s) return `<tr${cls}><td><b>${p.label}</b></td><td colspan="19" class="muted">no ${isAdt ? "trade-count" : "par"} data</td></tr>`;
    const o = r.out;
    const outCell = o && o.amt != null
      ? `<td class="num" title="${o.src}${o.asof ? ` (as of ${o.asof})` : ""}">$${(o.amt / 1000).toFixed(1)}T</td>`
      : `<td class="num muted" title="${o ? o.src : "n/a"}">—</td>`;
    const tv = turnVal(r);
    const turnCell = tv == null
      ? `<td class="num muted"${isAdt ? ` title="Turnover is par-based (ADV view only)"` : ""}>—</td>`
      : `<td class="num" title="ADV × 252 ÷ outstanding (annualized)">${tv >= 100 ? tv.toFixed(0) : tv.toFixed(1)}%</td>`;
    return `<tr data-pid="${p.id}" title="Click to view ${p.label} chart"${cls}>` +
      `<td><b>${p.label}</b></td>` +
      `<td class="num">${fmt(s.cur.v)}</td>` +
      outCell + turnCell +
      `<td class="num"${heat(s.d1)}>${pct1(s.d1)}</td>` +
      `<td class="num"${heat(s.w1)}>${pct1(s.w1)}</td>` +
      `<td class="num"${heat(s.m1)}>${pct1(s.m1)}</td>` +
      `<td class="num"${heat(s.q1)}>${pct1(s.q1)}</td>` +
      `<td class="num"${heat(s.y1)}>${pct1(s.y1)}</td>` +
      `<td class="num"${heat(s.d3)}>${pct1(s.d3)}</td>` +
      `<td>${rangePlotDotted(s, "pct")}</td>` +
      `<td>${rangePlotDotted(s, "z")}</td>` +
      `<td class="num">${fmt(s.hi52)}</td>` +
      `<td class="num">${fmt(s.lo52)}</td>` +
      `<td class="num">${fmt(s.lo)}</td>` +
      `<td class="num">${fmt(s.hi)}</td>` +
      `<td class="num">${fmt(s.avg)}</td>` +
      `<td class="num">${s.pct.toFixed(0)}</td>` +
      `<td class="num">${s.z == null ? "—" : (s.z >= 0 ? "+" : "") + s.z.toFixed(2)}</td>` +
      `<td>${sparkline(s.win)}</td></tr>`;
  }).join("");
  wrap.querySelector("table.trace-grid tbody").innerHTML = rows;
  wrap.querySelectorAll("table.trace-grid th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = k === "label" ? 1 : -1; }
      renderTable();
    }));
  wrap.querySelectorAll("table.trace-grid tr[data-pid]").forEach((tr) =>
    tr.addEventListener("click", () => {
      setTraceChartProduct(tr.dataset.pid);
      document.getElementById("trace-view-chart")?.click();
      document.getElementById("trace-chart-wrap")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  const asofEl = wrap.querySelector("#trace-grid-asof");
  if (asofEl && state.asof) asofEl.textContent =
    `as of ${mlabel(state.asof)} · ${unit} · 3Y window (● = now, ◆ = avg/50th pct) · Treasury history from Feb 2023 · ` +
    `1D/1W n/a on monthly rows · ADT total sums products with trade-count data · ${OUTSTANDING_NOTE}`;
}

export function renderTraceGrid() {
  const wrap = document.getElementById("trace-grid-wrap");
  if (!wrap || wrap.dataset.init) return;
  wrap.dataset.init = "1";
  const reqId = ++state.reqId;
  wrap.innerHTML = `
    <div class="trace-controls">
      <span class="seg" id="trace-grid-metric">
        <button data-m="adv" class="on">ADV</button><button data-m="adt">ADT</button>
      </span>
      <span class="muted" id="trace-grid-asof">Loading…</span>
    </div>
    <table class="trace-grid"><thead><tr>${
      COLS.map((c) => `<th data-sort="${c.key}" class="${c.num ? "num" : ""}">${c.key === "cur" ? "ADV $B/d" : c.title}</th>`).join("")
    }</tr></thead><tbody><tr><td colspan="20" class="muted">Loading TRACE history…</td></tr></tbody></table>`;
  wrap.querySelectorAll("#trace-grid-metric button").forEach((b) =>
    b.addEventListener("click", async () => {
      if (state.metric === b.dataset.m) return;
      state.metric = b.dataset.m;
      wrap.querySelectorAll("#trace-grid-metric button").forEach((x) => x.classList.toggle("on", x === b));
      await refresh();
    }));
  loadData().then((data) => {
    if (reqId !== state.reqId) return;
    wrap.dataset.raw = "1";
    wrap._rawData = data;
    const advRows = buildRows(data, "adv");
    const mo = advRows.map((r) => r.stats?.asof).filter(Boolean).sort().pop();
    state.asof = mo || null;
    refresh();
  }).catch((err) => {
    if (reqId !== state.reqId) return;
    wrap.querySelector("tbody").innerHTML =
      `<tr><td colspan="20" class="muted">Failed to load grid — ${err.message}</td></tr>`;
  });

  async function refresh() {
    const data = wrap._rawData;
    if (!data) return;
    state.rows = buildRows(data, state.metric);
    // update the ADV/ADT header label
    const th = wrap.querySelector('th[data-sort="cur"]');
    if (th) th.textContent = state.metric === "adt" ? "ADT" : "ADV $B/d";
    renderTable();
  }
  wrap._refresh = refresh;
}
