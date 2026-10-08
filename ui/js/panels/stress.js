// STRESS — MATRIX · VELOCITY (Phase 1): Time heatmap (View 1) + Velocity heatmap (View 6).
// Fed by /api/stress/matrix and /api/stress/velocity — cached docs written by the
// collector stress job; the API never recomputes inline.
//
// Phase-1 docs carry CURRENT scores only (no per-week history), so the Time view
// renders the "Today" column fully and notes that the 26-week history replay
// arrives in Phase 2. Nothing is invented client-side: weekly columns are not
// derived from store series here because the doc does not carry the
// indicator-id -> series-id mapping needed to do it honestly.
//
// Warm-dark theme throughout (#171410 / #e8c96a / #7fc9b5). heatmap.js helpers
// are NOT used here: they are light-theme delta-intensity helpers, while this
// panel needs fixed absolute band scales on a dark background.
import { getStressMatrix, getStressVelocity, getStressHorizon, getStressContagion,
         getStressDivergence, getStressReplay, getStressQuadrant } from "../api.js";
import { fmtAge } from "../fmt.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// --- module state (controls re-render without refetch) ----------------------
const S = {
  view: "time",            // "time" | "velocity" | "quadrant" | "horizon" | "contagion" | "divergence" | "replay"
  sort: "category",        // "category" | "stress" | "change"
  showExtended: false,
  search: "",
  collapsed: new Set(),    // collapsed category names
  moversOpen: true,
  matrix: null,
  velocity: null,
  docs: {},                // phase-2 view docs, fetched lazily per view
  replayPos: null,         // slider position for the replay view
};

// Phase-2 view -> API getter (matrix/velocity are always fetched).
const VIEW_GET = {
  horizon: getStressHorizon,
  contagion: getStressContagion,
  divergence: getStressDivergence,
  replay: getStressReplay,
  quadrant: getStressQuadrant,
};
const MORE_VIEWS = ["horizon", "contagion", "divergence", "replay"];
const VIEW_LABEL = { horizon: "Horizon", contagion: "Contagion", divergence: "Divergence",
                     replay: "Replay", quadrant: "Quadrant" };

async function viewDoc(view) {
  // Lazily fetch + cache the phase-2 doc for a view; {"status":"building"}
  // (or an error marker) when the collector has not written it yet.
  if (!VIEW_GET[view]) return null;
  if (S.docs[view]) return S.docs[view];
  try {
    S.docs[view] = await VIEW_GET[view]();
  } catch {
    S.docs[view] = { status: "error" };
  }
  return S.docs[view];
}

// Category colours (warm-dark) for the quadrant scatter.
const CAT_COLOR = {
  "Volatility": "#e8c96a", "Credit": "#c97436", "Funding": "#7fc9b5",
  "Treasury plumbing": "#8ab8d8", "Equity internals": "#b48ad8",
  "Banks": "#d88a9e", "Global": "#8ad8c0", "Market activity": "#d8c98a",
  "Hedge fund leverage": "#9e8ad8", "Official refs": "#a89a83",
};
const catColor = (c) => CAT_COLOR[c] ?? "#a89a83";

// --- color scales (fixed, warm-dark adapted) --------------------------------
// Level: 0-25 deep green, 25-50 pale green, 50-75 yellow, 75-90 orange, 90-100 red.
function scoreBg(score) {
  if (score == null || !isFinite(score)) return null;
  if (score < 25) return "#2f7d46";
  if (score < 50) return "#5da86a";
  if (score < 75) return "#d9a93b";
  if (score < 90) return "#c97436";
  return "#cf4a42";
}
function scoreFg(score) {
  // dark text on the light yellow/green bands, white elsewhere
  if (score == null || !isFinite(score)) return "";
  return (score >= 25 && score < 75) ? "#171410" : "#f5efe0";
}
// Velocity diverging: deep blue (-3) -> neutral dark (0) -> deep red (+3).
function lerp(a, b, t) {
  return a.map((x, i) => Math.round(x + (b[i] - x) * t));
}
const VEL_NEG = [47, 99, 184], VEL_MID = [33, 29, 22], VEL_POS = [207, 74, 66];
function velBg(sigma) {
  if (sigma == null || !isFinite(sigma)) return null;
  const c = Math.max(-3, Math.min(3, sigma));
  const rgb = c < 0 ? lerp(VEL_MID, VEL_NEG, -c / 3) : lerp(VEL_MID, VEL_POS, c / 3);
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}
const velFg = (sigma) =>
  (sigma == null || !isFinite(sigma) || Math.abs(sigma) < 1.2) ? "#a89a83" : "#f5efe0";

const REGIME_COLOR = {
  Calm: "#7fc9b5", Normal: "#e8c96a", Elevated: "#d9a93b",
  High: "#c97436", Acute: "#cf4a42",
};
const TAG_CHIP = { DERIVED: "#7fc9b5", PROXY: "#c97436", MANUAL: "#d9a93b", Q: "#8ab86f", E: "#8ab86f" };

const fmtN = (x, d = 1) => (x == null || !isFinite(x) ? "—" : Number(x).toFixed(d));
const fmtScore = (x) => (x == null || !isFinite(x) ? "n/a" : Math.round(x));
const fmtSigned = (x, d = 1) =>
  (x == null || !isFinite(x) ? "—" : `${x < 0 ? "−" : x > 0 ? "+" : ""}${Math.abs(x).toFixed(d)}`);
const ageOf = (iso) => {
  if (!iso) return null;
  try { return fmtAge(iso); } catch { return null; }
};
const ageMs = (iso) => {
  const t = Date.parse(iso);
  return isFinite(t) ? Date.now() - t : null;
};

// --- header -----------------------------------------------------------------
function feedHealth(iso) {
  // live / stale / failed from the doc's own updated_at
  const age = ageMs(iso);
  if (age == null) return ["failed", "#cf4a42", "no doc"];
  if (age < 36 * 3600e3) return ["live", "#8ab86f", ageOf(iso)];
  if (age < 5 * 86400e3) return ["stale", "#d9a93b", ageOf(iso)];
  return ["stale", "#cf4a42", ageOf(iso)];
}

