// PULSE Phase 2 — the 60-second conference view (2026-10-05).
// KPI tile strip + RANGE CHECK + auto-generated TALK TRACK + TOP MOVERS +
// TODAY ECON + NEXT AUCTIONS + RISK GAUGES. All live from terminal APIs,
// re-rendered on every dashboard refresh (15-min interval in main.js).
import { getScorecard, getSeries, getDashboard, getAuctions, getEconCalendar } from "../api.js";
import { rangePlotDotted, zToPct } from "../rangeviz.js";
// TRACE/Treasury monthly volume: reuse the grid's canonical TOTAL math
// (Treasury Total + all 10 TRACE products, 11 components — corrected 2026-10-05).
import { TOTAL_PARTS, toMetric, totalVals, rowStats, rangeById } from "./trace_grid.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---- KPI tile definitions: label -> scorecard series id ----
const TILES = [
  { label: "S&P 500",   sid: "etf-spy",        fmt: "px" },
  { label: "UST 10Y",   sid: "us10y",          fmt: "yld" },
  { label: "SOFR",      sid: "sofr",           fmt: "yld" },
  { label: "VVIX",      sid: "vvix",           fmt: "idx" },
  { label: "HY OAS",    sid: "hy-oas",         fmt: "bp" },
  { label: "30Y MTG",   sid: "us-mortgage-30y",fmt: "yld" },
  { label: "TGA",       sid: "tga",            fmt: "$B" },
  { label: "VIX",       sid: "vix",            fmt: "idx" },
];

const fmtVal = (v, fmt) => {
  if (v == null) return "—";
  if (fmt === "yld") return `${v.toFixed(2)}%`;
  if (fmt === "bp") return `${Math.round(v)}bp`;
  if (fmt === "idx") return v.toFixed(1);
  if (fmt === "$B") return `$${(v / 1e9).toFixed(0)}B`;
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
};

const fmtChg = (row, fmt) => {
  const d1 = row.d1;
  if (d1 == null) return { txt: "—", cls: "flat" };
  const up = d1 > 0, dn = d1 < 0;
  const cls = up ? "up" : dn ? "down" : "flat";
  let txt;
  if (fmt === "yld" || fmt === "bp") {
    const bp = fmt === "yld" ? d1 * 100 : d1;
    txt = `${bp >= 0 ? "+" : ""}${bp.toFixed(0)}bp`;
  } else if (fmt === "$B") {
    txt = `${d1 >= 0 ? "+" : "−"}$${Math.abs(d1 / 1e9).toFixed(0)}B`;
  } else {
    txt = `${d1 >= 0 ? "+" : ""}${d1.toFixed(2)}%`;
  }
  return { txt, cls };
};

const hzCell = (v, fmt) => {
  if (v == null) return "—";
  if (fmt === "yld" || fmt === "bp") {
    const bp = fmt === "yld" ? v * 100 : v;
    return `${bp >= 0 ? "+" : ""}${bp.toFixed(0)}bp`;
  }
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
};

