// ETF FLOWS tab: AUM / NAV / shares / price / expense / yield from the iShares
// product screener (T+1, keyless) + FMP fallback (free key) + CoinLaw crypto
// CSV. Flows are derived: shares = AUM / NAV; net_flow(day) =
// (shares_t − shares_{t-1}) × nav_t. Prices via Yahoo chart API (keyless, 1Y
// history → immediate 1M/3M/1Y returns). Disc/Prem = (price − NAV)/NAV.
// Data: /api/dashboard "etfflows" panel + /api/series etf:{TICKER}:{metric}.
import { getSeries } from "../api.js";
import { fmtAge } from "../fmt.js";
import { heatStyle } from "../heatmap.js";
import { rangeCells, statsFromValues, RANGE_LEGEND } from "../rangeviz.js";
import { symNameHtml } from "../names.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const seriesCache = {};

async function seriesPts(sid) {
  let pts = seriesCache[sid];
  if (!pts) {
    const j = await getSeries(sid, "max");
    pts = (j.points ?? []).map(([d, v]) => [d, Number(v)]).filter(([, v]) => isFinite(v));
    seriesCache[sid] = pts;
  }
  return pts;
}

function money(v) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toFixed(0)}`;
}

function signedMoney(v) {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "−") + money(Math.abs(v)).slice(0);
}

function pct(v, digits = 1) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}%`;
}

/** Net flows from consecutive share snapshots: (shares_t − shares_{t-1}) × nav_t.
 *  Quarterly SEC-XBRL checkpoints (>10d gaps) are skipped — daily flows only. */
async function fundFlows(t) {
  const shares = await seriesPts(`etf:${t}:shares`);
  const nav = await seriesPts(`etf:${t}:nav`);
  if (shares.length < 2) return null;
  const navByD = Object.fromEntries(nav);
  const flows = [];
  for (let i = 1; i < shares.length; i++) {
    const [d, s1] = shares[i];
    const [d0, s0] = shares[i - 1];
    if ((new Date(d) - new Date(d0)) / 86400000 > 10) continue;
    const n = navByD[d] ?? navByD[d0];
    if (n == null) continue;
    flows.push([d, (s1 - s0) * n]);
  }
  return flows;
}

/** Price return over ~n sessions from the price series. */
async function priceReturn(t, sessions) {
  const px = await seriesPts(`etf:${t}:price`);
  if (px.length < 2) return null;
  const last = px[px.length - 1][1];
  const idx = Math.max(0, px.length - 1 - sessions);
  const base = px[idx][1];
  if (!base) return null;
  return (last / base - 1) * 100;
}

function kpiTile(label, val, sub) {
  return `<div class="kpi"><div class="kpi-label">${label}</div>` +
    `<div class="kpi-value">${val ?? "—"}</div>` +
    `<div class="kpi-sub muted">${sub ?? ""}</div></div>`;
}

const heatFlow = (v) => v == null || v === 0 ? ""
  : heatStyle({ z: v > 0 ? 1.5 : -1.5 });

