// ETF FLOWS tab: AUM / NAV / shares / price / expense / yield from the iShares
// product screener (T+1, keyless) + FMP fallback (free key) + CoinLaw crypto
// CSV. Flows are derived: shares = AUM / NAV; net_flow(day) =
// (shares_t − shares_{t-1}) × nav_t. Prices via Yahoo chart API (keyless, 1Y
// history → immediate 1M/3M/1Y returns). Disc/Prem = (price − NAV)/NAV.
// Data: /api/dashboard "etfflows" panel + /api/series etf:{TICKER}:{metric}.
import { getSeries } from "../api.js";
import { fmtAge } from "../fmt.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
import { rangeCells, statsFromValues, RANGE_LEGEND } from "../rangeviz.js";
import { symNameHtml } from "../names.js";
import { renderEtfHoldersInto } from "./etfholders.js";

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
  const activeTag = f.active === true
    ? ` <span class="muted" style="font-size:10px" title="Actively managed — issuer: ${esc(f.issuer ?? "n/a")}">ACTIVE</span>` : "";
  return `<tr><td class="sym">${symNameHtml(t)}${subTag}${bdcTag}${activeTag}</td>` +
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
let etfActive = "all"; // active/passive filter: "all" | "active" | "passive"

const CLASS_ORDER = ["equity", "fi-treasury", "fi-ig", "fi-hy", "fi-mbs",
  "fi-muni", "fi-tips", "fi-loans", "fi-conv", "fi-agg", "fi-intl",
  "fi-floater", "fi-short", "fi-abs", "fi-cmbs",
  "privcredit", "bdc", "commodity", "crypto", "realestate", "leveraged", "ai", "other"];
const CLASS_LABELS_UI = {
  equity: "Equities",
  "fi-treasury": "FI: Treasury", "fi-ig": "FI: IG", "fi-hy": "FI: High Yield",
  "fi-mbs": "FI: MBS", "fi-muni": "FI: Munis", "fi-tips": "FI: TIPS",
  "fi-loans": "FI: Loans/CLO", "fi-conv": "FI: Conv/Pfd",
  "fi-agg": "FI: Aggregate", "fi-intl": "FI: International",
  "fi-floater": "FI: Floaters", "fi-short": "FI: Short Duration",
  "fi-abs": "FI: ABS", "fi-cmbs": "FI: CMBS",
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
  "fi-floater", "fi-short", "fi-abs", "fi-cmbs"];

// Duration buckets for fixed-income tickers (Harry 2026-10-06).
const DUR_SHORT = new Set(["SHY", "SHV", "SGOV", "TBIL", "BIL", "VGSH",
  "SPSB", "VCSH", "IGSB", "SHYG", "SJNK", "STIP", "VTIP", "SUB", "SHM",
  "FLOT", "TFLO", "FLRN", "USFR", "JABS",
  "BKLN", "SRLN", "JAAA", "CLOX", "CLOI", "JBBB", "AAA",
  "JPST", "AVSF"]);
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

  // 6. Active vs passive (Harry 2026-10-07)
  const isActive = (t) => funds[t].active === true;
  const activeBuckets = [
    { key: "active", label: "Active",
      tickers: tickers.filter(isActive) },
    { key: "passive", label: "Passive",
      tickers: tickers.filter((t) => !isActive(t)) },
  ];

  return [
    { kind: "asset", title: "BY ASSET CLASS",
      note: "Money Market carved out of short Treasury bills",
      buckets: assetBuckets },
    { kind: "active", title: "BY MANAGEMENT — ACTIVE VS PASSIVE",
      note: "Active = manager discretion or systematic active (Dimensional, Avantis, JPMorgan, Capital Group, Fidelity, T. Rowe Price, American Century). Index and leveraged/inverse funds count as passive.",
      buckets: activeBuckets },
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

// ---------------------------------------------------------------------------
// In-panel sub-tabs: Flows | Holders (finra.js TRACE Detail subtab pattern).
// Holders hosts Worker B's etfholders panel; rendered lazily on first open.
// ---------------------------------------------------------------------------
let etfSub = "flows";        // "flows" | "holders"
let etfHoldersDoc = null;    // cached holders doc (from main.js)
let holdersRendered = false; // holders view mounted yet?

function ensureHoldersView() {
  const el = document.getElementById("etf-holders-view");
  if (!el || holdersRendered) return;
  holdersRendered = true;
  renderEtfHoldersInto(el, etfHoldersDoc || { symbols: {} });
}

// ---------------------------------------------------------------------------
// Bloomberg-style views (Harry 2026-10-07). All ETF-only: mutual-fund flows
// (ICI weekly / N-PORT monthly) land later and are not included here.
// Daily flows are derived per fund: net_flow(day) = (shares_t - shares_{t-1})
// x nav_t (see fundFlows); CoinLaw crypto rows carry only a reported weekly
// flow (flow7d), used as the latest-week proxy where marked.
// ---------------------------------------------------------------------------
const ETF_ONLY_TAG = `<span class="muted" title="Mutual-fund flows (ICI weekly / N-PORT monthly) are not included yet">ETF-only</span>`;

/** Monday (YYYY-MM-DD) of the week containing date d (YYYY-MM-DD). */
function weekKey(d) {
  const dt = new Date(d + "T12:00:00Z");
  if (isNaN(dt)) return d.slice(0, 10);
  dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7));
  return dt.toISOString().slice(0, 10);
}

