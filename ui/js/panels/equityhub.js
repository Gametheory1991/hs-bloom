// EQUITY hub: short volume (Reg SHO), margin debt (FINRA), short interest.
// Moved here 2026-10-06 per Harry — previously split between the FINRA panel
// (rich top-shorted table, margin summary) and POSITIONING → Short Interest.
// Each sub-tab: nominal+% deltas, Bloomberg sparklines, chart with stat/date
// selectors, CSV/XLSX export.
import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";
import { RANGES, rangeById } from "./trace_grid.js";
import { matrixToCSV, exportTablesXLSX, todayStamp } from "../export.js";
import { heatStyle, heatBg, HEAT_LEGEND } from "../heatmap.js";

const big = (x) =>
  x == null ? "—" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 });
const pct1 = (x) => (x == null ? "—" : `${(Number(x) * 100).toFixed(1)}%`);
const esc = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// Nominal+% delta: "+1.2M (+3.4%)".
const dCellSh = (nom, pct) => {
  if (nom == null && (pct == null || !isFinite(pct))) return "—";
  const n = nom == null ? "—" : `${nom >= 0 ? "+" : "−"}${Math.abs(Math.round(nom)).toLocaleString("en-US")}`;
  const p = pct == null || !isFinite(pct) ? "—" : `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
  if (n === "—") return p;
  if (p === "—") return n;
  return `${n} <span class="muted">(${p})</span>`;
};
// 1D delta vs previous point from daily {d,v} history.
function d1Delta(pts) {
  if (!pts || pts.length < 2) return { nom: null, pct: null };
  const cur = pts[pts.length - 1], prev = pts[pts.length - 2];
  if (!prev.v) return { nom: null, pct: null };
  return { nom: cur.v - prev.v, pct: (cur.v - prev.v) / prev.v };
}
// Delta vs N days back from {d,v} points (for monthly margin data too).
function backDelta(pts, days) {
  if (!pts || pts.length < 2) return { nom: null, pct: null };
  const cur = pts[pts.length - 1];
  let ref = null;
  for (let i = pts.length - 2; i >= 0; i--) {
    if (Math.round((Date.parse(cur.d) - Date.parse(pts[i].d)) / 86400000) >= days) { ref = pts[i]; break; }
  }
  if (!ref || !ref.v) return { nom: null, pct: null };
  return { nom: cur.v - ref.v, pct: (cur.v - ref.v) / ref.v };
}

// ---- Company name + GICS sector for heavily-shorted names (Reg SHO files
// carry no names). Static fallback; backend resolves new names dynamically.
import { STOCK_META as TICKER_META } from "../names.js";
const tickerMeta = (t) => {
  if (t && typeof t === "object" && t.name) return [t.name, t.sector ?? "Other"];
  const sym = typeof t === "string" ? t : t?.symbol;
  return TICKER_META[sym] ?? ["—", "Other"];
};
const chg = (x) => x == null ? "—" :
  `<span class="${x >= 0 ? "up" : "down"}">${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%</span>`;
const nomFromPct = (cur, pct) =>
  (cur == null || pct == null || !isFinite(pct) || 1 + pct === 0) ? null : cur - cur / (1 + pct);
const fmtShNom = (v) => v == null || !isFinite(v) ? "—" :
  `${v >= 0 ? "+" : "−"}` + (Math.abs(v) >= 1e9 ? (Math.abs(v) / 1e9).toFixed(2) + "B sh" :
    Math.abs(v) >= 1e6 ? (Math.abs(v) / 1e6).toFixed(1) + "M sh" :
    Math.abs(v) >= 1e3 ? (Math.abs(v) / 1e3).toFixed(1) + "K sh" :
    Math.round(Math.abs(v)).toLocaleString("en-US") + " sh");
const vcCell = (cur, pct, isRatio) => {
  if ((pct == null || !isFinite(pct)) && cur == null) return `<td class="num">—</td>`;
  const nom = nomFromPct(cur, pct);
  const pHtml = chg(pct);
  if (nom == null) return `<td class="num">${pHtml}</td>`;
  const nTxt = isRatio
    ? `${nom >= 0 ? "+" : "−"}${(Math.abs(nom) * 100).toFixed(1)}pp`
    : fmtShNom(nom);
  const cls = nom >= 0 ? "up" : nom < 0 ? "down" : "";
  return `<td class="num"${heatStyle({ pct })}><span class="${cls}"><b>${nTxt}</b></span> <span class="muted">(${pHtml})</span></td>`;
};
const pxFmt = (x) => x == null ? "—" :
  "$" + x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const mcapFmt = (x) => {
  if (x == null) return "—";
  if (x >= 1e6) return "$" + (x / 1e6).toFixed(2) + "T";
  if (x >= 1e3) return "$" + (x / 1e3).toFixed(1) + "B";
  return "$" + x.toFixed(0) + "M";
};
const peFmt = (x) => x == null ? `<span class="na">n/a</span>` : x.toFixed(1);
const spark1y = (pts) => {
  if (!pts || pts.length < 2) return `<span class="muted">—</span>`;
  const w = 110, h = 28;
  const lo = Math.min(...pts), hi = Math.max(...pts), rng = hi - lo || 1;
  const str = pts.map((v, i) =>
    `${(i / (pts.length - 1) * w).toFixed(1)},${(h - 2 - ((v - lo) / rng) * (h - 4)).toFixed(1)}`
  ).join(" ");
  const cls = pts[pts.length - 1] >= pts[0] ? "up" : "down";
  return `<svg width="${w}" height="${h}" class="spark"><polyline points="${str}" fill="none" stroke="currentColor" class="${cls}" stroke-width="1.5"/></svg>`;
};
const smaTri = (price, sma, label) => {
  if (price == null || sma == null) return `<span class="muted" title="${label}: n/a">—</span>`;
  return price >= sma
    ? `<span class="up" title="${label} ${sma.toFixed(2)} — price above">▲</span>`
    : `<span class="down" title="${label} ${sma.toFixed(2)} — price below">▼</span>`;
};
const rsBar = (rank) => {
  if (rank == null) return `<span class="muted">—</span>`;
  return `<span class="rsbar" title="1M return percentile rank within this table"><span class="rsfill" style="width:${rank}%"></span></span> <span class="num">${rank}</span>`;
};

// ---- CSV/XLSX download helper: builds a hidden table, reuses export.js ----
function downloadTable(headers, rows, base) {
  const t = document.createElement("table");
  const thead = document.createElement("tr");
  headers.forEach((h) => { const th = document.createElement("th"); th.textContent = h; thead.appendChild(th); });
  t.appendChild(thead);
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    r.forEach((v) => { const td = document.createElement("td"); td.textContent = v ?? ""; tr.appendChild(td); });
    t.appendChild(tr);
  });
  return t;
}
function dlCSV(headers, rows, base) {
  const csv = matrixToCSV(headers, rows.map((r) => r.map((v) => v ?? "")));
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${base}-${todayStamp()}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
function dlXLSX(headers, rows, base, title) {
  exportTablesXLSX(
    [{ name: title, table: downloadTable(headers, rows), meta: { title } }],
    `${base}-${todayStamp()}`
  );
}

// ============================================================
// SHORT VOLUME — Reg SHO daily
// ============================================================
const svState = { range: "1y", stat: "vol", customStart: null, customEnd: null, reqId: 0, plot: null };

function svFilterRange(points) {
  if (svState.range === "custom" && svState.customStart && svState.customEnd)
    return points.filter(([d]) => d >= svState.customStart && d <= svState.customEnd);
  if (svState.range === "max") return points;
  const r = rangeById(svState.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-Math.round(r.months * 21));
}

async function drawSvChart() {
  const reqId = ++svState.reqId;
  const el = document.getElementById("sv-chart");
  const statsEl = document.getElementById("sv-chart-status");
  if (!el) return;
  if (statsEl) statsEl.textContent = "Loading…";
  try {
    const isRatio = svState.stat === "ratio";
    const [vol, ratio] = await Promise.all([
      getSeries("regsho-cnms-shortvol", "max"),
      getSeries("regsho-cnms-shortratio", "max"),
    ]);
    if (reqId !== svState.reqId) return;
    const vp = svFilterRange(vol.points ?? []), rp = svFilterRange(ratio.points ?? []);
    const byDate = new Map();
    for (const [d, v] of vp) byDate.set(d, [v, null]);
    for (const [d, v] of rp) { const cur = byDate.get(d) ?? [null, null]; cur[1] = v; byDate.set(d, cur); }
    const dates = [...byDate.keys()].sort();
    const xs = dates.map((d) => Date.parse(d) / 1000);
    const y1 = dates.map((d) => byDate.get(d)[0]);
    const y2 = dates.map((d) => byDate.get(d)[1]);
    if (svState.plot) { svState.plot.destroy(); svState.plot = null; }
    el.innerHTML = "";
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      svState.plot = new uPlot({
        width: Math.max(300, el.clientWidth || 900),
        height: 360,
        scales: { x: { time: true }, y: {}, y2: {} },
        series: [
          {},
          { label: "Short vol (sh)", stroke: "#2563eb", width: 1.5, scale: "y", spanGaps: true,
            show: !isRatio,
            values: (u, vs) => vs.map((v) => v == null ? "—" : v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : `${(v / 1e6).toFixed(0)}M`) },
          { label: "Short ratio", stroke: "#06b6d4", width: 1.5, scale: "y2", spanGaps: true,
            show: isRatio,
            value: (u, v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`) },
        ],
        axes: [axisStyle,
          { ...axisStyle, values: (u, vs) => vs.map((v) => v == null ? "—" : v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : `${(v / 1e6).toFixed(0)}M`) },
          { side: 1, scale: "y2", ...axisStyle, values: (u, vs) => vs.map((v) => v == null ? "—" : `${(v * 100).toFixed(0)}%`) }],
      }, [xs, y1, y2], el);
    }
    const rLbl = svState.range === "custom" ? `${svState.customStart}→${svState.customEnd}` : (rangeById(svState.range)?.label || svState.range);
    if (statsEl) {
      const rs = y2.filter((v) => v != null);
      const avg = (n) => rs.slice(-n).reduce((a, b) => a + b, 0) / Math.min(n, rs.length);
      statsEl.textContent = rs.length
        ? `Consolidated (CNMS) — ${dates.length} pts (${rLbl}) · short ratio latest ${pct1(rs[rs.length - 1])} · 1W avg ${pct1(avg(5))} · 1M avg ${pct1(avg(21))}`
        : `${dates.length} pts (${rLbl})`;
    }
  } catch (e) {
    if (statsEl) statsEl.textContent = `Chart unavailable — ${e.message}`;
  }
}

