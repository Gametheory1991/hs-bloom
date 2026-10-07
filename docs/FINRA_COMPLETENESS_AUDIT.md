# FINRA Data Completeness Audit

**Date:** 2026-10-06
**Directive:** Harry — "Do the same for all FINRA data — make sure you pull all and not selective."
**Standing rule:** each source-report cell should be its own data point.
**Scope:** all 13 FINRA fetchers in `collector/src/collector/fetchers/`. Read-only audit; no code changed.

**TOTAL_PARTS allowlist verified complete:** `["ust","tba","corp","eln","conv","agcy","abs","absx","cmo","mbs","chrc"]` — Treasury + all 10 monthly TRACE products. No contamination.

---

## Per-fetcher audit

### 1. `trace_monthly.py` — TRACE Monthly Volume Report
**Source:** `https://cdn.finra.org/trace/volume/monthly/TRACE_Public_Monthly_Report_YYYY-MM.xlsx` — monthly, published 3rd business day after month-end, history to 2017-01.
**Layout:** per product row: `[Trades: ATS | Interdealer | Customer | Total]`, `[Par $M: ATS | Interdealer | Customer | Total]`.

| What we pull | Status |
|---|---|
| Total par ($M) for all 10 products | ✅ complete |
| Total trades for CORP, CONV, CHRC, ELN | ✅ |
| Derived corp customer share | ✅ |
| Full history backfill to 2017-01 | ✅ complete |

**Gaps (selective):**
- **Trades for AGCY, ABS, ABSX, CMO, MBS, TBA not stored** — the trades columns exist in the file; only par is stored for these 6 products.
- **ATS / Interdealer / Customer venue splits not stored for ANY product** — only the Total columns are taken. This is the biggest gap: Harry's standing rules explicitly require ATS vs D2C venue splits, and they're sitting in the file unparsed.

### 2. `trace_treasury.py` — Treasury TRACE Aggregates
**Source:** daily `ts-daily-aggregates-YYYY-MM-DD.xlsx` (history since 2023-02-13) + monthly files for backfill.
**Layout:** per category row: `[Trades, Par $bn] × (ATS&Interdealer | Dealer-to-Customer | Total)` + VWAP. Category rows: Bills, FRNs, Nominal Coupons (with maturity-bucket + on/off-the-run detail rows), TIPS, Total.

| What we pull | Status |
|---|---|
| Total/Bills/FRNs/Coupons/TIPS par, Total trades | ✅ |
| On-the-run / off-the-run par (summed across buckets) | ✅ |

