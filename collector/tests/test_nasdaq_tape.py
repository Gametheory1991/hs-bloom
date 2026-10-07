"""NasdaqTrader tape fetcher tests: CSV parsing, slug, backfill upserts."""
import csv
import io

import pytest

from collector.fetchers import nasdaq_tape
from collector.fetchers.nasdaq_tape import (
    fetch_nasdaq_tape,
    parse_tape_csv,
    slugify,
)
from collector.store import Store


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Exchange", "Tape A", "Tape A %", "Tape B", "Tape B %",
                "Tape C", "Tape C %", "Total", "Total %",
                "Tape A Moving", "Tape A % Moving"])
    w.writerows(rows)
    return buf.getvalue()


SAMPLE = _csv([
    ["08/24/2026", "NASDAQ", "363530489", "8.0%", "249500797", "9.3%",
     "1459932769", "19.2%", "2072964055", "14.0%", "364152729", "8.4%"],
    ["08/24/2026", "FINRA/NASDAQ TRF", "1000000", "0.0%", "2000000", "0.1%",
     "3000000", "0.0%", "6000000", "0.0%", "1000000", "0.0%"],
    ["08/25/2026", "NASDAQ", "400000000", "8.8%", "250000000", "9.3%",
     "1500000000", "19.7%", "2150000000", "14.5%", "365000000", "8.4%"],
])


def test_slugify():
    assert slugify("FINRA/NASDAQ TRF") == "finra-nasdaq-trf"
    assert slugify("NYSE ARCA") == "nyse-arca"
    assert slugify("BATS Z") == "bats-z"


def test_parse_tape_csv():
    rows = parse_tape_csv(SAMPLE)
    assert len(rows) == 3
    r = rows[0]
    assert str(r["date"]) == "2026-08-24"
    assert r["exchange"] == "NASDAQ" and r["slug"] == "nasdaq"
    assert r["a"] == 363530489.0 and r["total"] == 2072964055.0


def test_fetch_backfills_full_window(tmp_path):
    store = Store(str(tmp_path / "t.db"))

    async def fake_get_text(url, params=None, headers=None):
        assert "nasdaqtrader.com" in url
        return SAMPLE

    import asyncio
    msg = asyncio.run(fetch_nasdaq_tape(store, fake_get_text))
    assert "2 venues" in msg and "2 days" in msg

    # per-venue series
    pts = store.points("cycle:tape-shares-nasdaq-total")
    assert len(pts) == 2
    # market-wide series = sum of venues
    mkt = store.points("cycle:tape-shares-all-total")
    assert len(mkt) == 2
    from datetime import date
    assert mkt[date(2026, 8, 24)] == pytest.approx(2072964055.0 + 6000000.0)

    # doc for the UI grid
    doc = store.doc("tape")
    assert doc is not None
    p = doc.payload
    assert p["as_of"] == "2026-08-25"
    assert p["history_days"] == 2
    assert p["venues"]["nasdaq"]["name"] == "NASDAQ"
    assert len(p["venues"]["nasdaq"]["shares"]) == 2
    assert p["venues"]["finra-nasdaq-trf"]["shares"][0][4] == 6000000.0

    # re-run is idempotent — no duplicate points
    asyncio.run(fetch_nasdaq_tape(store, fake_get_text))
    assert len(store.points("cycle:tape-shares-nasdaq-total")) == 2


def test_fetch_degrades_on_failure(tmp_path):
    store = Store(str(tmp_path / "t.db"))

    async def boom(url, params=None, headers=None):
        raise RuntimeError("HTTP 500")

    import asyncio
    msg = asyncio.run(fetch_nasdaq_tape(store, boom))
    assert "no data" in msg
