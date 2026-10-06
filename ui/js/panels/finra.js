// FINRA tab: every FINRA-sourced dataset in one place.
// Reads dash.panels.finra (backend _finra_panel).
// Sections: Reg SHO short volume (incl. FINRA TRF venue), threshold list,
// short interest, market breadth, most-active corporate bonds, capped volume,
// margin statistics, TRACE treasury/monthly, TRACE volume charts.
// The STRUCT cycle tab (TRACE series, short interest, margin, breadth,
// sentiment, corp bonds, Reg SHO, capped volume — all Now/Δ1M/Δ1Y tables)
// also renders on this tab via #cycle-struct; ICE Vantage moved to its own
// cycle tab on POS.
import { renderTraceCharts, setTraceChartProduct } from "./trace_charts.js";
import { renderTraceGrid } from "./trace_grid.js";
import { refiWallSection, renderOasIndexes } from "./refi_wall.js";
import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";
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

// Company name + GICS sector for heavily-shorted names (Reg SHO files carry
// no names). Covers the usual top-50 suspects; unmapped tickers show "—".
const TICKER_META = {
  NVDA: ["Nvidia", "Technology"], AAPL: ["Apple", "Technology"], MSFT: ["Microsoft", "Technology"],
  AMD: ["AMD", "Technology"], AVGO: ["Broadcom", "Technology"], INTC: ["Intel", "Technology"],
  QCOM: ["Qualcomm", "Technology"], TXN: ["Texas Instruments", "Technology"],
  AMAT: ["Applied Materials", "Technology"], LRCX: ["Lam Research", "Technology"],
  MU: ["Micron", "Technology"], KLAC: ["KLA", "Technology"], ADI: ["Analog Devices", "Technology"],
  MRVL: ["Marvell", "Technology"], ARM: ["Arm Holdings", "Technology"],
  SMCI: ["Super Micro", "Technology"], DELL: ["Dell", "Technology"], HPQ: ["HP", "Technology"],
  IBM: ["IBM", "Technology"], ORCL: ["Oracle", "Technology"], CRM: ["Salesforce", "Technology"],
  ADBE: ["Adobe", "Technology"], INTU: ["Intuit", "Technology"], NOW: ["ServiceNow", "Technology"],
  PANW: ["Palo Alto Networks", "Technology"], CRWD: ["CrowdStrike", "Technology"],
  FTNT: ["Fortinet", "Technology"], PLTR: ["Palantir", "Technology"], SNOW: ["Snowflake", "Technology"],
  DDOG: ["Datadog", "Technology"], NET: ["Cloudflare", "Technology"], MDB: ["MongoDB", "Technology"],
  SHOP: ["Shopify", "Technology"], XYZ: ["Block", "Technology"], PYPL: ["PayPal", "Technology"],
  COIN: ["Coinbase", "Financials"], MSTR: ["Strategy", "Technology"],
  TSLA: ["Tesla", "Consumer Discretionary"], AMZN: ["Amazon", "Consumer Discretionary"],
  HD: ["Home Depot", "Consumer Discretionary"], MCD: ["McDonald's", "Consumer Discretionary"],
  NKE: ["Nike", "Consumer Discretionary"], SBUX: ["Starbucks", "Consumer Discretionary"],
  BKNG: ["Booking", "Consumer Discretionary"], ABNB: ["Airbnb", "Consumer Discretionary"],
  RIVN: ["Rivian", "Consumer Discretionary"], LCID: ["Lucid", "Consumer Discretionary"],
  F: ["Ford", "Consumer Discretionary"], GM: ["General Motors", "Consumer Discretionary"],
  GME: ["GameStop", "Consumer Discretionary"], AMC: ["AMC Entertainment", "Communication Services"],
  GOOGL: ["Alphabet", "Communication Services"], GOOG: ["Alphabet", "Communication Services"],
  META: ["Meta", "Communication Services"], NFLX: ["Netflix", "Communication Services"],
  DIS: ["Disney", "Communication Services"], T: ["AT&T", "Communication Services"],
  VZ: ["Verizon", "Communication Services"], TMUS: ["T-Mobile", "Communication Services"],
  EA: ["Electronic Arts", "Communication Services"], TTWO: ["Take-Two", "Communication Services"],
  RBLX: ["Roblox", "Communication Services"], DJT: ["Trump Media", "Communication Services"],
  SNAP: ["Snap", "Communication Services"], PINS: ["Pinterest", "Communication Services"],
  JPM: ["JPMorgan", "Financials"], BAC: ["Bank of America", "Financials"],
  WFC: ["Wells Fargo", "Financials"], C: ["Citigroup", "Financials"],
  GS: ["Goldman Sachs", "Financials"], MS: ["Morgan Stanley", "Financials"],
  AXP: ["Amex", "Financials"], V: ["Visa", "Financials"], MA: ["Mastercard", "Financials"],
  COF: ["Capital One", "Financials"], SCHW: ["Charles Schwab", "Financials"],
  HOOD: ["Robinhood", "Financials"], SOFI: ["SoFi", "Financials"],
  BX: ["Blackstone", "Financials"], KKR: ["KKR", "Financials"], ARES: ["Ares", "Financials"],
  JNJ: ["Johnson & Johnson", "Healthcare"], UNH: ["UnitedHealth", "Healthcare"],
  LLY: ["Eli Lilly", "Healthcare"], PFE: ["Pfizer", "Healthcare"], MRK: ["Merck", "Healthcare"],
  ABBV: ["AbbVie", "Healthcare"], AMGN: ["Amgen", "Healthcare"], GILD: ["Gilead", "Healthcare"],
  BIIB: ["Biogen", "Healthcare"], REGN: ["Regeneron", "Healthcare"], VRTX: ["Vertex", "Healthcare"],
  ISRG: ["Intuitive Surgical", "Healthcare"], TMO: ["Thermo Fisher", "Healthcare"],
  DHR: ["Danaher", "Healthcare"], CVS: ["CVS Health", "Healthcare"], HCA: ["HCA", "Healthcare"],
  XOM: ["Exxon Mobil", "Energy"], CVX: ["Chevron", "Energy"], COP: ["ConocoPhillips", "Energy"],
  EOG: ["EOG Resources", "Energy"], SLB: ["SLB", "Energy"], OXY: ["Occidental", "Energy"],
  MPC: ["Marathon Petroleum", "Energy"], VLO: ["Valero", "Energy"],
  MARA: ["MARA Holdings", "Energy"], RIOT: ["Riot Platforms", "Energy"],
  BA: ["Boeing", "Industrials"], CAT: ["Caterpillar", "Industrials"], GE: ["GE Aerospace", "Industrials"],
  HON: ["Honeywell", "Industrials"], UPS: ["UPS", "Industrials"], FDX: ["FedEx", "Industrials"],
  LMT: ["Lockheed Martin", "Industrials"], RTX: ["RTX", "Industrials"],
  NOC: ["Northrop Grumman", "Industrials"], DAL: ["Delta", "Industrials"],
  UAL: ["United Airlines", "Industrials"],
  WMT: ["Walmart", "Consumer Staples"], COST: ["Costco", "Consumer Staples"],
  PG: ["Procter & Gamble", "Consumer Staples"], KO: ["Coca-Cola", "Consumer Staples"],
  PEP: ["PepsiCo", "Consumer Staples"],
  NEE: ["NextEra", "Utilities"], DUK: ["Duke Energy", "Utilities"],
  LIN: ["Linde", "Materials"], FCX: ["Freeport-McMoRan", "Materials"], NEM: ["Newmont", "Materials"],
  AMT: ["American Tower", "Real Estate"], PLD: ["Prologis", "Real Estate"],
  SPY: ["S&P 500 ETF", "ETF"], QQQ: ["Nasdaq 100 ETF", "ETF"], IWM: ["Russell 2000 ETF", "ETF"],
  TLT: ["20Y+ Treasury ETF", "ETF"], HYG: ["HY Bond ETF", "ETF"], LQD: ["IG Bond ETF", "ETF"],
  // resolved 2026-10-05 for the then-current top-shorted list (SEC + Yahoo);
  // the backend ticker master now resolves new names dynamically and these
  // stay as the offline fallback.
  FNGR: ["FingerMotion", "Technology"], FLUX: ["Flux Power", "Industrials"],
  QTEX: ["QTREX Quantum", "Technology"], AMOD: ["Alpha Modus", "Technology"],
  SDEV: ["Stablecoin Development", "Financials"], NIVF: ["NewGenIvf Group", "Healthcare"],
  AAL: ["American Airlines", "Industrials"], SCKT: ["Socket Mobile", "Technology"],
  SPCX: ["Space Exploration Technologies", "Industrials"], NU: ["Nu Holdings", "Financials"],
  SCNX: ["Scienture Holdings", "Healthcare"], NVD: ["2x Short NVDA ETF", "ETF"],
  BITO: ["ProShares Bitcoin ETF", "ETF"], SOXS: ["Semiconductor Bear 3X", "ETF"],
  DDC: ["DDC Enterprise", "Consumer Staples"], MSTZ: ["2X Inverse MSTR ETF", "ETF"],
  RWM: ["Short Russell 2000", "ETF"], CYCU: ["Cycurion", "Technology"],
  ONDS: ["Ondas Holdings", "Technology"], PLUG: ["Plug Power", "Industrials"],
  CTVA: ["Corteva", "Materials"],
};
const tickerMeta = (t) => {
  // Backend-resolved names (dynamic ticker master) take precedence; the
  // static map above is the offline fallback.
  if (t && typeof t === "object" && t.name) return [t.name, t.sector ?? "Other"];
  const sym = typeof t === "string" ? t : t?.symbol;
  return TICKER_META[sym] ?? ["—", "Other"];
};
const chg = (x) => x == null ? "—" :
  `<span class="${x >= 0 ? "up" : "down"}">${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%</span>`;
