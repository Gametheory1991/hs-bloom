"""Wraps every fetcher run: status recording + total error isolation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from collector.store import Store

log = logging.getLogger(__name__)

FetchFn = Callable[[], Awaitable[str]]  # returns active source label on success

JOB_RUNS_DOC = "job_runs"  # doc key: {job_name: last_success_iso}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def run_fetcher(name: str, store: Store, fn: FetchFn) -> None:
    try:
        active_source = await fn()
        store.record_success(name, active_source)
        # persist last-run so the scheduler can catch up overdue jobs on boot
        doc = store.doc(JOB_RUNS_DOC)
        runs = dict(doc.payload) if doc and isinstance(doc.payload, dict) else {}
        runs[name] = _now()
        store.put_doc(JOB_RUNS_DOC, runs, "scheduler")
    except Exception as exc:  # noqa: BLE001 — isolation is the contract
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("fetcher %s failed: %s", name, msg, exc_info=exc)
        store.record_error(name, msg)