function sparkSvg(pts, up) {
  if (!pts || pts.length < 2) return `<span class="muted">—</span>`;
  const w = 148, h = 30;
  const vs = pts.map((p) => p[1]).filter((v) => v != null);
  if (vs.length < 2) return `<span class="muted">—</span>`;
  const lo = Math.min(...vs), hi = Math.max(...vs), rg = (hi - lo) || 1;
  const path = vs.map((v, i) =>
    `${(i / (vs.length - 1) * w).toFixed(1)},${(h - 3 - ((v - lo) / rg) * (h - 6)).toFixed(1)}`).join(" ");
  const col = up == null ? "#2563eb" : up ? "var(--up)" : "var(--down)";
  return `<svg class="pspark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
    `<polyline points="${path}" fill="none" stroke="${col}" stroke-width="1.6"/></svg>`;
}

// pct change between last value and value ~days back
function hzPct(pts, days) {
  if (!pts || pts.length < 2) return null;
  const last = pts[pts.length - 1];
  const target = Date.parse(last[0]) - days * 864e5;
  let ref = null;
  for (const [d, v] of pts) { if (Date.parse(d) <= target) ref = v; }
  if (ref == null || ref === 0) return null;
  return ((last[1] - ref) / Math.abs(ref)) * 100;
}

async function tileData(t, scoreRows) {
  const row = scoreRows.find((r) => r.id === t.sid || r.series === t.sid);
  let pts = [];
  try {
    const s = await getSeries(t.sid, "3m");
    pts = (s.points ?? []).slice().sort((a, b) => (a[0] < b[0] ? -1 : 1));
  } catch { /* sparkline degrades gracefully */ }
  const last30 = pts.slice(-30);
  const w1 = hzPct(pts, 7), q1 = hzPct(pts, 91);
  return { t, row, pts: last30, w1, q1 };
}

function tileHtml({ t, row, pts, w1, q1 }) {
  const v = row?.last;
  const chg = fmtChg(row ?? {}, t.fmt);
  const up = chg.cls === "up" ? true : chg.cls === "down" ? false : null;
  const z = row?.z_1y;
  const hz = [
    ["1D", row?.d1], ["1W", w1], ["1M", row?.m1], ["1Q", q1], ["1Y", row?.y1],
  ].map(([h, val]) => `<span><b>${h}</b> ${hzCell(val, t.fmt)}</span>`).join("");
  return `<div class="kpi"><div class="lbl">${esc(t.label)}</div>` +
    `<div class="val">${fmtVal(v, t.fmt)}</div>` +
    `<div class="chg ${chg.cls}">${chg.txt} <span class="note">1D</span></div>` +
    `${sparkSvg(pts, up)}` +
    `<div class="hz">${hz}<span class="zbadge">z ${z == null ? "—" : (z > 0 ? "+" : "") + z.toFixed(1)}</span></div></div>`;
}

// ---- TRACE/TREASURY VOLUME: TOTAL + Treasury + TRACE ex-Treasury tiles ----
const MONTH_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const mlabel = (d) => (d && d.length >= 7 ? `${MONTH_ABBR[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}` : "—");
const fmtVol = (v) => { // $B/d -> $T/d or $B/d
  if (v == null || !isFinite(v)) return "—";
  if (v >= 1000) return `$${(v / 1000).toFixed(2)}T/d`;
  if (v >= 10) return `$${v.toFixed(0)}B/d`;
  if (v >= 1) return `$${v.toFixed(1)}B/d`;
  return `$${v.toFixed(2)}B/d`;
};
const pctFrac = (x) => (x == null || !isFinite(x) ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`);

async function volumeData() {
  const jobs = TOTAL_PARTS.map(async (p) => {
    const s = await getSeries(p.par, "max").catch(() => null);
    if (!s?.points?.length) return null;
    return { p, par: s.points };
  });
  const data = (await Promise.all(jobs)).filter(Boolean);
  if (!data.length) return null;
  const r3y = rangeById("3y");
  const advTotal = rowStats(totalVals(data, "adv"), r3y);
  const advUst = rowStats(totalVals(data, "adv", TOTAL_PARTS.filter((p) => p.id === "ust")), r3y);
  const advTrace = rowStats(totalVals(data, "adv", TOTAL_PARTS.filter((p) => p.id !== "ust")), r3y);
  const prods = data.map(({ p, par }) => ({ p, st: rowStats(toMetric(p, par, "adv"), r3y) }));
  return { advTotal, advUst, advTrace, prods };
}

// Monthly rows: 1D/1W are "—" by design (monthly cadence), headline is M/M.
function volTileHtml(label, st) {
  const m1 = st?.m1;
  const cls = m1 == null ? "flat" : m1 > 0 ? "up" : m1 < 0 ? "down" : "flat";
  const hz = [["1D", st?.d1], ["1W", st?.w1], ["1M", st?.m1], ["1Q", st?.q1], ["1Y", st?.y1]]
    .map(([h, val]) => `<span><b>${h}</b> ${pctFrac(val)}</span>`).join("");
  const z = st?.z;
  const pts = (st?.win ?? []).map(({ d, v }) => [d, v]);
  return `<div class="kpi"><div class="lbl">${esc(label)}</div>` +
    `<div class="val">${fmtVol(st?.cur?.v)}</div>` +
    `<div class="chg ${cls}">${pctFrac(m1)} <span class="note">1M</span></div>` +
    `${sparkSvg(pts, cls === "up" ? true : cls === "down" ? false : null)}` +
    `<div class="hz">${hz}<span class="zbadge">z ${z == null ? "—" : (z > 0 ? "+" : "") + z.toFixed(1)}</span></div>` +
    `<div class="muted" style="font-size:9px;margin-top:2px">${esc(mlabel(st?.asof))} · monthly</div></div>`;
}

// ---- RANGE CHECK: key series with dotted range in both versions ----
const RANGE_SERIES = [
  { label: "UST 10Y", sid: "us10y", fmt: "yld" },
  { label: "HY OAS", sid: "hy-oas", fmt: "bp" },
  { label: "VVIX", sid: "vvix", fmt: "idx" },
  { label: "VIX", sid: "vix", fmt: "idx" },
  { label: "30Y MTG", sid: "us-mortgage-30y", fmt: "yld" },
  { label: "IG OAS", sid: "ig-oas", fmt: "bp" },
];

function rangeRow(label, valTxt, pct, z) {
  const pctCls = pct == null ? "" : pct >= 75 ? "up" : pct <= 25 ? "down" : "flat";
  const zCls = z == null ? "" : Math.abs(z) >= 2 ? (z > 0 ? "up strong" : "down strong") : Math.abs(z) >= 1 ? (z > 0 ? "up" : "down") : "flat";
  return `<tr><td class="sym">${esc(label)}</td><td class="num">${valTxt}</td>` +
    `<td>${rangePlotDotted({ pct, z }, "pct")}</td>` +
    `<td>${rangePlotDotted({ pct, z }, "z")}</td>` +
    `<td class="${pctCls} num">${pct == null ? "—" : pct.toFixed(0)}</td>` +
    `<td class="${zCls} num">${z == null ? "—" : (z > 0 ? "+" : "") + z.toFixed(2)}</td></tr>`;
}

async function rangeCheckHtml(scoreRows, vol) {
  const rows = await Promise.all(RANGE_SERIES.map(async ({ label, sid, fmt }) => {
    const row = scoreRows.find((r) => r.id === sid || r.series === sid);
    const z = row?.z_1y ?? null;
    return rangeRow(label, fmtVal(row?.last, fmt), zToPct(z), z);
  }));
  // TRACE/Treasury monthly ADV rows (3Y window stats, same math as the grid)
  const vst = (id) => vol?.prods?.find((x) => x.p.id === id)?.st ?? null;
  const volDefs = [
    ["TOTAL ADV (TRACE + TSY)", vol?.advTotal],
    ["Treasury ADV", vol?.advUst],
    ["TBA ADV", vst("tba")],
    ["Corporate ADV", vst("corp")],
  ];
  const volRows = volDefs.filter(([, st]) => st != null)
    .map(([label, st]) => rangeRow(label, fmtVol(st.cur?.v), st.pct ?? null, st.z ?? null));
  return `<table data-sortable><tr><th>Series</th><th>Now</th>` +
    `<th data-sort="off">Range · percentile</th><th data-sort="off">Range · z-score</th>` +
    `<th>%ile</th><th>z</th></tr>${rows.join("")}${volRows.join("")}</table>`;
}

// ---- TALK TRACK: auto-generated 60-second bullets from live data ----
function volumeBullets(vol) {
  const out = [];
  const t = vol?.advTotal;
  if (!t?.cur) return out;
  const curM = mlabel(t.asof);
  const win = t.win ?? [];
  const prevPt = win.length >= 2 ? win[win.length - 2] : null;
  const prevM = mlabel(prevPt?.d);
  const vsAvg = t.avg ? (t.cur.v - t.avg) / t.avg : null;
  // Top contributor: product with the largest MoM delta in the total's direction.
  const deltas = (vol.prods ?? []).map(({ p, st }) => {
    if (st?.m1 == null || st?.cur == null) return null;
    const prev = st.cur.v / (1 + st.m1);
    return { label: p.label, delta: st.cur.v - prev, m1: st.m1, z: st.z };
  }).filter(Boolean);
  const totalDelta = prevPt ? t.cur.v - prevPt.v : null;
  let leader = null;
  if (totalDelta != null && deltas.length) {
    const sameSign = deltas.filter((d) => (totalDelta > 0 ? d.delta > 0 : d.delta < 0));
    leader = (sameSign.length ? sameSign : deltas)
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))[0];
  }
  out.push(`<b>Volume:</b> TOTAL fixed-income ADV ${fmtVol(t.cur.v)} in ${curM} (${pctFrac(t.m1)} vs ${prevM})` +
    (leader ? ` — led by ${leader.label} (${pctFrac(leader.m1)} MoM)` : "") +
    (vsAvg != null ? `; now ${vsAvg >= 0 ? "+" : ""}${(vsAvg * 100).toFixed(0)}% vs 3Y avg (${fmtVol(t.avg)}).` : "."));
  // Flags: |1M%| > 20% or |z| > 2
  const flags = deltas.filter((d) => Math.abs(d.m1) > 0.20 || Math.abs(d.z ?? 0) > 2)
    .sort((a, b) => Math.abs(b.m1) - Math.abs(a.m1))
    .map((d) => `${d.label} ${pctFrac(d.m1)} MoM${d.z != null ? `, z ${d.z > 0 ? "+" : ""}${d.z.toFixed(1)}` : ""}`);
  if (flags.length) {
    out.push(`<b>Volume flags:</b> ${flags.join(" · ")} — ${flags.length === 1 ? "watch" : "watch these products"} for dealer positioning shifts.`);
  }
  return out;
}