function syncSvControls() {
  document.querySelectorAll("#sv-stat button").forEach((b) =>
    b.classList.toggle("on", b.dataset.stat === svState.stat));
  document.querySelectorAll("#sv-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === svState.range));
  const customBox = document.getElementById("sv-range-custom");
  if (customBox) customBox.style.display = svState.range === "custom" ? "" : "none";
}

// Fill 1D deltas for Reg SHO markets (async, after render).
async function fillSvMarketDeltas() {
  const mktMap = { cnms: "cnms", fnyx: "fnyx", fnsq: "fnsq" };
  document.querySelectorAll("#panel-shortvol tr[data-mkt]").forEach(async (tr) => {
    const k = (mktMap[tr.dataset.mkt] || tr.dataset.mkt).toLowerCase();
    try {
      const [sv, sr] = await Promise.all([
        getSeries(`regsho-${k}-shortvol`, "1m").catch(() => null),
        getSeries(`regsho-${k}-shortratio`, "1m").catch(() => null),
      ]);
      const dS = d1Delta((sv?.points ?? []).map((p) => ({ d: p[0], v: p[1] })));
      const dR = d1Delta((sr?.points ?? []).map((p) => ({ d: p[0], v: p[1] })));
      const c1 = tr.querySelector("[data-d1short]"), c2 = tr.querySelector("[data-d1ratio]");
      if (c1) { c1.innerHTML = dCellSh(dS.nom, dS.pct); const bg = heatBg({ pct: dS.pct }); if (bg) c1.style.background = bg; }
      if (c2) { c2.innerHTML = dCellSh(dR.nom == null ? null : dR.nom * 100, dR.pct); const bg = heatBg({ pct: dR.pct }); if (bg) c2.style.background = bg; }
    } catch { /* leave placeholder */ }
  });
}

function svRichTopTable(r, ts) {
  const top50 = r.top50 ?? [];
  const tstats = (ts && ts.tickers) || {};
  const statsAsOf = ts && ts.as_of;
  const sectorTot = {};
  for (const t of top50) {
    const [, sec] = tickerMeta(t);
    sectorTot[sec] = (sectorTot[sec] ?? 0) + (t.short_volume ?? 0);
  }
  const top = top50.map((t) => {
    const [name, sec] = tickerMeta(t);
    const secPct = sectorTot[sec] ? t.short_volume / sectorTot[sec] : null;
    const s = tstats[t.symbol] || {};
    const vvc = (k) => vcCell(t.short_volume, t[k], false);
    const vrc = (k) => vcCell(t.short_ratio, t[k], true);
    const sma = ["sma20", "sma50", "sma200"].map((k, i) =>
      smaTri(s.price, s[k], ["20SMA", "50SMA", "200SMA"][i])).join(" ");
    return `<tr><td><b>${esc(t.symbol)}</b></td><td>${esc(name)}</td>` +
    `<td class="num">${pxFmt(s.price)}</td>` +
    `<td class="num">${chg(s.pct_1d)}</td>` +
    `<td class="num">${mcapFmt(s.mcap)}</td>` +
    `<td class="num">${peFmt(s.pe)}</td>` +
    `<td class="num">${chg(s.ytd)}</td>` +
    `<td>${spark1y(s.spark)}</td>` +
    `<td class="num">${s.off_high52 == null ? "—" : pct1(s.off_high52)}</td>` +
    `<td class="num">${rsBar(s.rs_1m)}</td>` +
    `<td class="num sma">${sma}</td>` +
    `<td class="num">${big(t.short_volume)}</td>` +
    `<td class="num">${pct1(t.short_ratio)}</td>` +
    vvc("short_chg_1d") + vvc("short_chg_1w") + vvc("short_chg_1m") + vvc("short_chg_1q") + vvc("short_chg_1y") + vvc("short_chg_3y") +
    vrc("ratio_chg_1d") + vrc("ratio_chg_1w") + vrc("ratio_chg_1m") + vrc("ratio_chg_1q") + vrc("ratio_chg_1y") + vrc("ratio_chg_3y") +
    `<td class="num" title="${esc(sec)} sector short vol in top-50">${secPct == null ? "—" : (secPct * 100).toFixed(1) + "%"}</td></tr>`;
  }).join("");
  return { html:
    `<h3>TOP SHORTED TICKERS <span class="muted">${top50.length} names · nominal + % changes 1D/1W/1M/1Q/1Y/3Y</span></h3>
    <div>${HEAT_LEGEND}</div>
    <div class="table-scroll"><table class="topshorted" data-sortable><tr><th>Symbol</th><th>Name</th>` +
    `<th colspan="9">Price action <span class="muted">${statsAsOf ? "as of " + esc(statsAsOf) : "stats pending"}</span></th>` +
    `<th>Short vol</th>` +
    `<th>Short ratio</th><th colspan="6">Δ short vol (nominal + %) — 1D | 1W | 1M | 1Q | 1Y | 3Y</th>` +
    `<th colspan="6">Δ short ratio (pp + %) — 1D | 1W | 1M | 1Q | 1Y | 3Y</th>` +
    `<th>% of sector short</th></tr><tr data-sort-row="off"><td colspan="2"></td>` +
    `<th>Price</th><th>%1D</th><th>Mkt cap</th><th>P/E</th><th>%YTD</th><th data-sort="off">1Y</th><th>Δ52wH</th><th>RS 1M</th><th data-sort="off">20/50/200</th>` +
    `<td colspan="2"></td>` +
    `<th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th><th>3Y</th>` +
    `<th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th><th>3Y</th><td></td></tr>${top}</table></div>
    <p class="muted">Δ cells show nominal change (bold) + % change (muted): short-vol nominal in shares, short-ratio nominal in percentage points. ` +
    `% of sector short = ticker short volume ÷ its GICS sector's total short volume within this top-50 — ` +
    `high values mean shorting is concentrated in the name, not spread across the sector. ` +
    `RS 1M = percentile rank of the 21-day return within this table (0-99). ` +
    `Δ52wH = % off the 52-week high. ` +
    `3Y deltas populate as daily history accumulates (currently ~2Y backfilled).</p>`,
    rows: top50.map((t) => {
      const [name, sec] = tickerMeta(t);
      return [t.symbol, name, sec, t.short_volume, t.short_ratio,
        t.short_chg_1d, t.short_chg_1w, t.short_chg_1m, t.short_chg_1q, t.short_chg_1y, t.short_chg_3y,
        t.ratio_chg_1d, t.ratio_chg_1w, t.ratio_chg_1m, t.ratio_chg_1q, t.ratio_chg_1y, t.ratio_chg_3y];
    }) };
}

export function renderShortVol(si, finraRegsho, tickerStats) {
  const body = document.querySelector("#panel-shortvol .panel-body");
  if (!body || body.dataset.init) return;
  body.dataset.init = "1";
  const s = si ?? {};
  const r = finraRegsho ?? s.regsho ?? null;
  const rangeBtns = RANGES.map((x) => `<button data-range="${x.id}" class="${x.id === svState.range ? "on" : ""}">${x.label}</button>`).join("");
  if (!r) {
    body.innerHTML = `<h3>SHORT VOLUME — REG SHO DAILY</h3><p class="muted">No Reg SHO data yet.</p>`;
    return;
  }
  const mkts = Object.entries(r.markets ?? {}).map(([k, m]) =>
    `<tr data-mkt="${esc(k)}"><td>${esc(m.label ?? k)}</td><td>${big(m.short)}</td>` +
    `<td>${big(m.total)}</td><td>${pct1(m.ratio)}</td>` +
    `<td class="num" data-d1short>…</td><td class="num" data-d1ratio>…</td></tr>`).join("");
  const mktRows = Object.entries(r.markets ?? {}).map(([k, m]) => [m.label ?? k, m.short, m.total, m.ratio]);
  const rich = svRichTopTable(r, tickerStats);
  const t = s.threshold;
  const secs = t?.securities ?? [];
  const thrRows = secs.map((x) => [x.symbol, x.name, x.category, x.reg_sho ? "Y" : "", x.rule4320 ? "Y" : ""]);
  const thrNote = t && t.count > secs.length ? ` <span class="muted">showing ${secs.length} of ${t.count}</span>` : "";
  body.innerHTML =
    `<h3>SHORT VOLUME — REG SHO DAILY <span class="muted">prior trading day · as of ${esc(r.as_of ?? "—")}</span></h3>
    <div class="star-dl-row">
      <button id="sv-dl-csv" class="mini-btn">⤓ CSV (markets + top 50 + threshold)</button>
      <button id="sv-dl-xlsx" class="mini-btn">⤓ XLSX (markets + top 50 + threshold)</button>
    </div>
    <div>${HEAT_LEGEND}</div><table data-sortable><tr><th>Market</th><th>Short vol (sh)</th><th>Total vol (sh)</th><th>Short ratio</th><th>1D Δ short vol</th><th>1D Δ ratio</th></tr>${mkts}</table>
    <h3>CONSOLIDATED SHORT VOLUME — TREND <span class="muted">CNMS · choose stat and dates</span></h3>
    <div class="trace-controls">
      <span class="seg" id="sv-stat">
        <button data-stat="vol" class="on" title="Short volume (shares)">Short vol</button><button data-stat="ratio" title="Short ratio">Short ratio</button>
      </span>
      <span class="seg" id="sv-range">${rangeBtns}</span>
      <span id="sv-range-custom" class="muted" style="display:none">
        <input type="date" id="sv-chart-start" aria-label="Start date"> →
        <input type="date" id="sv-chart-end" aria-label="End date">
        <button id="sv-chart-apply" class="mini-btn">Apply</button>
      </span>
    </div>
    <div id="sv-chart" class="trace-chart"></div>
    <div id="sv-chart-status" class="muted"></div>
    ${rich.html}
    <h3>THRESHOLD LIST — REG SHO <span class="muted">${t ? `${t.count} securities as of ${esc(t.as_of ?? "—")}` : "no data yet"}${thrNote}</span></h3>
    ${secs.length ? `<table data-sortable><tr><th>Symbol</th><th>Name</th><th>Category</th><th>Reg SHO</th><th>Rule 4320</th></tr>` +
      secs.map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td>${esc((x.name ?? "").slice(0, 48))}</td><td>${esc(x.category ?? "—")}</td><td>${x.reg_sho ? "Y" : "—"}</td><td>${x.rule4320 ? "Y" : "—"}</td></tr>`).join("") + `</table>`
      : `<p class="muted">List is empty.</p>`}`;

  const topHeaders = ["Symbol", "Name", "Sector", "Short vol (sh)", "Short ratio",
    "Δ vol 1D", "Δ vol 1W", "Δ vol 1M", "Δ vol 1Q", "Δ vol 1Y", "Δ vol 3Y",
    "Δ ratio 1D", "Δ ratio 1W", "Δ ratio 1M", "Δ ratio 1Q", "Δ ratio 1Y", "Δ ratio 3Y"];
  document.getElementById("sv-dl-csv").addEventListener("click", () =>
    dlCSV(["Market", "Short vol (sh)", "Total vol (sh)", "Short ratio"], mktRows, "short-volume-markets"));
  document.getElementById("sv-dl-xlsx").addEventListener("click", () =>
    dlXLSX(topHeaders, rich.rows, "short-volume-top50", "Top shorted tickers"));

  body.querySelectorAll("#sv-stat button").forEach((b) =>
    b.addEventListener("click", () => {
      if (svState.stat === b.dataset.stat) return;
      svState.stat = b.dataset.stat;
      syncSvControls();
      drawSvChart();
    }));
  body.querySelectorAll("#sv-range button").forEach((b) =>
    b.addEventListener("click", () => {
      svState.range = b.dataset.range;
      syncSvControls();
      if (svState.range !== "custom") drawSvChart();
    }));
  document.getElementById("sv-chart-apply").addEventListener("click", () => {
    const st = document.getElementById("sv-chart-start").value;
    const en = document.getElementById("sv-chart-end").value;
    if (!st || !en || st > en) return;
    svState.customStart = st; svState.customEnd = en; svState.range = "custom";
    syncSvControls();
    drawSvChart();
  });

  syncSvControls();
  drawSvChart();
  fillSvMarketDeltas();
}

// ============================================================
// MARGIN DEBT — FINRA monthly
// ============================================================
const MARGIN_SERIES = [
  { id: "finra-margin-debit", label: "Debit balances", unit: "$M" },
  { id: "finra-margin-credit-cash", label: "Free credit — cash accts", unit: "$M" },
  { id: "finra-margin-credit-margin", label: "Free credit — margin accts", unit: "$M" },
];
const mgState = { series: "finra-margin-debit", range: "max", customStart: null, customEnd: null, reqId: 0, plot: null };
const fmtM$ = (v) => v == null || !isFinite(v) ? "—" :
  `$${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "B" : Math.round(v).toLocaleString("en-US") + "M"}`;

function mgFilterRange(points) {
  if (mgState.range === "custom" && mgState.customStart && mgState.customEnd)
    return points.filter(([d]) => d >= mgState.customStart && d <= mgState.customEnd);
  if (mgState.range === "max") return points;
  const r = rangeById(mgState.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-r.months);
}

async function drawMgChart() {
  const reqId = ++mgState.reqId;
  const el = document.getElementById("mg-chart");
  const statusEl = document.getElementById("mg-chart-status");
  if (!el) return;
  if (statusEl) statusEl.textContent = "Loading…";
  try {
    const s = await getSeries(mgState.series, "max");
    if (reqId !== mgState.reqId) return;
    const pts = mgFilterRange(s.points ?? []).sort((a, b) => (a[0] < b[0] ? -1 : 1));
    if (mgState.plot) { mgState.plot.destroy(); mgState.plot = null; }
    el.innerHTML = "";
    const def = MARGIN_SERIES.find((x) => x.id === mgState.series);
    if (typeof uPlot !== "undefined" && pts.length > 1) {
      const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
      mgState.plot = new uPlot({
        width: Math.max(300, el.clientWidth || 900),
        height: 360,
        series: [{}, { label: `${def.label} ($M)`, stroke: "#2563eb", width: 1.5, spanGaps: true }],
        axes: [axisStyle, { ...axisStyle,
          values: (u, vs) => vs.map((v) => v == null ? "—" : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}T` : `$${(v / 1e3).toFixed(0)}B`) }],
      }, [pts.map(([d]) => Date.parse(d) / 1000), pts.map(([, v]) => v)], el);
    }
    const rLbl = mgState.range === "custom" ? `${mgState.customStart}→${mgState.customEnd}` : (rangeById(mgState.range)?.label || mgState.range);
    if (statusEl) statusEl.textContent =
      `${def.label} — ${pts.length} monthly points (${rLbl}) · FINRA margin statistics, $M · released ~3 weeks after month-end`;
  } catch (e) {
    if (statusEl) statusEl.textContent = `Chart unavailable — ${e.message}`;
  }
}

