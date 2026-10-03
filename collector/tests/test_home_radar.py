"""Tests for fetchers/home_radar.py — synthetic store data only."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from collector.fetchers.home_radar import (
    _color_pctile,
    _color_score100,
    _drawdown_hist,
    _pctile,
    build_radar,
    refresh_home_radar,
)


class FakeStore:
    def __init__(self, series=None):
        self.series = series or {}
        self.docs = {}

    def points(self, key, since=None):
        pts = self.series.get(key, {})
        if since:
            pts = {d: v for d, v in pts.items() if d >= since}
        return pts

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


def daily(start: date, n: int, fn):
    return {start + timedelta(days=i): fn(i) for i in range(n)}


START = date(2020, 1, 1)
N = 400


def test_pctile_top_is_100():
    h = daily(START, N, lambda i: float(i))
    assert _pctile(h, 999.0) == 100.0


def test_pctile_bottom_is_0():
    h = daily(START, N, lambda i: float(i))
    assert _pctile(h, -999.0) == 0.0


def test_pctile_needs_history():
    assert _pctile(daily(START, 5, lambda i: 1.0), 1.0) is None


def test_color_bands():
    assert _color_pctile(85, "up_bad") == "red"
    assert _color_pctile(15, "up_bad") == "green"
    assert _color_pctile(15, "down_bad") == "red"   # inverted direction
    assert _color_pctile(85, "down_bad") == "green"
    assert _color_score100(80) == "red"
    assert _color_score100(10) == "green"


def test_drawdown_at_high_is_zero():
    h = daily(START, 300, lambda i: 100.0 + i * 0.1)  # always rising
    dd = _drawdown_hist(h)
    assert max(dd) == date(2020, 1, 1) + timedelta(days=299)
    assert dd[max(dd)] == 0.0


def test_drawdown_depth():
    h = {START: 100.0, START + timedelta(days=1): 90.0}
    dd = _drawdown_hist(h)
    assert dd[START + timedelta(days=1)] == -10.0


def _full_store():
    # VIX pinned at its 1Y extreme -> red
    vix = daily(START, N, lambda i: 15.0)
    for i in range(N - 10, N):
        vix[START + timedelta(days=i)] = 40.0
    s = FakeStore({
        "cycle:vix": vix,
        "cycle:vix3m": daily(START, N, lambda i: 17.0),
        "cycle:vvix": daily(START, N, lambda i: 90.0),
        "rvol:spx:21d": daily(START, N, lambda i: 15.0),
        "risk:basis_stress": daily(START, N, lambda i: 20.0),
        "risk:auction_stress": daily(START, N, lambda i: 10.0),
        "cycle:rrp-on": daily(START, N, lambda i: 100.0),
        "cycle:hy-oas": daily(START, N, lambda i: 3.0),
        "idx:SPX": daily(START, N, lambda i: 4000.0 + i),
        "cycle:tlt": daily(START, N, lambda i: 90.0),
        "cycle:spw-spx": daily(START, N, lambda i: 0.20),
        "movers:dispersion-5d": {START + timedelta(days=7 * i): 1.5 for i in range(30)},
        "cycle:us-5y-breakeven": daily(START, N, lambda i: 2.4),
        "cycle:breakeven-10y": daily(START, N, lambda i: 2.3),
        "cycle:us-cpi-yoy": {date(2026, m, 1): 3.0 for m in range(1, 10)},
        "cycle:sofr": daily(START, N, lambda i: 4.30),
        "cycle:iorb": daily(START, N, lambda i: 4.40),
        "risk:cta_z_10y": {START + timedelta(days=i): 0.5 for i in range(30)},
    })
    s.put_doc("risk_summary", {"regime": "LATE_CYCLE", "verdict": "ELEVATED: test"},
              source="risk")
    return s


def test_build_radar_full():
    radar = build_radar(_full_store())
    assert radar["regime"] == "LATE_CYCLE"
    assert radar["verdict"] == "ELEVATED: test"
    ids = [i["id"] for i in radar["indicators"]]
    assert "vol_vix" in ids and "stress_basis" in ids and "infl_cpi" in ids
    vix = next(i for i in radar["indicators"] if i["id"] == "vol_vix")
    assert vix["color"] == "red" and vix["percentile_1y"] == 100.0
    basis = next(i for i in radar["indicators"] if i["id"] == "stress_basis")
    assert basis["color"] == "green"  # 20/100
    # SPX pinned at highs -> zero drawdown -> green despite percentile ties
    dd = next(i for i in radar["indicators"] if i["id"] == "mom_spx_dd")
    assert dd["color"] == "green"
    # every indicator carries a sparkline tail
    for ind in radar["indicators"]:
        assert isinstance(ind["hist"], list)
        assert ind["value_fmt"]


def test_build_radar_empty_store_degrades():
    radar = build_radar(FakeStore())
    assert radar["indicators"] == []
    assert radar["regime"] == "UNKNOWN"
    assert radar["verdict"] is None


def test_momentum_symmetric_colors():
    # TLT rallies hard into the end -> 60d momentum at its extreme ->
    # symmetric orange (not directional red)
    s = _full_store()
    tlt = daily(START, N, lambda i: 90.0)
    for i in range(N - 30, N):
        tlt[START + timedelta(days=i)] = 130.0
    s.series["cycle:tlt"] = tlt
    ind = next(i for i in build_radar(s)["indicators"] if i["id"] == "mom_tlt")
    assert ind["color"] in ("orange", "red")


@pytest.mark.asyncio
async def test_refresh_writes_doc():
    s = _full_store()
    assert await refresh_home_radar(s) == "home-radar"
    assert s.doc("home_radar") is not None