function talkBullets(dash, vol) {
  const p = dash.panels ?? {};
  const rows = p.scorecard?.rows ?? [];
  const byId = (id) => rows.find((r) => r.id === id || r.series === id);
  const us10y = byId("us10y"), vix = byId("vix"), vvix = byId("vvix");
  const hyOas = byId("hy-oas"), igOas = byId("ig-oas");
  const bullets = volumeBullets(vol);
  // Rates
  if (us10y?.last != null) {
    const bp = us10y.d1 != null ? `${us10y.d1 * 100 >= 0 ? "+" : ""}${(us10y.d1 * 100).toFixed(0)}bp 1D` : "—";
    bullets.push(`<b>Rates:</b> 10Y ${us10y.last.toFixed(2)}% (${bp}), 1Y z ${us10y.z_1y != null ? (us10y.z_1y > 0 ? "+" : "") + us10y.z_1y.toFixed(1) : "—"} — ` +
      (us10y.z_1y > 1 ? "elevated vs trailing year; duration pain continues." : us10y.z_1y < -1 ? "well off trailing-year highs; duration bid returning." : "mid-range vs trailing year."));
  }
  // Vol regime
  if (vix?.last != null || vvix?.last != null) {
    const v = vix?.last != null ? `VIX ${vix.last.toFixed(1)}` : "";
    const vv = vvix?.last != null ? `VVIX ${vvix.last.toFixed(0)}` : "";
    const split = (vvix?.z_1y ?? 0) - (vix?.z_1y ?? 0);
    bullets.push(`<b>Vol:</b> ${[v, vv].filter(Boolean).join(" · ")} — ` +
      (split > 0.8 ? "rates vol running hotter than equity vol; bond vol is the story." : "vol regimes aligned; no cross-asset vol dislocation."));
  }
  // Credit
  if (hyOas?.last != null) {
    bullets.push(`<b>Credit:</b> HY OAS ${Math.round(hyOas.last)}bp (${hyOas.d1 != null ? (hyOas.d1 >= 0 ? "+" : "") + hyOas.d1.toFixed(0) + "bp 1D" : "—"}), ` +
      `IG ${igOas?.last != null ? Math.round(igOas.last) + "bp" : "—"} — ` +
      ((hyOas.z_1y ?? 0) > 1 ? "spreads wide vs trailing year; default risk being priced." : "spreads contained; credit bid intact."));
  }
  // Regime
  const regime = p.radar?.regime;
  if (regime && regime !== "UNKNOWN") {
    bullets.push(`<b>Regime:</b> ${regime.replace(/_/g, " ")} — radar composite across vol/stress/returns/breadth/inflation/funding.`);
  }
  // Funding
  const sofr = byId("sofr"), tga = byId("tga");
  if (sofr?.last != null || tga?.last != null) {
    bullets.push(`<b>Funding:</b> ${sofr?.last != null ? `SOFR ${sofr.last.toFixed(2)}%` : ""}${sofr?.last != null && tga?.last != null ? " · " : ""}${tga?.last != null ? `TGA $${(tga.last / 1e9).toFixed(0)}B` : ""} — watch bill supply vs reserve drain.`);
  }
  if (!bullets.length) return `<li class="muted">No live data yet — tiles are still loading.</li>`;
  return bullets.map((b) => `<li>${b}</li>`).join("");
}