function syncMgControls() {
  const sel = document.getElementById("mg-series");
  if (sel) sel.value = mgState.series;
  document.querySelectorAll("#mg-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === mgState.range));
  const customBox = document.getElementById("mg-range-custom");
  if (customBox) customBox.style.display = mgState.range === "custom" ? "" : "none";
}

async function fillMarginTable() {
  const tbody = document.getElementById("mg-tbody");
  if (!tbody) return;
  const rows = await Promise.all(MARGIN_SERIES.map(async (def) => {
    try {
      const s = await getSeries(def.id, "max");
      const pts = (s.points ?? []).map(([d, v]) => ({ d, v })).sort((a, b) => a.d < b.d ? -1 : 1);
      if (!pts.length) return null;
      const cur = pts[pts.length - 1];
      const mgCell = (nom, pct) => {
        if ((nom == null || !isFinite(nom)) && (pct == null || !isFinite(pct))) return "—";
        const n = nom == null || !isFinite(nom) ? "—" : `${nom >= 0 ? "+" : "−"}${fmtM$(nom).replace("-", "")}`;
        const p = pct == null || !isFinite(pct) ? "—" : `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
        if (n === "—") return p;
        if (p === "—") return n;
        return `${n} <span class="muted">(${p})</span>`;
      };
      const cell = (days) => {
        const { nom, pct } = backDelta(pts, days);
        return `<td class="num"${heatStyle({ pct })}>${mgCell(nom, pct)}</td>`;
      };
      const stats = statsFromValues(pts.map((p) => p.v));
      return `<tr><td><b>${esc(def.label)}</b></td>` +
        `<td class="num"><b>${fmtM$(cur.v)}</b> <span class="muted">${cur.d.slice(0, 7)}</span></td>` +
        cell(31) + cell(93) + cell(365) + cell(1095) + rangeCells(stats, "full history") + `</tr>`;
    } catch { return null; }
  }));
  tbody.innerHTML = rows.filter(Boolean).join("") ||
    `<tr data-sort-row="off"><td colspan="8" class="muted">No margin history yet.</td></tr>`;
}

export function renderMargin(margin) {
  const body = document.querySelector("#panel-margin .panel-body");
  if (!body || body.dataset.init) return;
  body.dataset.init = "1";
  const m = margin ?? {};
  const rangeBtns = RANGES.map((x) => `<button data-range="${x.id}" class="${x.id === mgState.range ? "on" : ""}">${x.label}</button>`).join("");
  const seriesOpts = MARGIN_SERIES.map((x) => `<option value="${x.id}">${esc(x.label)}</option>`).join("");
  body.innerHTML =
    `<h3>MARGIN DEBT — FINRA <span class="muted">monthly · as of ${esc(m.as_of ?? "—")} · released ~3 weeks after month-end</span></h3>
    <div class="star-dl-row">
      <button id="mg-dl-csv" class="mini-btn">⤓ CSV (full history)</button>
      <button id="mg-dl-xlsx" class="mini-btn">⤓ XLSX (full history)</button>
    </div>
    <p class="muted">Debit balances in customers' securities margin accounts vs free credit balances. ` +
    `Rising debit + falling free credit = leverage building; the reverse = de-risking.</p>
    <div>${HEAT_LEGEND}</div><table data-sortable><tr><th>Series</th><th>Latest</th><th>1M Δ</th><th>3M Δ</th><th>1Y Δ</th><th>3Y Δ</th>${RANGE_TH}</tr>
    <tbody id="mg-tbody"><tr data-sort-row="off"><td colspan="8" class="muted">Loading history…</td></tr></tbody></table>
    <h3>MARGIN CHART <span class="muted">choose series and dates</span></h3>
    <div class="trace-controls">
      <label>Series <select id="mg-series">${seriesOpts}</select></label>
      <span class="seg" id="mg-range">${rangeBtns}</span>
      <span id="mg-range-custom" class="muted" style="display:none">
        <input type="date" id="mg-chart-start" aria-label="Start date"> →
        <input type="date" id="mg-chart-end" aria-label="End date">
        <button id="mg-chart-apply" class="mini-btn">Apply</button>
      </span>
    </div>
    <div id="mg-chart" class="trace-chart"></div>
    <div id="mg-chart-status" class="muted"></div>`;

  document.getElementById("mg-dl-csv").addEventListener("click", async () => {
    const cols = await Promise.all(MARGIN_SERIES.map(async (def) => {
      try { const s = await getSeries(def.id, "max"); return new Map((s.points ?? []).map(([d, v]) => [d, v])); }
      catch { return new Map(); }
    }));
    const dates = [...new Set(cols.flatMap((x) => [...x.keys()]))].sort();
    dlCSV(["Date", ...MARGIN_SERIES.map((x) => `${x.label} ($M)`)],
      dates.map((d) => [d, ...cols.map((c) => { const v = c.get(d); return v == null ? "" : v; })]),
      "margin-debt");
  });
  document.getElementById("mg-dl-xlsx").addEventListener("click", async () => {
    const cols = await Promise.all(MARGIN_SERIES.map(async (def) => {
      try { const s = await getSeries(def.id, "max"); return new Map((s.points ?? []).map(([d, v]) => [d, v])); }
      catch { return new Map(); }
    }));
    const dates = [...new Set(cols.flatMap((x) => [...x.keys()]))].sort();
    dlXLSX(["Date", ...MARGIN_SERIES.map((x) => `${x.label} ($M)`)],
      dates.map((d) => [d, ...cols.map((c) => { const v = c.get(d); return v == null ? "" : v; })]),
      "margin-debt", "FINRA margin debt (monthly)");
  });

  document.getElementById("mg-series").addEventListener("change", (e) => {
    mgState.series = e.target.value;
    drawMgChart();
  });
  body.querySelectorAll("#mg-range button").forEach((b) =>
    b.addEventListener("click", () => {
      mgState.range = b.dataset.range;
      syncMgControls();
      if (mgState.range !== "custom") drawMgChart();
    }));
  document.getElementById("mg-chart-apply").addEventListener("click", () => {
    const st = document.getElementById("mg-chart-start").value;
    const en = document.getElementById("mg-chart-end").value;
    if (!st || !en || st > en) return;
    mgState.customStart = st; mgState.customEnd = en; mgState.range = "custom";
    syncMgControls();
    drawMgChart();
  });

  syncMgControls();
  drawMgChart();
  fillMarginTable().catch(() => {});
}

// ============================================================
// SHORT INTEREST — FINRA settlement (biweekly)
// ============================================================
const sintState = { range: "max", customStart: null, customEnd: null, reqId: 0, plot: null };

function sintFilterRange(points) {
  if (sintState.range === "custom" && sintState.customStart && sintState.customEnd)
    return points.filter(([d]) => d >= sintState.customStart && d <= sintState.customEnd);
  if (sintState.range === "max") return points;
  const r = rangeById(sintState.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-r.months);
}

async function drawSintChart() {
  const reqId = ++sintState.reqId;
  const el = document.getElementById("sint-chart");
  const statusEl = document.getElementById("sint-chart-status");
  if (!el) return;
  if (statusEl) statusEl.textContent = "Loading…";
  try {
    const s = await getSeries("finra-short-total", "max");
    if (reqId !== sintState.reqId) return;
    const pts = sintFilterRange(s.points ?? []).sort((a, b) => (a[0] < b[0] ? -1 : 1));
    if (sintState.plot) { sintState.plot.destroy(); sintState.plot = null; }
    el.innerHTML = "";
    if (typeof uPlot !== "undefined" && pts.length > 1) {
      const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
      sintState.plot = new uPlot({
        width: Math.max(300, el.clientWidth || 900),
        height: 360,
        series: [{}, { label: "Total short (sh)", stroke: "#2563eb", width: 1.5, spanGaps: true }],
        axes: [axisStyle, { ...axisStyle,
          values: (u, vs) => vs.map((v) => v == null ? "—" : `${(v / 1e9).toFixed(1)}B`) }],
      }, [pts.map(([d]) => Date.parse(d) / 1000), pts.map(([, v]) => v)], el);
    }
    const rLbl = sintState.range === "custom" ? `${sintState.customStart}→${sintState.customEnd}` : (rangeById(sintState.range)?.label || sintState.range);
    if (statusEl) statusEl.textContent =
      `Total short interest, all listed securities — ${pts.length} biweekly settlement points (${rLbl})`;
  } catch (e) {
    if (statusEl) statusEl.textContent = `Chart unavailable — ${e.message}`;
  }
}

function syncSintControls() {
  document.querySelectorAll("#sint-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === sintState.range));
  const customBox = document.getElementById("sint-range-custom");
  if (customBox) customBox.style.display = sintState.range === "custom" ? "" : "none";
}

export function renderShortInt(si) {
  const body = document.querySelector("#panel-shortint .panel-body");
  if (!body || body.dataset.init) return;
  body.dataset.init = "1";
  const s = (si ?? {}).short_interest ?? null;
  const rangeBtns = RANGES.map((x) => `<button data-range="${x.id}" class="${x.id === sintState.range ? "on" : ""}">${x.label}</button>`).join("");
  if (!s) {
    body.innerHTML = `<h3>SHORT INTEREST — FINRA SETTLEMENT</h3><p class="muted">No short-interest data yet.</p>`;
    return;
  }
  const rows = Object.entries(s.tickers ?? {})
    .sort((a, b) => (b[1].short ?? 0) - (a[1].short ?? 0))
    .map(([sym, v]) => {
      const nom = v.short != null && v.prev != null ? v.short - v.prev : null;
      const n = nom == null ? "—" : `${nom > 0 ? "+" : ""}${big(Math.abs(nom))}`;
      const p = v.chg_pct == null ? "—" : `${v.chg_pct > 0 ? "+" : ""}${(v.chg_pct * 100).toFixed(1)}%`;
      const [wname] = tickerMeta({ name: v.name, symbol: sym });
      return { sym, short: v.short, n, p, adv: v.adv, dtc: v.dtc, name: wname,
        html: `<tr><td><b>${esc(sym)}</b></td><td>${esc(wname)}</td><td>${big(v.short)}</td>` +
        `<td${heatStyle({ pct: v.chg_pct })}>${n} <span class="muted">(${p})</span></td><td>${big(v.adv)}</td><td>${v.dtc ?? "—"}</td></tr>` };
    });
  body.innerHTML =
    `<h3>SHORT INTEREST — FINRA SETTLEMENT <span class="muted">as of ${esc(s.as_of ?? "—")}</span></h3>
    <div class="star-dl-row">
      <button id="sint-dl-csv" class="mini-btn">⤓ CSV (watchlist)</button>
      <button id="sint-dl-xlsx" class="mini-btn">⤓ XLSX (watchlist)</button>
    </div>
    <p class="muted">Biweekly settlement (15th and last business day of month), published ~8 business days later. ` +
    `Levels, not flow — compare with Reg SHO daily flow on the Short Volume tab.</p>
    <table><tr><th>Total short shares (all listed)</th></tr><tr><td><b>${big(s.total_short_shares)}</b></td></tr></table>
    <h3>TOTAL SHORT INTEREST — TREND <span class="muted">choose dates</span></h3>
    <div class="trace-controls">
      <span class="seg" id="sint-range">${rangeBtns}</span>
      <span id="sint-range-custom" class="muted" style="display:none">
        <input type="date" id="sint-chart-start" aria-label="Start date"> →
        <input type="date" id="sint-chart-end" aria-label="End date">
        <button id="sint-chart-apply" class="mini-btn">Apply</button>
      </span>
    </div>
    <div id="sint-chart" class="trace-chart"></div>
    <div id="sint-chart-status" class="muted"></div>
    <h3>WATCHLIST <span class="muted">change vs prior settlement</span></h3>
    <div>${HEAT_LEGEND}</div><table data-sortable><tr><th>Ticker</th><th>Name</th><th>Short (sh)</th><th>Δ vs prior settl.</th><th>Avg daily vol (sh)</th><th>Days to cover</th></tr>` +
    rows.map((r) => r.html).join("") + `</table>`;

  const wHeaders = ["Ticker", "Name", "Short (sh)", "Δ nominal (sh)", "Δ %", "Avg daily vol (sh)", "Days to cover"];
  const wRows = rows.map((r) => [r.sym, r.name, r.short, r.n, r.p, r.adv, r.dtc]);
  document.getElementById("sint-dl-csv").addEventListener("click", () => dlCSV(wHeaders, wRows, "short-interest-watchlist"));
  document.getElementById("sint-dl-xlsx").addEventListener("click", () => dlXLSX(wHeaders, wRows, "short-interest-watchlist", "FINRA short interest watchlist"));

  body.querySelectorAll("#sint-range button").forEach((b) =>
    b.addEventListener("click", () => {
      sintState.range = b.dataset.range;
      syncSintControls();
      if (sintState.range !== "custom") drawSintChart();
    }));
  document.getElementById("sint-chart-apply").addEventListener("click", () => {
    const st = document.getElementById("sint-chart-start").value;
    const en = document.getElementById("sint-chart-end").value;
    if (!st || !en || st > en) return;
    sintState.customStart = st; sintState.customEnd = en; sintState.range = "custom";
    syncSintControls();
    drawSintChart();
  });

  syncSintControls();
  drawSintChart();
}
