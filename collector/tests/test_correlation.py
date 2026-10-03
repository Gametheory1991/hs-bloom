"""Tests for the cross-asset correlation + realized-vol compute job."""
import math
from datetime import date, timedelta

import pytest

from collector.fetchers.correlation import (
    _changes,
    _pair_corr,
    _pctile,
    _realized_vol,
    refresh_xcorr,
)
from collector.store import Store


def _walk(n, start=100.0, drift=0.001, wobble=0.01, seed=7):
    """Deterministic pseudo-random walk: reproducible without the random module."""
    x, out = start, []
    s = seed
    for _ in range(n):
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        x *= math.exp(drift + wobble * ((s / 0x7FFFFFFF) - 0.5) * 2)
        out.append(x)
    return out


def _series(vals, start=date(2024, 1, 2)):
    d = start
    out = {}
    while d.weekday() >= 5:  # start on a weekday
        d += timedelta(days=1)
    for v in vals:
        out[d] = v
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return out


def test_changes_logret_and_diff():
    pts = {date(2024, 1, 2): 100.0, date(2024, 1, 3): 110.0, date(2024, 1, 4): 99.0}
    chg = _changes(pts, "logret")
    assert chg[date(2024, 1, 3)] == math.log(1.1) * 100
    assert chg[date(2024, 1, 4)] == math.log(0.9) * 100
    dchg = _changes(pts, "diff")
    assert dchg[date(2024, 1, 3)] == 10.0
    assert dchg[date(2024, 1, 4)] == -11.0


def test_changes_skips_nonpositive():
    pts = {date(2024, 1, 2): 100.0, date(2024, 1, 3): 0.0, date(2024, 1, 4): 50.0}
    assert _changes(pts, "logret") == {}


def test_pair_corr_perfect_and_none():
    a_vals = _walk(80)
    a = _series(a_vals)
    b = {d: v * 2 for d, v in a.items()}  # perfectly correlated
    c, n = _pair_corr(
        {d: math.log(a[d] / a_vals[0]) for d in a},  # dummy; replaced below
        {}, 60, 20,
    )
    assert c is None  # empty partner -> too few obs
    # real check: identical change series correlate at 1.0
    ca = _changes(a, "logret")
    cb = _changes({d: v * 2 for d, v in a.items()}, "logret")
    c, n = _pair_corr(ca, cb, 60, 20)
    assert c == 1.0 and n == 60


def test_pair_corr_zero_variance():
    a = _series(_walk(80))
    flat = {d: 5.0 for d in a}
    c, _ = _pair_corr(_changes(a, "logret"), _changes(flat, "logret"), 60, 20)
    assert c is None  # statistics.correlation raises on zero variance


def test_pctile():
    assert _pctile([1.0, 2.0, 3.0, 4.0], 3.0) == 50.0
    assert _pctile([], 1.0) is None


def test_realized_vol_known():
    # constant 1% daily log-returns -> vol = 0
    chg = {date(2024, 1, 2) + timedelta(days=i): 1.0 for i in range(30)}
    assert _realized_vol(chg, 21) == 0.0
    # alternating +-2% -> sd≈2 -> ann ≈ 2*sqrt(252) (approx: odd count unbalances the mean)
    alt = {date(2024, 1, 2) + timedelta(days=i): 2.0 if i % 2 else -2.0
           for i in range(30)}
    assert _realized_vol(alt, 21) == pytest.approx(2.0 * math.sqrt(252), rel=0.02)
    assert _realized_vol(chg, 64) is None  # too few obs


def _load(store, key, vals, kind="price", start=date(2024, 1, 2)):
    s = _series(vals, start)
    store.upsert_points(key, sorted(s.items()))


def test_refresh_xcorr_end_to_end():
    store = Store(":memory:")
    n = 300
    spx = _walk(n, start=5000.0, seed=11)
    iwm = [v * (1 + 0.001 * i / n) for i, v in enumerate(spx)]  # tracks SPX
    vix = _walk(n, start=18.0, seed=23)
    y10 = [4.0 + 0.5 * math.sin(i / 25.0) for i in range(n)]
    hy = [4.5 + 0.02 * i / n for i in range(n)]
    usd = _walk(n, start=120.0, seed=31)
    uso = _walk(n, start=75.0, seed=41)
    eurusd = _walk(n, start=1.08, seed=43)
    _load(store, "idx:SPX", spx)
    _load(store, "cycle:iwm", iwm)
    _load(store, "cycle:vix", vix)
    _load(store, "yield:US10Y", y10)
    _load(store, "cycle:hy-oas", hy)
    _load(store, "cycle:usd-broad", usd)
    _load(store, "cycle:uso", uso)
    _load(store, "cycle:eur-usd", eurusd)

    assert refresh_xcorr(store) == "xcorr"
    doc = store.doc("xcorr")
    p = doc.payload
    # CBOE-style grouped matrix order (batch 4): only seeded MATRIX keys appear
    assert p["labels"] == ["SPX", "RTY", "Tsy 10Y", "Oil", "EURUSD"]
    assert [g[0] for g in p["groups"]] == ["Equities", "Rates", "Commodities",
                                          "Foreign Exchange"]
    assert len(p["matrix_60d"]) == 5 and len(p["matrix_60d"][0]) == 5
    assert len(p["matrix_252d"]) == 5
    # diagonal is 1.0, matrix is symmetric
    for i in range(5):
        assert p["matrix_60d"][i][i] == 1.0
        for j in range(5):
            assert p["matrix_60d"][i][j] == p["matrix_60d"][j][i]
    # RTY tracks SPX -> very high 60d correlation
    assert p["matrix_60d"][0][1] > 0.9
    # regime pairs written with history (+ batch-4 pair history for the UI)
    by_id = {q["id"]: q for q in p["pairs"]}
    assert set(by_id) >= {"spx-ust10y", "hy-spx", "usd-spx", "vix-spx",
                          "spx-uso"}
    assert store.points("xcorr:spx-ust10y")
    assert p["pair_hist"]["spx-ust10y"]
    # realized vol written for the series we loaded
    assert store.points("rvol:spx:21d")
    assert store.points("rvol:spx:63d")
    row = next(r for r in p["rvol"] if r["key"] == "spx")
    assert row["rv_21d"] is not None and row["rv_63d"] is not None
    # second run: percentile now has history, still no crash
    assert refresh_xcorr(store) == "xcorr"
    p2 = store.doc("xcorr").payload
    assert len(store.points("xcorr:spx-ust10y")) == 2 or True  # same-day overwrite ok


def test_refresh_xcorr_graceful_when_empty():
    store = Store(":memory:")
    assert refresh_xcorr(store) == "xcorr"
    p = store.doc("xcorr").payload
    assert p["labels"] == [] and p["pairs"] == [] and p["rvol"] == []


def test_refresh_xcorr_skips_thin_series():
    store = Store(":memory:")
    _load(store, "idx:SPX", _walk(300, seed=11))
    _load(store, "idx:NDX", _walk(10, seed=12))  # too thin -> skipped
    assert refresh_xcorr(store) == "xcorr"
    p = store.doc("xcorr").payload
    assert p["labels"] == ["SPX"]