// ---- Main render ----
let pulseReq = 0;

export async function renderPulse() {
  const req = ++pulseReq;
  const tilesEl = document.getElementById("pulse-tiles");
  const rangeEl = document.getElementById("pulse-rangecheck");
  const talkEl = document.getElementById("pulse-talktrack");
  if (!tilesEl && !rangeEl && !talkEl) return; // PULSE DOM not present

  let dash = {};
  let scoreRows = [];
  try {
    dash = await getDashboard();
    if (req !== pulseReq) return;
    scoreRows = (await getScorecard()).rows ?? [];
    if (req !== pulseReq) return;
  } catch (err) {
    if (tilesEl) tilesEl.innerHTML = `<p class="muted">PULSE UNAVAILABLE — ${esc(err.message)}</p>`;
    return;
  }

  // TRACE/Treasury monthly volume: TOTAL + Treasury + TRACE ex-Treasury
  let vol = null;
  try {
    vol = await volumeData();
    if (req !== pulseReq) return;
  } catch { /* volume tiles degrade gracefully */ }

  // KPI tiles
  if (tilesEl) {
    tilesEl.innerHTML = `<p class="muted">Loading tiles…</p>`;
    const datas = await Promise.all(TILES.map((t) => tileData(t, scoreRows)));
    if (req !== pulseReq) return;
    const asof = new Date().toLocaleString("en-US", { timeZone: "America/New_York" });
    const volTiles = vol ? [
      ["TOTAL FI ADV", vol.advTotal],
      ["TREASURY ADV", vol.advUst],
      ["TRACE ADV (EX-TSY)", vol.advTrace],
    ].filter(([, st]) => st != null).map(([label, st]) => volTileHtml(label, st)).join("") : "";
    tilesEl.innerHTML = datas.map(tileHtml).join("") + volTiles +
      `<div class="kpi-foot muted">LIVE · as of ${esc(asof)} ET · 1D/1W/1M/1Q/1Y + 1Y z · volume tiles monthly ADV (3Y stats)</div>`;
  }

  // Range check
  if (rangeEl) {
    rangeEl.innerHTML = await rangeCheckHtml(scoreRows, vol);
    if (req !== pulseReq) return;
  }

  // Talk track (both snapshot panel and talktrack subtab)
  const talkHtml = `<ul class="talk">${talkBullets(dash, vol)}</ul>`;
  if (talkEl) talkEl.innerHTML = talkHtml;
  const talkTab = document.querySelector('[data-hub="pulse"][data-sub="talktrack"] .panel-body');
  if (talkTab) talkTab.innerHTML = `<ul class="talk big">${talkBullets(dash, vol)}</ul>` +
    `<p class="muted">Auto-built from live terminal data · ${esc(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }))} ET</p>`;
  if (req !== pulseReq) return;

  // Top movers (from dashboard movers panel)
  const movEl = document.getElementById("pulse-movers");
  if (movEl) {
    const mv = p_movers(dash);
    movEl.innerHTML = mv;
  }

  // Today econ
  const econEl = document.getElementById("pulse-econ");
  if (econEl) {
    try {
      const cal = await getEconCalendar();
      if (req !== pulseReq) return;
      econEl.innerHTML = econHtml(cal);
    } catch { econEl.innerHTML = `<p class="muted">Calendar unavailable.</p>`; }
  }

  // Next auctions
  const aucEl = document.getElementById("pulse-auctions");
  if (aucEl) {
    try {
      const auc = await getAuctions();
      if (req !== pulseReq) return;
      aucEl.innerHTML = auctionsHtml(auc);
    } catch { aucEl.innerHTML = `<p class="muted">Auctions unavailable.</p>`; }
  }

  // Risk gauges
  const gEl = document.getElementById("pulse-gauges");
  if (gEl) gEl.innerHTML = gaugesHtml(dash, scoreRows);
}

