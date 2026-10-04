"""Tests for the FINRA bond breadth + sentiment fetcher (finra_breadth).

The source is FINRA's public dynamic-reporting API (no auth, no fixtures
on disk): parsing tests use small inline samples copied from real API
responses captured 2026-10-04; job tests run against a fake session
(no network)."""
from __future__ import annotations

import asyncio
from datetime import date

from collector.fetchers import finra_breadth
from collector.fetchers.finra_breadth import (
    breadth_series_ids,
    fetch_finra_breadth,
    parse_breadth,
    parse_sentiment,
    sentiment_series_ids,
)

# Inline samples: real MarketActivityAggregates rows, 2026-09-28.
BREADTH_ROWS = [
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 20338.41,
     "dataTypeDescription": "Dollar Volume", "bondType": "CORP_144A",
     "fieldC": 11729.1, "fieldB": 1557.16, "fieldD": 7052.14},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 17,
     "dataTypeDescription": "52 Week High", "bondType": "CORP_144A",
     "fieldC": 1, "fieldB": 3, "fieldD": 13},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 35,
     "dataTypeDescription": "Unchanged", "bondType": "CORP_144A",
     "fieldC": 20, "fieldB": 0, "fieldD": 15},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 2966,
     "dataTypeDescription": "Declines", "bondType": "CORP_144A",
     "fieldC": 1571, "fieldB": 81, "fieldD": 1314},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 217,
     "dataTypeDescription": "Advances", "bondType": "CORP_144A",
     "fieldC": 123, "fieldB": 18, "fieldD": 76},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 3344,
     "dataTypeDescription": "Total Issues Traded", "bondType": "CORP_144A",
     "fieldC": 1753, "fieldB": 102, "fieldD": 1489},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 5586.95,
     "dataTypeDescription": "Dollar Volume", "bondType": "AGENCY",
     "fieldC": 824.44, "fieldB": 1011.7, "fieldD": 842.48},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 400,
     "dataTypeDescription": "Advances", "bondType": "AGENCY",
     "fieldC": 90, "fieldB": 120, "fieldD": 80},
    {"originalTradeReportedDate": "2026-09-28", "fieldA": 150,
     "dataTypeDescription": "Declines", "bondType": "AGENCY",
     "fieldC": 40, "fieldB": 50, "fieldD": 30},
]

# Inline samples: real MarketSentimentAggregates rows, 2026-09-28.
SENTIMENT_ROWS = [
    {"originalTradeReportedDate": "2026-09-28",
     "totalTradedSecuritiesCount": 102, "fieldTypeCode": "All Securities",
     "totalTradedVolume": 1557.16, "totalTransactionsCount": 463,
     "bondType": "Corp_144a", "issueTypeCode": "Convertible Bonds"},
    {"originalTradeReportedDate": "2026-09-28",
     "totalTradedSecuritiesCount": 92,
     "fieldTypeCode": "Dealer Buy from Customer",
     "totalTradedVolume": 757.87, "totalTransactionsCount": 233,
     "bondType": "Corp_144a", "issueTypeCode": "Convertible Bonds"},
    {"originalTradeReportedDate": "2026-09-28",
     "totalTradedSecuritiesCount": 72,
     "fieldTypeCode": "Dealer Sell to Customer",
     "totalTradedVolume": 743.53, "totalTransactionsCount": 214,
     "bondType": "Corp_144a", "issueTypeCode": "Convertible Bonds"},
    {"originalTradeReportedDate": "2026-09-28",
     "totalTradedSecuritiesCount": 10, "fieldTypeCode": "Inter-Dealer",
     "totalTradedVolume": 36.43, "totalTransactionsCount": 10,
     "bondType": "Corp_144a", "issueTypeCode": "Convertible Bonds"},
    {"originalTradeReportedDate": "2026-09-28",
     "totalTradedSecuritiesCount": 5000,
     "fieldTypeCode": "Dealer Buy from Customer",
     "totalTradedVolume": 20000.0, "totalTransactionsCount": 9000,
     "bondType": "Corp", "issueTypeCode": "Investment Grade"},
    {"originalTradeReportedDate": "2026-09-28",
     "totalTradedSecuritiesCount": 5200,
     "fieldTypeCode": "Dealer Sell to Customer",
     "totalTradedVolume": 21000.0, "totalTransactionsCount": 9500,
     "bondType": "Corp", "issueTypeCode": "Investment Grade"},
]


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


class FakeSession:
    """Stands in for DynarepSession: records query windows, serves rows."""

    instances: list["FakeSession"] = []

    def __init__(self, breadth_rows=None, sentiment_rows=None, fail=False):
        self.breadth_rows = breadth_rows if breadth_rows is not None else []
        self.sentiment_rows = sentiment_rows if sentiment_rows is not None else []
        self.fail = fail
        self.calls: list[tuple[str, date, date]] = []
        FakeSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def query(self, dataset, fields, start, end):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((dataset, start, end))
        if "Activity" in dataset:
            return self.breadth_rows
        return self.sentiment_rows


# ---------- parsing ----------