function headerHtml(m, v) {
  const o = m?.overall ?? {};
  const score = o.score, regime = o.regime ?? "—";
  const rc = REGIME_COLOR[regime] ?? "#a89a83";
  const asof = o.as_of ?? m?.updated_at;
  const [health, hcolor, htitle] = feedHealth(m?.updated_at);
  const cov = o.coverage;
  const covTxt = cov == null ? "n/a" :
    typeof cov === "object" ? `${cov.n_avail ?? "?"}/${cov.n_total ?? "?"} indicators` : String(cov);
  const speed = v?.overall?.speed_label ?? v?.overall?.label ?? null;
  return `<div class="stress-head">
    <div class="stress-gauge">
      <div class="stress-score" style="color:${rc}">${fmtScore(score)}</div>
      <div class="stress-regime" style="color:${rc}">${esc(regime)}</div>
      <div class="stress-sub muted">OVERALL STRESS · 0–100</div>
    </div>
    <div class="stress-meta">
      <div><span class="stress-dot" style="background:${hcolor}" title="${esc(htitle ?? "")}"></span>
        <b>${esc(health.toUpperCase())}</b>
        <span class="muted">as of ${esc(asof ?? "—")}${ageOf(m?.updated_at) ? ` (${esc(ageOf(m.updated_at))})` : ""}</span></div>
      <div class="muted">coverage: ${esc(covTxt)}</div>
      ${speed ? `<div>speed of stress: <b style="color:#e8c96a">${esc(speed)}</b></div>` : ""}
      ${v?.breadth ? `<div class="muted">breadth ≥75: ${fmtN((v.breadth.share_above_75 ?? 0) * 100, 0)}% · fast ≥90th %ile: ${fmtN((v.breadth.share_fast_90 ?? 0) * 100, 0)}% · n=${v.breadth.n ?? "—"}</div>` : ""}
    </div>
  </div>`;
}

// --- rows: group / sort / filter --------------------------------------------
function indicatorList(m) {
  return Object.values(m?.indicators ?? {});
}
function catOf(ind) { return ind?.category ?? "uncategorized"; }

function visibleIndicators(m) {
  let list = indicatorList(m);
  if (!S.showExtended) list = list.filter((i) => (i?.tier ?? "core") !== "extended");
  const q = S.search.trim().toLowerCase();
  if (q) list = list.filter((i) =>
    `${i?.name ?? ""} ${i?.id ?? ""} ${i?.category ?? ""}`.toLowerCase().includes(q));
  return list;
}

function groupedRows(list) {
  // category -> indicators, categories ordered by composite severity desc
  const cats = {};
  for (const i of list) (cats[catOf(i)] ??= []).push(i);
  const comp = (c) => S.matrix?.composites?.[c]?.score;
  return Object.keys(cats).sort((a, b) => {
    const sa = comp(a), sb = comp(b);
    return (sb ?? -1) - (sa ?? -1);
  }).map((c) => ({ category: c, rows: cats[c] }));
}

function sortRows(list) {
  const l = [...list];
  if (S.sort === "stress") l.sort((a, b) => (b?.score ?? -1) - (a?.score ?? -1));
  else if (S.sort === "change") l.sort((a, b) => (b?.d20_score ?? -Infinity) - (a?.d20_score ?? -Infinity));
  return l;
}

// --- cells -------------------------------------------------------------------
function levelCell(ind, vel) {
  const s = ind?.score;
  const bg = scoreBg(s);
  if (!bg) return `<td class="num stress-cell hatched" title="no data"><span class="muted">n/a</span></td>`;
  const sig5 = vel?.indicators?.[ind?.id]?.sigma?.d5;
  const arrow = sig5 == null || !isFinite(sig5) ? ""
    : sig5 > 0.5 ? `<span class="vel-arrow up">▲</span>` : sig5 < -0.5 ? `<span class="vel-arrow down">▼</span>` : "";
  return `<td class="num stress-cell clickable" data-ind="${esc(ind?.id)}" ` +
    `style="background:${bg};color:${scoreFg(s)}" title="${esc(ind?.name ?? "")} — score ${fmtScore(s)}">` +
    `<b>${fmtScore(s)}</b>${arrow}</td>`;
}

function velCell(ind, key) {
  const v = S.velocity?.indicators?.[ind?.id];
  const sig = v?.sigma?.[key];
  const bg = velBg(sig);
  if (!bg) return `<td class="num stress-cell hatched" title="no velocity"><span class="muted">n/a</span></td>`;
  const arrow = sig > 0.3 ? "▲" : sig < -0.3 ? "▼" : "·";
  const strong = v?.pctile?.[key] != null && v.pctile[key] >= 90 ? " vel-strong" : "";
  return `<td class="num stress-cell clickable${strong}" data-ind="${esc(ind?.id)}" ` +
    `style="background:${bg};color:${velFg(sig)}" title="${esc(ind?.name ?? "")} — ${key} ${fmtSigned(sig)}σ">${arrow} ${fmtSigned(sig)}</td>`;
}

function catHeaderRow(category, view) {
  const comp = S.matrix?.composites?.[category] ?? {};
  const collapsed = S.collapsed.has(category);
  const s = comp.score;
  const bg = scoreBg(s);
  const style = bg ? ` style="background:${bg};color:${scoreFg(s)}"` : "";
  const cols = view === "time" ? 2 : 5;
  const cov = comp.n_avail != null ? ` ${comp.n_avail}/${comp.n_total ?? "?"}` : "";
  let extra = "";
  if (view === "velocity") {
    const vc = S.velocity?.composites?.[category] ?? {};
    extra = `<span class="muted" style="margin-left:8px">5d ${fmtSigned(vc.sigma5)}σ${vc.accel ? ` · accel ${fmtSigned(vc.accel)}` : ""}</span>`;
  }
  return `<tr class="stress-cat clickable" data-cat="${esc(category)}" title="tap to collapse/expand">` +
    `<td colspan="${cols}"${style}><b>${collapsed ? "▸" : "▾"} ${esc(category.toUpperCase())}</b>` +
    ` <span class="stress-comp-score">${bg ? Math.round(s) : "n/a"}</span>` +
    `<span class="muted"${bg ? ' style="color:inherit;opacity:.75"' : ""}>${esc(cov)}${comp.status === "low_coverage" ? " · LOW COVERAGE" : ""}</span>${extra}</td></tr>`;
}

