"""Tests for fetchers/country_risk.py — synthetic store data only.

Note on methodology: inputs are percentile ranks vs trailing history, so a
series sitting at its trailing-window extreme is percentile 0 or 100 *by
construction*. Several tests below pin the last-N observations to engineer
an exact percentile.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from collector.fetchers.country_risk import (
    _apply_trend,
    _pctile,
    refresh_country_risk,
    score_country,
)


class FakeStore:
    def __init__(self, series: dict[str, dict[date, float]] | None = None):
        self.series = series or {}
        self.docs: dict[str, dict] = {}

    def points(self, key):
        return self.series.get(key, {})

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), d)()


def daily(start: date, n: int, fn) -> dict[date, float]:
    return {start + timedelta(days=i): fn(i) for i in range(n)}


START = date(2020, 1, 1)
N = 2000  # ~5.5y of daily data


def ramp_pinned(last_val: float, last_n: int = 30):
    """Linear 1.0 -> 3.0 ramp with the final `last_n` days pinned at last_val."""
    def fn(i: int) -> float:
        if i >= N - last_n:
            return last_val
        return 1.0 + 2.0 * i / (N - last_n)
    return fn


def test_pctile_edges():
    assert _pctile([], 1.0) is None
    assert _pctile([1, 2, 3], 0.5) == 0.0
    assert _pctile([1, 2, 3], 3.5) == 100.0
    assert _pctile([1, 2, 3], 2.0) == pytest.approx(33.33, abs=0.01)


def test_all_four_buckets():
    asof = START + timedelta(days=N - 1)
    # red: pinned at the top of a ramp -> ~100th percentile
    # green: pinned at the bottom -> ~0th percentile
    store = FakeStore({
        "yield:US10Y": daily(START, N, ramp_pinned(3.0)),
        "yield:DE10Y": daily(START, N, ramp_pinned(1.0)),
        "yield:FR10Y": daily(START, N, ramp_pinned(2.2)),  # ~55th pct -> orange
        "yield:IT10Y": daily(START, N, ramp_pinned(1.7)),  # ~28th pct -> yellow
    })
    us = score_country(store, "US", "United States", None, None, asof)
    de = score_country(store, "DE", "Germany", None, None, asof)
    fr = score_country(store, "FR", "France", None, None, asof)
    it = score_country(store, "IT", "Italy", None, None, asof)
    assert us["bucket"] == "red", us
    assert de["bucket"] == "green", de
    assert fr["bucket"] == "orange", fr
    assert it["bucket"] == "yellow", it
    assert us["score"] > fr["score"] > it["score"] > de["score"]


def test_missing_inputs_degrade():
    store = FakeStore({})
    asof = START + timedelta(days=N - 1)
    p = score_country(store, "US", "United States", "SPX", ("eur-usd", -1), asof)
    assert p["score"] is None
    assert p["bucket"] == "nodata"
    assert p["bucket_label"] == "NO DATA"
    assert p["inputs_available"] == 0
    # partial: yield only -> score == the single input
    store2 = FakeStore({"yield:US10Y": daily(START, N, lambda i: 3.0)})
    p2 = score_country(store2, "US", "United States", "SPX", ("eur-usd", -1), asof)
    assert p2["inputs_available"] == 1
    assert set(p2["inputs"]) == {"yield_pct"}
    assert p2["score"] == p2["inputs"]["yield_pct"]


def test_equity_drawdown_input():
    asof = START + timedelta(days=N - 1)
    # steady climb then a sharp 20% drop at the end -> extreme drawdown percentile
    def crashed(i):
        base = 100 + i * 0.05
        return base * 0.80 if i >= N - 30 else base
    store = FakeStore({"idx:SPX": daily(START, N, crashed)})
    p = score_country(store, "US", "United States", "SPX", None, asof)
    assert p["inputs"]["equity_dd_pct"] > 90
    # uninterrupted climb -> zero drawdown -> minimal percentile
    store2 = FakeStore({"idx:SPX": daily(START, N, lambda i: 100 + i * 0.05)})
    p2 = score_country(store2, "US", "United States", "SPX", None, asof)
    assert p2["inputs"]["equity_dd_pct"] < 10


def test_fx_depreciation_direction():
    asof = START + timedelta(days=399)

    def fx_move(base: float, target: float | None):
        def fn(i: int) -> float:
            if target is None or i < 300:
                return base
            f = (i - 300) / 99.0
            return base + (target - base) * f
        return fn

    # EUR falls 1.20 -> 1.05 late: sharp depreciation vs flat history -> high pct
    store = FakeStore({"cycle:eur-usd": daily(START, 400, fx_move(1.20, 1.05))})
    p = score_country(store, "DE", "Germany", None, ("eur-usd", -1), asof)
    assert p["inputs"]["fx_dep_pct"] > 80
    # EUR rises 1.00 -> 1.15 late: appreciation -> low percentile
    store2 = FakeStore({"cycle:eur-usd": daily(START, 400, fx_move(1.00, 1.15))})
    p2 = score_country(store2, "DE", "Germany", None, ("eur-usd", -1), asof)
    assert p2["inputs"]["fx_dep_pct"] < 20
    # JPY: usd-jpy rising = JPY depreciation (direction +1)
    store3 = FakeStore({"cycle:usd-jpy": daily(START, 400, fx_move(110.0, 150.0))})
    p3 = score_country(store3, "JP", "Japan", None, ("usd-jpy", +1), asof)
    assert p3["inputs"]["fx_dep_pct"] > 80


def test_trend_direction_no_lookahead():
    asof = START + timedelta(days=N - 1)
    # flat yields then a spike in the last 10d: 0th -> ~100th percentile
    store = FakeStore({"yield:US10Y": daily(START, N, lambda i: 2.0 + (3.0 if i >= N - 10 else 0.0))})
    p = _apply_trend(store, score_country(store, "US", "United States", None, None, asof), asof)
    assert p["trend"] == "deteriorating"
    assert p["trend_delta"] > 0
    # ramp into highs, then collapse inside the last 21d: ~100th -> ~70th percentile
    store2 = FakeStore({"yield:US10Y": daily(START, N, ramp_pinned(2.5, last_n=10))})
    # ramp_pinned ends at 2.5 after a 1->3 ramp: engineered improving case
    p2 = _apply_trend(store2, score_country(store2, "US", "United States", None, None, asof), asof)
    assert p2["trend"] == "improving", p2
    assert p2["trend_delta"] < 0
    # flat throughout -> flat trend
    store3 = FakeStore({"yield:US10Y": daily(START, N, lambda i: 3.0)})
    p3 = _apply_trend(store3, score_country(store3, "US", "United States", None, None, asof), asof)
    assert p3["trend"] == "flat"
    assert p3["trend_delta"] == 0.0


def test_no_lookahead_in_point_in_time():
    # triangle: peak at day 1000, back to start by day 1999
    def tri(i: int) -> float:
        return 1.0 + (i if i <= 1000 else 2000 - i) * 0.004
    store = FakeStore({"yield:US10Y": daily(START, N, tri)})
    p_mid = score_country(store, "US", "United States", None, None, START + timedelta(days=1000))
    p_end = score_country(store, "US", "United States", None, None, START + timedelta(days=N - 1))
    # at the peak the score must be ~100 even though the series later declines;
    # a lookahead implementation would dilute it
    assert p_mid["score"] == 100.0
    assert p_end["score"] < 50


@pytest.mark.asyncio
async def test_refresh_writes_doc_and_history():
    def crashed(i):
        base = 100 + i * 0.05
        return base * 0.80 if i >= N - 30 else base
    store = FakeStore({
        "yield:US10Y": daily(START, N, ramp_pinned(3.0)),
        "idx:SPX": daily(START, N, crashed),
    })
    asof = START + timedelta(days=N - 1)
    result = await refresh_country_risk(store, today=asof)
    assert result == "country-risk"
    doc = store.doc("country_risk")
    assert doc.payload["asof"] == asof.isoformat()
    assert len(doc.payload["countries"]) == 15
    codes = {c["code"] for c in doc.payload["countries"]}
    assert codes == {
        "US", "DE", "FR", "IT", "ES", "NL", "BE", "UK", "JP", "CA", "AU", "CH", "SE",
        "MX", "KR",
    }
    us = next(c for c in doc.payload["countries"] if c["code"] == "US")
    assert us["bucket"] == "red"
    assert us["trend"] in ("improving", "deteriorating", "flat", "unknown")
    assert store.points("risk:country:US")[asof] == us["score"]
    # countries with no data at all -> nodata and no history point written
    se = next(c for c in doc.payload["countries"] if c["code"] == "SE")
    assert se["bucket"] == "nodata"
    assert se["score"] is None
    assert "risk:country:SE" not in store.series