def test_parse_breadth_pivots_and_derives_spread():
    out = parse_breadth(BREADTH_ROWS)
    d = date(2026, 9, 28)
    # 144a convertibles: fieldB
    assert out["finra-breadth-144a-conv-dvol"][d] == 1557.16
    assert out["finra-breadth-144a-all-adv"][d] == 217
    assert out["finra-breadth-144a-all-dec"][d] == 2966
    assert "finra-breadth-144a-hy-lo52" not in out  # no lo52 row in sample
    # derived advance-decline spread: 217 - 2966
    assert out["finra-breadth-144a-all-adspread"][d] == 217 - 2966
    # agency sectors map to Fannie/FHLB/Freddie, not conv/hy/ig
    assert out["finra-breadth-agency-fannie-dvol"][d] == 1011.7
    assert out["finra-breadth-agency-fhlb-dvol"][d] == 824.44
    assert out["finra-breadth-agency-freddie-dvol"][d] == 842.48
    assert out["finra-breadth-agency-all-adspread"][d] == 400 - 150
    assert "finra-breadth-agency-conv-dvol" not in out


def test_parse_breadth_ignores_unknown_rows():
    rows = BREADTH_ROWS + [
        {"originalTradeReportedDate": "2026-09-28", "fieldA": 1,
         "dataTypeDescription": "Advances", "bondType": "NOPE",
         "fieldB": 1, "fieldC": 1, "fieldD": 1},
        {"originalTradeReportedDate": "2026-09-28", "fieldA": 1,
         "dataTypeDescription": "Mystery Metric", "bondType": "CORP",
         "fieldB": 1, "fieldC": 1, "fieldD": 1},
        {"originalTradeReportedDate": "not-a-date", "fieldA": 1,
         "dataTypeDescription": "Advances", "bondType": "CORP",
         "fieldB": 1, "fieldC": 1, "fieldD": 1},
    ]
    out = parse_breadth(rows)
    assert not any("NOPE" in k for k in out)
    assert not any("mystery" in k.lower() for k in out)


def test_parse_sentiment_pivots_and_derives_netflow():
    out = parse_sentiment(SENTIMENT_ROWS)
    d = date(2026, 9, 28)
    assert out["finra-sent-144a-conv-dbuy-vol"][d] == 757.87
    assert out["finra-sent-144a-conv-dbuy-trades"][d] == 233
    assert out["finra-sent-144a-conv-dsell-vol"][d] == 743.53
    assert out["finra-sent-144a-conv-inter-vol"][d] == 36.43
    assert out["finra-sent-144a-conv-total-vol"][d] == 1557.16
    # net customer flow = dealer sells - dealer buys (customers net selling)
    assert out["finra-sent-144a-conv-netflow"][d] == 743.53 - 757.87
    assert out["finra-sent-corp-ig-netflow"][d] == 21000.0 - 20000.0


def test_series_inventories_are_unique_and_complete():
    b = breadth_series_ids()
    s = sentiment_series_ids()
    assert len(b) == len(set(b)) == 96  # 12 bond-sector x (7 + adspread)
    assert len(s) == len(set(s)) == 144  # 16 bond-issue x (8 + netflow)
    assert not (set(b) & set(s))
    assert "finra-breadth-corp-all-adspread" in b
    assert "finra-sent-corp-ig-netflow" in s
    assert "finra-sent-agency-fannie-dbuy-vol" in s


# ---------- job ----------

def _run(store, **kw):
    return asyncio.run(fetch_finra_breadth(store, **kw))


def test_job_backfills_from_2018_when_store_empty():
    FakeSession.instances.clear()
    store = FakeStore()
    out = _run(store, today=date(2026, 10, 4),
               session_factory=lambda: FakeSession(BREADTH_ROWS,
                                                   SENTIMENT_ROWS))
    assert out == "finra-bond-breadth"
    sess = FakeSession.instances[-1]
    assert len(sess.calls) == 2
    for _ds, start, end in sess.calls:
        assert start == date(2018, 1, 22)
        assert end == date(2026, 10, 4)
    d = date(2026, 9, 28)
    assert store.series["cycle:finra-breadth-144a-conv-dvol"][d] == 1557.16
    assert (store.series["cycle:finra-sent-corp-ig-netflow"][d]
            == 21000.0 - 20000.0)
    doc = store.docs["finra_breadth"]["payload"]
    assert doc["status"] == "ok"
    assert doc["as_of"] == "2026-09-28"
    assert len(doc["series"]) == len(store.series)


def test_job_refreshes_trailing_window_when_populated():
    FakeSession.instances.clear()
    store = FakeStore()
    store.upsert_points("cycle:finra-breadth-corp-all-adv",
                        [(date(2026, 10, 1), 100.0)])
    _run(store, today=date(2026, 10, 4),
         session_factory=lambda: FakeSession(BREADTH_ROWS, SENTIMENT_ROWS))
    sess = FakeSession.instances[-1]
    for _ds, start, end in sess.calls:
        assert start == date(2026, 9, 24)  # trailing 10 days
        assert end == date(2026, 10, 4)


def test_job_records_error_and_raises():
    store = FakeStore()
    try:
        _run(store, today=date(2026, 10, 4),
             session_factory=lambda: FakeSession(fail=True))
    except RuntimeError as exc:
        assert "finra_breadth fetch failed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
    doc = store.docs["finra_breadth"]["payload"]
    assert doc["status"] == "error"
    assert "boom" in doc["error"]


def test_job_rejects_empty_responses():
    store = FakeStore()
    try:
        _run(store, today=date(2026, 10, 4),
             session_factory=lambda: FakeSession([], []))
    except RuntimeError as exc:
        assert "empty response" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