async function leagueRow(t, f) {
  const [aumPts, pricePts, navPts] = await Promise.all([
    seriesPts(`etf:${t}:aum`), seriesPts(`etf:${t}:price`),
    seriesPts(`etf:${t}:nav`),
  ]);
  const flows = await fundFlows(t);
  const aum = f.aum;
  const price = pricePts.length ? pricePts[pricePts.length - 1][1] : null;
  const nav = navPts.length ? navPts[navPts.length - 1][1]
    : (f.nav ?? null);
  const disprem = (price != null && nav) ? (price / nav - 1) * 100 : null;
  const vals = aumPts.map(([, v]) => v);
  const s = vals.length >= 3 ? statsFromValues(vals) : { z: null, pct: null };
  const sumLast = (n) => {
    if (!flows || flows.length < n) return null;
    return flows.slice(-n).reduce((a, [, v]) => a + v, 0);
  };
  let f1 = sumLast(1), f5 = sumLast(5), f21 = sumLast(21);
  let wNote = "";
  if (f5 == null && f.source === "coinlaw") {
    const wpts = await seriesPts(`etf:${t}:flow7d`);
    if (wpts.length) { f5 = wpts[wpts.length - 1][1]; wNote = " (reported wk)"; }
  }
  const [r1m, r3m, r1y] = await Promise.all([
    priceReturn(t, 21), priceReturn(t, 63), priceReturn(t, 252)]);
  const expense = f.expense;
  const divy = f.divyield;
  const flowCell = (v, note = "") => v == null ? `<td class="num muted">—</td>`
    : `<td class="num"${heatFlow(v)} title="${esc(note)}"><b>${signedMoney(v)}</b>${note ? `<span class="muted">${esc(note)}</span>` : ""}</td>`;
  const retCell = (v) => v == null ? `<td class="num muted">—</td>`
    : `<td class="num"${heatStyle({ z: v === 0 ? 0 : (v > 0 ? 1 : -1) })}><b>${pct(v)}</b></td>`;
  const subTag = f.class === "crypto" && f.sub
    ? ` <span class="muted" style="font-size:10px" title="${f.sub === "spot" ? "Holds the underlying coin" : "Crypto-adjacent equities / futures"}">${f.sub === "spot" ? "SPOT" : "EQUITY"}</span>` : "";
  const bdcTag = f.class === "bdc"
    ? ` <span class="muted" style="font-size:10px" title="Market cap (not AUM)">MCAP</span>` : "";
  return `<tr><td class="sym">${symNameHtml(t)}${subTag}${bdcTag}</td>` +
    `<td class="num"><b>${price == null ? "—" : "$" + price.toFixed(2)}</b></td>` +
    `<td class="num">${nav == null ? "—" : "$" + nav.toFixed(2)}</td>` +
    (disprem == null ? `<td class="num muted">—</td>`
      : `<td class="num"${heatStyle({ z: Math.abs(disprem) < 0.05 ? 0 : (disprem > 0 ? 1 : -1) })} title="Premium (+) / discount (−) to NAV"><b>${pct(disprem, 2)}</b></td>`) +
    `${flowCell(f1)}${flowCell(f5, wNote)}${flowCell(f21)}` +
    `${retCell(r1m)}${retCell(r3m)}${retCell(r1y)}` +
    `<td class="num"><b>${money(aum)}</b></td>` +
    `<td class="num">${expense == null ? "—" : expense.toFixed(2) + "%"}</td>` +
    `<td class="num">${divy == null ? "—" : divy.toFixed(2) + "%"}</td>` +
    `${rangeCells({ z: s.z, pct: s.pct }, "AUM history")}` +
    `<td class="muted">${esc(f.source ?? "")}</td></tr>`;
}

function flowsChart(flows, title) {
  if (!flows || flows.length < 2) return `<div class="muted">Not enough history yet — flows accumulate daily from the first run.</div>`;
  const W = 720, H = 240, padL = 60, padR = 12, padT = 14, padB = 30;
  const mx = Math.max(...flows.map(([, v]) => Math.abs(v)), 1);
  const X = (i) => padL + (i / (flows.length - 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v + mx) / (2 * mx)) * (H - padT - padB);
  const bw = Math.max(1.5, (W - padL - padR) / flows.length - 1);
  let s = `<div class="panel-subhead"><span>${esc(title)}</span></div>`;
  s += `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:760px;display:block" role="img">`;
  s += `<line x1="${padL}" y1="${Y(0)}" x2="${W - padR}" y2="${Y(0)}" stroke="#9aa4b2" stroke-width="1"/>`;
  flows.forEach(([d, v], i) => {
    const x = X(i) - bw / 2;
    const y0 = Y(0), y1 = Y(v);
    s += `<rect x="${x.toFixed(1)}" y="${Math.min(y0, y1).toFixed(1)}" width="${bw}" height="${Math.abs(y1 - y0).toFixed(1)}" fill="${v >= 0 ? "#4f9cf0" : "#e05c5c"}"><title>${d}: ${signedMoney(v)}</title></rect>`;
  });
  s += `<text x="${padL}" y="${H - 10}" font-size="10" fill="#9aa4b2">${flows[0][0]}</text>`;
  s += `<text x="${W - padR - 62}" y="${H - 10}" font-size="10" fill="#9aa4b2">${flows[flows.length - 1][0]}</text>`;
  s += `</svg>`;
  return s;
}

let etfClass = "all"; // asset-class filter

