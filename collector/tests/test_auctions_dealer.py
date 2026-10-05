"""Tests for the auction surveillance and primary-dealer fetchers."""
from datetime import date
from pathlib import Path

from collector.config import AuctionsCfg, DealerSeriesCfg
from collector.fetchers.auctions import (
    fetch_auctions,
    fetch_page,
    normalize_bucket,
    parse_auctions,
)
from collector.fetchers.dealer import fetch_dealer, fetch_series, parse_timeseries

FIX = Path(__file__).parent / "fixtures"


class FakeStore:
    def __init__(self):
        self.points = {}
        self._docs = {}

    def upsert_points(self, series_id, points):
        self.points[series_id] = list(points)

    def put_doc(self, key, payload, source):
        self._docs[key] = payload

    def doc(self, key):
        return None


def test_normalize_bucket_reopenings_land_in_benchmark_bucket():
    assert normalize_bucket("Note", "10-Year") == "Note-10Y"
    assert normalize_bucket("Bill", "4-Week") == "Bill-4W"
    assert normalize_bucket("Bill", "13-Week") == "Bill-13W"
    assert normalize_bucket("Bond", "30-Year") == "Bond-30Y"
    # reopenings snap to the benchmark tenor
    assert normalize_bucket("Bond", "29-Year 11-Month") == "Bond-30Y"
    assert normalize_bucket("Bond", "19-Year 10-Month") == "Bond-20Y"
    assert normalize_bucket("Note", "9-Year 10-Month") == "Note-10Y"
    assert normalize_bucket("Note", "1-Year 11-Month") == "Note-2Y"
    assert normalize_bucket("Note", "bogus") is None
    assert normalize_bucket("", "10-Year") is None


def test_parse_auctions_metrics_and_future_skip():
    recs = parse_auctions((FIX / "auctions_sample.json").read_text())
    assert len(recs) == 3  # future (null) auction skipped
    by_bucket = {r["bucket"]: r for r in recs}
    note10 = by_bucket["Note-10Y"]
    assert note10["date"] == date(2026, 8, 12)
    assert note10["metrics"]["bid_to_cover"] == 2.53
    assert abs(note10["metrics"]["indirect_pct"] - 60.98) < 0.01
    assert abs(note10["metrics"]["dealer_pct"] - 6.84) < 0.01
    assert abs(note10["metrics"]["direct_pct"] - 11.66) < 0.01
    assert by_bucket["Bond-30Y"]["metrics"]["bid_to_cover"] == 2.61  # reopening bucketed
    assert by_bucket["Bill-4W"]["metrics"]["bid_to_cover"] == 2.8
    assert all(v is not None for r in recs for v in r["metrics"].values())


async def test_fetch_page_sends_filter_and_paging_params():
    seen = {}

    async def fake_get(url, params=None, headers=None):
        seen["url"] = url
        seen["params"] = params
        return (FIX / "auctions_sample.json").read_text()

    recs = await fetch_page(2, "2025-08-29", fake_get)
    assert seen["url"].endswith("auctions_query")
    assert seen["params"]["filter"] == "record_date:gte:2025-08-29"
    assert seen["params"]["page[number]"] == "2"
    assert seen["params"]["page[size]"] == "100"
    assert len(recs) == 3


async def test_fetch_auctions_stores_per_bucket_metrics():
    async def fake_get(url, params=None, headers=None):
        if params and params.get("page[number]") != "1":
            return '{"data":[]}'
        return (FIX / "auctions_sample.json").read_text()

    store = FakeStore()
    cfg = AuctionsCfg(buckets=["Note-10Y", "Bond-30Y"], lookback_days=400)
    assert await fetch_auctions(cfg, store, fake_get) == "auctions"
    # Bill-4W not in cfg.buckets -> ignored
    assert "auction:Bill-4W:bid_to_cover" not in store.points
    btc = store.points["auction:Note-10Y:bid_to_cover"]
    assert btc == [(date(2026, 8, 12), 2.53)]
    ind = store.points["auction:Bond-30Y:indirect_pct"]
    assert len(ind) == 1 and abs(ind[0][1] - 79.33) < 0.01
    assert "auction:Note-10Y:offering" in store.points


def test_dealer_parse_timeseries_skips_bad_row():
    pts = parse_timeseries((FIX / "dealer_timeseries.json").read_text())
    assert pts == [
        (date(2026, 9, 9), 460449.0),
        (date(2026, 9, 16), 454557.0),
        (date(2026, 9, 23), 470836.0),
    ]


async def test_dealer_fetch_series_hits_pd_endpoint():
    seen = {}

    async def fake_get(url, params=None, headers=None):
        seen["url"] = url
        return (FIX / "dealer_timeseries.json").read_text()

    pts = await fetch_series("PDPOSGST-TOT", fake_get)
    assert seen["url"] == "https://markets.newyorkfed.org/api/pd/get/PDPOSGST-TOT.json"
    assert len(pts) == 3


async def test_fetch_dealer_per_series_isolation():
    async def fake_get(url, params=None, headers=None):
        if "BADKEY" in url:
            raise RuntimeError("HTTP 400")
        return (FIX / "dealer_timeseries.json").read_text()

    store = FakeStore()
    series = [
        DealerSeriesCfg(id="ust-net", name="n", keyid="PDPOSGST-TOT", unit="$m"),
        DealerSeriesCfg(id="broken", name="n", keyid="BADKEY", unit="$m"),
    ]
    try:
        await fetch_dealer(series, store, fake_get)
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "broken" in str(exc)
    assert raised
    assert store.points["dealer:ust-net"][0] == (date(2026, 9, 9), 460449.0)
