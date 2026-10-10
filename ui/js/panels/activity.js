// MULTI-ASSET ACTIVITY tab: MARKETS → Activity.
// Five-leg cross-market activity view: tokenized treasuries, Treasury TRACE,
// Treasury ETFs, options, futures. One /api/activity payload drives the whole
// view — cross-leg heatmap, MacroSpark lead signal, then per-leg KPI strip +
// primary chart + structure bars + sortable grid.
//
// Export convention matches other panels (e.g. options.js, stress.js):
// an async render function the app calls on startup, on poll, and on
// sub-tab navigation.
import { getActivity } from "../api.js";
import { tradingX } from "../tradingx.js";
import { ordinal } from "../fmt.js";
import { symInfo } from "../names.js";

const AMBER = "#e8c96a", TEAL = "#7fc9b5", RED = "#d9736a", MUTED = "#a89a83";
const SERIES_COLORS = [AMBER, TEAL, MUTED, RED, "#8ab86f"];
const AXIS = { stroke: "#a89a83", grid: { stroke: "#38312a" } };

// Backend leg keys → fallback section labels (backend LEG.title wins when present).
const LEG_ORDER = [
  ["tokenized", "TOKENIZED TREASURIES"],
  ["trace", "TREASURY TRACE"],
  ["etfs", "TREASURY ETFs"],
  ["options", "OPTIONS"],
  ["futures", "FUTURES"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---- number formatting ----------------------------------------------------
// Contract sends values as either preformatted strings or raw numbers.
// Numbers compact to T/B/M/K; nulls are never blank — always "n/a".
const isNum = (v) => typeof v === "number" && isFinite(v);

const compact = (x) => {
  if (!isNum(x)) return "n/a";
  const a = Math.abs(x);
  if (a >= 1e12) return `${(x / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${(x / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(x / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(x / 1e3).toFixed(1)}K`;
  return a < 10 && a > 0 ? x.toFixed(2) : x.toFixed(0);
};

// Native-unit value: strings pass through untouched, numbers compact.
const fmtVal = (v) => (typeof v === "string" ? (v.trim() === "" ? "n/a" : v) : compact(v));

const signed = (x, money = false) => {
  if (!isNum(x)) return "n/a";
  return `${x > 0 ? "+" : ""}${money ? "$" : ""}${compact(x)}`;
};

const signedPct = (p) => {
  if (!isNum(p)) return "n/a";
  return `${p > 0 ? "+" : ""}${p.toFixed(2)}%`;
};

const fmtZ = (z) => {
  if (!isNum(z)) return "n/a";
  return `${z > 0 ? "+" : ""}${z.toFixed(2)}σ`;
};

const fmtDate = (d) => (typeof d === "string" && d.length >= 10 ? d.slice(0, 10) : "n/a");

// Direction class for delta coloring (theme: up = green, down = red).
const dirCls = (v) => (v == null || !isNum(v) || v === 0 ? "flat" : v > 0 ? "up" : "down");

// Cell tint: amber = above/positive, teal = below/negative (warm-dark theme).
const tint = (v, full = 3) => {
  if (!isNum(v) || v === 0) return "";
  const a = (0.06 + Math.min(Math.abs(v) / full, 1) * 0.30).toFixed(2);
  const rgb = v > 0 ? "232,201,106" : "127,201,181";
  return `background:rgba(${rgb},${a});`;
};

// ---- shared fragments ------------------------------------------------------
function placeholder(title, msg) {
  return `<section class="panel" style="margin:8px 0"><div class="panel-title">${esc(title)}</div>` +
    `<div class="panel-body"><p class="muted">${esc(msg)}</p>` +
    `<p class="muted">Source: <b>/api/activity</b>. No data was invented — this view stays empty until the backend ships the snapshot.</p></div></section>`;
}

const sectionHead = (title, note) =>
  `<div style="color:var(--amber);font-size:11px;letter-spacing:2px;margin:18px 0 8px;` +
  `border-bottom:1px solid var(--line);padding-bottom:4px;">${esc(title)}` +
  (note ? ` <span class="muted" style="letter-spacing:0">· ${esc(note)}</span>` : "") + `</div>`;

// ---- regime bar ------------------------------------------------------------
function regimeBar(syn) {
  const regime = syn?.regime ?? null;
  const lead = syn?.lead ?? null;
  const hm = Array.isArray(syn?.heatmap) ? syn.heatmap : [];
  const fresh = Array.isArray(syn?.freshness) ? syn.freshness : [];

  // Confirmation is derived (not in the contract): legs clearing |z|≥2 and
  // whether their stress sign aligns with the lead signal.
  const stressed = hm.filter((r) => isNum(r?.z) && Math.abs(r.z) >= 2);
  let conf;
  if (!hm.length) conf = "n/a";
  else if (!stressed.length) conf = "no leg stressed (|z|≥2) — calm across legs";
  else {
    const aligned = isNum(lead?.z) && stressed.every((r) => Math.sign(r.z) === Math.sign(lead.z));
    conf = `${stressed.length} of ${hm.length} legs stressed (|z|≥2)${aligned ? " — sign-aligned with lead" : ""}`;
  }

  const dates = fresh.map((f) => f?.asof).filter((d) => typeof d === "string" && d.length >= 10).sort();
  const frsh = dates.length ? fmtDate(dates[dates.length - 1]) : "n/a";
  const oldest = dates.length ? fmtDate(dates[0]) : "n/a";

  const tile = (lbl, body) =>
    `<div class="kpi"><div class="lbl">${lbl}</div>` +
    `<div style="font-size:14px;font-weight:700;color:var(--fg);margin:2px 0;` +
    `overflow:hidden;text-overflow:ellipsis;">${body}</div></div>`;

  return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;margin:10px 0;">` +
    tile("REGIME", regime == null ? "n/a" : esc(String(regime))) +
    tile("LEAD SIGNAL", lead ? `${esc(String(lead.series ?? "n/a"))} <span class="${dirCls(lead.z)}">${fmtZ(lead.z)}</span>` : `<span class="muted">none</span>`) +
    tile("CONFIRMATION", esc(conf)) +
    tile("FRESHEST / OLDEST", `<span title="freshest">${esc(frsh)}</span> / <span class="muted" title="oldest">${esc(oldest)}</span>`) +
    `</div>`;
}

// ---- cross-leg heatmap ------------------------------------------------------
function heatmapTable(syn) {
  const hm = Array.isArray(syn?.heatmap) ? syn.heatmap : [];
  if (!hm.length) return `<p class="muted">No heatmap rows in this snapshot.</p>`;
  const rows = hm.map((r, i) => {
    const stale = !!r?.stale;
    const zCell = isNum(r?.z)
      ? `<td style="${tint(r.z)}${Math.abs(r.z) >= 2 ? "outline:1px solid #d9736a;outline-offset:-1px;" : ""}">${fmtZ(r.z)}</td>`
      : `<td>n/a</td>`;
    const d1wNom = signed(r?.d1w_nom, true), d1wPct = signedPct(r?.d1w_pct);
    const d1mNom = signed(r?.d1m_nom, true), d1mPct = signedPct(r?.d1m_pct);
    const lbl = r?.leg ?? LEG_ORDER[i]?.[0] ?? `leg ${i + 1}`;
    return `<tr${stale ? ' style="opacity:.45"' : ""}>` +
      `<td class="sym">${esc(String(r?.label ?? lbl))}</td>` +
      `<td>${fmtVal(r?.level)}</td>` +
      `<td class="${dirCls(r?.d1w_nom ?? r?.d1w_pct)}" style="${tint(r?.d1w_pct, 25)}">${d1wNom} (${d1wPct})</td>` +
      `<td class="${dirCls(r?.d1m_nom ?? r?.d1m_pct)}" style="${tint(r?.d1m_pct, 25)}">${d1mNom} (${d1mPct})</td>` +
      zCell +
      `<td style="${tint(r?.vel7)}">${fmtVal(r?.vel7)}</td>` +
      `<td style="${tint(r?.vel30)}">${fmtVal(r?.vel30)}</td></tr>`;
  }).join("");
  return `<table style="margin:6px 0"><thead><tr>` +
    `<th>Leg</th><th>Level</th><th>1W Δ</th><th>1M Δ</th><th>Z</th><th>7D vel</th><th>30D vel</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>` +
    `<p class="muted">Amber = above/positive · teal = below/negative · red outline = |z|≥2 stress · ` +
    `muted rows = stale (asof older than the leg's cadence). Deltas always show nominal + %.</p>`;
}

// ---- lead MacroSpark --------------------------------------------------------
function leadSpark(syn) {
  const lead = syn?.lead ?? null;
  if (!lead) {
    return `<div class="kpi" style="margin:10px 0"><div class="lbl">LEAD MACROSPARK</div>` +
      `<div style="font-size:15px;margin:4px 0;">no broad anomaly</div>` +
      `<div class="muted">No cross-leg signal cleared the anomaly gate in this snapshot.</div></div>`;
  }
  return `<div class="kpi" style="margin:10px 0;border-left:3px solid var(--amber)">` +
    `<div class="lbl">LEAD MACROSPARK</div>` +
    `<div style="font-size:18px;font-weight:700;color:var(--amber);margin:2px 0;">` +
    `${esc(String(lead.series ?? "n/a"))} <span class="${dirCls(lead.z)}" style="font-size:14px">${fmtZ(lead.z)}</span></div>` +
    (lead.definition ? `<div style="margin:2px 0;">${esc(String(lead.definition))}</div>` : "") +
    `<div class="muted">leg: ${esc(String(lead.leg ?? "n/a"))} · as of ${esc(fmtDate(lead.asof))}</div></div>`;
}

// ---- leg blocks --------------------------------------------------------------
function kpiStrip(leg) {
  const kpis = Array.isArray(leg?.kpis) ? leg.kpis : [];
  if (!kpis.length) return `<p class="muted">No KPIs in this snapshot.</p>`;
  return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin:8px 0;">` +
    kpis.map((k) => {
      const val = typeof k?.value === "string" ? (k.value.trim() === "" ? "n/a" : esc(k.value)) : compact(k?.value);
      const nom = k?.chg_nom, pct = k?.chg_pct;
      const delta = (nom == null && pct == null)
        ? "n/a"
        : `${signed(nom, true)} (${signedPct(pct)})`;
      const meta = [
        k?.pctile != null && isNum(k.pctile) ? `${ordinal(Math.round(k.pctile))} pctile` : null,
        isNum(k?.z) ? `z ${fmtZ(k.z)}` : null,
        k?.asof ? `as of ${esc(fmtDate(k.asof))}` : null,
      ].filter(Boolean).join(" · ") || null;
      return `<div class="kpi"><div class="lbl">${esc(String(k?.label ?? "—"))}</div>` +
        `<div class="val">${val}</div>` +
        `<div class="chg ${dirCls(nom ?? pct)}">${delta}</div>` +
        (meta ? `<div class="muted">${meta}</div>` : "") + `</div>`;
    }).join("") + `</div>`;
}

function structBars(leg) {
  const st = leg?.structure ?? null;
  const rows = Array.isArray(st?.rows) ? st.rows : [];
  if (!rows.length) return "";
  const maxV = Math.max(...rows.map((r) => (isNum(r?.value) ? r.value : 0)), 1e-12);
  const bar = (r) => {
    let share = isNum(r?.share) ? r.share : null;
    if (share != null && share > 1) share = share / 100; // backend may send percent
    const w = share != null
      ? Math.max(0, Math.min(1, share))
      : (isNum(r?.value) ? r.value / maxV : 0);
    const shareT = isNum(r?.share) ? ` · ${r.share > 1 ? r.share.toFixed(1) : (r.share * 100).toFixed(1)}%` : "";
    return `<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">` +
      `<span style="width:180px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(String(r?.label ?? "—"))}</span>` +
      `<span style="flex:1;background:rgba(127,201,181,.12);border-radius:2px;height:12px;position:relative;">` +
      `<span style="display:block;height:100%;width:${(w * 100).toFixed(1)}%;background:var(--amber);border-radius:2px;"></span></span>` +
      `<span class="muted" style="width:150px;flex:none;text-align:right;">${fmtVal(r?.value)}${shareT}</span></div>`;
  };
  return sectionHead(st?.title ?? "STRUCTURE") + `<div style="margin:6px 0;">${rows.map(bar).join("")}</div>`;
}

// Sortable grid. Sorting NEVER moves header rows (thead) or total rows
// (rows whose first cell reads like a total — pinned at top in both
// directions; missing values sink to the bottom).
const TOTAL_RE = /^(total|combined|all)/i;

function gridTable(leg, key) {
  const g = leg?.grid ?? null;
  const cols = Array.isArray(g?.cols) ? g.cols : [];
  const rows = Array.isArray(g?.rows) ? g.rows : [];
  if (!cols.length || !rows.length) return "";
  const cell = (v, i) => {
    if (v == null || (typeof v === "string" && v.trim() === "")) return `<td>n/a</td>`;
    if (isNum(v)) return `<td data-sort-val="${v}">${compact(v)}</td>`;
    const t = String(v);
    // Long ticker names where known (Harry's standing rule).
    if (i === 0 && symInfo(t)) return `<td class="sym">${esc(t)} — ${esc(symInfo(t).name)}</td>`;
    return `<td>${esc(t)}</td>`;
  };
  const body = rows.map((r) => {
    const cells = Array.isArray(r) ? r : [];
    const isTotal = cells.length && typeof cells[0] === "string" && TOTAL_RE.test(cells[0].trim());
    return `<tr${isTotal ? ' data-total="1" style="font-weight:700;border-top:1px solid var(--line);"' : ""}>` +
      cols.map((_, i) => cell(cells[i], i)).join("") + `</tr>`;
  }).join("");
  return sectionHead(g?.title ?? "DETAIL") +
    `<div style="overflow-x:auto;"><table class="act-grid" id="act-grid-${key}" style="margin:6px 0">` +
    `<thead><tr>${cols.map((c) => `<th>${esc(String(c))}</th>`).join("")}</tr></thead>` +
    `<tbody>${body}</tbody></table></div>` +
    `<p class="muted">Click a column header to sort — total rows stay pinned.</p>`;
}

// ---- charts ------------------------------------------------------------------
// Native units, trading-day-only x-axis (repo tradingX helper + uPlot, the
// same pattern trace_charts.js uses). Charts are drawn after innerHTML lands.
const msToDay = (ms) => {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

function drawLegChart(key, chart) {
  const div = document.getElementById(`act-chart-${key}`);
  if (!div) return;
  if (div._plot) { try { div._plot.destroy(); } catch { /* noop */ } div._plot = null; }
  const series = (chart?.series ?? []).filter((s) => (s?.points ?? []).length > 0);
  if (!series.length) { div.innerHTML = `<p class="muted">No chart data in this snapshot.</p>`; return; }
  if (typeof uPlot === "undefined") { div.innerHTML = `<p class="muted">Chart library still loading…</p>`; return; }
  try {
    // Union of trading days across series, null-filled gaps.
    const byDay = new Map();
    for (const s of series) {
      for (const [ms, v] of s.points) {
        if (!isNum(ms) || !isNum(v)) continue;
        const day = msToDay(ms);
        if (!byDay.has(day)) byDay.set(day, new Array(series.length).fill(null));
        byDay.get(day)[series.indexOf(s)] = v;
      }
    }
    const days = [...byDay.keys()].sort();
    if (!days.length) { div.innerHTML = `<p class="muted">No chart data in this snapshot.</p>`; return; }
    const tx = tradingX(days, (d) => d.slice(5).replace("-", "/"));
    const data = [tx.x, ...series.map((_, i) => days.map((d) => byDay.get(d)[i]))];
    const w = Math.max(300, div.clientWidth || 760);
    const opts = {
      width: w,
      height: w < 640 ? 260 : 340,
      scales: { x: { time: false } }, // tradingX uses ordinal x [0..N], not timestamps
      series: [{}, ...series.map((s, i) => ({
        label: s.name ?? `series ${i + 1}`,
        stroke: SERIES_COLORS[i % SERIES_COLORS.length],
        width: 1.5,
        spanGaps: true,
      }))],
      axes: [Object.assign({}, AXIS, { values: tx.values }), Object.assign({}, AXIS)],
    };
    div.innerHTML = "";
    div._plot = new uPlot(opts, data, div);
  } catch (err) {
    console.error(`[activity] chart ${key} failed:`, err);
    div.innerHTML = `<p class="muted">Chart failed to render.</p>`;
  }
}

// ---- per-header sort (pins total rows) ---------------------------------------
const sortValOf = (td) => {
  const raw = td.dataset.sortVal ?? td.textContent ?? "";
  const t = String(raw).replace(/[−‐]/g, "-").replace(/\s+/g, " ").trim();
  if (t === "" || /^(n\/a|—|–|-)$/i.test(t)) return null;
  const m = t.replace(/[$,%\s]/g, "").replace(/,/g, "");
  const mult = /T$/i.test(t) ? 1e12 : /B$/i.test(t) ? 1e9 : /M$/i.test(t) ? 1e6 : /[kK]$/.test(t) ? 1e3 : 1;
  const n = parseFloat(m.replace(/[TBMkK]$/i, ""));
  return isFinite(n) ? n * mult : t.toLowerCase();
};

// Exported (underscore-prefixed) for unit testing; renderActivity uses it internally.
export function _wireActivitySorts(root) {
  root.querySelectorAll("table.act-grid").forEach((t) => {
    const ths = [...t.querySelectorAll("thead th")];
    const tbody = t.querySelector("tbody");
    if (!tbody) return;
    const pinned = [...tbody.querySelectorAll("tr[data-total]")];
    const dataRows = [...tbody.querySelectorAll("tr:not([data-total])")];
    const order0 = dataRows.map((r) => r);
    ths.forEach((th, i) => {
      th.style.cursor = "pointer";
      th.title = "Click to sort";
      th.addEventListener("click", () => {
        const dir = th.dataset.dir === "desc" ? "asc" : th.dataset.dir === "asc" ? "none" : "desc";
        ths.forEach((h) => { delete h.dataset.dir; h.textContent = h.textContent.replace(/ [▲▼]$/, ""); });
        if (dir === "none") {
          pinned.forEach((r) => tbody.appendChild(r)); // total rows stay pinned at top
          order0.forEach((r) => tbody.appendChild(r));
          return;
        }
        th.dataset.dir = dir;
        th.textContent = `${th.textContent} ${dir === "desc" ? "▼" : "▲"}`;
        const rows = order0.map((r, oi) => ({ r, oi, v: sortValOf(r.children[i]) }));
        rows.sort((a, b) => {
          if (a.v == null && b.v == null) return a.oi - b.oi;
          if (a.v == null) return 1; // missing sinks to the bottom in both directions
          if (b.v == null) return -1;
          if (typeof a.v === "number" && typeof b.v === "number") return dir === "desc" ? b.v - a.v : a.v - b.v;
          const c = String(a.v).localeCompare(String(b.v));
          return dir === "desc" ? -c : c;
        });
        pinned.forEach((r) => tbody.appendChild(r)); // total rows stay pinned at top
        rows.forEach(({ r }) => tbody.appendChild(r));
      });
    });
  });
}

// ---- main render ---------------------------------------------------------------
const DEFS = "ADV = average daily volume · ADT = average daily trade count · " +
  "GEX = gamma exposure (options dealer-hedging pressure proxy) · z = z-score vs trailing 1Y · " +
  "vel7 = max 7-day rise (shock intensity) · vel30 = max 30-day rise (sustained pressure)";

export async function renderActivity(el) {
  const body = el instanceof HTMLElement ? el
    : document.querySelector("#panel-activity .panel-body");
  if (!body) return;
  body.innerHTML = `<p class="muted">Loading multi-asset activity…</p>`;

  let doc;
  try {
    doc = await getActivity();
  } catch (err) {
    body.innerHTML = placeholder("ACTIVITY — DATA UNAVAILABLE",
      `Couldn't reach /api/activity (${err?.message ?? "network error"}).`);
    return;
  }
  if (!doc || doc.status === "building" || !doc.legs) {
    body.innerHTML = placeholder("ACTIVITY — SNAPSHOT BUILDING",
      "The activity snapshot is still building. It populates on the next collector run.");
    return;
  }

  try {
    const syn = doc.synthesis ?? {};
    let html = `<p class="muted">Five legs — tokenized treasuries, Treasury TRACE, Treasury ETFs, options, futures. ` +
      `As of <b>${esc(fmtDate(doc.asof))}</b>.</p>` +
      `<p class="muted">${esc(DEFS)}</p>` +
      regimeBar(syn) +
      sectionHead("CROSS-LEG HEATMAP") + heatmapTable(syn) +
      sectionHead("LEAD SIGNAL") + leadSpark(syn);

    for (const [key, fallback] of LEG_ORDER) {
      const leg = doc.legs?.[key] ?? null;
      html += sectionHead(leg?.title ?? fallback);
      if (!leg) { html += `<p class="muted">No data for this leg in this snapshot.</p>`; continue; }
      html += kpiStrip(leg);
      const ch = leg?.chart ?? null;
      if (ch?.series?.length) {
        html += `<div style="color:var(--muted);font-size:10px;text-transform:uppercase;` +
          `margin:14px 0 4px;">${esc(String(ch.title ?? "chart"))}` +
          (ch.source ? ` · ${esc(String(ch.source))}` : "") +
          (ch.asof ? ` · as of ${esc(fmtDate(ch.asof))}` : "") + `</div>` +
          `<div id="act-chart-${key}" style="min-height:120px;"></div>`;
      }
      html += structBars(leg);
      html += gridTable(leg, key);
    }

    body.innerHTML = html;
    // Draw charts after the DOM lands (hidden tabs report clientWidth 0 —
    // main.js re-renders on hash change so charts size correctly).
    for (const [key] of LEG_ORDER) drawLegChart(key, doc.legs?.[key]?.chart);
    _wireActivitySorts(body);

    const foot = document.querySelector("#panel-activity .panel-foot");
    if (foot) foot.textContent = `DATA: /API/ACTIVITY · as of ${fmtDate(doc.asof)}`;
  } catch (err) {
    console.error("[activity] render failed:", err);
    body.innerHTML = placeholder("ACTIVITY — RENDER ERROR",
      "The snapshot loaded but couldn't be rendered. The panel will retry on the next poll.");
  }
}
