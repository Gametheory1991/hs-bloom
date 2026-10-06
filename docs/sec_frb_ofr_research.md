# SEC / FRB / OFR Data Research — os-bloom risk completeness
Research date: 2026-10-05. All URLs below were verified live with curl in this session
(HTTP status + actual response shape checked). No collector code was written.

Conventions: SEC.gov aggressively rate-limits scripts (403 "Request Rate Threshold
Exceeded" on generic UAs). All SEC fetches in this report succeeded with a declared
contact UA: `os-bloom-research/1.0 (contact: harrysugamakc@gmail.com)` — SEC requires
this. Keep request cadence low (≤1 req/2s).

---

## 1. SEC N-CEN (registered investment company census)

- **Index (HTTP 200):** https://www.sec.gov/data-research/sec-markets-data/form-n-cen-data-sets
- **ZIP pattern (verified):** https://www.sec.gov/files/dera/data/form-n-cen-data-sets/YYYYqN_ncen.zip
  e.g. 2026 Q2 → `2026q2_ncen.zip`, **HTTP 200, 8,404,110 bytes**, downloaded and inspected.
- **Format:** ZIP of ~40 TSVs (tab-separated, UTF-8) + `ncen_metadata.json` + `ncen_readme.htm`.
  `FUND_REPORTED_INFO.tsv` = 108 columns, one row per series/fund filing.
- **Cadence:** annual per fund (by fiscal year-end); SEC publishes *receipt* batches quarterly.
  Latest batch = **2026 Q2** (filings received Apr–Jun 2026; 2026 Q1 = 15.61 MB, 2025 Q4 = 7.22 MB).
  Slow census — not a high-frequency signal.
- **Key risk-relevant fields (verified in headers):**
  - Fund-type flags: `IS_ETF`, `IS_INDEX`, `IS_MULTI_INVERSE_INDEX` (leveraged/inverse),
    `IS_INTERVAL`, `IS_MONEY_MARKET`, `IS_FUND_OF_FUND`, `IS_NON_DIVERSIFIED`
  - Per-fund vol + return: `RETURN_B4_FEES_AND_EXPENSES`, `RETURN_AFTR_FEES_AND_EXPENSES`,
    `STDV_B4/AFTR_FEES_AND_EXPENSES` (annualized std dev — aggregate into vol-by-category)
  - Size: `MONTHLY_AVG_NET_ASSETS`, `DAILY_AVG_NET_ASSETS`, `NAV_PER_SHARE`
  - Securities lending: `SECURITY_LENDING.tsv` (`AVG_VALUE_SEC_LOAN`), `DID_LEND_SECURITIES`,
    `IS_COLLATERAL_LIQUIDATED`
  - Borrowing/liquidity: `LINE_OF_CREDIT_DETAIL.tsv` (`LINE_OF_CREDIT_SIZE`),
    `INTER_FUND_BORROWING_DETAIL.tsv`, `HAS_LINE_OF_CREDIT`
- **Recommendation: PERIODIC BATCH (quarterly).** Aggregate per quarter: fund counts/AUM by
  type flag, average STDV by category (risk appetite), sec-lending participation rate,
  credit-line usage. Cheap (8–16 MB/quarter) but slow-moving — census, not a timer series.

## 2. SEC N-PORT (monthly portfolio holdings, public 3rd-month reports)

- **Index (HTTP 200):** https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets
- **ZIP pattern (verified):** https://www.sec.gov/files/dera/data/form-n-port-data-sets/YYYYqN_nport.zip
  `2026q2_nport.zip` → **HTTP 200, 440,699,889 bytes (~420 MB)**, `Last-Modified: 2026-07-09`.
  (HEAD only — not downloaded.)
- **Format:** ZIP of **32 TSVs** + readme + metadata. Key tables:
  `SUBMISSION`, `REGISTRANT`, `FUND_REPORTED_INFO` (one row per series: `TOTAL_ASSETS`,
  `NET_ASSETS`, `BORROWING_PAY_WITHIN_1YR`, …), `FUND_REPORTED_HOLDING` (issuer, asset
  class, fair value), `IDENTIFIERS`, `DEBT_SECURITY`, derivative schedules
  (`SWAPTION_OPTION_WARNT_DERIV`, `FUT_FWD_NONFOREIGNCUR_CONTRACT`,
  `FWD_FOREIGNCUR_CONTRACT_SWAP`, `NONFOREIGN_EXCHANGE_SWAP`,
  `OTHER_DERIV_NOTIONAL_AMOUNT`), `SECURITIES_LENDING`, `REPURCHASE_AGREEMENT` /
  `REPURCHASE_COUNTERPARTY` / `REPURCHASE_COLLATERAL`, `INTEREST_RATE_RISK`,
  `MONTHLY_TOTAL_RETURN`, `BORROWER` / `BORROW_AGGREGATE`.
- **Cadence / delay:** funds file monthly (30 days); only the 3rd month of each fiscal
  quarter is made public, **60 days after quarter-end**. Practical latest as of Oct 2026:
  **2026 Q2 zip** (public holdings mostly as of Mar 2026 — ~6-month holdings lag);
  2026 Q3 posts ~early Oct 2026.
- **Recommendation: PERIODIC BATCH, aggregate-first (or SKIP for now).** At ~420 MB/quarter
  this is the heaviest feed here. Dashboard value is in aggregates — extract only
  `FUND_REPORTED_INFO` (net assets, borrowings), derivative notional sums, securities-lending
  totals — and discard the holding-level detail unless fund-level analytics is a stated goal.
  If storage/parse cost is a concern, the Private Funds Statistics XLSX (§4a) covers most of
  the same risk story at 592 KB.

## 3. OFR Hedge Fund Monitor — full positioning coverage

Dataset = `fpf` (SEC Form PF aggregates, qualifying hedge funds).

- **Catalog (HTTP 200, verified):** `GET https://data.financialresearch.gov/hf/v1/metadata/mnemonics?dataset=fpf`
  → JSON list of **329 mnemonics**, keys `mnemonic` + `series_name`. Keyless, no auth.
- **Series (HTTP 200, verified):** `GET https://data.financialresearch.gov/hf/v1/series/timeseries?mnemonic=<id>`
  → `[[date, value], …]`; **quarterly, 2013-03-31 → 2026-06-30 (54 points)**; metadata shows
  `last_update: 2026-09-16` (~2.5-month lag). OFR asks clients not to poll more than daily.
- **Already tracked (do not duplicate):** `FPF-BORROW_REPO_SUM`,
  `FPF-ASSETCLASS_LTREASURY_SUM`, `FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE`.

### Top 15 positioning mnemonics to add (series_name + units verified in catalog)

| # | Mnemonic | What it is (units) | Why it matters |
|---|----------|--------------------|----------------|
| 1 | `FPF-ASSETCLASS_STREASURY_SUM` | short UST exposure ($) — **$1.498T at 2026-06-30** | basis-trade short leg; pair with long leg already tracked |
| 2 | `FPF-ALLQHF_NAV_SUM` | total qualifying-HF net assets ($) — **$5.511T** | denominator for every HF risk ratio |
| 3 | `FPF-ASSETCLASS_IRD_GNE_SUM` | interest-rate-derivatives gross notional ($) — **$13.90T** | rates-leverage footprint |
| 4 | `FPF-STRATEGY_RV_LEVERAGERATIO_NAVWMEAN` | relative-value leverage (ratio) — **8.10x** | basis-trade concentration gauge |
| 5 | `FPF-STRATEGY_MULTI_LEVERAGERATIO_NAVWMEAN` | multi-strategy leverage (ratio) | pod-shop leverage trend |
| 6 | `FPF-STRATEGY_MACRO_LEVERAGERATIO_NAVWMEAN` | macro leverage (ratio) | discretionary rates/FX leverage |
| 7 | `FPF-ASSETCLASS_CREDIT_GNE_SUM` (+`_LGNE_SUM`/`_SGNE_SUM`) | credit gross/long/short notional ($) | credit book size + direction |
| 8 | `FPF-ASSETCLASS_EQUITIES_GNE_SUM` (+`_LGNE_SUM`/`_SGNE_SUM`) | equity gross/long/short notional ($) | equity long/short crowding |
| 9 | `FPF-BORROW_REPO_GAVN10_PERCENT` / `_GAVN11TO50_PERCENT` / `_GAVN51_PERCENT` | share of repo borrowing by size tier (%) | funding concentration in top-10 funds |
| 10 | `FPF-BORROW_PRIMEBROKER_SUM` | prime-brokerage borrowing ($) | PB funding channel (Archegos-angle) |
| 11 | `FPF-ASSETCLASS_REPO_REPO_SUM` / `FPF-ASSETCLASS_REPO_REVERSEREPO_SUM` | repo vs reverse-repo exposure ($) | net repo funding posture |
| 12 | `FPF-ALLQHF_GAVN11TO50_LEVERAGERATIO_AVERAGE` / `_GAVN51_…` | leverage by size tier (ratio) | is leverage broadening beyond top 10 |
| 13 | `FPF-STRATEGY_RV_CASHRATIO_NAVWMEAN` (also MULTI) | unencumbered cash (% of NAV) | liquidity buffer vs leverage |
| 14 | `FPF-ALLQHF_CDSUP250BPS_P5` / `_P50` | stress-test: NAV impact of +250bp credit-spread shock (%, p5/median) | tail sensitivity, ready-made risk tile |
| 15 | `FPF-ASSETCLASS_FX_GNE_SUM` | FX gross notional ($) | currency-book footprint |

Known catalog gap: **no counterparty breakdown** (borrowing is by type — repo / prime /
other-secured — not by dealer). All 15 are the same keyless quarterly pattern as the 3
already tracked, so they drop straight into the existing `ofr.py` fetcher.
**Recommendation: INTEGRATE NOW** (all 15, quarterly cadence).

### Other OFR public APIs with hedge-fund-relevant positioning

- **OFR Short-Term Funding Monitor API — VERIFIED LIVE, keyless.**
  Base: `https://data.financialresearch.gov/v1`
  Endpoints: `/v1/metadata/mnemonics` (**HTTP 200, 442 mnemonics**), `/v1/metadata/query?mnemonic=`,
  `/v1/series/timeseries?mnemonic=`, `/v1/calc/spread/`, `/v1/series/dataset/`.
  Datasets (prefix / count / cadence):
  - `FNYR-*` (30, **daily**): SOFR/EFFR/BGCR/TGCR/OBFR + percentiles. `FNYR-SOFR-A` verified
    live — **3.88 on 2026-10-02** (matches os-bloom's current SOFR). Clean JSON alternative to
    scraping NY Fed.
  - `REPO-*` (164, **daily**): FICC DVP cleared-repo average rates. Suffix `-P` = preliminary
    (**current: 3.86 on 2026-10-02**), `-F` = final (lags to 2026-06-30). Use `-P` for dashboard.
  - `MMF-*` (42, **monthly**): money-market-fund stats. `MMF-MMF_TOT-M` = **$8.53T total MMF
    assets (Aug 2026)**; also agency/GSE-repo splits.
  - `NYPD-*` (194, **weekly**, updated 2026-10-02): NY Fed primary-dealer stats —
    `AFtD`/`AFtR` fails (`NYPD-PD_AFtD_TOT-A` = **$308.2B fails-to-deliver, week of
    2026-09-23**), plus `RP`/`RRP`/`SB`/`SL` financing (44 series each, by collateral type).
    Cleaner JSON than the markets.newyorkfed.org API already in `dealer.py`.
  - `TYLD-*` (12): Treasury constant-maturity yields — duplicates FRED, skip.
  **Recommendation: INTEGRATE NOW** — FNYR-SOFR-A (daily), REPO-DVP_AR_TOT-P (daily),
  MMF-MMF_TOT-M (monthly), NYPD-PD_AFtD_TOT-A + NYPD-PD_AFtR_TOT-A (weekly), plus
  NYPD RP/RRP financing totals.
- **OFR securities-lending data:** no public API found (candidate page 404s). Collection is
  underway but nothing machine-readable as of Oct 2026 — **monitor, skip for now**.
- Note: `/hedge-fund-monitor/api/` and `/short-term-funding-monitor/api/` are the only two
  documented public OFR APIs. There is no OFR API for money-market-fund *holdings*
  (that's SEC N-MFP, §4b) or cleared bilateral repo beyond the DVP series above.

## 4. Risk data-completeness audit — SEC + FRB sources NOT in os-bloom

(Already in os-bloom and therefore excluded: FRED macro/rates series, NY Fed primary-dealer
stats via `dealer.py`, CFTC positioning via `cftc_pos.py`, FINRA TRACE, 13F, the 3 OFR HF series.)

### SEC

a. **Private Fund Statistics (IM Analytics Office; Form PF + Form ADV aggregates)** —
   **INTEGRATE NOW (quarterly batch).**
   - Index (HTTP 200): https://www.sec.gov/data-research/investment-management-data/division-investment-management-private-fund-statistics
   - Latest: **2025 Q3** report PDF (HTTP 200, 462 KB) + companion
     **XLSX (HTTP 200, 592 KB, posted 2026-04-01):**
     https://www.sec.gov/files/investment/private-funds-statistics-2025-q3-supporting-data.xlsx
   - Format: PDF narrative + **XLSX workbook with ~100+ tables** (the machine-readable part).
   - Cadence: quarterly; data typically ≥6 months old at publication.
   - Why it matters: the only *public* Form PF aggregates — hedge-fund leverage, borrowing
     distribution, gross-notional-exposure/NAV, strategy exposures, investor-vs-portfolio
     liquidity mismatch. Directly fills the "private funds" hole in the dashboard's risk tab
     and cross-checks the OFR HF series (same underlying Form PF).

b. **N-MFP (money-market-fund portfolio data)** — **PERIODIC BATCH or SKIP.**
   - Index (HTTP 200): https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets
   - Pattern: https://www.sec.gov/files/dera/data/form-n-mfp-data-sets/YYYYMMDD-YYYYMMDD_nmfp.zip
     (monthly receipt batches, e.g. `20260810-20260908_nmfp.zip` → **HTTP 200, ~11 MB**).
   - Why it matters: MMF portfolio holdings incl. repo counterparties and WLA — funding-risk
     detail behind the $8.5T MMF complex. But STFM `MMF-*` aggregates (§3) + FRED already cover
     the dashboard-level story; N-MFP only pays off for counterparty-level work.

c. **FOCUS (broker-dealer financials)** — **SKIP.** No machine-readable bulk feed exists;
   only annual aggregate tables inside "Select SEC and Market Data" PDFs
   (e.g. https://www.sec.gov/about/secstats2015.pdf). Not automatable.

### Federal Reserve Board

d. **Financial Stability Report chart data** — **SKIP as automated feed (reference only).**
   - https://www.federalreserve.gov/publications/2026-may-financial-stability-report-accessibility-tables.htm
     (**HTTP 200, 3.1 MB** HTML tables; semi-annual).
   - Why it matters: the Board's own vulnerability charts (valuations, business/household
     borrowing, financial-sector leverage, funding risk). But it's hand-built HTML per edition —
     brittle to scrape. Use as a manual cross-check, not a collector.

e. **SLOOS (Senior Loan Officer Opinion Survey)** — **INTEGRATE NOW (quarterly batch).**
   - Machine-readable via the Board's Data Download Program:
     https://www.federalreserve.gov/datadownload/Choose.aspx?rel=sloos (**HTTP 200**;
     preformatted CSV packages).
   - Cadence: quarterly. Why it matters: net % of banks tightening lending standards is the
     canonical credit-cycle / bank-risk-aversion gauge — missing from the dashboard.

f. **Commercial Paper (Board release)** — **INTEGRATE NOW (weekly).**
   - https://www.federalreserve.gov/datadownload/Choose.aspx?rel=CP (**HTTP 200**; CSV via DDP).
   - Cadence: weekly. Why it matters: CP outstanding by issuer type (financial/nonfinancial/ABCP)
     = short-term wholesale funding stress, complements the OFR repo series. (FRED also carries
     COMPAPER, but the DDP release has the full type breakdown.)

g. **Z.1 Financial Accounts** — **ALREADY-COVERED PATTERN.** Available through the same DDP
   (`rel=Z1`) and FRED (BOGZ1* credit-market-debt series). Recommend adding specific FRED
   series rather than a new fetcher (e.g. total credit-market debt, household/business leverage
   ratios).

h. **H.4.1 (reserve balances / balance sheet)** — **ALREADY PARTIALLY COVERED** (TGA etc. via
   FRED). No new feed needed; note the STFM NYPD fails series (§3) are a cleaner JSON source
   than the current NY Fed API path if `dealer.py` ever needs a fallback.

---

## Summary of recommendations

| Source | Verdict |
|---|---|
| OFR HF Monitor: 15 mnemonics (§3 table) | **Integrate now** — same keyless pattern as existing 3 |
| OFR STFM API: FNYR-SOFR-A, REPO-DVP_AR_TOT-P, MMF-MMF_TOT-M, NYPD fails + financing | **Integrate now** — keyless, daily/weekly/monthly |
| SEC Private Fund Statistics XLSX | **Integrate now** — quarterly batch |
| FRB SLOOS (DDP CSV) | **Integrate now** — quarterly batch |
| FRB Commercial Paper (DDP CSV) | **Integrate now** — weekly |
| SEC N-CEN | Periodic batch (quarterly) — census/slow |
| SEC N-PORT | Periodic batch aggregate-first, or skip — 420 MB/qtr |
| SEC N-MFP | Periodic batch or skip — STFM MMF covers aggregates |
| FRB FSR chart data | Skip (manual reference) |
| SEC FOCUS | Skip (no machine feed) |
| OFR securities lending | Monitor — no public API yet |
