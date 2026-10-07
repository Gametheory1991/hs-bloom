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


# ---------- longshot buckets (batch 12+ volume/longshot upgrade) ----------

def test_longshot_bucket_boundaries():
    from collector.fetchers.pred_edge import longshot_bucket
    assert longshot_bucket(0.005) == "le2"
    assert longshot_bucket(0.02) == "le2"
    assert longshot_bucket(0.021) == "b2_10"
    assert longshot_bucket(0.10) == "b2_10"
    assert longshot_bucket(0.11) == "b10_30"
    assert longshot_bucket(0.30) == "b10_30"
    assert longshot_bucket(0.31) == "b30_70"
    assert longshot_bucket(0.70) == "b30_70"
    assert longshot_bucket(0.71) == "gt70"
    assert longshot_bucket(None) is None
    assert longshot_bucket(1.5) is None
    assert longshot_bucket(-0.1) is None


def test_candle_day_usd_converts_contracts():
    from datetime import datetime, timezone
    from collector.fetchers.kalshi import _candle_day_usd
    day, usd, mid, contracts = _candle_day_usd({
        "end_period_ts": 1791324000,
        "volume_fp": "100.00",
        "yes_bid": {"close_dollars": "0.0400"},
        "yes_ask": {"close_dollars": "0.0600"},
    })
    assert day == datetime.fromtimestamp(1791324000, tz=timezone.utc).date()
    assert mid == pytest.approx(0.05)
    assert usd == pytest.approx(5.0)  # 100 contracts x $0.05
    assert contracts == pytest.approx(100.0)
    # no price -> no dollar estimate, no crash
    _, usd2, mid2, ct2 = _candle_day_usd({"end_period_ts": 1791324000, "volume_fp": "10"})
    assert usd2 == 0.0 and mid2 is None


async def test_kalshi_backfill_writes_volume_history(tmp_path):
    store = Store(tmp_path / "t.db")
    candles = {"markets": [{
        "market_ticker": "FEDHIKE-26OCT-B25",
        "candlesticks": [
            {"end_period_ts": 1759276800, "volume_fp": "100.00",
             "yes_bid": {"close_dollars": "0.8000"},
             "yes_ask": {"close_dollars": "0.8400"}},
            {"end_period_ts": 1759363200, "volume_fp": "50.00",
             "yes_bid": {"close_dollars": "0.7800"},
             "yes_ask": {"close_dollars": "0.8200"}},
        ],
    }]}

    async def fake_get_text(url, params=None, headers=None):
        return json.dumps(candles)

    rows = kalshi.parse_markets(_kalshi())
    out = await kalshi.backfill_kalshi_volume(store, fake_get_text, rows)
    assert "1 tickers" in out["status"]
    pts = store.points("cycle:kal-fedhike_26oct_b25-vol")
    assert len(pts) == 2
    assert list(pts.values())[0] == pytest.approx(82.0)  # 100 x 0.82 mid
    px = store.points("cycle:kal-fedhike_26oct_b25-yes")
    assert len(px) == 2
    assert list(px.values())[0] == pytest.approx(0.82)
    # venue total history rebuilt from backfilled tickers (excludes today)
    tot = store.points("cycle:predvol-kalshi")
    assert len(tot) == 2
    # resume-safe: second run the same day is a no-op
    out2 = await kalshi.backfill_kalshi_volume(store, fake_get_text, rows)
    assert out2["status"] == "already ran today"
    doc = store.doc("kalshi_vol_backfill")
    assert doc.payload["done"] == {"FEDHIKE-26OCT-B25": "fedhike_26oct_b25"}


async def test_polymarket_price_backfill(tmp_path):
    store = Store(tmp_path / "t.db")
    hist = {"history": [
        {"t": 1759276800, "p": 0.5},
        {"t": 1759363200, "p": 0.6},
        {"t": 1759363200, "p": "bad"},   # skipped
        {"t": 1759363200, "p": 1.5},     # out of range, skipped
    ]}

    async def fake_get_text(url, params=None, headers=None):
        assert "clob.polymarket.com" in url
        return json.dumps(hist)

    rows = polymarket.parse_markets(_gamma())
    assert rows[0]["clob_yes_token"]  # fixture carries token ids
    out = await polymarket.backfill_polymarket_prices(store, fake_get_text, rows)
    assert "markets" in out["status"]
    pts = store.points(f"cycle:pm-{rows[0]['key']}-yes")
    assert len(pts) == 2
    assert sorted(pts.values()) == pytest.approx([0.5, 0.6])
    doc = store.doc("poly_px_backfill")
    assert doc.payload["asof"] == date.today().isoformat()
    out2 = await polymarket.backfill_polymarket_prices(store, fake_get_text, rows)
    assert out2["status"] == "already ran today"


