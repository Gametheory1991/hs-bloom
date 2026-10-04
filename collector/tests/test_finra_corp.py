"""Tests for the FINRA most-active corporate bonds fetcher.

Uses inline samples shaped like the real dynarep API responses
(MostActiveCorporateSecurities) — no binary fixtures, no network.
"""
from __future__ import annotations

import asyncio
from datetime import date

from collector.fetchers import finra_corp
from collector.fetchers.finra_dynarep import DynarepSession  # noqa: F401 (import surface)

import pytest


def _row(issuer, symbol, cat, last, yld, chg, day="2026-10-02"):
    return {
        "issuerName": issuer,
        "issueSymbolIdentifier": symbol,
        "securityTypeCode": cat,
        "couponRate": 4.5,
        "maturityDate": "2030-01-15 00:00:00.000",
        "moodysRating": "Baa2",
        "standardAndPoorsRating": "BBB",
        "highPrice": last + 1,
        "lowPrice": last - 1,
        "lastPrice": last,
        "priceChangeAmount": chg,
        "yieldPercent": yld,
        "reportDate": f"{day} 00:00:00.000",
    }


SAMPLE_ROWS = [
    _row("ACME CORP", "ACME123", "inv", 102.5, 4.2, 0.3),
    _row("ACME CORP 2", "ACME124", "inv", 99.0, 4.8, -0.2),
    _row("RISKY INC", "RSK999", "hy", 87.5, 8.1, 1.1),
    _row("RISKY INC 2", "RSK998", "hy", 91.0, 7.4, -0.5),
    _row("CONV CO", "CNV111", "conv", 110.0, 2.5, 2.0),
    # garbage prints are filtered, not fatal
    _row("BAD DATA", "BAD000", "conv", 1050.0, -84.5, 0.0),
    _row("UNKNOWN CAT", "UNK000", "muni", 100.0, 5.0, 0.0),
]


def test_parse_most_active_averages():
    series, lists = finra_corp.parse_most_active(SAMPLE_ROWS, "corp")
    # IG: (102.5+99)/2=100.75 price, (4.2+4.8)/2=4.5 yield, (0.3-0.2)/2=0.05 chg
    ig_y = series["finra-corp-ig-avgyield"][date(2026, 10, 2)]
    assert ig_y == 4.5
    assert series["finra-corp-ig-avgprice"][date(2026, 10, 2)] == 100.75
    assert series["finra-corp-ig-avgchg"][date(2026, 10, 2)] == pytest.approx(0.05)
    # HY
    assert series["finra-corp-hy-avgyield"][date(2026, 10, 2)] == 7.75
    # converts: BAD DATA row filtered by price guard; only CONV CO counts
    assert series["finra-corp-conv-avgyield"][date(2026, 10, 2)] == 2.5
    assert series["finra-corp-conv-avgprice"][date(2026, 10, 2)] == 110.0
    # unknown category skipped
    assert not any("muni" in k for k in series)
    # bond lists captured for the snapshot doc (BAD DATA + UNK skipped)
    assert len(lists["2026-10-02"]) == 5


def test_parse_most_active_empty():
    series, lists = finra_corp.parse_most_active([], "corp")
    assert series == {}
    assert lists == {}


def test_corp_series_ids():
    ids = finra_corp.corp_series_ids()
    assert len(ids) == 18  # 2 datasets x 3 cats x 3 metrics
    assert "finra-corp-ig-avgyield" in ids
    assert "finra-corp144a-conv-avgchg" in ids


class FakeSession:
    """Fake DynarepSession returning canned rows per dataset."""

    def __init__(self, rows_by_ds):
        self._rows = rows_by_ds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def query(self, dataset, fields, start, end):
        return self._rows.get(dataset, [])


class FakeStore:
    def __init__(self):
        self.series: dict[str, dict] = {}
        self.docs: dict[str, dict] = {}

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def points(self, key, since=None):
        return self.series.get(key, {})

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        return None if d is None else type("Doc", (), d)()


def test_fetch_finra_corp_job():
    rows = {"MostActiveCorporateSecurities": SAMPLE_ROWS,
            "MostActiveCorporate144ASecurities": []}
    store = FakeStore()
    out = asyncio.run(finra_corp.fetch_finra_corp(
        store, today=date(2026, 10, 3),
        session_factory=lambda: FakeSession(rows)))
    assert out == finra_corp.SOURCE
    assert store.points("cycle:finra-corp-ig-avgyield")
    # 144A empty is fine; corp data stored
    doc = store.docs["finra_corp"]["payload"]
    assert doc["as_of"] == "2026-10-02"
    assert doc["status"] == "ok"
    assert "finra-corp-ig-avgyield" in doc["series"]
    assert doc["lists"]["corp"]["bonds"]


def test_fetch_finra_corp_error_recorded():
    class BoomSession(FakeSession):
        async def query(self, dataset, fields, start, end):
            raise RuntimeError("HTTP 403 blocked")

    store = FakeStore()
    try:
        asyncio.run(finra_corp.fetch_finra_corp(
            store, today=date(2026, 10, 3),
            session_factory=lambda: BoomSession({})))
    except RuntimeError as e:
        assert "finra_corp fetch failed" in str(e)
    else:
        raise AssertionError("should have raised")
    assert store.docs["finra_corp"]["payload"]["status"] == "error"


def test_fetch_finra_corp_empty_raises():
    store = FakeStore()
    try:
        asyncio.run(finra_corp.fetch_finra_corp(
            store, today=date(2026, 10, 3),
            session_factory=lambda: FakeSession({})))
    except RuntimeError as e:
        assert "empty response" in str(e)
    else:
        raise AssertionError("should have raised")
