// Bloomberg-style TRACE volume grid: delta heatmap + 3Y range dot plots + sparklines.
// Mounted by renderFinra() into #trace-grid-wrap. Data via /api/series (no cycle: prefix).
// ADV for TRACE monthly products = monthly par ($M) / NYSE trading days / 1000.
// Trading-day calendar verified 116/117 vs canonical CSV (only miss: 2018-12-05
// Bush national day of mourning, special-cased below).
import { getSeries } from "../api.js";
import { setTraceChartProduct } from "./trace_charts.js";

const PRODUCTS = [
  { id: "ust",  label: "Treasury Total", par: "trace-ust-par",  trades: "trace-ust-trades",  monthly: false },
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
];

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
function toAdv(p, points) {
  // Treasury monthly-file values are monthly TOTALS ($bn); daily-file values are
  // already $/day but are excluded here (grid is monthly: month-end points only).
  if (!p.monthly) return points.filter(([d]) => isMonthEnd(d)).map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) };
  });
  return points.map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) / 1000 }; // $M monthly -> $B/day
  });
}
function toAdt(p, points) {
  const src = p.monthly ? points : points.filter(([d]) => isMonthEnd(d));
  return src.map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) };
  });
}
function rowStats(vals) { // vals sorted asc by d
  const n = vals.length;
  if (!n) return null;
  const cur = vals[n - 1];
  const prev = n > 1 ? vals[n - 2] : null;
  const ym = cur.d.slice(0, 7);
  const yoyKey = `${+ym.slice(0, 4) - 1}${ym.slice(4)}`;
  const yoyPt = vals.find((v) => v.d.slice(0, 7) === yoyKey);
  const win = vals.slice(-36);
  const vs = win.map((v) => v.v);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const avg = vs.reduce((a, b) => a + b, 0) / vs.length;
  const rank = vs.filter((v) => v <= cur.v).length;
  return {
    cur, lo, hi, avg, win, n: vs.length, asof: cur.d,
    mom: prev && prev.v ? (cur.v - prev.v) / prev.v : null,
    yoy: yoyPt && yoyPt.v ? (cur.v - yoyPt.v) / yoyPt.v : null,
    d3: avg ? (cur.v - avg) / avg : null,
    pct: (100 * rank) / vs.length,
  };
}

// ---- formatting ----
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const mlabel = (d) => `${MONTHS[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`;
const fmtB = (v) => v == null || !isFinite(v) ? "—" : v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
const fmtN = (v) => v == null || !isFinite(v) ? "—" : Math.round(v).toLocaleString("en-US");
const pct1 = (x) => x == null || !isFinite(x) ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const heat = (x) => {
  if (x == null || !isFinite(x)) return "";
  const a = Math.min(Math.abs(x) / 0.25, 1) * 0.45;
  return ` style="background:rgba(${x >= 0 ? "22,163,74" : "220,38,38"},${a.toFixed(2)})"`;
};

function rangePlot(s, fmt) {
  const w = 150, p = 6;
  const span = (s.hi - s.lo) || 1;
  const X = (v) => p + Math.max(0, Math.min(1, (v - s.lo) / span)) * (w - 2 * p);
  const ax = X(s.avg), cx = X(s.cur.v);
  return `<svg class="trng" width="${w}" height="20" viewBox="0 0 ${w} 20">` +
    `<line x1="${p}" y1="10" x2="${w - p}" y2="10" style="stroke:var(--line)" stroke-width="3" stroke-linecap="round"/>` +
    `<polygon points="${ax.toFixed(1)},5 ${(ax + 4.5).toFixed(1)},10 ${ax.toFixed(1)},15 ${(ax - 4.5).toFixed(1)},10" fill="#f5a623"><title>Avg ${fmt(s.avg)}</title></polygon>` +
    `<circle cx="${cx.toFixed(1)}" cy="10" r="5" fill="#2563eb" stroke="#fff" stroke-width="1.5"><title>Current ${fmt(s.cur.v)} (${mlabel(s.asof)})</title></circle>` +
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
  { key: "mom",   title: "MoM %", num: true, heat: true },
  { key: "yoy",   title: "YoY %", num: true, heat: true },
  { key: "d3",    title: "Δ 3Y avg %", num: true, heat: true },
  { key: "range", title: "3Y range", num: false },
  { key: "lo",    title: "Low", num: true },
  { key: "hi",    title: "High", num: true },
  { key: "avg",   title: "Avg", num: true },
  { key: "pct",   title: "%ile", num: true },
  { key: "trend", title: "Trend (3Y)", num: false },
];

