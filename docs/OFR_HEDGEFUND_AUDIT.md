# OFR Hedge Fund Coverage Audit — os-bloom

**As of 2026-10-06.** Question: do we have the FULL set of OFR data to track hedge funds?

**Short answer: No — we have ~12% of the Form PF aggregates (40/329 mnemonics), 7/442 Short-Term Funding Monitor series, and 0/153 of the OFR Traders-in-Financial-Futures (TFF) series. The core leverage/repo/Treasury-exposure story is covered; the basis-trade futures leg, strategy breadth, stress tests, and liquidity term-structure are the gaps.**

All OFR endpoints are keyless. Docs: https://www.financialresearch.gov/hedge-fund-monitor/api

## 1. What we pull today

### A. OFR Hedge Fund Monitor — Form PF aggregates (dataset=fpf, quarterly, 2013→present)
Fetcher: `collector/src/collector/fetchers/ofr.py` → stored as `ofr:{mnemonic}` and `cycle:hf-*`. **40/329 mnemonics.**

| Area | We have | Missing |
|---|---|---|
| Gross assets / NAV / fund count | GAV_SUM, NAV_SUM, COUNT, top-10 GAV share | — (covered) |
| Leverage by size tier | Top-10, 11–50, 51+ ratios | GAV-weighted variants exist but NAV-weighted is the standard — fine |
| Repo borrowing | Total, prime-brokerage, top-10/11–50/51+ shares, repo vs reverse-repo exposure | Bilateral vs triparty split (not in FPF) |
| Treasury exposure | Long + short UST $ | 10Y-equivalent / DV01 versions don't exist in FPF (they're in TFF) |
| Asset-class notionals | Credit (GNE/L/S), Equities (GNE/L/S), FX GNE, rates-derivatives GNE | Sovereign GNE, US-gov-agency, listed equity sec vs deriv splits |
| Strategy leverage | RV, Multi, Macro (NAV-weighted) | **Equity, Credit, Event, Futures, FoF, Other** (12 mnemonics) |
| Cash buffers | RV + Multi unencumbered cash % | **Equity, Credit, Event, Macro, Futures cash ratios** |
| Stress tests | CDS +250bp (P5/P50), EQ −20% (P5/P50), CDS −250bp P5 | CDS −250bp P50, EQ **+20%**, currency **±20%**, rates **±75bp** (10 mnemonics) |
| Liquidity gates | Currently-gated %, currently-suspended % | Side-pocket % (1 mnemonic) |
| Financing liquidity | ≤7d $, 90d+ % | **1–7d, 7–90d buckets** (sums + %), investor-liquidity and portfolio-liquidity term structures (~18 mnemonics) |
| Counterparty | — | **Top-10 counterparty exposures** PARTY1–10 (10 mnemonics) |

### B. OFR Short-Term Funding Monitor (442 mnemonics; we pull 7)
Fetcher: `collector/src/collector/fetchers/ofr_stfm.py`.

| We pull | Missing (hedge-fund-relevant) |
|---|---|
| SOFR (daily), cleared-repo avg rate (daily), MMF total assets (monthly), dealer fails deliver/receive (weekly), dealer repo/RRP financing (weekly) | **GCF repo volumes** (48 mnemonics — the dealer-intermediated funding HFs use), **DVP repo by collateral/tenor** (164 mnemonics), triparty repo, bilateral repo rates/volumes, securities-lending (not in STFM by keyword — likely unpublished) |

### C. Leveraged-funds futures (CFTC TFF via Socrata — partial cover)
`cftc_pos` pulls TFF leveraged-funds long/short for 2Y/5Y/10Y/30Y Treasury futures + SOFR/VIX/Nasdaq/Russell 2000. This covers the "who is short" question at the **contract-count level**, weekly.

## 2. The gaps (prioritized)

### P0 — OFR TFF dataset via the HF API (0/153 pulled; verified live 2026-10-06)
`https://data.financialresearch.gov/hf/v1/metadata/mnemonics?dataset=tff` → 153 mnemonics, same keyless `[[date,value]]` timeseries shape as FPF. **This is the single biggest gap.** The config itself flags it (config.yaml:238): *"the free OFR Hedge Fund Monitor API has it (TFF-LF_TREAS_NET_POSITION) but needs a small ofr.py fetcher."*

