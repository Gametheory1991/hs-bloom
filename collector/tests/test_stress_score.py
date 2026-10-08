"""Unit tests for collector.stress_score (Stress Monitor v2, Phase 1).

All data is synthetic. Sections marked HAND-CHECK reimplement the expected
value with plain inline math (independent of the code under test) per
prompt section 10.
"""

from datetime import date, timedelta
from statistics import fmean, pstdev

import pytest

from collector import stress_score as ss
from collector.store import Store


def d(i: int) -> date:
    return date(2023, 1, 2) + timedelta(days=i)


def hist(vals):
    return {d(i).isoformat(): float(v) for i, v in enumerate(vals)}


# --------------------------------------------------------------------------
# HAND-CHECK 1: percentile, direction "+", with duplicate values
# --------------------------------------------------------------------------
def test_handcheck_percentile_plus():
    # 252 ascending obs 0..251, then one more obs equal to 126 (the median-ish).
    vals = list(range(252)) + [126]
    h = hist(vals)
    out = ss.score_series(h, "+", d(len(vals) - 1))
    assert out["status"] == "ok"
    # MANUAL: percentile = 100 * #{x <= 126} / 253.
    # x <= 126: 0..126 (127 values) plus the final 126 -> 128 values.
    n_le = sum(1 for x in vals if x <= 126)
    expected = 100.0 * n_le / len(vals)
    assert n_le == 128
    assert abs(out["percentile"] - expected) < 1e-9
    assert out["score"] == out["percentile"]
    # manual z
    assert abs(out["z"] - (126 - fmean(vals)) / pstdev(vals)) < 1e-9
    assert out["n_obs"] == 253


# --------------------------------------------------------------------------
# HAND-CHECK 2: direction "-" mirrors the percentile
# --------------------------------------------------------------------------
def test_handcheck_direction_minus():
    vals = list(range(252)) + [126]
    h = hist(vals)
    out = ss.score_series(h, "-", d(len(vals) - 1))
    n_le = sum(1 for x in vals if x <= 126)
    expected_pct = 100.0 * n_le / len(vals)
    assert abs(out["score"] - (100.0 - expected_pct)) < 1e-9


# --------------------------------------------------------------------------
# HAND-CHECK 3: direction "±": percentile of |z| vs window median
# --------------------------------------------------------------------------
def test_handcheck_direction_twosided():
    vals = list(range(1, 253)) + [200]  # 253 obs, latest = 200
    h = hist(vals)
    out = ss.score_series(h, "±", d(len(vals) - 1))
    assert out["status"] == "ok"
    # MANUAL: median of window, sd of window, |z| of each obs, rank of latest.
    import statistics
    med = statistics.median(vals)
    sd = statistics.pstdev(vals)
    z_abs = [abs((x - med) / sd) for x in vals]
    z_latest = z_abs[-1]
    expected = 100.0 * sum(1 for zc in z_abs if zc <= z_latest) / len(vals)
    assert abs(out["score"] - expected) < 1e-9
    assert abs(out["z"] - (200 - med) / sd) < 1e-9


# --------------------------------------------------------------------------
# HAND-CHECK 4: velocity raw + normalized sigma (manual trailing std)
# --------------------------------------------------------------------------
def test_handcheck_velocity_raw_and_sigma():
    # 100 obs: linear trend with alternating step -> non-degenerate changes.
    vals = [i + (10 if i % 2 else 0) for i in range(100)]
    h = hist(vals)
    ao = d(len(vals) - 1)
    out = ss.velocity(h, ao, direction="+")
    k = 1
    # MANUAL raw
    raw = vals[-1] - vals[-2]
    assert out["raw"]["d1"] == pytest.approx(raw)
    # MANUAL trailing-1Y std of 1-changes
    changes = [vals[j] - vals[j - 1] for j in range(1, len(vals))]
    trail = changes[-252:]
    expected_sigma = raw / pstdev(trail)
    assert out["sigma"]["d1"] == pytest.approx(expected_sigma)
    # MANUAL k=5
    raw5 = vals[-1] - vals[-6]
    changes5 = [vals[j] - vals[j - 5] for j in range(5, len(vals))]
    assert out["sigma"]["d5"] == pytest.approx(raw5 / pstdev(changes5[-252:]))


# --------------------------------------------------------------------------
# HAND-CHECK 5: velocity percentile (rank of |change| vs trailing changes)
# --------------------------------------------------------------------------
def test_handcheck_velocity_percentile():
    # 60 obs, all +1 changes, then one +5 jump -> |change| is the max.
    vals = [float(i) for i in range(59)] + [63.0]  # last change = +5
    h = hist(vals)
    out = ss.velocity(h, d(len(vals) - 1))
    assert out["raw"]["d1"] == 5.0
    # MANUAL: 59 changes, all |1| except the last |5| -> rank 59/59.
    changes = [vals[j] - vals[j - 1] for j in range(1, len(vals))]
    expected = 100.0 * sum(1 for c in changes if abs(c) <= 5.0) / len(changes)
    assert expected == 100.0
    assert out["pctile"]["d1"] == pytest.approx(expected)