const CLASS_ORDER = ["equity", "fi-treasury", "fi-ig", "fi-hy", "fi-mbs",
  "fi-muni", "fi-tips", "fi-loans", "fi-conv", "fi-agg", "fi-intl",
  "fi-floater", "fi-abs", "fi-cmbs",
  "privcredit", "bdc", "commodity", "crypto", "realestate", "leveraged", "ai", "other"];
const CLASS_LABELS_UI = {
  equity: "Equities",
  "fi-treasury": "FI: Treasury", "fi-ig": "FI: IG", "fi-hy": "FI: High Yield",
  "fi-mbs": "FI: MBS", "fi-muni": "FI: Munis", "fi-tips": "FI: TIPS",
  "fi-loans": "FI: Loans/CLO", "fi-conv": "FI: Conv/Pfd",
  "fi-agg": "FI: Aggregate", "fi-intl": "FI: International",
  "fi-floater": "FI: Floaters", "fi-abs": "FI: ABS", "fi-cmbs": "FI: CMBS",
  privcredit: "Private Credit", bdc: "BDCs",
  commodity: "Commodities", crypto: "Bitcoin & Digital",
  realestate: "Real Estate", leveraged: "Leveraged/Inverse", ai: "AI",
  other: "Other",
};
const CLASS_DESC = {
  leveraged: "Specialized ETFs that use financial derivatives to amplify or reverse the daily returns of an underlying index, sector, or single stock.",
  crypto: "Spot-holding ETFs (IBIT, FBTC, ETHA…) own the underlying coin; crypto-equity ETFs (miners, exchanges, futures like BITO) track crypto-adjacent stocks. Tagged SPOT / EQUITY.",
  bdc: "Business Development Companies — publicly traded lenders to middle-market firms. Equity-like with high distribution yields, tracked here for income comparison. Size shown is market cap, not AUM; no creations/redemptions.",
};

// ---------------------------------------------------------------------------
// Breakdown charts (Harry 2026-10-06): nominal $ bars + % share toggle,
// click a bar to filter the league table.
// ---------------------------------------------------------------------------
let breakdownMode = "nominal"; // "nominal" | "pct"
let breakdownFilter = null;    // {kind, key, label, tickers[]} | null

// Money-market ETFs carved out of FI for the asset-class breakdown.
const MM_TICKERS = new Set(["SHV", "BIL", "SGOV", "TBIL", "USFR"]);

const FI_CLASSES = ["fi-treasury", "fi-ig", "fi-hy", "fi-mbs", "fi-muni",
  "fi-tips", "fi-loans", "fi-conv", "fi-agg", "fi-intl",
  "fi-floater", "fi-abs", "fi-cmbs"];

// Duration buckets for fixed-income tickers (Harry 2026-10-06).
const DUR_SHORT = new Set(["SHY", "SHV", "SGOV", "TBIL", "BIL", "VGSH",
  "SPSB", "VCSH", "IGSB", "SHYG", "SJNK", "STIP", "VTIP", "SUB", "SHM",
  "FLOT", "TFLO", "FLRN", "USFR", "JABS",
  "BKLN", "SRLN", "JAAA", "CLOX", "CLOI", "JBBB", "AAA"]);
const DUR_LONG = new Set(["TLT", "VGLT", "SCHQ", "SPTL", "EDV", "ZROZ",
  "TYD", "UST", "LQD", "LQDH", "ANGL", "PFF", "PGX", "PFXF", "HYD",
  "EMB", "VWOB", "PCY"]);
// Belly = FI tickers not in DUR_SHORT / DUR_LONG.

// Sentiment buckets (Harry 2026-10-06).
const LEV_LONG = new Set(["TQQQ", "SPXL", "UPRO", "TNA", "QLD", "TECL",
  "FNGU", "SOXL", "LABU", "NUGT", "JNUG", "FAS", "EDC", "YINN", "TMF",
  "BULZ", "TSLL", "NVDL"]);
const INVERSE = new Set(["SQQQ", "SPXS", "SDS", "TZA", "QID", "TECS",
  "FNGD", "SOXS", "LABD", "DUST", "JDST", "FAZ", "EDZ", "YANG", "TMV",
  "TBT", "BERZ", "SVXY"]);
