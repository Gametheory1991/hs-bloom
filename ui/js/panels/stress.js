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
import { getStressMatrix, getStressVelocity } from "../api.js";
import { fmtAge } from "../fmt.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// --- module state (controls re-render without refetch) ----------------------
const S = {
  view: "time",            // "time" | "velocity"
  sort: "category",        // "category" | "stress" | "change"
  showExtended: false,
  search: "",
  collapsed: new Set(),    // collapsed category names
  moversOpen: true,
  matrix: null,
  velocity: null,
};

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

  const segBtn = (id, label) =>
    `<button type="button" data-view="${id}" class="${S.view === id ? "on" : ""}">${label}</button>`;
  body.innerHTML =
    headerHtml(m, v) +
    `<div class="stress-ctl">
      <div class="seg" role="tablist">${segBtn("time", "Time")}${segBtn("velocity", "Velocity")}</div>
      <select class="stress-sort" aria-label="sort">
        <option value="category"${S.sort === "category" ? " selected" : ""}>Sort: category</option>
        <option value="stress"${S.sort === "stress" ? " selected" : ""}>Sort: current stress</option>
        <option value="change"${S.sort === "change" ? " selected" : ""}>Sort: 4-week change</option>
      </select>
      <button type="button" class="stress-tier">${S.showExtended ? "Hide extended" : "Show extended"}</button>
      <input type="search" class="stress-search" placeholder="Search indicators…" value="${esc(S.search)}" aria-label="search indicators">
    </div>` +
    moversHtml() +
    `<div class="stress-scroll">${heatmapHtml(S.view)}
      ${S.view === "time" ? `<p class="muted stress-note">Phase-1 docs carry current scores only — the 26-week weekly history replay arrives in Phase 2.</p>` : `<p class="muted stress-note">Normalized velocity: k-day raw change ÷ trailing-1Y σ of k-day changes. Q-series show no velocity.</p>`}
    </div>` +
    legendHtml() +
    footerHtml();

  // wire controls (re-render from cached docs, no refetch)
  body.querySelectorAll(".seg button").forEach((b) =>
    b.addEventListener("click", () => { S.view = b.dataset.view; renderStress(); }));
  body.querySelector(".stress-sort")?.addEventListener("change", (e) => { S.sort = e.target.value; renderStress(); });
  body.querySelector(".stress-tier")?.addEventListener("click", () => { S.showExtended = !S.showExtended; renderStress(); });
  body.querySelector(".stress-search")?.addEventListener("input", (e) => { S.search = e.target.value; renderStress(); });
  body.querySelector(".stress-movers")?.addEventListener("toggle", (e) => { S.moversOpen = e.target.open; });
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
