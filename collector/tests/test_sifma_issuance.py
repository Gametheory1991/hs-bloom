from datetime import date
from pathlib import Path

import pytest

from collector.fetchers.sifma_issuance import (
    _parse_month,
    backfill_from_snapshots,
    fetch_sifma_issuance,
    parse_sifma_issuance,
    resolve_workbook,
)
from collector.store import Store

FIXTURE = (Path(__file__).parent / "fixtures" / "sifma_issuance_test.xlsx").read_bytes()


def test_parse_sifma_monthly_values():
    series = parse_sifma_issuance(FIXTURE)
    hy = dict(series["cycle:hy-issuance-monthly"])
    ig = dict(series["cycle:ig-issuance-monthly"])
    # both Excel-serial and ISO-string date rows parse
    assert hy[date(2026, 7, 31)] == 19.168
    assert hy[date(2026, 8, 31)] == 26.926
    assert hy[date(2026, 9, 30)] == 60.463
    assert ig[date(2026, 9, 30)] == 190.986
    # annual / quarterly / YTD rows skipped
    assert len(hy) == 3
    assert len(ig) == 3


def test_parse_sifma_rejects_non_xlsx():
    with pytest.raises(ValueError, match="magic bytes"):
        parse_sifma_issuance(b"<HTML><H" + b"x" * 100)


def test_parse_month_labels():
    assert _parse_month("45930") == date(2025, 9, 30)
    assert _parse_month("2026-09-30 00:00:00") == date(2026, 9, 30)
    assert _parse_month("2015") is None
    assert _parse_month("3Q26") is None
    assert _parse_month("YTD 2026") is None
    assert _parse_month("") is None


async def test_resolve_workbook_prefers_primary():
    seen = []

    async def fake_get_bytes(url, params=None, headers=None):
        seen.append(url)
        return FIXTURE

    url, content = await resolve_workbook(fake_get_bytes)
    assert "US-Corporate-Bonds-Statistics-SIFMA.xlsx" in url
    assert content == FIXTURE
    assert len(seen) == 1  # no page scrape needed


async def test_resolve_workbook_falls_back_to_page_scrape():
    async def fake_get_bytes(url, params=None, headers=None):
        if "sifma.org/research" in url:
            return b'<a href="/wp-content/uploads/2026/09/Corp-Stats.xlsx">dl</a>'
        if "Corp-Stats.xlsx" in url:
            return FIXTURE
        raise RuntimeError("404")

    url, _ = await resolve_workbook(fake_get_bytes)
    assert url == "https://www.sifma.org/wp-content/uploads/2026/09/Corp-Stats.xlsx"


async def test_resolve_workbook_raises_clearly_when_everything_fails():
    async def fake_get_bytes(url, params=None, headers=None):
        if "sifma.org/research" in url:
            return b"<html>no downloads here</html>"
        raise RuntimeError("404")

    with pytest.raises(RuntimeError, match="no usable issuance workbook"):
        await resolve_workbook(fake_get_bytes)


async def test_backfill_merges_snapshots_newest_wins(monkeypatch):
    import collector.fetchers.sifma_issuance as mod

    snap1 = FIXTURE  # has Jul/Sep 2026
    # second snapshot: same Sep 2026 month with revised value
    async def fake_get_bytes(url, params=None, headers=None):
        return FIXTURE

    async def fake_try(get_bytes, url):
        return (url, FIXTURE)

    monkeypatch.setattr(mod, "_try_url", fake_try)
    monkeypatch.setattr(mod, "SNAPSHOT_URLS", ["s1", "s2"])
    store = Store(":memory:")
    n = await backfill_from_snapshots(store, fake_get_bytes)
    assert n == 6  # 3 months x 2 series, no duplicates
    pts = store.points("cycle:hy-issuance-monthly")
    assert pts[date(2026, 9, 30)] == 60.463


async def test_fetch_sifma_issuance_upserts_and_docs(tmp_path):
    async def fake_get_bytes(url, params=None, headers=None):
        return FIXTURE

    store = Store(tmp_path / "t.db")
    assert await fetch_sifma_issuance(store, fake_get_bytes) == "sifma"
    pts = store.points("cycle:hy-issuance-monthly")
    assert pts[date(2026, 9, 30)] == 60.463
    ipts = store.points("cycle:ig-issuance-monthly")
    assert ipts[date(2026, 7, 31)] == 139.069
    doc = store.doc("sifma_issuance")
    assert doc is not None
    assert doc.payload["latest_month"] == "2026-09-30"
    assert doc.payload["latest_hy_usd_b"] == 60.463
