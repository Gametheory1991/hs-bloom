# Holdings by Holder + Debt Outstanding Composition — Research
Research date: 2026-10-05. Every series ID / URL below was verified live with curl
(HTTP status + actual data sample) in this session. Nothing below is from memory:
FRED Z.1 codes were derived from the Board's official DDP-FRED crosswalk
(https://www.federalreserve.gov/data/documents/DDP-FRED%20Data%20Series%20Crosswalk.csv,
HTTP 200, 50,314 rows) and each ID was then fetched via the keyless
https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID> endpoint.

Conventions: FRED Z.1 levels are **$ millions**, quarterly, end-of-period,
not seasonally adjusted. TIC values are **$ billions**, monthly. MSPD amounts
are **$ millions**. OFS-2 amounts are **$ billions**. "Verified value" below is
the latest observation returned (Z.1 = Q2 2026 for most series; a few still Q1).

---

## Workstream 1: Holdings by holder (who holds the bonds)

### 1a. Treasury securities by holder — FRED Z.1 (quarterly levels)

| Holder sector | FRED series ID (verified) | Q2 2026 value | Exact Z.1 title (via Board Series Analyzer) |
|---|---|---|---|
| Federal Reserve (SOMA) | BOGZ1FL713061103Q | $4,082.7 bn | Central bank; total Treasury securities; asset |
| U.S.-chartered depository institutions (banks) | BOGZ1FL763061100Q | $1,799.1 bn | U.S.-chartered depository institutions; Treasury securities; asset |
| Mutual funds | BOGZ1FL653061105Q | $1,733.7 bn | Mutual funds; Treasury securities; asset (market value) |
| Money market funds | BOGZ1FL633061105Q | $3,426.4 bn | Money market funds; Treasury securities; asset |
| Rest of world (foreign) | ROWTSEQ027S | $9,269.2 bn | Rest of the world; Treasury securities; asset |
| Private pension funds | BOGZ1FL573061105Q | $573.0 bn | Private pension funds; Treasury securities; asset |
| State & local govt employee retirement funds | BOGZ1FL223061143Q | $561.6 bn | State and local government employee defined benefit pension funds; Treasury securities; asset |
| Life insurance companies | BOGZ1FL543061105Q | $219.0 bn | Life insurance companies; Treasury securities; asset |
| Property-casualty insurers | BOGZ1FL513061105Q | $405.0 bn | Property-casualty insurance companies; Treasury securities, incl. U.S. residual market reinsurers; asset |
| Households + nonprofits | HNOTSAQ027S | $2,831.1 bn | Households and nonprofit organizations; Treasury securities; asset |
| Nonfinancial corporate business | TSABSNNCB | $218.6 bn | Nonfinancial corporate business; Treasury securities; asset |
| State & local governments | BOGZ1FL213061103Q | $1,647.6 bn | State and local governments; Treasury securities, **excluding SLGS**; asset |

Notes:
- Sector codes are NOT guessable: several sectors use FRED-mnemonic IDs
  (ROWTSEQ027S, HNOTSAQ027S, TSABSNNCB) rather than BOGZ1, and variant suffixes
  differ by sector (…05 for most, …03 for Fed/SL govt, …00 for banks, …43 for
  SL retirement). Always resolve via the DDP-FRED crosswalk, never by pattern.
- Hedge funds (domestic): BOGZ1FL623061103Q = $123.5 bn — title is
  "Treasury securities **net of short sales**; asset (market value)". That is why
  it is far below OFR's $2.36T *long* exposure: Z.1 nets the $1.5T short book
  against it. Do NOT present it as gross long exposure.
- Z.1 "Mutual funds" (sector 65) is mutual funds + ETFs at market value.
- SL govt series excludes SLGS (State and Local Government Series) — SLGS sits
  in nonmarketable debt instead.
- Publication lag: Z.1 quarterly, ~1 quarter (Q2 2026 released Sept 2026).

### 1b. Corporate & foreign bonds by holder — FRED Z.1 (quarterly, $mn)

| Holder | Series ID (verified) | Q2 2026 |
|---|---|---|
| Banks (U.S.-chartered DI) | BOGZ1FL763063005Q | $817.7 bn |
| Mutual funds | BOGZ1FL653063005Q | $2,672.1 bn |
| Money market funds | BOGZ1FL633063005Q | $20.2 bn |
| Private pensions | BOGZ1FL573063005Q | $897.3 bn |
| Life insurers | BOGZ1FL543063005Q | $3,944.2 bn |
| P&C insurers | BOGZ1FL513063005Q | $906.0 bn |
| Foreign | ROWCBSQ027S | $5,195.7 bn |
| Households + nonprofits | CFBABSHNO | $192.0 bn |
| Nonfinancial corporate | BOGZ1FL103063065Q | $4.8 bn |
| State & local govts | SLGCORQ027S | $327.0 bn |
| SL retirement funds | BOGZ1FL223063045Q | $634.6 bn |
| (Total liability cross-check) | ASCFBL — "All Sectors; Corporate and Foreign Bonds; Liability" | $17,705.2 bn |