async def test_fetch_jobs_store_volume_series(tmp_path):
    store = Store(tmp_path / "t.db")

    async def fake_get_text(url, params=None, headers=None):
        if "gamma-api.polymarket.com" in url:
            return _gamma()
        if "clob.polymarket.com" in url:
            return json.dumps({"history": []})
        return json.dumps(_kalshi())

    await polymarket.fetch_polymarket(store, fake_get_text)
    vpts = store.points("cycle:predvol-polymarket")
    assert len(vpts) == 1 and list(vpts.values())[0] > 0
    mpts = store.points(
        "cycle:pm-will-indiana-enact-a-data-center-moratorium-by-december-31-2-vol")
    assert len(mpts) == 1
    await kalshi.fetch_kalshi(store, fake_get_text)
    kpts = store.points("cycle:predvol-kalshi")
    assert len(kpts) == 1 and list(kpts.values())[0] > 0
    # kalshi vol stored as est. dollars: 5000 contracts x 0.82 mid
    k1 = store.points("cycle:kal-fedhike_26oct_b25-vol")
    assert list(k1.values())[0] == pytest.approx(5000 * 0.82)


async def test_pred_edge_longshot_pnl_on_resolution(tmp_path):
    store = Store(tmp_path / "t.db")
    store.put_doc("pred_edge", {
        "tracked": {
            "pm:longshot-yes": {
                "venue": "polymarket", "ref": "longshot-yes",
                "label": "Longshot test", "implied": 0.02,
                "category": "other", "first_seen": "2026-10-01",
            },
            "kal:longshot-yes": {
                "venue": "kalshi", "ref": "KXTEST",
                "label": "Kalshi longshot", "implied": 0.05,
                "category": "other", "first_seen": "2026-10-01",
            },
        },
        "calibration": [],
        "calibration_raw": {},
    }, source="x")

    async def fake_get_text(url, params=None, headers=None):
        if "markets?slug=" in url:
            return json.dumps([{"closed": True, "outcomePrices": "[\"0\", \"1\"]"}])  # No won
        if "/markets/KXTEST" in url:
            return json.dumps({"market": {"status": "settled", "result": "yes"}})
        raise AssertionError(f"unexpected HTTP: {url}")

    out = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out == pred_edge.SOURCE
    p = store.doc("pred_edge").payload
    assert p["resolved_this_run"] == 2
    pnl = {(r["venue"], r["bucket"]): r for r in p["longshot_pnl"]}
    # poly: $1 Yes at 2c buys 50 contracts, No won -> -$1.00, -100%
    assert pnl[("polymarket", "le2")]["n"] == 1
    assert pnl[("polymarket", "le2")]["pnl"] == pytest.approx(-1.0)
    assert pnl[("polymarket", "le2")]["return"] == pytest.approx(-1.0)
    # kalshi: $1 Yes at 5c buys 20 contracts, Yes won -> +$19.00, +1900%
    assert pnl[("kalshi", "b2_10")]["n"] == 1
    assert pnl[("kalshi", "b2_10")]["pnl"] == pytest.approx(19.0)
    assert pnl[("kalshi", "b2_10")]["return"] == pytest.approx(19.0)
    # cumulative-return series written per bucket
    s1 = store.points("cycle:predpnl-polymarket-le2")
    assert list(s1.values())[0] == pytest.approx(-1.0)
    # volume-share series written even with no live snapshots (0 markets)
    vs = store.points("cycle:predlong-polymarket-le2")
    assert list(vs.values())[0] == 0.0
    assert p["longshot_pnl_raw"]["polymarket"]["le2"]["n"] == 1


async def test_pred_edge_second_run_no_crash(tmp_path):
    # Regression: pre-fix docs stored calibration as a leaderboard LIST;
    # the next run crashed on list.setdefault / list.items(), so the engine
    # never accumulated past run one.
    store = Store(tmp_path / "t.db")
    store.put_doc("pred_edge", {
        "tracked": {},
        "calibration": [{"venue": "polymarket", "category": "econ", "n": 1,
                         "brier": 0.03, "win_rate": 1.0, "avg_implied": 0.82}],
    }, source="x")

    async def fake_get_text(url, params=None, headers=None):
        raise AssertionError("no HTTP expected")

    out = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out == pred_edge.SOURCE
    p = store.doc("pred_edge").payload
    assert p["calibration"] == []  # raw sums unrecoverable -> one-time reset
    assert p["calibration_raw"] == {}
    # and a third run stays healthy
    out2 = await pred_edge.fetch_pred_edge(store, fake_get_text)
    assert out2 == pred_edge.SOURCE