/** Per-fund daily flows for all tickers. CoinLaw rows fall back to the
 *  reported weekly flow as a single latest-week proxy point. */
async function allFundFlows(tickers, funds) {
  const out = {};
  await Promise.all(tickers.map(async (t) => {
    let fl = await fundFlows(t);
    if (!fl && funds[t] && funds[t].source === "coinlaw") {
      const wpts = await seriesPts(`etf:${t}:flow7d`);
      if (wpts.length) {
        const [dd, v] = wpts[wpts.length - 1];
        fl = [[dd, v]];
      }
    }
    out[t] = (fl || []).slice().sort((a, b) => (a[0] < b[0] ? -1 : 1));
  }));
  return out;
}

/** Last n calendar weeks (Monday dates) seen across all fund flows. */
function globalWeeks(flowsMap, n = 12) {
  const seen = {};
  for (const t in flowsMap) for (const [d] of flowsMap[t]) seen[weekKey(d)] = 1;
  return Object.keys(seen).sort().slice(-n);
}

/** Fund daily flows -> weekly $ totals aligned to the given week list. */
function weeklyVector(daily, weeks) {
  const byW = {};
  for (const [d, v] of daily) byW[weekKey(d)] = (byW[weekKey(d)] || 0) + v;
  return weeks.map((w) => byW[w] || 0);
}

/** Tiny blue/red bar sparkline of weekly $ flows (matches flowsChart hues). */
function miniBars(vals, w = 104, h = 30) {
  if (!vals || vals.length < 2) return `<span class="muted">—</span>`;
  const mx = Math.max(...vals.map(Math.abs), 1);
  const bw = w / vals.length;
  const mid = h / 2;
  let s = `<svg width="${w}" height="${h}" style="display:block" role="img">`;
  vals.forEach((v, i) => {
    const bh = Math.abs(v) / mx * (h / 2 - 1);
    const y = v >= 0 ? mid - bh : mid;
    s += `<rect x="${(i * bw).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${Math.max(0.5, bh).toFixed(1)}" fill="${v >= 0 ? "#4f9cf0" : "#e05c5c"}"><title>${signedMoney(v)}</title></rect>`;
  });
  s += `<line x1="0" y1="${mid}" x2="${w}" y2="${mid}" stroke="#9aa4b2" stroke-width="0.5"/></svg>`;
  return s;
}

const wM = (v) => v == null ? "—"
  : `${v >= 0 ? "+" : "−"}$${(Math.abs(v) / 1e6).toFixed(1)}M`;
const wCell = (v) => v === 0 ? `<td class="num muted">—</td>`
  : `<td class="num" title="${signedMoney(v)}"><b>${wM(v)}</b></td>`;