Fed holds no corporate bonds (NONE in crosswalk — correct, facilities wound down).

### 1c. Agency- and GSE-backed securities by holder — FRED Z.1 (quarterly, $mn)

| Holder | Series ID (verified) | Q2 2026 |
|---|---|---|
| Federal Reserve | BOGZ1FL713061705Q | $1,670.6 bn |
| Banks (U.S.-chartered DI) | BOGZ1FL763061705Q | $2,837.6 bn |
| Mutual funds | BOGZ1FL653061703Q | $782.5 bn |
| Money market funds | BOGZ1FL633061700Q | $1,207.6 bn |
| Private pensions | BOGZ1FL573061705Q | $278.9 bn |
| SL retirement funds | BOGZ1FL223061743Q | $216.3 bn |
| Life insurers | BOGZ1FL543061705Q | $276.7 bn |
| P&C insurers | BOGZ1FL513061705Q | $243.0 bn |
| Foreign | ROWGBSQ027S | $1,460.3 bn |
| Households + nonprofits | AGSEBSABSHNO | $868.9 bn |
| Nonfinancial corporate | AGSEBSABSNNCB | $71.7 bn |
| State & local govts | SLGGBSQ027S | $475.7 bn |

### 1d. Municipal securities by holder — FRED Z.1 (quarterly, $mn)