// Nominal (absolute) change implied by a % ratio: cur - cur/(1+pct).
const nomFromPct = (cur, pct) =>
  (cur == null || pct == null || !isFinite(pct) || 1 + pct === 0) ? null : cur - cur / (1 + pct);
const fmtShNom = (v) => v == null || !isFinite(v) ? "—" :
  `${v >= 0 ? "+" : "−"}` + (Math.abs(v) >= 1e9 ? (Math.abs(v) / 1e9).toFixed(2) + "B sh" :
    Math.abs(v) >= 1e6 ? (Math.abs(v) / 1e6).toFixed(1) + "M sh" :
    Math.abs(v) >= 1e3 ? (Math.abs(v) / 1e3).toFixed(1) + "K sh" :
    Math.round(Math.abs(v)).toLocaleString("en-US") + " sh");
// Delta cell for the top-shorted table: nominal (bold) + % (muted).
// isRatio: nominal shown in percentage points.
const vcCell = (cur, pct, isRatio) => {
  if ((pct == null || !isFinite(pct)) && cur == null) return `<td class="num">—</td>`;
  const nom = nomFromPct(cur, pct);
  const pHtml = chg(pct);
  if (nom == null) return `<td class="num">${pHtml}</td>`;
  const nTxt = isRatio
    ? `${nom >= 0 ? "+" : "−"}${(Math.abs(nom) * 100).toFixed(1)}pp`
    : fmtShNom(nom);
  const cls = nom >= 0 ? "up" : nom < 0 ? "down" : "";
  return `<td class="num"><span class="${cls}"><b>${nTxt}</b></span> <span class="muted">(${pHtml})</span></td>`;
};