# --------------------------------------------------------------------------
# HAND-CHECK 6: acceleration = sigma5(t) - sigma5(t-5), manual
# --------------------------------------------------------------------------
def test_handcheck_acceleration():
    import math
    vals = [10.0 * math.sin(2 * math.pi * i / 20) for i in range(300)]
    h = hist(vals)
    out = ss.velocity(h, d(len(vals) - 1))

    def sigma5(end):
        # MANUAL 5-obs normalized velocity ending at index `end`
        raw5 = vals[end] - vals[end - 5]
        ch = [vals[j] - vals[j - 5] for j in range(max(5, end - 252 + 1), end + 1)]
        return raw5 / pstdev(ch)

    expected = sigma5(299) - sigma5(294)
    assert out["accel"] == pytest.approx(expected)
    assert out["accel"] != 0.0  # the series genuinely accelerates/decelerates


# --------------------------------------------------------------------------
# no look-ahead
# --------------------------------------------------------------------------
def test_no_lookahead():
    vals = list(range(300))
    h_past = hist(vals[:280])
    h_full = hist(vals)
    ao = d(279)
    a = ss.score_series(h_past, "+", ao)
    b = ss.score_series(h_full, "+", ao)  # future obs exist but are excluded
    assert a["score"] == b["score"] == pytest.approx(b["score"])
    assert a["n_obs"] == b["n_obs"] == 280
    va = ss.velocity(h_past, ao)
    vb = ss.velocity(h_full, ao)
    assert va["raw"] == vb["raw"]


# --------------------------------------------------------------------------
# min-252 "building" status
# --------------------------------------------------------------------------
def test_building_status():
    h = hist(range(200))
    out = ss.score_series(h, "+", d(199))
    assert out["status"] == "building"
    assert out["score"] is None
    assert out["n_obs"] == 200


def test_no_data_status():
    out = ss.score_series({}, "+", d(0))
    assert out["status"] == "no_data"
    assert out["score"] is None


# --------------------------------------------------------------------------
# gap handling: no interpolation, changes between stored obs only
# --------------------------------------------------------------------------
def test_gap_not_interpolated():
    # obs on days 0,1,2 then a gap to day 10: k=1 change is v(10)-v(2),
    # NOT divided by the 8-day calendar gap.
    hh = {d(0).isoformat(): 100.0, d(1).isoformat(): 101.0,
          d(2).isoformat(): 102.0, d(10).isoformat(): 110.0}
    out = ss.velocity(hh, d(10))
    assert out["raw"]["d1"] == 8.0
    assert out["raw"]["d5"] is None  # only 4 obs < k+1 -> n/a, never 0


def test_short_history_windows_na():
    h = hist(range(30))
    out = ss.velocity(h, d(29))
    assert out["raw"]["d1"] == 1.0
    assert out["raw"]["d60"] is None
    assert out["status"] == "ok"


# --------------------------------------------------------------------------
# Q-series: no velocity
# --------------------------------------------------------------------------
def test_q_series_no_velocity():
    vals = [1.0 + 0.1 * i for i in range(40)]  # 40 quarterly obs
    out = ss.velocity(hist(vals), d(39), freq="Q")
    assert out["status"] == "no_velocity"
    assert out["accel"] is None


# --------------------------------------------------------------------------
# composites: 50% coverage rule
# --------------------------------------------------------------------------
def test_composite_coverage_rule():
    ok = ss.composite([80.0, 60.0, 70.0, None, None])   # 3/5 = 60%
    assert ok["status"] == "ok"
    assert ok["score"] == pytest.approx(70.0)
    assert ok["coverage"] == pytest.approx(0.6)
    low = ss.composite([80.0, 60.0, None, None, None])  # 2/5 = 40%
    assert low["status"] == "low_coverage"
    assert low["score"] == pytest.approx(70.0)  # still computed, but excluded
    edge = ss.composite([80.0, None])  # exactly 50% -> ok (rule is <50%)
    assert edge["status"] == "ok"
    empty = ss.composite([None, None])
    assert empty["status"] == "no_data"
    assert empty["score"] is None


# --------------------------------------------------------------------------
# overall gauge: reweighting + regime boundaries
# --------------------------------------------------------------------------
def test_overall_gauge_reweighting():
    comps = {
        "funding": {"score": 80.0, "status": "ok"},
        "credit": {"score": 20.0, "status": "low_coverage"},  # dropped
        "rates": {"score": 50.0, "status": "ok"},
    }
    w = {"funding": 0.5, "credit": 0.25, "rates": 0.25}
    out = ss.overall_gauge(comps, w)
    # reweighted over funding+rates: (0.5*80 + 0.25*50) / 0.75
    assert out["score"] == pytest.approx((40.0 + 12.5) / 0.75)
    assert out["dropped"] == ["credit"]
    assert out["reweighted"] is True
    assert out["regime"] == "High"  # 70.0
    assert out["weights_used"]["funding"] == pytest.approx(0.5 / 0.75)