| Holder | Series ID (verified) | Q2 2026 |
|---|---|---|
| Banks (U.S.-chartered DI) | BOGZ1FL763062005Q | $352.4 bn |
| Mutual funds | BOGZ1FL653062003Q | $861.3 bn |
| Money market funds | BOGZ1FL633062000Q | $154.6 bn |
| Life insurers | BOGZ1FL543062005Q | $165.6 bn |
| P&C insurers | BOGZ1FL513062005Q | $215.1 bn |
| Foreign | ROWMLAQ027S | $131.1 bn |
| Households + nonprofits | MSABSHNO | $2,114.2 bn |
| Nonfinancial corporate | MSABSNNCB | $26.3 bn |
| State & local govts | SLGMLOQ027S | $50.2 bn |
| SL retirement funds | BOGZ1FL223062043Q | $0 bn (verified zero — economically correct: tax-exempt pensions don't buy tax-exempt bonds) |

No Fed muni holdings (NONE — correct). No private-pension muni series (NONE — same tax logic).

### 1e. TIC beyond Table 5 — official vs private split?

Checked live: `https://ticdata.treasury.gov/Publish/` directory listing → **404**
(no listing). `slt_table1.txt` through `slt_table6.txt` → all **HTTP 200**,
but tables 1–4 and 6 are **flow** tables (purchases/sales, $mn), not holdings,
and none contain official/private rows (grepped). `mfh.txt` is frozen at
Jan 2023 (per existing fetcher notes). `https://ticdata.treasury.gov/resource/`
→ 404.

**Verdict: no machine-readable monthly official-vs-private foreign split exists
in the TIC txt files.** The split appears only in the monthly press-release
HTML tables (not machine-friendly). Recommendation: use Z.1 ROWTSEQ027S
(quarterly foreign total, $9.27T — reconciles with TIC Grand Total) as the
cross-check; keep TIC Table 5 for the monthly country detail already integrated.

### 1f. Treasury Bulletin OFS-2 — machine-readable? YES

Endpoint (HTTP 200, verified):
`https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/tb/ofs2_estimated_ownership_treasury_securities`

Fields: `record_date`, `end_of_month`, `securities_owner`, `securities_bil_amt`
($bn). Quarterly rows. **Lag caveat: the detailed holder breakdown lags ~9 months**
(latest with detail = end_of_month 2025-12-31, published in the Sept 2026
Bulletin); headline totals (Total Public Debt, Fed+Govt accounts, Total
Privately Held) are ~3 months fresh. Detail for 2025-12-31 ($bn):
Total Public Debt 38,514.0; Fed + Govt Accounts 11,895.1; Total Privately Held
26,618.9; Depository Institutions 2,082.7; Savings Bonds 149.9; Private Pensions
602.2; SL Pensions 519.1; Insurance Companies 571.8; Mutual Funds 5,203.0;
State & Local Govts 1,642.9; Foreign & International 9,269.5; Other Investors
6,577.9. **Trap: OFS-2 "Mutual Funds" INCLUDES money market funds** (Bulletin
footnote) — do not add MMF separately when using OFS-2.

### 1g. Fed SOMA Treasury holdings — which series?

- **Recommend FRED TREAST** (verified: 2026-09-30 → $4,564,161 mn = $4.56T):
  "U.S. Treasury Securities Held by the Federal Reserve", **weekly**, $mn.
  Timeliest single number for the dashboard.
- Z.1 BOGZ1FL713061103Q ("Central bank; total Treasury securities; asset",
  quarterly, $4.08T @ Q2) as the quarterly cross-check. Note the amortized-cost
  sibling BOGZ1FL713061163Q ($4.64T) — valuation-concept trap; use …103.
- For SOMA-vs-public per CUSIP, use the NY Fed file (Workstream 2, §2c), not FRED.

### 1h. Coverage map — what the dashboard already has vs what's new

| Holder | Already covered | New from this research |
|---|---|---|
| Foreign | TIC Table 5 (monthly, by country) | Z.1 ROWTSEQ027S quarterly total (cross-check); OFS-2 "Foreign & International" |
| Hedge funds | OFR FPF-ASSETCLASS_LTREASURY_SUM (long $) | Z.1 BOGZ1FL623061103Q **net** of shorts ($123.5B) — different concept, label carefully |
| Mutual funds | N-CEN/N-PORT aggregates (in build) | Z.1 BOGZ1FL653061105Q market-value level |
| Fed | — | TREAST (weekly) — recommend adding |
| Banks, pensions, insurers, households, SL govts | — | All Z.1 series above — new |
| MMFs | STFM MMF totals (in build) | Z.1 BOGZ1FL633061105Q Treasury-holdings slice |

**Double-counting traps:**
- OFS-2 "Mutual Funds" includes MMFs — never sum OFS-2 mutual funds + MMF.
- TIC foreign (monthly) vs Z.1 ROW (quarterly) vs OFS-2 foreign — same universe,
  three cadences; pick one per view, cross-check with the others.
- Z.1 "Mutual funds" vs N-CEN/N-PORT aggregates — same universe.
- OFR HF long-UST ($2.36T gross long) vs Z.1 HF net-of-shorts ($123.5B) — gross
  vs net; never mix.
- TIC has custodial bias (e.g. Belgium/Luxembourg/Cayman as custodians); Z.1 ROW
  is the cleaner "true foreign" total.

---

## Workstream 2: Debt outstanding composition (the stock cube)

### 2a. CUSIP-level outstanding — MSPD Table III (VERIFIED)

Endpoint (HTTP 200):
`https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd/mspd_table_3?sort=-record_date&page[size]=<n>`

- **1,090 records per month-end**, ~0.9 MB/month. Monthly, record_date =
  month-end, latest **2026-08-31** (~5-week lag).
- Per-record fields: CUSIP is in `security_class2_desc` (e.g. `912797VE4`);
  `security_class1_desc` = product class; `issue_date`, `maturity_date`,
  `interest_rate_pct`, `yield_pct`, `issued_amt`, `inflation_adj_amt`,
  `redeemed_amt`, `outstanding_amt` (**$ millions**), `security_type_desc`
  (Marketable/Nonmarketable).
- Product taxonomy (record counts, Aug 2026): Notes 326, Bonds 313,
  Bills "Bills Maturity Value" 90, TIPS "Inflation-Protected Securities" 133,
  FRNs "Floating Rate Notes" 24 → 888 marketable; Government Account Series 171,
  SLGS 4, Savings Securities 7+2 total rows, Domestic Series 4, Other Debt 9,
  Federal Financing Bank 1 → 202 nonmarketable; plus total rows.
- Sample records (verified 2026-08-31): Bill 912797VE4, issued 2026-08-04,
  matures 2026-09-29, yield 3.675%, issued $105,462.4mm; Bill 912796ZW2-class
  912797SA6, issued 2025-10-02, matures 2026-10-01, outstanding $334,135.4mm.

### 2b. Marketable vs non-marketable summary — MSPD Table 1 (VERIFIED)

Endpoint (HTTP 200):
`https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd/mspd_table_1`
Fields include `security_type_desc`, `security_class_desc`,
`debt_held_public_mil_amt`, `intragov_hold_mil_amt`, `total_mil_amt` ($mm).
2026-08-31 ($bn): Marketable **31,828.0** (Bills 7,248.1 / Notes 16,218.4 /
Bonds 5,525.3 / TIPS 2,152.7 / FRN 680.0); Nonmarketable **8,347.6** (Government
Account Series 8,106.2 / SLGS 78.9 / Savings 146.9 / Domestic 10.7 / Other 4.9);
**Total Public Debt Outstanding 40,175.6**. Also gives the debt-held-by-public
vs intragovernmental split per class. (mspd_table_2 is the debt-*limit* table —
not ownership; skip for this purpose.)

### 2c. NY Fed SOMA holdings by CUSIP (VERIFIED)

- API docs: `https://markets.newyorkfed.org/static/docs/markets-api.yml`
  (HTTP 200) → paths `/api/soma/tsy/get/asof/{date}.{format}`,
  `/api/soma/tsy/get/cusip/{cusip}.{format}`,
  `/api/soma/asofdates/latest.{format}`; formats json/xml/csv.
- Verified: `https://markets.newyorkfed.org/api/soma/tsy/get/asof/2026-09-30.csv`
  → HTTP 200, **433 CUSIPs**, columns: As Of Date, CUSIP, Security Type
  (Bills/FRNs/NotesBonds/TIPS), Maturity Date, Coupon, **Par Value ($)**,
  Current Face Value, Inflation Compensation, **Percent Outstanding**
  (SOMA share of that CUSIP), Change From Prior Week/Year.
- Total par **$4,458.1 bn** @ 2026-09-30. **Weekly** (as-of Wednesdays);
  latest as-of date from `/api/soma/asofdates/latest.json` → 2026-09-30.
- Agency side exists too: `/api/soma/agency/get/asof/{date}.{format}`.

### 2d. Proposed slice cube

Dimensions:
1. **Product** — from Table III `security_class1_desc`: Bills / Notes / Bonds /
   TIPS ("Inflation-Protected Securities") / FRNs ("Floating Rate Notes").
   (Or maturity-date bands computed from `maturity_date` − `record_date`:
   ≤1Y, 1–2Y, 2–3Y, 3–5Y, 5–7Y, 7–10Y, 10–20Y, 20–30Y, 30Y+.)
2. **Holder bucket** — SOMA (NY Fed CUSIP file: `Par Value × Percent Outstanding`
   per CUSIP, aggregated) / Public marketable (Table III outstanding − SOMA) /
   Non-marketable intragovernmental (Table III non-marketable rows, or Table 1
   GAS/SLGS/savings).
3. **Measure** — USD notional ($bn) AND % of total outstanding (denominator =
   Table 1 "Total Public Debt Outstanding" for the all-debt view, or total
   marketable for the marketable-only view).

Fetch plan: one Table III pull per month (~0.9 MB) + one SOMA CSV per week
(~48 KB) + Table 1 summary. All keyless.

**Double-counting traps:**
- SOMA holdings are *part of* marketable outstanding — subtract, don't add.
- TIPS: `outstanding_amt` vs `inflation_adj_amt` — pick one concept and label it
  (outstanding = original face; inflation-adjusted = current).
- Bills appear as "Bills Maturity Value" (discount basis) — don't mix with
  coupon-bearing par.
- OFS-2 mutual-funds-include-MMFs rule (above) applies if OFS-2 is ever joined
  to this cube.
- Table III non-marketable rows are aggregates by *fund* (GAS), not CUSIPs —
  no CUSIP-level SOMA math applies there (SOMA holds only marketables).

---

### Source index (all HTTP 200 in-session)

- FRED keyless CSV: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>`
- DDP-FRED crosswalk: `https://www.federalreserve.gov/data/documents/DDP-FRED%20Data%20Series%20Crosswalk.csv`
- Z.1 bulk: `https://www.federalreserve.gov/datadownload/Output.aspx?rel=Z1&filetype=zip` (37.6 MB zip; SDMX XML inside)
- Board Series Analyzer: `https://www.federalreserve.gov/apps/fof/SeriesAnalyzer.aspx?s=<MNEMONIC>&t=`
- TIC: `https://ticdata.treasury.gov/Publish/slt_table{1,2,3,4,5,6}.txt`
- OFS-2: `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/tb/ofs2_estimated_ownership_treasury_securities`
- MSPD T3: `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd/mspd_table_3`
- MSPD T1: `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd/mspd_table_1`
- SOMA API spec: `https://markets.newyorkfed.org/static/docs/markets-api.yml`
- SOMA tsy CUSIP: `https://markets.newyorkfed.org/api/soma/tsy/get/asof/{yyyy-MM-dd}.csv`
