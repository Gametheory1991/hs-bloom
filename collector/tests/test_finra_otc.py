"""Tests for the FINRA OTC Market fetcher. Offline — no network."""
from __future__ import annotations

import asyncio
from datetime import date

from collector.fetchers import finra_otc
from collector.fetchers.finra_otc import (
    _categorize_dl,
    _num,
    _slug,
    _store_stats,
    fetch_finra_otc,
)
from collector.store import Store


def _store():
    return Store(":memory:")


def test_slug():
    assert _slug("Other OTC") == "other-otc"
    assert _slug("") == "unknown"


def test_num():
    assert _num("123.5") == 123.5
    assert _num("") is None
    assert _num(None) is None
    assert _num("abc") is None


def test_categorize_dl():
    assert _categorize_dl({"securityAddFlag": "Y"}) == "additions"
    assert _categorize_dl({"securityDeleteFlag": "Y"}) == "deletions"
    assert _categorize_dl({"changeSymbolFlag": "Y"}) == "symbol_changes"
    assert _categorize_dl({"bankruptcyFlag": "Y"}) == "bankruptcy"
    assert _categorize_dl({"dividendTypeCode": "C"}) == "dividends"
    assert _categorize_dl({"changeSecurityAttributeFlag": "Y"}) == "attribute_changes"
    assert _categorize_dl({}) == "other"


def test_store_stats():
    store = _store()
    rows = [{
        "yearStartDate": "2024-01-01",
        "marketDescription": "Other OTC",
        "securityTypeDescription": "All",
        "totalShareVolume": "123456",
        "totalDollarVolume": "789.5",
        "averagePrice": "",
    }]
    n = _store_stats(store, rows, "yearStartDate", "yearly")
    assert n == 2
    pts = store.points("cycle:otc-yearly-other-otc-all-totalShareVolume")
    assert pts[date(2024, 1, 1)] == 123456.0


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Client:
    """Fake httpx client returning scripted dataset payloads."""

    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, json))
        ds = url.rsplit("/name/", 1)[-1]
        return _Resp(_PAYLOADS.get(ds, []))


_PAYLOADS = {
    "YearlyMarketStatistics": [{
        "yearStartDate": "2024-01-01", "marketDescription": "Other OTC",
        "securityTypeDescription": "All", "totalShareVolume": "100",
        "totalDollarVolume": "50", "totalTransactionCount": "10",
        "totalIssueCount": "5", "totalIssueTradedCount": "4",
        "averageShareVolume": "1", "averageDollarVolume": "0.5",
        "averagePrice": "0.01",
    }],
    "monthlyMarketStatistics": [{
        "monthStartDate": "2024-01-01", "marketDescription": "Other OTC",
        "securityTypeDescription": "All", "totalShareVolume": "1000",
        "totalDollarVolume": "500", "totalTransactionCount": "100",
        "totalIssueCount": "50", "totalIssueTradedCount": "40",
        "averageShareVolume": "10", "averageDollarVolume": "5",
        "averagePrice": "0.02",
    }],
    "monthlyTop100": [{
        "monthStartDate": "2024-01-01", "issueSymbolIdentifier": "TEST",
        "issueName": "Test Corp", "marketDescription": "Other OTC",
        "numberOfSharesTraded": "999", "dollarVolume": "111",
        "closingPrice": "0.05",
    }],
    "otcDailyList": [],
    "thresholdList": [],
    "tradingHaltsCurrent": [{
        "issueSymbolIdentifier": "HALT", "securityDescription": "Halted Inc",
        "haltActionCode": "H", "haltReasonDescription": "Regulatory",
        "haltActionTimestamp": "2024-01-15T10:00:00",
        "tradeResumptionTimestamp": "", "originatingRegulatorCode": "FINRA",
    }],
    "otcSecurityMaster": [{
        "issueSymbolIdentifier": "TEST", "securityDescription": "Test Corp",
        "issuerName": "Test Issuer", "market": "Other OTC",
        "issueType": "Common", "asOfDate": "2024-01-15",
    }],
    "MarketParticipantList": [{"MPID": "ABCD", "marketParticipantName": "Test MM"}],
}


def test_full_run_offline(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    store = _store()
    out = asyncio.run(fetch_finra_otc(store))
    assert "yearly" in out and "monthly" in out and "top100" in out
    # series landed
    pts = store.points("cycle:otc-yearly-other-otc-all-totalShareVolume")
    assert pts[date(2024, 1, 1)] == 100.0
    pts = store.points("cycle:otc-monthly-total-shares")
    assert pts[date(2024, 1, 1)] == 1000.0
    # docs landed
    doc = store.doc("otc-top100-2024-01")
    assert doc is not None and doc.payload["rows"][0]["symbol"] == "TEST"
    halts = store.doc("otc-halts-current")
    assert halts.payload["rows"][0]["symbol"] == "HALT"
    sm = store.doc("otc-secmaster")
    assert sm.payload["symbols"]["TEST"]["issuer"] == "Test Issuer"
    mp = store.doc("otc-mplist")
    assert mp.payload["rows"][0]["mpid"] == "ABCD"
    status = store.doc("finra_otc")
    assert status is not None