Why it matters: TFF-LF_TREAS_NET_POSITION = leveraged-funds (i.e. hedge funds) **net Treasury futures position**, weekly since 2013-03. Latest verified: **−$798.8B (2026-09-15)** — the basis-trade short footprint. The OFR version adds what CFTC-Socrata doesn't: **DV01 and 10Y-equivalent** measures (TFF-LF_TREAS_NET_DV01, _NET_POS10YREQV), plus asset-manager and dealer splits (AI/DI) for the same contracts.

Highest-value mnemonics to add (~15):
- `TFF-LF_TREAS_NET_POSITION`, `_LONG_POSITION`, `_SHORT_POSITION`
- `TFF-LF_TREAS_NET_DV01`, `_NET_POS10YREQV`
- `TFF-LF_ED_NET_DV01`, `TFF-LF_FF_NET_DV01` (Eurodollar/SOFR futures DV01 — funding leg)
- `TFF-AI_TREAS_NET_POSITION`, `TFF-DI_TREAS_NET_POSITION` (who's on the other side)
- `TFF-LF_BITCOIN_NET_POSITION`, `TFF-LF_ETHER_NET_POSITION` (crypto basis)

Effort: small — same fetch pattern as `ofr.py` with `dataset=tff` on the metadata call; timeseries endpoint is identical.

### P1 — Strategy leverage breadth (12 mnemonics)
Add `FPF-STRATEGY_{EQUITY,CREDIT,EVENT,FUTURES,FOF,OTHER}_LEVERAGERATIO_NAVWMEAN` (+ GAVWMEAN variants). Today a credit-stress event at a credit fund is invisible — we only see RV/Multi/Macro.

### P2 — Stress-test completion (10 mnemonics)
Add the missing shock percentiles: `CDSDOWN250BPS_P50`, `EQUP20P_P5/P50`, `CURRENCYDOWN20P`/`CURRENCYUP20P` (P5/P50), `RFDOWN75BPS`/`RFUP75BPS` (P5/P50). The P5 (tail) rows are the early-warning ones.

### P3 — Liquidity term structure (~18 mnemonics)
`FINANCINGLIQUIDTYGT1LE7`, `GT7LE90` (sums), `INVESTORLIQUIDITY*`, `PORTFOLIOLIQUIDITY*` — the maturity-mismatch / redemption-risk view. Plus `SIDEPOCKET` % and `PARTY1–10` counterparty concentration.

### P4 — STFM funding depth
Add GCF repo volumes (`REPO-GCF_AR_*`) and DVP repo by tenor/collateral. These show the funding HFs actually roll.

## 3. What "full" can't include (structural limits)

- **Fund-level Form PF** is confidential by statute — OFR aggregates (the FPF dataset) are the entire public surface. There is no deeper free source.
- **OFR Hedge Fund Monitor briefs** (financialresearch.gov/briefs) are qualitative PDFs, not machine-readable. Could add the briefs feed to REG WATCH as a news source.
- **SEC 13F** covers long equity positions of large managers (we pull Millennium/Bridgewater/Renaissance/Pershing/Scion/Berkshire) — useful cross-check, not a substitute for Form PF leverage data.
- **Bilateral repo (OFR cleared bilateral + FICC)** — the FPF BORROW_REPO_SUM is the aggregate; trade-level bilateral data is regulatory-only.

## 4. Bottom line for Harry

| Dataset | Have | Total | Verdict |
|---|---|---|---|
| OFR FPF (Form PF aggs) | 40 | 329 | Core covered; strategy/stress/liquidity breadth missing |
| OFR TFF (lev-funds futures) | 0 | 153 | **Biggest gap** — basis-trade DV01/10Y-equiv not pulled |
| OFR STFM | 7 | 442 | Rates + dealer financing covered; GCF/DVP volumes missing |
| CFTC TFF via Socrata | ~10 codes | — | Partial cover of futures positioning (counts, not DV01) |
| SEC 13F (cross-check) | 6 managers | — | Fine as-is |

**Recommendation:** P0 (TFF via OFR API) is a ~1-hour build on the existing `ofr.py` pattern and closes the most strategically important gap — it gives us the hedge-fund Treasury futures short in DV01 terms, which is the number Harry watches for basis-trade stress. P1–P3 are config-only additions (no code) to the `ofr_series` list.
