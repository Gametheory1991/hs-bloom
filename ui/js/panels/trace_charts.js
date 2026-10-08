// TRACE volume charts with overlay capability.
// Inline uPlot chart (not the modal) with product/metric/range/overlay selectors.
// Mounted by renderFinra() into #trace-charts-root.
import { getSeries, getRecessions } from "../api.js";
import { tradingX, bandsToIndices } from "../tradingx.js";
import { toMetric, METRICS, RANGES, rangeById, metricById, capTotalVals, VENUES, venueById, UST_VENUES, ustVenueById } from "./trace_grid.js";

// TRACE monthly products. Trade counts are published for all 10 products.
// NOTE: /api/series takes bare ids (no cycle: prefix) — the backend prepends it.
const PRODUCTS = [
  { id: "total", label: "TOTAL (Treasury + TRACE)", synthetic: true },
  { id: "ust", label: "Treasury Total", par: "trace-ust-par", trades: "trace-ust-trades", monthly: false, ust: true },
  { id: "ust-bills", label: "Treasury — Bills", par: "trace-ust-bills-par", trades: "trace-ust-bills-trades", monthly: false, ust: true },
  { id: "ust-coupons", label: "Treasury — Nom Coupons", par: "trace-ust-coupons-par", trades: "trace-ust-coupons-trades", monthly: false, ust: true },
  { id: "ust-tips", label: "Treasury — TIPS", par: "trace-ust-tips-par", trades: "trace-ust-tips-trades", monthly: false, ust: true },
  { id: "ust-frns", label: "Treasury — FRNs", par: "trace-ust-frns-par", trades: "trace-ust-frns-trades", monthly: false, ust: true },
  { id: "ust-onrun", label: "Treasury On-the-Run", par: "trace-ust-onrun-par", trades: "trace-ust-onrun-trades", monthly: false },
  { id: "ust-offrun", label: "Treasury Off-the-Run", par: "trace-ust-offrun-par", trades: "trace-ust-offrun-trades", monthly: false },
  // Treasury coupon maturity buckets (venue splits via the Treasury venue selector).
  { id: "ust-c-le2y", label: "Treasury — Coupons ≤2Y", par: "trace-ust-coupons-le2y-par", trades: "trace-ust-coupons-le2y-trades", monthly: false, ust: true },
  { id: "ust-c-2y3y", label: "Treasury — Coupons 2–3Y", par: "trace-ust-coupons-2y3y-par", trades: "trace-ust-coupons-2y3y-trades", monthly: false, ust: true },
  { id: "ust-c-3y5y", label: "Treasury — Coupons 3–5Y", par: "trace-ust-coupons-3y5y-par", trades: "trace-ust-coupons-3y5y-trades", monthly: false, ust: true },
  { id: "ust-c-5y7y", label: "Treasury — Coupons 5–7Y", par: "trace-ust-coupons-5y7y-par", trades: "trace-ust-coupons-5y7y-trades", monthly: false, ust: true },
  { id: "ust-c-7y10y", label: "Treasury — Coupons 7–10Y", par: "trace-ust-coupons-7y10y-par", trades: "trace-ust-coupons-7y10y-trades", monthly: false, ust: true },
  { id: "ust-c-10y20y", label: "Treasury — Coupons 10–20Y", par: "trace-ust-coupons-10y20y-par", trades: "trace-ust-coupons-10y20y-trades", monthly: false, ust: true },
  { id: "ust-c-gt20y", label: "Treasury — Coupons >20Y", par: "trace-ust-coupons-gt20y-par", trades: "trace-ust-coupons-gt20y-trades", monthly: false, ust: true },
  { id: "ust-t-le5y", label: "Treasury — TIPS ≤5Y", par: "trace-ust-tips-le5y-par", trades: "trace-ust-tips-le5y-trades", monthly: false, ust: true },
  { id: "ust-t-5y10y", label: "Treasury — TIPS 5–10Y", par: "trace-ust-tips-5y10y-par", trades: "trace-ust-tips-5y10y-trades", monthly: false, ust: true },
  { id: "ust-t-gt10y", label: "Treasury — TIPS >10Y", par: "trace-ust-tips-gt10y-par", trades: "trace-ust-tips-gt10y-trades", monthly: false, ust: true },
  // On-the-run VWAP by coupon bucket (price per $100 par; daily files only).
  { id: "ust-v-le2y", label: "Treasury VWAP — Coupons ≤2Y", par: "trace-ust-coupons-le2y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "ust-v-2y3y", label: "Treasury VWAP — Coupons 2–3Y", par: "trace-ust-coupons-2y3y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "ust-v-3y5y", label: "Treasury VWAP — Coupons 3–5Y", par: "trace-ust-coupons-3y5y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "ust-v-5y7y", label: "Treasury VWAP — Coupons 5–7Y", par: "trace-ust-coupons-5y7y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "ust-v-7y10y", label: "Treasury VWAP — Coupons 7–10Y", par: "trace-ust-coupons-7y10y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "ust-v-10y20y", label: "Treasury VWAP — Coupons 10–20Y", par: "trace-ust-coupons-10y20y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "ust-v-gt20y", label: "Treasury VWAP — Coupons >20Y", par: "trace-ust-coupons-gt20y-vwap", trades: null, unit: "px", daily: true, vwap: true },
  { id: "tba", label: "TBA", par: "trace-tba-par", trades: "trace-tba-trades", monthly: true },
  { id: "corp", label: "Corporate", par: "trace-corp-par", trades: "trace-corp-trades", monthly: true },
  { id: "mbs", label: "MBS (Spec Pools)", par: "trace-mbs-par", trades: "trace-mbs-trades", monthly: true },
  { id: "cmo", label: "CMO", par: "trace-cmo-par", trades: "trace-cmo-trades", monthly: true },
  { id: "absx", label: "ABSX (CLO/CMBS)", par: "trace-absx-par", trades: "trace-absx-trades", monthly: true },
  { id: "agcy", label: "Agency", par: "trace-agcy-par", trades: "trace-agcy-trades", monthly: true },
  { id: "conv", label: "Convertibles", par: "trace-conv-par", trades: "trace-conv-trades", monthly: true },
  { id: "abs", label: "ABS", par: "trace-abs-par", trades: "trace-abs-trades", monthly: true },
  { id: "eln", label: "ELN", par: "trace-eln-par", trades: "trace-eln-trades", monthly: true },
  { id: "chrc", label: "Church Plans", par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true },
  // STAR: FINRA IDS Structured Trading Activity Reports (daily)
  { id: "star-total", label: "STAR — Total", synthetic: true, star: true },
  { id: "star-tba", label: "STAR — TBA Total", par: "star-tba-par", trades: "star-tba-trades", monthly: false, daily: true },
  { id: "star-tba-umbs", label: "STAR — TBA UMBS", par: "star-tba-umbs-par", trades: null, monthly: false, daily: true },
  { id: "star-tba-fnma", label: "STAR — TBA FNMA", par: "star-tba-fnma-par", trades: null, monthly: false, daily: true },
  { id: "star-tba-fhlmc", label: "STAR — TBA FHLMC", par: "star-tba-fhlmc-par", trades: null, monthly: false, daily: true },
  { id: "star-tba-gnma", label: "STAR — TBA GNMA", par: "star-tba-gnma-par", trades: null, monthly: false, daily: true },
  { id: "star-spec", label: "STAR — Specified Pools", par: "star-spec-par", trades: "star-spec-trades", monthly: false, daily: true },
  { id: "star-agcmo", label: "STAR — Agency CMO", par: "star-agcmo-par", trades: "star-agcmo-trades", monthly: false, daily: true },
  { id: "star-nagcmo", label: "STAR — Non-Agency CMO", par: "star-nagcmo-par", trades: "star-nagcmo-trades", monthly: false, daily: true },
  { id: "star-nagcmo-ig", label: "STAR — Non-Agency CMO IG", par: "star-nagcmo-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-nagcmo-hy", label: "STAR — Non-Agency CMO HY", par: "star-nagcmo-nonig-par", trades: null, monthly: false, daily: true },
  { id: "star-nagcmbs", label: "STAR — Non-Agency CMBS", par: "star-nagcmbs-par", trades: "star-nagcmbs-trades", monthly: false, daily: true },
  { id: "star-nagcmbs-ig", label: "STAR — Non-Agency CMBS IG", par: "star-nagcmbs-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-nagcmbs-hy", label: "STAR — Non-Agency CMBS HY", par: "star-nagcmbs-nonig-par", trades: null, monthly: false, daily: true },
  { id: "star-agcmbs", label: "STAR — Agency CMBS", par: "star-agcmbs-par", trades: "star-agcmbs-trades", monthly: false, daily: true },
  { id: "star-abs", label: "STAR — ABS Total", par: "star-abs-par", trades: "star-abs-trades", monthly: false, daily: true },
  { id: "star-abs-ig", label: "STAR — ABS IG", par: "star-abs-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-abs-hy", label: "STAR — ABS HY", par: "star-abs-nonig-par", trades: null, monthly: false, daily: true },
  { id: "star-clo", label: "STAR — CLO Total", par: "star-clo-par", trades: "star-clo-trades", monthly: false, daily: true },
  { id: "star-clo-ig", label: "STAR — CLO IG", par: "star-clo-ig-par", trades: null, monthly: false, daily: true },
  { id: "star-clo-hy", label: "STAR — CLO HY", par: "star-clo-nonig-par", trades: null, monthly: false, daily: true },
  { id: "si-total", label: "Short Interest — Total", par: "finra-short-total", trades: null, unit: "shares" },
  { id: "si-msft", label: "Short Interest — MSFT", par: "short-MSFT", trades: null, unit: "shares" },
  { id: "si-nvda", label: "Short Interest — NVDA", par: "short-NVDA", trades: null, unit: "shares" },
  { id: "si-aapl", label: "Short Interest — AAPL", par: "short-AAPL", trades: null, unit: "shares" },
  { id: "si-amzn", label: "Short Interest — AMZN", par: "short-AMZN", trades: null, unit: "shares" },
  { id: "si-googl", label: "Short Interest — GOOGL", par: "short-GOOGL", trades: null, unit: "shares" },
  { id: "si-meta", label: "Short Interest — META", par: "short-META", trades: null, unit: "shares" },
  // Market breadth (FINRA, daily)
  { id: "br-corp-all-spr", label: "Breadth — Corp All A/D Spread", par: "finra-breadth-corp-all-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-corp-ig-spr", label: "Breadth — Corp IG A/D Spread", par: "finra-breadth-corp-ig-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-corp-hy-spr", label: "Breadth — Corp HY A/D Spread", par: "finra-breadth-corp-hy-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-agcy-all-spr", label: "Breadth — Agency All A/D Spread", par: "finra-breadth-agency-all-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-144a-all-spr", label: "Breadth — 144A All A/D Spread", par: "finra-breadth-144a-all-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-144a-ig-spr", label: "Breadth — 144A IG A/D Spread", par: "finra-breadth-144a-ig-adspread", trades: null, unit: "ct", daily: true },
  { id: "br-144a-hy-spr", label: "Breadth — 144A HY A/D Spread", par: "finra-breadth-144a-hy-adspread", trades: null, unit: "ct", daily: true },
  // Market sentiment (FINRA, daily)
  { id: "se-corp-all-flow", label: "Sentiment — Corp All Net Flow", par: "finra-sent-corp-all-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-corp-ig-flow", label: "Sentiment — Corp IG Net Flow", par: "finra-sent-corp-ig-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-corp-hy-flow", label: "Sentiment — Corp HY Net Flow", par: "finra-sent-corp-hy-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-agcy-all-flow", label: "Sentiment — Agency All Net Flow", par: "finra-sent-agency-all-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-144a-all-flow", label: "Sentiment — 144A All Net Flow", par: "finra-sent-144a-all-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-144a-ig-flow", label: "Sentiment — 144A IG Net Flow", par: "finra-sent-144a-ig-netflow", trades: null, unit: "$M", daily: true },
  { id: "se-144a-hy-flow", label: "Sentiment — 144A HY Net Flow", par: "finra-sent-144a-hy-netflow", trades: null, unit: "$M", daily: true },
  // Breadth counts & dealer buy/sell legs — displayed by the TRACE Detail
  // breadth/sentiment card; added here so they stay chartable from the main tab.
  { id: "br-corp-all-hi52", label: "Breadth — Corp All 52wk Highs", par: "finra-breadth-corp-all-hi52", trades: null, unit: "ct", daily: true },
  { id: "br-corp-all-lo52", label: "Breadth — Corp All 52wk Lows", par: "finra-breadth-corp-all-lo52", trades: null, unit: "ct", daily: true },
  { id: "br-corp-all-dvol", label: "Breadth — Corp All $ Volume", par: "finra-breadth-corp-all-dvol", trades: null, unit: "$M", daily: true },
  { id: "se-corp-all-buy", label: "Sentiment — Corp All Dealer Buys", par: "finra-sent-corp-all-dbuy-vol", trades: null, unit: "$M", daily: true },
  { id: "se-corp-all-sell", label: "Sentiment — Corp All Dealer Sells", par: "finra-sent-corp-all-dsell-vol", trades: null, unit: "$M", daily: true },
  // ICE BofA index OAS & YTW (FRED, ~3Y history) — the series behind the
  // TRACE Detail OAS tables. OAS is stored as a fraction; scale ×100 → bps.
  { id: "oas-aaa", label: "OAS — AAA", par: "aaa-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-aa", label: "OAS — AA", par: "aa-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-a", label: "OAS — A", par: "a-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-bbb", label: "OAS — BBB", par: "bbb-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-bb", label: "OAS — BB", par: "bb-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-b", label: "OAS — B", par: "b-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-ccc", label: "OAS — CCC & lower", par: "ccc-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-13y", label: "OAS — 1-3Y", par: "corp-13y-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-35y", label: "OAS — 3-5Y", par: "corp-35y-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-57y", label: "OAS — 5-7Y", par: "corp-57y-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-710y", label: "OAS — 7-10Y", par: "corp-710y-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-1015y", label: "OAS — 10-15Y", par: "corp-1015y-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "oas-15py", label: "OAS — 15Y+", par: "corp-15py-oas", trades: null, unit: "bp", daily: true, scale: 100 },
  { id: "ytw-aaa", label: "YTW — AAA", par: "aaa-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-aa", label: "YTW — AA", par: "aa-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-a", label: "YTW — A", par: "a-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-bbb", label: "YTW — BBB", par: "bbb-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-bb", label: "YTW — BB", par: "bb-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-b", label: "YTW — B", par: "b-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-ccc", label: "YTW — CCC & lower", par: "ccc-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-13y", label: "YTW — 1-3Y", par: "corp-13y-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-35y", label: "YTW — 3-5Y", par: "corp-35y-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-57y", label: "YTW — 5-7Y", par: "corp-57y-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-710y", label: "YTW — 7-10Y", par: "corp-710y-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-1015y", label: "YTW — 10-15Y", par: "corp-1015y-yield", trades: null, unit: "%", daily: true },
  { id: "ytw-15py", label: "YTW — 15Y+", par: "corp-15py-yield", trades: null, unit: "%", daily: true },
  // Short interest top tickers (Reg SHO daily; ADT/TRADES → short ratio)
  { id: "sit-msft", label: "Short — MSFT", par: "regsho-top-MSFT-shortvol", trades: "regsho-top-MSFT-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-nvda", label: "Short — NVDA", par: "regsho-top-NVDA-shortvol", trades: "regsho-top-NVDA-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-aapl", label: "Short — AAPL", par: "regsho-top-AAPL-shortvol", trades: "regsho-top-AAPL-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-amzn", label: "Short — AMZN", par: "regsho-top-AMZN-shortvol", trades: "regsho-top-AMZN-totalvol", unit: "sh", daily: true, siTop: true },
  { id: "sit-tsla", label: "Short — TSLA", par: "regsho-top-TSLA-shortvol", trades: "regsho-top-TSLA-totalvol", unit: "sh", daily: true, siTop: true },
  // Capped volume report (FINRA, monthly). Total capped par by grade;
  // ADT/TRADES shows avg capped trade size ($000s, nodiv). 144A grades
  // publish total par only.
  { id: "cap-total", label: "Capped — Total", synthetic: true, capped: true },
  { id: "cap-ig", label: "Capped — Investment Grade", par: "finra-cap-ig-total", trades: "finra-cap-ig-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-hy", label: "Capped — High Yield", par: "finra-cap-hy-total", trades: "finra-cap-hy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-agcy", label: "Capped — Agency", par: "finra-cap-agcy-total", trades: "finra-cap-agcy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-144a-ig", label: "Capped — 144A IG", par: "finra-cap-144a-ig-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
  { id: "cap-144a-hy", label: "Capped — 144A HY", par: "finra-cap-144a-hy-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
];

// Components summed into the synthetic TOTAL chart product (same set as the grid).
// Monthly TRACE rows honor the chart's venue selector (ATS/Interdealer/Customer splits).
const TOTAL_PARTS = [
  { par: "trace-ust-par", trades: "trace-ust-trades", monthly: false, ust: true },
  { par: "trace-tba-par", trades: "trace-tba-trades", monthly: true },
  { par: "trace-corp-par", trades: "trace-corp-trades", monthly: true },
  { par: "trace-eln-par", trades: "trace-eln-trades", monthly: true },
  { par: "trace-conv-par", trades: "trace-conv-trades", monthly: true },
  { par: "trace-agcy-par", trades: "trace-agcy-trades", monthly: true },
  { par: "trace-abs-par", trades: "trace-abs-trades", monthly: true },
  { par: "trace-absx-par", trades: "trace-absx-trades", monthly: true },
  { par: "trace-cmo-par", trades: "trace-cmo-trades", monthly: true },
  { par: "trace-mbs-par", trades: "trace-mbs-trades", monthly: true },
  { par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true },
];

// STAR Total components: 8 top-level categories (daily). Issuer/grade
// breakdowns excluded to avoid double-counting.
const STAR_TOTAL_PARTS = [
  { par: "star-tba-par", trades: "star-tba-trades", monthly: false, daily: true },
  { par: "star-spec-par", trades: "star-spec-trades", monthly: false, daily: true },
  { par: "star-agcmo-par", trades: "star-agcmo-trades", monthly: false, daily: true },
  { par: "star-nagcmo-par", trades: "star-nagcmo-trades", monthly: false, daily: true },
  { par: "star-nagcmbs-par", trades: "star-nagcmbs-trades", monthly: false, daily: true },
  { par: "star-agcmbs-par", trades: "star-agcmbs-trades", monthly: false, daily: true },
  { par: "star-abs-par", trades: "star-abs-trades", monthly: false, daily: true },
  { par: "star-clo-par", trades: "star-clo-trades", monthly: false, daily: true },
];

// Synthetic TOTAL series: monthly values summed over Treasury + all 10
// TRACE products, by calendar month, normalized to the selected metric.
// Honors the venue selector (same-venue components).
async function totalSeries(metric) {
  const venue = venueById(state.venue);
  const uvenue = ustVenueById(state.ustVenue);
  const isCount = metric === "adt" || metric === "trades";
  const all = (await Promise.all(TOTAL_PARTS.map(async (c) => {
    let sid = isCount ? c.trades : c.par;
    if (!sid) return null;
    if (venue.id !== "total" && c.monthly) sid = sid + venue.suffix;
    if (uvenue.id !== "total" && c.ust) sid = sid + uvenue.suffix;
    const s = await getSeries(sid, "max");
    return toMetric(c, s.points, metric);
  }))).filter(Boolean);
  const sums = new Map(); // "YYYY-MM" -> {d, v}
  for (const vals of all)
    for (const { d, v } of vals) {
      const key = d.slice(0, 7);
      const e = sums.get(key);
      if (e) { e.v += v; if (d > e.d) e.d = d; }
      else sums.set(key, { d, v });
    }
  const rows = [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  const mu = metricById(metric);
  const vNote = venue.id !== "total" ? ` (${venue.label} venue)` : "";
  const uvNote = uvenue.id !== "total" ? ` (UST ${uvenue.label})` : "";
  return {
    id: "total", unit: mu.unit,
    name: `TOTAL ${mu.label} — Treasury + TRACE${vNote}${uvNote} (${mu.unit})`,
    points: rows.map(({ d, v }) => [d, v]),
  };
}

// Synthetic STAR Total series: daily values summed over the 8 top-level
// STAR categories, normalized to the selected metric.
async function starTotalSeries(metric) {
  const isCount = metric === "adt" || metric === "trades";
  const all = (await Promise.all(STAR_TOTAL_PARTS.map(async (c) => {
    const sid = isCount ? c.trades : c.par;
    if (!sid) return null;
    const s = await getSeries(sid, "max");
    return toMetric(c, s.points, metric);
  }))).filter(Boolean);
  const sums = new Map(); // "YYYY-MM-DD" -> {d, v} (daily granularity)
  for (const vals of all)
    for (const { d, v } of vals) {
      const e = sums.get(d);
      if (e) e.v += v;
      else sums.set(d, { d, v });
    }
  const rows = [...sums.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  const mu = metricById(metric);
  return {
    id: "star-total", unit: mu.unit,
    name: `STAR Total ${mu.label} — structured products (${mu.unit})`,
    points: rows.map(({ d, v }) => [d, v]),
  };
}

// Capped Total components: the 5 grades (monthly). Not part of the main
// TOTAL — capped volume is large-trade activity already in TRACE volumes.
const CAP_TOTAL_PARTS = [
  { id: "cap-ig", par: "finra-cap-ig-total", trades: "finra-cap-ig-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-hy", par: "finra-cap-hy-total", trades: "finra-cap-hy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-agcy", par: "finra-cap-agcy-total", trades: "finra-cap-agcy-avgsize", monthly: true, capped: true, parScale: 1e9, nodivTrades: true },
  { id: "cap-144a-ig", par: "finra-cap-144a-ig-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
  { id: "cap-144a-hy", par: "finra-cap-144a-hy-total", trades: null, monthly: true, capped: true, parScale: 1e9 },
];

// Synthetic Capped Total series. ADV/PAR: sum of grades' total par by month.
// ADT/TRADES: par-weighted avg capped trade size (reuses capTotalVals from
// the grid so chart and grid always agree).
async function capTotalSeries(metric) {
  const isCount = metric === "adt" || metric === "trades";
  const data = await Promise.all(CAP_TOTAL_PARTS.map(async (c) => {
    const [par, tr] = await Promise.all([
      getSeries(c.par, "max"),
      c.trades ? getSeries(c.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    return { p: c, par: par.points, tr: tr ? tr.points : null };
  }));
  const rows = capTotalVals(data, metric, CAP_TOTAL_PARTS);
  if (isCount) {
    return {
      id: "cap-total", unit: "$000s",
      name: "Capped — Total — Avg capped trade size ($000s, par-weighted)",
      points: (rows || []).map(({ d, v }) => [d, v]),
    };
  }
  const mu = metricById(metric);
  return {
    id: "cap-total", unit: mu.unit,
    name: `Capped — Total ${mu.label} — capped par (${mu.unit})`,
    points: (rows || []).map(({ d, v }) => [d, v]),
  };
}

const OVERLAYS = [
  { id: "", label: "No overlay" },
  { id: "us10y", label: "US 10Y Yield (FRED)" },
  { id: "us-mortgage-30y", label: "30Y Mortgage Rate (FRED)" },
  { id: "ig-oas", label: "IG OAS" },
  { id: "hy-oas", label: "HY OAS" },
  { id: "tlt", label: "TLT (20Y+ Treasury ETF)" },
  { id: "lqd", label: "LQD (IG ETF)" },
  { id: "hyg", label: "HYG (HY ETF)" },
  { id: "mbb-us", label: "MBB (MBS ETF)" },
  { id: "vix", label: "VIX" },
  { id: "vvix", label: "VVIX" },
];

const state = { product: "total", metric: "adv", venue: "total", ustVenue: "total", range: "3y", customStart: null, customEnd: null, overlay: "", overlayType: "line", overlayColor: "#7fc9b5", plot: null, reqId: 0 };
let recessionsPromise = null;

function loadRecessions() {
  recessionsPromise ??= getRecessions()
    .then((r) => r.bands.map(([a, b]) => [Date.parse(a) / 1000, Date.parse(b) / 1000]))
    .catch(() => []);
  return recessionsPromise;
}

// Merge two [date, value] series onto one x-axis (union of dates, null-filled).
function mergeSeries(main, overlay) {
  const byDate = new Map();
  for (const [d, v] of main.points) byDate.set(d, [v, null]);
  for (const [d, v] of overlay.points) {
    const cur = byDate.get(d) ?? [null, null];
    cur[1] = v;
    byDate.set(d, cur);
  }
  const dates = [...byDate.keys()].sort();
  return [
    dates,
    dates.map((d) => byDate.get(d)[0]),
    dates.map((d) => byDate.get(d)[1]),
  ];
}

function bandsHook(bands) {
  return (u) => {
    const ctx = u.ctx;
    ctx.save();
    // Warm-dark theme: subtle dark recession shading (Harry 2026-10-07: no yellow background).
    ctx.fillStyle = "rgba(0, 0, 0, 0.25)";
    for (const [a, b] of bands) {
      const x0 = Math.max(u.valToPos(a, "x", true), u.bbox.left);
      const x1 = Math.min(u.valToPos(b, "x", true), u.bbox.left + u.bbox.width);
      if (x1 > x0) ctx.fillRect(x0, u.bbox.top, x1 - x0, u.bbox.height);
    }
    ctx.restore();
  };
}

function destroyPlot() {
  if (state.plot) {
    state.plot.destroy();
    state.plot = null;
  }
}

// Apply the venue selectors: TRACE monthly products take the monthly
// venue (ATS/Interdealer/Customer splits); UST rows take the Treasury
// venue (ATS&Interdealer / Dealer-to-Customer splits). Treasury/STAR/
// capped/SI rows are otherwise venue-less.
function venueSeriesId(sid, p) {
  const v = venueById(state.venue);
  if (v.id !== "total" && p.monthly && !p.star && !p.capped) return sid + v.suffix;
  const uv = ustVenueById(state.ustVenue);
  if (uv.id !== "total" && p.ust && !p.vwap) return sid + uv.suffix;
  return sid;
}

function currentSeriesId() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  const isCount = state.metric === "adt" || state.metric === "trades";
  return venueSeriesId(isCount && p.trades ? p.trades : p.par, p);
}

function currentTitle() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  const v = venueById(state.venue);
  const vNote = v.id !== "total" && p.monthly && !p.star && !p.capped ? ` (${v.label} venue)` : "";
  const uv = ustVenueById(state.ustVenue);
  const uvNote = uv.id !== "total" && p.ust && !p.vwap ? ` (UST ${uv.label})` : "";
  if (p.raw) return `${p.label} — short shares (biweekly)`;
  if (p.capped && (state.metric === "adt" || state.metric === "trades"))
    return `${p.label} — Avg capped trade size ($000s)`;
  const mu = metricById(state.metric);
  return `${p.label}${vNote}${uvNote} — ${mu.label} (${mu.unit})`;
}

// Filter [d, v] points to the selected range (client-side; we fetch "max").
function filterRange(points) {
  if (state.range === "custom" && state.customStart && state.customEnd)
    return points.filter(([d]) => d >= state.customStart && d <= state.customEnd);
  if (state.range === "max") return points;
  const r = rangeById(state.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-r.months);
}

async function loadMain() {
  const p = PRODUCTS.find((x) => x.id === state.product);
  if (p.id === "star-total") {
    const s = await starTotalSeries(state.metric);
    return { ...s, points: filterRange(s.points) };
  }
  if (p.id === "cap-total") {
    const s = await capTotalSeries(state.metric);
    return { ...s, points: filterRange(s.points) };
  }
  if (p.synthetic) {
    const s = await totalSeries(state.metric);
    return { ...s, points: filterRange(s.points) };
  }
  if (p.raw) { // short interest: biweekly levels, metric toggle n/a
    const s = await getSeries(p.par, "max");
    return { id: p.id, unit: "shares", name: `${p.label} — short shares`, points: filterRange(s.points) };
  }
  if (p.siTop) {
    // Short interest top tickers: ADV/PAR → short volume; ADT/TRADES → short ratio.
    const isCount = state.metric === "adt" || state.metric === "trades";
    const [sv, tv] = await Promise.all([
      getSeries(p.par, "max"),
      p.trades ? getSeries(p.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    let pts = sv.points;
    let unit = "sh", name = `${p.label} — short volume (sh)`;
    if (isCount && tv) {
      const tvByDate = new Map(tv.points.map(([d, v]) => [d, v]));
      pts = sv.points.map(([d, v]) => {
        const t = tvByDate.get(d);
        return t ? [d, v / t] : null;
      }).filter(Boolean);
      unit = "ratio"; name = `${p.label} — short ratio`;
    }
    return { id: p.id, unit, name, points: filterRange(pts) };
  }
  const s = await getSeries(currentSeriesId(), "max");
  const scale = p.scale || 1;
  const vals = toMetric(p, s.points, state.metric).map(({ d, v }) => [d, v * scale]);
  const mu = metricById(state.metric);
  const name = p.unit === "px"
    ? `${p.label} — VWAP ($ per $100 par)`
    : p.unit === "bp"
      ? `${p.label} — OAS (bps)`
      : p.unit === "%"
        ? `${p.label} — YTW (%)`
        : p.unit
          ? `${p.label} — ${mu.label} (${p.unit})`
          : (p.capped && (state.metric === "adt" || state.metric === "trades"))
            ? `${p.label} — Avg capped trade size ($000s)`
            : `${p.label} — ${mu.label} (${mu.unit})`;
  return { id: p.id, unit: mu.unit, name, points: filterRange(vals) };
}

async function drawChart() {
  const reqId = ++state.reqId;
  const chartDiv = document.getElementById("trace-chart");
  const statusDiv = document.getElementById("trace-chart-status");
  if (!chartDiv) return;
  const p = PRODUCTS.find((x) => x.id === state.product);
  statusDiv.textContent = "Loading…";
  try {
    const [series, secondRaw, bands] = await Promise.all([
      loadMain(),
      state.overlay ? getSeries(state.overlay, "max") : Promise.resolve(null),
      loadRecessions(),
    ]);
    if (reqId !== state.reqId) return; // superseded
    const second = secondRaw ? { ...secondRaw, points: filterRange(secondRaw.points) } : null;
    destroyPlot();
    chartDiv.innerHTML = "";
    const axisStyle = { stroke: "#a89a83", grid: { stroke: "#38312a" } };
    const opts = {
      width: Math.max(300, chartDiv.clientWidth || 760),
      // Mobile: shorter chart on narrow viewports (Harry 2026-10-07).
      height: (chartDiv.clientWidth || 760) < 640 ? 320 : 500,
      scales: { x: { time: false } }, // tradingX uses ordinal x [0..N], not timestamps
      // Warm-dark theme: no area fill (Harry 2026-10-07: no yellow background).
      series: [{}, { label: series.name ?? currentTitle(), stroke: "#e8c96a", width: 1.5, spanGaps: true }],
      axes: [axisStyle, { ...axisStyle }],
      hooks: { drawClear: [bandsHook(bands)] },
    };
    const rangeLbl = state.range === "custom"
      ? `${state.customStart}→${state.customEnd}` : (rangeById(state.range)?.label || state.range);
    // Monthly TRACE data: YYYY-MM labels; daily data: MM/DD.
    const xfmt = p.monthly === false ? undefined : (d) => d.slice(0, 7);
    let data;
    if (second) {
      // Harry 2026-10-07: overlay can be line or bar, with user-chosen color.
      const ovSeries = { label: second.name, stroke: state.overlayColor, width: 1.2, scale: "y2", spanGaps: true };
      if (state.overlayType === "bar") {
        ovSeries.paths = uPlot.paths.bars({ size: [0.6, 100] });
        ovSeries.points = { show: false };
        ovSeries.fill = state.overlayColor + "80"; // 50% opacity for bars
      }
      opts.series.push(ovSeries);
      opts.axes.push({ ...axisStyle, scale: "y2", side: 1, grid: { show: false } });
      const merged = mergeSeries(series, second);
      const tx = tradingX(merged[0], xfmt);
      data = [tx.x, merged[1], merged[2]];
      opts.axes[0] = { ...axisStyle, values: tx.values };
      opts.hooks = { drawClear: [bandsHook(bandsToIndices(bands, merged[0]))] };
      statusDiv.textContent = `${series.points.length} pts (${rangeLbl}) · overlay: ${second.name} (${second.points.length} pts)`;
    } else {
      const dates = series.points.map(([d]) => d);
      const tx = tradingX(dates, xfmt);
      data = [tx.x, series.points.map(([, v]) => v)];
      opts.axes[0] = { ...axisStyle, values: tx.values };
      opts.hooks = { drawClear: [bandsHook(bandsToIndices(bands, dates))] };
      const note = p.synthetic
        ? `${series.points.length} monthly ${metricById(state.metric).label} points (${rangeLbl}) — Treasury + TRACE products${(state.metric === "adt" || state.metric === "trades") ? " with trade-count data" : ""}`
        : p.raw
          ? `${series.points.length} biweekly settlement points (${rangeLbl}, shares)`
          : `${series.points.length} monthly points (${rangeLbl})`;
      statusDiv.textContent = note;
    }
    state.plot = new uPlot(opts, data, chartDiv);
  } catch (err) {
    if (reqId !== state.reqId) return;
    destroyPlot();
    chartDiv.innerHTML = "";
    statusDiv.textContent = `Failed to load — ${err.message}`;
  }
}

function syncControls() {
  const prodSel = document.getElementById("trace-prod");
  const ovSel = document.getElementById("trace-overlay");
  if (prodSel) prodSel.value = state.product;
  if (ovSel) ovSel.value = state.overlay;
  const ovTypeSel = document.getElementById("trace-overlay-type");
  if (ovTypeSel) ovTypeSel.value = state.overlayType;
  const ovColor = document.getElementById("trace-overlay-color");
  if (ovColor) ovColor.value = state.overlayColor;
  document.querySelectorAll("#trace-venue button").forEach((b) =>
    b.classList.toggle("on", b.dataset.venue === state.venue));
  document.querySelectorAll("#trace-ust-venue button").forEach((b) =>
    b.classList.toggle("on", b.dataset.uvenue === state.ustVenue));
  const p = PRODUCTS.find((x) => x.id === state.product);
  const isCount = state.metric === "adt" || state.metric === "trades";
  // Metric buttons: ADT/TRADES need trade-count data; SI rows are levels-only.
  document.querySelectorAll("#trace-metric button").forEach((b) => {
    const mid = b.dataset.metric;
    const needTrades = mid === "adt" || mid === "trades";
    const ok = p.raw ? false : !needTrades || !!p.trades || p.synthetic;
    b.disabled = !ok;
    b.title = p.raw ? "Metric n/a — biweekly share levels"
      : ok ? metricById(mid).title : "Trade counts not published for this product";
    b.classList.toggle("on", state.metric === mid);
  });
  if (p.raw && state.metric !== "adv") {
    // SI rows ignore the metric toggle (levels only) — keep "adv" selected visually.
  }
  document.querySelectorAll("#trace-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === state.range));
  const customBox = document.getElementById("trace-range-custom");
  if (customBox) customBox.style.display = state.range === "custom" ? "" : "none";
}

// Programmatic product selection (used by the grid's row click).
export function setTraceChartProduct(id, venue, ustVenue) {
  if (!PRODUCTS.some((p) => p.id === id)) return;
  state.product = id;
  if (venue && VENUES.some((v) => v.id === venue)) state.venue = venue;
  if (ustVenue && UST_VENUES.some((v) => v.id === ustVenue)) state.ustVenue = ustVenue;
  syncControls();
  drawChart();
}

export function renderTraceCharts() {
  const root = document.getElementById("trace-chart-wrap");
  if (!root || root.dataset.init) return;
  root.dataset.init = "1";

  const prodOpts = PRODUCTS.map((p) => `<option value="${p.id}">${p.label}</option>`).join("");
  const ovOpts = OVERLAYS.map((o) => `<option value="${o.id}">${o.label}</option>`).join("");
  const metricBtns = METRICS.map((m) =>
    `<button data-metric="${m.id}" title="${m.title}">${m.label}</button>`).join("");
  const venueBtns = VENUES.map((v) =>
    `<button data-venue="${v.id}" title="TRACE monthly venue: ${v.label}">${v.label}</button>`).join("");
  const ustVenueBtns = UST_VENUES.map((v) =>
    `<button data-uvenue="${v.id}" title="Treasury venue: ${v.title || v.label}">${v.label}</button>`).join("");
  const rangeBtns = RANGES.map((r) => `<button data-range="${r.id}">${r.label}</button>`).join("");

  root.innerHTML = `
    <style>
      /* TRACE-4a: chart fills the full content width and renders taller. */
      #trace-chart-wrap .trace-chart { width: 100%; min-height: 500px; }
      #trace-chart { width: 100%; }
    </style>
    <div class="trace-controls">
      <label>Product
        <select id="trace-prod">${prodOpts}</select>
      </label>
      <span class="seg" id="trace-metric">${metricBtns}</span>
      <span class="seg" id="trace-venue">${venueBtns}</span>
      <span class="seg" id="trace-ust-venue">${ustVenueBtns}</span>
      <span class="seg" id="trace-range">${rangeBtns}</span>
      <span id="trace-range-custom" class="muted" style="display:none">
        <input type="date" id="trace-chart-start" aria-label="Start date"> →
        <input type="date" id="trace-chart-end" aria-label="End date">
        <button id="trace-chart-apply" class="mini-btn">Apply</button>
      </span>
      <label>Overlay
        <select id="trace-overlay">${ovOpts}</select>
      </label>
      <label>Overlay type
        <select id="trace-overlay-type">
          <option value="line">Line</option>
          <option value="bar">Bar</option>
        </select>
      </label>
      <label>Overlay color
        <input type="color" id="trace-overlay-color" value="#7fc9b5">
      </label>
    </div>
    <div id="trace-chart" class="trace-chart"></div>
    <div id="trace-chart-status" class="muted"></div>`;

  document.getElementById("trace-prod").addEventListener("change", (e) => {
    state.product = e.target.value;
    syncControls();
    drawChart();
  });
  root.querySelectorAll("#trace-venue button").forEach((b) =>
    b.addEventListener("click", () => {
      if (state.venue === b.dataset.venue) return;
      state.venue = b.dataset.venue;
      syncControls();
      drawChart();
    }));
  root.querySelectorAll("#trace-ust-venue button").forEach((b) =>
    b.addEventListener("click", () => {
      if (state.ustVenue === b.dataset.uvenue) return;
      state.ustVenue = b.dataset.uvenue;
      syncControls();
      drawChart();
    }));
  root.querySelectorAll("#trace-metric button").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.disabled || state.metric === b.dataset.metric) return;
      state.metric = b.dataset.metric;
      syncControls();
      drawChart();
    }));
  document.getElementById("trace-overlay").addEventListener("change", (e) => {
    state.overlay = e.target.value;
    drawChart();
  });
  document.getElementById("trace-overlay-type").addEventListener("change", (e) => {
    state.overlayType = e.target.value;
    drawChart();
  });
  document.getElementById("trace-overlay-color").addEventListener("input", (e) => {
    state.overlayColor = e.target.value;
    drawChart();
  });
  root.querySelectorAll("#trace-range button").forEach((b) =>
    b.addEventListener("click", () => {
      state.range = b.dataset.range;
      syncControls();
      if (state.range !== "custom") drawChart();
    }));
  document.getElementById("trace-chart-apply").addEventListener("click", () => {
    const s = document.getElementById("trace-chart-start").value;
    const e = document.getElementById("trace-chart-end").value;
    if (!s || !e || s > e) return;
    state.customStart = s;
    state.customEnd = e;
    state.range = "custom";
    syncControls();
    drawChart();
  });

  syncControls();
  drawChart();
}