function indRowTime(ind) {
  const tag = ind?.tag ? `<span class="stress-tag" style="border-color:${TAG_CHIP[ind.tag] ?? "#a89a83"};color:${TAG_CHIP[ind.tag] ?? "#a89a83"}">${esc(ind.tag)}</span>` : "";
  return `<tr><td class="sym stress-label">${esc(ind?.name ?? ind?.id ?? "?")} ${tag}</td>${levelCell(ind, S.velocity)}</tr>`;
}
function indRowVel(ind) {
  const tag = ind?.tag ? `<span class="stress-tag" style="border-color:${TAG_CHIP[ind.tag] ?? "#a89a83"};color:${TAG_CHIP[ind.tag] ?? "#a89a83"}">${esc(ind.tag)}</span>` : "";
  return `<tr><td class="sym stress-label">${esc(ind?.name ?? ind?.id ?? "?")} ${tag}</td>` +
    `${velCell(ind, "d1")}${velCell(ind, "d5")}${velCell(ind, "d20")}${velCell(ind, "d60")}</tr>`;
}

function heatmapHtml(view) {
  const m = S.matrix;
  const list = sortRows(visibleIndicators(m));
  if (!list.length) return `<div class="empty-state">NO INDICATORS MATCH (docs may still be building)</div>`;
  const head = view === "time"
    ? `<tr><th>Indicator</th><th>Today</th></tr>`
    : `<tr><th>Indicator</th><th>1d σ</th><th>5d σ</th><th>20d σ</th><th>60d σ</th></tr>`;
  const rowFn = view === "time" ? indRowTime : indRowVel;
  let rows;
  if (S.sort === "category" && !S.search.trim()) {
    rows = groupedRows(list).map((g) => {
      const h = catHeaderRow(g.category, view);
      if (S.collapsed.has(g.category)) return h;
      return h + g.rows.map(rowFn).join("");
    }).join("");
  } else {
    rows = list.map(rowFn).join("");
  }
  return `<table class="stress-grid">${head}${rows}</table>`;
}

function buildingHtml(label) {
  return `<div class="empty-state">${label} VIEW BUILDING — the collector stress job has not written this doc yet.</div>`;
}

// --- Phase-2 views ------------------------------------------------------------
// All read their cached doc (S.docs[view]); nothing is invented client-side:
// a missing doc renders the building placeholder.

function visibleListFrom(indObj) {
  // tier filter + search over any {id: indicator} object (matrix or horizon).
  let list = Object.values(indObj ?? {});
  if (!S.showExtended) list = list.filter((i) => (i?.tier ?? "core") !== "extended");
  const q = S.search.trim().toLowerCase();
  if (q) list = list.filter((i) =>
    `${i?.name ?? ""} ${i?.id ?? ""} ${i?.category ?? ""}`.toLowerCase().includes(q));
  return list;
}

function horizonCell(ind, win) {
  const p = ind?.[`pctl_${win}`];
  const bg = scoreBg(p);
  const n = ind?.[`n_${win}`];
  if (!bg) return `<td class="num stress-cell hatched" title="no data in ${win} window"><span class="muted">n/a</span></td>`;
  return `<td class="num stress-cell clickable" data-ind="${esc(ind?.id)}" ` +
    `style="background:${bg};color:${scoreFg(p)}" title="${esc(ind?.name ?? "")} — ${win} percentile ${fmtN(p, 0)} (n=${n ?? "?"})"><b>${fmtN(p, 0)}</b></td>`;
}

function horizonHtml() {
  const hz = S.docs.horizon;
  if (!hz || hz.status === "building" || hz.status === "error") return buildingHtml("HORIZON");
  const list = visibleListFrom(hz.indicators);
  if (!list.length) return `<div class="empty-state">NO INDICATORS MATCH (docs may still be building)</div>`;
  const l = [...list];
  if (S.sort === "stress") l.sort((a, b) => (b?.pctl_5Y ?? -1) - (a?.pctl_5Y ?? -1));
  else if (S.sort === "change") l.sort((a, b) =>
    ((b?.pctl_1M ?? 0) - (b?.pctl_5Y ?? 0)) - ((a?.pctl_1M ?? 0) - (a?.pctl_5Y ?? 0)));
  const rowFn = (ind) => {
    const tag = ind?.tag ? `<span class="stress-tag" style="border-color:${TAG_CHIP[ind.tag] ?? "#a89a83"};color:${TAG_CHIP[ind.tag] ?? "#a89a83"}">${esc(ind.tag)}</span>` : "";
    return `<tr><td class="sym stress-label">${esc(ind?.name ?? ind?.id ?? "?")} ${tag}</td>` +
      `${horizonCell(ind, "1M")}${horizonCell(ind, "3M")}${horizonCell(ind, "1Y")}${horizonCell(ind, "5Y")}</tr>`;
  };
  let rows;
  if (S.sort === "category" && !S.search.trim()) {
    const cats = {};
    for (const i of l) (cats[catOf(i)] ??= []).push(i);
    rows = Object.keys(cats).sort().map((c) => {
      const h = catHeaderRow(c, "horizon");
      if (S.collapsed.has(c)) return h;
      return h + cats[c].map(rowFn).join("");
    }).join("");
  } else {
    rows = l.map(rowFn).join("");
  }
  return `<table class="stress-grid"><tr><th>Indicator</th><th>1M %ile</th><th>3M %ile</th><th>1Y %ile</th><th>5Y %ile</th></tr>${rows}</table>` +
    `<p class="muted stress-note">Direction-adjusted percentile of the latest observation vs its own trailing window (or the longest window that exists). Sort "4-week change" ranks by 1M minus 5Y percentile — the spike versus the long-run extreme.</p>`;
}