const VIX_FEAR = new Set(["UVXY"]);
// Unleveraged = everything else.

// Sector map for equity ETFs (Harry 2026-10-06).
const SECTOR_MAP = { XLE: "Energy", XLF: "Financials", XLK: "Technology" };

// Market-cap buckets (Harry 2026-10-06).
const CAP_SMALL = new Set(["IWM", "IJR", "TNA", "TZA"]);
const CAP_MID = new Set(["IJH"]);
const CAP_LARGE = new Set(["SPY", "VOO", "IVV", "DIA", "QQQ", "VTI",
  "XLK", "XLF", "XLE", "IVW", "IVE", "IWD", "IWF", "QUAL", "USMV", "MTUM",
  "VEA", "IEFA", "IDEV", "ACWI", "ITOT", "IXUS", "EFA", "VWO", "IEMG",
  "EEM", "EMXC", "SPXL", "SPXS", "UPRO", "SDS", "TQQQ", "SQQQ", "QLD",
  "QID", "TECL", "TECS", "FNGU", "FNGD"]);

function buildBreakdowns(funds) {
  const tickers = Object.keys(funds);
  const cls = (t) => funds[t].class || "other";
  const inTickers = (set) => tickers.filter((t) => set.has(t));

  // 1. Asset class
  const assetBuckets = [
    { key: "equity", label: "Equity",
      tickers: tickers.filter((t) => cls(t) === "equity") },
    { key: "fi", label: "Fixed Income",
      tickers: tickers.filter((t) => FI_CLASSES.includes(cls(t)) && !MM_TICKERS.has(t)) },
    { key: "mm", label: "Money Market", tickers: inTickers(MM_TICKERS) },
    { key: "digital", label: "Bitcoin & Digital",
      tickers: tickers.filter((t) => cls(t) === "crypto") },
    { key: "privcredit", label: "Private Credit",
      tickers: tickers.filter((t) => cls(t) === "privcredit") },
    { key: "bdc", label: "BDCs",
      tickers: tickers.filter((t) => cls(t) === "bdc") },
    { key: "commodity", label: "Commodities",
      tickers: tickers.filter((t) => cls(t) === "commodity") },
    { key: "realestate", label: "Real Estate",
      tickers: tickers.filter((t) => cls(t) === "realestate") },
    { key: "leveraged", label: "Leveraged/Inverse",
      tickers: tickers.filter((t) => cls(t) === "leveraged") },
    { key: "ai", label: "AI/Thematic",
      tickers: tickers.filter((t) => cls(t) === "ai") },
  ];

  // 2. Duration (fixed income only)
  const fiTickers = tickers.filter((t) => FI_CLASSES.includes(cls(t)));
  const durationBuckets = [
    { key: "short", label: "Short (0–3Y / floating)",
      tickers: fiTickers.filter((t) => DUR_SHORT.has(t)) },
    { key: "belly", label: "Belly (3–10Y)",
      tickers: fiTickers.filter((t) => !DUR_SHORT.has(t) && !DUR_LONG.has(t)) },
    { key: "long", label: "Long (10Y+)",
      tickers: fiTickers.filter((t) => DUR_LONG.has(t)) },
  ];

  // 3. Sentiment
  const sentimentBuckets = [
    { key: "levlong", label: "Leveraged Long",
      tickers: inTickers(LEV_LONG), color: "#2e7d32" },
    { key: "inverse", label: "Inverse / Short",
      tickers: inTickers(INVERSE), color: "#c62828" },
    { key: "vix", label: "Volatility (VIX fear gauge)",
      tickers: inTickers(VIX_FEAR), color: "#ef6c00" },
    { key: "unlev", label: "Unleveraged",
      tickers: tickers.filter((t) => !LEV_LONG.has(t) && !INVERSE.has(t) && !VIX_FEAR.has(t)),
      color: "#5b7fa6" },
  ];

  // 4. Sectors (equity ETFs)
  const eqTickers = tickers.filter((t) => cls(t) === "equity");
  const sectorBuckets = [
    { key: "tech", label: "Technology",
      tickers: eqTickers.filter((t) => SECTOR_MAP[t] === "Technology") },
    { key: "fin", label: "Financials",
      tickers: eqTickers.filter((t) => SECTOR_MAP[t] === "Financials") },
    { key: "energy", label: "Energy",
      tickers: eqTickers.filter((t) => SECTOR_MAP[t] === "Energy") },
    { key: "broad", label: "Broad Market",
      tickers: eqTickers.filter((t) => !SECTOR_MAP[t]) },
  ];

  // 5. Market cap
  const capBuckets = [
    { key: "large", label: "Large Cap", tickers: inTickers(CAP_LARGE) },
    { key: "mid", label: "Mid Cap", tickers: inTickers(CAP_MID) },
    { key: "small", label: "Small Cap (Russell)",
      tickers: inTickers(CAP_SMALL) },
    { key: "other", label: "Other / Blended",
      tickers: tickers.filter((t) => !CAP_LARGE.has(t) && !CAP_MID.has(t) && !CAP_SMALL.has(t)) },
  ];

  return [
    { kind: "asset", title: "BY ASSET CLASS",
      note: "Money Market carved out of short Treasury bills",
      buckets: assetBuckets },
    { kind: "duration", title: "BY DURATION — FIXED INCOME",
      note: "Short = 0–3Y + all floaters/CLOs · Belly = 3–10Y · Long = 10Y+",
      buckets: durationBuckets },
    { kind: "sentiment", title: "BY SENTIMENT",
      note: "Inverse AUM rising = bearish positioning · UVXY = fear gauge (high VIX = negative)",
      buckets: sentimentBuckets },
    { kind: "sector", title: "BY SECTOR — EQUITIES",
      note: "Sector SPDRs mapped; broad-market ETFs grouped",
      buckets: sectorBuckets },
    { kind: "cap", title: "BY MARKET CAP",
      note: "Small = Russell 2000 + small-cap leveraged",
      buckets: capBuckets },
  ];
}

