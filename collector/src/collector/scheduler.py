"""Registers one APScheduler job per fetcher, all wrapped in run_fetcher.

Every job also fires once immediately on startup (next_run_time=now) so a
fresh deployment populates within seconds instead of one full cadence. That
relies on a generous misfire_grace_time: jobs are registered during build(),
before uvicorn's ASGI startup hook actually starts the scheduler, and that
gap alone can exceed APScheduler's default 1s grace — silently dropping the
startup run on every deployment.

The newsletter job intentionally starts a few seconds after insights so a new
digest exists before the first delivery attempt.

Boot catch-up: run_fetcher records each success in the job_runs doc, and
register_jobs gives any staggered job whose last run is missing or older
than its cadence an early first run (start + 60s, 75s apart, in dependency
order) instead of the hardcoded large offsets — so a fresh deploy populates
the radar/universe panels within minutes, not over an hour.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import partial

log = logging.getLogger(__name__)

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from collector.config import Config
from collector.fetchers.bonds import fetch_bonds
from collector.fetchers.correlation import refresh_xcorr
from collector.fetchers.cycle import fetch_cycle
from collector.fetchers.cftc_pos import fetch_cftc_positioning
from collector.fetchers.country_risk import refresh_country_risk
from collector.fetchers.equity import fetch_equity
from collector.fetchers.fred import fetch_macro_history
from collector.fetchers.gse import fetch_gse
from collector.fetchers.home_radar import refresh_home_radar
from collector.fetchers.hyperscaler import fetch_hyperscaler
from collector.fetchers.ai_capex import fetch_universe_capex
from collector.fetchers.bank_financials import fetch_bank_financials
from collector.fetchers.finance_dirs import fetch_finance_dirs
from collector.fetchers.ai_graph import fetch_universe_graph
from collector.fetchers.tsv_watch import fetch_tsv_watch
from collector.fetchers.macro import fetch_calendar_if_due
from collector.fetchers.midnight import fetch_midnight
from collector.fetchers.morpho import fetch_morpho
from collector.fetchers.movers import fetch_movers
from collector.fetchers.news import fetch_news
from collector.fetchers.regwatch import fetch_regwatch
from collector.fetchers.ofr import fetch_ofr
from collector.fetchers.ofr_tff import fetch_ofr_tff
from collector.fetchers.refs import fetch_refs
from collector.fetchers.risk import refresh_risk
from collector.fetchers.stress import INDICATORS, STRESS_WEIGHTS, refresh_stress_full
from collector.fetchers.stress_views import refresh_stress_views
from collector.fetchers.thirteenf import fetch_thirteenf
from collector.notify import refresh_digest_and_notify
from collector.fetchers.tic_flows import fetch_tic_all
from collector.fetchers.voldash import refresh_voldash
from collector.fetchers.auctions import fetch_auctions
from collector.fetchers.dealer import fetch_dealer
from collector.fetchers.finra_breadth import fetch_finra_breadth
from collector.fetchers.finra_corp import fetch_finra_corp
from collector.fetchers.finra_capped import fetch_finra_capped
from collector.fetchers.finra_margin import fetch_finra_margin
from collector.short_metrics import refresh_short_metrics
from collector.fetchers.finra_short import fetch_finra_short
from collector.fetchers.finra_regsho import fetch_finra_regsho
from collector.fetchers.ticker_stats import fetch_ticker_stats
from collector.fetchers.finra_ids_star import fetch_finra_ids_star
from collector.fetchers.nyfed_cmdi import fetch_nyfed_cmdi
from collector.fetchers.finra_factbook import fetch_finra_factbook, fetch_finra_factbook_annual
from collector.fetchers.finra_ats import fetch_finra_ats
from collector.fetchers.finra_otc import fetch_finra_otc
from collector.fetchers.ofr_stfm import fetch_ofr_stfm
from collector.fetchers.frb_ddp import fetch_frb_ddp
from collector.fetchers.sec_ncen import fetch_sec_ncen
from collector.fetchers.sec_nport import fetch_sec_nport
from collector.fetchers.nport_flows import fetch_nport_flows
from collector.fetchers.ici_mutual_flows import fetch_ici_mutual_flows
from collector.fetchers.sec_pfs import fetch_sec_pfs
from collector.fetchers.sec_ftd import fetch_sec_ftd
from collector.fetchers.sifma_issuance import fetch_sifma_issuance
from collector.fetchers.z1_holdings import fetch_z1_holdings
from collector.fetchers.mspd import fetch_mspd
from collector.fetchers.soma_cusip import fetch_soma_cusip
from collector.debt_cube import refresh_debt_cube
from collector.stress_heatmaps import refresh_stress_heatmaps
from collector.fetchers.ice_star import fetch_ice_star
from collector.fetchers.refs_history import fetch_refs_history
from collector.fetchers.trace_monthly import fetch_trace_monthly
from collector.fetchers.trace_treasury import fetch_trace_treasury
from collector.fetchers.worldbank import fetch_worldbank
from collector.fetchers.usaspending import fetch_usaspending
from collector.fetchers.coingecko import fetch_coingecko
from collector.fetchers.defillama_rwa import fetch_defillama_rwa
from collector.fetchers.cboe_options import fetch_cboe_options
from collector.fetchers.ishares_etf import fetch_ishares_etf
from collector.fetchers.sec_xbrl_etf import fetch_sec_xbrl_etf
from collector.fetchers.etf_holders_13f import fetch_etf_holders_13f
from collector.fetchers.edgar_13f_holders import fetch_edgar_13f_holders
from collector.fetchers.bdc_financials import fetch_bdc_financials
from collector.fetchers.bdc_universe import fetch_bdc_universe
from collector.fetchers.nasdaq_tape import fetch_nasdaq_tape
from collector.fetchers.openfigi import fetch_openfigi
from collector.fetchers.finnhub import fetch_finnhub
from collector.fetchers.polymarket import fetch_polymarket
from collector.fetchers.kalshi import fetch_kalshi
from collector.fetchers.pred_edge import fetch_pred_edge
from collector.fetchers.fed_meetings import fetch_fed_meetings
from collector.fetchers.zyfai import fetch_defi
from collector.http import GetBytes, GetText, PostJson, PostText, get_text_curl
from collector.http import post_text as _default_post_text
from collector.newsletter import SmtpCfg, deliver_newsletter
from collector.runner import run_fetcher
from collector.runner import JOB_RUNS_DOC
from collector.store import Store

MACRO_HISTORY_SECONDS = 86400  # daily; not config — no reason to tune it
GSE_SECONDS = 30 * 86400  # monthly; the GSE summaries release ~25d after month-end

CATCHUP_DELAY = 60   # first catch-up slot: start + 60s
CATCHUP_GAP = 75     # spacing between catch-up slots (60–90s): no thundering herd


async def _stress_with_views(store, cfg, fred_api_key, get_text, get_bytes):
    """Stress job: Phase-1 fetch + scoring, then Phase-2 view payloads (WS1).

    refresh_stress_full writes the stress_matrix / stress_velocity docs;
    refresh_stress_views then rebuilds stress_horizon, stress_contagion,
    stress_divergence, stress_replay and stress_quadrant from the same raw
    series (recomputed, never read back from the Phase-1 docs). Per-view
    isolation inside refresh_stress_views means one bad view never blocks
    the others, and a views failure never breaks the Phase-1 docs.
    run_fetcher (the job wrapper) records any exception as a job error.
    """
    await refresh_stress_full(store, cfg, fred_api_key, get_text, get_bytes)
    refresh_stress_views(store, INDICATORS, STRESS_WEIGHTS)
    # Phase-2 WS2: evaluate alert rules against the fresh docs. Guarded so a
    # missing/broken alerts module never breaks the stress docs.
    try:
        from collector.stress_alerts import evaluate_alerts
        evaluate_alerts(store)
    except Exception as e:  # noqa: BLE001 - alerts are best-effort
        log.warning("stress alerts evaluation failed: %s", e)
    # Phase-2 WS3: episode-relative velocity matrix. Guarded likewise.
    try:
        from collector.stress_episodes import refresh_episodes
        refresh_episodes(store, INDICATORS, STRESS_WEIGHTS)
    except Exception as e:  # noqa: BLE001 - episodes are best-effort
        log.warning("stress episodes refresh failed: %s", e)
    # Phase-2 WS6: IMF chart panels (§15). Async; guarded likewise. Degrades
    # to "source not connected" panels when the Google credential is absent.
    try:
        from collector.fetchers.imf import refresh_imf
        await refresh_imf(store, get_text, fred_api_key)
    except Exception as e:  # noqa: BLE001 - IMF panels are best-effort
        log.warning("IMF panels refresh failed: %s", e)


def _catchup_first_runs(
    fetchers: dict[str, tuple[int, object, datetime]],
    store: Store,
    start: datetime,
) -> None:
    """Boot catch-up: jobs whose last successful run is missing or older than
    their cadence get an early first run (start + 60s, 75s apart) instead of
    the hardcoded large stagger offsets. Iteration follows dict order, which
    is dependency order (risk before country_risk before xcorr ... before
    home_radar), so the catch-up sequence preserves it. Jobs with a recent
    run keep their configured first-run time as fallback spacing.

    run_fetcher persists each success into the job_runs doc, so this survives
    restarts only when the store itself survives (DATABASE_URL); on an
    ephemeral store every job is "missing" and the whole staggered set
    catches up shortly after boot instead of up to 70 minutes later.
    """
    doc = store.doc(JOB_RUNS_DOC)
    last_runs = doc.payload if doc and isinstance(doc.payload, dict) else {}
    slot = start + timedelta(seconds=CATCHUP_DELAY)
    for name, (seconds, fn, first) in fetchers.items():
        if not isinstance(first, datetime) or first <= start + timedelta(seconds=CATCHUP_DELAY):
            continue  # fires at/near boot already; nothing to catch up
        last = last_runs.get(name)
        overdue = True
        if isinstance(last, str) and last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                overdue = (start - last_dt).total_seconds() >= seconds
            except ValueError:
                overdue = True
        if overdue:
            fetchers[name] = (seconds, fn, slot)
            slot += timedelta(seconds=CATCHUP_GAP)


def register_jobs(
    scheduler: AsyncIOScheduler,
    cfg: Config,
    store: Store,
    get_text: GetText,
    post_json: PostJson,
    get_bytes: GetBytes,
    fred_api_key: str,
    smtp_cfg: SmtpCfg,
    post_text: PostText | None = None,
) -> None:
    start = datetime.now(timezone.utc)
    pt = post_text or _default_post_text
    fetchers = {
        "equity": (cfg.cadences["equity"], partial(fetch_equity, cfg.indexes, store, get_text), start),
        "bonds": (cfg.cadences["bonds"], partial(fetch_bonds, cfg.bonds, cfg.cb_rates, store, get_text,
                                                 fred_api_key=fred_api_key), start),
        "macro": (cfg.cadences["macro"], partial(fetch_calendar_if_due, cfg.calendar_url, cfg.calendar_map,
                                                 store, get_text), start),
        "news": (cfg.cadences["news"], partial(fetch_news, cfg.feeds, store, get_text, max_items=cfg.max_news), start),
        "macro_history": (MACRO_HISTORY_SECONDS, partial(fetch_macro_history, cfg.series, store, fred_api_key, get_text), start),
        "defi": (cfg.cadences["defi"], partial(fetch_defi, cfg.defi, cfg.zyfai_base, store, get_text), start),
        "midnight": (cfg.cadences["midnight"], partial(fetch_midnight, cfg.defi, cfg.midnight_base, store, get_text), start),
        "refs": (cfg.cadences["refs"], partial(fetch_refs, cfg.refs, store, get_text, post_json), start),
        "refs_history": (cfg.cadences["refs_history"], partial(fetch_refs_history, cfg.refs, store, get_text), start),
        "morpho": (cfg.cadences["morpho"], partial(fetch_morpho, cfg.defi, store, post_json), start),
        "cycle": (cfg.cadences["cycle"], partial(fetch_cycle, cfg.cycle_series, store, fred_api_key, get_text, get_bytes), start),
        "ofr": (cfg.cadences["ofr"], partial(fetch_ofr, cfg.ofr_series, store, get_text), start),
        "cftc_pos": (cfg.cadences["cftc_pos"], partial(fetch_cftc_positioning, cfg.cftc_pos, store, get_text), start),
        "tic": (cfg.cadences["tic"], partial(fetch_tic_all, cfg.tic, store, get_text), start),
        "insights": (cfg.cadences["insights"], partial(refresh_digest_and_notify, store, cfg, post_json), start),
        "thirteenf": (cfg.cadences["thirteenf"], partial(fetch_thirteenf, cfg.thirteenf, store, get_text), start),
        "auctions": (cfg.cadences["auctions"], partial(fetch_auctions, cfg.auctions, store, get_text), start),
        "dealer": (cfg.cadences["dealer"], partial(fetch_dealer, cfg.dealer, store, get_text), start),
        # risk is compute-only (no HTTP): it reads whatever the data jobs have
        # stored. APScheduler has no dependency ordering, so it starts 5 min
        # after everything else — on a fresh deploy the data jobs get a head
        # start, and on the daily cadence it runs 5 min after them each day.
        # Best-effort only: the job is idempotent, recomputes daily, and every
        # input degrades gracefully, so a premature run just yields thinner
        # composites, never a crash. .get() keeps an old config.yaml (without
        # the risk cadence) from breaking scheduler registration entirely.
        "risk": (cfg.cadences.get("risk", 86400), partial(refresh_risk, store),
                 start + timedelta(seconds=300)),
        # Stress Monitor v2 Phase-1: fetch genuinely missing Tier-1 sources,
        # then run the scoring refresh. refresh_stress_full (fetchers/stress.py)
        # coordinates with Worker A's collector.stress_score.refresh_stress(store,
        # registry) — same signature, registry = the INDICATORS list. The scoring
        # module is not expected to exist yet; the job degrades to data-only and
        # logs it. .get() guard like the risk job.
        # Phase-2 (WS1): the same job chains refresh_stress_views, which builds
        # the five Phase-2 view docs (horizon/contagion/divergence/replay/
        # quadrant) from the raw series the Phase-1 step just wrote.
        "stress": (cfg.cadences.get("stress", 86400),
                 partial(_stress_with_views, store, cfg, fred_api_key, get_text, get_bytes),
                 start + timedelta(seconds=600)),
        # country risk map: compute-only, reads bond/equity/cycle history.
        # Starts after the risk engine; same graceful-degradation contract and
        # .get() guard so an old config.yaml can't break registration.
        "country_risk": (cfg.cadences.get("country_risk", 86400), partial(refresh_country_risk, store),
                 start + timedelta(seconds=600)),
        # GSE retained portfolios: monthly PDFs from Fannie Mae + Freddie Mac
        # (needs pdftotext; see fetchers/gse.py). .get() guard like the risk job.
        "gse": (cfg.cadences.get("gse", GSE_SECONDS), partial(fetch_gse, store, get_bytes), start),
        # cross-asset correlations + realized vol: compute-only, reads
        # idx:/yield:/cycle: history. Starts after country_risk; same
        # graceful-degradation contract and .get() guard.
        "xcorr": (cfg.cadences.get("xcorr", 86400), partial(refresh_xcorr, store),
                 start + timedelta(seconds=900)),
        # vol dashboard: compute-only, reads cycle:/idx: vol + price history.
        # Starts after xcorr; same contract and .get() guard.
        "voldash": (cfg.cadences.get("voldash", 86400), partial(refresh_voldash, store),
                 start + timedelta(seconds=1200)),
        # single-stock sigma movers: ~600 Yahoo chart requests, polite spacing
        # (~8 min), weekly. Per-symbol isolation; a throttled symbol degrades
        # to a thinner list, never a failed job. .get() guard like the rest.
        "movers": (cfg.cadences.get("movers", 604800), partial(fetch_movers, store, get_text),
                 start + timedelta(seconds=1500)),
        # market radar: compute-only, reads stored history (risk, vol, movers,
        # country risk, cycle). Starts after movers; same graceful-degradation
        # contract and .get() guard.
        "home_radar": (cfg.cadences.get("home_radar", 86400), partial(refresh_home_radar, store),
                 start + timedelta(seconds=1800)),
        # hyperscaler desk: weekly EDGAR debt-offering scan (6 submissions
        # requests + per-424B2 prospectus fetches, polite 0.5s gaps) plus
        # equity cards from stored cycle history. One issuer never kills it.
        "hyperscaler": (cfg.cadences.get("hyperscaler", 604800), partial(fetch_hyperscaler, cfg, store, get_text),
                 start + timedelta(seconds=300)),
        # AI capex tracker: universe-driven XBRL fundamentals (generic
        # coverage-map template: the same code takes any universe_id).
        # ~60 public companies x 12 tags (three-statement set), SEC fair
        # access, one bad filer never fails the job. .get() guard like the rest.
        "ai_capex": (cfg.cadences.get("ai_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "ai_buildout"),
                 start + timedelta(seconds=600)),
        # AI money-flow graph: zero HTTP, overlays ai_capex financials onto
        # the vendored press-reported deal graph. Starts after ai_capex.
        "ai_graph": (cfg.cadences.get("ai_graph", 604800), partial(fetch_universe_graph, store, "ai_buildout"),
                 start + timedelta(seconds=900)),
        # batch 8: market-structure universe (brokers, ATS/dark pools,
        # market makers, quant/prop, OMS, exchanges) — same generic
        # universe engine as batch 7, second coverage map. ~22 public
        # companies x 5 tags, SEC fair access.
        "ms_capex": (cfg.cadences.get("ms_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "market_structure"),
                 start + timedelta(seconds=1200)),
        # market-structure money-flow graph: zero HTTP, overlays ms_capex
        # financials onto the vendored deal graph. Starts after ms_capex.
        "ms_graph": (cfg.cadences.get("ms_graph", 604800), partial(fetch_universe_graph, store, "market_structure"),
                 start + timedelta(seconds=1500)),
        # batch 6: FINRA TRACE Treasury aggregates (daily; monthly backfill).
        "trace_treasury": (cfg.cadences.get("trace_treasury", 86400), partial(fetch_trace_treasury, store, get_bytes),
                 start + timedelta(seconds=2100)),
        # batch 6: FINRA TRACE monthly corporate/agency/securitized volumes.
        "trace_monthly": (cfg.cadences.get("trace_monthly", 30 * 86400), partial(fetch_trace_monthly, store, get_bytes),
                 start + timedelta(seconds=2400)),
        # batch 6: ICE Vantage daily STAR aggregate (securitized trading).
        "ice_star": (cfg.cadences.get("ice_star", 86400), partial(fetch_ice_star, store, get_bytes),
                 start + timedelta(seconds=2700)),
        # batch 6: FINRA short interest, twice-monthly (weekly poll).
        "finra_short": (cfg.cadences.get("finra_short", 604800), partial(fetch_finra_short, store, get_text),
                 start + timedelta(seconds=3000)),
        # batch 6: FINRA margin statistics, monthly.
        "finra_margin": (cfg.cadences.get("finra_margin", 30 * 86400), partial(fetch_finra_margin, store, get_bytes),
                 start + timedelta(seconds=3300)),
        # computed short metrics (days-to-cover, SI velocity) — runs after finra_short.
        "short_metrics": (cfg.cadences.get("short_metrics", 86400), partial(refresh_short_metrics, store),
                 start + timedelta(seconds=3600)),
        # batch 9: tokenized-securities universe — same generic universe
        # fetchers, new universe_id. XBRL fundamentals for the 6 public
        # names (COIN/HOOD/BLSH/BTCS/NDAQ/ICE), then the money-flow graph.
        "tsv_capex": (cfg.cadences.get("tsv_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "tokenized_securities"),
                 start + timedelta(seconds=3600)),
        "tsv_graph": (cfg.cadences.get("tsv_graph", 604800), partial(fetch_universe_graph, store, "tokenized_securities"),
                 start + timedelta(seconds=3900)),
        # batch 9: TSV regulatory watch — SEC press releases + Federal
        # Register scan for TSV notices/orders (weekly; no central SEC
        # registry exists, so this is the machine-trackable part).
        "tsv_watch": (cfg.cadences.get("tsv_watch", 604800), partial(fetch_tsv_watch, store, get_text),
                 start + timedelta(seconds=4200)),
        # bank/fixed-income universe — same generic universe fetchers,
        # new universe_id. XBRL fundamentals for the 19 public names
        # (money-center, IBs, regionals, custody, GSEs, Jefferies), then
        # the money-flow graph.
        "bank_capex": (cfg.cadences.get("bank_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "bank_fixed_income"),
                 start + timedelta(seconds=4500)),
        "bank_graph": (cfg.cadences.get("bank_graph", 604800), partial(fetch_universe_graph, store, "bank_fixed_income"),
                 start + timedelta(seconds=4800)),
        # bank three-statement financials: SEC XBRL quarterly (balance sheet
        # + NII/non-interest income/net income, NIM proxy, loan-to-deposit).
        # Runs after bank_capex; same universe, separate job so the
        # directory build does not depend on it.
        "bank_financials": (cfg.cadences.get("bank_financials", 604800), partial(fetch_bank_financials, cfg, store, get_text),
                 start + timedelta(seconds=5100)),
        # finance directories: primary dealers, broker-dealers (BrokerCheck
        # CRDs), ATS venues, depository MPIDs. Monthly; polite scrapes.
        "finance_dirs": (cfg.cadences.get("finance_dirs", 2592000), partial(fetch_finance_dirs, cfg, store, get_text),
                 start + timedelta(seconds=5400)),
        # technology universe (trading tech, fintech infra, regtech,
        # crypto infra, post-trade) — same generic universe engine.
        # ~11 public companies x 5 tags, SEC fair access.
        "tech_capex": (cfg.cadences.get("tech_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "technology"),
                 start + timedelta(seconds=4500)),
        # technology money-flow graph: zero HTTP, overlays tech_capex
        # financials onto the vendored deal graph. Starts after tech_capex.
        "tech_graph": (cfg.cadences.get("tech_graph", 604800), partial(fetch_universe_graph, store, "technology"),
                 start + timedelta(seconds=4800)),
        # vendor universe (market-data vendors, index providers, rating
        # agencies, alt-data, research) — same generic universe engine.
        # ~6 public companies x 5 tags, SEC fair access.
        "vendor_capex": (cfg.cadences.get("vendor_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "vendor"),
                 start + timedelta(seconds=5100)),
        # vendor money-flow graph: zero HTTP, overlays vendor_capex
        # financials onto the vendored deal graph. Starts after vendor_capex.
        "vendor_graph": (cfg.cadences.get("vendor_graph", 604800), partial(fetch_universe_graph, store, "vendor"),
                 start + timedelta(seconds=5400)),
        # crypto universe (exchanges, miners, stablecoin issuers, spot BTC
        # ETF issuers, BTC treasury companies, DeFi protocols) — same generic
        # universe engine. ~14 public names x 5 tags, SEC fair access.
        # Complements technology.json's crypto_infra (custody/tech) and the
        # CoinGecko top-50 token breadth feed.
        "crypto_capex": (cfg.cadences.get("crypto_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "crypto"),
                 start + timedelta(seconds=6150)),
        # crypto money-flow graph: zero HTTP, overlays crypto_capex
        # financials onto the vendored deal graph. Starts after crypto_capex.
        "crypto_graph": (cfg.cadences.get("crypto_graph", 604800), partial(fetch_universe_graph, store, "crypto"),
                 start + timedelta(seconds=6450)),
        # ETF ecosystem universe (issuers, market makers, APs, liquidity
        # providers, index providers) — same generic universe engine.
        # ~15 public companies x 5 tags, SEC fair access.
        "etf_capex": (cfg.cadences.get("etf_capex", 604800), partial(fetch_universe_capex, cfg, store, get_text, "etf"),
                 start + timedelta(seconds=5700)),
        # ETF money-flow graph: zero HTTP, overlays etf_capex financials
        # onto the vendored deal graph. Starts after etf_capex.
        "etf_graph": (cfg.cadences.get("etf_graph", 604800), partial(fetch_universe_graph, store, "etf"),
                 start + timedelta(seconds=6000)),
        # batch 11: World Bank macro fundamentals (GDP/CPI/unemployment)
        # for the 13 bond-matrix countries — keyless, 3 batched calls.
        "worldbank": (cfg.cadences.get("worldbank", 604800), partial(fetch_worldbank, store, get_text),
                 start + timedelta(seconds=4500)),
        # batch 11: USAspending.gov federal fiscal pulse — monthly
        # obligations + top recipients/agencies, keyless, 3 POSTs.
        "usaspending": (cfg.cadences.get("usaspending", 604800), partial(fetch_usaspending, store, post_json),
                 start + timedelta(seconds=4800)),
        # batch 11: CoinGecko crypto breadth — top-50 by market cap,
        # keyless, one batched call per day.
        "coingecko": (cfg.cadences.get("coingecko", 86400), partial(fetch_coingecko, store, get_text),
                 start + timedelta(seconds=5100)),
        # Tokenized assets (RWA): DefiLlama RWA protocol TVL + CoinGecko
        # tokenized-asset market caps by class. Keyless; ~25 batched calls.
        "defillama_rwa": (cfg.cadences.get("defillama_rwa", 86400), partial(fetch_defillama_rwa, store, get_text),
                 start + timedelta(seconds=5250)),
        # CBOE delayed options chains (14 symbols) — aggregates + unusual
        # activity only, never full chains. Keyless; Yahoo v7 fallback.
        # Daily cadence, runs after the equity close job.
        "cboe_options": (cfg.cadences.get("cboe_options", 86400), partial(fetch_cboe_options, store, get_text),
                 start + timedelta(seconds=5550)),
        # ETF AUM/NAV/shares: iShares screener (T+1, keyless) + Yahoo
        # fallback for non-iShares funds. Flows derived at query time.
        "ishares_etf": (cfg.cadences.get("ishares_etf", 86400), partial(fetch_ishares_etf, store, get_text),
                 start + timedelta(seconds=5850)),
        # SEC XBRL quarterly backfill for crypto-ETF shares/AUM/NAV history
        # (daily feeds only give snapshots). Weekly cadence; descriptive
        # contact UA required by SEC fair-access rules.
        "sec_xbrl_etf": (cfg.cadences.get("sec_xbrl_etf", 604800), partial(fetch_sec_xbrl_etf, store, get_text,
                 (cfg.sec_data.user_agent if cfg.sec_data else
                  "hs-bloom/1.0 contact harrysugamakc@gmail.com")),
                 start + timedelta(seconds=6150)),
        # Inverse 13F: institutional holders per ETF (FMP, free key; 250
        # calls/day). Quarterly snapshots with a 45-day filing lag: the
        # fetcher targets the most recent fully-reported quarter and
        # processes DAILY_BUDGET symbols per run, skipping completed
        # symbol-quarters so partial runs resume cleanly. Daily cadence so
        # a new target quarter starts filling within a day of becoming due.
        # Harry 2026-10-09: FMP institutional-ownership is Ultimate-tier
        # paywalled (403 on both stable + v3). Replaced with free SEC EDGAR
        # 13F-HR inverse fetcher (edgar_13f_holders.py) — same output schema.
        "etf_holders_13f": (cfg.cadences.get("etf_holders_13f", 86400),
                 partial(fetch_edgar_13f_holders, store, get_text),
                 start + timedelta(seconds=6450)),
        # BDC XBRL fundamentals: quarterly balance sheet / income / cash
        # flow per listed BDC from SEC companyfacts (keyless). Weekly poll;
        # filings land ~45 days after quarter-end; upserts are idempotent.
        "bdc_financials": (cfg.cadences.get("bdc_financials", 604800),
                 partial(fetch_bdc_financials, store, get_text,
                 (cfg.sec_data.user_agent if cfg.sec_data else
                  "hs-bloom/1.0 contact harrysugamakc@gmail.com")),
                 start + timedelta(seconds=6600)),
        # Private Credit universe graph: enriches the vendored BDC/manager/
        # ETF/bond universe with Yahoo prices + XBRL fundamentals daily.
        "bdc_universe": (cfg.cadences.get("bdc_universe", 86400),
                 partial(fetch_bdc_universe, store, get_text),
                 start + timedelta(seconds=6750)),
        # NasdaqTrader Full Volume Summary — tape/exchange volume (shares,
        # trades, dollar vol; keyless CSVs, 30 trading days). Every run
        # upserts the full 30-day window; the DB accumulates from there
        # (Harry 2026-10-06: pull all historical available). Daily after close.
        "nasdaq_tape": (cfg.cadences.get("nasdaq_tape", 86400), partial(fetch_nasdaq_tape, store, get_text),
                 start + timedelta(seconds=6000)),
        # batch 11: OpenFIGI symbology enrichment — needs OPENFIGI_API_KEY
        # (free registration); skips cleanly without it, never fails.
        "openfigi": (cfg.cadences.get("openfigi", 604800), partial(fetch_openfigi, store, post_json),
                 start + timedelta(seconds=5400)),
        # batch 11: Finnhub earnings calendar + insider sentiment — needs
        # FINNHUB_API_KEY (free tier); skips cleanly without it. Daily when
        # keyed so the earnings window rolls.
        "finnhub": (cfg.cadences.get("finnhub", 86400), partial(fetch_finnhub, store, get_text),
                 start + timedelta(seconds=5700)),
        # batch 12: Polymarket top markets by 24h volume — keyless Gamma API,
        # one batched call per run, 30-min cadence for fresh prices.
        "polymarket": (cfg.cadences.get("polymarket", 1800), partial(fetch_polymarket, store, get_text),
                 start + timedelta(seconds=6000)),
        # batch 12: Kalshi open markets — keyless Trade API v2, universe call
        # plus per-series calls for cross-venue matchable markets.
        "kalshi": (cfg.cadences.get("kalshi", 1800), partial(fetch_kalshi, store, get_text),
                 start + timedelta(seconds=6300)),
        # batch 12: prediction-market edge engine — hourly compute job reading
        # the venue snapshots + price histories; bounded resolution checks.
        # Starts after both venue fetchers; same graceful-degradation contract.
        "pred_edge": (cfg.cadences.get("pred_edge", 3600), partial(fetch_pred_edge, store, get_text),
                 start + timedelta(seconds=6600)),
        # FOMC decision pricing: Kalshi vs Polymarket vs Fed funds
        # futures (keyless; daily snapshot of the next meeting).
        "fed_meetings": (cfg.cadences.get("fed_meetings", 86400), partial(fetch_fed_meetings, store, get_text),
                 start + timedelta(seconds=6650)),
        # FINRA bond market breadth + sentiment (daily aggregates from the
        # public dynarep API behind finra.org market-activity/market-sentiment;
        # history to 2018; keyless). Starts after the other FINRA jobs.
        "finra_breadth": (cfg.cadences.get("finra_breadth", 86400), partial(fetch_finra_breadth, store),
                 start + timedelta(seconds=6900)),
        # FINRA most-active corporate bonds (daily top-10 IG/HY/convertible
        # lists from the public dynarep API behind finra.org market-corp;
        # history to 2023; keyless). Starts after finra_breadth.
        "finra_corp": (cfg.cadences.get("finra_corp", 86400), partial(fetch_finra_corp, store),
                 start + timedelta(seconds=7200)),
        # FINRA Reg SHO daily short volume + OTC threshold list (both
        # keyless; threshold via the public FINRA Query API). Daily.
        # Starts after the other FINRA jobs.
        "finra_regsho": (cfg.cadences.get("finra_regsho", 86400), partial(fetch_finra_regsho, store, get_text, pt, post_json),
                 start + timedelta(seconds=7500)),
        # Per-ticker speculator stats (price, %1D, mcap, P/E, %YTD, 1Y
        # sparkline, off-52w-high, RS rank, SMAs) for the top-shorted table.
        # Finnhub-keyed; reads the fresh top-50 from regsho_daily, so it runs
        # right after finra_regsho. Daily.
        "ticker_stats": (cfg.cadences.get("ticker_stats", 86400), partial(fetch_ticker_stats, store, get_text),
                 start + timedelta(seconds=7650)),
        # FINRA capped volume report — monthly corporate/agency capped trade
        # volume (keyless CSV, 12 rolling months, published 1st business day).
        # Starts after the other FINRA jobs.
        "finra_capped": (cfg.cadences.get("finra_capped", 30 * 86400), partial(fetch_finra_capped, store, get_text),
                 start + timedelta(seconds=7800)),
        # FINRA IDS STAR — Structured Product Activity Reports (daily ZIPs,
        # keyless, 36-month backfill). Public equivalent of ICE Vantage.
        # Starts after the other FINRA jobs.
        "finra_ids_star": (cfg.cadences.get("finra_ids_star", 86400), partial(fetch_finra_ids_star, store, get_bytes),
                 start + timedelta(seconds=8100)),
        # FINRA TRACE Fact Book — quarterly workbooks (corporate/agency/
        # securitized XLSX, keyless; 12-quarter backfill on first run).
        # Starts after the other FINRA jobs.
        "finra_factbook": (cfg.cadences.get("finra_factbook", 30 * 86400), partial(fetch_finra_factbook, store, get_text, get_bytes),
                 start + timedelta(seconds=8250)),
        # FINRA TRACE Fact Book — annual Transaction/Issue/Participant
        # Information workbooks (time-of-day intervals, annual top lists,
        # dealer concentration, issues outstanding). Yearly refresh.
        "finra_factbook_annual": (cfg.cadences.get("finra_factbook_annual", 365 * 86400), partial(fetch_finra_factbook_annual, store, get_text, get_bytes),
                 start + timedelta(seconds=8550)),
        # FINRA ATS Transparency — dark-pool volume. Keyless monthly
        # blocksSummary (2016->) always runs; weekly ATS_W_* detail needs
        # FINRA_CLIENT_ID/FINRA_CLIENT_SECRET (skips gracefully without).
        # Weekly cadence (data is weekly, 2-4wk delayed). Starts after the
        # other FINRA jobs.
        "finra_ats": (cfg.cadences.get("finra_ats", 604800), partial(fetch_finra_ats, store),
                 start + timedelta(seconds=8700)),
        # FINRA OTC Market (otce.finra.org) — keyless api.finra.org.
        # Daily list / threshold / halts are daily; statistics monthly.
        # Full history on first run, then incremental.
        "finra_otc": (cfg.cadences.get("finra_otc", 86400), partial(fetch_finra_otc, store),
                 start + timedelta(seconds=8730)),
        # batch 13: OFR Short-Term Funding Monitor (keyless API; SOFR daily,
        # cleared repo daily-prelim, MMF monthly, dealer fails/RP/RRP weekly).
        # Daily job; series stored as-is via upsert.
        "ofr_stfm": (cfg.cadences.get("ofr_stfm", 86400), partial(fetch_ofr_stfm, cfg.ofr_stfm, store, get_text),
                 start + timedelta(seconds=8400)),
        # OFR Traders in Financial Futures (keyless; weekly). All 153 mnemonics
        # with full history (2013 ->); stored as cycle:tff-<slug>. Daily-max job
        # per OFR guidance; the doc 'tff' powers the POSITIONING TFF view.
        "ofr_tff": (cfg.cadences.get("ofr_tff", 86400), partial(fetch_ofr_tff, store, get_text),
                 start + timedelta(seconds=8430)),
        # NY Fed Corporate Bond Market Distress Index — weekly xlsx (keyless,
        # browser UA). Stores cycle:cmdi-market/ig/hy; full history on first run.
        "nyfed_cmdi": (cfg.cadences.get("nyfed_cmdi", 604800), partial(fetch_nyfed_cmdi, store, get_bytes),
                 start + timedelta(seconds=8700)),
        # batch 13: FRB SLOOS + Commercial Paper (Board source via FRED — the
        # DDP is retiring Nov 2026). Weekly poll, idempotent upserts.
        "frb_ddp": (cfg.cadences.get("frb_ddp", 604800), partial(fetch_frb_ddp, cfg.frb_ddp, store, fred_api_key, get_text),
                 start + timedelta(seconds=8700)),
        # batch 13: SEC N-CEN fund census (~8-16 MB receipt-batch zips; declared
        # contact UA, 1 req/2s). Monthly poll; quarterly receipt batches.
        "sec_ncen": (cfg.cadences.get("sec_ncen", 2592000), partial(fetch_sec_ncen, cfg.sec_data, store, get_bytes),
                 start + timedelta(seconds=9000)),
        # batch 13: SEC N-PORT holdings aggregates — aggregate-first, streamed
        # download, selective zip extraction; never unpacks the ~420 MB
        # archive. ~6-month holdings lag (public 3rd-month reports, 60-day delay).
        "sec_nport": (cfg.cadences.get("sec_nport", 2592000), partial(fetch_sec_nport, cfg.sec_data, store),
                 start + timedelta(seconds=9300)),
        # mutual-fund flows phase 2: per-fund MONTHLY net flows (reported
        # sales+reinvestment-redemption) + quarterly TNA from the same N-PORT
        # batch. Monthly poll; staggers after sec_nport's ~420 MB download.
        "nport_flows": (cfg.cadences.get("nport_flows", 2592000), partial(fetch_nport_flows, cfg.sec_data, store),
                 start + timedelta(seconds=9450)),
        # mutual-fund flows phase 1: ICI estimated long-term mutual fund
        # flows — weekly (estimated, ~1wk lag) + monthly (actual) aggregates.
        # Keyless 58 KB .xls; weekly poll. Aggregate only, no per-fund detail.
        "ici_flows": (cfg.cadences.get("ici_flows", 604800), partial(fetch_ici_mutual_flows, store, get_bytes),
                 start + timedelta(seconds=9750)),
        # batch 13: SEC Private Fund Statistics supporting XLSX — latest quarter
        # resolved at runtime from the index page; anchor-driven table parse.
        "sec_pfs": (cfg.cadences.get("sec_pfs", 2592000), partial(fetch_sec_pfs, cfg.sec_data, store, get_bytes),
                 start + timedelta(seconds=9600)),
        # batch: SEC CNS fails-to-deliver — twice-monthly half-month zips
        # (1st-half avail ~end of month M, 2nd-half ~15th of M+1). Polls
        # twice-monthly; the fetcher tracks completed YYYYMMa/b in the
        # sec_ftd_files doc, so the first run backfills 2004->present and
        # later runs pick up only new files. Idempotent upserts.
        "sec_ftd": (cfg.cadences.get("sec_ftd", 1296000), partial(fetch_sec_ftd, cfg.sec_data, store, get_bytes),
                 start + timedelta(seconds=10350)),
        # batch 13: FRED Z.1 holdings-by-holder (keyless FRED CSV; quarterly
        # levels in $mn, idempotent upserts). Monthly poll; quarterly data.
        "z1_holdings": (cfg.cadences.get("z1_holdings", 2592000), partial(fetch_z1_holdings, store, get_text_curl),
                 start + timedelta(seconds=9900)),
        # batch 13: MSPD Tables 1 + 3 (keyless Fiscal Data API; ~1 MB/month
        # CUSIP detail). Monthly poll; ~5-week publication lag.
        "mspd": (cfg.cadences.get("mspd", 2592000), partial(fetch_mspd, store, get_text),
                 start + timedelta(seconds=10200)),
        # batch 13: NY Fed SOMA Treasury holdings by CUSIP (keyless CSV;
        # weekly, as-of Wednesdays). Weekly poll.
        "soma_cusip": (cfg.cadences.get("soma_cusip", 604800), partial(fetch_soma_cusip, store, get_text),
                 start + timedelta(seconds=10500)),
        # batch 13: debt-outstanding slice cube (product x maturity x holder).
        # Compute-only: no network, reads the mspd/soma docs, skips cleanly
        # until all source docs exist. Daily.
        "debt_cube": (cfg.cadences.get("debt_cube", 86400), partial(refresh_debt_cube, store),
                 start + timedelta(seconds=10800)),
        # Stress Monitor 8 heatmaps: compute-only, reads FRED series history
        # from the store, caches the 8 matrices via put_doc. Daily; starts
        # after the macro_history job so fresh FRED data is in.
        "stress_heatmaps": (cfg.cadences.get("stress_heatmaps", 86400), partial(refresh_stress_heatmaps, store),
                 start + timedelta(seconds=11100)),
        # SIFMA US corporate bond issuance (IG + HY, $B, Refinitiv via SIFMA).
        # Monthly poll (~1 month publication lag); first run deep-backfills
        # Jan 2020 -> present from archived workbook snapshots, later runs
        # just upsert the live workbook's ~13 months. Primary-market credit
        # stress signal: HY issuance shutting is real stress.
        "sifma_issuance": (cfg.cadences.get("sifma_issuance", 2592000), partial(fetch_sifma_issuance, store, get_bytes),
                 start + timedelta(seconds=11250)),
        # REG WATCH: regulatory news + surveillance — agency RSS feeds (SEC,
        # CFTC, FINRA, Fed, OCC, FDIC, FSB, OFR), Federal Register rulemaking
        # tracker, Gemini one-line summaries, topic tagging. Hourly; one dead
        # feed never breaks the run. .get() guard like the rest.
        "regwatch": (cfg.cadences.get("regwatch", 3600), partial(fetch_regwatch, store, get_text),
                 start + timedelta(seconds=5400)),
        "newsletter": (cfg.cadences["insights"], partial(deliver_newsletter, store, smtp_cfg), start + timedelta(seconds=5)),
    }
    # Boot catch-up (see _catchup_first_runs): overdue staggered jobs run
    # soon after boot instead of waiting out their full stagger offsets.
    _catchup_first_runs(fetchers, store, start)
    for name, (seconds, fn, next_run_time) in fetchers.items():
        scheduler.add_job(
            partial(run_fetcher, name, store, fn),
            "interval",
            seconds=seconds,
            id=name,
            next_run_time=next_run_time,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,  # jobs are registered before the ASGI startup hook
                                    # starts the scheduler; default 1s grace silently
                                    # drops every "fire immediately" startup run
        )