async function loadData() {
  const jobs = PRODUCTS.map(async (p) => {
    const [par, tr] = await Promise.all([
      getSeries(p.par, "max"),
      p.trades ? getSeries(p.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    return { p, par: par.points, tr: tr ? tr.points : null };
  });
  return Promise.all(jobs);
}

function buildRows(data, metric) {
  return data.map(({ p, par, tr }) => {
    const src = metric === "adt" ? tr : par;
    if (!src) return { p, stats: null };
    const vals = (metric === "adt" ? toAdt(p, src) : toAdv(p, src))
      .sort((a, b) => (a.d < b.d ? -1 : 1));
    return { p, stats: rowStats(vals) };
  });
}

function sortRows(rows) {
  if (!state.sortKey || state.sortKey === "label") return rows;
  const k = state.sortKey, dir = state.sortDir;
  const val = (r) => {
    if (!r.stats) return -Infinity;
    switch (k) {
      case "cur": return r.stats.cur.v;
      case "mom": return r.stats.mom ?? -Infinity;
      case "yoy": return r.stats.yoy ?? -Infinity;
      case "d3": return r.stats.d3 ?? -Infinity;
      case "pct": return r.stats.pct;
      case "lo": return r.stats.lo; case "hi": return r.stats.hi; case "avg": return r.stats.avg;
      default: return -Infinity;
    }
  };
  return [...rows].sort((a, b) => (val(a) - val(b)) * dir);
}

function renderTable() {
  const wrap = document.getElementById("trace-grid-wrap");
  if (!wrap) return;
  const isAdt = state.metric === "adt";
  const unit = isAdt ? "trades/d" : "$B/d";
  const fmt = isAdt ? fmtN : fmtB;
  const head = COLS.map((c) => {
    const title = c.key === "cur" ? (isAdt ? "ADT" : "ADV $B/d") : c.title;
    const arrow = state.sortKey === c.key ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
    return `<th data-sort="${c.key}" class="${c.num ? "num" : ""}" title="Sort by ${title}">${title}${arrow}</th>`;
  }).join("");
  const rows = sortRows(state.rows).map(({ p, stats: s }) => {
    if (!s) return `<tr><td><b>${p.label}</b></td><td colspan="10" class="muted">no ${isAdt ? "trade-count" : "par"} data</td></tr>`;
    return `<tr data-pid="${p.id}" title="Click to view ${p.label} chart">` +
      `<td><b>${p.label}</b></td>` +
      `<td class="num">${fmt(s.cur.v)}</td>` +
      `<td class="num"${heat(s.mom)}>${pct1(s.mom)}</td>` +
      `<td class="num"${heat(s.yoy)}>${pct1(s.yoy)}</td>` +
      `<td class="num"${heat(s.d3)}>${pct1(s.d3)}</td>` +
      `<td>${rangePlot(s, fmt)}</td>` +
      `<td class="num">${fmt(s.lo)}</td>` +
      `<td class="num">${fmt(s.hi)}</td>` +
      `<td class="num">${fmt(s.avg)}</td>` +
      `<td class="num">${s.pct.toFixed(0)}</td>` +
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
    `as of ${mlabel(state.asof)} · ${unit} · 3Y window (blue dot = current, ◆ = avg) · Treasury history from Feb 2023`;
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
    }</tr></thead><tbody><tr><td colspan="11" class="muted">Loading TRACE history…</td></tr></tbody></table>`;
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
      `<tr><td colspan="11" class="muted">Failed to load grid — ${err.message}</td></tr>`;
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