// ---- View 1: Issuer tree ---------------------------------------------------
function issuerTreeHtml(funds, flowsMap) {
  const tickers = Object.keys(funds);
  const weeks = globalWeeks(flowsMap, 12);
  if (!weeks.length) {
    return `<div class="muted">No flow history yet — weekly flows accumulate from the first daily run. ${ETF_ONLY_TAG}</div>`;
  }
  const wkLabel = weeks.map((w) => w.slice(5).replace("-", "/"));
  const vec = {};
  for (const t of tickers) vec[t] = weeklyVector(flowsMap[t] || [], weeks);
  const byIss = {};
  for (const t of tickers) {
    const iss = funds[t].issuer || "Other";
    (byIss[iss] = byIss[iss] || []).push(t);
  }
  const issRows = Object.entries(byIss).map(([iss, ts]) => {
    const wsum = weeks.map((_, i) => ts.reduce((a, t) => a + vec[t][i], 0));
    const tot = wsum.reduce((a, b) => a + b, 0);
    const aum = ts.reduce((a, t) => a + (funds[t].aum || 0), 0);
    return { iss, ts: ts.slice().sort(), wsum, tot, aum };
  }).sort((a, b) => b.tot - a.tot);
  const head = `<tr><th>Issuer / Fund</th>` +
    wkLabel.map((l) => `<th class="num">w/c<br>${l}</th>`).join("") +
    `<th>12W spark</th></tr>`;
  const rows = issRows.map(({ iss, ts, wsum, tot, aum }) => {
    const fundRows = ts.map((t) => {
      const f = funds[t];
      const cells = vec[t].map((v) => wCell(v)).join("");
      return `<tr><td style="padding-left:18px">${symNameHtml(t)}` +
        (f.source === "coinlaw" ? ` <span class="muted" style="font-size:10px" title="Weekly flow is the CoinLaw reported weekly figure">WK</span>` : "") +
        `<br><span class="muted">${money(f.aum)}</span></td>${cells}<td>${miniBars(vec[t])}</td></tr>`;
    }).join("");
    return `<tr style="background:#eef4fc"><td><b>${esc(iss)}</b><br><span class="muted">${ts.length} funds · ${money(aum)} AUM</span></td>` +
      wsum.map((v) => wCell(v)).join("") +
      `<td>${miniBars(wsum)}</td></tr>` + fundRows;
  }).join("");
  const firstW = weeks[0], lastW = weeks[weeks.length - 1];
  return `<div class="panel-subhead"><span>ISSUER TREE — WEEKLY NET FLOWS ($M) ${ETF_ONLY_TAG} <span class="muted">weeks commencing ${firstW} to ${lastW} · derived from daily share-implied flows (CoinLaw rows: reported weekly)</span></span></div>` +
    `<div style="overflow-x:auto"><table class="etftable"><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
}

// ---- View 2: Five baskets --------------------------------------------------
const BASKETS = [
  { name: "Traders", desc: "high-turnover trading vehicles",
    tickers: ["SPY", "QQQ", "MDY", "IWM", "EEM", "EFA", "XLK", "XLC", "XLF", "HYG", "EMB", "DIA"] },
  { name: "Buy-and-Hold", desc: "core index building blocks",
    tickers: ["VOO", "VO", "VB", "VEA", "VGT", "VOX", "VTI", "VEU", "SCHX", "SCHM", "SCHA", "SCHE", "SCHF", "IJH", "IJR", "IEFA", "USHY"] },
  { name: "Safe Haven", desc: "flight-to-safety",
    tickers: ["GLD", "TLT", "USMV"] },
  { name: "Risk On", desc: "pro-cyclical / high-beta",
    tickers: ["IWF", "MTUM", "IWO", "SPHB", "XLK", "XLY", "HYG", "SPYG", "FDN", "HYLB", "XLC", "EMB"] },
  { name: "Buffer / Defined-Outcome", desc: "options-based downside buffers (Innovator, AllianzIM, …)",
    tickers: "discover" },
];

/** Find buffer / defined-outcome ETFs in our universe by issuer/name. */
function discoverBufferTickers(funds) {
  return Object.keys(funds).filter((t) => {
    const f = funds[t];
    const hay = `${f.issuer ?? ""} ${f.name ?? ""}`.toLowerCase();
    return /buffer|defined.outcome|outcome|innovator|allianz|protected|hedged equity/.test(hay) &&
      !/rate hedged|interest rate/.test(hay);
  });
}

async function basketTableHtml(b, funds, flowsMap) {
  let tickers = b.tickers;
  let discNote = "";
  if (tickers === "discover") {
    tickers = discoverBufferTickers(funds);
    discNote = ` <span class="muted">universe scan: issuer/name match for Innovator / AllianzIM / buffer / defined-outcome — ` +
      (tickers.length ? `${tickers.length} found: ${tickers.join(", ")}` : "none found in the 243-fund universe") + `</span>`;
  }
  const uni = new Set(Object.keys(funds));
  const sumLast = (arr, n) => arr.length >= n
    ? arr.slice(-n).reduce((a, [, v]) => a + v, 0) : null;
  const rows = await Promise.all(tickers.map(async (t) => {
    if (!uni.has(t)) {
      return `<tr><td><b>${t}</b></td><td class="num muted" colspan="4">no data — not in the 243-fund universe</td></tr>`;
    }
    const f = funds[t];
    const fl = flowsMap[t] || [];
    let f1 = sumLast(fl, 1), f5 = sumLast(fl, 5), f21 = sumLast(fl, 21);
    let wNote = "";
    if (f.source === "coinlaw") {
      // Reported weekly only: do not mislabel it as 1D/1M.
      f5 = fl.length ? fl[fl.length - 1][1] : null;
      f1 = null; f21 = null;
      wNote = "reported wk";
    }
    const r1m = await priceReturn(t, 21);
    const cell = (v, note = "") => v == null ? `<td class="num muted">—</td>`
      : `<td class="num"${heatFlow(v)} title="${esc(note)}"><b>${signedMoney(v)}</b>${note ? `<span class="muted"> ${esc(note)}</span>` : ""}</td>`;
    return `<tr><td class="sym">${symNameHtml(t)}</td>${cell(f1)}${cell(f5, wNote)}${cell(f21)}` +
      `<td class="num"><b>${money(f.aum)}</b></td>` +
      (r1m == null ? `<td class="num muted">—</td>`
        : `<td class="num"${heatStyle({ z: r1m === 0 ? 0 : (r1m > 0 ? 1 : -1) })}><b>${pct(r1m)}</b></td>`) +
      `</tr>`;
  }));
  const covered = tickers.filter((t) => uni.has(t)).length;
  return `<div class="panel-subhead"><span>${esc(b.name).toUpperCase()} <span class="muted">${esc(b.desc)} · ${covered}/${tickers.length} tickers covered ${ETF_ONLY_TAG}</span>${discNote}</span></div>` +
    (tickers.length
      ? `<div style="overflow-x:auto"><table class="etftable"><thead><tr><th>ETF</th><th>1D Flow</th><th>1W Flow</th><th>1M Flow</th><th>AUM</th><th>1M Ret</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`
      : `<div class="muted">No buffer / defined-outcome ETFs in our universe yet — this basket stays empty until such funds are added.</div>`);
}

async function basketsHtml(funds, flowsMap) {
  const parts = [];
  for (const b of BASKETS) parts.push(await basketTableHtml(b, funds, flowsMap));
  return `<div class="panel-subhead"><span>FLOW BASKETS — BLOOMBERG-STYLE ${ETF_ONLY_TAG} <span class="muted">missing tickers are flagged, not silently dropped</span></span></div>` + parts.join("");
}

// ---- View 3: Active / Passive / Total flows --------------------------------
function aggDaily(tickers, flowsMap) {
  const byD = {};
  for (const t of tickers) for (const [d, v] of (flowsMap[t] || [])) byD[d] = (byD[d] || 0) + v;
  return Object.entries(byD).sort(([a], [b]) => (a < b ? -1 : 1));
}

function activePassiveHtml(funds, flowsMap) {
  const tickers = Object.keys(funds);
  const act = tickers.filter((t) => funds[t].active === true);
  const pas = tickers.filter((t) => funds[t].active !== true);
  const A = aggDaily(act, flowsMap), P = aggDaily(pas, flowsMap), T = aggDaily(tickers, flowsMap);
  const win = (arr, n) => arr.length >= n ? arr.slice(-n).reduce((a, [, v]) => a + v, 0) : null;
  const prior = (arr, n) => arr.length >= 2 * n ? arr.slice(-2 * n, -n).reduce((a, [, v]) => a + v, 0) : null;
  const lastD = T.length ? T[T.length - 1][0] : "—";
  const prevD = T.length >= 10 ? T[T.length - 10][0] : "—";
  const g = (tickers2, arr) => {
    const w = win(arr, 5), m = win(arr, 21), pw = prior(arr, 5);
    return { n: tickers2.length, w, m, pw };
  };
  const ga = g(act, A), gp = g(pas, P), gt = g(tickers, T);
  const share = (x) => (x.w == null || gt.w == null || Math.abs(gt.w) < 1) ? null : (x.w / gt.w) * 100;
  const pshare = (x) => (x.pw == null || gt.pw == null || Math.abs(gt.pw) < 1) ? null : (x.pw / gt.pw) * 100;
  const sa = share(ga), sp = share(gp), spa = pshare(ga), spp = pshare(gp);
  const cell$ = (v) => v == null ? `<td class="num muted">—</td>`
    : `<td class="num"${heatFlow(v)}><b>${signedMoney(v)}</b></td>`;
  const row = (label, x, s, ps) => {
    const dNom = (x.w == null || x.pw == null) ? null : x.w - x.pw;
    const dPp = (s == null || ps == null) ? null : s - ps;
    return `<tr><td><b>${label}</b><br><span class="muted">${x.n} funds</span></td>` +
      cell$(x.w) + cell$(x.m) +
      (s == null ? `<td class="num muted">—</td>` : `<td class="num"><b>${s.toFixed(1)}%</b></td>`) +
      (dNom == null ? `<td class="num muted">—</td>`
        : `<td class="num"${heatFlow(dNom)}><b>${signedMoney(dNom)}</b>` +
          (dPp == null ? "" : ` <span class="muted">(${dPp >= 0 ? "+" : "−"}${Math.abs(dPp).toFixed(1)}pp)</span>`) + `</td>`) +
      `</tr>`;
  };
  return `<div class="panel-subhead"><span>ACTIVE VS PASSIVE FLOWS ${ETF_ONLY_TAG} <span class="muted">1W = trailing 5 sessions ending ${lastD} · prior week = 5 sessions ending ${prevD}</span></span></div>` +
    `<div style="overflow-x:auto"><table class="etftable"><thead><tr><th></th><th>1W Net Flow</th><th>1M Net Flow</th><th>1W Share of Total</th><th>Δ vs Prior Week (nominal + pp)</th></tr></thead><tbody>` +
    row("Active", ga, sa, spa) + row("Passive", gp, sp, spp) + row("Total", gt, 100, 100) +
    `</tbody></table></div>` +
    `<div class="muted">Shares are shares of net flow — they can exceed 100% or go negative when active and passive flow in opposite directions. ${HEAT_LEGEND}</div>`;
}

// ---- View 4: Flow-%-of-AUM heatmap (asset class x month) --------------------
async function aumAt(t, monthEnd) {
  const pts = await seriesPts(`etf:${t}:aum`);
  let v = null;
  for (const [d, x] of pts) { if (d <= monthEnd) v = x; else break; }
  return v;
}

async function flowHeatmapHtml(funds, flowsMap) {
  const months = [];
  const now = new Date();
  for (let i = 5; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    months.push(d.toISOString().slice(0, 7));
  }
  const classes = [...new Set(Object.keys(funds).map((t) => funds[t].class || "other"))]
    .sort((a, b) => CLASS_ORDER.indexOf(a) - CLASS_ORDER.indexOf(b));
  const rows = [];
  for (const c of classes) {
    const ts = Object.keys(funds).filter((t) => (funds[t].class || "other") === c);
    const cells = [];
    for (const m of months) {
      let flow = 0, aum = 0;
      for (const t of ts) {
        for (const [d, v] of (flowsMap[t] || [])) if (d.slice(0, 7) === m) flow += v;
      }
      const aums = await Promise.all(ts.map((t) => aumAt(t, m + "-31")));
      aum = aums.reduce((a, v) => a + (v || 0), 0);
      const p = aum > 0 ? (flow / aum) * 100 : null;
      cells.push(p == null ? `<td class="num muted">—</td>`
        : `<td class="num"${heatStyle({ pct: p / 100 })} title="${signedMoney(flow)} on ${money(aum)} AUM"><b>${pct(p)}</b></td>`);
    }
    rows.push(`<tr><td><b>${esc(CLASS_LABELS_UI[c] ?? c)}</b><br><span class="muted">${ts.length} ETFs</span></td>${cells.join("")}</tr>`);
  }
  return `<div class="panel-subhead"><span>MONTHLY FLOW AS % OF AUM — BY ASSET CLASS ${ETF_ONLY_TAG} <span class="muted">monthly net flow ÷ month-end AUM · ${months[0]} to ${months[months.length - 1]}</span></span></div>` +
    `<div class="muted">${HEAT_LEGEND} — red = net inflow (hot), blue = net outflow (cold); deeper = larger % of AUM.</div>` +
    `<div style="overflow-x:auto"><table class="etftable"><thead><tr><th>Asset Class</th>${months.map((m) => `<th class="num">${m}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

// ---- View 5: Cumulative flows as % of starting AUM --------------------------
async function startAum(tickers) {
  let s = 0;
  for (const t of tickers) {
    const pts = await seriesPts(`etf:${t}:aum`);
    if (pts.length) s += pts[0][1];
  }
  return s;
}

function cumLineChart(series, firstD, lastD) {
  // series: [{label, color, dash, pts: [[d, pct]...]}]
  const W = 720, H = 280, padL = 56, padR = 12, padT = 14, padB = 30;
  const all = series.flatMap((s) => s.pts.map(([, v]) => v));
  if (!all.length) return `<div class="muted">No flow history yet.</div>`;
  let lo = Math.min(...all, 0), hi = Math.max(...all, 0);
  if (hi - lo < 1e-9) { hi += 0.5; lo -= 0.5; }
  const n = Math.max(...series.map((s) => s.pts.length));
  const X = (i) => padL + (n <= 1 ? 0 : (i / (n - 1)) * (W - padL - padR));
  const Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:760px;display:block;background:#211d16" role="img">`;
  s += `<line x1="${padL}" y1="${Y(0)}" x2="${W - padR}" y2="${Y(0)}" stroke="#9aa4b2" stroke-width="1"/>`;
  for (const { label, color, dash, pts } of series) {
    const d = pts.map(([, v], i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
    s += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2"${dash ? ` stroke-dasharray="${dash}"` : ""}><title>${esc(label)}</title></path>`;
  }
  s += `<text x="${padL - 6}" y="${Y(hi) + 4}" font-size="10" fill="#a89a83" text-anchor="end">${hi.toFixed(1)}%</text>`;
  s += `<text x="${padL - 6}" y="${Y(lo) + 4}" font-size="10" fill="#a89a83" text-anchor="end">${lo.toFixed(1)}%</text>`;
  s += `<text x="${padL}" y="${H - 10}" font-size="10" fill="#a89a83">${firstD}</text>`;
  s += `<text x="${W - padR}" y="${H - 10}" font-size="10" fill="#a89a83" text-anchor="end">${lastD}</text>`;
  s += `</svg>`;
  s += `<div class="muted" style="margin-top:4px">` + series.map(({ label, color, dash }) =>
    `<span style="display:inline-block;width:18px;border-top:3px ${dash ? "dashed" : "solid"} ${color};margin-right:4px;vertical-align:middle"></span>${esc(label)}&nbsp;&nbsp;`).join("") + `</div>`;
  return s;
}

async function cumFlowHtml(funds, flowsMap) {
  const tickers = Object.keys(funds);
  const act = tickers.filter((t) => funds[t].active === true);
  const pas = tickers.filter((t) => funds[t].active !== true);
  const mk = (ts) => {
    const byD = {};
    for (const t of ts) for (const [d, v] of (flowsMap[t] || [])) byD[d] = (byD[d] || 0) + v;
    return Object.entries(byD).sort(([a], [b]) => (a < b ? -1 : 1));
  };
  const [sA, sP, sT] = await Promise.all([startAum(act), startAum(pas), startAum(tickers)]);
  const A = mk(act), P = mk(pas), T = mk(tickers);
  if (!T.length) return `<div class="muted">No flow history yet — cumulative flows accumulate from the first daily run. ${ETF_ONLY_TAG}</div>`;
  // Common date axis (trading days only): union of group dates, zero-filled.
  const dates = [...new Set([...A, ...P, ...T].map(([d]) => d))].sort();
  const byD = (arr) => Object.fromEntries(arr);
  const AByD = byD(A), PByD = byD(P), TByD = byD(T);
  const pctSeries = (map, base) => {
    let c = 0;
    return dates.map((d) => { c += map[d] || 0; return [d, base > 0 ? (c / base) * 100 : 0]; });
  };
  const sApts = pctSeries(AByD, sA), sPpts = pctSeries(PByD, sP), sTpts = pctSeries(TByD, sT);
  const firstD = dates[0], lastD = dates[dates.length - 1];
  const chart = cumLineChart([
    { label: "Active", color: "#e8c96a", pts: sApts },
    { label: "Passive", color: "#7fc9b5", pts: sPpts },
    { label: "Total", color: "#1e3a8a", dash: "6,3", pts: sTpts },
  ], firstD, lastD);
  const cur = (pts) => pts.length ? pts[pts.length - 1][1] : null;
  const stat = (label, v) => v == null ? "" : ` <b>${label}</b> ${pct(v, 1)} ·`;
  return `<div class="panel-subhead"><span>CUMULATIVE NET FLOWS AS % OF STARTING AUM ${ETF_ONLY_TAG} <span class="muted">since ${firstD} · starting AUM = first recorded snapshot (trading days only)</span></span></div>` +
    `<div class="muted" style="margin-bottom:4px">Latest cumulative:` +
    stat("Active", cur(sApts)) + stat("Passive", cur(sPpts)) + stat("Total", cur(sTpts)) + `</div>` + chart;
}

// ---- View 6: Top issuers by flows ------------------------------------------
function topIssuersHtml(funds, flowsMap) {
  const tickers = Object.keys(funds);
  const byIss = {};
  for (const t of tickers) {
    const iss = funds[t].issuer || "Other";
    (byIss[iss] = byIss[iss] || []).push(t);
  }
  const rows = Object.entries(byIss).map(([iss, ts]) => {
    let m = 0, aum = 0;
    for (const t of ts) {
      const fl = flowsMap[t] || [];
      if (fl.length >= 21) m += fl.slice(-21).reduce((a, [, v]) => a + v, 0);
      aum += funds[t].aum || 0;
    }
    return { iss, m, aum, n: ts.length };
  }).sort((a, b) => b.m - a.m).slice(0, 10);
  if (!rows.length) return `<div class="muted">No flow history yet. ${ETF_ONLY_TAG}</div>`;
  const W = 720, rowH = 34, padL = 170, padR = 110, H = rows.length * rowH + 30;
  const mx = Math.max(...rows.map((r) => Math.abs(r.m)), 1);
  const zeroX = padL + (W - padL - padR) / 2;
  const X = (v) => zeroX + (v / mx) * ((W - padL - padR) / 2);
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:760px;display:block;background:#211d16" role="img">`;
  s += `<line x1="${zeroX}" y1="8" x2="${zeroX}" y2="${H - 24}" stroke="#9aa4b2" stroke-width="1"/>`;
  rows.forEach((r, i) => {
    const y = 10 + i * rowH;
    const x0 = Math.min(zeroX, X(r.m)), x1 = Math.max(zeroX, X(r.m));
    s += `<text x="${padL - 8}" y="${y + 12}" font-size="11" fill="#ece4d3" text-anchor="end">${esc(r.iss)} <tspan fill="#a89a83">(${r.n})</tspan></text>`;
    s += `<rect x="${x0.toFixed(1)}" y="${y}" width="${Math.max(1, x1 - x0).toFixed(1)}" height="18" fill="${r.m >= 0 ? "#4f9cf0" : "#e05c5c"}"><title>${r.iss}: ${signedMoney(r.m)} 1M · ${money(r.aum)} AUM</title></rect>`;
    s += `<text x="${(r.m >= 0 ? x1 + 6 : x0 - 6).toFixed(1)}" y="${y + 13}" font-size="11" fill="#ece4d3" text-anchor="${r.m >= 0 ? "start" : "end"}"><b>${signedMoney(r.m)}</b></text>`;
  });
  s += `</svg>`;
  return `<div class="panel-subhead"><span>TOP ISSUERS BY 1M NET FLOWS ${ETF_ONLY_TAG} <span class="muted">trailing 21 sessions · null issuers grouped as "Other" · CoinLaw weekly-reported rows have no 1M history yet (excluded)</span></span></div>` + s;
}

// ---- View 7: Smart-beta factor tree -----------------------------------------
const FACTOR_ORDER = ["Growth", "Value", "Dividend", "Size", "Low Volatility", "Momentum", "Quality", "Rate Hedged"];
const FACTOR_MAP = {
  // Growth
  IVW: "Growth", IWF: "Growth", FBCG: "Growth", TGRW: "Growth",
  // Value
  IVE: "Value", IWD: "Value", DFUV: "Value", DFIV: "Value", DFAS: "Value",
  AVUV: "Value", AVDV: "Value", AVLV: "Value", FBCV: "Value", CGDV: "Value",
  // Dividend
  JEPI: "Dividend", JEPQ: "Dividend", CGDV: "Dividend", TDVG: "Dividend",
  // Size
  IWM: "Size", IJR: "Size", IJH: "Size", AVUV: "Size", AVDV: "Size",
  AVSC: "Size", DFAS: "Size", DFSV: "Size",
  // Low Volatility
  USMV: "Low Volatility", LVOL: "Low Volatility",
  // Momentum
  MTUM: "Momentum",
  // Quality
  QUAL: "Quality", DUHP: "Quality",
  // Rate Hedged
  LQDH: "Rate Hedged",
};

function factorTreeHtml(funds, flowsMap) {
  const uni = new Set(Object.keys(funds));
  const mapped = new Set();
  const weeks = globalWeeks(flowsMap, 12);
  const vec = {};
  for (const t of uni) vec[t] = weeklyVector(flowsMap[t] || [], weeks);
  const parts = FACTOR_ORDER.map((fac) => {
    const ts = Object.keys(FACTOR_MAP).filter((t) => FACTOR_MAP[t] === fac && uni.has(t)).sort();
    ts.forEach((t) => mapped.add(t));
    if (!ts.length) return "";
    const wsum = weeks.map((_, i) => ts.reduce((a, t) => a + vec[t][i], 0));
    const tot12 = wsum.reduce((a, b) => a + b, 0);
    const w1 = ts.reduce((a, t) => {
      const fl = flowsMap[t] || [];
      return a + (fl.length >= 5 ? fl.slice(-5).reduce((x, [, v]) => x + v, 0) : 0);
    }, 0);
    const fundRows = ts.map((t) => {
      const fl = flowsMap[t] || [];
      const f5 = fl.length >= 5 ? fl.slice(-5).reduce((a, [, v]) => a + v, 0) : null;
      return `<tr><td style="padding-left:18px">${symNameHtml(t)}<br><span class="muted">${money(funds[t].aum)}</span></td>` +
        (f5 == null ? `<td class="num muted">—</td>` : `<td class="num"${heatFlow(f5)}><b>${signedMoney(f5)}</b></td>`) +
        `<td>${miniBars(vec[t])}</td></tr>`;
    }).join("");
    return `<tr style="background:#eef4fc"><td><b>${fac}</b><br><span class="muted">${ts.length} funds · 12W ${signedMoney(tot12)}</span></td>` +
      `<td class="num"${heatFlow(w1)}><b>${signedMoney(w1)}</b></td><td>${miniBars(wsum)}</td></tr>` + fundRows;
  }).join("");
  const unmapped = Object.keys(funds).filter((t) => !mapped.has(t)).length;
  return `<div class="panel-subhead"><span>SMART-BETA FACTOR TREE ${ETF_ONLY_TAG} <span class="muted">fund → factor mapping curated from fund names/mandates · 1W = trailing 5 sessions</span></span></div>` +
    `<div style="overflow-x:auto"><table class="etftable"><thead><tr><th>Factor / Fund</th><th>1W Flow</th><th>12W spark</th></tr></thead><tbody>${parts}</tbody></table></div>` +
    `<div class="muted">${unmapped} of ${Object.keys(funds).length} universe funds are not factor ETFs (broad-market index, credit, leveraged, crypto, commodity, etc.) and are excluded from this tree.</div>`;
}

/** Renders the 7 Bloomberg-style views at the end of the Flows view. */
async function renderBloombergViews(el, funds) {
  const tickers = Object.keys(funds);
  const host = document.createElement("div");
  host.id = "etf-bloomberg";
  el.appendChild(host);
  host.innerHTML = `<div class="panel-subhead"><span>FLOW ANALYTICS — BLOOMBERG-STYLE ${ETF_ONLY_TAG}</span></div><div class="muted">Computing…</div>`;
  try {
    const flowsMap = await allFundFlows(tickers, funds);
    const parts = [
      issuerTreeHtml(funds, flowsMap),
      await basketsHtml(funds, flowsMap),
      activePassiveHtml(funds, flowsMap),
      await flowHeatmapHtml(funds, flowsMap),
      await cumFlowHtml(funds, flowsMap),
      topIssuersHtml(funds, flowsMap),
      factorTreeHtml(funds, flowsMap),
    ];
    host.innerHTML = `<div class="panel-subhead"><span>FLOW ANALYTICS — BLOOMBERG-STYLE ${ETF_ONLY_TAG} <span class="muted">all views ETF-only; mutual-fund flows land later</span></span></div>` +
      parts.join(`<div style="height:18px"></div>`);
  } catch (e) {
    host.innerHTML = `<div class="muted">Flow analytics unavailable: ${esc(e.message ?? e)}</div>`;
  }
}

export async function renderEtfFlows(doc, holdersDoc) {
  const body = document.querySelector("#panel-etfflows .panel-body");
  if (!body) return;
  if (holdersDoc !== undefined) etfHoldersDoc = holdersDoc;
  holdersRendered = false; // shell was rebuilt (holders div wiped); re-mount lazily
  body.innerHTML =
    `<nav class="sub-row etf-subtabs" role="tablist" aria-label="ETF views">` +
    `<button type="button" id="etf-tab-flows" class="${etfSub === "flows" ? "active" : ""}" role="tab" aria-selected="${etfSub === "flows"}">Flows</button>` +
    `<button type="button" id="etf-tab-holders" class="${etfSub === "holders" ? "active" : ""}" role="tab" aria-selected="${etfSub === "holders"}">Holders</button>` +
    `</nav>` +
    `<div id="etf-flows-view" role="tabpanel"${etfSub === "flows" ? "" : " hidden"}></div>` +
    `<div id="etf-holders-view" role="tabpanel"${etfSub === "holders" ? "" : " hidden"}></div>`;
  const flowsBtn = document.getElementById("etf-tab-flows");
  const holdBtn = document.getElementById("etf-tab-holders");
  const flowsView = document.getElementById("etf-flows-view");
  const holdView = document.getElementById("etf-holders-view");
  const setTab = (which) => {
    etfSub = which;
    const showFlows = which === "flows";
    flowsView.hidden = !showFlows;
    holdView.hidden = showFlows;
    flowsBtn.classList.toggle("active", showFlows);
    holdBtn.classList.toggle("active", !showFlows);
    flowsBtn.setAttribute("aria-selected", String(showFlows));
    holdBtn.setAttribute("aria-selected", String(!showFlows));
    if (!showFlows) ensureHoldersView();
  };
  flowsBtn.addEventListener("click", () => setTab("flows"));
  holdBtn.addEventListener("click", () => setTab("holders"));
  await renderFlowsView(flowsView, doc);
  if (etfSub === "holders") ensureHoldersView();
}

/** The Flows sub-tab: the existing league table / breakdowns / KPIs, plus
 *  the 7 Bloomberg-style views appended below. */
async function renderFlowsView(viewEl, doc) {
  const body = viewEl;
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
  const activeRow = `<div class="btnrow" id="etf-act-row">` +
    [["all", "All"], ["active", "Active"], ["passive", "Passive"]].map(([v, l]) =>
      `<button type="button" data-etf-act="${v}" class="${etfActive === v ? "active" : ""}">${l}</button>`).join("") +
    `</div>`;
  const clsDesc = CLASS_DESC[etfClass]
    ? `<div class="muted" style="margin:4px 0">${esc(CLASS_DESC[etfClass])}</div>` : "";

  const pending = d.pending || [];
  const pendingNote = pending.length
    ? `<div class="muted">Pending issuer-direct coverage (${pending.length}): ${pending.map(esc).join(", ")} — shown once their feeds land.</div>`
    : "";

  const shownBase = (etfClass === "all" ? tickers
    : tickers.filter((t) => (funds[t].class || "other") === etfClass))
    .filter((t) => etfActive === "all" ? true
      : etfActive === "active" ? funds[t].active === true : funds[t].active !== true);
  const shown = breakdownFilter
    ? shownBase.filter((t) => breakdownFilter.tickers.includes(t))
    : shownBase;
  const bdFilterNote = breakdownFilter
    ? ` <span class="muted">· filtered: ${esc(breakdownFilter.label)} <button type="button" data-bd-clearall="1" style="margin-left:4px">clear ✕</button></span>` : "";

  body.innerHTML =
    `<div class="panel-subhead"><span>ETF FLOWS — AUM, PRICE, NAV & CREATIONS/REDEMPTIONS <span class="muted">as of ${esc(d.asof ?? "—")} · iShares T+1 · FMP · CoinLaw weekly (crypto) · Yahoo prices</span></span></div>` +
    filterRow + activeRow + clsDesc + pendingNote +
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

  body.querySelectorAll("[data-etf-act]").forEach((b) =>
    b.addEventListener("click", () => { etfActive = b.dataset.etfAct; renderEtfFlows(doc); }));
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
  const clsTitle = (etfClass === "all" ? "ALL TRACKED ETFs" : (CLASS_LABELS_UI[etfClass] ?? etfClass).toUpperCase()) +
    (etfActive === "all" ? "" : etfActive === "active" ? " · ACTIVE" : " · PASSIVE");
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

  await renderBloombergViews(body, funds);

  const foot = document.querySelector("#panel-etfflows .panel-foot");
  if (foot) foot.textContent = `DATA: ISHARES T+1 / FMP / COINLAW / YAHOO · ${fmtAge(d.updated_at)}`;
}
