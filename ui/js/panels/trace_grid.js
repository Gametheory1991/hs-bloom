// Bloomberg-style TRACE volume grid: delta heatmap + 3Y range dot plots + sparklines.
// Mounted by renderFinra() into #trace-grid-wrap. Data via /api/series (no cycle: prefix).
// ADV for TRACE monthly products = monthly par ($M) / NYSE trading days / 1000.
// Trading-day calendar verified 116/117 vs canonical CSV (only miss: 2018-12-05
// Bush national day of mourning, special-cased below).
// Rows: TOTAL (Treasury + all 10 TRACE products) first per Harry's standing rule,
// then Treasury total + breakdowns (bills/coupons/TIPS/FRNs, on-the-run/off-the-run),
// the 10 TRACE products, then FINRA short-interest levels with MoM/YoY deltas.
import { getSeries } from "../api.js";
import { setTraceChartProduct } from "./trace_charts.js";
import { OUTSTANDING, totalOutstanding, OUTSTANDING_NOTE } from "./outstanding.js";
import { rangePlotDotted } from "../rangeviz.js";

const PRODUCTS = [
  { id: "total", label: "TOTAL (Treasury + TRACE)", synthetic: true },
  { id: "ust",  label: "Treasury Total", par: "trace-ust-par",  trades: "trace-ust-trades",  monthly: false },
  { id: "ust-bills",   label: "Treasury — Bills",      par: "trace-ust-bills-par",   monthly: false },
  { id: "ust-coupons", label: "Treasury — Nom Coupons", par: "trace-ust-coupons-par", monthly: false },
  { id: "ust-tips",    label: "Treasury — TIPS",       par: "trace-ust-tips-par",    monthly: false },
  { id: "ust-frns",    label: "Treasury — FRNs",       par: "trace-ust-frns-par",    monthly: false },
  { id: "ust-onrun",   label: "Treasury — On-the-run", par: "trace-ust-onrun-par",   monthly: false },
  { id: "ust-offrun",  label: "Treasury — Off-the-run", par: "trace-ust-offrun-par", monthly: false },
  { id: "tba",  label: "TBA",            par: "trace-tba-par",  trades: null,                 monthly: true  },
  { id: "corp", label: "Corporate",      par: "trace-corp-par", trades: "trace-corp-trades", monthly: true  },
  { id: "eln",  label: "ELN",            par: "trace-eln-par",  trades: "trace-eln-trades",   monthly: true  },
  { id: "conv", label: "Convertibles",   par: "trace-conv-par", trades: "trace-conv-trades", monthly: true  },
  { id: "agcy", label: "Agency",         par: "trace-agcy-par", trades: null,                 monthly: true  },
  { id: "abs",  label: "ABS",            par: "trace-abs-par",  trades: null,                 monthly: true  },
  { id: "absx", label: "ABSX",           par: "trace-absx-par", trades: null,                 monthly: true  },
  { id: "cmo",  label: "CMO",            par: "trace-cmo-par",  trades: null,                 monthly: true  },
  { id: "mbs",  label: "MBS",            par: "trace-mbs-par",  trades: null,                 monthly: true  },
  { id: "chrc", label: "Church",         par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true  },
  // STAR: FINRA IDS Structured Trading Activity Reports (daily).
  // Granular breakdown: TBA by issuer, specified pools, agency/non-agency
  // CMO/CMBS, ABS, CLO. Daily data — 1D/1W deltas are meaningful.
  { id: "star-total", label: "STAR — Total", synthetic: true, star: true },
  { id: "star-tba",      label: "STAR — TBA Total",       par: "star-tba-par",      trades: "star-tba-trades",      monthly: false, daily: true, star: true },
  { id: "star-tba-umbs",  label: "STAR — TBA UMBS",       par: "star-tba-umbs-par",  trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-tba-fnma",  label: "STAR — TBA FNMA",       par: "star-tba-fnma-par",  trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-tba-fhlmc", label: "STAR — TBA FHLMC",      par: "star-tba-fhlmc-par", trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-tba-gnma",  label: "STAR — TBA GNMA",       par: "star-tba-gnma-par",  trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-spec",     label: "STAR — Specified Pools", par: "star-spec-par",     trades: "star-spec-trades",    monthly: false, daily: true, star: true },
  { id: "star-agcmo",    label: "STAR — Agency CMO",      par: "star-agcmo-par",    trades: "star-agcmo-trades",   monthly: false, daily: true, star: true },
  { id: "star-nagcmo",    label: "STAR — Non-Agency CMO",      par: "star-nagcmo-par",        trades: "star-nagcmo-trades",        monthly: false, daily: true, star: true },
  { id: "star-nagcmo-ig",    label: "STAR — Non-Agency CMO IG",      par: "star-nagcmo-ig-par",     trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-nagcmo-hy",    label: "STAR — Non-Agency CMO HY",      par: "star-nagcmo-nonig-par",  trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-nagcmbs",    label: "STAR — Non-Agency CMBS",      par: "star-nagcmbs-par",        trades: "star-nagcmbs-trades",        monthly: false, daily: true, star: true },
  { id: "star-nagcmbs-ig",    label: "STAR — Non-Agency CMBS IG",      par: "star-nagcmbs-ig-par",     trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-nagcmbs-hy",    label: "STAR — Non-Agency CMBS HY",      par: "star-nagcmbs-nonig-par",  trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-agcmbs",   label: "STAR — Agency CMBS",     par: "star-agcmbs-par",   trades: "star-agcmbs-trades",  monthly: false, daily: true, star: true },
  { id: "star-abs",      label: "STAR — ABS Total",       par: "star-abs-par",      trades: "star-abs-trades",     monthly: false, daily: true, star: true },
  { id: "star-abs-ig",   label: "STAR — ABS IG",          par: "star-abs-ig-par",   trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-abs-hy",   label: "STAR — ABS HY",          par: "star-abs-nonig-par", trades: null,                 monthly: false, daily: true, star: true },
  { id: "star-clo",      label: "STAR — CLO Total",       par: "star-clo-par",      trades: "star-clo-trades",     monthly: false, daily: true, star: true },
  { id: "star-clo-ig",   label: "STAR — CLO IG",          par: "star-clo-ig-par",   trades: null,                  monthly: false, daily: true, star: true },
  { id: "star-clo-hy",   label: "STAR — CLO HY",          par: "star-clo-nonig-par", trades: null,                 monthly: false, daily: true, star: true },
  // Short interest: biweekly settlement levels (shares), not rates — no
  // trading-day division; deltas are true MoM (vs ~30d prior point) and
  // YoY (vs ~365d prior point) since adjacent points are 2 weeks apart.
  { id: "si-total", label: "Short Int — Total", par: "finra-short-total", unit: "sh", raw: true },
  { id: "si-msft",  label: "Short Int — MSFT",  par: "short-MSFT",  unit: "sh", raw: true },
  { id: "si-nvda",  label: "Short Int — NVDA",  par: "short-NVDA",  unit: "sh", raw: true },
  { id: "si-aapl",  label: "Short Int — AAPL",  par: "short-AAPL",  unit: "sh", raw: true },
  { id: "si-amzn",  label: "Short Int — AMZN",  par: "short-AMZN",  unit: "sh", raw: true },
  { id: "si-googl", label: "Short Int — GOOGL", par: "short-GOOGL", unit: "sh", raw: true },
  { id: "si-meta",  label: "Short Int — META",  par: "short-META",  unit: "sh", raw: true },
  // Market breadth (FINRA, daily since Jan 2018). A/D spread = advances −
  // declines; advances/declines/hi52/lo52 are issue counts. All daily —
  // 1D/1W deltas are meaningful. Corp/144A split IG/HY; agency splits by issuer.
  { id: "br-corp-all-spr",  label: "Breadth — Corp All A/D Spread",  par: "finra-breadth-corp-all-adspread",  unit: "ct", daily: true, breadth: true },
  { id: "br-corp-ig-spr",   label: "Breadth — Corp IG A/D Spread",   par: "finra-breadth-corp-ig-adspread",   unit: "ct", daily: true, breadth: true },
  { id: "br-corp-hy-spr",   label: "Breadth — Corp HY A/D Spread",   par: "finra-breadth-corp-hy-adspread",   unit: "ct", daily: true, breadth: true },
  { id: "br-agcy-all-spr",  label: "Breadth — Agency All A/D Spread", par: "finra-breadth-agency-all-adspread", unit: "ct", daily: true, breadth: true },
  { id: "br-144a-all-spr",  label: "Breadth — 144A All A/D Spread",  par: "finra-breadth-144a-all-adspread",  unit: "ct", daily: true, breadth: true },
  { id: "br-144a-ig-spr",   label: "Breadth — 144A IG A/D Spread",   par: "finra-breadth-144a-ig-adspread",   unit: "ct", daily: true, breadth: true },
  { id: "br-144a-hy-spr",   label: "Breadth — 144A HY A/D Spread",   par: "finra-breadth-144a-hy-adspread",   unit: "ct", daily: true, breadth: true },
  { id: "br-corp-all-adv",  label: "Breadth — Corp All Advances",    par: "finra-breadth-corp-all-adv",  unit: "ct", daily: true, breadth: true },
  { id: "br-corp-all-dec",  label: "Breadth — Corp All Declines",    par: "finra-breadth-corp-all-dec",  unit: "ct", daily: true, breadth: true },
  { id: "br-corp-all-hi52", label: "Breadth — Corp All 52wk Highs",  par: "finra-breadth-corp-all-hi52", unit: "ct", daily: true, breadth: true },
  { id: "br-corp-all-lo52", label: "Breadth — Corp All 52wk Lows",   par: "finra-breadth-corp-all-lo52", unit: "ct", daily: true, breadth: true },
  { id: "br-agcy-all-adv",  label: "Breadth — Agency All Advances",  par: "finra-breadth-agency-all-adv",  unit: "ct", daily: true, breadth: true },
  { id: "br-agcy-all-dec",  label: "Breadth — Agency All Declines",  par: "finra-breadth-agency-all-dec",  unit: "ct", daily: true, breadth: true },
  { id: "br-agcy-all-hi52", label: "Breadth — Agency All 52wk Highs", par: "finra-breadth-agency-all-hi52", unit: "ct", daily: true, breadth: true },
  { id: "br-agcy-all-lo52", label: "Breadth — Agency All 52wk Lows", par: "finra-breadth-agency-all-lo52", unit: "ct", daily: true, breadth: true },
  { id: "br-144a-all-adv",  label: "Breadth — 144A All Advances",    par: "finra-breadth-144a-all-adv",  unit: "ct", daily: true, breadth: true },
  { id: "br-144a-all-dec",  label: "Breadth — 144A All Declines",    par: "finra-breadth-144a-all-dec",  unit: "ct", daily: true, breadth: true },
  { id: "br-144a-all-hi52", label: "Breadth — 144A All 52wk Highs",  par: "finra-breadth-144a-all-hi52", unit: "ct", daily: true, breadth: true },
  { id: "br-144a-all-lo52", label: "Breadth — 144A All 52wk Lows",   par: "finra-breadth-144a-all-lo52", unit: "ct", daily: true, breadth: true },
  // Market sentiment (FINRA, daily since Jan 2018). Net flow = dealer sells −
  // dealer buys ($M par); + = customers net buying (risk-on). Dealer buys/
  // sells are $M par customer flow.
  { id: "se-corp-all-flow",  label: "Sentiment — Corp All Net Flow",   par: "finra-sent-corp-all-netflow",  unit: "$M", daily: true, sent: true },
  { id: "se-corp-ig-flow",   label: "Sentiment — Corp IG Net Flow",    par: "finra-sent-corp-ig-netflow",   unit: "$M", daily: true, sent: true },
  { id: "se-corp-hy-flow",   label: "Sentiment — Corp HY Net Flow",    par: "finra-sent-corp-hy-netflow",   unit: "$M", daily: true, sent: true },
  { id: "se-agcy-all-flow",  label: "Sentiment — Agency All Net Flow", par: "finra-sent-agency-all-netflow", unit: "$M", daily: true, sent: true },
  { id: "se-144a-all-flow",  label: "Sentiment — 144A All Net Flow",   par: "finra-sent-144a-all-netflow",  unit: "$M", daily: true, sent: true },
  { id: "se-144a-ig-flow",   label: "Sentiment — 144A IG Net Flow",    par: "finra-sent-144a-ig-netflow",   unit: "$M", daily: true, sent: true },
  { id: "se-144a-hy-flow",   label: "Sentiment — 144A HY Net Flow",    par: "finra-sent-144a-hy-netflow",   unit: "$M", daily: true, sent: true },
  { id: "se-corp-all-buy",   label: "Sentiment — Corp All Dealer Buys",  par: "finra-sent-corp-all-dbuy-vol",  unit: "$M", daily: true, sent: true },
  { id: "se-corp-all-sell",  label: "Sentiment — Corp All Dealer Sells", par: "finra-sent-corp-all-dsell-vol", unit: "$M", daily: true, sent: true },
  { id: "se-agcy-all-buy",   label: "Sentiment — Agency All Dealer Buys",  par: "finra-sent-agency-all-dbuy-vol",  unit: "$M", daily: true, sent: true },
  { id: "se-agcy-all-sell",  label: "Sentiment — Agency All Dealer Sells", par: "finra-sent-agency-all-dsell-vol", unit: "$M", daily: true, sent: true },
  { id: "se-144a-all-buy",   label: "Sentiment — 144A All Dealer Buys",  par: "finra-sent-144a-all-dbuy-vol",  unit: "$M", daily: true, sent: true },
  { id: "se-144a-all-sell",  label: "Sentiment — 144A All Dealer Sells", par: "finra-sent-144a-all-dsell-vol", unit: "$M", daily: true, sent: true },
  // Short interest top-25 (Reg SHO daily, 2Y backfilled). par = short volume
  // series, trades = total volume series (for ratio). Metric toggle: ADV/PAR
  // → short volume (shares); ADT/TRADES → short ratio (short/total).
  // Tickers rotate; these cover the persistent top names — missing data shows "—".
  { id: "sit-msft",  label: "Short — MSFT",  par: "regsho-top-MSFT-shortvol",  trades: "regsho-top-MSFT-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-nvda",  label: "Short — NVDA",  par: "regsho-top-NVDA-shortvol",  trades: "regsho-top-NVDA-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-aapl",  label: "Short — AAPL",  par: "regsho-top-AAPL-shortvol",  trades: "regsho-top-AAPL-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-amzn",  label: "Short — AMZN",  par: "regsho-top-AMZN-shortvol",  trades: "regsho-top-AMZN-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-googl", label: "Short — GOOGL", par: "regsho-top-GOOGL-shortvol", trades: "regsho-top-GOOGL-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-meta",  label: "Short — META",  par: "regsho-top-META-shortvol",  trades: "regsho-top-META-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-tsla",  label: "Short — TSLA",  par: "regsho-top-TSLA-shortvol",  trades: "regsho-top-TSLA-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-amd",   label: "Short — AMD",   par: "regsho-top-AMD-shortvol",   trades: "regsho-top-AMD-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-nflx",  label: "Short — NFLX",  par: "regsho-top-NFLX-shortvol",  trades: "regsho-top-NFLX-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-jpm",   label: "Short — JPM",   par: "regsho-top-JPM-shortvol",   trades: "regsho-top-JPM-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-bac",   label: "Short — BAC",   par: "regsho-top-BAC-shortvol",   trades: "regsho-top-BAC-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-xom",   label: "Short — XOM",   par: "regsho-top-XOM-shortvol",   trades: "regsho-top-XOM-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-wmt",   label: "Short — WMT",   par: "regsho-top-WMT-shortvol",   trades: "regsho-top-WMT-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-jnj",   label: "Short — JNJ",   par: "regsho-top-JNJ-shortvol",   trades: "regsho-top-JNJ-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-v",     label: "Short — V",     par: "regsho-top-V-shortvol",     trades: "regsho-top-V-totalvol",     unit: "sh", daily: true, siTop: true },
  { id: "sit-unh",   label: "Short — UNH",   par: "regsho-top-UNH-shortvol",   trades: "regsho-top-UNH-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-hd",    label: "Short — HD",    par: "regsho-top-HD-shortvol",    trades: "regsho-top-HD-totalvol",    unit: "sh", daily: true, siTop: true },
  { id: "sit-dis",   label: "Short — DIS",   par: "regsho-top-DIS-shortvol",   trades: "regsho-top-DIS-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-intc",  label: "Short — INTC",  par: "regsho-top-INTC-shortvol",  trades: "regsho-top-INTC-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-crm",   label: "Short — CRM",   par: "regsho-top-CRM-shortvol",   trades: "regsho-top-CRM-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-abbv",  label: "Short — ABBV",  par: "regsho-top-ABBV-shortvol",  trades: "regsho-top-ABBV-totalvol",  unit: "sh", daily: true, siTop: true },
  { id: "sit-ko",    label: "Short — KO",    par: "regsho-top-KO-shortvol",    trades: "regsho-top-KO-totalvol",    unit: "sh", daily: true, siTop: true },
  { id: "sit-pep",   label: "Short — PEP",   par: "regsho-top-PEP-shortvol",   trades: "regsho-top-PEP-totalvol",   unit: "sh", daily: true, siTop: true },
  { id: "sit-ma",    label: "Short — MA",    par: "regsho-top-MA-shortvol",    trades: "regsho-top-MA-totalvol",    unit: "sh", daily: true, siTop: true },
  { id: "sit-pg",    label: "Short — PG",    par: "regsho-top-PG-shortvol",    trades: "regsho-top-PG-totalvol",    unit: "sh", daily: true, siTop: true },
];
// Components summed into the synthetic TOTAL row: Treasury Total + all 10
// TRACE products. Treasury breakdown rows (bills/coupons/TIPS/FRNs/
// on-the-run/off-the-run) are EXCLUDED — bills+coupons+TIPS+FRNs sum to the
// Treasury Total, so including any breakdown would double-count Treasury.
// Note: on-the-run + off-the-run cover coupons + TIPS only (bills and FRNs
// have no on/off-the-run split in the FINRA file), so they do NOT sum to
// the Treasury Total — verified Sep-26: on+off = $23,483.5B vs Total $28,142.0B.
export const TOTAL_PARTS = PRODUCTS.filter((p) =>
  !p.synthetic && !p.raw && !p.star && !p.id.startsWith("ust-")); // ust- bills/coupons/tips/frns/onrun/offrun excluded; star excluded (separate total)
// Components summed into the synthetic STAR — Total row: the 8 top-level
// STAR categories. Issuer/grade breakdowns (TBA UMBS/FNMA/FHLMC/GNMA,
// non-agency IG/HY, ABS IG/HY, CLO IG/HY) are EXCLUDED — they sum to their
// respective totals, so including them would double-count.
export const STAR_TOTAL_PARTS = PRODUCTS.filter((p) =>
  p.star && !p.synthetic && ["star-tba", "star-spec", "star-agcmo", "star-nagcmo",
    "star-nagcmbs", "star-agcmbs", "star-abs", "star-clo"].includes(p.id));

// ---- NYSE trading-day calendar ----
const ONE_OFF_CLOSURES = new Set(["2018-12-05"]); // G.H.W. Bush national day of mourning
function easterSunday(y) {
  const a = y % 19, b = (y / 100) | 0, c = y % 100, d = (b / 4) | 0, e = b % 4,
        f = ((b + 8) / 25) | 0, g = ((b - f + 1) / 3) | 0,
        h = (19 * a + b - d - g + 15) % 30, i = (c / 4) | 0, k = c % 4,
        l = (32 + 2 * e + 2 * i - h - k) % 7, m = ((a + 11 * h + 22 * l) / 451) | 0;
  const mo = ((h + l - 7 * m + 114) / 31) | 0, da = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(y, mo - 1, da));
}
function nthWeekday(y, mo, wd, n) { // mo 0-based; n>=1, -1 = last
  if (n > 0) {
    const d = new Date(Date.UTC(y, mo, 1));
    const off = (wd - d.getUTCDay() + 7) % 7;
    return new Date(Date.UTC(y, mo, 1 + off + 7 * (n - 1)));
  }
  const last = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
  const d = new Date(Date.UTC(y, mo, last));
  return new Date(Date.UTC(y, mo, last - ((d.getUTCDay() - wd + 7) % 7)));
}
const iso = (d) => d.toISOString().slice(0, 10);
function observed(dt, isNewYear) {
  const w = dt.getUTCDay();
  if (w === 6) {
    // NYSE precedent: a Saturday Jan 1 is NOT observed (Dec 31, 2021 traded).
    // Verified against canonical TRACE history: 2020-07-03 and 2021-12-24 closed.
    if (isNewYear) return null;
    return new Date(dt.getTime() - 864e5);
  }
  if (w === 0) return new Date(dt.getTime() + 864e5);
  return dt;
}
function nyseHolidays(y) {
  const s = new Set();
  const add = (d) => s.add(iso(d));
  const ny = observed(new Date(Date.UTC(y, 0, 1)), true);
  if (ny) add(ny);
  add(nthWeekday(y, 0, 1, 3)); add(nthWeekday(y, 1, 1, 3));
  add(new Date(easterSunday(y).getTime() - 2 * 864e5));
  add(nthWeekday(y, 4, 1, -1));
  if (y >= 2022) add(observed(new Date(Date.UTC(y, 5, 19))));
  add(observed(new Date(Date.UTC(y, 6, 4))));
  add(nthWeekday(y, 8, 1, 1));
  add(nthWeekday(y, 10, 4, 4));
  add(observed(new Date(Date.UTC(y, 11, 25))));
  return s;
}
function tradingDays(y, m) { // m 1-based
  const hol = new Set();
  for (const yy of [y - 1, y, y + 1]) for (const d of nyseHolidays(yy)) hol.add(d);
  const n = new Date(Date.UTC(y, m, 0)).getUTCDate();
  let c = 0;
  for (let d = 1; d <= n; d++) {
    const dt = new Date(Date.UTC(y, m - 1, d));
    if (dt.getUTCDay() === 0 || dt.getUTCDay() === 6) continue;
    const s = iso(dt);
    if (hol.has(s) || ONE_OFF_CLOSURES.has(s)) continue;
    c++;
  }
  return c;
}
const isMonthEnd = (d) => {
  const [y, m, dd] = d.split("-").map(Number);
  return dd === new Date(Date.UTC(y, m, 0)).getUTCDate();
};

