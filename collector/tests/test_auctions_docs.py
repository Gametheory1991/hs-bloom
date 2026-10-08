"""Tests for per-auction detail records + upcoming schedule docs."""
import json
from datetime import date, timedelta

import pytest

from collector.config import AuctionsCfg
from collector.fetchers.auctions import (
    _pull_cutoff,
    _store_auction_docs,
    fetch_auctions,
    parse_detail,
)


class FakeStore:
    def __init__(self):
        self.points = {}
        self._docs = {}

    def upsert_points(self, series_id, points):
        self.points[series_id] = list(points)

    def upsert_points_batch(self, items):
        for series_id, d, v in items:
            self.points.setdefault(series_id, []).append((d, v))

    def put_doc(self, key, payload, source):
        self._docs[key] = payload

    def doc(self, key):
        if key not in self._docs:
            return None
        payload = self._docs[key]

        class D:
            pass

        d = D()
        d.payload = payload
        d.updated_at = "2026-10-05T00:00:00Z"
        d.source = "fiscaldata.treasury.gov"
        return d


def _raw(auction_date, stype, sterm, btc, hy=None, hir=None, cusip="C1"):
    return {
        "auction_date": auction_date,
        "announcemt_date": auction_date,
        "cusip": cusip,
        "security_type": stype,
        "security_term": sterm,
        "offering_amt": "58000000000",
        "high_yield": hy,
        "high_investment_rate": hir,
        "high_discnt_rate": None,
        "bid_to_cover_ratio": btc,
        "total_accepted": "58000000000",
        "total_tendered": "140000000000",
        "indirect_bidder_accepted": "34000000000",
        "direct_bidder_accepted": "12000000000",
        "primary_dealer_accepted": "5000000000",
        "issue_date": auction_date,
        "maturity_date": None,
        "reopening": "N",
    }


def test_parse_detail_completed_note():
    d = parse_detail(_raw("2026-09-09", "Note", "10-Year", "2.53", hy="4.213"))
    assert d["completed"] is True
    assert d["bucket"] == "Note-10Y"
    assert d["high_yield"] == 4.213
    assert abs(d["indirect_pct"] - 58.6207) < 0.01
    assert abs(d["dealer_pct"] - 8.6207) < 0.01


def test_parse_detail_bill_uses_investment_rate():
    d = parse_detail(_raw("2026-09-01", "Bill", "4-Week", "2.8", hir="3.956"))
    assert d["bucket"] == "Bill-4W"
    assert d["high_yield"] == 3.956


def test_parse_detail_upcoming_not_completed():
    d = parse_detail(_raw("2099-01-05", "Note", "3-Year", "null"))
    assert d["completed"] is False
    assert d["bid_to_cover"] is None
    assert d["bucket"] == "Note-3Y"


def test_parse_detail_rejects_bad_records():
    assert parse_detail({"auction_date": "bogus", "security_type": "Note",
                         "security_term": "10-Year"}) is None
    assert parse_detail({"auction_date": "2026-09-09", "security_type": "Note",
                         "security_term": "bogus"}) is None


async def test_fetch_auctions_writes_docs_and_high_yield_series():
    today = date.today()
    past = (today - timedelta(days=5)).isoformat()
    fut = (today + timedelta(days=2)).isoformat()
    payload = {"data": [
        _raw(past, "Note", "10-Year", "2.53", hy="4.213", cusip="A1"),
        _raw(fut, "Note", "3-Year", "null", cusip="A2"),
    ]}

    async def fake_get(url, params=None, headers=None):
        if params and params.get("page[number]") != "1":
            return json.dumps({"data": []})
        return json.dumps(payload)

    store = FakeStore()
    cfg = AuctionsCfg(buckets=["Note-10Y", "Note-3Y"], lookback_days=30)
    assert await fetch_auctions(cfg, store, fake_get) == "auctions"
    res = store._docs["auction_results"]["results"]
    assert len(res) == 1 and res[0]["cusip"] == "A1"
    upc = store._docs["upcoming_auctions"]["auctions"]
    assert len(upc) == 1 and upc[0]["cusip"] == "A2"
    assert "auction:Note-10Y:high_yield" in store.points
    pts = dict(store.points["auction:Note-10Y:high_yield"])
    assert pts[date.fromisoformat(past)] == 4.213


async def test_fetch_auctions_doc_upsert_overwrites_announced():
    today = date.today()
    past = (today - timedelta(days=5)).isoformat()
    store = FakeStore()
    # seed an announced record for the same auction
    store.put_doc("auction_results", {"results": [
        {"auction_date": past, "cusip": "A1", "bucket": "Note-10Y",
         "completed": False, "bid_to_cover": None},
    ]}, source="fiscaldata.treasury.gov")

    async def fake_get(url, params=None, headers=None):
        return json.dumps({"data": [
            _raw(past, "Note", "10-Year", "2.53", hy="4.213", cusip="A1"),
        ]})

    cfg = AuctionsCfg(buckets=["Note-10Y"], lookback_days=30)
    await fetch_auctions(cfg, store, fake_get)
    res = store._docs["auction_results"]["results"]
    assert len(res) == 1  # upserted, not duplicated
    assert res[0]["completed"] is True and res[0]["bid_to_cover"] == 2.53


# --- history policy: no prune, no cap, incremental-from-earliest ---------

def _seed_results(store, dates_cusips):
    store.put_doc("auction_results", {"results": [
        {"auction_date": d, "cusip": c, "bucket": "Note-10Y",
         "completed": True, "bid_to_cover": 2.5}
        for d, c in dates_cusips
    ]}, source="fiscaldata.treasury.gov")


