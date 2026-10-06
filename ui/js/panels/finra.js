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
import { getSeries } from "../api.js";
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
};
const tickerMeta = (sym) => TICKER_META[sym] ?? ["—", "Other"];
const chg = (x) => x == null ? "—" :
  `<span class="${x >= 0 ? "up" : "down"}">${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%</span>`;

function regshoSection(r) {
  if (!r) return `<h3>SHORT VOLUME — REG SHO DAILY</h3><p class="muted">No Reg SHO data yet.</p>`;
  const mkts = r.markets ?? {};
  const rows = Object.entries(mkts).map(([k, m]) =>
    `<tr><td>${m.label ?? k}</td><td>${big(m.short)}</td><td>${big(m.total)}</td>` +
    `<td>${pct1(m.ratio)}</td></tr>`).join("");
  const top50 = r.top50 ?? [];
  // % of sector short: ticker short vol / sector short vol within this top-50 set.
  const sectorTot = {};
  for (const t of top50) {
    const [, sec] = tickerMeta(t.symbol);
    sectorTot[sec] = (sectorTot[sec] ?? 0) + (t.short_volume ?? 0);
  }
  const top = top50.map((t) => {
    const [name, sec] = tickerMeta(t.symbol);
    const secPct = sectorTot[sec] ? t.short_volume / sectorTot[sec] : null;
    const vc = (k) => `<td class="num">${chg(t[k])}</td>`;
    return `<tr><td><b>${t.symbol}</b></td><td>${name}</td>` +
    `<td class="num">${big(t.short_volume)}</td>` +
    `<td class="num">${pct1(t.short_ratio)}</td>` +
    vc("short_chg_1d") + vc("short_chg_1w") + vc("short_chg_1m") + vc("short_chg_1q") + vc("short_chg_1y") +
    vc("ratio_chg_1d") + vc("ratio_chg_1w") + vc("ratio_chg_1m") + vc("ratio_chg_1q") + vc("ratio_chg_1y") +
    `<td class="num" title="${sec} sector short vol in top-50">${secPct == null ? "—" : (secPct * 100).toFixed(1) + "%"}</td></tr>`;
  }).join("");
  return `<h3>SHORT VOLUME — REG SHO DAILY <span class="muted">as of ${r.as_of ?? "—"}</span></h3>
    <table><tr><th>Market</th><th>Short vol</th><th>Total vol</th><th>Short ratio</th></tr>${rows}</table>
    <h3>TOP SHORTED TICKERS <span class="muted">${top50.length} names · 1D/1W/1M/1Q/1Y % changes</span></h3>
    <div class="table-scroll"><table class="topshorted"><tr><th>Symbol</th><th>Name</th><th>Short vol</th>` +
    `<th>Short ratio</th><th colspan="5">%Chg short vol — 1D | 1W | 1M | 1Q | 1Y</th>` +
    `<th colspan="5">%Chg short ratio — 1D | 1W | 1M | 1Q | 1Y</th>` +
    `<th>% of sector short</th></tr><tr><td colspan="4"></td>` +
    `<th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th>` +
    `<th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th><td></td></tr>${top}</table></div>
    <p class="muted">% of sector short = ticker short volume ÷ its GICS sector's total short volume within this top-50 — ` +
    `high values mean shorting is concentrated in the name, not spread across the sector. ` +
    `Longer-horizon deltas build from daily history going forward (full 1Y after a year of pulls).</p>`;
}

