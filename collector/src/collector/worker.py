"""Standalone fetcher worker: runs the APScheduler job loop with no web server.

Deployed as a Render Background Worker. Shares Postgres with the web service
via DATABASE_URL. Isolates fetcher memory pressure from the web process so a
heavy data job can never OOM-kill the site (the 2026-10-09 crash loop).
"""
from __future__ import annotations

import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from collector.config import load_config
from collector.http import get_bytes, get_text, post_json
from collector.main import build_store  # reuse store construction
from collector.newsletter import load_smtp_cfg
from collector.scheduler import register_jobs

log = logging.getLogger("collector.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config(os.environ.get("CONFIG_PATH", "/app/config.yaml"))
    store = build_store(cfg)
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_jobs(
        scheduler, cfg, store, get_text, post_json, get_bytes,
        os.environ.get("FRED_API_KEY", ""), load_smtp_cfg(),
    )
    scheduler.start()
    log.info("worker scheduler started (%d jobs)", len(scheduler.get_jobs()))
    try:
        await asyncio.Event().wait()  # run forever
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