// Top movers mini-table from dashboard movers panel
function p_movers(dash) {
  const m = dash.panels?.movers ?? {};
  const rows = [];
  for (const idx of Object.values(m.indexes ?? {})) {
    for (const w of ["d1", "w1"]) {
      const win = idx[w] ?? {};
      for (const r of [...(win.up ?? []), ...(win.down ?? [])]) rows.push(r);
    }
  }
  rows.sort((a, b) => Math.abs(b.z ?? 0) - Math.abs(a.z ?? 0));
  const top = rows.slice(0, 6);
  if (!top.length) return `<p class="muted">No movers data.</p>`;
  return `<table><tr><th>Sym</th><th>Δ1D</th><th>σ</th></tr>` + top.map((r) => {
    const cls = (r.z ?? 0) >= 0 ? "up" : "down";
    return `<tr><td class="sym">${esc(r.symbol)}</td>` +
      `<td class="${cls} num">${r.ret_pct != null ? `${r.ret_pct >= 0 ? "+" : ""}${r.ret_pct.toFixed(1)}%` : "—"}</td>` +
      `<td class="num">${r.z != null ? `${r.z >= 0 ? "+" : ""}${r.z.toFixed(1)}σ` : "—"}</td></tr>`;
  }).join("") + `</table>`;
}

