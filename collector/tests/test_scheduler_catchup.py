"""Boot catch-up: overdue staggered jobs get early first runs; fresh ones keep offsets."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from collector.config import load_config
from collector.newsletter import load_smtp_cfg
from collector.runner import JOB_RUNS_DOC, run_fetcher
from collector.scheduler import CATCHUP_DELAY, CATCHUP_GAP, register_jobs
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


async def fake_get(url, params=None):
    raise AssertionError("no network in tests")


async def fake_post(url, json=None, headers=None):
    raise AssertionError("no network in tests")


async def fake_bytes(url, params=None, headers=None):
    raise AssertionError("no network in tests")


def _register(store):
    cfg = load_config(REPO_ROOT / "config.yaml")
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_jobs(
        scheduler, cfg, store, get_text=fake_get, post_json=fake_post,
        get_bytes=fake_bytes, fred_api_key="k",
        smtp_cfg=load_smtp_cfg({"SMTP_PORT": "465"}),
    )
    return {j.id: j for j in scheduler.get_jobs()}


def _first_run_delta_seconds(job):
    now = datetime.now(timezone.utc)
    nxt = job.next_run_time
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return (nxt - now).total_seconds()


def test_catchup_empty_store_gives_early_slots_in_order(tmp_path):
    jobs = _register(Store(tmp_path / "t.db"))
    # data jobs still fire at boot
    assert _first_run_delta_seconds(jobs["equity"]) < CATCHUP_DELAY
    # staggered compute jobs catch up early, in dependency order, spaced out
    ordered = ["risk", "stress", "country_risk", "xcorr", "voldash", "movers", "home_radar"]
    deltas = [_first_run_delta_seconds(jobs[n]) for n in ordered]
    assert all(d < 600 for d in deltas), deltas  # far sooner than the old +300s…+1800s offsets
    assert deltas == sorted(deltas), deltas  # dependency order preserved
    gaps = [b - a for a, b in zip(deltas, deltas[1:])]
    assert all(60 <= g <= 90 for g in gaps), gaps


def test_catchup_recent_run_keeps_large_offset(tmp_path):
    store = Store(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    now_s = now.isoformat().replace("+00:00", "Z")
    stale = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    # every staggered job ran recently except home_radar (stale)
    recent = {
        "risk": now_s, "stress": now_s, "country_risk": now_s, "xcorr": now_s, "voldash": now_s,
        "movers": now_s, "home_radar": stale, "hyperscaler": now_s,
        "ai_capex": now_s, "ai_graph": now_s, "ms_capex": now_s,
        "ms_graph": now_s, "trace_treasury": now_s, "trace_monthly": now_s,
        "ice_star": now_s, "finra_short": now_s, "finra_margin": now_s,
        "tsv_capex": now_s, "tsv_graph": now_s, "tsv_watch": now_s,
    }
    store.put_doc(JOB_RUNS_DOC, recent, "t")
    jobs = _register(store)
    assert 200 < _first_run_delta_seconds(jobs["risk"]) < 400  # ~+300s offset kept
    assert _first_run_delta_seconds(jobs["home_radar"]) < 120  # caught up early


def test_run_fetcher_persists_job_run(tmp_path):
    store = Store(tmp_path / "t.db")

    async def fn() -> str:
        return "yahoo"

    import asyncio
    asyncio.run(run_fetcher("equity", store, fn))
    doc = store.doc(JOB_RUNS_DOC)
    assert doc is not None and "equity" in doc.payload
    # failure does NOT record a run
    async def bad() -> str:
        raise RuntimeError("x")
    asyncio.run(run_fetcher("news", store, bad))
    assert "news" not in store.doc(JOB_RUNS_DOC).payload