// ---- data shaping ----
// Monthly ADV ($B/day) from raw points. Treasury rows (monthly:false) use
// month-end points only (monthly-file values are monthly TOTALS in $bn).
export function toAdv(p, points) {
  // Daily products (STAR): values are already daily — return as-is.
  if (p.daily) return points.map(([d, v]) => ({ d, v }));
  if (!p.monthly) return points.filter(([d]) => isMonthEnd(d)).map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) };
  });
  return points.map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) / 1000 }; // $M monthly -> $B/day
  });
}
export function toAdt(p, points) {
  // Daily products (STAR): trade counts are already daily.
  if (p.daily) return points.map(([d, v]) => ({ d, v }));
  const src = p.monthly ? points : points.filter(([d]) => isMonthEnd(d));
  return src.map(([d, v]) => {
    const [y, m] = d.split("-").map(Number);
    return { d, v: v / tradingDays(y, m) };
  });
}
// Monthly PAR totals ($B/month). Treasury rows are already $bn monthly
// totals; monthly TRACE products are $M monthly -> $B.
// Daily products (STAR): par is already daily $ — return as-is.
export function toPar(p, points) {
  if (p.daily) return points.map(([d, v]) => ({ d, v }));
  if (!p.monthly) return points.filter(([d]) => isMonthEnd(d)).map(([d, v]) => ({ d, v }));
  return points.map(([d, v]) => ({ d, v: v / 1000 }));
}
// Monthly TRADE totals (raw counts). Treasury rows use month-end points.
// Daily products (STAR): trades are already daily counts — return as-is.
export function toTrades(p, points) {
  if (p.daily) return points.map(([d, v]) => ({ d, v }));
  const src = p.monthly ? points : points.filter(([d]) => isMonthEnd(d));
  return src.map(([d, v]) => ({ d, v }));
}
// Normalize raw points to one of the four selectable metrics.
export function toMetric(p, points, metric) {
  switch (metric) {
    case "adt": return toAdt(p, points);
    case "par": return toPar(p, points);
    case "trades": return toTrades(p, points);
    default: return toAdv(p, points);
  }
}
export const METRICS = [
  { id: "adv", label: "ADV", unit: "$B/d", title: "Average daily par volume" },
  { id: "adt", label: "ADT", unit: "trades/d", title: "Average daily trade count" },
  { id: "par", label: "PAR", unit: "$B/mo", title: "Total par volume for the month" },
  { id: "trades", label: "TRADES", unit: "trades/mo", title: "Total trade count for the month" },
];
export const metricById = (id) => METRICS.find((m) => m.id === id) || METRICS[0];