function breakdownBarChart(bd, funds) {
  const rows = bd.buckets.map((b) => {
    let aum = 0, n = 0;
    for (const t of b.tickers) {
      const f = funds[t];
      if (f && f.aum) { aum += f.aum; n++; }
    }
    return { ...b, aum, n };
  }).filter((r) => r.n > 0);
  const total = rows.reduce((a, r) => a + r.aum, 0);
  const max = Math.max(...rows.map((r) => r.aum), 1);
  const isPct = breakdownMode === "pct";
  const bars = rows
    .sort((a, b) => b.aum - a.aum)
    .map((r) => {
      const val = isPct ? (total ? (r.aum / total) * 100 : 0) : r.aum;
      const valTxt = isPct ? val.toFixed(1) + "%" : money(r.aum);
      const w = Math.max(2, (r.aum / max) * 100);
      const active = breakdownFilter && breakdownFilter.kind === bd.kind &&
        breakdownFilter.key === r.key;
      const color = r.color || "#3b7dd8";
      return `<div class="bd-row${active ? " bd-active" : ""}" data-bd-kind="${bd.kind}" data-bd-key="${esc(r.key)}" style="cursor:pointer" title="Click to filter league table to ${esc(r.label)}">` +
        `<span class="bd-label">${esc(r.label)} <span class="muted">${r.n}</span></span>` +
        `<span class="bd-barwrap"><span class="bd-bar" style="width:${w.toFixed(1)}%;background:${color}"></span></span>` +
        `<span class="bd-val num"><b>${valTxt}</b>${isPct ? "" : ` <span class="muted">${total ? ((r.aum / total) * 100).toFixed(1) : "0.0"}%</span>`}</span></div>`;
    }).join("");
  const toggle = `<span class="btnrow" style="display:inline-flex;margin-left:8px">` +
    `<button type="button" data-bd-mode="nominal" class="${!isPct ? "active" : ""}">$</button>` +
    `<button type="button" data-bd-mode="pct" class="${isPct ? "active" : ""}">%</button></span>`;
  const clear = breakdownFilter && breakdownFilter.kind === bd.kind
    ? ` <button type="button" data-bd-clear="${bd.kind}" class="active">clear ✕</button>` : "";
  return `<div class="panel-subhead"><span>${bd.title} <span class="muted">${esc(bd.note)}</span></span>${toggle}${clear}</div>` +
    `<div class="bd-chart">${bars || `<div class="muted">No data</div>`}</div>`;
}