// ---- Speculator-style per-ticker stats (Finnhub-backed; see ticker_stats
// fetcher). Rendered into the top-shorted table next to the short data. ----
const pxFmt = (x) => x == null ? "—" :
  "$" + x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Finnhub marketCapitalization is in $M.
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

function regshoSection(r, ts) {
  if (!r) return `<h3>SHORT VOLUME — REG SHO DAILY</h3><p class="muted">No Reg SHO data yet.</p>`;
  const mkts = r.markets ?? {};
  const rows = Object.entries(mkts).map(([k, m]) =>
    `<tr><td>${m.label ?? k}</td><td>${big(m.short)}</td><td>${big(m.total)}</td>` +
    `<td>${pct1(m.ratio)}</td></tr>`).join("");
  const top50 = r.top50 ?? [];
  const tstats = (ts && ts.tickers) || {};
  const statsAsOf = ts && ts.as_of;
  // % of sector short: ticker short vol / sector short vol within this top-50 set.
  const sectorTot = {};
  for (const t of top50) {
    const [, sec] = tickerMeta(t);
    sectorTot[sec] = (sectorTot[sec] ?? 0) + (t.short_volume ?? 0);
  }
  const top = top50.map((t) => {
    const [name, sec] = tickerMeta(t);
    const secPct = sectorTot[sec] ? t.short_volume / sectorTot[sec] : null;
    const s = tstats[t.symbol] || {};
    // Nominal + % deltas: nominal implied from current value and % ratio.
    const vvc = (k) => vcCell(t.short_volume, t[k], false);
    const vrc = (k) => vcCell(t.short_ratio, t[k], true);
    const sma = ["sma20", "sma50", "sma200"].map((k, i) =>
      smaTri(s.price, s[k], ["20SMA", "50SMA", "200SMA"][i])).join(" ");
    return `<tr><td><b>${t.symbol}</b></td><td>${name}</td>` +
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
    `<td class="num" title="${sec} sector short vol in top-50">${secPct == null ? "—" : (secPct * 100).toFixed(1) + "%"}</td></tr>`;
  }).join("");
  return `<h3>SHORT VOLUME — REG SHO DAILY <span class="muted">as of ${r.as_of ?? "—"}</span></h3>
    <table data-sortable><tr><th>Market</th><th>Short vol</th><th>Total vol</th><th>Short ratio</th></tr>${rows}</table>
    <h3>TOP SHORTED TICKERS <span class="muted">${top50.length} names · nominal + % changes 1D/1W/1M/1Q/1Y/3Y</span></h3>
    <div class="table-scroll"><table class="topshorted" data-sortable><tr><th>Symbol</th><th>Name</th>` +
    `<th colspan="9">Price action <span class="muted">${statsAsOf ? "as of " + statsAsOf : "stats pending"}</span></th>` +
    `<th>Short vol</th>` +
    `<th>Short ratio</th><th colspan="6">Δ short vol (nominal + %) — 1D | 1W | 1M | 1Q | 1Y | 3Y</th>` +
    `<th colspan="6">Δ short ratio (pp + %) — 1D | 1W | 1M | 1Q | 1Y | 3Y</th>` +
    `<th>% of sector short</th></tr><tr><td colspan="2"></td>` +
    `<th>Price</th><th>%1D</th><th>Mkt cap</th><th>P/E</th><th>%YTD</th><th data-sort="off">1Y</th><th>Δ52wH</th><th>RS 1M</th><th data-sort="off">20/50/200</th>` +
    `<td colspan="2"></td>` +
    `<th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th><th>3Y</th>` +
    `<th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th><th>3Y</th><td></td></tr>${top}</table></div>
    <p class="muted">Δ cells show nominal change (bold) + % change (muted): short-vol nominal in shares, short-ratio nominal in percentage points. ` +
    `% of sector short = ticker short volume ÷ its GICS sector's total short volume within this top-50 — ` +
    `high values mean shorting is concentrated in the name, not spread across the sector. ` +
    `RS 1M = percentile rank of the 21-day return within this table (0-99). ` +
    `Δ52wH = % off the 52-week high. ` +
    `3Y deltas populate as daily history accumulates (currently ~2Y backfilled).</p>`;
}

