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
from collector.fetchers.cycle import fetch_cycle
from collector.fetchers.equity import fetch_equity
from collector.fetchers.etf import fetch_etf_catalog
from collector.fetchers.fred import fetch_macro_history
from collector.fetchers.macro import fetch_calendar_if_due
from collector.fetchers.midnight import fetch_midnight
from collector.fetchers.morpho import fetch_morpho
from collector.fetchers.news import fetch_news
from collector.fetchers.refs import fetch_refs
from collector.fetchers.refs_history import fetch_refs_history
from collector.fetchers.zyfai import fetch_defi
from collector.http import GetBytes, GetText, PostJson
from collector.insights import refresh_digest
from collector.newsletter import SmtpCfg, deliver_newsletter
from collector.runner import run_fetcher
from collector.store import Store

MACRO_HISTORY_SECONDS = 86400  # daily; not config — no reason to tune it


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
        "etf": (cfg.cadences["etf"], partial(fetch_etf_catalog, cfg.etfdb_catalog_url, store, get_text), start),
        "macro_history": (MACRO_HISTORY_SECONDS, partial(fetch_macro_history, cfg.series, store, fred_api_key, get_text), start),
        "defi": (cfg.cadences["defi"], partial(fetch_defi, cfg.defi, cfg.zyfai_base, store, get_text), start),
        "midnight": (cfg.cadences["midnight"], partial(fetch_midnight, cfg.defi, cfg.midnight_base, store, get_text), start),
        "refs": (cfg.cadences["refs"], partial(fetch_refs, cfg.refs, store, get_text, post_json), start),
        "refs_history": (cfg.cadences["refs_history"], partial(fetch_refs_history, cfg.refs, store, get_text), start),
        "morpho": (cfg.cadences["morpho"], partial(fetch_morpho, cfg.defi, store, post_json), start),
        "cycle": (cfg.cadences["cycle"], partial(fetch_cycle, cfg.cycle_series, store, fred_api_key, get_text, get_bytes), start),
        "insights": (cfg.cadences["insights"], partial(refresh_digest, store, cfg), start),
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
