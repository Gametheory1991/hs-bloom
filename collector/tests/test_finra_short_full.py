"""Tests for the FINRA short-interest full-universe ingest + 2020 backfill.

Offline against the real-format fixture (no network).
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from collector.fetchers import finra_short
from collector.fetchers.finra_short import (
    fetch_finra_short,
    parse_short,
    parse_short_rows,
    settlement_dates,
)
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"
TEXT = (FIX / "finra_short_sample.csv").read_text()


def test_parse_short_rows_full_fields():
    asof, total, rows = parse_short_rows(TEXT)
    assert asof == date(2026, 9, 15)
    assert len(rows) == 18
    assert total == sum(r["short"] for r in rows)
    msft = next(r for r in rows if r["symbol"] == "MSFT")
    assert msft["name"] == "Microsoft Corporation"
    assert msft["exchange"] == "Q" and msft["market_class"] == "NSDQ"
    assert msft["prev"] == 49120433.0
    assert msft["adv"] == 52341200.0
    assert msft["dtc"] == 0.92
    assert msft["chg_pct"] == -1.69 and msft["chg_nom"] == -829088.0
    assert msft["split_flag"] is None and msft["revision"] is None
    # legacy wrapper still returns the watchlist shape
    _a, parsed = parse_short(TEXT)
    assert parsed["_total"] == total
    assert parsed["NVDA"]["short"] == 152334455.0


def test_settlement_dates_weekend_15th():
    # 2020-08-15 was a Saturday -> settlement file dated the 14th
    ds = settlement_dates(date(2020, 8, 1), date(2020, 8, 31))
    assert ds == [date(2020, 8, 14), date(2020, 8, 31)]
    # 2020-02: 15th was a Saturday; 2020-02-29 a Saturday -> 28th
    ds = settlement_dates(date(2020, 2, 1), date(2020, 2, 29))
    assert ds == [date(2020, 2, 14), date(2020, 2, 28)]
    # 24 settlements a year
    assert len(settlement_dates(date(2020, 1, 15), date(2020, 12, 31))) == 24


class _Fakes:
    """Only the 2026-09-15 file exists (latest probe target)."""

    async def get_text(self, url, params=None, headers=None):
        if "shrt20260915.csv" in url:
            return TEXT * 5  # pad: the job rejects suspiciously small files
        raise RuntimeError(f"HTTP 404 for {url}")


def test_job_stores_full_universe_and_backfills(tmp_path, monkeypatch):
    monkeypatch.setattr(finra_short, "REQUEST_GAP", 0)
    monkeypatch.setattr(finra_short, "BACKFILL_SINCE", date(2026, 9, 1))
    store = Store(tmp_path / "t.db")
    assert asyncio.run(fetch_finra_short(store, _Fakes().get_text,
                                         today=date(2026, 10, 3))) == "finra-short-interest"
    # full universe stored for 2026-09-15
    settlements = store.short_interest_settlements()
    assert (date(2026, 9, 15), 18) in settlements
    # backfill covered the 2026-08-31 settlement too (fixture served? no —
    # fake 404s it, so only the 2026-09-15 settlement lands; tolerated)
    scope = store.short_interest_scope()
    assert scope["rows"] == 18
    total, rows = store.short_interest_rows(date(2026, 9, 15))
    assert total == 18
    assert rows[0]["symbol"] == "NVDA"  # default sort: short desc
    total_s, rows_s = store.short_interest_rows(date(2026, 9, 15), search="micro")
    assert total_s == 1 and rows_s[0]["symbol"] == "MSFT"
    hist = store.short_interest_symbol_history("MSFT")
    assert len(hist) == 1 and hist[0]["chg_nom"] == -829088.0
    # existing aggregate + watchlist series intact
    assert store.points("cycle:finra-short-total")[date(2026, 9, 15)] > 0
    assert store.points("cycle:short-MSFT")[date(2026, 9, 15)] == 48291345.0
    doc = store.doc("finra_short").payload
    assert doc["as_of"] == "2026-09-15"
    assert doc["tickers"]["MSFT"]["short"] == 48291345.0


def test_job_holiday_map_skips_resolved(tmp_path, monkeypatch):
    """A holiday settlement resolved to its preceding-business-day file is
    not refetched on the next run (persistent holiday map)."""
    import json

    monkeypatch.setattr(finra_short, "REQUEST_GAP", 0)
    monkeypatch.setattr(finra_short, "BACKFILL_SINCE", date(2021, 2, 12))
    store = Store(tmp_path / "t.db")

    async def get_text(url, params=None, headers=None):
        if "shrt20210212.csv" in url:
            return TEXT * 5  # the Presidents'-Day-week file
        raise RuntimeError(f"HTTP 404 for {url}")

    # run 1: latest probe finds nothing recent (all 404) -> RuntimeError?
    # Instead seed the latest settlement so the probe succeeds fast.
    store.upsert_short_interest([("2026-09-15", "AAA", "Aaa", "Q", "NSDQ",
                                  1.0, 0.0, 1.0, 1.0, 10.0, 0.1, None, None)])

    async def get_text2(url, params=None, headers=None):
        if "shrt20260915.csv" in url:
            return TEXT * 5
        if "shrt20210212.csv" in url:
            # the holiday-week file carries its own settlement date
            return (TEXT * 5).replace("20260915", "20210212").replace(
                "2026-09-15", "2021-02-12")
        raise RuntimeError(f"HTTP 404 for {url}")

    calls = []

    async def counting(url, params=None, headers=None):
        calls.append(url)
        return await get_text2(url, params, headers)

    asyncio.run(finra_short.fetch_finra_short(store, counting,
                                              today=date(2026, 10, 3)))
    assert any("20210212" in u for u in calls)
    hmap = store.doc("finra_short_holiday_map").payload
    assert hmap.get("2021-02-15") == "2021-02-12"

    # run 2: the holiday settlement is skipped entirely (2021-02-26 month-end
    # is still attempted and 404-tolerated — that's the fake, not the map)
    calls.clear()
    asyncio.run(finra_short.fetch_finra_short(store, counting,
                                              today=date(2026, 10, 3)))
    assert not any("20210215" in u or "20210212" in u for u in calls)


def test_job_backfill_resumable(tmp_path, monkeypatch):
    """A settlement already in the table is not refetched."""
    monkeypatch.setattr(finra_short, "REQUEST_GAP", 0)
    monkeypatch.setattr(finra_short, "BACKFILL_SINCE", date(2026, 8, 14))
    store = Store(tmp_path / "t.db")
    # pretend 2026-08-31 was stored by an earlier interrupted run
    store.upsert_short_interest([("2026-08-31", "AAA", "Aaa", "Q", "NSDQ",
                                  1.0, 0.0, 1.0, 1.0, 10.0, 0.1, None, None)])
    calls = []

    async def get_text(url, params=None, headers=None):
        calls.append(url)
        if "shrt20260915.csv" in url:
            return TEXT * 5  # pad: the job rejects suspiciously small files
        raise RuntimeError(f"HTTP 404 for {url}")

    asyncio.run(fetch_finra_short(store, get_text, today=date(2026, 10, 3)))
    fetched = [u for u in calls if "shrt" in u]
    # latest probe + one backfill attempt for 2026-08-14 (404, tolerated);
    # 2026-08-31 never requested again
    assert not any("20260831" in u for u in fetched)
    assert sum("20260915" in u for u in fetched) >= 1
    # idempotent: second run stores the same 18 rows, no duplicates
    asyncio.run(fetch_finra_short(store, get_text, today=date(2026, 10, 3)))
    assert store.short_interest_scope()["rows"] == 19  # 18 + the seeded AAA row
