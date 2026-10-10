"""Wraps every fetcher run: status recording + total error isolation."""
from __future__ import annotations

import asyncio
import gc
import inspect
import logging
import resource
from datetime import datetime, timezone
from typing import Awaitable, Callable, Union

from collector.store import Store

log = logging.getLogger(__name__)

FetchFn = Callable[[], Union[Awaitable[str], str]]  # returns active source label on success

JOB_RUNS_DOC = "job_runs"  # doc key: {job_name: last_success_iso}

# Global concurrency cap: APScheduler fires 92 jobs on independent cadences
# with no built-in cross-job limit. When heavy jobs align (morning batch,
# post-deploy catch-up) they OOM the container (exit 137, observed
# 2026-10-08). This semaphore serializes beyond MAX_CONCURRENT_FETCHERS.
MAX_CONCURRENT_FETCHERS = 3
_fetcher_semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHERS)

# Memory watchdog: refuse to start a fetcher when RSS exceeds this fraction
# of the 2GB container. Prevents the OOM-kill crash loop (exit 137) that
# took the site down repeatedly on 2026-10-09. The skipped job retries on
# its next cadence; the web server stays alive.
MEMORY_LIMIT_FRACTION = 0.75  # 1.5GB of 2GB


def _rss_fraction() -> float:
    """Current RSS as a fraction of the 2GB container limit."""
    # ru_maxrss is KB on Linux
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (rss_kb * 1024) / (2 * 1024**3)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def run_fetcher(name: str, store: Store, fn: FetchFn) -> None:
    async with _fetcher_semaphore:
        # Memory watchdog: skip this run if we're close to the container
        # limit. The job retries on its next cadence; the web server survives.
        rss = _rss_fraction()
        if rss > MEMORY_LIMIT_FRACTION:
            log.warning("fetcher %s skipped: RSS at %.0f%% of container limit",
                        name, rss * 100)
            gc.collect()
            return
        try:
            result = fn()
            # refresh_xcorr / refresh_voldash are sync; everything else is async
            active_source = await result if inspect.isawaitable(result) else result
            store.record_success(name, active_source)
            # persist last-run so the scheduler can catch up overdue jobs on boot
            doc = store.doc(JOB_RUNS_DOC)
            runs = dict(doc.payload) if doc and isinstance(doc.payload, dict) else {}
            runs[name] = _now()
            # quiet write: a heartbeat must not invalidate the dashboard cache
            # (a data_version bump on every job success defeated memoization).
            # getattr keeps duck-typed test stores without the method working.
            getattr(store, "put_doc_quiet", store.put_doc)(JOB_RUNS_DOC, runs, "scheduler")
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            msg = f"{type(exc).__name__}: {exc}"
            log.warning("fetcher %s failed: %s", name, msg, exc_info=exc)
            store.record_error(name, msg)
        finally:
            # Reclaim fetcher memory aggressively. Python's allocator holds
            # freed blocks; without this, RSS ratchets up across jobs until
            # the OOM killer fires (the 2026-10-09 crash loop).
            gc.collect()