function thresholdSection(t) {
  if (!t) return `<h3>THRESHOLD LIST — REG SHO</h3><p class="muted">No threshold data yet.</p>`;
  const secs = (t.securities ?? []).map((s) =>
    `<tr><td><b>${s.symbol}</b></td><td>${(s.name ?? "").slice(0, 50)}</td>` +
    `<td>${s.category ?? "—"}</td></tr>`).join("");
  return `<h3>THRESHOLD LIST — REG SHO <span class="muted">${t.count} securities as of ${t.as_of ?? "—"}</span></h3>
    ${secs ? `<table><tr><th>Symbol</th><th>Name</th><th>Category</th></tr>${secs}</table>` : ""}`;
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

function bsDeltaCell(now, ref, kind) {
  if (now == null || ref == null) return "—";
  if (kind === "pct") {
    const c = ref ? (now - ref) / ref : null;
    return c == null ? "—" : chg(c);
  }
  const c = now - ref; // spreads/flows/counts: absolute point change
  return `<span class="${c >= 0 ? "up" : "down"}">${c >= 0 ? "+" : ""}${c.toLocaleString("en-US", { maximumFractionDigits: 1 })}</span>`;
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
    // delta table — Harry's universal 1D/1W/1M/1Q/1Y horizon standard
    const trows = BS_SERIES.map((s) => {
      const p = pts(s.id);
      const r = (d) => refBack(p, d);
      const r1 = r(1), r7 = r(7), r30 = r(30), r91 = r(91), r365 = r(365);
      const asof = p.length ? p[p.length - 1][0] : "—";
      const fmtNow = s.kind === "pct"
        ? (r1.now == null ? "—" : "$" + (r1.now / 1e3).toFixed(1) + "B")
        : (r1.now == null ? "—" : r1.now.toLocaleString("en-US", { maximumFractionDigits: 1 }) + " " + s.unit);
      const dc = (x) => `<td class="num">${bsDeltaCell(x.now, x.ref, s.kind)}</td>`;
      return `<tr><td><b>${s.label}</b>${s.note ? `<br><span class="muted">${s.note}</span>` : ""}</td>` +
        `<td class="num">${fmtNow}<br><span class="muted">${asof}</span></td>` +
        dc(r1) + dc(r7) + dc(r30) + dc(r91) + dc(r365) + `</tr>`;
    }).join("");
    host.innerHTML =
      `<table class="bs-deltas"><tr><th>Indicator</th><th>Now</th><th>1D</th><th>1W</th><th>1M</th><th>1Q</th><th>1Y</th></tr>${trows}</table>` +
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
  const html = Object.entries(lists).map(([slug, l]) => {
    const bonds = (l.bonds ?? []).map((bd) => {
      const cpn = bd.coupon != null ? `${Number(bd.coupon).toFixed(3)}%` : "—";
      const mat = (bd.maturity ?? "").slice(0, 10) || "—";
      const yld = bd.yield != null ? `${Number(bd.yield).toFixed(2)}%` : "—";
      const px = bd.last != null ? Number(bd.last).toFixed(2) : "—";
      return `<tr><td><b>${bd.symbol ?? "—"}</b></td><td>${(bd.issuer ?? "").slice(0, 40)}</td>` +
        `<td>${cpn}</td><td>${mat}</td><td>${yld}</td><td>${px}</td></tr>`;
    }).join("");
    return `<h3>MOST ACTIVE — ${slug.toUpperCase()} <span class="muted">${l.as_of ?? ""} · ${l.count} bonds</span></h3>` +
      (bonds ? `<table><tr><th>Symbol</th><th>Issuer</th><th>Coupon</th><th>Maturity</th><th>Yield</th><th>Price</th></tr>${bonds}</table>`
             : `<p class="muted">No bond rows.</p>`);
  }).join("");
  return `<h3>CORPORATE ACTIVITY — FINRA <span class="muted">as of ${c.as_of ?? "—"}</span></h3>` + html;
}

const GRADE_LABELS = { ig: "Investment Grade", hy: "High Yield", agcy: "Agency", "144a-ig": "144A IG", "144a-hy": "144A HY" };

function cappedSection(c) {
  if (!c) return `<h3>CAPPED VOLUME REPORT</h3><p class="muted">No capped-volume data yet — September pull pending.</p>`;
  const grades = c.grades ?? {};
  const rows = Object.entries(grades).map(([slug, g]) =>
    `<tr><td>${GRADE_LABELS[slug] ?? slug}</td>` +
    `<td>${g.avgsize == null ? "—" : "$" + Number(g.avgsize).toLocaleString("en-US", { maximumFractionDigits: 0 }) + "k"}</td>` +
    `<td>${usdM(g.total)}</td></tr>`).join("");
  return `<h3>CAPPED VOLUME — MONTHLY <span class="muted">${c.as_of ?? "—"} · ${c.months ?? 0} months</span></h3>
    ${rows ? `<table><tr><th>Grade</th><th>Avg capped size</th><th>Total par</th></tr>${rows}</table>`
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
    <table class="star-table"><tr><th>Product</th><th>$ Volume</th><th>Trades</th></tr>${rows}</table>
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
    regshoSection(f.regsho) +
    thresholdSection(f.threshold) +
    shortInterestSection(f.short_interest) +
    breadthSection(f.breadth) +
    corpSection(f.corp) +
    cappedSection(f.capped) +
    marginSection(f.margin) +
    traceSection(f.trace_treasury, f.trace_monthly);
  renderTraceCharts();
  renderTraceGrid();
  renderBreadthSentiment().catch(() => {});
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
