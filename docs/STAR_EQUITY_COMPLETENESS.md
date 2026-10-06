# STAR + Equity Data Completeness Matrix
Audited 2026-10-06 against the live FINRA CDN files (not the ICE Vantage screenshots —
verified each dimension exists in the free public feed before claiming it).

## STAR — TradingActivity sheet (free, `FINRA_IDS_STAR-*.xlsx`)

| Metric | Breakdown | Pulled | Exposed in UI |
|---|---|---|---|
| $ Trades (par) | TBA / Specified / Agency CMO / Non-Ag CMO / Non-Ag CMBS / Agency CMBS / ABS / CLO totals | Y | Y — grid rows + chart |
| Trade count | same 8 totals | Y | Y — grid rows + chart |
| Unique SEC IDs | same 8 totals | Y (new) | Y — export; chart via product list |
| $ / trades / secids | TBA by issuer: UMBS / FNMA / FHLMC / GNMA / **OTHER AGENCY** (new) | Y | Y — chart |
| $ / trades | TBA: 15Y / 30Y / Other; Specified: 15Y / 30Y / Adj-Hybrid / Other | Y (new) | Y — chart + table rows |
| $ / trades / secids | Agency CMO: P&I / IO/PO | Y (new) | Y — chart + table rows |
| $ / secids | Non-Ag CMO/CMBS, Agency CMBS: P&I / IO/PO × IG / HY | Y (new) | Y — chart |
| $ / trades | ABS / CLO / **OTHER**: IG / HY / total | Y (OTHER new) | Y — chart (ABS/CLO rows) |

## STAR — Pricing Tables (free, `FINRA_IDS_PXTABLES-*.xlsx`, 7 sheets)

| Metric | Breakdown | Pulled | Exposed in UI |
|---|---|---|---|
| Avg price, Wtd avg, Bottom-5, Q2/Q3/Q4, Top-5, Stdev, Volume, # trades | TBA × issuer × coupon (≤3.5–>6) | Y (new, `finra_ids_px.py`) | chart (wiring in progress) |
| same 10 | Specified MBS × issuer × coupon | Y (new) | chart (wiring in progress) |
| same 10 | Agency CMO × issuer × deal vintage | Y (new) | chart (wiring in progress) |
| same 10 | Non-Ag CMO / ABS × IG × product | Y (new) | chart (wiring in progress) |
| same 10 | Agency CMBS × P&I/IO/PO × vintage (daily + weekly) | Y (new) | chart (wiring in progress) |
| same 10 | CBO/CDO/CLO × vintage; AAA × vintage | Y (new) | chart (wiring in progress) |

**Not in the free feed (ICE Vantage paywalled):** intraday updates, custom cross-report
analytics, pre-built dashboards. All raw price/vintage/coupon dimensions above ARE free.

## Equity datasets

| Dataset | Field | Pulled | Exposed |
|---|---|---|---|
| Reg SHO daily | Short vol / total vol / short ratio — CNMS, NYSE, FINRA TRF | Y | Y — markets table + chart |
| Reg SHO daily | Short-exempt vol — 3 markets | Y | N (series only) |
| Reg SHO daily | Per-ticker short vol / total vol / **short-exempt** (new) — top 50 | Y | Y — rich table |
| Threshold list | Symbol / name / category / Reg SHO / Rule 4320 | Y | Y — table |
| FINRA short interest | Total short shares (all listed) | Y | Y — KPI + chart |
| FINRA short interest | Per-ticker: short, prev, avg daily vol, days-to-cover, Δ% | Y | Y — watchlist |
| FINRA short interest | Per-ticker: **company name, Δ nominal shares** (new) | Y (new) | Y — watchlist |
| FINRA margin | Debit balances / free credit (cash + margin accts), $M, monthly since 1997 | Y (all 3 cols) | Y — table + chart |

## Gaps closed this round
1. PXTABLES price metrics — were completely unparsed; now parsed (free source).
2. Unique SEC IDs — published per cell; now stored.
3. OTHER AGENCY issuer column — was silently dropped; now stored + included in totals.
4. 15Y/30Y, P&I/IO/PO sub-breakdowns — were aggregated away; now separate series.
5. OTHER single-row category — was ignored; now stored.
6. Short-interest company names + nominal change — now pulled from the file.
7. Per-ticker short-exempt volume — now stored.