function thresholdSection(t) {
  if (!t) return `<h3>THRESHOLD LIST — REG SHO</h3><p class="muted">No threshold data yet.</p>`;
  const secs = (t.securities ?? []).map((s) =>
    `<tr><td><b>${s.symbol}</b></td><td>${(s.name ?? "").slice(0, 50)}</td>` +
    `<td>${s.category ?? "—"}</td></tr>`).join("");
  return `<h3>THRESHOLD LIST — REG SHO <span class="muted">${t.count} securities as of ${t.as_of ?? "—"}</span></h3>
    ${secs ? `<table data-sortable><tr><th>Symbol</th><th>Name</th><th>Category</th></tr>${secs}</table>` : ""}`;
}

function shortInterestSection(s) {
  if (!s) return `<h3>SHORT INTEREST — FINRA</h3><p class="muted">No short-interest data yet.</p>`;
  return `<h3>SHORT INTEREST — FINRA <span class="muted">settlement ${s.as_of ?? "—"}</span></h3>
    <table><tr><th>Total short shares</th></tr>
    <tr><td>${big(s.total_short_shares)}</td></tr></table>`;
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
    series: [{ id: "finra-breadth-corp-all-adspread", color: "#2563eb" }] },
  { el: "bs-ighy", title: "IG vs HY advance/decline spread",
    series: [{ id: "finra-breadth-corp-ig-adspread", color: "#2563eb", label: "IG" },
             { id: "finra-breadth-corp-hy-adspread", color: "#dc2626", label: "HY" }] },
  { el: "bs-netflow", title: "Dealer net customer flow — corporate ($M par)",
    series: [{ id: "finra-sent-corp-all-netflow", color: "#0891b2" }],
    foot: "Positive = customers net buying from dealers (risk-on); negative = net selling (risk-off)." },
  { el: "bs-bs", title: "Dealer buys vs sells from customers — corporate ($M par)",
    series: [{ id: "finra-sent-corp-all-dbuy-vol", color: "#16a34a", label: "Dealer buys" },
             { id: "finra-sent-corp-all-dsell-vol", color: "#dc2626", label: "Dealer sells" }] },
  { el: "bs-hilo", title: "52-week highs vs lows — corporate bonds",
    series: [{ id: "finra-breadth-corp-all-hi52", color: "#16a34a", label: "52wk highs" },
             { id: "finra-breadth-corp-all-lo52", color: "#dc2626", label: "52wk lows" }] },
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
  const data = [dates.map((d) => Date.parse(d) / 1000),
    ...defs.map((_, i) => dates.map((d) => byDate.get(d)[i]))];
  const axis = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
  new uPlot({
    width: Math.max(300, el.clientWidth || 720), height: 250,
    series: [{}, ...defs.map((s) => ({ label: s.label, stroke: s.color, width: 1.4, spanGaps: true }))],
    axes: [axis, { ...axis }],
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
      const dc = (x) => `<td class="num">${bsDeltaCell(x.now, x.ref, s.kind, s.unit)}</td>`;
      const rs = statsFromValues(p.map((pt) => pt[1]));
      return `<tr><td><b>${s.label}</b>${s.note ? `<br><span class="muted">${s.note}</span>` : ""}</td>` +
        `<td class="num">${fmtNow}<br><span class="muted">${asof}</span></td>` +
        dc(r1) + dc(r7) + dc(r30) + dc(r91) + dc(r365) + dc(r1095) +
        `${rangeCells(rs, "full history since Jan 2018")}</tr>`;
    }).join("");
    host.innerHTML =
      `<table class="bs-deltas" data-sortable><tr><th>Indicator</th><th>Now</th><th>1D Δ</th><th>1W Δ</th><th>1M Δ</th><th>1Q Δ</th><th>1Y Δ</th><th>3Y Δ</th>${RANGE_TH}</tr>${trows}</table>` +
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