export async function renderEtfFlows(doc) {
  const body = document.querySelector("#panel-etfflows .panel-body");
  if (!body) return;
  const d = doc || {};
  const funds = d.funds || {};
  const tickers = Object.keys(funds);
  if (!tickers.length) {
    body.innerHTML = `<div class="muted">No ETF data yet — the ishares_etf job runs daily.</div>`;
    return;
  }

  const classes = [...new Set(tickers.map((t) => funds[t].class || "other"))]
    .sort((a, b) => CLASS_ORDER.indexOf(a) - CLASS_ORDER.indexOf(b));
  const filterRow = `<div class="btnrow" id="etf-cls-row">` +
    [`<button type="button" data-etf-cls="all" class="${etfClass === "all" ? "active" : ""}">All</button>`,
     ...classes.map((c) =>
       `<button type="button" data-etf-cls="${c}" class="${etfClass === c ? "active" : ""}">${CLASS_LABELS_UI[c] ?? c}</button>`)].join("") +
    `</div>`;
  const clsDesc = CLASS_DESC[etfClass]
    ? `<div class="muted" style="margin:4px 0">${esc(CLASS_DESC[etfClass])}</div>` : "";

  const pending = d.pending || [];
  const pendingNote = pending.length
    ? `<div class="muted">Pending issuer-direct coverage (${pending.length}): ${pending.map(esc).join(", ")} — shown once their feeds land.</div>`
    : "";

  const shownBase = etfClass === "all" ? tickers
    : tickers.filter((t) => (funds[t].class || "other") === etfClass);
  const shown = breakdownFilter
    ? shownBase.filter((t) => breakdownFilter.tickers.includes(t))
    : shownBase;
  const bdFilterNote = breakdownFilter
    ? ` <span class="muted">· filtered: ${esc(breakdownFilter.label)} <button type="button" data-bd-clearall="1" style="margin-left:4px">clear ✕</button></span>` : "";

  body.innerHTML =
    `<div class="panel-subhead"><span>ETF FLOWS — AUM, PRICE, NAV & CREATIONS/REDEMPTIONS <span class="muted">as of ${esc(d.asof ?? "—")} · iShares T+1 · FMP · CoinLaw weekly (crypto) · Yahoo prices</span></span></div>` +
    filterRow + clsDesc + pendingNote +
    `<div class="kpirow" id="etf-kpis"><div class="muted">Computing flows…</div></div>` +
    `<div id="etf-chart"><div class="muted">Loading…</div></div>` +
    `<div class="panel-subhead"><span>CATEGORY SUMMARY <span class="muted">avg expense · avg yield · total AUM · 1W flows</span></span></div>` +
    `<div style="overflow-x:auto" id="etf-catsummary"></div>` +
    `<div class="panel-subhead"><span>BREAKDOWN <span class="muted">AUM by bucket · click a bar to filter the league table</span></span></div>` +
    `<div id="etf-breakdown"></div>` +
    `<div class="panel-subhead"><span>LEAGUE TABLE <span class="muted">${etfClass === "all" ? "all classes" : CLASS_LABELS_UI[etfClass] ?? etfClass} · flows = Δshares × NAV (derived) or reported weekly (CoinLaw) · heat = inflow blue / outflow red</span>${bdFilterNote}</span></div>` +
    `<div style="overflow-x:auto"><table class="etftable" data-sortable><thead><tr>` +
    `<th>ETF</th><th>Price</th><th>NAV</th><th>Disc/Prem</th><th>1D Flow</th><th>1W Flow</th><th>1M Flow</th>` +
    `<th>1M Ret</th><th>3M Ret</th><th>1Y Ret</th><th>AUM</th><th>Expense</th><th>Div Yield</th>` +
    `<th data-sort="off">AUM Range<br>${RANGE_LEGEND}</th><th>Src</th></tr></thead>` +
    `<tbody id="etf-league"><tr><td colspan="15" class="muted">Loading…</td></tr></tbody></table></div>` +
    `<div class="muted">${esc(d.note ?? "")}</div>`;

  body.querySelectorAll("[data-etf-cls]").forEach((b) =>
    b.addEventListener("click", () => { etfClass = b.dataset.etfCls; renderEtfFlows(doc); }));
  body.querySelectorAll("[data-bd-clearall]").forEach((b) =>
    b.addEventListener("click", () => { breakdownFilter = null; renderEtfFlows(doc); }));

  const per = await Promise.all(shown.map(async (t) => ({ t, flows: await fundFlows(t) })));

  const byD = {};
  for (const { flows } of per) {
    if (!flows) continue;
    for (const [dd, v] of flows) byD[dd] = (byD[dd] || 0) + v;
  }
  const agg = Object.entries(byD).sort(([a], [b]) => (a < b ? -1 : 1));
  const f1 = agg.length ? agg[agg.length - 1][1] : null;
  const f5 = agg.length >= 5 ? agg.slice(-5).reduce((a, [, v]) => a + v, 0) : null;

  const shownAum = shown.reduce((a, t) => a + (funds[t].aum || 0), 0);
  const clsTitle = etfClass === "all" ? "ALL TRACKED ETFs" : (CLASS_LABELS_UI[etfClass] ?? etfClass).toUpperCase();
  const kpis =
    kpiTile("AUM Tracked", money(shownAum), `${shown.length} ETFs`) +
    kpiTile("Net Flow 1D", f1 == null ? "—" : signedMoney(f1), agg.length ? agg[agg.length - 1][0] : "") +
    kpiTile("Net Flow 1W", f5 == null ? "—" : signedMoney(f5), "trailing 5 sessions");

  const kc = document.getElementById("etf-kpis");
  if (kc) kc.innerHTML = kpis;
  const ch = document.getElementById("etf-chart");
  if (ch) ch.innerHTML = flowsChart(agg, `AGGREGATE DAILY NET FLOWS — ${clsTitle}`);

  // category summary: avg expense, avg div yield, total AUM, 1W flows
  const allPer = etfClass === "all" ? per
    : await Promise.all(tickers.map(async (t) => ({ t, flows: await fundFlows(t) })));
  const flowsByCls = {};
  for (const { t, flows } of allPer) {
    if (!flows) continue;
    const c = funds[t].class || "other";
    for (const [dd, v] of flows) {
      flowsByCls[c] = flowsByCls[c] || {};
      flowsByCls[c][dd] = (flowsByCls[c][dd] || 0) + v;
    }
  }
  const avg = (xs) => xs.length
    ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const catRows = classes.map((c) => {
    const ts = tickers.filter((t) => (funds[t].class || "other") === c);
    const clsAum = ts.reduce((a, t) => a + (funds[t].aum || 0), 0);
    const exps = ts.map((t) => funds[t].expense).filter((v) => v != null);
    const ylds = ts.map((t) => funds[t].divyield).filter((v) => v != null);
    const dd = flowsByCls[c] || {};
    const arr = Object.entries(dd).sort(([a], [b]) => (a < b ? -1 : 1));
    const w = arr.length >= 5 ? arr.slice(-5).reduce((a, [, v]) => a + v, 0) : null;
    const ae = avg(exps), ay = avg(ylds);
    const label = CLASS_LABELS_UI[c] ?? c;
    return `<tr data-cls="${c}" style="cursor:pointer" title="Filter to ${esc(label)}">` +
      `<td><b>${esc(label)}</b>${etfClass === c ? ' <span class="muted">●</span>' : ""}<br><span class="muted">${ts.length} ETFs</span></td>` +
      `<td class="num"><b>${money(clsAum)}</b></td>` +
      `<td class="num"${heatFlow(w)}><b>${w == null ? "—" : signedMoney(w)}</b></td>` +
      `<td class="num">${ae == null ? "—" : ae.toFixed(2) + "%"}</td>` +
      `<td class="num">${ay == null ? "—" : ay.toFixed(2) + "%"}</td></tr>`;
  }).join("");
  const csEl = document.getElementById("etf-catsummary");
  if (csEl) {
    csEl.innerHTML =
      `<table class="etftable"><thead><tr><th>Category</th><th>AUM</th><th>1W Flow</th><th>Avg Expense</th><th>Avg Div Yield</th></tr></thead>` +
      `<tbody>${catRows}</tbody></table>` +
      `<div class="muted">Click a row to filter the league table.</div>`;
    csEl.querySelectorAll("[data-cls]").forEach((r) =>
      r.addEventListener("click", () => {
        etfClass = etfClass === r.dataset.cls ? "all" : r.dataset.cls;
        renderEtfFlows(doc);
      }));
  }

  // Breakdown charts (Harry 2026-10-06)
  const breakdowns = buildBreakdowns(funds);
  const bdEl = document.getElementById("etf-breakdown");
  const renderBreakdowns = () => {
    if (!bdEl) return;
    bdEl.innerHTML = breakdowns.map((bd) => breakdownBarChart(bd, funds)).join("");
    bdEl.querySelectorAll("[data-bd-mode]").forEach((b) =>
      b.addEventListener("click", () => {
        breakdownMode = b.dataset.bdMode; renderBreakdowns();
      }));
    bdEl.querySelectorAll("[data-bd-clear]").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (breakdownFilter && breakdownFilter.kind === b.dataset.bdClear) {
          breakdownFilter = null; renderEtfFlows(doc);
        }
      }));
    bdEl.querySelectorAll(".bd-row[data-bd-kind]").forEach((r) =>
      r.addEventListener("click", () => {
        const kind = r.dataset.bdKind, key = r.dataset.bdKey;
        const bd = breakdowns.find((x) => x.kind === kind);
        const b = bd && bd.buckets.find((x) => x.key === key);
        if (!b) return;
        if (breakdownFilter && breakdownFilter.kind === kind &&
            breakdownFilter.key === key) {
          breakdownFilter = null;
        } else {
          breakdownFilter = { kind, key, label: b.label, tickers: b.tickers };
        }
        renderEtfFlows(doc);
      }));
  };
  renderBreakdowns();

  // class rollup for the chart section (existing behavior)
  const clsAgg = {};
  for (const { t, flows } of allPer) {
    if (!flows) continue;
    const c = funds[t].class || "other";
    for (const [dd, v] of flows) {
      clsAgg[c] = clsAgg[c] || {};
      clsAgg[c][dd] = (clsAgg[c][dd] || 0) + v;
    }
  }
  const clsRows = Object.entries(clsAgg)
    .sort(([a], [b]) => CLASS_ORDER.indexOf(a) - CLASS_ORDER.indexOf(b))
    .map(([c, dd]) => {
      const arr = Object.entries(dd).sort(([a], [b]) => (a < b ? -1 : 1));
      const w = arr.length >= 5 ? arr.slice(-5).reduce((a, [, v]) => a + v, 0) : null;
      const clsAum = tickers.filter((t) => (funds[t].class || "other") === c)
        .reduce((a, t) => a + (funds[t].aum || 0), 0);
      const label = CLASS_LABELS_UI[c] ?? c;
      return `<tr data-cls="${c}" style="cursor:pointer" title="Filter to ${esc(label)}">` +
        `<td><b>${esc(label)}</b>${etfClass === c ? ' <span class="muted">●</span>' : ""}</td>` +
        `<td class="num"><b>${money(clsAum)}</b></td>` +
        `<td class="num"${heatFlow(w)}><b>${w == null ? "—" : signedMoney(w)}</b></td></tr>`;
    }).join("");

  const tb = document.getElementById("etf-league");
  if (tb) {
    const rows = await Promise.all(shown.map((t) => leagueRow(t, funds[t])));
    tb.innerHTML = rows.join("");
  }
  if (ch) {
    ch.innerHTML +=
      `<div class="panel-subhead"><span>BY ASSET CLASS <span class="muted">1W net flows · click a row to filter</span></span></div>` +
      `<div style="overflow-x:auto"><table class="etftable"><thead><tr><th>Class</th><th>AUM</th><th>1W Flow</th></tr></thead>` +
      `<tbody>${clsRows}</tbody></table></div>`;
    ch.querySelectorAll("[data-cls]").forEach((r) =>
      r.addEventListener("click", () => {
        etfClass = etfClass === r.dataset.cls ? "all" : r.dataset.cls;
        renderEtfFlows(doc);
      }));
  }

  const foot = document.querySelector("#panel-etfflows .panel-foot");
  if (foot) foot.textContent = `DATA: ISHARES T+1 / FMP / COINLAW / YAHOO · ${fmtAge(d.updated_at)}`;
}
