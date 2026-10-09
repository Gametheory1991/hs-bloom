from datetime import date
from pathlib import Path

import pytest

from collector.fetchers.cboe import fetch_daily_ratios, fetch_ratio_history, parse_daily

FIXTURE = (Path(__file__).parent / "fixtures" / "cboe_daily.json").read_text()


def test_parse_daily_extracts_named_ratio():
    assert parse_daily(FIXTURE, "TOTAL PUT/CALL RATIO") == 0.72
    assert parse_daily(FIXTURE, "EQUITY PUT/CALL RATIO") == 0.51


def test_parse_daily_unknown_name_raises():
    with pytest.raises(ValueError):
        parse_daily(FIXTURE, "NO SUCH RATIO")


async def test_fetch_daily_ratios_one_file_for_all_ratios():
    seen = []

    async def fake_get(url, params=None, headers=None):
        seen.append(url)
        return FIXTURE

    # Sat 2026-08-22: skips the weekend, one fetch of Friday's file
    out = await fetch_daily_ratios(
        ["TOTAL PUT/CALL RATIO", "EQUITY PUT/CALL RATIO"],
        fake_get, today=date(2026, 8, 22),
    )
    assert len(seen) == 1 and "2026-08-21_daily_options" in seen[0]
    assert out["TOTAL PUT/CALL RATIO"] == (date(2026, 8, 21), 0.72)
    assert out["EQUITY PUT/CALL RATIO"] == (date(2026, 8, 21), 0.51)


async def test_fetch_daily_ratios_missing_ratio_raises():
    async def fake_get(url, params=None, headers=None):
        return FIXTURE

    with pytest.raises(ValueError, match="missing ratio"):
        await fetch_daily_ratios(["NO SUCH RATIO"], fake_get, today=date(2026, 8, 24))


async def test_fetch_ratio_history_walks_weekdays_and_skips_holidays():
    seen = []

    async def fake_get(url, params=None, headers=None):
        seen.append(url)
        if "2026-08-21" in url:  # pretend Friday was a holiday: CDN 403s
            raise RuntimeError("HTTP 403")
        return FIXTURE

    # Mon 2026-08-24 back 7 days: weekdays 8/18..8/24 = 5 URLs
    pts = await fetch_ratio_history(
        "TOTAL PUT/CALL RATIO", fake_get, days=7, today=date(2026, 8, 24)
    )
    assert len(seen) == 5
    assert all("_daily_options" in u for u in seen)
    assert (date(2026, 8, 24), 0.72) in pts
    assert all(d != date(2026, 8, 21) for d, _ in pts)  # holiday skipped
    assert all(d.weekday() < 5 for d, _ in pts)