// correlation cell: deep blue (-1) -> neutral dark (0) -> deep red (+1)
function corrBg(c) {
  if (c == null || !isFinite(c)) return null;
  const t = Math.max(-1, Math.min(1, c));
  const rgb = t < 0 ? lerp(VEL_MID, VEL_NEG, -t) : lerp(VEL_MID, VEL_POS, t);
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

function contagionHtml() {
  const cg = S.docs.contagion;
  if (!cg || cg.status === "building" || cg.status === "error") return buildingHtml("CONTAGION");
  const cats = cg.categories ?? [];
  if (!cats.length) return `<div class="empty-state">NO CATEGORY DATA (docs may still be building)</div>`;
  const flag = cg.flagged
    ? `<div class="stress-flag" style="border:1px solid #c97436;border-radius:8px;padding:8px 10px;margin:8px 0;color:#e8c96a">` +
      `<b>CONTAGION FLAG</b> — mean cross-category correlation ${fmtN(cg.mean_cross_corr, 2)} ` +
      `above its 1Y 90th percentile (${fmtN(cg.mean_cross_corr_p90_1y, 2)})</div>`
    : `<div class="muted stress-note">mean cross-category correlation ${fmtN(cg.mean_cross_corr, 2)} ` +
      `(1Y 90th %ile ${fmtN(cg.mean_cross_corr_p90_1y, 2)}) — no flag</div>`;
  const head = `<tr><th></th>${cats.map((c) => `<th title="${esc(c)}">${esc(c.slice(0, 10))}</th>`).join("")}</tr>`;
  const rows = cats.map((r) => `<tr><td class="sym stress-label">${esc(r)}</td>` + cats.map((c) => {
    const v = cg.matrix?.[r]?.[c];
    const bg = corrBg(v);
    if (!bg) return `<td class="num stress-cell hatched"><span class="muted">n/a</span></td>`;
    return `<td class="num stress-cell" style="background:${bg};color:${Math.abs(v) > 0.6 ? "#f5efe0" : "#a89a83"}" ` +
      `title="${esc(r)} × ${esc(c)}: ${fmtN(v, 2)}">${fmtN(v, 2)}</td>`;
  }).join("") + `</tr>`).join("");
  // stress breadth line: weekly share of indicators scoring > 75
  const bd = cg.breadth?.dates ?? [], bv = cg.breadth?.share_above_75 ?? [];
  let breadthSvg = "";
  if (bd.length > 1) {
    const W = 600, H = 110, P = 24;
    const X = (i) => P + (i / (bd.length - 1)) * (W - 2 * P);
    const Y = (v) => H - P - v * (H - 2 * P);
    const pts = bv.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
    breadthSvg = `<div style="margin-top:10px"><b>STRESS BREADTH</b> <span class="muted">share of indicators scoring &gt;75, weekly</span>` +
      `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="stress breadth">` +
      `<line x1="${P}" y1="${Y(0.3)}" x2="${W - P}" y2="${Y(0.3)}" stroke="#c97436" stroke-dasharray="4 3" stroke-width="1"/>` +
      `<text x="${W - P}" y="${Y(0.3) - 4}" fill="#a89a83" font-size="10" text-anchor="end">30% alert line</text>` +
      `<polyline points="${pts}" fill="none" stroke="#e8c96a" stroke-width="2"/>` +
      `<text x="${P}" y="${H - 6}" fill="#a89a83" font-size="10">${esc(bd[0])}</text>` +
      `<text x="${W - P}" y="${H - 6}" fill="#a89a83" font-size="10" text-anchor="end">${esc(bd[bd.length - 1])}</text>` +
      `<text x="${P}" y="${Y(bv[bv.length - 1]) - 5}" fill="#e8c96a" font-size="11">${fmtN(bv[bv.length - 1] * 100, 0)}%</text>` +
      `</svg></div>`;
  }
  return `${flag}<div class="stress-scroll"><table class="stress-grid">${head}${rows}</table></div>` +
    `<p class="muted stress-note">${esc(cg.window ?? "")}</p>${breadthSvg}`;
}

function divergenceHtml() {
  const dv = S.docs.divergence;
  if (!dv || dv.status === "building" || dv.status === "error") return buildingHtml("DIVERGENCE");
  let pairs = [...(dv.pairs ?? [])];
  const q = S.search.trim().toLowerCase();
  if (q) pairs = pairs.filter((p) =>
    `${p.a_name ?? ""} ${p.b_name ?? ""} ${p.a_id ?? ""} ${p.b_id ?? ""}`.toLowerCase().includes(q));
  if (!S.showExtended) pairs = pairs.filter((p) =>
    !((p.a_tier ?? "core") === "extended" && (p.b_tier ?? "core") === "extended"));
  if (!pairs.length) return `<div class="empty-state">NO DIVERGENT PAIRS (or none match the filter)</div>`;
  const rows = pairs.map((p) => {
    const bg = scoreBg(p.gap_pctl_1y);
    const gc = bg ? ` style="background:${bg};color:${scoreFg(p.gap_pctl_1y)}"` : "";
    return `<tr class="clickable" data-ind="${esc(p.a_id)}">` +
      `<td class="sym">${esc(p.a_name ?? p.a_id)}<br><span class="muted">vs</span> ${esc(p.b_name ?? p.b_id)}</td>` +
      `<td class="muted">${esc(p.category ?? "")}</td>` +
      `<td class="num">${fmtN(p.corr_1y, 2)}</td>` +
      `<td class="num"><b>${fmtScore(p.score_a)}</b></td>` +
      `<td class="num"><b>${fmtScore(p.score_b)}</b></td>` +
      `<td class="num stress-cell"${gc} title="gap ${fmtN(p.gap, 1)} — 1Y percentile ${fmtN(p.gap_pctl_1y, 0)} (n=${p.n_weeks ?? "?"})"><b>${fmtN(p.gap, 0)}</b></td>` +
      `<td class="num">${p.gap_pctl_1y == null ? "n/a" : `p${fmtN(p.gap_pctl_1y, 0)}`}</td></tr>`;
  }).join("");
  return `<table class="stress-grid"><tr><th>Pair</th><th>Category</th><th>1Y corr</th><th>Score A</th><th>Score B</th><th>Gap</th><th>Gap 1Y %ile</th></tr>${rows}</table>` +
    `<p class="muted stress-note">${esc(dv.note ?? "")}</p>`;
}

function replayHtml() {
  const rp = S.docs.replay;
  if (!rp || rp.status === "building" || rp.status === "error") return buildingHtml("REPLAY");
  const dates = rp.dates ?? [], overall = rp.overall ?? [];
  const n = dates.length;
  if (n < 2) return `<div class="empty-state">NOT ENOUGH REPLAY HISTORY YET</div>`;
  if (S.replayPos == null || S.replayPos >= n) S.replayPos = n - 1;
  const W = 600, H = 240, P = 30;
  const X = (i) => P + (i / (n - 1)) * (W - 2 * P);
  const Y = (v) => v == null ? null : H - P - (v / 100) * (H - 2 * P);
  // episode bands
  let bands = "";
  for (const e of (rp.episodes ?? [])) {
    const i0 = dates.findIndex((d) => d >= e.start);
    const i1 = dates.findIndex((d) => d > e.finish);
    const j0 = i0 < 0 ? 0 : i0, j1 = i1 < 0 ? n - 1 : Math.max(j0, i1 - 1);
    bands += `<rect x="${X(j0)}" y="${P}" width="${Math.max(2, X(j1) - X(j0))}" height="${H - 2 * P}" ` +
      `fill="#7fc9b5" opacity="0.12"><title>${esc(e.name)}: ${esc(e.start)} → ${esc(e.finish)} — ${esc(e.trigger)}</title></rect>`;
  }
  const pts = overall.map((v, i) => (v == null ? null : `${X(i).toFixed(1)},${Y(v).toFixed(1)}`)).filter(Boolean).join(" ");
  const regime = (s) => s == null ? "—" : s < 30 ? "Calm" : s < 50 ? "Normal" : s < 70 ? "Elevated" : s <= 85 ? "High" : "Acute";
  return `<div class="stress-replay">` +
    `<div class="muted stress-note">Scrub 5Y of weekly Overall + composite scores. Shaded bands = confirmed stress episodes (hover for trigger).</div>` +
    `<input type="range" class="stress-slider" min="0" max="${n - 1}" value="${S.replayPos}" style="width:100%" aria-label="scrub through time">` +
    `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="overall stress replay">` +
    `${bands}` +
    `<line x1="${P}" y1="${Y(50)}" x2="${W - P}" y2="${Y(50)}" stroke="#3a332a" stroke-width="1"/>` +
    `<polyline points="${pts}" fill="none" stroke="#e8c96a" stroke-width="2"/>` +
    `<line id="rq-cursor" x1="${X(S.replayPos)}" y1="${P}" x2="${X(S.replayPos)}" y2="${H - P}" stroke="#f5efe0" stroke-width="1.5"/>` +
    `<text x="${P}" y="${H - 8}" fill="#a89a83" font-size="10">${esc(dates[0])}</text>` +
    `<text x="${W - P}" y="${H - 8}" fill="#a89a83" font-size="10" text-anchor="end">${esc(dates[n - 1])}</text>` +
    `</svg>` +
    `<div id="rq-readout"></div>` +
    `</div>`;
}

function updateReplayDom() {
  // Slider moved: reposition cursor + readout without rebuilding the slider
  // (keeps drag focus intact).
  const rp = S.docs.replay;
  if (!rp) return;
  const dates = rp.dates ?? [], overall = rp.overall ?? [], n = dates.length;
  const pos = Math.max(0, Math.min(n - 1, S.replayPos ?? n - 1));
  const W = 600, P = 30;
  const X = (i) => P + (i / (n - 1)) * (W - 2 * P);
  const cur = document.querySelector("#rq-cursor");
  if (cur) { cur.setAttribute("x1", X(pos)); cur.setAttribute("x2", X(pos)); }
  const regime = (s) => s == null ? "—" : s < 30 ? "Calm" : s < 50 ? "Normal" : s < 70 ? "Elevated" : s <= 85 ? "High" : "Acute";
  const o = overall[pos];
  const rc = REGIME_COLOR[regime(o)] ?? "#a89a83";
  const comps = Object.entries(rp.composites ?? {})
    .map(([c, arr]) => ({ c, v: arr?.[pos] }))
    .filter((r) => r.v != null)
    .sort((a, b) => b.v - a.v);
  const ro = document.querySelector("#rq-readout");
  if (ro) ro.innerHTML =
    `<div style="margin:6px 0"><b>${esc(dates[pos])}</b> — Overall <b style="color:${rc}">${fmtScore(o)}</b> ` +
    `<span style="color:${rc}">${esc(regime(o))}</span></div>` +
    `<table class="stress-grid"><tr><th>Category</th><th>Score</th></tr>` +
    comps.map((r) => `<tr><td class="sym">${esc(r.c)}</td><td class="num"><b>${fmtScore(r.v)}</b></td></tr>`).join("") +
    `</table>`;
}

function quadrantPoints() {
  const qd = S.docs.quadrant;
  let pts = [...(qd?.points ?? [])];
  if (!S.showExtended) pts = pts.filter((p) => (p?.tier ?? "core") !== "extended");
  const q = S.search.trim().toLowerCase();
  if (q) pts = pts.filter((p) =>
    `${p?.name ?? ""} ${p?.id ?? ""} ${p?.category ?? ""}`.toLowerCase().includes(q));
  return pts;
}

function quadrantHtml() {
  const qd = S.docs.quadrant;
  if (!qd || qd.status === "building" || qd.status === "error") return buildingHtml("QUADRANT");
  const counts = qd.counts ?? {};
  const pts = quadrantPoints().filter((p) => p.quadrant !== "n/a" && p.score != null && p.sigma5 != null);
  const W = 400, P = 34;
  const X = (s) => P + (s / 100) * (W - 2 * P);
  const Y = (v) => W - P - ((Math.max(-3, Math.min(3, v)) + 3) / 6) * (W - 2 * P);
  const dots = pts.map((p) => {
    const r = 4 + Math.min(10, Math.abs(p.accel ?? 0) * 6);
    return `<g class="clickable" data-ind="${esc(p.id)}">` +
      `<title>${esc(p.name ?? p.id)} — score ${fmtScore(p.score)}, 5d ${fmtSigned(p.sigma5)}σ, ${esc(p.quadrant)}</title>` +
      `<circle cx="${X(p.score).toFixed(1)}" cy="${Y(p.sigma5).toFixed(1)}" r="22" fill="transparent"/>` +
      `<circle cx="${X(p.score).toFixed(1)}" cy="${Y(p.sigma5).toFixed(1)}" r="${r.toFixed(1)}" fill="${catColor(p.category)}" opacity="0.9" pointer-events="none"/></g>`;
  }).join("");
  const counter = (label, color) =>
    `<span style="margin-right:12px"><span class="stress-dot" style="background:${color}"></span><b>${counts[label] ?? 0}</b> ${label}</span>`;
  // list fallback below the scatter
  const l = [...quadrantPoints()];
  if (S.sort === "stress") l.sort((a, b) => (b?.score ?? -1) - (a?.score ?? -1));
  else if (S.sort === "change") l.sort((a, b) => Math.abs(b?.sigma5 ?? -1) - Math.abs(a?.sigma5 ?? -1));
  const rows = l.map((p) => {
    const qc = { Escalating: "#cf4a42", Unwinding: "#d9a93b", Emerging: "#e8c96a", Calm: "#7fc9b5", "n/a": "#a89a83" }[p.quadrant] ?? "#a89a83";
    return `<tr class="clickable" data-ind="${esc(p.id)}" style="min-height:44px">` +
      `<td class="sym">${esc(p.name ?? p.id)}</td><td class="muted">${esc(p.category ?? "")}</td>` +
      `<td class="num"><b>${fmtScore(p.score)}</b></td>` +
      `<td class="num">${p.sigma5 == null ? "n/a" : fmtSigned(p.sigma5) + "σ"}</td>` +
      `<td class="num" style="color:${qc}"><b>${esc(p.quadrant)}</b></td></tr>`;
  }).join("");
  const legend = Object.keys(CAT_COLOR).map((c) =>
    `<span style="margin-right:10px;white-space:nowrap"><span class="stress-dot" style="background:${CAT_COLOR[c]}"></span>${esc(c)}</span>`).join("");
  return `<div style="margin:4px 0 8px">${counter("Escalating", "#cf4a42")}${counter("Emerging", "#e8c96a")}` +
    `<span class="muted">Unwinding ${counts.Unwinding ?? 0} · Calm ${counts.Calm ?? 0} · n/a ${counts["n/a"] ?? 0}</span></div>` +
    `<svg viewBox="0 0 ${W} ${W}" style="width:100%;max-width:520px;height:auto;display:block;margin:0 auto" role="img" aria-label="level vs velocity quadrant">` +
    `<rect x="${P}" y="${P}" width="${W - 2 * P}" height="${W - 2 * P}" fill="#211d16" rx="6"/>` +
    `<line x1="${X(50)}" y1="${P}" x2="${X(50)}" y2="${W - P}" stroke="#3a332a" stroke-width="1"/>` +
    `<line x1="${P}" y1="${Y(0)}" x2="${W - P}" y2="${Y(0)}" stroke="#3a332a" stroke-width="1"/>` +
    `<text x="${X(75)}" y="${P + 14}" fill="#a89a83" font-size="11" text-anchor="middle">ESCALATING</text>` +
    `<text x="${X(25)}" y="${P + 14}" fill="#a89a83" font-size="11" text-anchor="middle">UNWINDING</text>` +
    `<text x="${X(75)}" y="${W - P - 8}" fill="#a89a83" font-size="11" text-anchor="middle">EMERGING</text>` +
    `<text x="${X(25)}" y="${W - P - 8}" fill="#a89a83" font-size="11" text-anchor="middle">CALM</text>` +
    `<text x="${W - P}" y="${W - 8}" fill="#a89a83" font-size="10" text-anchor="end">score →</text>` +
    `<text x="${P + 4}" y="${P + 12}" fill="#a89a83" font-size="10">+3σ</text>` +
    `<text x="${P + 4}" y="${W - P - 6}" fill="#a89a83" font-size="10">−3σ</text>` +
    `${dots}</svg>` +
    `<div class="muted stress-note" style="margin-top:6px">${legend}<br>x = 0–100 score · y = 5d normalized velocity · dot size = |acceleration| · tap a dot for detail</div>` +
    `<table class="stress-grid" style="margin-top:8px"><tr><th>Indicator</th><th>Category</th><th>Score</th><th>5d σ</th><th>Quadrant</th></tr>${rows}</table>`;
}

function viewBodyHtml(view) {
  switch (view) {
    case "horizon": return horizonHtml();
    case "contagion": return contagionHtml();
    case "divergence": return divergenceHtml();
    case "replay": return replayHtml();
    case "quadrant": return quadrantHtml();
    case "velocity": return `<div class="stress-scroll">${heatmapHtml("velocity")}` +
      `<p class="muted stress-note">Normalized velocity: k-day raw change ÷ trailing-1Y σ of k-day changes. Q-series show no velocity.</p></div>`;
    case "time":
    default: return `<div class="stress-scroll">${heatmapHtml("time")}` +
      `<p class="muted stress-note">Phase-1 docs carry current scores only — the 26-week weekly history replay arrives in Phase 2.</p></div>`;
  }
}

// --- Top 20 movers ------------------------------------------------------------
function moversHtml() {
  const m = S.matrix;
  const list = visibleIndicators(m).map((ind) => {
    const v = S.velocity?.indicators?.[ind?.id] ?? {};
    const sig5 = v?.sigma?.d5;
    const key = sig5 != null && isFinite(sig5) ? Math.abs(sig5)
      : (ind?.d5_score != null && isFinite(ind.d5_score) ? Math.abs(ind.d5_score) / 25 : -1);
    return { ind, key, sig5, d5: ind?.d5_score };
  }).filter((r) => r.key >= 0)
    .sort((a, b) => b.key - a.key).slice(0, 20);
  if (!list.length) return "";
  const rows = list.map(({ ind, sig5, d5 }) => {
    const arrow = sig5 != null && isFinite(sig5)
      ? (sig5 > 0 ? `<span class="vel-arrow up">▲</span>` : `<span class="vel-arrow down">▼</span>`) : "";
    return `<tr class="clickable" data-ind="${esc(ind?.id)}">` +
      `<td class="sym">${esc(ind?.name ?? ind?.id)}</td>` +
      `<td class="muted">${esc(catOf(ind))}</td>` +
      `<td class="num"><b>${fmtScore(ind?.score)}</b></td>` +
      `<td class="num">${arrow} ${sig5 != null && isFinite(sig5) ? fmtSigned(sig5) + "σ" : fmtSigned(d5)}</td></tr>`;
  }).join("");
  return `<details class="stress-movers"${S.moversOpen ? " open" : ""}>` +
    `<summary><b>TOP 20 MOVERS</b> <span class="muted">by |5d velocity| — tap a row for detail</span></summary>` +
    `<table class="stress-grid"><tr><th>Indicator</th><th>Category</th><th>Score</th><th>5d move</th></tr>${rows}</table></details>`;
}

// --- popover -----------------------------------------------------------------
function closePopover() { document.querySelector(".stress-pop")?.remove(); }

function sparklineHtml(ind) {
  // §16.7: sparklines lazy-render on expand only — never drawn 160 at once.
  // Phase-1 docs carry current scores only (no history points), so until the
  // Phase-2 doc includes history there is nothing to draw — and we refuse to
  // invent a line. The history window the doc claims is shown instead.
  const n = ind?.history_n, w = ind?.window_used;
  return `<div class="stress-spark-note muted">` +
    (n ? `history: ${n} obs · window ${esc(w ?? "—")}` : "history not in doc yet") +
    ` — sparkline replay in Phase 2</div>`;
}

function popoverHtml(id) {
  const ind = S.matrix?.indicators?.[id] ?? {};
  const v = S.velocity?.indicators?.[id] ?? {};
  const tag = ind?.tag ? `<span class="stress-tag" style="border-color:${TAG_CHIP[ind.tag] ?? "#a89a83"};color:${TAG_CHIP[ind.tag] ?? "#a89a83"}">${esc(ind.tag)}</span>` : "";
  const kv = (k, val) => `<div class="stress-kv"><span class="muted">${k}</span><b>${val}</b></div>`;
  const hz = S.docs.horizon?.indicators?.[id];
  const hzRow = hz && hz.status === "ok"
    ? kv("1M/3M/1Y/5Y %ile", `${fmtN(hz.pctl_1M, 0)} / ${fmtN(hz.pctl_3M, 0)} / ${fmtN(hz.pctl_1Y, 0)} / ${fmtN(hz.pctl_5Y, 0)}`)
    : "";
  const qd = (S.docs.quadrant?.points ?? []).find((p) => p?.id === id);
  const qdRow = qd && qd.quadrant !== "n/a"
    ? kv("quadrant", `${esc(qd.quadrant)}${qd.sigma5 != null ? ` · 5d ${fmtSigned(qd.sigma5)}σ` : ""}${qd.accel != null ? ` · accel ${fmtSigned(qd.accel)}` : ""}`)
    : "";
  return `<div class="stress-pop-head"><b>${esc(ind?.name ?? id)}</b> ${tag}
      <button type="button" class="stress-pop-x" aria-label="close">✕</button></div>
    <div class="muted">${esc(ind?.id ?? "")} · ${esc(catOf(ind))} · tier ${esc(ind?.tier ?? "core")}</div>
    ${kv("value", `${ind?.value == null ? "n/a" : esc(ind.value)}${ind?.unit ? " " + esc(ind.unit) : ""}`)}
    ${kv("as of", esc(ind?.as_of ?? "—"))}
    ${kv("score", `${fmtScore(ind?.score)} / 100${ind?.status ? ` · ${esc(ind.status)}` : ""}`)}
    ${kv("1Y percentile", fmtN(ind?.percentile, 0))}
    ${kv("1Y z-score", fmtSigned(ind?.z, 2))}
    ${kv("1W score Δ", fmtSigned(ind?.d5_score))}
    ${kv("1M score Δ", fmtSigned(ind?.d20_score))}
    ${kv("5d velocity", v?.sigma?.d5 == null ? "—" : `${fmtSigned(v.sigma.d5)}σ (p${fmtN(v?.pctile?.d5, 0)})${v?.accel != null ? ` · accel ${fmtSigned(v.accel)}` : ""}`)}
    ${kv("source", esc(ind?.source ?? "—"))}
    ${kv("window", esc(ind?.window_used ?? "—"))}
    ${kv("confidence", esc(ind?.confidence ?? "—"))}
    ${hzRow}${qdRow}
    ${sparklineHtml(ind)}`;
}

function openPopover(id, anchorEl) {
  closePopover();
  const pop = document.createElement("div");
  pop.className = "stress-pop";
  pop.innerHTML = popoverHtml(id);
  document.body.appendChild(pop);
  const r = anchorEl.getBoundingClientRect();
  const pw = Math.min(340, window.innerWidth - 16);
  pop.style.width = `${pw}px`;
  let left = Math.min(r.left, window.innerWidth - pw - 8);
  pop.style.left = `${Math.max(8, left + window.scrollX)}px`;
  pop.style.top = `${r.bottom + window.scrollY + 6}px`;
  pop.querySelector(".stress-pop-x").addEventListener("click", (e) => { e.stopPropagation(); closePopover(); });
}

// --- legend / footer ----------------------------------------------------------
const LEVEL_SWATCHES = [[12, "#2f7d46"], [37, "#5da86a"], [62, "#d9a93b"], [82, "#c97436"], [95, "#cf4a42"]];
function legendHtml() {
  const sw = LEVEL_SWATCHES.map(([v, c]) =>
    `<span class="hl-sw" style="background:${c}" title="${v}"></span>`).join("");
  return `<div class="stress-legend">` +
    `<span><b>LEVEL</b> ${sw} 0→100</span>` +
    `<span><b>VEL</b> <span class="hl-sw" style="background:#2f63b8"></span>−3σ ` +
    `<span class="hl-sw" style="background:#211d16;border:1px solid #3a332a"></span>0 ` +
    `<span class="hl-sw" style="background:#cf4a42"></span>+3σ</span>` +
    `<span><span class="hl-sw hatched"></span>no data</span>` +
    `<span class="muted">▲▼ 5d σ arrows on level cells</span></div>`;
}

function footerHtml() {
  const gaps = S.matrix?.gaps ?? [];
  const gapList = gaps.length
    ? `<ul class="stress-gaps">${gaps.map((g) =>
        `<li>${esc(typeof g === "string" ? g : g?.id ?? JSON.stringify(g))}</li>`).join("")}</ul>`
    : `<p class="muted">no data gaps reported</p>`;
  return `<div class="panel-foot muted stress-foot">
    <div><b>DATA GAPS</b></div>${gapList}
    <div class="stress-method">METHOD: score 0–100 = direction-adjusted percentile vs own history
    (expanding ≤5Y window, ≥252 obs else "building"); weekly cells use the week's last observation;
    tags: DERIVED = computed from real series · PROXY = related series standing in ·
    MANUAL = hand-entered · Q = quarterly (carried forward) · E = event (last value carried).
    Velocity = raw change ÷ trailing-1Y σ of k-day changes. Not investment advice.</div>
  </div>`;
}

// --- main render --------------------------------------------------------------
export async function renderStress() {
  const body = document.querySelector("#panel-stress .panel-body");
  if (!body) return;
  let m = null, v = null;
  try {
    [m, v] = await Promise.all([getStressMatrix(), getStressVelocity()]);
  } catch {
    body.innerHTML = `<div class="empty-state">STRESS DOCS UNAVAILABLE — collector unreachable</div>`;
    return;
  }
  S.matrix = m; S.velocity = v;
  const inds = indicatorList(m);
  if (!m || m.status === "building" || !inds.length) {
    body.innerHTML = `<div class="empty-state">STRESS MATRIX BUILDING — first stress-job run has not written the docs yet.</div>`;
    return;
  }
  // Phase-2 view docs are fetched lazily and cached; a missing doc renders
  // the view's building placeholder, never invented values.
  await viewDoc(S.view);

  const segBtn = (id, label) =>
    `<button type="button" data-view="${id}" class="${S.view === id ? "on" : ""}">${label}</button>`;
  const moreSel = MORE_VIEWS.map((v) =>
    `<option value="${v}"${S.view === v ? " selected" : ""}>${VIEW_LABEL[v]}</option>`).join("");
  const showMovers = S.view === "time" || S.view === "velocity";
  body.innerHTML =
    headerHtml(m, v) +
    `<div class="stress-ctl">
      <div class="seg" role="tablist">${segBtn("time", "Time")}${segBtn("velocity", "Velocity")}${segBtn("quadrant", "Quadrant")}` +
      `<select class="stress-more" aria-label="more views"><option value="">More…</option>${moreSel}</select></div>
      <select class="stress-sort" aria-label="sort">
        <option value="category"${S.sort === "category" ? " selected" : ""}>Sort: category</option>
        <option value="stress"${S.sort === "stress" ? " selected" : ""}>Sort: current stress</option>
        <option value="change"${S.sort === "change" ? " selected" : ""}>Sort: 4-week change</option>
      </select>
      <button type="button" class="stress-tier">${S.showExtended ? "Hide extended" : "Show extended"}</button>
      <input type="search" class="stress-search" placeholder="Search indicators…" value="${esc(S.search)}" aria-label="search indicators">
    </div>` +
    (showMovers ? moversHtml() : "") +
    viewBodyHtml(S.view) +
    legendHtml() +
    footerHtml();

  // wire controls (re-render from cached docs, no refetch)
  body.querySelectorAll(".seg button").forEach((b) =>
    b.addEventListener("click", () => { S.view = b.dataset.view; S.replayPos = null; renderStress(); }));
  body.querySelector(".stress-more")?.addEventListener("change", (e) => {
    if (e.target.value) { S.view = e.target.value; S.replayPos = null; renderStress(); }
  });
  body.querySelector(".stress-sort")?.addEventListener("change", (e) => { S.sort = e.target.value; renderStress(); });
  body.querySelector(".stress-tier")?.addEventListener("click", () => { S.showExtended = !S.showExtended; renderStress(); });
  body.querySelector(".stress-search")?.addEventListener("input", (e) => { S.search = e.target.value; renderStress(); });
  body.querySelector(".stress-movers")?.addEventListener("toggle", (e) => { S.moversOpen = e.target.open; });
  // replay slider: update cursor + readout in place (keeps drag focus)
  body.querySelector(".stress-slider")?.addEventListener("input", (e) => {
    S.replayPos = +e.target.value; updateReplayDom();
  });
  updateReplayDom();
  body.querySelectorAll("[data-cat]").forEach((el) =>
    el.addEventListener("click", () => {
      const c = el.dataset.cat;
      S.collapsed.has(c) ? S.collapsed.delete(c) : S.collapsed.add(c);
      renderStress();
    }));
  body.querySelectorAll("[data-ind]").forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); openPopover(el.dataset.ind, el); }));
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".stress-pop") && !e.target.closest("[data-ind]")) closePopover();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });
