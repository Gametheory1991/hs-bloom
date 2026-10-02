"""Wire config, store, gateway, API, scheduler; run under uvicorn."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from collector.api import create_app
from collector.config import load_config
from collector.fetchers.fred_mcp import endpoint
from collector.http import get_bytes, get_text, post_json
from collector.newsletter import load_smtp_cfg
from collector.scheduler import register_jobs
from collector.store import Store

log = logging.getLogger(__name__)


def build() -> tuple[FastAPI, AsyncIOScheduler]:
    cfg = load_config(os.environ.get("CONFIG_PATH", "../config.yaml"))
    store = Store(cfg.db_path)
    if not os.environ.get("FRED_API_KEY") and not endpoint():
        log.warning("FRED_API_KEY not set; FRED-backed macro series and the US bond yield will fail")
    app = create_app(store, cfg)
    scheduler = AsyncIOScheduler(timezone="UTC")
    smtp_cfg = load_smtp_cfg()
    register_jobs(
        scheduler, cfg, store, get_text, post_json, get_bytes, os.environ.get("FRED_API_KEY", ""), smtp_cfg
    )
    # FastAPI dropped add_event_handler; router.on_startup/on_shutdown lists
    # are the remaining escape hatch for wiring events onto an app built
    # elsewhere (create_app doesn't accept a lifespan callable).
    app.router.on_startup.append(scheduler.start)
    app.router.on_shutdown.append(scheduler.shutdown)

    # Dev convenience: SERVE_UI=1 serves the repo's ui/ so no nginx is needed locally.
    ui_path = os.environ.get("UI_PATH")
    ui_dir = Path(ui_path) if ui_path else Path(__file__).resolve().parents[3] / "ui"  # parents[3] = repo root
    if os.environ.get("SERVE_UI") == "1" and ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

        # Same rationale as nginx.conf: without Cache-Control the browser
        # heuristically caches ES modules and keeps running stale JS after edits.
        @app.middleware("http")
        async def no_cache_ui(request, call_next):  # noqa: ANN001 — FastAPI middleware signature
            resp = await call_next(request)
            if not request.url.path.startswith("/api"):
                resp.headers.setdefault("Cache-Control", "no-cache")
            return resp
    return app, scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app, _scheduler = build()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
