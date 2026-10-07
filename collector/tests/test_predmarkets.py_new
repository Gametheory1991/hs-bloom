"""Tests for batch 12 prediction markets: Polymarket Gamma fetcher, Kalshi
Trade API v2 fetcher, and the pred_edge engine (cross-venue spreads,
staleness, mispricing scores, Brier calibration). All parsing is offline
against real-shape fixtures; jobs run against fake get_text fakes."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from collector.fetchers import kalshi, polymarket, pred_edge
from collector.fetchers.pred_edge import (
    brier,
    categorize,
    implied,
    match_pairs,
    mispricing_score,
    staleness,
)
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"


def _gamma() -> str:
    return (FIX / "predmarkets_gamma.json").read_text()


def _kalshi() -> dict:
    return json.loads((FIX / "predmarkets_kalshi.json").read_text())


# ---------- Polymarket parsing ----------

def test_parse_polymarket_sorted_by_volume24h():
    rows = polymarket.parse_markets(_gamma())
    assert [r["slug"] for r in rows] == [
        "will-indiana-enact-a-data-center-moratorium-by-december-31-2027",
        "will-there-be-no-change-in-fed-interest-rates-after-the-october-2026-meeting",
        "will-bitcoin-hit-200k-in-2026",
        "broken-market",
    ]
    fed = rows[1]
    assert fed["yes_price"] == pytest.approx(0.82)
    assert fed["no_price"] == pytest.approx(0.18)
    assert fed["best_bid"] == pytest.approx(0.81)
    assert fed["best_ask"] == pytest.approx(0.83)
    assert fed["volume24h"] == pytest.approx(48801.15)
    assert fed["liquidity"] == pytest.approx(1266034.78)
    assert fed["url"].endswith(fed["slug"])
    # double-encoded JSON strings are decoded
    assert fed["outcomes"] == ["Yes", "No"]


def test_parse_polymarket_rejects_non_list():
    with pytest.raises(ValueError):
        polymarket.parse_markets('{"markets": []}')


def test_slug_key_sanitizes():
    assert polymarket.slug_key("a b/c?d", "q") == "a-b-c-d"
    assert polymarket.slug_key(None, "Will X?") == "will-x"
    assert len(polymarket.slug_key("x" * 200, "q")) == 60


def test_poly_mid_price_prefers_book():
    rows = polymarket.parse_markets(_gamma())
    assert polymarket.mid_price(rows[1]) == pytest.approx(0.82)  # (0.81+0.83)/2
    assert polymarket.mid_price(rows[2]) == pytest.approx(0.35)  # last trade fallback
    assert polymarket.mid_price(rows[3]) is None  # no prices at all


# ---------- Kalshi parsing ----------

def test_parse_kalshi_skips_non_binary_and_sorts():
    rows = kalshi.parse_markets(_kalshi())
    tickers = [r["ticker"] for r in rows]
    assert tickers == ["FEDHIKE-26OCT-B25", "KXCPI-26OCT-T0.9"]  # scalar skipped
    fed = rows[0]
    assert fed["yes_bid"] == pytest.approx(0.80)
    assert fed["yes_ask"] == pytest.approx(0.84)
    assert fed["volume24h"] == pytest.approx(5000.0)
    assert fed["open_interest"] == pytest.approx(20000.0)
    assert fed["url"].endswith("FEDHIKE-26OCT-B25")


def test_parse_kalshi_rejects_bad_payload():
    with pytest.raises(ValueError):
        kalshi.parse_markets({"nope": []})


def test_kalshi_mid_price_prefers_book():
    rows = kalshi.parse_markets(_kalshi())
    assert kalshi.mid_price(rows[0]) == pytest.approx(0.82)  # (0.80+0.84)/2


def test_kalshi_host_fallback():
    calls = []

    async def fake_get_text(url, params=None, headers=None):
        calls.append(url)
        if "api.elections.kalshi.com" in url:
            raise RuntimeError("HTTP 403")
        return '{"markets": []}'

    import asyncio
    out = asyncio.run(kalshi._get_first(fake_get_text, "/markets?status=open"))
    assert out == {"markets": []}
    assert len(calls) == 2
    assert "external-api.kalshi.com" in calls[1]


# ---------- engine primitives ----------

def test_categorize():
    assert categorize("Will the Fed cut rates?") == "econ"
    assert categorize("Who wins the presidential election?") == "politics"
    assert categorize("Will Bitcoin hit $200k?") == "crypto"
    assert categorize("Lakers vs Celtics game") == "sports"
    assert categorize("Will Indiana enact a moratorium?") == "other"


def test_implied_prefers_mid():
    assert implied({"best_bid": 0.81, "best_ask": 0.83}) == pytest.approx(0.82)
    assert implied({"yes_bid": 0.80, "yes_ask": 0.84}) == pytest.approx(0.82)
    assert implied({"last_price": 0.5}) == pytest.approx(0.5)
    assert implied({}) is None


def test_match_pairs_fed():
    poly = polymarket.parse_markets(_gamma())
    kal = kalshi.parse_markets(_kalshi())
    pairs = match_pairs(poly, kal)
    fed = [p for p in pairs if p[0]["id"] == "fed-policy"]
    assert len(fed) == 1
    _, pr, kr = fed[0]
    assert "fed" in pr["question"].lower()
    assert kr["ticker"] == "FEDHIKE-26OCT-B25"


def test_staleness_counts_trailing_flat():
    d0 = date(2026, 10, 1)
    pts = {d0: 0.5, date(2026, 10, 2): 0.5, date(2026, 10, 3): 0.5}
    assert staleness(pts) == 2
    pts[date(2026, 10, 3)] = 0.6
    assert staleness(pts) == 0
    assert staleness({}) == 0


def test_mispricing_score_gates_illiquid():
    assert mispricing_score(0.10, 0, None, liquid=False) == 0.0
    s = mispricing_score(0.10, 0, None, liquid=True)
    assert s == pytest.approx(20.0)  # (0.10/0.20)*40
    assert mispricing_score(0.30, 6, 0.10, liquid=True) == 100.0  # capped


def test_brier():
    assert brier([(1.0, 1.0), (0.0, 0.0)]) == pytest.approx(0.0)
    assert brier([(0.5, 1.0), (0.5, 0.0)]) == pytest.approx(0.25)
    assert brier([]) is None


# ---------- fetcher jobs ----------

async def test_fetch_polymarket_job(tmp_path):
    store = Store(tmp_path / "t.db")

    async def fake_get_text(url, params=None, headers=None):
        assert "gamma-api.polymarket.com" in url
        return _gamma()

    out = await polymarket.fetch_polymarket(store, fake_get_text)
    assert out == polymarket.SOURCE
    pts = store.points("cycle:pm-will-indiana-enact-a-data-center-moratorium-by-december-31-2-yes")
    assert len(pts) == 1  # key truncated to 60 chars but still stored
    doc = store.doc("polymarket")
    assert doc is not None and len(doc.payload["markets"]) == 4
    assert doc.source == polymarket.SOURCE


async def test_fetch_kalshi_job(tmp_path):
    store = Store(tmp_path / "t.db")
    payload = json.dumps(_kalshi())

    async def fake_get_text(url, params=None, headers=None):
        return payload  # same universe for series calls is fine for the test

    out = await kalshi.fetch_kalshi(store, fake_get_text)
    assert out == kalshi.SOURCE
    pts = store.points("cycle:kal-fedhike_26oct_b25-yes")
    assert list(pts.values())[0] == pytest.approx(0.82)
    doc = store.doc("kalshi")
    assert doc is not None and len(doc.payload["markets"]) == 2


# ---------- edge engine job ----------

async def _seed_venue_docs(store):
    async def fake_get_text(url, params=None, headers=None):
        if "gamma-api.polymarket.com" in url:
            return _gamma()
        return json.dumps(_kalshi())

    await polymarket.fetch_polymarket(store, fake_get_text)
    await kalshi.fetch_kalshi(store, fake_get_text)


async def test_fetch_pred_edge_end_to_end(tmp_path):
    store = Store(tmp_path / "t.db")
    await _seed_venue_docs(store)

    async def fake_get_text(url, params=None, headers=None):
        raise AssertionError(f"unexpected HTTP in engine run: {url}")

    out = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out == pred_edge.SOURCE
    doc = store.doc("pred_edge")
    p = doc.payload
    # fed-policy pair: poly mid 0.82 vs kalshi mid 0.82 -> no spread, not surfaced
    assert p["coverage"] == {"polymarket": 4, "kalshi": 2}
    assert p["tracked_count"] == 6
    assert p["resolved_this_run"] == 0
    assert p["disclaimer"] and "not guarantees" in p["disclaimer"]
    assert p["skipped"] == []


async def test_pred_edge_flags_divergence(tmp_path):
    store = Store(tmp_path / "t.db")

    async def fake_get_text(url, params=None, headers=None):
        if "gamma-api.polymarket.com" in url:
            return _gamma()
        # kalshi fed market priced 10c cheaper -> 10c spread
        body = _kalshi()
        body["markets"][0]["yes_bid_dollars"] = "0.7000"
        body["markets"][0]["yes_ask_dollars"] = "0.7400"
        return json.dumps(body)

    await polymarket.fetch_polymarket(store, fake_get_text)
    await kalshi.fetch_kalshi(store, fake_get_text)
    out = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out == pred_edge.SOURCE
    edges = store.doc("pred_edge").payload["edges"]
    fed = [e for e in edges if e["rule"] == "fed-policy"]
    assert len(fed) == 1
    e = fed[0]
    assert e["spread"] == pytest.approx(0.10, abs=0.001)  # 0.82 - 0.72
    assert e["edge_estimate"] == pytest.approx(0.06, abs=0.001)  # minus 4c fee buffer
    assert e["tradable_estimate"] is True
    assert e["mispricing_score"] > 0
    assert "not a guarantee" in e["note"]


async def test_pred_edge_calibration_on_resolution(tmp_path):
    store = Store(tmp_path / "t.db")
    store.put_doc("pred_edge", {
        "tracked": {
            "pm:will-there-be-no-change-in-fed-interest-rates-after-the-october-2026-meeting": {
                "venue": "polymarket",
                "ref": "will-there-be-no-change-in-fed-interest-rates-after-the-october-2026-meeting",
                "label": "Will there be no change in Fed interest rates...?",
                "implied": 0.82, "category": "econ", "first_seen": "2026-10-01",
            },
        },
        "calibration": {},
    }, source="pred_edge")

    async def fake_get_text(url, params=None, headers=None):
        if "markets?slug=" in url:
            # resolved: Yes won
            return json.dumps([{"closed": True, "outcomePrices": "[\"1\", \"0\"]"}])
        raise AssertionError(f"unexpected HTTP: {url}")

    out = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out == pred_edge.SOURCE
    p = store.doc("pred_edge").payload
    assert p["resolved_this_run"] == 1
    assert p["tracked_count"] == 0  # resolved market leaves tracking
    lb = p["calibration"]
    assert len(lb) == 1
    row = lb[0]
    assert (row["venue"], row["category"], row["n"]) == ("polymarket", "econ", 1)
    assert row["brier"] == pytest.approx((0.82 - 1.0) ** 2)
    assert row["win_rate"] == 1.0
    assert "polymarket snapshot missing" in p["skipped"]  # graceful degradation


async def test_pred_edge_degrades_without_snapshots(tmp_path):
    store = Store(tmp_path / "t.db")

    async def fake_get_text(url, params=None, headers=None):
        raise AssertionError("no HTTP expected")

    out = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out == pred_edge.SOURCE
    p = store.doc("pred_edge").payload
    assert p["edges"] == [] and p["calibration"] == []
    assert len(p["skipped"]) == 2