**Gaps (selective):**
- **ATS&Interdealer / Dealer-to-Customer splits not stored** — only Total columns taken (same gap as #1).
- **Maturity-bucket detail not stored** — the file has per-bucket rows (2Y/3Y/5Y/7Y/10Y/20Y/30Y); we only sum on/off-the-run across them. Harry's rules require the 5Y bucket and maturity splits.
- **VWAP column not stored.**
- **Trades for Bills/FRNs/Coupons/TIPS not stored** — only Total trades.
- **History: only 24 months backfilled.** Daily starts 2023-02-13; monthly files likely go back further (couldn't verify oldest — CDN rate-limited the probes).

### 3. `finra_regsho.py` — Reg SHO Daily Short Volume + Threshold List
**Source:** `https://cdn.finra.org/equity/regsho/daily/{CNMS,FNYX,FNSQ}shvolYYYYMMDD.txt` (fields: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market) + OTC threshold list via `api.finra.org` Query API.

| What we pull | Status |
|---|---|
| Per-venue aggregates (short/exempt/total/ratio) for CNMS/FNYX/FNSQ | ✅ |
| 6 watchlist tickers + top-50 per-ticker daily history | ✅ |
| Threshold count series + latest list doc | ✅ |

**Gaps (selective):**
- **Full per-ticker universe not stored** — each daily file has thousands of tickers; only aggregates + top-50 + 6 watchlist names are kept. (Storage tradeoff, but per the "each cell = one data point" rule this is the largest row-count gap in the equity set.)
- **History: only 252 trading days backfilled.** Verified live: files exist back to at least 2020-01-02 (200 OK), likely earlier — 1Y is a fraction of what's available.
- **Threshold: only the latest list stored** (60 dates of counts); historical per-security lists not kept.

### 4. `finra_short.py` — Short Interest (biweekly)
**Source:** `https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv` — ~22,596 rows/file, fields: symbol, name, exchange, market class, current/previous short, split flag, ADV, days-to-cover, revision, change %, change, settlement date.

| What we pull | Status |
|---|---|
| Total short shares (sum) + 6 hyperscaler tickers | ✅ |

**Gaps (selective):**
- **22,590 of 22,596 tickers not stored.** Only the aggregate total and 6 names are kept. Largest single selectivity gap in the audit.
- **History: only 12 settlements (~6 months) backfilled.** Files verified back to 2020-01-15 (200 OK); FINRA publishes this back to 2003.
- Per-row fields (exchange, market class, split flag, revision) not stored for anyone.

### 5. `finra_ids_star.py` — STAR TradingActivity
**Source:** monthly ZIP `HISTORIC_SPREPORTS-YYYYMM.zip`, daily files back to 2011. Docstring claims "captures every published cell" (~100 series: trade count / unique SEC IDs / $ trades per issuer/grade/sub-breakdown).

**Gaps:**
- **History: only 36 months backfilled; ZIPs go back to 2011** (15 years available, we take 3).
- The "every cell" claim should be spot-checked against a live file (not verified in this audit).

### 6. `finra_ids_px.py` — STAR PXTABLES (pricing)
**Source:** same ZIP, 7 data sheets × 10 metric rows per block/dimension.

| What we pull | Status |
|---|---|
| 10 metrics (avgpx, wavgpx, bot5, q2/q3/q4, top5, stdev, vol, ntrades) per coupon/vintage/issuer cell | ✅ |

**Gaps (CONFIRMED in code — the known critical gap):**
- **CUSTOMER BUY rows skipped**
- **CUSTOMER SELL rows skipped**
- **DEALER TO DEALER rows skipped**
- **Ticket-size bucket rows skipped** (≤$1MM / $10MM / $100MM and trade-count mirrors)
- **History: only ~3 months backfilled** (new dataset).

### 7. `finra_capped.py` — Capped Volume Report
**Source:** `https://cdn.finra.org/trace/cta/monthly/CA_CTA.csv` — MONTH × Grade × (AVG Size, Total), 5 grades, 12 rolling months.
**Verdict: COMPLETE.** All 5 grades, both columns, accumulates via upsert. ✅

### 8. `finra_margin.py` — Margin Statistics
**Source:** FINRA margin-statistics.xlsx — debit balances, free credit (cash + margin), history to 1997-01 in one file.
**Verdict: COMPLETE.** All 3 columns, full history. ✅

### 9. `finra_breadth.py` — Bond Breadth + Sentiment (dynarep API)
**Source:** `MarketActivityAggregates` + `MarketSentimentAggregates` via the public dynarep API; history from 2018-01-22.

| What we pull | Status |
|---|---|
| Breadth: 3 bond types × 4 sectors × 7 metrics + AD spread | ✅ |
| Sentiment: 3 × 6 issue types × 4 flows × (volume + trades) + netflow | ✅ |
| Full history to 2018-01-22 | ✅ |

**Gaps (selective):**
- **Affiliate flows:** the module docstring says the sentiment dataset carries "dealer buy-from-customer / sell-to-customer / inter-dealer / affiliate flows" but `SENT_FLOWS` only maps 4 flows with no affiliate — verify against the live API whether an affiliate flow code exists and is being dropped.
- **`totalTradedSecuritiesCount` (issue counts) is fetched from the API but NOT stored** — only volume and trade counts are kept.

### 10. `finra_corp.py` — Most-Active Corporate Bonds (dynarep API)
**Source:** `MostActiveCorporateSecurities` + `MostActiveCorporate144ASecurities` — 10 IG + 10 HY + 10 convertibles per day, history from 2023-02-15.

| What we pull | Status |
|---|---|
| Per-category daily avg yield/price/change + full bond lists in snapshot doc | ✅ |
| Full history to 2023-02-15 (verified earliest) | ✅ |

**Gaps (selective):**
- **`highPrice` / `lowPrice` fetched but dropped** — only last/change/yield stored per bond.
- Note: top-10-per-category is what FINRA publishes (a sample by design, not our filter).

### 11. `finra_factbook.py` — TRACE Fact Book (quarterly + annual)
**Source:** quarterly workbooks (back to 2016) + annual Transaction/Issue/Participant workbooks. Very comprehensive: top-50 lists, size buckets, buy-sell ratios, time-of-day grids, issues outstanding, participant concentration.

**Gaps (selective):**
- **Quarterly P1/S1 sheets: 144A / Publicly-Traded sub-rows skipped** — `parse_block` takes the headline Total + 6 size buckets only.
- **Quarterly buy-sell sheets: maturity-band → rating → bucket drill-down skipped** — parsing stops after the top-level bucket run.
- **Top-50/Top-25 lists: latest quarter only** (snapshot doc) — not kept as per-quarter time series.
- History accumulates from deploy; workbooks go back to 2016.

### 12. `ice_star.py` — ICE Vantage STAR (daily ZIP)
**Source:** `vantage.interactivedata.com/aggregate/download` — daily ZIP of the same STAR report.

| What we pull | Status |
|---|---|
| 4 coarse aggregates: agency/non-agency par + trades | ⚠️ |

**Assessment:** overlaps `finra_ids_star.py` (same underlying report, 100+ granular series). Its only edge is **freshness** — ICE is a daily ZIP; the FINRA CDN ships in monthly ZIPs (delayed). **History: only 30 days backfilled.**
**Decision needed:** keep for timeliness (document the role) or retire as redundant.

### 13. `ticker_stats.py`
Finnhub-based (price stats for the Reg SHO top-50), not a FINRA fetcher. Out of scope.

---

## FINRA feeds we are NOT pulling at all

1. **FINRA ATS Transparency (dark pools)** — `ats.finra.org`. Weekly per-security per-ATS volume and trade counts, free downloadable files back to **2014**. This is the dark-pool tape Harry's market-structure work keeps circling. **Highest-value missing feed.**
2. **FINRA OTC (non-ATS) Transparency** — weekly/monthly non-ATS OTC equity volume by firm and security (the other half of off-exchange volume). API at `developer.finra.org` (needs API-center setup); web at `otctransparency.finra.org`. Not pulled.
3. **TRACE transaction-level downloads** (`download.finratraqs.org` — end-of-day TRACE files for Treasuries/Corp-Agency/Securitized per the published Web API specs). Connection blocked from this sandbox (likely IP-entitled); needs verification whether keyless access is possible.
4. **Other dynarep `FixedIncomeMarket` datasets** beyond the 4 registered templates — not enumerated in this audit (would need live template probing).
5. **FINRA Industry Snapshot market-data tables** — annual aggregates; superseded by our own series. Low value.

---

## Prioritized fix list

**P0 — completeness of what we already pull (Harry's core rule):**
1. PXTABLES: store customer buy/sell, dealer-to-dealer, and ticket-size bucket rows as their own series.
2. `trace_monthly`: store ATS / Interdealer / Customer venue splits for all 10 products.
3. `trace_monthly`: store trades for AGCY, ABS, ABSX, CMO, MBS, TBA (columns exist).
4. `trace_treasury`: store ATS&Interdealer / D2C splits + per-maturity-bucket rows + VWAP.
5. `finra_short`: store the full per-ticker universe (or top-500 minimum) instead of 6 tickers; extend backfill beyond 12 settlements.
6. `finra_regsho`: extend daily backfill beyond 252 days (files go back to ≥2020).

**P1 — new feeds:**
7. ATS Transparency weekly dark-pool data (new fetcher; free, back to 2014).
8. OTC non-ATS transparency (new fetcher; API setup at developer.finra.org).

**P2 — depth and polish:**
9. STAR: extend backfill 36 months → full history (2011).
10. `trace_treasury`: extend monthly backfill beyond 24 months (verify oldest file).
11. Fact Book: 144A sub-rows + buy-sell maturity/rating drill-down; top-50 lists as time series.
12. Breadth/sentiment: verify affiliate flows; store issue counts.
13. `finra_corp`: store high/low prices per bond.
14. `ice_star`: decide keep (timeliness) vs retire (redundant); extend backfill either way.
15. Threshold list: keep historical per-security lists, not just counts.
16. TRACE TRAQS downloads: verify whether keyless access is possible.
