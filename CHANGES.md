## 2026-10-03 — UI+data batch: central-bank watch, command palette, alert-tuning UI, briefcheck view (subagent)

**Central-bank watch tab (`central`).** New `ui/js/panels/central.js` + TABS/nav/mount
wiring: (a) FOMC meeting calendar 2026–2027 with day-countdowns to each decision
day — dates vendored as statics, VERIFIED 2026-10-03 against
https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm ("Last Update:
September 16, 2026"): 2026 → Jan 27-28, Mar 17-18*, Apr 28-29, Jun 16-17*,
Jul 28-29, Sep 15-16*, Oct 27-28, Dec 8-9*; 2027 → Jan 26-27, Mar 16-17*,
Apr 27-28, Jun 8-9*, Jul 27-28, Sep 14-15*, Oct 26-27, Dec 7-8* (* = with SEP);
(b) implied Fed-funds path from the SOFR 3M futures strip — 7 new config.yaml
cycle series (`sofr-v26` … `sofr-h28`, Yahoo `SR3V26.CME` … `SR3H28.CME`, ALL
verified live 2026-10-03; implied 3M = 100 − price), rendered as a small SVG
path chart + table. Refreshed on the 15-min cadence. Contract codes roll:
strip covers Oct-26 → Mar-28 and must be re-coded as expiries pass.
Fed speaker calendar SKIPPED — no free machine-readable source verifies.

**Command palette (`ui/js/palette.js`).** Cmd+K / Ctrl+K (or `/` when not
typing) opens a fuzzy-search overlay across tabs, panels, and series; Enter
navigates (`#/tab`), up/down + Esc work, click selects. The index is built
live by main.js from the nav DOM + dashboard cycle series + scorecard rows +
futures list — nothing hardcoded. Styles in terminal.css, mobile-compact.

**Alert-tuning UI (`ui/js/panels/alerts.js`, `#panel-alerts` on mkt tab).**
Lists alert types from `GET /api/alerts/config` (anomaly, trend) with a
threshold-multiplier input (0.1–10) and mute toggle each; changes PUT to
`/api/alerts/config` immediately with transient saved ✓ / error state.

**Briefcheck view (`ui/js/panels/briefcheck.js`, `#panel-briefcheck` on mkt
tab).** Reads `GET /api/briefcheck`; shows checked count, timestamp, and the
mismatch table (metric, briefing vs terminal, deviation); 404 renders a quiet
"no cross-check yet" empty state. Refreshed on the 15-min cadence.

**Market internals SKIPPED (data).** No free machine-readable source for NYSE
advance/decline, new highs/lows, or up/down volume (TRIN) verifies live
2026-10-03: Nasdaq exposes no public advance/decline endpoint; NYSE/WSJ/
MarketWatch are JS-walled; the TradingView scanner endpoint is unreachable
from the build network (connection timeout); CNN's Fear & Greed JSON API
bot-blocks ("I'm a teapot"). Nothing faked — no tab added. Existing breadth
proxies remain: equal-weight/SPX ratio, 5d cross-sectional dispersion, CBOE
put/call ratios. Revisit if a clean source appears.

**Also:** `FUTURES` now exported from `ui/js/panels/futures.js` (palette
index); `getAlertConfig`/`putAlertConfig`/`getBriefcheck` added to
`ui/js/api.js`. New tests `collector/tests/test_central.py` (4 tests:
FOMC schedule pinned to the verified federalreserve.gov dates, weekday/
chronology guards, SOFR-strip config shape, implied-rate arithmetic).
`node --check` clean on all touched JS.

## 2026-10-03 — Reliability batch: Postgres persistence, boot catch-up, alert tuning, briefcheck (subagent)

**1. Postgres persistence — `collector/src/collector/store.py`.**
`Store` now honors `DATABASE_URL`: when set it connects via psycopg and uses
`%s` placeholders; otherwise SQLite at `db_path` exactly as before. All
methods (`upsert_points`, `points`, `put_doc`/`doc`, `record_success`,
`record_error`, `prune_outside_range`, `status`/`statuses`) behave identically
on both backends. psycopg is an optional extra (`pip install -e
".[postgres]"`) so SQLite stays the zero-dependency default; a clear
RuntimeError is raised if DATABASE_URL is set without it.
Enabling persistence: provision a free Postgres (Neon or Supabase), set
`DATABASE_URL` on Render (and install the extra in the Docker image), and
the terminal's history — radar, risk engine, universe graphs — survives
deploys instead of rebuilding from empty `/tmp/bloom.db` each time.
Verified: 5 new tests drive the PG path through an in-memory fake psycopg
(placeholder style, upsert round-trip, docs, statuses, corrupt-doc
handling, missing-driver error).

**2. Scheduler boot catch-up — `scheduler.py` + `runner.py`.**
`run_fetcher` now records each success in the `job_runs` doc. On
`register_jobs`, any staggered job whose last run is missing or older than
its cadence gets an early first run (start + 60s, 75s apart, in dependency
order: risk → country_risk → xcorr → voldash → movers → home_radar → …)
instead of the hardcoded +300s…+4200s offsets. Jobs with a recent run keep
their configured offsets. Existing scheduler tests untouched (they assert
intervals/ids only); 3 new tests cover catch-up ordering, recent-run
preservation, and job_runs persistence.

**3. Alert tuning backend — `collector/src/collector/alert_config.py` (NEW).**
Per-type config persisted in the `alert_config` doc, seeded from the two
digest kinds notify.py emits:
- `anomaly` — Anomaly alerts (|z| ≥ 2.2σ), `threshold_mult` 1.0, `muted` false
- `trend` — Trend alerts (1m move ≥ 1.15σ), `threshold_mult` 1.0, `muted` false
`threshold_mult` scales the trigger threshold in `build_digest`
(2.0 ≈ half as many alerts); `muted` types are filtered in
`push_new_alerts` (still counted as seen so they don't queue while muted).
New endpoints: `GET /api/alerts/config` → `{"types": [{id, label,
threshold_mult, muted}]}`; `PUT /api/alerts/config` with
`{id, threshold_mult?, muted?}` → `{"type": {...}}`, 400 on unknown id or
out-of-range multiplier (0.1–10). CORS allow_methods gained PUT. 6 new
tests incl. end-to-end threshold scaling and muted-send suppression.

**4. briefcheck.py (NEW, repo root) — briefing-vs-terminal cross-check.**
Standalone stdlib-only script for an operator machine (OFF Render):
`python briefcheck.py --briefing-html PATH --base-url
https://os-bloom.onrender.com [--tolerance-pct N]`. Parses the briefing's
tables generically by header row, maps ~45 metrics (rates/curve, plumbing,
vol, conditions, ETFs) to `/api/scorecard` + `/api/series`, compares with
per-unit tolerances (10bp yields/spreads, 5bp OAS, 1% levels, 5% dollar
stocks, 2% ratios), prints a JSON report
`{checked_at, checked_count, mismatches: [{table, metric, briefing_value,
terminal_value, deviation, unit}]}` and exits 1 on any breach.
Live-verified 2026-10-03 against the real briefing + production terminal:
23 metrics checked, 16 mismatches flagged — including the briefing's stale
SPR (400.0 vs 283.767 MMBbls) and stale front-end rates (SOFR 5.31 vs 3.87,
UST 1M 5.25 vs 4.06; briefing carries the old rate regime).
New endpoints: `POST /api/briefcheck` accepts that JSON report (stores doc
`briefcheck_latest`) → `{"stored": true}`; `GET /api/briefcheck` returns it
or 404 when none posted. 8 new tests (mocked terminal).
Skipped: briefing tables 6–8 (futures z-scores have no terminal equivalent;
auction dynamics are event-specific not current levels; econ calendar is
events not values).

Tests: full suite 413 passed / 15 failed / 2 skipped — the 15 failures are
byte-identical to the pre-change pristine baseline (test_batch6_finra_ice
fixture FileNotFoundErrors, test_tsv), zero new failures. +22 new tests.

## 2026-10-03 — Batch 8: MARKET STRUCTURE universe (subagent)

Second coverage map on the batch-7 generic universe engine (no new fetcher
code — `fetch_universe_capex` / `fetch_universe_graph` take `universe_id` and
were called with `"market_structure"`).

**Universe** — `collector/src/collector/data/market_structure.json` (NEW):
55 companies, 7 verticals, 29 press-reported edges (~$58.3B in attributed
deal value), 2 revenue aggregates (MSB6 money-center banks, EXCH4 exchanges),
4 risk notes.

Roster by vertical (all tickers + 22 CIKs verified live 2026-10-03 — Yahoo
chart API HTTP 200; CIKs via sec.gov/files/company_tickers.json):
- OMS providers (Trading tech): Bloomberg AIM (priv), Charles River/State
  Street (STT 0000093751), BlackRock/Aladdin (BLK 0002012383), SS&C Eze
  (SSNC 0001402436), SimCorp (priv, Deutsche Börse unit), Enfusion (priv),
  Broadridge (BR 0001383312), Deutsche Börse (DB1.DE, public, no US CIK)
- ATS / dark pools & venues (Venues): UBS ATS, Sigma X (GS), MS Pool (MS),
  Barclays LX (all priv), Citadel Connect (priv), Luminex (priv consortium),
  IntelligentCross (priv), Tradeweb (TW 0001758730), MarketAxess
  (MKTX 0001278021)
- Market makers (Liquidity): Citadel Securities (priv), Virtu
  (VIRT 0001592386), Jane Street, Susquehanna/SIG, IMC, Optiver, Flow
  Traders, Hudson River Trading (all priv)
- Non-bank liquidity providers (Liquidity): XTX Markets, Jump, Tower
  Research, DRW, XR Trading, Maven Securities (all priv)
- Quant / prop (Quant): Renaissance, D.E. Shaw, Two Sigma, Citadel,
  Millennium, PDT Partners, Winton (all priv)
- Retail brokers / wirehouses (Brokers & banks): Schwab (SCHW 0000316709),
  Interactive Brokers (IBKR 0001381197), Robinhood (HOOD 0001783879),
  Morgan Stanley (MS 0000895421), Goldman Sachs (GS 0000886982), JPMorgan
  (JPM 0000019617), Bank of America (BAC 0000070858), Citigroup
  (C 0000831001), Barclays (BCS 0000312069), UBS (UBS 0001610520), LPL
  (LPLA 0001397911)
- Exchanges (Venues): CME (CME 0001156375), ICE (ICE 0001571949), Nasdaq
  (NDAQ 0001120193), Cboe (CBOE 0001374310), MEMX (priv)

**Edges** — 8 confirmed acquisitions: State Street→Charles River $2.6B
(2018-07, State Street PR); Deutsche Börse→SimCorp ~€3.9B (2023-09, SimCorp
PR); Virtu→ITG $1.0B (2019-03, Virtu PR); Virtu→KCG $1.4B (2017, company
history, reported); Schwab→TD Ameritrade $22B (2020-10, Schwab); Morgan
Stanley→E*TRADE $13B (2020-10, Nasdaq); Cboe→Bats $3.4B (2017-02, Cboe IR);
Nasdaq→Adenza $10.5B (2023-06, Nasdaq). 11 MEMX invest edges (6 founding
2019: BofA/Schwab/Citadel Sec/MS-via-E*TRADE/UBS/Virtu; 5 later: BLK/Citi/
JPM/GS/Jane Street; Markets Media/Wikipedia). 4 PFOF edges (Citadel
Sec/Virtu ↔ Robinhood/Schwab; Rule 606 disclosures). 4 ATS-ownership edges
(UBS→UBS ATS, GS→Sigma X, MS→MS Pool, Barclays→Barclays LX). Citadel→
Citadel Securities affiliate; MS→PDT Partners 2013 spinoff. Acquired/
delisted counterparties (ITG, KCG, TD Ameritrade, E*TRADE, Bats, Adenza)
are edge endpoints only, not nodes.

**Risk notes** — OMS market share (Bloomberg Intelligence European
Institutional Equity Trading Study 2026: Bloomberg AIM 26.4%, Charles
River 15.4%, Aladdin 11%, SS&C Eze 7.7%, SimCorp 5.5%, Enfusion 3.3%);
PFOF wholesaler concentration; MEMX founding context; infrastructure
consolidation (exchanges/custodians buying the software layer).

**Pipeline** — scheduler jobs `ms_capex` / `ms_graph` (weekly, after the AI
jobs); docs `market_structure_capex` / `market_structure_graph`; series
`market_structure:<TICKER|MSB6|EXCH4>:<metric>`; dashboard panel `ms_flow`;
insights `_ms_universe_note` + digest bullet + digest_id keys
(`ms_neg_fcf`, `ms_deals_bn`) — named to avoid the batch-6
`_market_structure_note` (FINRA/TRACE data). Honest flag behavior: the
generic XBRL flags degrade sensibly for banks — only `cashflow_negative`
can fire; `capex_intensity_high` / `funding_gap_large` thresholds are never
hit by banks/brokers, so no forced AI-style flags.

**UI** — `ui/js/panels/ai_flow.js` is now the generic universe renderer:
`renderUniverse(doc, opts)` with per-universe options (hubs, default ego,
chart title/note, legend) and per-universe state; `renderUniverseSelector`
adds an AI BUILDOUT / MARKET STRUCTURE toggle in the HYPER tab (panel
renamed "COVERAGE MAPS — UNIVERSE & MONEY FLOW") — no new tab. New edge
kinds styled (acquire/invest/pfof/owns/spinoff/affiliate); null deal
amounts guarded; group colors added (Trading tech, Venues, Liquidity,
Quant, Brokers & banks); aggregate chart title/note parameterized
(MS shows revenue, not capex); Deutsche Börse renders as public-without-
XBRL ("No XBRL financials — deals only").

**Config (needs Harry's merge)** — `config-snippet-batch8.yaml`:
cadences `ms_capex`/`ms_graph` (604800) + 22 `ms-*` cycle series
(ms-stt…ms-cboe; `ms-ms` = Morgan Stanley since `msft` is Microsoft).

**Tests** — `test_market_structure.py` (NEW, 10 tests: schema, verified
ticker/CIK pin, edge endpoints, aggregates, graph job, panels, insights
note, JS syntax, no-AI-hardcoding, snippet ids). Full suite: 368 passed,
1 skipped.

**Honest gaps** — private-company financials (Citadel Securities, Jane
Street, XTX, etc. have no XBRL); single-name credit/CDS (no free feed);
PFOF dollar amounts per wholesaler (606 disclosures are qualitative
routing tables); ATS volume market share (FINRA ATS transparency data is
free but not yet wired — candidate for a later batch); Flow Traders kept
private-kind (Euronext listing exists but no US CIK, XBRL not applicable).

## 2026-10-03 — Batch 9: TSV (Tokenized Securities Venue) universe (subagent)

SEC Release No. 34-106402 (File No. 4-927), issued 2026-09-17: 5-year
Innovation Exemption (through 2031-09-17) creating the Tokenized Securities
Venue category — US persons running permissioned AMM pools in tokenized NMS
stock, exempt from the "exchange" definition; liquidity providers ("Covered
Firms") exempt from the "dealer" definition. Caps: Tier 1 = 75 symbols at
0.25% of prior-month ADV (S&P 500 / Russell 1000 / ETPs >$2M); Tier 2 = 250
symbols at 2.5%. Conditions: 30-day public website notice (Condition C) +
written SEC notice within 1 business day; 30-day issuer veto on third-party
tokens; halts with listing exchange; trades within 10 min; no leverage;
equal rights (dividends/votes/liquidation); secondary only; auditable
public smart contracts on a permissionless ledger. Press release 2026-90;
comments open under File 4-927. Earliest trading ~2026-10-17.

**Universe** — `collector/src/collector/data/tokenized_securities.json` (NEW),
generic coverage-map schema (universe_id "tokenized_securities"): 17
companies, 5 verticals (TSV operators watchlist / tokenized issuers /
Covered Firms / market infra / related listed), 3 press-reported edges,
order facts embedded. Reuses fetch_universe_capex + fetch_universe_graph
(scheduler jobs tsv_capex/tsv_graph, weekly). All 6 tickers + 6 CIKs
verified live 2026-10-03.

**Watch job** — `fetchers/tsv_watch.py` (NEW), weekly: scans SEC press
releases listing for "tokeniz" items + Federal Register API for TSV
documents; writes doc "tsv_watch". Honest design note: NO central SEC
registry exists — Condition C notices live on operator websites, so the
job checks what is machine-trackable and says so in the payload caveat.

**Operator status (verified 2026-10-03, nothing invented):** NO TSV notice
filed anywhere as of ~2026-09-20 (checked Securitize, Superstate, Oasis
Pro, Coinbase, Robinhood, Kraken); SEC staff expect first notices "next
quarter". Positioned: Coinbase, Robinhood (caps constrain US launch),
Kraken, Bullish (Equiniti acquisition), Backpack Securities (17 tokenized
stocks on Solana, US excluded), Securitize, Superstate, Oasis Pro.
Preparing: BTCS Inc./Imperium (Covered Firm prep announced 2026-09-28, no
launch date). Issuers: Ondo, Dinari, Backed (non-US person — ineligible as
TSV), Figure. Excluded in current form: Robinhood EU tokens, Kraken xStocks
(synthetics/derivatives, no shareholder rights).

**UI** — `ui/js/panels/tsv.js` (NEW): exemption explainer, venue
watchlist with status chips, weekly scan results, key facts; wired into
HYPER tab (panel-tsv section) via main.js/index.html/terminal.css.

**Gaps:** no free machine-readable Condition C notice feed (firm websites
only — browser needed); private-company financials; single-name credit.

## 2026-10-03 — Batch 6: FINRA + ICE (subagent)

Harry's steer: FINRA TRACE fixed-income data is the top priority — go deep.
Every external source below was verified live 2026-10-03 before inclusion;
login-only/paid sources are documented as gaps, never faked.

**1. FINRA TREASURY TRACE AGGREGATES** — `collector/src/collector/fetchers/trace_treasury.py`
(NEW), daily. Free on the FINRA CDN, no auth:
`https://cdn.finra.org/trace/treasury-aggregates/daily/ts-daily-aggregates-YYYY-MM-DD.xlsx`
(published ~8pm ET each trading day, history since 2023-02-13) plus the
monthly files `.../monthly/ts-monthly-aggregates-YYYY-MM.xlsx` (same layout
minus VWAP) for backfill. Parses Bills/FRNs/Nominal Coupons/TIPS/Total —
total par ($bn), total trades, bills/coupons/TIPS par. Daily job probes back
6 days (weekends/holidays); 24-month monthly backfill on an empty store.

**2. FINRA TRACE MONTHLY VOLUME** — `fetchers/trace_monthly.py` (NEW), monthly.
`https://cdn.finra.org/trace/volume/monthly/TRACE_Public_Monthly_Report_YYYY-MM.xlsx`
— total + average-daily volumes for CORP, AGCY, ABS, ABSX, CMO, MBS, TBA
(trades and par $M), history back to 2017-01, published 3rd business day
after month-end. Full backfill to 2017 on an empty store.

**3. ICE VANTAGE DAILY AGGREGATE** — `fetchers/ice_star.py` (NEW), daily.
`https://vantage.interactivedata.com/aggregate/download?date=YYYY-MM-DD&vdl=aggregate_download`
returns a ZIP (no auth) with the FINRA-ICE Structured Trading Activity
Report (agency pass-thru/CMO by issuer; non-agency/ABS/CLO by investment
grade; trade counts + $ trades in 000s) and pricing tables. Parses agency vs
non-agency par ($bn) and trade counts; the pricing tables are documented as
available-but-not-parsed (all-zero on rolled contracts). 30-day backfill on
an empty store. Verified for 2023-09-18, 2024-06-12, 2025-01-15.

**4. FINRA SHORT INTEREST** — `fetchers/finra_short.py` (NEW), weekly poll of
the twice-monthly consolidated file
`https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv`
(pipe-delimited; settlement 15th + month-end, published ~8 business days
later). Total short interest (all listed) plus per-ticker series for the six
hyperscalers (MSFT/NVDA/AAPL/AMZN/GOOGL/META); the `finra_short` doc feeds
the HYPER tab cards (short shares, days-to-cover, change %) via panels.py.
This closes the "no free short-interest feed" gap the HYPER tab previously
declared. 12-settlement backfill on an empty store.

**5. FINRA MARGIN STATISTICS** — `fetchers/finra_margin.py` (NEW), monthly.
`https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx`
— one static file updated in place, history to 1997-01: debit balances and
free credit balances ($M). Full-history refresh every run (idempotent).

**Wiring** — new shared stdlib-only xlsx reader (`fetchers/xlsx.py`;
openpyxl is not a dependency). New `external: true` flag on CycleSeriesCfg:
dedicated jobs write `cycle:<id>` directly and the cycle job skips them.
Scheduler: 5 new jobs (trace_treasury, trace_monthly, ice_star, finra_short,
finra_margin) → 34 total. New MARKET STRUCTURE cycle tab on the POS tab
(#cycle-struct; also added the missing #cycle-quant render target — the
QUANT tab existed in config but rendered nowhere). Insights digest gains a
"Market structure" bullet (margin debt, Treasury/corp TRACE, short interest).
Config snippet: `batch6-config-snippet.yaml` (cadences, 4 NY Fed corporate
dealer series, all new cycle_series, STRUCT tab, 2 ICE BofA FRED effective-
yield series — BAMLC0A0CMEY IG, BAMLH0A0HYM2EY HY; verified the existing
`hy-oas` label is correct — BAMLH0A0HYM2 *is* the HY OAS).
Tests: 13 new (batch-6) + 1 insights + 1 cycle-external; full suite
349 passed, 1 skipped.

**Documented gaps (verified, not built):** per-trade TRACE feeds are paid
entitlements (~$750/mo/dataset); FINRA ATS/OTC transparency current weekly
data is OAuth-only via api.finra.org (legacy static archives end 2016);
FINRA Bond Market Activity data API 302s to an ews.fip.finra.org login;
FINRA Query API needs Gateway entitlement; ICE Vantage /cds and /curves need
CAS auth; /movers is client-rendered JS.

## 2026-10-03 — Batch 5: market radar, hyperscaler desk, analyst-grade chat (subagent)

Harry asked for (a) a HOME-page Bloomberg-style mission-control radar —
dynamic map + heatmap from multi-indicator historical percentiles, green to
red; (b) hyperscaler issuance/securities/flows tracking like a Bloomberg AI
dashboard; (c) end-to-end analyst chat capabilities. Every new external
source below was verified live before inclusion.

**1. MARKET RADAR** — `collector/src/collector/fetchers/home_radar.py` (NEW),
daily, compute-only (no HTTP). 17 indicators, each with current value +
percentile rank vs trailing 252 observations, direction-aware coloring
(high = red for stress/vol/inflation; inverted for vol term structure and
equal-weight breadth; symmetric extremes for TLT momentum; absolute-depth
bands for SPX drawdown so a calm year can't paint green as red; the risk
engine's 0-100 scores and CTA z-scores keep their native scales):
volatility (VIX, VIX3M−VIX, SPX 21d realized vol via `rvol:spx:21d`, VVIX),
stress (basis-trade, auction, ON RRP take-up in $bn, HY OAS), returns
(SPX vs 52w high, TLT 60d momentum, CTA max |z|), breadth (equal-weight vs
SPX, cross-sectional 5d dispersion from the movers universe), inflation
(5Y/10Y breakeven, CPI YoY), funding (SOFR−IORB bp spread). Writes the
`home_radar` doc {as_of, regime, verdict, indicators[]} with weekly-sampled
1Y history tails for sparklines; regime/verdict come from the risk engine.
One missing series degrades its cell, never the job. New `radar` panel on
/api/dashboard. UI: MARKET RADAR hero at the top of the MKT tab
(`ui/js/panels/radar.js`) — regime badge + verdict strip, world choropleth
reusing the risk-map geometry/buckets (`riskmap.js` now exports
COLORS/featurePath/project), and a grouped heatmap grid (VOLATILITY /
STRESS / RETURNS / BREADTH / INFLATION / FUNDING); tap a cell for its 1Y
sparkline. Scheduler: `home_radar` daily, starts 30 min after deploy.

**2. HYPERSCALER DESK** — `collector/src/collector/fetchers/hyperscaler.py`
(NEW), weekly. (a) ISSUANCE: scans each issuer's SEC submissions feed
(data.sec.gov, free, keyless, contact UA like the thirteenf fetcher) for
debt-offering filings (424B2/424B3/424B5/FWP/S-3ASR) in the last 90 days;
each 424B2's prospectus is fetched and parsed best-effort for per-tranche
coupon, maturity, and principal amount. CIKs verified live 2026-10-03 via
SEC company_tickers.json: AAPL 0000320193, GOOGL 0001652044, MSFT
0000789019, AMZN 0001018724, META 0001326801, ORCL 0001341439. Live finds:
Alphabet 424B2 2026-08-07 (parsing verified against the real filing — 8
tranches, 4.500% 2028 → 6.500% 2066), Amazon 424B3 2026-08-18. The EDGAR
full-text search API (efts.sec.gov) 403s automated access — documented, and
the submissions-API scan is the live path instead. (b) EQUITIES: the six
names added as Yahoo single-symbol cycle series (config snippet: googl,
amzn, meta, orcl, msft, aapl); equity cards (last, 1m change, 90d spark)
built from stored cycle history, zero extra HTTP. (c) SECURITIES/FLOWS —
honest limits shown in the UI, not faked: no free single-name bond
spread/CDS or short-interest feed exists (S3, S&P Global, Markit are paid);
holder flows are covered quarterly by the 13F watchlist. New `hyper` panel
on /api/dashboard; new HYPER tab (`ui/js/panels/hyper.js`): equity cards +
debt-issuance table with SEC links. Scheduler: `hyperscaler` weekly.
Config snippet adds cadences (home_radar 86400, hyperscaler 604800) and the
six equity series.

**3. ANALYST-GRADE CHAT** — `collector/src/collector/chat.py` (modified).
(a) Every market question now gets a server-side analyst pack prepended:
regime + verdict, basis/auction stress, recession probability, vol table
highlights (richest/cheapest implied), top σ-movers 5d/20d, country-risk
red/green extremes, latest hyperscaler issuance, this week's macro
calendar, top headlines — compact (~1.5k tokens, well under the 3k
budget). (b) Company Q&A: any SPX/NDX constituent ticker in the question
(matched against the vendored lists) gets a card with its 5d/20d
sigma-moves and returns from the weekly movers run plus vol context; the
movers doc now also stores the full universe map (`all`) and
`dispersion_5d` (also upserted to `movers:dispersion-5d` history for the
radar). (c) Honest limits: the system prompt now states there is NO
earnings/estimates/fundamentals feed — EPS/valuation questions get a plain
"price and positioning only" answer. Endpoint path, auth, and model
unchanged (Gemini generateContent REST). Tests mock the Gemini call and
assert the pack is built and injected.

Tests: 26 new (test_home_radar.py 10, test_hyperscaler.py 6,
test_chat_batch5.py 10); full staged suite 117/117 green. UI verified
headless in node (regime strip, cells, map, sparkline, hyper cards,
issuance table, empty states).

## 2026-10-03 — Batch 4: Bloomberg/CBOE institutional visuals (subagent)

Harry sent screenshots of the Bloomberg/CBOE institutional visuals he follows
and asked for the data and visuals as terminal features. Every new external
source below was verified live before inclusion.

**1. SINGLE-STOCK SIGMA MOVERS** — `collector/src/collector/fetchers/movers.py`
(NEW), weekly. Replicates the tasty*live* "10 largest 5-day / 20-day std-dev
moves" visual for S&P 500 + Nasdaq 100. Constituent lists vendored into the
repo (`collector/src/collector/data/sp500.txt`: 503 tickers from a
daily-updated public CSV, verified live;
`collector/src/collector/data/ndx100.txt`: 101 tickers from Wikipedia's
constituent page, verified 2026-10-03). Prices via Yahoo chart API
(range=6mo), ~600 requests with 0.35s polite spacing (~8 min), weekly
Saturday run; per-symbol isolation (a dead symbol or 429 degrades that
symbol only). Stooq was evaluated and rejected — it now serves a JS
proof-of-work challenge to automated fetches. Per symbol: 5d return /
trailing-20d sigma and 20d return / trailing-60d sigma; top-10 positive and
negative per (index, window) written to the `movers` doc. New MOVERS tab
(`ui/js/panels/movers.js`): green/red bar lists on a std-dev axis, mirroring
the screenshot. Store keys: `movers` doc + `movers:asof` history.

**2. SOFR OPTIONS STRIKE POSITIONING** — gap, documented honestly. No fetcher
built: cmegroup.com IP-blocks automated access outright ("This IP address is
blocked due to suspected web scraping activity"), including the CmeWS quote
endpoints. The Bloomberg "Most Active SOFR Option Strikes" / "SOFR Options
Open Interest" visuals cannot be replicated from Render. Paid alternatives:
CME DataMine, Barchart OnDemand. Not faked with put/call ratios (already
tracked separately).

**3. TREASURY FUTURES OPEN INTEREST** — `collector/src/collector/fetchers/cftc.py`
(staged replacement of the live-main file; original net-noncommercial path
kept byte-faithful). The Socrata 6dca-aqww dataset carries `open_interest_all`
(verified live 2026-10-03); new `fetch_open_interest()` + `parse_open_interest()`,
wired through a new `cftc_oi:` CycleSeriesCfg field in `fetchers/cycle.py`.
Config snippet adds `oi-ust-2y/5y/10y/30y` (codes 042601/044601/043602/020601)
and a QUANT tab panel "TREASURY FUTURES OPEN INTEREST (CFTC)" overlaying 10Y
futures OI on the 10Y yield (new `us10y` FRED DGS10 cycle series) — the
Bloomberg "growing short base" visual.

**4. VOL DASHBOARD** — `collector/src/collector/fetchers/voldash.py` (NEW),
daily compute job (zero HTTP; implied legs arrive via new cycle `yahoo:`
series). Yahoo symbols verified live 2026-10-03: ^VVIX, ^GVZ, ^OVX, ^VXSLV all
serve chart data; ^RVX 404s (dead — RTY row is realized-only); ^MOVE is dead
(documented batch 3). Table rows [SPX, RTY, QQQ, GLD, USO, SLV, TLT, LQD, HYG]:
1M implied, weekly change (pts), 1Y percentile of implied, 21d ann. realized
(from our price history), implied-minus-realized spread, 1Y percentile of the
spread. Plus VIX-vs-VVIX history and 60d rolling spot-vol betas (VIX on SPX,
VVIX on VIX) with trailing histories. New VOL tab (`ui/js/panels/vol.js`):
CBOE-style table with green (<=10th) / red (>=90th) percentile cells,
implied-vs-realized scatter with Cheap/Rich quadrants, VIX-vs-VVIX chart,
spot-vol beta gauges. The vol regime line ("N vol indices in the top decile
— protection is rich") feeds the insights digest and digest_id hash.

**5. CBOE-STYLE CORRELATION MATRIX** — `fetchers/correlation.py` extended.
Universe grew to 30 series (new: RTY/IWM, EEM, USO, copper HG=F, GBP/USD,
SLV, TLT, US 30Y via FRED DGS30 — all Yahoo/FRED symbols verified live
2026-10-03). The matrix is now the CBOE digest's grouped subset
(Equities 5 / Corporate Credit 2 / Rates 2 / Commodities 3 / FX 3) with group
headers in the `xcorr` doc; regime pairs extended to 11 with the six CBOE
time-series pairs first (Equity-Rates, Equity-CorpBond, Equity-Oil,
Equity-Gold, Equity-FX x2). Each pair's trailing-1y 60d-rolling-corr history
is now embedded in the doc (`pair_hist`) for the UI. `ui/js/panels/xcorr.js`
extended: triangular lower-triangle matrix (diagonal skipped, like CBOE) with
>=80% cells highlighted, plus the six pair time-series charts.

**6. CRE/CMBS STRESS PROXY** — config-only: `cre-delinq` cycle series
(FRED DRCRELEXFACBS, commercial real-estate loan delinquency, quarterly;
verified live: Q2 2026 at 1.53%, updated Aug 25 2026) with a RISK tab panel
"CRE STRESS (FRED, QUARTERLY)". No free FRED CMBS-delinquency series exists;
Trepp is paid. The Bloomberg "Centre Square bonds at pennies on the dollar"
visual is paid data — documented as a gap, not faked.

Scheduler: `movers` (weekly, starts +1500s) and `voldash` (daily, starts
+1200s), both with .get() cadence guards. UI: new VOL + MOVERS tabs
(tabs.js, index.html, main.js), extended xcorr.js, new movers.js/vol.js,
terminal.css additions for the vol table. Insights: vol regime + richest/
cheapest implied readings in the newsletter bullets; vol_regime and
movers_asof hashed into digest_id. Config merge needed (parent):
`batch4-config-snippet.yaml` — cadences (movers, voldash), 17 new
cycle_series, 4 tab-row additions. Tests: 91/91 green (74 prior + 17 new:
test_movers 5, test_cftc_oi 5, test_voldash 4, test_correlation_batch4 3).

## 2026-10-03 — Batch 3: hedge-fund watchlist breadth, GSE/MBS, correlations, vol, options & futures positioning (subagent)

Harry asked for: Millennium on the 13F watchlist; more GSE holding data; ETF
flows; correlations; all volatility; option positioning; futures positioning
breadth. Every new external source below was verified live before inclusion.

**1. MILLENNIUM 13F** — CIK **0001273087** verified live via EDGAR
(data.sec.gov/submissions: "MILLENNIUM MANAGEMENT LLC", latest 13F-HR filed
2026-08-14). No code change needed (thirteenf.py iterates cfg.watchlist);
config snippet `millennium-13f-snippet.yaml` appends it to the watchlist.

**2. GSE/MBS** — `collector/src/collector/fetchers/gse.py` (NEW), monthly.
Fannie Mae Monthly Summary PDFs live at the predictable, date-based URL
`https://www.fanniemae.com/media/document/pdf/MMDDYY.pdf` (MMDD = month-end;
301-redirects to /media/<id>/display, serves the PDF to the honest UA when
redirects are followed — no browser impersonation needed). Table 3 "Retained
Mortgage Portfolio Activity" gives the monthly retained-portfolio ending
balance ($M). Freddie Mac Monthly Volume Summaries live at
`https://www.freddiemac.com/investors/financials/pdf/MMYYmvs.pdf`; Table 2
gives the mortgage-related investments ending balance, Table 3 the agency
securities component. Each PDF carries ~13 months of history; the job probes
the last 4 month-ends newest-first (release lag ~25d). Parsing is via
`pdftotext -layout` (poppler-utils) — pypdf interleaves the side-by-side
tables and is unusable, so the staged Dockerfile now installs poppler-utils;
without it the job raises a clear error and degrades (empty GSE panel, no
crash). Parser hardened against same-shaped tables (Freddie Table 1, Fannie
Tables 7/8 footnotes, debt tables) via section bounds + column splitting.
Verified against the real Aug 2026 PDFs: Fannie $93.3B→$172.6B,
Freddie $113.5B→$138.4B, Freddie agency $30.6B→$55.3B (Aug 2025→Aug 2026).
Store keys `gse:fannie-retained`, `gse:freddie-retained`, `gse:freddie-agency`
($M, month-end) + a `gse` doc. Surfaces in the insights digest as an `mbs`
note (real data only): combined retained + 3m change + MBB 3m move, with a
plain-English stress line when both are shrinking.

**3. ETF FLOWS** — gap, documented honestly. ICI publishes exactly what we
want (weekly Estimated ETF Net Issuance: Equity/Domestic/World/Hybrid/Bond/
Taxable/Municipal/Commodity/Total, ~1wk lag, at
https://www.ici.org/research/stats/etf_flows), but ici.org returns Akamai
"Access Denied" (403) to automated fetches — not collectible from Render.
No fetcher built; no price ratio is labeled a flow. Paid alternative: EPFR.

**4. CORRELATIONS** — `collector/src/collector/fetchers/correlation.py`
(NEW), daily zero-HTTP compute job. Reads `idx:*` (equity.py), `yield:*`
(bonds.py), `cycle:*` (cycle.py) — all store keys verified against live
main. 18-series universe (SPX/NDX/SX5E/Nikkei, US+DE 10Y, VIX/VIX3M, IG/HY
OAS, USD broad, EUR/USD, USD/JPY, TLT/SHY, MBB, HYG, GLD, BTC); log returns
for prices/vol, diffs for yields/spreads; 60d + 252d Pearson matrices on
pairwise-complete common dates (min 20/60 obs). Six regime pairs
(stocks-vs-bonds, credit-vs-equity, USD-vs-stocks, vol-vs-stocks, IG-vs-HY,
BTC-vs-stocks) with 60d corr, percentile vs own history, and an EXTREME flag
at |corr|≥0.7. Writes doc `xcorr` + `xcorr:<pair>` histories. Extreme pairs
are hashed into the insights `digest_id` so the newsletter fires on
correlation-regime flips. Graceful: missing series are skipped, never
zero-filled.

**5. VOLATILITY** — realized vol (21d/63d annualized) computed in the same
job for SPX, NDX, TLT/SHY, MBB, HYG, GLD, BTC, EUR/USD, US 10Y (pp for the
yield); written as `rvol:<key>:21d/:63d` histories + the xcorr doc. Implied:
`^VIX3M` verified live on Yahoo (real CBOE 3M index, last 18.01) — added via
the new cycle `yahoo:` single-symbol field. `^MOVE` is a dead Yahoo symbol
(resolves to an unrelated Northern Trust ETF, not the Merrill Lynch MOVE
index) — confirmed no free MOVE feed; documented as a gap. Term-structure
proxy VIX3M-vs-VIX is now chartable (both series present).

**6. OPTIONS POSITIONING** — inventory: pc-total + pc-equity (CBOE CDN) and
cot-vix (CFTC legacy) already existed. Verified live additions from the same
free CBOE daily JSON (no code change — new `cboe:` ratio names):
INDEX PUT/CALL RATIO, EXCHANGE TRADED PRODUCTS PUT/CALL RATIO,
SPX + SPXW PUT/CALL RATIO (includes 0DTE SPXW), CBOE VOLATILITY INDEX (VIX)
PUT/CALL RATIO. Gaps: OCC daily volume (JS query screens, no stable keyless
endpoint), 0DTE volume stats (CBOE monthly PDFs), S3 short interest + CBOE
data shop (paid).

**7. FUTURES POSITIONING** — TFF (udgc-27he) codes verified live with
lev_money columns: 134741 SOFR-3M (lev funds net short ~2.4M, 2026-09-29),
1170E1 VIX futures, 20974+ Nasdaq-100 consolidated, 239747 Micro E-mini
Russell 2000. Added to `cftc_pos` (config snippet). The same four codes also
verified fresh in the legacy COT dataset (6dca-aqww), so they are added as
`cftc:` cycle series too (config-only) for the QUANT/POS tabs. Known quirk
(pre-existing): the TFF dataset carries FutOnly + Combined rows per date and
cftc_pos.py doesn't filter — same-date points overwrite (last wins); nets are
close either way, left as-is per the same-conventions rule.

**Config/schema changes** — `CycleSeriesCfg` gains `yahoo:` (single Yahoo
symbol, daily closes); `fetchers/cycle.py` handles it. Snippets (NOT merged
into the main config): `millennium-13f-snippet.yaml`,
`batch3-config-snippet.yaml` (cadences gse 30d + xcorr 1d; 15 new
cycle_series incl. MBB/HYG/LQD/GLD/BTC/VIX3M/MBB-SHY, 4 CBOE ratios, 4 legacy
COT breadth series; 4 TFF additions; tab-row additions for RISK/POS/QUANT).

**Scheduler/panels/insights** — full modified copies (union of all prior
jobs): `gse` monthly job (needs get_bytes), `xcorr` daily compute job
(start +900s, after country_risk). New `gse` + `xcorr` dashboard panels;
insights digest gains `xcorr` + `mbs` payloads, the GSE series in the anomaly
catalog (unit `$m` → $B formatting), newsletter bullets for correlation
regime + MBS stress, and both hashed into `digest_id`.

**UI** — new `X-CORR` tab (vanilla JS + inline SVG, zero new libraries):
correlation heatmap with 60D/1Y toggle, regime pairs with percentile bars +
EXTREME badges, realized-vol table, GSE retained-portfolio section.
Registered in tabs.js / index.html / main.js; styles appended to
terminal.css. Render verified headless in node with a synthetic payload.

**Tests** — 74/74 passing (46 prior + 28 new): test_gse.py (11: both PDF
parsers against realistic fixtures incl. adversarial same-shaped tables,
URL patterns, end-to-end fetch with mocked bytes, per-source isolation,
pdftotext-missing error, Millennium snippet CIK), test_correlation.py (9:
change math, perfect/zero-variance correlations, realized-vol values,
end-to-end doc+histories, empty-store and thin-series degradation),
test_cycle_yahoo.py (3: single-symbol fetch, ratio regression, no-source
error), test_insights_batch3.py (5: MBS stress/growth/no-data paths, catalog
inclusion, $m formatting).

## 2026-10-03 — RISK MAP tab: per-country market risk choropleth (subagent)

Harry asked for a terminal tab with a full world map: green = good/indicators
positive, yellow = improving, orange = deteriorating, red = risk zone. Also
asked that the map data land in his Google Drive — covered by wiring the
`country_risk` payload into the `/api/insights` digest, which the daily
`daily-osbloom-drive-snapshot` cron pulls.

**Backend** — `collector/src/collector/fetchers/country_risk.py` (NEW): daily
compute job, zero HTTP. Scores each of the 13 bond-matrix countries 0–100 from
up to three percentile-vs-own-history inputs:
  a. 10Y yield percentile vs trailing 5Y (`yield:<CC>10Y` — store key verified
     in fetchers/bonds.py)
  b. equity drawdown from 252d high, percentiled (`idx:<SYM>` — verified in
     fetchers/equity.py; US→SPX, DE→DAX, FR→CAC, UK→UKX, JP→NKX, CH→SMI)
  c. FX depreciation vs USD over 63d, percentiled (`cycle:eur-usd`,
     `cycle:usd-jpy` — the only FRED DEX series in config, verified on live
     main; direction-aware: falling DEXUSEU / rising DEXJPUS = depreciation)
Score = mean of available inputs. Buckets: ≤25 green STABLE, ≤50 yellow
IMPROVING, ≤75 orange DETERIORATING, >75 red RISK ZONE. Trend = point-in-time
score change over 21d (no lookahead), improving/deteriorating/flat. Writes
`risk:country:<CC>` history + `country_risk` doc. Every input degrades
gracefully; one bad country never fails the job.
**Scheduler**: `country_risk` job registered daily (start +600s, after the risk
engine), `.get("country_risk", 86400)` guard like the risk job.
**API**: `riskmap` panel added to `/api/dashboard` (panels.py, reads the
`country_risk` doc); `country_risk` payload added to `/api/insights` digest
output and hashed into `digest_id` (same pattern as `risk_summary`).
**Frontend** (vanilla JS + SVG, no new libraries): new `RISK MAP` tab
(`ui/js/tabs.js` TABS, `ui/index.html` nav + `<main data-tab="map">`,
`ui/js/main.js` render call, `ui/css/terminal.css` grid + map styles).
`ui/js/panels/riskmap.js` renders an equirectangular world choropleth from an
embedded 67KB simplified Natural Earth 110m geometry set
(`ui/js/world110m.js`; Antarctica excluded; France matched via ADM0_A3 since
NE codes it ISO_A2="-99"; GB geometry mapped to UK). Tap a colored country
for a detail card (score, bucket, trend, per-input percentiles). Colorblind-safe
palette; SVG viewBox scales to phone widths.
**Tests**: `collector/tests/test_country_risk.py` — 8 tests, all pass
(4 buckets, missing-input degradation, drawdown/FX direction, trend incl.
no-lookahead, doc+history writes). Frontend render verified headless in node:
13 clickable paths, correct fills, GB→UK mapping, legend counts, empty state.

**Staged files** (mirror into repo; `ui/` paths replace in full):
- NEW: `collector/src/collector/fetchers/country_risk.py`
- MODIFIED: `collector/src/collector/scheduler.py` (job registration),
  `collector/src/collector/insights.py` (+country_risk in digest),
  `collector/src/collector/panels.py` (riskmap panel),
  `ui/js/tabs.js`, `ui/index.html`, `ui/js/main.js`, `ui/css/terminal.css`
- NEW: `ui/js/panels/riskmap.js`, `ui/js/world110m.js`
- Tests: `collector/tests/test_country_risk.py`
- Config: merge `risk-map-config-snippet.yaml` (`country_risk: 86400` cadence)
  into `~/workspace/your_files/os-bloom/config.yaml` — do NOT replace that file.

## 2026-10-03 — Auction surveillance, TFF Treasury positioning, primary-dealer stats (subagent batch 2)

Staged under `~/workspace/os-bloom-data/`, mirroring repo paths. Config additions are
in `config-snippet-auctions-tff-dealer.yaml` (additive snippet — merge into the
parent-owned `~/workspace/your_files/os-bloom/config.yaml`, do not wholesale-replace it).

### 1. Treasury auction surveillance — `fetchers/auctions.py` (NEW)

- **API:** `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query`
  (keyless; verified live 2026-10-03). Params: `filter=record_date:gte:<date>`,
  `sort=-record_date`, `page[size]=100`, `page[number]=N`; pages built manually
  (the API's `links.next` is a relative query fragment, not followed).
- **Fields confirmed against the real payload** (not assumed): `bid_to_cover_ratio`,
  `total_accepted`, `indirect_bidder_accepted`, `direct_bidder_accepted`,
  `primary_dealer_accepted`, `offering_amt`, `security_type` (Bill/Note/Bond),
  `security_term` ("4-Week" … "29-Year 11-Month"), `auction_date`. Unheld auctions
  (null `bid_to_cover_ratio`) are skipped.
- **Tenor normalization** (tested): reopenings snap to benchmark buckets
  ("29-Year 11-Month"→Bond-30Y, "9-Year 10-Month"→Note-10Y, "1-Year 11-Month"→Note-2Y).
- **Metrics per configured bucket:** `bid_to_cover`, `indirect_pct`/`direct_pct`/
  `dealer_pct` (share of accepted, %), `offering` (raw $). Dated by `auction_date`.
  Stored as `auction:<Type>-<Tenor>:<metric>`, e.g. `auction:Note-10Y:bid_to_cover`.
- **Live smoke 2026-10-03:** 15 series stored; latest 4W bill (2026-10-01) btc 2.83,
  indirect 56.1%, dealer 27.5%, offering $100B; 30Y bond (2026-09-10) btc 2.61.
- Cadence `auctions: 86400` (daily). Per-page skip-on-failure; buckets outside
  `cfg.buckets` are ignored.

### 2. TFF leveraged-funds Treasury futures — config only (no new fetcher)

- **Codes verified live 2026-10-03** against the TFF Socrata dataset
  (`publicreporting.cftc.gov/resource/udgc-27he.json`): `042601` (2Y), `044601` (5Y),
  `043602` (10Y), `020601` (30Y bonds) — identical to legacy COT codes. `lev_money`
  long/short columns confirmed returning data for 043602. The existing `cftc_pos.py`
  fetcher handles these with zero code changes; four entries appended to the
  `cftc_pos` config section (groups: `[lev_money]`). Stored as
  `cftc:tff:<code>:lev_money`.

### 3. Primary dealers — `fetchers/dealer.py` (NEW)

- **API:** NY Fed Markets Data API (keyless; verified live 2026-10-03):
  `GET https://markets.newyorkfed.org/api/pd/get/{keyid}.json`
  → `{"pd":{"timeseries":[{"asofdate":"...","keyid":"...","value":"..."}]}}`.
  Values are **$millions**, Wednesday levels, weekly (~1wk release lag).
- **Series wired (all verified live):** `PDPOSGST-TOT` (net outright UST ex-TIPS
  dealer position), `PDFTD-USTET` (UST fails to deliver), `PDFTR-USTET` (UST fails
  to receive). Series list at `/api/pd/list/timeseries.json` (1539 series).
- **Live smoke 2026-10-03:** `dealer:ust-net` = 470,836 ($m, 2026-09-23);
  `dealer:fail-d` = 192,514 ($m).
- Cadence `dealer: 604800` (weekly). Per-series skip-on-failure. Stored as `dealer:<id>`.

### Schema + scheduler (full staged copies, based on live main 2026-10-03)

- `config.py`: `AuctionsCfg(buckets, lookback_days)`, `DealerSeriesCfg(id, name, keyid, unit)`,
  `Config.auctions` / `Config.dealer` fields + `load_config` wiring.
- `scheduler.py`: `"auctions"` and `"dealer"` jobs registered (same `run_fetcher` wrapping).
  NOTE: a concurrent parent edit added a `"risk"` job to this file; the staged copy
  includes it — my additions were re-applied on top and re-verified.

### Tests

`collector/tests/test_auctions_dealer.py` — 7 tests, mocked HTTP, fixtures from real
payloads (`fixtures/auctions_sample.json`: completed 10Y/bill/reopening + one unheld
auction; `fixtures/dealer_timeseries.json`: 3 good points + 1 bad row): tenor
normalization incl. reopenings, metric math (indirect/dealer/direct %), future-auction
skip, endpoint/param assertions, paging, per-series isolation. **All 7 pass** (15/15
with the earlier suite). `load_config` verified end-to-end on live main's config.yaml
plus the new sections.

### Apply

1. Copy staged code files over the repo tree (new: `fetchers/auctions.py`,
   `fetchers/dealer.py`; modified: `config.py`, `scheduler.py`; tests + fixtures optional).
2. Merge `config-snippet-auctions-tff-dealer.yaml` into
   `~/workspace/your_files/os-bloom/config.yaml` (cadences + `auctions`/`dealer`
   sections + 4 `cftc_pos` entries).
3. Commit via GitHub web UI (App connection is read-only); Render auto-deploys.
4. Optional follow-up (not built): an `auction:` source key on `CycleSeriesCfg`
   (same 5-line pattern as the `ofr:` key) would put 10Y bid-to-cover etc. directly
   on the QUANT tab; currently auction series live under `auction:` store keys only.

## 1. Risk/prediction engine (2026-10-03, staged)

Compute-only job — no HTTP, reads store history, writes five composites plus
a `risk_summary` doc. Staged files (repo paths mirrored):

| Staged path | Repo path | Change |
|---|---|---|
| `collector/src/collector/fetchers/risk.py` | same (new) | The engine: 5 composites + summary/verdict |
| `collector/src/collector/scheduler.py` | same (modified) | `risk` job registered: daily, `start + 300s`. **Union merge with sibling**: this copy contains the sibling's `auctions`/`dealer` jobs AND the risk job. APScheduler has no dependency ordering — the 300s delay is best-effort (documented in code); the job is idempotent and degrades gracefully |
| `collector/src/collector/insights.py` | same (modified) | `build_digest` adds `"risk": <risk_summary payload>` to /api/insights output (+9 lines); the risk verdict is hashed into `digest_id` so the newsletter fires when the risk read changes |
| `collector/tests/test_risk.py` | same (new) | 23 tests, FakeStore, all pass |
| `risk-config-snippet.yaml` | — | `risk: 86400` cadence block for the parent to merge into config.yaml (no config.py change needed) |

Methodology (full detail in the module docstring):
- **Basis-trade stress 0-100**: mean of available sub-scores — SOFR-IORB spread
  percentile, ON-RRP level (inverted) + 63d drain-rate percentile, HF repo
  borrowing percentile (OFR, quarterly; `ofr:FPF-BORROW_REPO_SUM` then
  `cycle:hf-repo-borrow` fallback), dealer fails percentile (optional).
- **Auction stress 0-100**: per bucket `auction:<Type>-<Tenor>:bid_to_cover`
  vs 6m avg + `dealer_pct` share (buckets probed: Note-2Y..Bond-30Y; sibling's
  actual key scheme). No auction series → `awaiting_auction_feed`, null value.
- **CTA crowdedness**: z-score of Treasury futures net positioning, expanding
  window, min 26 weekly obs. **Prefers TFF leveraged-funds**
  (`cftc:tff:<code>:lev_money`, sibling wiring), falls back to legacy
  `cycle:cot-ust-*`. Stored as `risk:cta_z_<tenor>`.
- **Regime**: rule-based score → CALM / LATE_CYCLE / RISK_OFF / STRESS / CRISIS.
  Auditable threshold table in the docstring (VIX, HY OAS, 10Y-2Y, TLT/SHY
  63d momentum).
- **Recession probability**: Sahm rule computed locally from
  `cycle:us-unemployment` (no new FRED series — fred.stlouisfed.org unreachable
  from sandbox) + t10y3m inversion + HY OAS + claims, logistic curves,
  weights 0.35/0.25/0.25/0.15 renormalized over available inputs.
- **Verdict**: max of available composites → CONTAINED <30 / ELEVATED <55 /
  HIGH <75 / SEVERE, plus top contributors and CTA crowdedness note.

Verified: 23/23 new tests pass, 38/38 full staged suite passes; all staged
files compile; graceful degradation covered by tests (empty store →
"NO DATA" verdict, no crash; missing auction feed → awaiting status).
**Not built**: ETF-flow input to any composite (no free machine-readable feed
— verified dead end earlier); daily OAT-Bund spread (no free daily French
yield feed). Cross-task note: dealer-fails probing prefers the sibling's exact
ids (`dealer:ust-fail-deliver` / `dealer:ust-fail-receive`, summed); auction
buckets follow the sibling's `auction:<Type>-<Tenor>` scheme.

## 0. Parent-agent additions on top (2026-10-03, merged into `~/workspace/your_files/os-bloom/config.yaml`)

- **`ofr:` cycle_series source key**: any OFR Hedge Fund Monitor mnemonic can now be a
  first-class cycle series (new `ofr` field on `CycleSeriesCfg`, dispatch branch in
  `fetchers/cycle.py`). Three verified mnemonics are wired on the QUANT tab:
  `hf-repo-borrow` (`FPF-BORROW_REPO_SUM`, $3.38T @ 2026-Q2 — the basis-trade leverage gauge),
  `hf-treasury-long` (`FPF-ASSETCLASS_LTREASURY_SUM`, $2.36T @ 2026-Q2),
  `hf-top10-lev` (`FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE`).
- **Unit fix**: the staged `ofr_series` used `unit: "$tn"`, but the API returns raw
  dollars (e.g. `13863000000000.0` for $13.863T gross assets, verified live). The merged
  config uses `unit: "$"` for dollar-denominated OFR series.
- **Not verified**: `TFF-LF_TREAS_NET_POSITION` (leveraged-funds net Treasury futures) does
  not exist in the `fpf` mnemonics list (329 checked live); that view remains available via
  the `cftc_pos` TFF dataset wiring as future work.
- The merged `config.yaml` also carries the parent's earlier changes: 22 pending FRED
  series, 13-country bond matrix, and the full QUANT tab (CFTC Treasury positioning,
  SOFR/OBFR/IORB/RRP funding panel, TLT/SHY + IEF/SHY momentum proxy).

# CHANGES.md — four new data fetchers (staged 2026-10-03)

Staged under `~/workspace/os-bloom-data/`, mirroring repo paths. Read-only GitHub
App connection: **nothing was pushed**. Apply by copying these files over the repo
tree, then commit via the GitHub web UI (Render auto-deploys on push).

**Do not touch** `~/workspace/your_files/os-bloom/config.yaml` — that is a separate
staged change owned by the parent agent; the config.yaml changes below are based on
the **live repo config.yaml fetched fresh 2026-10-03** (~14:45 UTC, pristine version
without the 22 pending FRED series — the merge is additive and conflict-free).

## Files added

| Staged path | Repo path | What |
|---|---|---|
| `collector/src/collector/fetchers/ofr.py` | `collector/src/collector/fetchers/ofr.py` | OFR Hedge Fund Monitor fetcher |
| `collector/src/collector/fetchers/cycle.py` | `collector/src/collector/fetchers/cycle.py` | **+ `ofr:` source-key branch** (2026-10-03 parent addition): `ofr.fetch_mnemonic` in `_fetch_one`; docstring "seven" → "eight source kinds" |
| `collector/src/collector/fetchers/cftc_pos.py` | `collector/src/collector/fetchers/cftc_pos.py` | CFTC TFF/CIT/Disaggregated positioning fetcher |
| `collector/src/collector/fetchers/tic.py` | `collector/src/collector/fetchers/tic.py` | Treasury TIC major foreign holders parser |
| `collector/src/collector/fetchers/thirteenf.py` | `collector/src/collector/fetchers/thirteenf.py` | SEC EDGAR 13F-HR watchlist fetcher |
| `collector/tests/test_new_fetchers.py` | `collector/tests/test_new_fetchers.py` | 8 tests (mocked HTTP) |
| `collector/tests/fixtures/ofr_timeseries.json` | `collector/tests/fixtures/ofr_timeseries.json` | OFR sample |
| `collector/tests/fixtures/cftc_pos_tff.json` | `collector/tests/fixtures/cftc_pos_tff.json` | TFF sample |
| `collector/tests/fixtures/tic_table5.txt` | `collector/tests/fixtures/tic_table5.txt` | TIC sample (tab-delimited) |
| `collector/tests/fixtures/thirteenf_submissions.json` | `…/thirteenf_submissions.json` | EDGAR submissions sample |
| `collector/tests/fixtures/thirteenf_index.json` | `…/thirteenf_index.json` | EDGAR archive index sample |
| `collector/tests/fixtures/thirteenf_infotable.xml` | `…/thirteenf_infotable.xml` | 13F infoTable sample |

## Files changed (full updated copies staged)

| Staged path | Repo path | Change |
|---|---|---|
| `collector/src/collector/config.py` | `collector/src/collector/config.py` | 5 new frozen dataclasses (`OfrSeriesCfg`, `CftcPosCfg`, `TicCfg`, `ThirteenFWatchCfg`, `ThirteenFCfg`), 4 new `Config` fields, `load_config` wiring — **plus `ofr: str | None` field on `CycleSeriesCfg`** (2026-10-03 parent addition) so any OFR mnemonic can be a first-class cycle_series and appear on tabs |
| `collector/src/collector/scheduler.py` | `collector/src/collector/scheduler.py` | 4 new jobs (`ofr`, `cftc_pos`, `tic`, `thirteenf`) registered in `fetchers`, same `run_fetcher` wrapping as existing jobs |
| `config.yaml` | `config.yaml` | 4 cadence entries + 4 new config sections (see below) |

Supporting verbatim copies (unchanged repo originals, kept so the staged tree is
test-runnable; **not changes**): `collector/src/collector/http.py`,
`collector/src/collector/store.py`, `collector/__init__.py`,
`collector/fetchers/__init__.py`, `collector/tests/__init__.py`.
A scratch venv used for test runs lives at `.venv/` (not part of the change).

---

## 1. OFR Hedge Fund Monitor — `fetchers/ofr.py`

- **API:** `https://data.financialresearch.gov/hf/v1/`, no auth. `dataset=fpf` =
  SEC Form PF aggregates (the hedge-fund-relevant dataset).
- Verified live 2026-10-03: `metadata/mnemonics?dataset=fpf` lists 329 mnemonics
  (`mnemonic` + `series_name`); `series/timeseries?mnemonic=<id>` returns
  `[[date, value], …]`, quarterly from 2013.
- Fetcher: per-mnemonic fetch with per-series skip-on-failure; stores raw history
  as `ofr:<mnemonic>`. Cadence `ofr: 86400` (daily max per OFR guidance; series
  are quarterly, so runs are cheap idempotent re-upserts).
- **13 series picked** (leverage / liquidity / risk / stress / gates):

| id | mnemonic | why |
|---|---|---|
| hf-gav | `FPF-ALLQHF_GAV_SUM` | aggregate HF gross assets — size of the leveraged sector |
| hf-count | `FPF-ALLQHF_COUNT` | number of qualifying funds — proliferation vs concentration |
| hf-top10-share | `FPF-ALLQHF_GAVN10_GAV_SHARE` | top-10 share of assets — concentration |
| hf-top10-lev | `FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE` | top-10 leverage ratio — systemic footprint |
| hf-top10-borrow | `FPF-ALLQHF_GAVN10_BORROWING_PERCENT` | top-10 share of borrowing — who holds the leverage |
| hf-fin-liq-7d | `FPF-ALLQHF_FINANCINGLIQUIDITYLE7_SUM` | financing maturing ≤7d — rollover/funding risk |
| hf-cash-ratio | `FPF-ALLQHF_GAVN10_CASHRATIO_AVERAGE` | top-10 unencumbered cash — liquidity buffer |
| hf-fin-90d-plus | `FPF-ALLQHF_FINANCINGLIQUIDTYGT90_PERCENT` | financing maturing 90d+ — term-funding stability |
| hf-stress-eq-p5 | `FPF-ALLQHF_EQDOWN20P_P5` | OFR stress test: −20% equity shock, 5th pct loss — tail risk |
| hf-stress-eq-p50 | `FPF-ALLQHF_EQDOWN20P_P50` | same shock, median — central stress outcome |
| hf-stress-cds-p5 | `FPF-ALLQHF_CDSDOWN250BPS_P5` | −250bp credit shock, 5th pct — credit tail risk |
| hf-gates | `FPF-ALLQHF_CURRENTLYGATES_PERCENT` | assets currently gated — realized stress |
| hf-suspensions | `FPF-ALLQHF_CURRENTLYSUSPENSIONS_PERCENT` | assets suspended — realized stress |

## 2. CFTC positioning expansion — `fetchers/cftc_pos.py`

Same anonymous Socrata pattern as `fetchers/cftc.py` (keyless, `$limit=5000`).

- **Datasets** (`DATASETS` in code): `tff`=`udgc-27he` (Traders in Financial
  Futures), `cit`=`j83k-qyrd` (Commodity Index Traders), `disagg`=`rxbv-e226`
  (Disaggregated commitments). All verified live 2026-10-03.
- **Groups** (`COLUMNS` in code, verified against live rows):
  tff → `lev_money`, `asset_mgr`, `dealer`; cit → `cit`;
  disagg → `m_money`, `swap`, `prod_merc`. Net = long − short, weekly (Tuesdays).
- **Parse gotcha (verified, baked into the code):** Socrata's disaggregated
  dataset has a typo — the swap-dealer *short* column is
  `swap__positions_short_all` (double underscore) while the long column is
  `swap_positions_long_all` (single). Using the single-underscore name 400s.
- **Contracts wired** (config `cftc_pos`, store `cftc:<dataset>:<code>:<group>`):
  E-mini S&P 500 `13874A` (lev_money/asset_mgr/dealer — equity risk positioning),
  US Dollar Index `098662` (same groups — dollar positioning),
  Gold `088691` (m_money/swap), WTI Crude `067651` (m_money/swap — the classic
  COT "spec" view on commodities). TFF is financial-futures-only, so commodities
  come from the disaggregated dataset.
- Cadence `cftc_pos: 604800` (weekly). Per-contract/per-group skip-on-failure;
  unknown dataset/group names are reported, not silently ignored.

## 3. TIC major foreign holders — `fetchers/tic.py`

- **Source:** `https://ticdata.treasury.gov/Publish/slt_table5.txt`,
  tab-delimited, CRLF line endings, monthly, ~6-week lag, values in **$bn**,
  current through 2026-07 as of 2026-10-03.
- **Important:** the legacy `mfh.txt` from the original task is **frozen at
  January 2023** (S-form era) — it is not the current release. Do not use it.
- **Parse:** find the header line starting `Country\t`; month columns are
  `YYYY-MM` (13 columns, newest first); dates stored as first-of-month.
  Data rows are `<Country>\t<val>…`; parsing stops at the `Notes:` line, skips
  `Of Which:` sub-rows, and tolerates `n.a.`/`--`/blank cells.
- Config `tic.countries`: Japan, United Kingdom, "China, Mainland", Belgium,
  "Cayman Islands", Luxembourg, Canada, Ireland, France, Taiwan (top-10 holders
  as of 2026-07). `Grand Total` is always stored. Stored as `tic:<slug>` e.g.
  `tic:japan`, `tic:china_mainland`, `tic:grand_total`.
- Cadence `tic: 604800` (weekly poll of a monthly release).

## 4. 13F-HR watchlist — `fetchers/thirteenf.py`

- **Flow per CIK** (verified live 2026-10-03 for Berkshire `0001067983` and
  Bridgewater `0001350694`): `data.sec.gov/submissions/CIK<cik-10-digit>.json`
  → latest `13F-HR` accession → `www.sec.gov/Archives/edgar/data/<cik-no-pad>/<accession-no-dashes>/index.json`
  → holdings XML (the `.xml` that isn't `primary_doc.xml`) → `infoTable` in
  namespace `http://www.sec.gov/edgar/document/thirteenf/informationtable`.
- **Aggregates duplicate issuers** (multiple share classes/CUSIPs) by issuer
  name, keeps the largest row's CUSIP/shares; values converted $000 → USD.
  Stores top 15 as a JSON doc at `thirteenf:<cik-10-digit>:top15` via
  `Store.put_doc(..., source="edgar")`.
- **SEC fair-access (mandatory):** descriptive User-Agent is **configurable**
  (`thirteenf.user_agent`) — SEC 403s the `product/version (+url)` style
  (verified twice 2026-10-03), and the repo's own default
  `os-bloom/0.1 (+https://github.com/cleyfe/os-bloom)` is exactly that style,
  so the fetcher passes its own UA. ≤10 req/sec honored via 0.5s pauses.
  **Set `user_agent` to a real contact before first run**
  (default is a placeholder `os-bloom/1.0 contact admin@example.com`).
- **Watchlist** (config `thirteenf.watchlist`, all verified to have 13F-HR
  filings): Berkshire Hathaway `0001067983`, Bridgewater Associates
  `0001350694`, Renaissance Technologies `0001037389`, Pershing Square
  `0001336528`, Scion Asset Management `0001649339`.
- Cadence `thirteenf: 604800` (weekly poll; filings are quarterly, ≤45d lag).
  Per-filer skip-on-failure.

## config.yaml additions

```yaml
cadences:
  ofr: 86400        # daily max per OFR guidance; series are quarterly
  cftc_pos: 604800  # weekly (Tuesdays)
  tic: 604800       # monthly TIC release
  thirteenf: 604800 # quarterly filings; weekly poll
```
plus `ofr_series:` (13 entries), `cftc_pos:` (4 contracts), `tic:` (10
countries), `thirteenf:` (user_agent + 5-filer watchlist) sections placed
before `feeds:`. See the staged `config.yaml` for the full text.

## Config schema (`config.py`)

```python
OfrSeriesCfg(id, name, mnemonic, unit)
CftcPosCfg(dataset, code, label, groups)      # dataset: tff | cit | disagg
TicCfg(countries)                             # Grand Total auto-added
ThirteenFWatchCfg(name, cik)
ThirteenFCfg(user_agent, watchlist)
```
wired into `Config` and `load_config` following the existing frozen-dataclass
pattern. Note: `user_agent` has **no safe default** — it must be set in config.

## Tests

`collector/tests/test_new_fetchers.py` — 8 tests, mocked HTTP (injected
`get_text`), following repo conventions (plain pytest, bare async tests):
OFR parse + endpoint/params, CFTC column-map coverage + net parse with bad-row
skip + code filtering, TIC table parse (Grand Total, `n.a.` skip, `Of Which`
exclusion, slug), 13F XML parse (issuer aggregation, bad-row skip) + full
filer flow (10-digit CIK padding, accession dash-stripping, UA header, latest
13F-HR selection). **All 8 pass** (`pytest -o asyncio_mode=auto`, matching the
repo's async-test style).

**Live smoke test** (real `get_text`, temp sqlite Store, 2026-10-03): all four
fetchers returned their labels cleanly — 13 OFR series, 10 CFTC series
(2 E-mini + 2 USD-index groups ×3 … precisely 3+3+2+2), 11 TIC series,
5 thirteenf docs. This caught and fixed the `swap__positions_short_all` typo
before staging.

## Apply steps

1. **config.yaml**: use `~/workspace/your_files/os-bloom/config.yaml` (the parent's merged
   copy — it contains everything: the 22 pending FRED series, 13-country bond matrix,
   QUANT tab, AND the four new sections below). Do **not** use the staged
   `~/workspace/os-bloom-data/config.yaml` — it was built from the pristine live config
   and lacks the parent's changes.
2. Copy staged code files over the repo tree (paths mirror the repo):
   new: `fetchers/ofr.py`, `fetchers/cftc_pos.py`, `fetchers/tic.py`,
   `fetchers/thirteenf.py`; modified: `fetchers/cycle.py`, `config.py`, `scheduler.py`.
   Optionally add `collector/tests/test_new_fetchers.py` + `fixtures/`.
3. Edit `thirteenf.user_agent` in config.yaml to a real contact (SEC fair-access
   requirement — the placeholder will get 403s).
4. Commit via GitHub web UI (App connection is read-only); Render auto-deploys.

## Open / unverified

- The `cit` dataset column map is verified but **no CIT contract is wired** in
  config yet — add one (e.g. a major ag/energy code) if index-trader positioning
  is wanted.
- `scheduler.py`/`config.py` were produced by scripted edits on extracted live
  originals; the staged copies are complete files — a quick diff at apply time
  is still worthwhile.
- Sandbox-only quirk (not a repo issue): this VM's egress proxy breaks
  httpx's *env-var* proxy parsing (`InvalidURL: Invalid port: ':1]'`); passing
  the proxy explicitly works. On Render there is no proxy env, so `get_text`
  behaves as it does for all existing fetchers.
- 13F `shares` are reported as filed (splits/adjustments not normalized);
  holdings values are quarter-end snapshots with up to 45 days' lag.

## 2026-10-03 — Batch 7: AI-infrastructure coverage map + money-flow graph (expanded scope)

Harry asked for FULL AI-buildout coverage (not ~25 names) built on a GENERIC
universe schema that later accepts broker-dealer, bank, crypto and
fixed-income universes — no AI-specific hardcoding in the XBRL fetcher or
graph renderer (both take a `universe_id`).

New/rewritten fetchers (weekly):
- `fetchers/ai_capex.py`: rewritten as `fetch_universe_capex(cfg, store,
  get_text, universe_id)` — reads `data/<universe_id>.json`, pulls free EDGAR
  XBRL companyconcept for every PUBLIC company in the universe (us-gaap with
  ifrs-full fallback: Revenues→Revenue, capex→
  PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities —
  covers TSMC/ASML/ARM 20-F filers; verified live 200s). Per quarter: FCF =
  OCF − capex, capex intensity, funding gap, debt load; aggregates declared
  in the universe file (Big-6 hyperscalers). Series keys
  `ai_buildout:<TICKER>:<metric>` + doc `ai_buildout_capex`. One bad company
  never fails the job; 0.4s pause/company (SEC fair access).
- `fetchers/ai_graph.py`: rewritten as `fetch_universe_graph(store,
  universe_id)` — vendored universe + live financial overlay, zero HTTP.
  Writes doc `ai_buildout_graph`.

Vendored data: `collector/src/collector/data/ai_buildout.json` (generic
coverage-map schema: universe_id, verticals[{id,label,group,companies[{id,
name,ticker,cik,kind}]}], edges, aggregates, risk_notes). 72 nodes —
60 public + 12 private; 27 press-reported edges (~$1,163B total deal value).

FINAL ROSTER (every ticker returned HTTP 200 on Yahoo chart API live
2026-10-03; every CIK verified via SEC company_tickers.json live):
- Chips & compute (12): NVDA, AVGO, AMD, INTC, MRVL, ARM, TSM, ASML, AMAT,
  LRCX, KLAC, MU
- Hyperscalers (6): MSFT, GOOGL, AMZN, META, ORCL, AAPL
- Neoclouds & miners (9 public): CRWV, NBIS, HUT, IREN, CLSK, CIFR, WULF,
  BTDR, APLD (+6 private: Lambda, Nscale, Sharon AI, Crusoe, Firmus, Naver)
- AI labs (4 private): OpenAI, xAI, Anthropic, Safe Superintelligence
- Data-center REITs (2): DLR, EQIX
- Power & uranium (10): CEG, VST, NRG, TLN, OKLO, SMR, CCJ, UUUU, LEU, GEV
  (GEV added — GE Vernova, verified live)
- Electrical & cooling (10): VRT, ETN, HUBB, EMR, MOD, TT, CARR, JCI, NVT,
  POWL (nVent Electric trades as NVT, not NVENT)
- Networking (3): ANET, CSCO, CIEN
- Storage & memory (3): STX, WDC, SNDK
- Servers & hardware (3): DELL, SMCI, HPE
- Construction / EPC (2): EME, FIX
- Financiers (2): Wall Street funds, SoftBank (private/deal-only)
Dropped: PSTG (Yahoo chart API 404s on 2026-10-03 — dead symbol).

Deal edges (unchanged from first pass, all attributed, per-edge
source/confidence): Nvidia→OpenAI ≤$100B+chips (Reuters 2025-09);
Nvidia→CoreWeave $2B @ $87.20 (TechCrunch 2026-01) + $6.3B backstop
(Reuters 2025-09); Nvidia→Nebius $2B (CNBC 2026-02); Anthropic $518B
compute commitments — Google $111.1B, Amazon $110B, Microsoft $31.4B,
Broadcom $161.2B leases, xAI ≤$84.5B (Reuters confidential IPO prospectus
2026-10); SoftBank→OpenAI $64.6B (The Next Web 2026-10); Amazon→Anthropic
$8B+$5B(+$20B); Microsoft→OpenAI $1B+$10B; Microsoft→Nebius $17.4B/5yr
(Reuters); OpenAI→CoreWeave $22.4B (Reuters); Alphabet→Meta $10B+ cloud
(Reuters 2025-08); Wall St→xAI $125B, OpenAI→neoclouds $105B, Hut 8 $50B,
Lambda $1.5B, Nscale $0.9B, Sharon AI $4.9B, IREN $3.4B (The Economist).

UI (HYPER tab, AI FLOW section): redesigned for scale — search box +
vertical filter chips; vertical-grouped company cards (FCF red dots, risk
flag badges); tap any card → detail view + EGO GRAPH (node + 1-hop deal
neighbors); money-flow section with hub selector (default Nvidia) and a
full-deal-map toggle for desktop widths; Big-6 quarterly capex stacked
chart; concentration + cashflow-risk notes.

Risk flags per company: cashflow_negative (FCF<0 ×2 quarters),
capex_intensity_high (>40%), funding_gap_large (>$5B/qtr),
private_no_financials (deal-only nodes).

Tests: 332 passed, 1 skipped (full staged suite, incl. 11 new
universe-schema/XBRL/IFRS tests).

Honest gaps: private labs/neoclouds have no free XBRL financials (deal
nodes only); single-name credit/CDS (paid); no free machine-readable ETF
flows; IFRS filers other than the three verified 20-F names may need
tag-by-tag checks; PSTG dead on Yahoo.
## 2026-10-03 — Batch 10: full FINRA TRACE sweep (subagent)

Harry asked for *all* of FINRA's TRACE data, not just Treasury. Systematic
inventory of every TRACE dataset (CDN probes with polite gaps + finra.org
documentation, all verified live 2026-10-03):

**FREE, machine-readable, keyless — all covered:**
- Treasury daily aggregates — `cdn.finra.org/trace/treasury-aggregates/daily/ts-daily-aggregates-YYYY-MM-DD.xlsx`
  (each trading day ~8pm ET, history since 2023-02-13). EXTENDED this batch:
  + `trace-ust-frns-par` (FRN par; 2026-10-02: $2.4bn), `trace-ust-onrun-par`
  (all on-the-run par summed across coupon+TIPS buckets; $1,169.8bn on
  2026-10-01 fixture), `trace-ust-offrun-par` ($326.3bn).
- Treasury monthly aggregates (same path, `monthly/`), history backfill — built batch 6.
- TRACE monthly volume by product — `cdn.finra.org/trace/volume/monthly/TRACE_Public_Monthly_Report-YYYY-MM.xlsx`
  (3rd business day after month-end, back to 2017-01). EXTENDED this batch:
  + `trace-conv-par/trades` (convertibles), `trace-chrc-par/trades`
  (church plans), `trace-eln-par/trades` (equity-linked notes) — every product
  row in the report now covered — plus derived `trace-corp-cust-share`
  (dealer-to-customer par / total, 0.774 in Aug 2026: retail/institutional mix).
- ICE Vantage STAR daily securitized aggregates (zip, no auth) — built batch 6.

**FREE but not machine-readable / dead — documented, not built:**
- Weekly Treasury aggregates on the CDN: discontinued Feb 2023 (replaced by
  daily). Probed, 403. Dead.
- Weekly TRACE volume report: does not exist (probed
  `trace/volume/weekly/`, 403). Monthly only.
- Daily corporate-bond aggregate: FINRA publishes none free (only monthly).
- TRACE Fact Book: annual PDF only
  (e.g. preview.finra.org/.../Trace-Factbook-2023.pdf) — rich historical
  tables incl. 144A, but not a time-series feed.
- 144A-specific aggregates: no free machine-readable file; only in the paid
  TSAR / the Fact Book PDF.

**PAID / auth-walled — documented as honest gaps (never buy):**
- TRACE Security Activity Report (TSAR): monthly per-security stats for
  corporate/agency, 90-day delayed — **$750/month** ($250 tax-exempt), agreement
  required (finra.org/finra-data → "Access TSAR" on myfiles.finra.org).
- End-of-Day TRACE Transaction File: free only to Vendor Real-Time Data Feed
  subscribers; otherwise part of the paid data products.
- Real-time TRACE vendor feed: **$1,500/month per Data Set** (continuous) or
  $250/month snapshot; professional display $50–$260/month by data-set count
  (SEC fee schedule, Exhibit 5).
- Academic Corporate Bond TRACE Data: transaction-level, 36-month delayed,
  masked MPIDs — academics only, agreement required, not keyless.
- FINRA API (developer.finra.org, incl. TRACE Market Reports endpoints):
  OAuth 2.0 with registered client credentials — not keyless. Re-confirmed.
- download.finratraqs.org (security masters, daily lists, closing reports):
  NWSF client certificate + account (member/subscriber only); probed 2026-10-03
  — connection refused without credentials.
- Per-trade feeds, FINRA ATS/OTC current weekly, OCC volume: paid/OAuth as
  previously documented.

**Code** — extended `fetchers/trace_treasury.py` (SUM_SERIES: repeated
"On-the-run"/"Off-the-run" bucket rows summed market-wide) and
`fetchers/trace_monthly.py` (full product-row coverage + derived corp customer
share). No new jobs: existing `trace_treasury` (daily) and `trace_monthly`
jobs pick up the new keys automatically; no scheduler/insights/panels/UI
changes needed — STRUCT tab renders them from config rows.

**Tests** — `test_batch6_finra_ice.py` extended: FRN par, on/off-the-run sums,
CONV/CHRC/ELN par+trades, cust-share parse + job-level store assertions
(13/13 pass; full staged suite below).

**Config merge (for parent — do NOT edit config.yaml directly):**
`config-snippet-trace10.yaml` — 10 new `cycle_series` entries (external: true)
+ 10 STRUCT tab rows for the two TRACE panels. No new cadences.

