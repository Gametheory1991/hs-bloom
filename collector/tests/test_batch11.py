"""Batch 11: World Bank, USAspending, CoinGecko, OpenFIGI, Finnhub fetchers."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from collector.fetchers.worldbank import fetch_worldbank, parse_indicator
from collector.fetchers.usaspending import (
    fetch_usaspending, parse_category, parse_spending_over_time,
)
from collector.fetchers.coingecko import fetch_coingecko, parse_markets
from collector.fetchers.openfigi import fetch_openfigi, parse_mappings
from collector.fetchers.finnhub import (
    fetch_finnhub, parse_earnings, parse_insider_sentiment,
)
from collector.fetchers.watchlist import watchlist_tickers
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"


def _no_sleep(monkeypatch):
    async def fake_sleep(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


# ---------- World Bank ----------

def test_worldbank_parse_indicator():
    text = (FIX / "wb_gdp_sample.json").read_text()
    out = parse_indicator(text)
    assert set(out) == {"US", "DE"}
    for pts in out.values():
        assert len(pts) == 2
        assert all(isinstance(v, float) for _, v in pts)


async def test_worldbank_job_writes_series_and_doc(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    store = Store(tmp_path / "t.db")
    text = (FIX / "wb_gdp_sample.json").read_text()

    async def get_text(url, params=None, headers=None):
        assert "api.worldbank.org" in url
        return text

    assert await fetch_worldbank(store, get_text) == "worldbank-v2"
    # one batched call per indicator -> same 2-country fixture each time
    assert store.points("cycle:wb:US:gdp")
    assert store.points("cycle:wb:DE:cpi")
    assert store.points("cycle:wb:US:unemp")
    doc = store.doc("worldbank")
    assert doc is not None
    assert doc.payload["countries"]["US"]["gdp"]["value"] > 0


def test_worldbank_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_indicator('{"foo": 1}')


# ---------- USAspending ----------

def test_usaspending_parse_spending_over_time():
    body = json.loads((FIX / "usaspending_sot_sample.json").read_text())
    rows = parse_spending_over_time(body)
    assert len(rows) >= 1
    r = rows[0]
    assert {"date", "total_b", "contract_b", "direct_b", "grants_b"} <= set(r)
    assert r["total_b"] > 0


def test_usaspending_parse_category_drops_multiple_recipients():
    body = json.loads((FIX / "usaspending_recipient_sample.json").read_text())
    rows = parse_category(body, limit=5)
    assert rows
    assert all(r["name"] != "MULTIPLE RECIPIENTS" for r in rows)
    assert all(r["amount_b"] > 0 for r in rows)


async def test_usaspending_job_writes_series_and_doc(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    store = Store(tmp_path / "t.db")
    sot = (FIX / "usaspending_sot_sample.json").read_text()
    rec = (FIX / "usaspending_recipient_sample.json").read_text()
    agy = (FIX / "usaspending_agency_sample.json").read_text()
    calls = []

    async def post_json(url, payload=None, headers=None):
        calls.append(url)
        if url.endswith("/spending_over_time/"):
            return json.loads(sot)
        if "recipient" in url:
            return json.loads(rec)
        return json.loads(agy)

    assert await fetch_usaspending(store, post_json) == "usaspending-api"
    assert len(calls) == 3
    assert store.points("cycle:usaspending:oblig-total")
    assert store.points("cycle:usaspending:oblig-contract")
    doc = store.doc("usaspending")
    assert doc is not None
    assert doc.payload["top_recipients"]
    assert doc.payload["top_agencies"]
    assert doc.payload["monthly"]


# ---------- CoinGecko ----------

def test_coingecko_parse_markets():
    text = (FIX / "coingecko_markets_sample.json").read_text()
    coins = parse_markets(text)
    assert len(coins) == 3
    btc = coins[0]
    assert btc["id"] == "bitcoin" and btc["symbol"] == "BTC"
    assert btc["price"] and btc["mcap"] and btc["chg24h"] is not None


async def test_coingecko_job_writes_doc_and_series(tmp_path):
    store = Store(tmp_path / "t.db")
    text = (FIX / "coingecko_markets_sample.json").read_text()

    async def get_text(url, params=None, headers=None):
        assert "coingecko.com" in url
        return text

    assert await fetch_coingecko(store, get_text) == "coingecko-markets"
    doc = store.doc("coingecko")
    assert doc is not None
    assert len(doc.payload["coins"]) == 3
    assert doc.payload["btc_dominance_pct"] and doc.payload["btc_dominance_pct"] > 50
    assert store.points("cycle:coingecko:bitcoin:price")
    assert store.points("cycle:coingecko:ethereum:mcap")


def test_coingecko_empty_raises():
    with pytest.raises(ValueError):
        parse_markets('{"x": 1}')


# ---------- OpenFIGI ----------

def test_openfigi_parse_mappings():
    results = json.loads((FIX / "openfigi_mapping_sample.json").read_text())
    out = parse_mappings(results)
    assert set(out) == {"AAPL", "MSFT"}
    assert out["AAPL"]["figi"] == "BBG000BPH459"
    assert out["MSFT"]["exchCode"] == "US"


async def test_openfigi_skips_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")

    async def post_json(url, json=None, headers=None):
        raise AssertionError("no network without a key")

    assert await fetch_openfigi(store, post_json) == "openfigi-skipped-no-key"
    assert store.doc("openfigi_map") is None


async def test_openfigi_writes_map_with_key(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setenv("OPENFIGI_API_KEY", "test-key")
    store = Store(tmp_path / "t.db")
    results = json.loads((FIX / "openfigi_mapping_sample.json").read_text())
    seen = {}

    async def post_json(url, payload=None, headers=None):
        assert headers["X-OPENFIGI-APIKEY"] == "test-key"
        seen["n"] = len(payload)
        # the fixture has 4 jobs; echo it regardless of request size
        return results

    assert await fetch_openfigi(store, post_json) == "openfigi-mapping"
    doc = store.doc("openfigi_map")
    assert doc is not None
    assert doc.payload["mappings"]["AAPL"]["figi"] == "BBG000BPH459"


# ---------- Finnhub ----------

def test_finnhub_parse_earnings_filters_watchlist():
    body = json.loads((FIX / "finnhub_earnings_sample.json").read_text())
    out = parse_earnings(body, {"MSFT", "META"})
    assert {e["symbol"] for e in out} == {"MSFT", "META"}  # UNRELATED dropped
    assert out[0]["date"] <= out[1]["date"]  # sorted
    assert out[0]["eps_estimate"] == 2.31


def test_finnhub_parse_insider_sentiment():
    body = json.loads((FIX / "finnhub_insider_sample.json").read_text())
    out = parse_insider_sentiment(body)
    assert len(out) == 2
    assert out[0]["symbol"] == "MSFT" and out[0]["mspr"] == 18.4


async def test_finnhub_skips_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")

    async def get_text(url, params=None, headers=None):
        raise AssertionError("no network without a key")

    assert await fetch_finnhub(store, get_text) == "finnhub-skipped-no-key"
    assert store.doc("finnhub") is None


async def test_finnhub_writes_earnings_and_insider(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    store = Store(tmp_path / "t.db")
    earn = (FIX / "finnhub_earnings_sample.json").read_text()
    ins = (FIX / "finnhub_insider_sample.json").read_text()
    tickers = watchlist_tickers()
    assert "MSFT" in tickers and "META" in tickers

    async def get_text(url, params=None, headers=None):
        assert params["token"] == "test-key"
        if "calendar/earnings" in url:
            return earn
        assert "insider-sentiment" in url
        return ins

    assert await fetch_finnhub(store, get_text) == "finnhub-v1"
    doc = store.doc("finnhub")
    assert doc is not None
    syms = {e["symbol"] for e in doc.payload["earnings"]}
    assert syms <= set(tickers)
    assert "MSFT" in syms  # in the watchlist -> kept
    assert doc.payload["insider"]  # insider pulled for earnings names


# ---------- watchlist ----------

def test_watchlist_tickers_deduped_and_ordered():
    t = watchlist_tickers()
    assert len(t) == len(set(t))
    assert t[:6] == ["MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "META"]
    assert len(t) > 50  # three universes contribute


# ---------- insights notes ----------

def test_crypto_breadth_note(tmp_path):
    import json as _j
    from collector.insights import _crypto_breadth_note
    store = Store(tmp_path / "t.db")
    assert _crypto_breadth_note(store) is None  # no doc -> None
    coins = _j.loads((FIX / "coingecko_markets_sample.json").read_text())
    rows = [{"chg24h": -6.0}, {"chg24h": 7.0}, {"chg24h": 0.5}]
    store.put_doc("coingecko", {"as_of": "2026-10-03", "coins": rows,
                                "btc_dominance_pct": 59.2}, "coingecko-markets")
    note = _crypto_breadth_note(store)
    assert note["n"] == 3 and note["down5_24h"] == 1 and note["up5_24h"] == 1
    assert note["btc_dominance_pct"] == 59.2


def test_fiscal_note(tmp_path):
    from collector.insights import _fiscal_note
    store = Store(tmp_path / "t.db")
    assert _fiscal_note(store) is None
    monthly = [{"date": f"2026-0{m}-01", "total_b": 500.0 + m,
                "contract_b": 80.0, "grants_b": 200.0} for m in range(1, 7)]
    store.put_doc("usaspending", {"as_of": "2026-10-03", "monthly": monthly,
                                  "top_recipients": [], "top_agencies": []},
                  "usaspending-api")
    note = _fiscal_note(store)
    assert note["latest_month"] == "2026-06"
    assert note["latest_total_b"] == 506.0
    assert note["avg3_total_b"] == round((503.0 + 504.0 + 505.0) / 3, 1)


def test_finnhub_note(tmp_path):
    import json as _j
    from datetime import date as _d, timedelta as _td
    from collector.insights import _finnhub_note
    store = Store(tmp_path / "t.db")
    assert _finnhub_note(store) is None
    soon = (_d.today() + _td(days=3)).isoformat()
    later = (_d.today() + _td(days=20)).isoformat()
    store.put_doc("finnhub", {
        "as_of": "2026-10-03",
        "earnings": [{"symbol": "MSFT", "date": soon},
                     {"symbol": "META", "date": later}],
        "insider": [{"symbol": "MSFT", "mspr": 10.0},
                    {"symbol": "META", "mspr": -4.0}],
    }, "finnhub-v1")
    note = _finnhub_note(store)
    assert note["n_earnings"] == 2
    assert note["week_out"] == ["MSFT"]
    assert note["avg_mspr"] == 3.0


def test_usaspending_fiscal_month_conversion():
    from collector.fetchers.usaspending import _fiscal_to_calendar
    from datetime import date as _d
    assert _fiscal_to_calendar(2026, 7) == _d(2026, 4, 1)   # Apr 2026
    assert _fiscal_to_calendar(2026, 12) == _d(2026, 9, 1)  # Sep 2026
    assert _fiscal_to_calendar(2026, 1) == _d(2025, 10, 1)  # Oct 2025
    assert _fiscal_to_calendar(2027, 1) == _d(2026, 10, 1)  # Oct 2026


def test_usaspending_parse_uses_calendar_months():
    body = {"results": [
        {"time_period": {"fiscal_year": "2026", "month": "7"},
         "aggregated_amount": 1e9, "Contract_Obligations": 2e8,
         "Direct_Obligations": 0, "Grant_Obligations": 3e8},
    ]}
    rows = parse_spending_over_time(body)
    assert rows[0]["date"].isoformat() == "2026-04-01"


# ---------- FIGI lookup endpoint ----------

def _figi_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from collector.api import create_app
    from collector.config import load_config
    from pathlib import Path as _P
    store = Store(tmp_path / "t.db")
    cfg = load_config(_P(__file__).resolve().parents[2] / "config.yaml")
    return TestClient(create_app(store, cfg))


def test_figi_lookup_no_key_returns_clean_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    client = _figi_client(tmp_path, monkeypatch)
    r = client.get("/api/figi/lookup", params={"idtype": "TICKER", "idvalue": "AAPL"})
    assert r.status_code == 200  # never a 500
    body = r.json()
    assert body["ok"] is False and body["error"] == "no_key"
    assert "OPENFIGI_API_KEY" in body["message"]


def test_figi_lookup_rejects_bad_idtype(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENFIGI_API_KEY", "test-key")
    client = _figi_client(tmp_path, monkeypatch)
    body = client.get("/api/figi/lookup", params={"idtype": "BOGUS", "idvalue": "AAPL"}).json()
    assert body["ok"] is False and body["error"] == "bad_idtype"


def test_figi_lookup_rejects_empty_value(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENFIGI_API_KEY", "test-key")
    client = _figi_client(tmp_path, monkeypatch)
    body = client.get("/api/figi/lookup", params={"idtype": "TICKER", "idvalue": "  "}).json()
    assert body["ok"] is False and body["error"] == "bad_idvalue"


def test_figi_lookup_proxies_openfigi(tmp_path, monkeypatch):
    import httpx as _httpx
    monkeypatch.setenv("OPENFIGI_API_KEY", "test-key")
    results = json.loads((FIX / "openfigi_mapping_sample.json").read_text())

    class Resp:
        status_code = 200
        def json(self):
            return results

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["key"] = headers["X-OPENFIGI-APIKEY"]
        seen["job"] = json[0]
        return Resp()

    monkeypatch.setattr(_httpx, "post", fake_post)
    client = _figi_client(tmp_path, monkeypatch)
    body = client.get("/api/figi/lookup", params={"idtype": "CUSIP", "idvalue": "037833100"}).json()
    assert body["ok"] is True
    assert seen["job"] == {"idType": "ID_CUSIP", "idValue": "037833100"}
    assert seen["key"] == "test-key"
    assert body["count"] == 2
    row = body["results"][0]
    assert row["ticker"] == "AAPL" and row["figi"] == "BBG000BPH459"
    assert row["composite_figi"] is None or isinstance(row["composite_figi"], str)