// Range definitions: trailing windows in months, plus custom date range.
// Stats (percentile/z/avg) are computed over the selected window (min 12
// points); the window label is shown on the range columns.
export const RANGES = [
  { id: "1m", label: "1M", months: 1 },
  { id: "3m", label: "3M", months: 3 },
  { id: "6m", label: "6M", months: 6 },
  { id: "1y", label: "1Y", months: 12 },
  { id: "3y", label: "3Y", months: 36 },
  { id: "5y", label: "5Y", months: 60 },
  { id: "max", label: "MAX", months: Infinity },
  { id: "custom", label: "Custom", months: null },
];
export const rangeById = (id) => RANGES.find((r) => r.id === id) || RANGES[4];
const DAY_MS = 864e5;
const dayDiff = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / DAY_MS);
// Harry's universal horizon standard (2026-10-05): every % change comparison
// shows 1D/1W/1M/1Q/1Y. Monthly/biweekly rows can't support 1D/1W — those
// cells render "—" (see rowStats/rowStatsLevel).
//
// Range handling: `range` = { months } trailing window, or { start, end }
// custom dates. `full` is the complete monthly series (for % changes vs
// history); `win` is the window slice used for percentile/z/avg/sparkline.
// Stats need >= 12 points to be meaningful — shorter windows fall back to
// the trailing 3Y and the fallback is flagged.
export function rowStats(vals, range) { // vals sorted asc by d — monthly ADV/ADT/PAR/TRADES series
  const n = vals.length;
  if (!n) return null;
  const { win, winLabel, fellBack } = windowSlice(vals, range);
  if (!win.length) return null;
  const cur = win[win.length - 1];
  // % changes are vs full history (not window-truncated) so 1Y works on any range.
  const idx = vals.findIndex((v) => v.d === cur.d);
  const back = (k) => (idx >= k ? vals[idx - k] : null);
  const ym = cur.d.slice(0, 7);
  const yoyKey = `${+ym.slice(0, 4) - 1}${ym.slice(4)}`;
  const yoyPt = vals.find((v) => v.d.slice(0, 7) === yoyKey);
  return finishStats(win, cur, { d1: null, w1: null, m1: back(1), q1: back(3), y1: yoyPt, y3: null }, winLabel, fellBack);
}
// Daily stats (STAR): true daily deltas. 1D = prior trading day, 1W = 7
// calendar days back, 1M = 30d, 1Q = 91d, 1Y = 365d, 3Y = 1095d. Uses the
// nearest available point at least N days back (handles weekends/holidays).
export function rowStatsDaily(vals, range) {
  const n = vals.length;
  if (!n) return null;
  const { win, winLabel, fellBack } = windowSliceDaily(vals, range);
  if (!win.length) return null;
  const cur = win[win.length - 1];
  // Reference lookup on full history (not window-truncated).
  const idx = vals.findIndex((v) => v.d === cur.d);
  const refBack = (days) => {
    if (idx < 0) return null;
    let ref = null;
    for (let i = idx - 1; i >= 0; i--) {
      if (dayDiff(vals[i].d, cur.d) >= days) { ref = vals[i]; break; }
    }
    return ref;
  };
  return finishStats(win, cur, {
    d1: refBack(1), w1: refBack(7), m1: refBack(30),
    q1: refBack(91), y1: refBack(365), y3: refBack(1095),
  }, winLabel, fellBack);
}
// Slice daily vals to the selected range (day-based, not month-based).
export function windowSliceDaily(vals, range) {
  if (!range || range.id === "max") return { win: vals, winLabel: "full history", fellBack: false };
  let win;
  if (range.id === "custom" && range.start && range.end) {
    win = vals.filter((v) => v.d >= range.start && v.d <= range.end);
  } else {
    // Convert month-based ranges to approximate day counts for daily data.
    const days = { "1m": 22, "3m": 66, "6m": 132, "1y": 262, "3y": 786, "5y": 1310 }[range.id] || 786;
    win = vals.slice(-days);
  }
  if (win.length >= 22) {
    const lbl = range.id === "custom" ? `${win[0].d}→${win[win.length - 1].d}` : range.label;
    return { win, winLabel: lbl, fellBack: false };
  }
  const fb = vals.slice(-786);
  const anchor = win.length ? win[win.length - 1] : fb[fb.length - 1];
  const fbWin = fb.filter((v) => v.d <= anchor.d);
  return { win: fbWin.length >= 22 ? fbWin : fb, winLabel: "3Y", fellBack: true };
}
// Slice vals to the selected range. Returns the window slice plus a label
// and whether we fell back to 3Y (window too short for meaningful stats).
export function windowSlice(vals, range) {
  if (!range || range.id === "max") return { win: vals, winLabel: "full history", fellBack: false };
  let win;
  if (range.id === "custom" && range.start && range.end) {
    win = vals.filter((v) => v.d >= range.start && v.d <= range.end);
  } else {
    const months = range.months || 36;
    win = vals.slice(-months);
  }
  if (win.length >= 12) {
    const lbl = range.id === "custom" ? `${win[0].d.slice(0, 7)}→${win[win.length - 1].d.slice(0, 7)}` : range.label;
    return { win, winLabel: lbl, fellBack: false };
  }
  // Too short — fall back to trailing 3Y for stats, keep window's endpoint as "now".
  const fb = vals.slice(-36);
  const anchor = win.length ? win[win.length - 1] : fb[fb.length - 1];
  const fbWin = fb.filter((v) => v.d <= anchor.d);
  return { win: fbWin.length >= 12 ? fbWin : fb, winLabel: "3Y", fellBack: true };
}
export function rowStatsLevel(vals) { // vals sorted asc — raw level series (biweekly short interest)
  const n = vals.length;
  if (!n) return null;
  const cur = vals[n - 1];
  // nearest prior point at least N days back (adjacent points are 2 weeks apart)
  const refBack = (days) => {
    let ref = null;
    for (const v of vals) {
      if (v.d < cur.d && dayDiff(v.d, cur.d) >= days) ref = v;
    }
    return ref;
  };
  return finishStats(vals, cur, { d1: null, w1: null, m1: refBack(30), q1: refBack(91), y1: refBack(365) });
}
export function finishStats(vals, cur, refs, winLabel = "3Y", fellBack = false) {
  const win = vals; // vals IS the window slice here (caller slices via windowSlice)
  const vs = win.map((v) => v.v);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const avg = vs.reduce((a, b) => a + b, 0) / vs.length;
  const sd = Math.sqrt(vs.reduce((a, b) => a + (b - avg) ** 2, 0) / vs.length);
  const z = sd > 0 ? (cur.v - avg) / sd : null;
  const zs = sd > 0 ? vs.map((v) => (v - avg) / sd) : vs.map(() => 0);
  const rank = vs.filter((v) => v <= cur.v).length;
  const rc = (r) => (r && r.v ? (cur.v - r.v) / r.v : null);
  // 52w hi/lo: last 12 monthly points of the 3Y window.
  const w52 = vals.slice(-12).map((v) => v.v);
  return {
    cur, lo, hi, avg, sd, z, zlo: Math.min(...zs), zhi: Math.max(...zs),
    win, n: vs.length, asof: cur.d, winLabel, fellBack,
    hi52: w52.length ? Math.max(...w52) : null,
    lo52: w52.length ? Math.min(...w52) : null,
    d1: rc(refs.d1), w1: rc(refs.w1), m1: rc(refs.m1), q1: rc(refs.q1), y1: rc(refs.y1),
    y3: rc(refs.y3),
    d3: avg ? (cur.v - avg) / avg : null,
    pct: (100 * rank) / vs.length,
  };
}
// Synthetic TOTAL: sum monthly values (ADV/ADT/PAR/TRADES) of the given
// `parts` (default: grid TOTAL_PARTS = Treasury + all 10 TRACE products;
// note the grid's breakdown rows are NOT in the default set to avoid
// double-counting Treasury) by calendar month. ADT/TRADES only sum
// products with trade-count data (tba/agcy/abs/absx/cmo/mbs publish none).
// Exported for the KOI scorecard's TOTAL KPI.
export function totalVals(data, metric, parts = TOTAL_PARTS) {
  const sums = new Map(); // "YYYY-MM" -> {d, v}
  const isCount = metric === "adt" || metric === "trades";
  for (const { p, par, tr } of data) {
    if (parts.indexOf(p) < 0) continue;
    const src = isCount ? tr : par;
    if (!src) continue;
    const vals = toMetric(p, src, metric);
    for (const { d, v } of vals) {
      const key = d.slice(0, 7);
      const e = sums.get(key);
      if (e) { e.v += v; if (d > e.d) e.d = d; }
      else sums.set(key, { d, v });
    }
  }
  return [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
}

// ---- formatting ----
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const mlabel = (d) => `${MONTHS[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`;
const fmtB = (v) => v == null || !isFinite(v) ? "—" : v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
const fmtN = (v) => v == null || !isFinite(v) ? "—" : Math.round(v).toLocaleString("en-US");
const fmtSh = (v) => v == null || !isFinite(v) ? "—" :
  v >= 1e12 ? (v / 1e12).toFixed(2) + "T sh" :
  v >= 1e9 ? (v / 1e9).toFixed(2) + "B sh" :
  v >= 1e6 ? (v / 1e6).toFixed(1) + "M sh" : fmtN(v) + " sh";
const pct1 = (x) => x == null || !isFinite(x) ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const heat = (x) => {
  if (x == null || !isFinite(x)) return "";
  const a = Math.min(Math.abs(x) / 0.25, 1) * 0.45;
  return ` style="background:rgba(${x >= 0 ? "22,163,74" : "220,38,38"},${a.toFixed(2)})"`;
};

// Bloomberg-style dotted range sparkline lives in ../rangeviz.js (shared).
// (local definition removed 2026-10-05; re-exported here for compatibility)
export { rangePlotDotted } from "../rangeviz.js";
function sparkline(win) {
  if (!win || win.length < 2) return `<span class="muted">—</span>`;
  const w = 120, h = 34;
  const vs = win.map((p) => p.v);
  const lo = Math.min(...vs), hi = Math.max(...vs), rg = (hi - lo) || 1;
  const pts = win.map((p, i) =>
    `${(i / (win.length - 1) * w).toFixed(1)},${(h - 3 - ((p.v - lo) / rg) * (h - 6)).toFixed(1)}`).join(" ");
  const up = vs[vs.length - 1] >= vs[0];
  return `<svg class="tspark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<polyline points="${pts}" fill="none" style="stroke:${up ? "var(--up)" : "var(--down)"}" stroke-width="1.5"/></svg>`;
}

// ---- grid ----
const state = { metric: "adv", range: rangeById("3y"), customStart: null, customEnd: null, sortKey: null, sortDir: 1, rows: [], asof: null, reqId: 0 };

const metricColTitle = () => {
  const m = metricById(state.metric);
  return state.metric === "adv" ? "ADV $B/d"
    : state.metric === "adt" ? "ADT"
    : state.metric === "par" ? "PAR $B/mo" : "TRADES/mo";
};
// Formatter for the "current value" column by metric (raw SI rows use shares).
const fmtCurFor = (p) => {
  if (p.unit === "sh" && !p._ratioMode) return fmtSh;
  if (p._ratioMode) return (v) => v == null || !isFinite(v) ? "—" : `${(v * 100).toFixed(1)}%`;
  if (p.unit === "ct") return fmtN;
  if (p.unit === "$M") return (v) => v == null || !isFinite(v) ? "—" : `$${fmtN(v)}M`;
  if (p.unit === "px") return (v) => v == null || !isFinite(v) ? "—" : v.toFixed(2);
  return state.metric === "adt" || state.metric === "trades" ? fmtN : fmtB;
};

const COLS = [
  { key: "label", title: "Product", num: false },
  { key: "cur",   title: "ADV $B/d", num: true },
  { key: "out",   title: "Outst $T", num: true, tip: "Par outstanding — hover each row's value for source/as-of" },
  { key: "turn",  title: "Turnov ann.%", num: true, tip: "ADV × 252 ÷ outstanding (annualized %). ADV view only." },
  { key: "d1", title: "1D %", num: true, heat: true, tip: "1-day % change (n/a for monthly data)" },
  { key: "w1", title: "1W %", num: true, heat: true, tip: "1-week % change (n/a for monthly data)" },
  { key: "m1", title: "1M %", num: true, heat: true, tip: "1-month % change" },
  { key: "q1", title: "1Q %", num: true, heat: true, tip: "3-month % change" },
  { key: "y1", title: "1Y %", num: true, heat: true, tip: "12-month % change" },
  { key: "y3", title: "3Y %", num: true, heat: true, tip: "3-year % change (daily data only)" },
  { key: "d3",    title: "Δ 3Y avg %", num: true, heat: true },
  { key: "rngpct", title: "Range %ile", num: false, tip: "Dotted 3Y range: blue dot = now (percentile), ◆ = 50th pct" },
  { key: "rngz", title: "Range z", num: false, tip: "Dotted 3Y range: blue dot = now (z-score), ◆ = mean (z=0)" },
  { key: "hi52",  title: "52w Hi", num: true, tip: "Highest monthly value in the last 12 months" },
  { key: "lo52",  title: "52w Lo", num: true, tip: "Lowest monthly value in the last 12 months" },
  { key: "lo",    title: "Low", num: true, tip: "3Y window low" },
  { key: "hi",    title: "High", num: true, tip: "3Y window high" },
  { key: "avg",   title: "Avg", num: true, tip: "3Y window average" },
  { key: "pct",   title: "RS", num: true, tip: "Relative-strength rank: percentile of current value vs 3Y history (0-100)" },
  { key: "z",     title: "z", num: true, tip: "Current z-score vs 3Y window" },
  { key: "trend", title: "Trend (3Y)", num: false },
];

async function loadData() {
  const jobs = PRODUCTS.filter((p) => !p.synthetic).map(async (p) => {
    const [par, tr] = await Promise.all([
      getSeries(p.par, "max"),
      p.trades ? getSeries(p.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    return { p, par: par.points, tr: tr ? tr.points : null };
  });
  const results = await Promise.all(jobs);
  // Most-active CUSIPs: build synthetic products from corp panel bond_hist.
  // Top-10 by latest volume across all lists.
  if (state.corpData) {
    const cusips = extractTopCusips(state.corpData, 10);
    for (const c of cusips) {
      results.push({ p: c.product, par: c.pricePoints, tr: null, cusipHist: c });
    }
  }
  return results;
}

// Extract top-N CUSIPs by volume from corp panel lists + bond_hist.
function extractTopCusips(corpData, n) {
  const lists = corpData.lists ?? {};
  const hist = corpData.bond_hist ?? {};
  const seen = new Map();
  for (const [, l] of Object.entries(lists)) {
    for (const b of (l.bonds ?? [])) {
      const cusip = b.symbol;
      if (!cusip || seen.has(cusip)) continue;
      const h = hist[cusip];
      if (!h || !h.d || h.d.length < 2) continue;
      seen.set(cusip, {
        cusip,
        issuer: b.issuer ?? b.name ?? cusip,
        coupon: b.coupon,
        pricePoints: h.d.map((d, i) => [d, h.p[i]]).filter(([, v]) => v != null),
        yieldPoints: h.d.map((d, i) => [d, h.y[i]]).filter(([, v]) => v != null),
      });
      if (seen.size >= n * 2) break; // collect extra, sort by volume below
    }
  }
  // Sort by latest price point recency + take top n (volume rank from lists order)
  return [...seen.values()].slice(0, n).map((c) => ({
    ...c,
    product: {
      id: `cusip-${c.cusip}`, label: `Bond — ${c.cusip}`,
      unit: "px", daily: true, cusip: true,
      issuer: c.issuer, coupon: c.coupon,
    },
  }));
}

function buildRows(data, metric, range) {
  const isCount = metric === "adt" || metric === "trades";
  const rows = data.map(({ p, par, tr }) => {
    if (p.raw) {
      const vals = par.map(([d, v]) => ({ d, v }))
        .sort((a, b) => (a.d < b.d ? -1 : 1));
      return { p, stats: rowStatsLevel(vals), out: null };
    }
    if (p.cusip) {
      // Most-active CUSIP: price history from bond_hist (in-memory).
      // Shows clean price with full daily deltas.
      const vals = par.map(([d, v]) => ({ d, v }))
        .sort((a, b) => (a.d < b.d ? -1 : 1));
      if (!vals.length) return { p, stats: null, out: null };
      return { p, stats: rowStatsDaily(vals, range), out: null };
    }
    if (p.siTop) {
      // Short interest top tickers: ADV/PAR → short volume (shares);
      // ADT/TRADES → short ratio (short vol / total vol).
      const sv = par.map(([d, v]) => ({ d, v })).sort((a, b) => (a.d < b.d ? -1 : 1));
      let vals = sv;
      if (isCount && tr) {
        const tvByDate = new Map(tr.map(([d, v]) => [d, v]));
        vals = sv.map((s) => {
          const tv = tvByDate.get(s.d);
          return tv ? { d: s.d, v: s.v / tv } : null;
        }).filter(Boolean);
        p._ratioMode = true;
      } else {
        p._ratioMode = false;
      }
      if (!vals.length) return { p, stats: null, out: null };
      return { p, stats: rowStatsDaily(vals, range), out: null };
    }
    const src = isCount ? tr : par;
    if (!src) return { p, stats: null, out: OUTSTANDING[p.id] || null };
    const vals = toMetric(p, src, metric)
      .sort((a, b) => (a.d < b.d ? -1 : 1));
    // Daily products (STAR) use daily stats with true 1D/1W deltas.
    const stats = p.daily ? rowStatsDaily(vals, range) : rowStats(vals, range);
    return { p, stats, out: OUTSTANDING[p.id] || null };
  });
  // Synthetic TOTAL (Treasury + TRACE) — always first per Harry's rule.
  const tot = PRODUCTS.find((p) => p.id === "total");
  const totOut = totalOutstanding(TOTAL_PARTS.map((p) => p.id));
  rows.unshift({
    p: tot,
    stats: rowStats(totalVals(data, metric), range),
    out: { amt: totOut.amt, asof: "mixed", src: `sum of component floats (${totOut.parts.length} products; agency-MBS float counted once)` },
  });
  // Synthetic STAR Total — sum of 8 top-level STAR categories (daily).
  const starTot = PRODUCTS.find((p) => p.id === "star-total");
  if (starTot) {
    const starVals = totalValsDaily(data, metric, STAR_TOTAL_PARTS);
    rows.push({
      p: starTot,
      stats: starVals ? rowStatsDaily(starVals, range) : null,
      out: null,
    });
  }
  return rows;
}
// Sum daily values across STAR parts (no month bucketing — daily granularity).
export function totalValsDaily(data, metric, parts = STAR_TOTAL_PARTS) {
  const isCount = metric === "adt" || metric === "trades";
  const byDate = new Map();
  for (const { p, par, tr } of data) {
    if (!parts.some((sp) => sp.id === p.id)) continue;
    const src = isCount ? tr : par;
    if (!src) continue;
    const vals = toMetric(p, src, metric);
    for (const { d, v } of vals) {
      const e = byDate.get(d);
      if (e) e.v += v;
      else byDate.set(d, { d, v });
    }
  }
  const rows = [...byDate.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  return rows.length ? rows : null;
}

function turnVal(r) { // annualized turnover % = ADV × 252 ÷ outstanding; ADV view only
  if (state.metric !== "adv" || !r.stats || !r.out || r.out.amt == null) return null;
  return (r.stats.cur.v * 252 / r.out.amt) * 100;
}

function sortRows(rows) {
  // Harry's standing rule: the combined TOTAL row is always first.
  const total = rows.filter((r) => r.p.synthetic);
  const rest = rows.filter((r) => !r.p.synthetic);
  if (!state.sortKey || state.sortKey === "label") return [...total, ...rest];
  const k = state.sortKey, dir = state.sortDir;
  const val = (r) => {
    if (!r.stats) return -Infinity;
    switch (k) {
      case "cur": return r.stats.cur.v;
      case "out": return r.out?.amt ?? -Infinity;
      case "turn": return turnVal(r) ?? -Infinity;
      case "d1": return r.stats.d1 ?? -Infinity;
      case "w1": return r.stats.w1 ?? -Infinity;
      case "m1": return r.stats.m1 ?? -Infinity;
      case "q1": return r.stats.q1 ?? -Infinity;
      case "y1": return r.stats.y1 ?? -Infinity;
      case "y3": return r.stats.y3 ?? -Infinity;
      case "d3": return r.stats.d3 ?? -Infinity;
      case "pct": return r.stats.pct;
      case "z": return r.stats.z ?? -Infinity;
      case "hi52": return r.stats.hi52 ?? -Infinity;
      case "lo52": return r.stats.lo52 ?? -Infinity;
      case "lo": return r.stats.lo; case "hi": return r.stats.hi; case "avg": return r.stats.avg;
      default: return -Infinity;
    }
  };
  return [...total, ...[...rest].sort((a, b) => (val(a) - val(b)) * dir)];
}

function renderTable() {
  const wrap = document.getElementById("trace-grid-wrap");
  if (!wrap) return;
  const m = metricById(state.metric);
  const isCount = state.metric === "adt" || state.metric === "trades";
  const unit = m.unit;
  const head = COLS.map((c) => {
    let title = c.title;
    if (c.key === "cur") title = metricColTitle();
    if (c.key === "rngpct") title = `Range %ile (${state.rows[0]?.stats?.winLabel || "3Y"})`;
    if (c.key === "rngz") title = `Range z (${state.rows[0]?.stats?.winLabel || "3Y"})`;
    if (c.key === "trend") title = `Trend (${state.rows[0]?.stats?.winLabel || "3Y"})`;
    if (c.key === "d3") title = `Δ ${state.rows[0]?.stats?.winLabel || "3Y"} avg %`;
    const arrow = state.sortKey === c.key ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
    const tip = c.tip ? ` title="${c.tip}"` : ` title="Sort by ${title}"`;
    return `<th data-sort="${c.key}" class="${c.num ? "num" : ""}"${tip}>${title}${arrow}</th>`;
  }).join("");
  const rows = sortRows(state.rows).map((r) => {
    const { p, stats: s } = r;
    const fmt = fmtCurFor(p);
    const cls = p.synthetic ? ` class="total-row"` : "";
    if (!s) return `<tr${cls}><td><b>${p.label}</b></td><td colspan="19" class="muted">no ${isCount ? "trade-count" : "par"} data</td></tr>`;
    const o = r.out;
    const outCell = o && o.amt != null
      ? `<td class="num" title="${o.src}${o.asof ? ` (as of ${o.asof})` : ""}">$${(o.amt / 1000).toFixed(1)}T</td>`
      : `<td class="num muted" title="${o ? o.src : "n/a"}">—</td>`;
    const tv = turnVal(r);
    const turnCell = tv == null
      ? `<td class="num muted"${isAdt ? ` title="Turnover is par-based (ADV view only)"` : ""}>—</td>`
      : `<td class="num" title="ADV × 252 ÷ outstanding (annualized)">${tv >= 100 ? tv.toFixed(0) : tv.toFixed(1)}%</td>`;
    return `<tr data-pid="${p.id}" title="Click to view ${p.label} chart"${cls}>` +
      `<td><b>${p.label}</b></td>` +
      `<td class="num">${fmt(s.cur.v)}</td>` +
      outCell + turnCell +
      `<td class="num"${heat(s.d1)}>${pct1(s.d1)}</td>` +
      `<td class="num"${heat(s.w1)}>${pct1(s.w1)}</td>` +
      `<td class="num"${heat(s.m1)}>${pct1(s.m1)}</td>` +
      `<td class="num"${heat(s.q1)}>${pct1(s.q1)}</td>` +
      `<td class="num"${heat(s.y1)}>${pct1(s.y1)}</td>` +
      `<td class="num"${heat(s.y3)}>${pct1(s.y3)}</td>` +
      `<td class="num"${heat(s.d3)}>${pct1(s.d3)}</td>` +
      `<td>${rangePlotDotted(s, "pct")}</td>` +
      `<td>${rangePlotDotted(s, "z")}</td>` +
      `<td class="num">${fmt(s.hi52)}</td>` +
      `<td class="num">${fmt(s.lo52)}</td>` +
      `<td class="num">${fmt(s.lo)}</td>` +
      `<td class="num">${fmt(s.hi)}</td>` +
      `<td class="num">${fmt(s.avg)}</td>` +
      `<td class="num">${s.pct.toFixed(0)}</td>` +
      `<td class="num">${s.z == null ? "—" : (s.z >= 0 ? "+" : "") + s.z.toFixed(2)}</td>` +
      `<td>${sparkline(s.win)}</td></tr>`;
  }).join("");
  wrap.querySelector("table.trace-grid tbody").innerHTML = rows;
  wrap.querySelectorAll("table.trace-grid th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = k === "label" ? 1 : -1; }
      renderTable();
    }));
  wrap.querySelectorAll("table.trace-grid tr[data-pid]").forEach((tr) =>
    tr.addEventListener("click", () => {
      setTraceChartProduct(tr.dataset.pid);
      document.getElementById("trace-view-chart")?.click();
      document.getElementById("trace-chart-wrap")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  const asofEl = wrap.querySelector("#trace-grid-asof");
  if (asofEl && state.asof) {
    const s0 = state.rows[0]?.stats;
    const winNote = s0 ? (s0.fellBack ? `stats over trailing 3Y (window <12 pts)` : `stats over ${s0.winLabel} window`) : "";
    asofEl.textContent =
      `as of ${mlabel(state.asof)} · ${m.label} (${unit}) · ${winNote} (● = now, ◆ = avg/50th pct) · ` +
      `Treasury history from Feb 2023 · 1D/1W n/a on monthly rows · ` +
      `On-the-run + Off-the-run = coupons + TIPS only (excl. bills/FRNs) · ` +
      `${isCount ? "ADT/TRADES sum products with trade-count data" : ""} · ${OUTSTANDING_NOTE}`;
  }
}

export function renderTraceGrid(corpData) {
  const wrap = document.getElementById("trace-grid-wrap");
  if (!wrap || wrap.dataset.init) return;
  wrap.dataset.init = "1";
  // Store corp panel data for most-active CUSIP rows (top-10 by volume).
  if (corpData) state.corpData = corpData;
  const reqId = ++state.reqId;
  const metricBtns = METRICS.map((mt) =>
    `<button data-m="${mt.id}" class="${mt.id === state.metric ? "on" : ""}" title="${mt.title}">${mt.label}</button>`).join("");
  const rangeBtns = RANGES.map((r) =>
    `<button data-r="${r.id}" class="${r.id === state.range.id ? "on" : ""}">${r.label}</button>`).join("");
  wrap.innerHTML = `
    <div class="trace-controls">
      <span class="seg" id="trace-grid-metric">${metricBtns}</span>
      <span class="seg" id="trace-grid-range">${rangeBtns}</span>
      <span id="trace-grid-custom" class="muted" style="display:none">
        <input type="date" id="trace-custom-start" aria-label="Start date"> →
        <input type="date" id="trace-custom-end" aria-label="End date">
        <button id="trace-custom-apply" class="mini-btn">Apply</button>
      </span>
      <span class="muted" id="trace-grid-asof">Loading…</span>
    </div>
    <table class="trace-grid"><thead><tr>${
      COLS.map((c) => `<th data-sort="${c.key}" class="${c.num ? "num" : ""}">${c.key === "cur" ? metricColTitle() : c.title}</th>`).join("")
    }</tr></thead><tbody><tr><td colspan="20" class="muted">Loading TRACE history…</td></tr></tbody></table>`;
  wrap.querySelectorAll("#trace-grid-metric button").forEach((b) =>
    b.addEventListener("click", async () => {
      if (state.metric === b.dataset.m) return;
      state.metric = b.dataset.m;
      wrap.querySelectorAll("#trace-grid-metric button").forEach((x) => x.classList.toggle("on", x === b));
      await refresh();
    }));
  const customBox = wrap.querySelector("#trace-grid-custom");
  wrap.querySelectorAll("#trace-grid-range button").forEach((b) =>
    b.addEventListener("click", async () => {
      const r = rangeById(b.dataset.r);
      state.range = r;
      wrap.querySelectorAll("#trace-grid-range button").forEach((x) => x.classList.toggle("on", x === b));
      customBox.style.display = r.id === "custom" ? "" : "none";
      if (r.id !== "custom") await refresh();
    }));
  wrap.querySelector("#trace-custom-apply").addEventListener("click", async () => {
    const s = wrap.querySelector("#trace-custom-start").value;
    const e = wrap.querySelector("#trace-custom-end").value;
    if (!s || !e || s > e) return;
    state.range = { id: "custom", label: "Custom", months: null, start: s, end: e };
    await refresh();
  });
  loadData().then((data) => {
    if (reqId !== state.reqId) return;
    wrap.dataset.raw = "1";
    wrap._rawData = data;
    const advRows = buildRows(data, "adv", state.range);
    const mo = advRows.map((r) => r.stats?.asof).filter(Boolean).sort().pop();
    state.asof = mo || null;
    refresh();
  }).catch((err) => {
    if (reqId !== state.reqId) return;
    wrap.querySelector("tbody").innerHTML =
      `<tr><td colspan="20" class="muted">Failed to load grid — ${err.message}</td></tr>`;
  });

  async function refresh() {
    const data = wrap._rawData;
    if (!data) return;
    state.rows = buildRows(data, state.metric, state.range.id === "custom"
      ? state.range : state.range);
    renderTable();
  }
  wrap._refresh = refresh;
}
