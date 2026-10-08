// FINRA tab: FINRA-sourced fixed-income datasets.
// Reads dash.panels.finra (backend _finra_panel).
// Main TRACE tab (this file): TRACE volume chart + TRACE volume grid only.
// Everything else that used to render underneath (breadth & sentiment,
// most-active corporate bonds, refi wall, capped volume, TRACE feed status)
// now lives in an in-panel "TRACE Detail" subtab below.
// Short volume / short interest / margin moved to the EQUITY hub 2026-10-06;
// STAR moved to FLOW → STAR 2026-10-06.
// The STRUCT cycle tab (TRACE series, short interest, margin, breadth,
// sentiment, corp bonds, Reg SHO, capped volume — all Now/Δ1M/Δ1Y tables)
// also renders on this tab via #cycle-struct; ICE Vantage moved to its own
// cycle tab on POS.
import { renderTraceCharts } from "./trace_charts.js";
import { renderTraceGrid } from "./trace_grid.js";
import { tradingX } from "../tradingx.js";
import { refiWallSection, renderOasIndexes } from "./refi_wall.js";
import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
const big = (x) =>
  x == null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const pct1 = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const usdM = (x) =>
  x == null ? "—" : "$" + (x / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "M";

function spark(hist, w = 220, h = 44) {
  if (!hist || hist.length < 2) return `<span class="muted">no history</span>`;
  const vs = hist.map((p) => p.v).filter((v) => v != null);
  if (vs.length < 2) return `<span class="muted">no history</span>`;
  const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1;
  const pts = hist
    .map((p, i) => {
      if (p.v == null) return null;
      const x = (i / (hist.length - 1)) * w;
      const y = h - 3 - ((p.v - lo) / rng) * (h - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  const last = vs[vs.length - 1];
  const cls = last >= vs[0] ? "up" : "down";
  return `<svg width="${w}" height="${h}" class="spark"><polyline points="${pts}" fill="none" stroke="currentColor" class="${cls}" stroke-width="1.5"/></svg>`;
}

// ---- Market breadth & sentiment: full-history uPlot charts + delta table ----
const BS_SERIES = [
  { id: "finra-breadth-corp-all-adspread", label: "Corp A/D spread", unit: "ct", kind: "spread" },
  { id: "finra-breadth-corp-ig-adspread", label: "IG A/D spread", unit: "ct", kind: "spread" },
  { id: "finra-breadth-corp-hy-adspread", label: "HY A/D spread", unit: "ct", kind: "spread" },
  { id: "finra-sent-corp-all-netflow", label: "Dealer net customer flow (corp)", unit: "$M", kind: "spread",
    note: "dealer sells − dealer buys; + = customers net buying (risk-on)" },
  { id: "finra-breadth-corp-all-hi52", label: "Corp 52wk highs", unit: "ct", kind: "count" },
  { id: "finra-breadth-corp-all-lo52", label: "Corp 52wk lows", unit: "ct", kind: "count" },
  { id: "finra-breadth-corp-all-dvol", label: "Corp $ volume", unit: "$M", kind: "pct" },
];
const BS_CHART_DEFS = [
  { el: "bs-ad", title: "Corporate bond breadth — advance/decline spread",
    series: [{ id: "finra-breadth-corp-all-adspread", color: "#e8c96a" }] },
  { el: "bs-ighy", title: "IG vs HY advance/decline spread",
    series: [{ id: "finra-breadth-corp-ig-adspread", color: "#e8c96a", label: "IG" },
             { id: "finra-breadth-corp-hy-adspread", color: "#d9736a", label: "HY" }] },
  { el: "bs-netflow", title: "Dealer net customer flow — corporate ($M par)",
    series: [{ id: "finra-sent-corp-all-netflow", color: "#7fc9b5" }],
    foot: "Positive = customers net buying from dealers (risk-on); negative = net selling (risk-off)." },
  { el: "bs-bs", title: "Dealer buys vs sells from customers — corporate ($M par)",
    series: [{ id: "finra-sent-corp-all-dbuy-vol", color: "#8ab86f", label: "Dealer buys" },
             { id: "finra-sent-corp-all-dsell-vol", color: "#d9736a", label: "Dealer sells" }] },
  { el: "bs-hilo", title: "52-week highs vs lows — corporate bonds",
    series: [{ id: "finra-breadth-corp-all-hi52", color: "#8ab86f", label: "52wk highs" },
             { id: "finra-breadth-corp-all-lo52", color: "#d9736a", label: "52wk lows" }] },
];

function bsLineChart(el, defs) {
  const byDate = new Map();
  defs.forEach((s, i) => (s.points ?? []).forEach(([d, v]) => {
    const row = byDate.get(d) ?? defs.map(() => null);
    row[i] = v;
    byDate.set(d, row);
  }));
  const dates = [...byDate.keys()].sort();
  if (dates.length < 2) { el.innerHTML = `<span class="muted">no history</span>`; return; }
  const tx = tradingX(dates);
  const data = [tx.x,
    ...defs.map((_, i) => dates.map((d) => byDate.get(d)[i]))];
  const axis = { stroke: "#a89a83", grid: { stroke: "#38312a" } };
  new uPlot({
    width: Math.max(300, el.clientWidth || 720), height: 250,
    series: [{}, ...defs.map((s) => ({ label: s.label, stroke: s.color, width: 1.4, spanGaps: true }))],
    axes: [{ ...axis, values: tx.values }, { ...axis }],
  }, data, el);
}

function bsDeltaCell(now, ref, kind, unit) {
  if (now == null || ref == null) return "—";
  const nom = now - ref;
  const nomTxt = `${nom >= 0 ? "+" : "−"}${Math.abs(nom).toLocaleString("en-US", { maximumFractionDigits: 1 })}`;
  // % change vs |ref| (spreads/flows/counts can cross zero — % is magnitude only).
  const pct = ref !== 0 ? nom / Math.abs(ref) : null;
  const pctTxt = pct == null || !isFinite(pct) ? "—" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
  const cls = nom > 0 ? "up" : nom < 0 ? "down" : "flat";
  const unitTxt = unit ? ` ${unit}` : "";
  if (kind === "pct") {
    // $ volume: nominal $ change + % change.
    const nomUsd = `${nom >= 0 ? "+" : "−"}$${(Math.abs(nom) / 1e3).toFixed(1)}B`;
    return `<span class="${cls}"><b>${nomUsd}</b></span> <span class="muted">(${pctTxt})</span>`;
  }
  return `<span class="${cls}"><b>${nomTxt}${unitTxt}</b></span> <span class="muted">(${pctTxt})</span>`;
}

async function renderBreadthSentiment() {
  const host = document.getElementById("bs-charts");
  if (!host) return;
  try {
    const ids = [...new Set([
      ...BS_SERIES.map((s) => s.id),
      ...BS_CHART_DEFS.flatMap((c) => c.series.map((s) => s.id)),
    ])];
    const fetched = await Promise.all(ids.map((id) => getSeries(id, "max").catch(() => null)));
    const byId = Object.fromEntries(ids.map((id, i) => [id, fetched[i]]));
    const pts = (id) => (byId[id]?.points ?? []).slice().sort((a, b) => (a[0] < b[0] ? -1 : 1));
    const labelOf = (id, fb) => byId[id]?.name ?? fb;
    const refBack = (points, days) => {
      if (points.length < 2) return { now: null, ref: null };
      const last = points[points.length - 1];
      const target = Date.parse(last[0]) - days * 864e5;
      let ref = null;
      for (const [d, v] of points) if (Date.parse(d) <= target) ref = v;
      return { now: last[1], ref };
    };
    // delta table — Harry's universal 1D/1W/1M/1Q/1Y/3Y horizon standard (nominal + %)
    const trows = BS_SERIES.map((s) => {
      const p = pts(s.id);
      const r = (d) => refBack(p, d);
      const r1 = r(1), r7 = r(7), r30 = r(30), r91 = r(91), r365 = r(365), r1095 = r(1095);
      const asof = p.length ? p[p.length - 1][0] : "—";
      const fmtNow = s.kind === "pct"
        ? (r1.now == null ? "—" : "$" + (r1.now / 1e3).toFixed(1) + "B")
        : (r1.now == null ? "—" : r1.now.toLocaleString("en-US", { maximumFractionDigits: 1 }) + " " + s.unit);
      const dc = (x) => {
        const pct = x.now != null && x.ref != null && x.ref !== 0 ? (x.now - x.ref) / Math.abs(x.ref) : null;
        return `<td class="num"${heatStyle({ pct })}>${bsDeltaCell(x.now, x.ref, s.kind, s.unit)}</td>`;
      };
      const rs = statsFromValues(p.map((pt) => pt[1]));
      return `<tr><td><b>${s.label}</b>${s.note ? `<br><span class="muted">${s.note}</span>` : ""}</td>` +
        `<td class="num">${fmtNow}<br><span class="muted">${asof}</span></td>` +
        dc(r1) + dc(r7) + dc(r30) + dc(r91) + dc(r365) + dc(r1095) +
        `${rangeCells(rs, "full history since Jan 2018")}</tr>`;
    }).join("");
    host.innerHTML =
      `<div>${HEAT_LEGEND}</div><table class="bs-deltas" data-sortable><tr><th>Indicator</th><th>Now</th><th>1D Δ</th><th>1W Δ</th><th>1M Δ</th><th>1Q Δ</th><th>1Y Δ</th><th>3Y Δ</th>${RANGE_TH}</tr>${trows}</table>` +
      BS_CHART_DEFS.map((c) => `<h4>${c.title}</h4><div id="${c.el}" class="bs-chart"></div>` +
        (c.foot ? `<p class="muted">${c.foot}</p>` : "")).join("") +
      `<p class="muted">FINRA fixed-income breadth (advances/declines/52wk high-low) and sentiment ` +
      `(dealer buy/sell/inter-dealer flows), daily since Jan 2018 — the bond-market equivalents of equity ` +
      `advance/decline lines and put/call ratios. Equity put/call data is not in a free FINRA feed.</p>`;
    for (const c of BS_CHART_DEFS) {
      const el = document.getElementById(c.el);
      if (el) bsLineChart(el, c.series.map((s) => ({ ...s, points: pts(s.id), label: labelOf(s.id, s.label) })));
    }
  } catch (err) {
    host.innerHTML = `<p class="muted">Breadth/sentiment charts failed to load — ${err.message}</p>`;
  }
}

function breadthSection(b) {
  if (!b) return `<h3>MARKET BREADTH & SENTIMENT</h3><p class="muted">No breadth data yet.</p>`;
  return `<h3>MARKET BREADTH & SENTIMENT <span class="muted">as of ${b.as_of ?? "—"} · ${b.series_count ?? 0} series · full history since Jan 2018</span></h3>
    <div id="bs-charts"><p class="muted">Loading breadth & sentiment history…</p></div>`;
}

function corpSection(c) {
  if (!c) return `<h3>MOST-ACTIVE CORPORATE BONDS</h3><p class="muted">No corporate activity data yet.</p>`;
  const lists = c.lists ?? {};
  const pctCol = (x) => {
    if (x == null) return "—";
    const cls = x > 0 ? "up" : x < 0 ? "down" : "";
    const s = x > 0 ? "+" : "";
    return `<span class="${cls}">${s}${x.toFixed(2)}%</span>`;
  };
  const html = Object.entries(lists).map(([slug, l]) => {
    const bonds = (l.bonds ?? []).map((bd) => {
      const cpn = bd.coupon != null ? `${Number(bd.coupon).toFixed(3)}%` : "—";
      const ytm = bd.ytm_yrs != null ? `${Number(bd.ytm_yrs).toFixed(1)}y` : "";
      const mat = ((bd.maturity ?? "").slice(0, 10) || "—") + (ytm ? ` <span class="muted">${ytm}</span>` : "");
      const yld = bd.yield != null ? `${Number(bd.yield).toFixed(2)}%` : "—";
      const px = bd.last != null ? Number(bd.last).toFixed(2) : "—";
      const spr = bd.spread_bps != null ? `${Math.round(bd.spread_bps)}` : "—";
      const rating = bd.rating ?? "—";
      const spk = bd.spark && bd.spark.length >= 2
        ? spark(bd.spark.map((v) => ({ v })), 140, 36)
        : `<span class="muted">building…</span>`;
      return `<tr><td class="muted">${bd.rank ?? "—"}</td><td><b>${bd.symbol ?? "—"}</b></td>` +
        `<td>${(bd.issuer ?? "").slice(0, 32)}</td><td>${cpn}</td><td>${mat}</td>` +
        `<td>${rating}</td><td>${px}</td><td>${pctCol(bd.chg_pct)}</td>` +
        `<td>${yld}</td><td>${spr}</td><td>${pctCol(bd.d52hi_pct)}</td><td>${spk}</td></tr>`;
    }).join("");
    return `<h3>MOST ACTIVE — ${slug.toUpperCase()} <span class="muted">${l.as_of ?? ""} · ${l.count} bonds</span></h3>` +
      (bonds ? `<div class="tbl-wrap"><table class="bond-tbl" data-sortable><tr><th>#</th><th>Symbol</th><th>Issuer</th><th>Coupon</th><th>Maturity</th><th>Rating</th><th>Price</th><th>%1D</th><th>Yield</th><th>G-Spr bp</th><th>Δ52w Hi</th><th data-sort="off">1Y Price</th></tr>${bonds}</table></div>` +
      `<p class="muted foot">Rank = position in FINRA's most-active list (volume rank). G-Spr = G-spread vs interpolated Treasury par curve (DGS); "—" for convertibles. Ratings are Moody's/S&P as reported by FINRA. Δ52w Hi = price vs trailing-52w high. Sparklines build from daily history going forward.</p>`
             : `<p class="muted">No bond rows.</p>`);
  }).join("");
  return `<h3>CORPORATE ACTIVITY — FINRA <span class="muted">as of ${c.as_of ?? "—"}</span></h3>` + html;
}

const GRADE_LABELS = { ig: "Investment Grade", hy: "High Yield", agcy: "Agency", "144a-ig": "144A IG", "144a-hy": "144A HY" };

function cappedSection(c) {
  if (!c) return `<h3>CAPPED VOLUME REPORT</h3><p class="muted">No capped-volume data yet — September pull pending.</p>`;
  const grades = c.grades ?? {};
  const tot = Object.values(grades).reduce((a, g) => a + (g.total || 0), 0);
  const rows = Object.entries(grades).map(([slug, g]) =>
    `<tr><td>${GRADE_LABELS[slug] ?? slug}</td>` +
    `<td>${g.avgsize == null ? "—" : "$" + Number(g.avgsize).toLocaleString("en-US", { maximumFractionDigits: 0 }) + "k"}</td>` +
    `<td>${usdM(g.total)}</td></tr>`).join("");
  return `<h3>CAPPED VOLUME — MONTHLY <span class="muted">${c.as_of ?? "—"} · ${c.months ?? 0} months</span></h3>
    <p>Total capped par <b>${usdM(tot)}</b> across ${Object.keys(grades).length} grades.
    Full breakdown with deltas (1D/1W/1M/1Q/1Y/3Y/Custom), range sparklines and charts
    is in the TRACE volumes grid above — filter to the Capped rows.</p>
    ${rows ? `<table data-sortable><tr><th>Grade</th><th>Avg capped size</th><th>Total par</th></tr>${rows}</table>`
           : `<p class="muted">No grade rows — September pull pending.</p>`}`;
}

function traceSection(t, mo) {
  const tHtml = t
    ? `<tr><td>Treasury TRACE daily</td><td>${t.as_of ?? "—"}</td><td>${t.series_count ?? 0} series</td><td class="up">live</td></tr>`
    : `<tr><td>Treasury TRACE daily</td><td>—</td><td>—</td><td class="flat">no data</td></tr>`;
  const mHtml = mo && !mo.blocked
    ? `<tr><td>TRACE monthly (all products)</td><td>${mo.as_of ?? "—"}</td><td>—</td><td class="up">live</td></tr>`
    : `<tr><td>TRACE monthly (all products)</td><td>${(mo && mo.as_of) || "—"}</td><td>—</td><td class="down">blocked — FINRA CDN 403</td></tr>`;
  return `<h3>TRACE VOLUMES</h3>
    <table><tr><th>Feed</th><th>As of</th><th>Series</th><th>Status</th></tr>${tHtml}${mHtml}</table>`;
}

export function renderFinra(p) {
  const body = document.querySelector("#panel-finra .panel-body");
  if (!body) return;
  const f = p ?? {};
  // TRACE Detail subtab content: exactly the cards that used to render under
  // the TRACE volumes chart, in the same order as before.
  const detailHtml =
    breadthSection(f.breadth) +
    corpSection(f.corp) +
    refiWallSection(f.corp?.refi_wall) +
    cappedSection(f.capped) +
    traceSection(f.trace_treasury, f.trace_monthly);
  body.innerHTML =
    // In-panel subtab bar reusing the hub subtab underline pattern
    // (.sub-row buttons in terminal.css). NOTE: tabs.js / index.html are
    // intentionally untouched — the FLOW hub's real subtab list is unchanged;
    // this keeps the TRACE Volume tab itself to chart + grid only.
    `<nav class="sub-row finra-subtabs" role="tablist" aria-label="TRACE views">
      <button type="button" id="finra-tab-volume" class="active" role="tab" aria-selected="true">TRACE Volume</button>
      <button type="button" id="finra-tab-detail" role="tab" aria-selected="false">TRACE Detail</button>
    </nav>
    <div id="finra-volume-view" role="tabpanel">
      <h3>TRACE VOLUMES <span class="muted">monthly · click-and-drag to zoom · click a grid row for its chart</span></h3>
      <div class="trace-view-toggle seg" role="tablist">
        <button id="trace-view-chart" class="on">Chart</button><button id="trace-view-grid">Grid</button>
      </div>
      <div id="trace-chart-wrap"></div>
      <div id="trace-grid-wrap" hidden></div>
    </div>
    <div id="finra-detail-view" role="tabpanel" hidden>${detailHtml}</div>`;
  renderTraceCharts();
  renderTraceGrid(f.corp);
  // Detail-view cards that render width-sensitive uPlot charts (#bs-charts)
  // render eagerly as before; re-run once when the tab is first opened so
  // the charts measure their visible widths.
  let detailInit = false;
  const refreshDetailCharts = () => {
    if (detailInit) return;
    detailInit = true;
    renderBreadthSentiment().catch(() => {});
    renderOasIndexes().catch(() => {});
  };
  renderBreadthSentiment().catch(() => {});
  renderOasIndexes().catch(() => {});
  const volBtn = document.getElementById("finra-tab-volume");
  const detBtn = document.getElementById("finra-tab-detail");
  const volView = document.getElementById("finra-volume-view");
  const detView = document.getElementById("finra-detail-view");
  const setTab = (which) => {
    const showVol = which === "volume";
    volView.hidden = !showVol;
    detView.hidden = showVol;
    volBtn.classList.toggle("active", showVol);
    detBtn.classList.toggle("active", !showVol);
    volBtn.setAttribute("aria-selected", String(showVol));
    detBtn.setAttribute("aria-selected", String(!showVol));
    if (!showVol) refreshDetailCharts();
  };
  volBtn.addEventListener("click", () => setTab("volume"));
  detBtn.addEventListener("click", () => setTab("detail"));
  const chartBtn = document.getElementById("trace-view-chart");
  const gridBtn = document.getElementById("trace-view-grid");
  const chartWrap = document.getElementById("trace-chart-wrap");
  const gridWrap = document.getElementById("trace-grid-wrap");
  const setView = (which) => {
    const showChart = which === "chart";
    chartWrap.hidden = !showChart;
    gridWrap.hidden = showChart;
    chartBtn.classList.toggle("on", showChart);
    gridBtn.classList.toggle("on", !showChart);
  };
  chartBtn.addEventListener("click", () => setView("chart"));
  gridBtn.addEventListener("click", () => setView("grid"));
}