function marginSection(m) {
  if (!m) return `<h3>MARGIN STATISTICS</h3><p class="muted">No margin data yet.</p>`;
  const debit = m.latest_debit_m;
  return `<h3>MARGIN STATISTICS <span class="muted">as of ${m.as_of ?? "—"}</span></h3>
    <table><tr><th>Debit balances</th><th>Trend (26 pts)</th></tr>
    <tr><td>${debit == null ? "—" : "$" + (debit / 1000).toFixed(1) + "B"}</td>
    <td>${spark(m.debit_hist)}</td></tr></table>
    <p class="muted">Debit balances in $M; chart shows recent history.</p>`;
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

const STAR_ROWS = [
  ["star-tba-par", "star-tba-trades", "TBA (all issuers)", "star-tba"],
  ["star-tba-umbs-par", null, "TBA — UMBS", "star-umbs"],
  ["star-tba-gnma-par", null, "TBA — GNMA", "star-gnma"],
  ["star-spec-par", "star-spec-trades", "Specified pools", "star-spec"],
  ["star-agcmo-par", "star-agcmo-trades", "Agency CMO", "star-agcmo"],
  ["star-nagcmo-par", "star-nagcmo-trades", "Non-agency CMO", "star-nagcmo"],
  ["star-nagcmbs-par", "star-nagcmbs-trades", "Non-agency CMBS", "star-nagcmbs"],
  ["star-agcmbs-par", "star-agcmbs-trades", "Agency CMBS", "star-agcmbs"],
  ["star-abs-par", "star-abs-trades", "ABS", "star-abs"],
  ["star-clo-par", "star-clo-trades", "CLO", "star-clo"],
];
function starSection(s) {
  if (!s || !s.latest) return `<h3>STRUCTURED PRODUCT ACTIVITY — STAR</h3><p class="muted">No STAR data yet — first pull pending.</p>`;
  const L = s.latest;
  const rows = STAR_ROWS.map(([parId, trId, label, chartId]) => {
    const par = L[parId], tr = trId ? L[trId] : null;
    return `<tr data-star-chart="${chartId}" title="Click to view ${label} chart">` +
      `<td><b>${label}</b></td>` +
      `<td class="num">${par == null ? "—" : "$" + (par / 1e9).toFixed(2) + "B"}</td>` +
      `<td class="num">${tr == null ? "—" : Math.round(tr).toLocaleString("en-US")}</td></tr>`;
  }).join("");
  return `<h3>STRUCTURED PRODUCT ACTIVITY — STAR <span class="muted">daily · as of ${s.as_of ?? "—"} · click a row for its trend chart</span></h3>
    <table class="star-table" data-sortable><tr><th>Product</th><th>$ Volume</th><th>Trades</th></tr>${rows}</table>
    <p class="muted">FINRA-ICE Data Services Structured Trading Activity Reports — the public equivalent of the ` +
    `login-walled ICE Vantage structured aggregates. Daily TBA/specified/CMO/CMBS/ABS/CLO activity by issuer and ` +
    `investment grade. Full trend lines in the TRACE chart above and the grid below.</p>`;
}

export function renderFinra(p) {
  const body = document.querySelector("#panel-finra .panel-body");
  if (!body) return;
  const f = p ?? {};
  body.innerHTML =
    `<h3>TRACE VOLUMES <span class="muted">monthly · click-and-drag to zoom · click a grid row for its chart</span></h3>
    <div class="trace-view-toggle seg" role="tablist">
      <button id="trace-view-chart" class="on">Chart</button><button id="trace-view-grid">Grid</button>
    </div>
    <div id="trace-chart-wrap"></div>
    <div id="trace-grid-wrap" hidden></div>` +
    starSection(f.star) +
    regshoSection(f.regsho, f.ticker_stats) +
    thresholdSection(f.threshold) +
    shortInterestSection(f.short_interest) +
    breadthSection(f.breadth) +
    corpSection(f.corp) +
    refiWallSection(f.corp?.refi_wall) +
    cappedSection(f.capped) +
    marginSection(f.margin) +
    traceSection(f.trace_treasury, f.trace_monthly);
  renderTraceCharts();
  renderTraceGrid(f.corp);
  renderBreadthSentiment().catch(() => {});
  renderOasIndexes().catch(() => {});
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
  // STAR row click-to-chart
  body.querySelectorAll("tr[data-star-chart]").forEach((tr) => {
    tr.style.cursor = "pointer";
    tr.addEventListener("click", () => {
      setView("chart");
      setTraceChartProduct(tr.dataset.starChart);
      chartWrap.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}