def test_regime_boundaries():
    cases = [(0, "Calm"), (29.99, "Calm"), (30, "Normal"), (49.99, "Normal"),
             (50, "Elevated"), (69.99, "Elevated"), (70, "High"),
             (85, "High"), (85.01, "Acute"), (100, "Acute")]
    for score, regime in cases:
        assert ss.regime_label(score) == regime, score
    assert ss.regime_label(None) is None


# --------------------------------------------------------------------------
# refresh_stress: end-to-end doc write
# --------------------------------------------------------------------------
def _store_with_series(tmp_path):
    store = Store(tmp_path / "t.db")
    # 300 trading obs per series; vix trending up -> high score
    for sid, base, slope in (("cycle:vix", 15.0, 0.05),
                             ("cycle:hy-oas", 300.0, 1.0),
                             ("cycle:sofr-iorb", 5.0, 0.02),
                             ("cycle:claims", 220.0, 0.1)):
        store.upsert_points(
            sid, [(d(i), base + slope * i + (i % 7)) for i in range(300)])
    return store


def test_refresh_stress_writes_docs(tmp_path):
    store = _store_with_series(tmp_path)
    ss.refresh_stress(store, ss.SAMPLE_REGISTRY, as_of=d(299))

    mdoc = store.doc("stress_matrix")
    vdoc = store.doc("stress_velocity")
    assert mdoc is not None and vdoc is not None

    m = mdoc.payload
    assert set(m) == {"updated_at", "indicators", "composites",
                      "overall", "gaps"}
    ind = m["indicators"]["vix"]
    assert set(ind) >= {"id", "category", "name", "unit", "direction", "freq",
                        "tier", "value", "as_of", "source", "tag", "score",
                        "percentile", "z", "d1_score", "d5_score", "d20_score",
                        "d60_score", "status", "window_used", "confidence",
                        "history_n"}
    assert ind["status"] == "ok"
    assert ind["score"] is not None and 0 <= ind["score"] <= 100
    assert ind["d5_score"] is not None  # score velocity computed
    assert m["overall"]["regime"] in (
        "Calm", "Normal", "Elevated", "High", "Acute")
    assert set(m["overall"]) == {"score", "regime", "as_of", "coverage"}
    # claims (freq W) is scored too: 300 weekly obs >= 252
    assert m["indicators"]["claims"]["status"] == "ok"

    v = vdoc.payload
    assert set(v) == {"updated_at", "indicators", "composites",
                      "overall", "breadth"}
    vi = v["indicators"]["vix"]
    assert set(vi["raw"]) == {"d1", "d5", "d20", "d60"}
    assert vi["status"] == "ok"
    assert set(v["overall"]) == {"d1", "d5", "d20", "sigma5", "accel",
                                 "speed_label"}
    assert set(v["breadth"]) == {"share_above_75", "share_fast_90", "n"}


def test_refresh_stress_low_coverage_excluded(tmp_path):
    store = Store(tmp_path / "t.db")
    # credit category: 3 indicators, only 1 has enough history -> low coverage
    store.upsert_points("cycle:hy-oas", [(d(i), 300.0 + i) for i in range(300)])
    store.upsert_points("cycle:ig-oas", [(d(i), 100.0) for i in range(50)])
    reg = [
        dict(ss.SAMPLE_REGISTRY[1], id="hy_oas", category="credit"),
        {"id": "ig_oas", "category": "credit", "name": "IG", "unit": "bp",
         "direction": "+", "freq": "D", "tier": "core",
         "series_id": "cycle:ig-oas", "source": "FRED", "tag": "primary"},
        {"id": "ccc_oas", "category": "credit", "name": "CCC", "unit": "bp",
         "direction": "+", "freq": "D", "tier": "core",
         "series_id": "cycle:ccc-oas", "source": "FRED", "tag": "primary"},
    ]
    ss.refresh_stress(store, reg, as_of=d(299))
    m = store.doc("stress_matrix").payload
    assert m["composites"]["credit"]["status"] == "low_coverage"
    assert m["composites"]["credit"]["coverage"] == pytest.approx(1 / 3)
    # gaps list the two missing ones
    gap_ids = {g["id"] for g in m["gaps"]}
    assert {"ig_oas", "ccc_oas"} <= gap_ids


def test_refresh_stress_q_excluded_from_composite(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_points("cycle:vix", [(d(i), 15.0 + 0.01 * i) for i in range(300)])
    store.upsert_points("ofr:hf-lev", [(d(i * 90), 2.0) for i in range(40)])
    reg = [
        dict(ss.SAMPLE_REGISTRY[0]),
        {"id": "hf_leverage", "category": "volatility", "name": "HF lev",
         "unit": "x", "direction": "+", "freq": "Q", "tier": "extended",
         "series_id": "ofr:hf-lev", "source": "OFR", "tag": "primary"},
    ]
    ss.refresh_stress(store, reg, as_of=d(299))
    m = store.doc("stress_matrix").payload
    comp = m["composites"]["volatility"]
    assert comp["n_total"] == 1  # Q-series excluded from the denominator
    assert comp["status"] == "ok"
    v = store.doc("stress_velocity").payload
    assert v["indicators"]["hf_leverage"]["status"] == "no_velocity"