def test_pull_cutoff_no_data_returns_none():
    assert _pull_cutoff(FakeStore(), 540) is None


def test_pull_cutoff_history_complete_returns_recent_window():
    store = FakeStore()
    store.put_doc("auction_results",
                  {"results": [], "history_complete": True,
                   "history_version": 2},
                  source="fiscaldata.treasury.gov")
    expected = (date.today() - timedelta(days=540)).isoformat()
    assert _pull_cutoff(store, 540) == expected


def test_pull_cutoff_v1_complete_triggers_full_repull():
    # History completed under the old `completed` definition (bid_to_cover
    # required) skipped pre-2000 auctions: must re-pull everything once.
    store = FakeStore()
    store.put_doc("auction_results",
                  {"results": [], "history_complete": True},
                  source="fiscaldata.treasury.gov")
    assert _pull_cutoff(store, 540) is None


def test_parse_detail_pre2000_completed_via_yield():
    # Treasury didn't publish bid_to_cover before ~2000; a record with a
    # high yield but no bid-to-cover is still a completed auction.
    det = parse_detail({
        "auction_date": "1995-06-15",
        "security_type": "Note", "security_term": "10-Year",
        "total_accepted": "12000", "high_yield": "6.540",
        "bid_to_cover_ratio": None,
    })
    assert det is not None
    assert det["completed"] is True
    assert det["high_yield"] == 6.54
    assert det["bid_to_cover"] is None


def test_parse_detail_announced_not_completed():
    # No yield and no bid-to-cover: results haven't posted.
    det = parse_detail({
        "auction_date": "2026-10-20",
        "security_type": "Note", "security_term": "10-Year",
        "total_accepted": None, "high_yield": None,
        "bid_to_cover_ratio": None,
    })
    assert det is not None
    assert det["completed"] is False


def test_store_auction_docs_sets_history_version():
    store = FakeStore()
    _store_auction_docs(store, [], [], history_complete=True)
    assert store._docs["auction_results"]["history_version"] == 2


def test_pull_cutoff_shallow_history_returns_none_for_deep_backfill():
    # Stored history shallower than one revision window: no deep pull has
    # completed yet, so the next run must pull everything.
    store = FakeStore()
    recent = (date.today() - timedelta(days=10)).isoformat()
    _seed_results(store, [(recent, "A1")])
    assert _pull_cutoff(store, 540) is None


def test_pull_cutoff_partial_deep_history_extends_backward():
    store = FakeStore()
    old = (date.today() - timedelta(days=900)).isoformat()
    _seed_results(store, [(old, "A1")])
    expected = (date.fromisoformat(old) - timedelta(days=540)).isoformat()
    assert _pull_cutoff(store, 540) == expected


def test_store_auction_docs_keeps_full_history_no_prune():
    # Records far older than the old 180-day prune window must survive.
    store = FakeStore()
    ancient = (date.today() - timedelta(days=400)).isoformat()
    _seed_results(store, [(ancient, "OLD1")])
    _store_auction_docs(store, [], [], history_complete=False)
    res = store._docs["auction_results"]["results"]
    assert any(r["cusip"] == "OLD1" for r in res)


def test_store_auction_docs_no_record_cap():
    # The old 1000-record cap is gone: everything is kept.
    store = FakeStore()
    base = date.today() - timedelta(days=1100)
    recs = [((base + timedelta(days=i)).isoformat(), f"C{i}")
            for i in range(1200)]
    _seed_results(store, recs)
    _store_auction_docs(store, [], [], history_complete=False)
    assert len(store._docs["auction_results"]["results"]) == 1200


async def test_fetch_auctions_full_pull_sets_history_complete():
    # Empty store -> no date filter -> clean full pull marks history done.
    seen_params = []

    async def fake_get(url, params=None, headers=None):
        seen_params.append(params or {})
        if (params or {}).get("page[number]") not in (None, "1"):
            return json.dumps({"data": []})
        past = (date.today() - timedelta(days=5)).isoformat()
        return json.dumps({"data": [_raw(past, "Note", "10-Year", "2.53",
                                         hy="4.213", cusip="A1")]})

    store = FakeStore()
    cfg = AuctionsCfg(buckets=["Note-10Y"], lookback_days=30)
    assert await fetch_auctions(cfg, store, fake_get) == "auctions"
    assert all("filter" not in p for p in seen_params)
    assert store._docs["auction_results"]["history_complete"] is True


async def test_fetch_auctions_interrupted_pull_leaves_marker_unset():
    # A pull that errors before paging to completion must not claim history
    # is complete; the idempotent upsert lets the next run resume.
    async def fake_get(url, params=None, headers=None):
        if (params or {}).get("page[number]") == "2":
            raise RuntimeError("boom")
        past = (date.today() - timedelta(days=5)).isoformat()
        return json.dumps({"data": [
            _raw(past, "Note", "10-Year", "2.53", hy="4.213", cusip=f"A{i}")
            for i in range(100)]})

    store = FakeStore()
    cfg = AuctionsCfg(buckets=["Note-10Y"], lookback_days=30)
    with pytest.raises(RuntimeError, match="boom"):
        await fetch_auctions(cfg, store, fake_get)
    # partial data kept for resume, but history not marked complete
    assert len(store._docs["auction_results"]["results"]) == 100
    assert store._docs["auction_results"].get("history_complete") is not True
