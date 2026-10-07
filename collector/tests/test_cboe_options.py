"""CBOE options fetcher tests: OCC parsing, aggregation math, fallbacks."""
import json

import pytest

from collector.fetchers.cboe_options import (
    SYMBOLS,
    aggregate_chain,
    aggregate_yahoo,
    fetch_cboe_options,
    fetch_symbol,
    parse_occ,
)
from collector.store import Store


def _cbo_contract(option, oi=1000, vol=600, gamma=0.01, iv=0.2, bid=1.0, ask=1.1):
    return {"option": option, "open_interest": oi, "volume": vol,
            "gamma": gamma, "iv": iv, "bid": bid, "ask": ask,
            "delta": 0.5, "last_trade_price": 1.05}


CBOE_CHAIN = json.dumps({
    "timestamp": 1720000000,
    "data": {
        "symbol": "SPY", "current_price": 780.0,
        "options": [
            _cbo_contract("SPY261006C00780000", oi=5000, vol=1200, gamma=0.02),
            _cbo_contract("SPY261006P00780000", oi=4000, vol=900, gamma=0.02),
            _cbo_contract("SPY261006C00790000", oi=2000, vol=300, gamma=0.015),
            _cbo_contract("SPY261006P00770000", oi=3000, vol=200, gamma=0.015),
            _cbo_contract("SPY261009C00800000", oi=1000, vol=100, gamma=0.01),
        ],
    },
})

YAHOO_OPTS = json.dumps({
    "optionChain": {"result": [{
        "expirationDates": [1791000000],
        "options": [{
            "calls": [{"contractSymbol": "SPY261006C00780000", "strike": 780,
                       "volume": 1200, "openInterest": 5000,
                       "impliedVolatility": 0.18, "bid": 1.0, "ask": 1.1}],
            "puts": [{"contractSymbol": "SPY261006P00780000", "strike": 780,
                      "volume": 900, "openInterest": 4000,
                      "impliedVolatility": 0.22, "bid": 1.2, "ask": 1.3}],
        }],
    }]},
})


def test_parse_occ():
    assert parse_occ("SPY261006C00780000") == ("SPY", "2026-10-06", "C", 780.0)
    assert parse_occ("QQQ261120P00550000") == ("QQQ", "2026-11-20", "P", 550.0)
    assert parse_occ("AAPL260918C00250000") == ("AAPL", "2026-09-18", "C", 250.0)
    assert parse_occ("garbage") is None
    assert parse_occ("") is None


def test_aggregate_chain_math():
    agg = aggregate_chain(json.loads(CBOE_CHAIN)["data"]["options"], 780.0, "2026-10-06")
    assert agg is not None
    # GEX: calls +5000*0.02+2000*0.015+1000*0.01=140; puts -(4000*0.02+3000*0.015)=-125
    # net units 15 * 100 * 780^2 / 1e9
    expect = (5000 * 0.02 + 2000 * 0.015 + 1000 * 0.01
              - 4000 * 0.02 - 3000 * 0.015) * 100 * 780.0**2 / 1e9
    assert abs(agg["gex"] - round(expect, 2)) < 0.01
    assert agg["pc_oi"] == round(7000 / 8000, 3)
    assert agg["pc_vol"] == round(1100 / 1600, 3)
    # ATM IV at 780 strike: (0.20 + 0.20) / 2 -> 20.0%
    assert agg["atm_iv"] == 20.0
    assert agg["near_expiry"] == "2026-10-06"
    # unusual: vol>=500, oi>0 -> the two 780 contracts (vol/oi 0.24, 0.225)
    assert len(agg["unusual"]) == 2
    assert agg["unusual"][0]["vol_oi"] == round(1200 / 5000, 1)
    # strike chart covers nearest expiry only (800 strike is 10-09)
    assert {r["strike"] for r in agg["strike_chart"]} == {770.0, 780.0, 790.0}


def test_aggregate_chain_empty():
    assert aggregate_chain([], 780.0, "2026-10-06") is None


def test_aggregate_yahoo_fallback():
    r0 = json.loads(YAHOO_OPTS)["optionChain"]["result"][0]
    agg = aggregate_yahoo(r0["options"], r0["expirationDates"])
    assert agg is not None
    assert agg["gex"] is None  # no greeks on Yahoo
    assert agg["pc_oi"] == round(4000 / 5000, 3)
    assert agg["pc_vol"] == round(900 / 1200, 3)
    assert len(agg["unusual"]) == 2


async def fake_get_text(url, **kw):
    if "cboe.com" in url:
        if "QQQ" in url:
            raise RuntimeError("cboe down for QQQ")
        return CBOE_CHAIN
    if "yahoo" in url:
        return YAHOO_OPTS
    raise AssertionError(f"unexpected url {url}")


@pytest.mark.asyncio
async def test_fetch_symbol_cboe(tmp_path):
    stats, source = await fetch_symbol("SPY", fake_get_text, "2026-10-06")
    assert source == "cboe"
    assert stats["gex"] is not None
    assert stats["spot"] == 780.0


@pytest.mark.asyncio
async def test_fetch_symbol_yahoo_fallback(tmp_path):
    stats, source = await fetch_symbol("QQQ", fake_get_text, "2026-10-06")
    assert source == "yahoo"
    assert stats["gex"] is None
    assert stats["pc_oi"] == round(4000 / 5000, 3)


@pytest.mark.asyncio
async def test_fetch_cboe_options_stores_series(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    res = await fetch_cboe_options(store, fake_get_text)
    assert f"{len(SYMBOLS)}/{len(SYMBOLS)}" in res  # 13 cboe + 1 yahoo
    doc = store.doc("options")
    assert doc is not None
    assert set(doc.payload["symbols"]) == set(SYMBOLS)
    assert doc.payload["symbols"]["SPY"]["source"] == "cboe"
    assert doc.payload["symbols"]["QQQ"]["source"] == "yahoo"
    # daily aggregate series landed
    assert store.points("cycle:opt-SPY-gex")
    assert store.points("cycle:opt-SPY-pc-oi")
    assert store.points("cycle:opt-SPY-pc-vol")
    assert store.points("cycle:opt-SPY-atm-iv")
    assert store.points("cycle:opt-SPY-maxpain")
    # Yahoo fallback has no greeks -> no gex/atm-iv/maxpain points
    assert not store.points("cycle:opt-QQQ-gex")
    assert store.points("cycle:opt-QQQ-pc-oi")
