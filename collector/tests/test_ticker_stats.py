"""Tests for the speculator-style per-ticker stats fetcher.

No network: get_text is faked; FINNHUB_API_KEY is monkeypatched.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from collector.fetchers import ticker_stats
from collector.fetchers.ticker_stats import (
    assign_rs_ranks,
    compute_stats,
    downsample_weekly,
    fetch_ticker_stats,
    parse_candle,
    parse_metric,
    parse_quote,
)
from collector.store import Store


def _closes(n=260, start=100.0):
    today = date(2026, 10, 2)
    return [(today - timedelta(days=n - 1 - i), start + i * 0.5)
            for i in range(n)]


def test_parse_quote():
    q = parse_quote({"c": 123.45, "dp": 1.25})
    assert q == {"price": 123.45, "pct_1d": 0.0125}
    assert parse_quote({"c": 0}) is None
    assert parse_quote({}) is None


def test_parse_metric():
    m = parse_metric({"metric": {"marketCapitalization": 3040000,
                                 "peTTM": 28.4,
                                 "52WeekHigh": 150.0, "52WeekLow": 90.0}})
    assert m == {"mcap": 3040000, "pe": 28.4,
                 "high52": 150.0, "low52": 90.0}


def test_parse_metric_pe_fallback_and_negative():
    m = parse_metric({"metric": {"peTTM": -5, "peNormalizedAnnual": 22.1}})
    assert m["pe"] == 22.1
    m2 = parse_metric({"metric": {}})
    assert m2 == {"mcap": None, "pe": None,
                  "high52": None, "low52": None}


def test_parse_candle_sorts_and_skips_nulls():
    body = {"s": "ok",
            "t": [1727827200, 1727740800, 1727913600],
            "c": [101.0, None, 102.0]}
    pts = parse_candle(body)
    assert len(pts) == 2
    assert pts[0][0] < pts[1][0]
    assert parse_candle({"s": "no_data"}) == []


def test_downsample_weekly_caps_points():
    pts = downsample_weekly(_closes(365))  # 365 calendar days ~ 52 ISO weeks
    assert 50 <= len(pts) <= 53
    assert pts[-1] == round(100.0 + 364 * 0.5, 2)


def test_compute_stats_sma_ytd_offhigh():
    closes = _closes(260, 100.0)
    quote = {"price": 229.5, "pct_1d": 0.01}
    metric = {"mcap": 1000.0, "pe": 20.0, "high52": 250.0, "low52": 90.0}
    s = compute_stats(closes, quote, metric)
    assert s["price"] == 229.5
    assert s["sma20"] == round(sum(100.0 + i * 0.5 for i in range(240, 260)) / 20, 2)
    assert s["sma200"] is not None
    assert s["off_high52"] == round(229.5 / 250.0 - 1, 4)
    assert s["pe"] == 20.0
    assert s["ret_1m"] == round(229.5 / (100.0 + 238 * 0.5) - 1, 4)
    assert len(s["spark"]) <= 53


def test_compute_stats_short_history_no_sma200():
    closes = _closes(30, 50.0)
    s = compute_stats(closes, {"price": 64.5, "pct_1d": 0},
                      {"mcap": None, "pe": None,
                       "high52": None, "low52": None})
    assert s["sma20"] is not None
    assert s["sma200"] is None
    assert s["pe"] is None
    assert s["off_high52"] is None


def test_assign_rs_ranks_percentile():
    tickers = {"A": {"ret_1m": 0.10}, "B": {"ret_1m": -0.05},
               "C": {"ret_1m": 0.02}, "D": {"ret_1m": None}}
    assign_rs_ranks(tickers)
    assert tickers["A"]["rs_1m"] == 99
    assert tickers["B"]["rs_1m"] == 0
    assert tickers["C"]["rs_1m"] == 50
    assert tickers["D"]["rs_1m"] is None


def _finnhub_fake(symbol):
    base = 100.0 + (sum(map(ord, symbol)) % 50)
    today = date(2026, 10, 2)
    n = 260
    closes = [base + i * 0.1 for i in range(n)]
    ts = [int((today - timedelta(days=n - 1 - i)).strftime("%s"))
          for i in range(n)]

    async def fake_get(url, params=None, headers=None):
        if url.endswith("/quote"):
            return json.dumps({"c": closes[-1], "dp": 1.5})
        if url.endswith("/stock/metric"):
            return json.dumps({"metric": {
                "marketCapitalization": 50000,
                "peTTM": 25.0, "52WeekHigh": closes[-1] * 1.1,
                "52WeekLow": closes[0] * 0.9}})
        if url.endswith("/stock/candle"):
            return json.dumps({"s": "ok", "t": ts, "c": closes})
        raise AssertionError(url)

    return fake_get


def test_fetch_ticker_stats_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    store = Store(tmp_path / "t.db")
    store.put_doc("regsho_daily", {
        "as_of": "2026-10-02",
        "top50": [{"symbol": "AAA"}, {"symbol": "BBB"}],
    }, source="finra-regsho")
    result = asyncio.run(fetch_ticker_stats(
        store, _finnhub_fake("AAA"), today=date(2026, 10, 2)))
    assert result == "ticker-stats"
    doc = store.doc("ticker_stats")
    assert doc.payload["count"] == 2
    a = doc.payload["tickers"]["AAA"]
    assert a["price"] > 0 and a["pe"] == 25.0
    assert a["mcap"] == 50000 and a["rs_1m"] in (0, 99)
    assert len(a["spark"]) <= 53
    assert a["sma20"] and a["sma50"] and a["sma200"]


def test_fetch_ticker_stats_no_key_skips(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")
    result = asyncio.run(fetch_ticker_stats(store, None))
    assert result == "ticker-stats-skipped-no-key"
    assert store.doc("ticker_stats") is None


def test_fetch_ticker_stats_no_universe_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    store = Store(tmp_path / "t.db")
    result = asyncio.run(fetch_ticker_stats(store, None))
    assert result == "ticker-stats-skipped-no-universe"