// Today econ from /api/econ-calendar
function econHtml(cal) {
  const evts = cal.events ?? cal ?? [];
  const today = new Date().toISOString().slice(0, 10);
  const todays = evts.filter((e) => (e.date ?? "").slice(0, 10) === today).slice(0, 5);
  const upcoming = evts.filter((e) => (e.date ?? "").slice(0, 10) > today).slice(0, 3);
  const rows = [...todays, ...upcoming];
  if (!rows.length) return `<p class="muted">No events on the calendar.</p>`;
  return `<table><tr><th>Time ET</th><th>Release</th><th></th></tr>` + rows.map((e) => {
    const t = (e.time ?? e.datetime ?? "").slice(0, 5) || (e.date ?? "").slice(5);
    return `<tr><td class="num">${esc(t)}</td><td class="sym">${esc(e.title ?? e.name ?? "—")}</td>` +
      `<td class="muted">${esc(e.consensus ? `cons ${e.consensus}` : e.note ?? "")}</td></tr>`;
  }).join("") + `</table>`;
}

// Next auctions from /api/auctions
function auctionsHtml(auc) {
  const list = auc.upcoming ?? auc.auctions ?? [];
  const next = list.slice(0, 4);
  if (!next.length) return `<p class="muted">No upcoming auctions.</p>`;
  return `<table><tr><th>Date</th><th>Tenor</th><th>Size</th></tr>` + next.map((a) => {
    const amt = a.offering ?? a.size;
    const amtTxt = amt != null ? `$${Number(amt) >= 1e9 ? (Number(amt) / 1e9).toFixed(0) + "B" : amt}` : "—";
    return `<tr><td class="num">${esc((a.date ?? "").slice(5) || "—")}</td>` +
      `<td class="sym">${esc(a.tenor ?? a.security ?? "—")}</td><td class="num">${amtTxt}</td></tr>`;
  }).join("") + `</table>`;
}

// Risk gauges: regime + key z-scores as 0-100 dials
function gaugesHtml(dash, scoreRows) {
  const p = dash.panels ?? {};
  const regime = (p.radar?.regime ?? "UNKNOWN").replace(/_/g, " ");
  const byId = (id) => scoreRows.find((r) => r.id === id || r.series === id);
  const gauges = [
    { label: "Regime", val: regime, z: null },
    { label: "Basis stress", val: null, z: byId("hy-oas")?.z_1y, inv: false, txt: "HY OAS z" },
    { label: "Vol stress", val: null, z: byId("vvix")?.z_1y, inv: false, txt: "VVIX z" },
    { label: "Rate stress", val: null, z: byId("us10y")?.z_1y, inv: false, txt: "10Y z" },
  ];
  return `<table><tr><th>Gauge</th><th>Now</th><th>1Y z</th></tr>` + gauges.map((g) => {
    const z = g.z;
    const zCls = z == null ? "" : Math.abs(z) >= 2 ? (z > 0 ? "up strong" : "down strong") : Math.abs(z) >= 1 ? (z > 0 ? "up" : "down") : "flat";
    const zTxt = z == null ? "—" : `${z > 0 ? "+" : ""}${z.toFixed(1)}`;
    const val = g.val ?? (g.txt ? `<span class="muted">${esc(g.txt)}</span>` : "—");
    return `<tr><td>${esc(g.label)}</td><td class="sym">${val}</td><td class="${zCls} num">${zTxt}</td></tr>`;
  }).join("") + `</table>`;
}
