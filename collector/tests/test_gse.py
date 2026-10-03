"""Tests for the GSE retained-portfolio fetcher (Fannie Mae + Freddie Mac)."""
from datetime import date
from pathlib import Path

import pytest
import yaml

from collector.fetchers import gse
from collector.fetchers.gse import (
    FANNIE_URL,
    FREDDIE_URL,
    fetch_gse,
    parse_fannie,
    parse_freddie,
    _month_ends,
)
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"


def test_parse_freddie_tables():
    text = (FIX / "gse_freddie.txt").read_text()
    retained, agency = parse_freddie(text)
    # Table 1's same-shaped row (Aug 2025 $42,143...) must NOT leak in, and
    # Table 4's debt rows after the end marker must NOT leak in either.
    assert retained == [
        (date(2025, 8, 31), 113546.0),
        (date(2025, 9, 30), 116423.0),
        (date(2025, 10, 31), 121750.0),
        (date(2026, 1, 31), 136809.0),
        (date(2026, 2, 28), 138572.0),
    ]
    assert [v for _, v in agency] == [30630.0, 30907.0, 34365.0, 48547.0, 55585.0]
    assert [d for d, _ in agency] == [d for d, _ in retained]


def test_parse_freddie_no_rows_raises():
    with pytest.raises(ValueError):
        parse_freddie("nothing but prose here")


def test_parse_fannie_table3():
    text = (FIX / "gse_fannie.txt").read_text()
    pts = parse_fannie(text)
    # Full-Year rows and the Table 5 / delinquency / rate-risk footnote lines
    # must not parse; Table 4's side-by-side rows have no parens and must not
    # match either.
    assert pts == [
        (date(2025, 8, 31), 93304.0),
        (date(2025, 9, 30), 98779.0),
        (date(2025, 10, 31), 111843.0),
        (date(2026, 1, 31), 141638.0),
        (date(2026, 2, 28), 150393.0),
    ]


def test_parse_fannie_no_rows_raises():
    with pytest.raises(ValueError):
        parse_fannie("nothing but prose here")


def test_month_ends_newest_first():
    ends = _month_ends(4, today=date(2026, 10, 3))
    assert ends == [
        date(2026, 10, 31), date(2026, 9, 30),
        date(2026, 8, 31), date(2026, 7, 31),
    ]


def test_url_patterns():
    d = date(2026, 8, 31)
    assert FANNIE_URL.format(mm="08", dd="31", yy="26") == \
        "https://www.fanniemae.com/media/document/pdf/083126.pdf"
    assert FREDDIE_URL.format(mm="08", yy="26") == \
        "https://www.freddiemac.com/investors/financials/pdf/0826mvs.pdf"
    assert d.day == 31  # sanity: month-end day feeds the Fannie pattern


def _pdf_bytes_for(text: str) -> bytes:
    return b"%PDF-1.4 fake\n" + text.encode()


async def _fake_get_bytes(mapping: dict):
    async def get_bytes(url, params=None, headers=None):
        if url in mapping:
            return mapping[url]
        raise RuntimeError(f"HTTP 404 for {url}")
    return get_bytes


def test_fetch_gse_end_to_end(monkeypatch):
    fred_text = (FIX / "gse_freddie.txt").read_text()
    fan_text = (FIX / "gse_fannie.txt").read_text()
    monkeypatch.setattr(gse, "_pdftotext", lambda pdf: pdf.split(b"\n", 1)[1].decode())
    store = Store(":memory:")

    async def get_bytes(url, params=None, headers=None):
        # newest month-end first: Oct 2026 PDF missing (not released yet),
        # Sep 2026 present for both enterprises.
        if "103126" in url or "1026mvs" in url:
            raise RuntimeError("HTTP 404")
        if "fanniemae" in url:
            return _pdf_bytes_for(fan_text)
        return _pdf_bytes_for(fred_text)

    import asyncio
    result = asyncio.run(fetch_gse(store, get_bytes))
    assert result == "gse"
    assert store.points("gse:fannie-retained")[date(2026, 2, 28)] == 150393.0
    assert store.points("gse:freddie-retained")[date(2026, 2, 28)] == 138572.0
    assert store.points("gse:freddie-agency")[date(2026, 2, 28)] == 55585.0
    doc = store.doc("gse")
    assert doc.payload["fannie_retained"]["value_usd_m"] == 150393.0
    assert doc.payload["freddie_retained"]["value_usd_m"] == 138572.0


def test_fetch_gse_per_source_isolation(monkeypatch):
    """Freddie failing must not starve Fannie (and vice versa)."""
    fan_text = (FIX / "gse_fannie.txt").read_text()
    monkeypatch.setattr(gse, "_pdftotext", lambda pdf: pdf.split(b"\n", 1)[1].decode())
    store = Store(":memory:")

    async def get_bytes(url, params=None, headers=None):
        if "fanniemae" in url:
            return _pdf_bytes_for(fan_text)
        raise RuntimeError("HTTP 404 — freddie down")

    import asyncio
    assert asyncio.run(fetch_gse(store, get_bytes)) == "gse"
    assert store.points("gse:fannie-retained")
    assert store.points("gse:freddie-retained") == {}


def test_fetch_gse_both_down_raises(monkeypatch):
    monkeypatch.setattr(gse, "_pdftotext", lambda pdf: "")
    store = Store(":memory:")

    async def get_bytes(url, params=None, headers=None):
        raise RuntimeError("HTTP 404")

    import asyncio
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_gse(store, get_bytes))


def test_pdftotext_missing_gives_clear_error(monkeypatch):
    monkeypatch.setattr(gse.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="poppler-utils"):
        gse._pdftotext(b"%PDF-1.4")


def test_millennium_snippet():
    """The 13F snippet carries Millennium's EDGAR-verified CIK."""
    raw = yaml.safe_load(
        (Path(__file__).parent.parent.parent / "millennium-13f-snippet.yaml").read_text()
    )
    (entry,) = raw["thirteenf"]["watchlist_add"]
    assert entry["name"] == "Millennium Management"
    assert entry["cik"] == "0001273087"  # verified live 2026-10-03 via EDGAR
    assert len(entry["cik"]) == 10 and entry["cik"].isdigit()
