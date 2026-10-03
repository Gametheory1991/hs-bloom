"""Registers one APScheduler job per fetcher, all wrapped in run_fetcher.

Every job also fires once immediately on startup (next_run_time=now) so a
fresh deployment populates within seconds instead of one full cadence. That
relies on a generous misfire_grace_time: jobs are registered during build(),
before uvicorn's ASGI startup hook actually starts the scheduler, and that
gap alone can exceed APScheduler's default 1s grace — silently dropping the
startup run on every deployment.

The newsletter job intentionally starts a few seconds after insights so a new
digest exists before the first delivery attempt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial

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
from collector.fetchers.ai_graph import fetch_universe_graph
from collector.fetchers.tsv_watch import fetch_tsv_watch
from collector.fetchers.macro import fetch_calendar_if_due
from collector.fetchers.midnight import fetch_midnight
from collector.fetchers.morpho import fetch_morpho
from collector.fetchers.movers import fetch_movers
from collector.fetchers.news import fetch_news
from collector.fetchers.ofr import fetch_ofr
from collector.fetchers.refs import fetch_refs
from collector.fetchers.risk import refresh_risk
from collector.fetchers.thirteenf import fetch_thirteenf
from collector.notify import refresh_digest_and_notify
from collector.fetchers.tic import fetch_tic
from collector.fetchers.voldash import refresh_voldash
from collector.fetchers.auctions import fetch_auctions
from collector.fetchers.dealer import fetch_dealer
from collector.fetchers.finra_margin import fetch_finra_margin
from collector.fetchers.finra_short import fetch_finra_short
from collector.fetchers.ice_star import fetch_ice_star
from collector.fetchers.refs_history import fetch_refs_history
from collector.fetchers.trace_monthly import fetch_trace_monthly
from collector.fetchers.trace_treasury import fetch_trace_treasury
from collector.fetchers.zyfai import fetch_defi
from collector.http import GetBytes, GetText, PostJson
from collector.newsletter import SmtpCfg, deliver_newsletter
from collector.runner import run_fetcher
from collector.store import Store

MACRO_HISTORY_SECONDS = 86400  # daily; not config — no reason to tune it
GSE_SECONDS = 30 * 86400  # monthly; the GSE summaries release ~25d after month-end


def register_jobs(
    scheduler: AsyncIOScheduler,
    cfg: Config,
    store: Store,
    get_text: GetText,
    post_json: PostJson,
    get_bytes: GetBytes,
    fred_api_key: str,
    smtp_cfg: SmtpCfg,
) -> None:
    start = datetime.now(timezone.utc)
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
        "tic": (cfg.cadences["tic"], partial(fetch_tic, cfg.tic, store, get_text), start),
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
        # ~60 public companies x 5 tags, SEC fair access, one bad filer
        # never fails the job. .get() guard like the rest.
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
        "newsletter": (cfg.cadences["insights"], partial(deliver_newsletter, store, smtp_cfg), start + timedelta(seconds=5)),
    }
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
