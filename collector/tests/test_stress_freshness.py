"""Tests for collector.stress_freshness (WS5 Phase 2) and its engine wiring.

All data is synthetic. Sections marked HAND-CHECK reimplement the expected
value with plain inline math (independent of the code under test) per
prompt section 10.
"""

from datetime import date, timedelta

import pytest

from collector import stress_freshness as sf
from collector import stress_score as ss

AS_OF = date(2026, 10, 8)
AS_OF_ISO = "2026-10-08"


def _entry(freq, exp_lag, max_age, latest, n_obs=None):
    e = {"id": "t", "category": "c", "name": "n", "unit": "u",
         "direction": "+", "freq": freq, "tier": "core",
         "series_id": "cycle:t", "source": "s", "tag": "primary",
         "expected_lag_days": exp_lag, "max_age_days": max_age}
    if latest is not None:
        e["latest_obs"] = latest.isoformat() if isinstance(latest, date) else latest
    return e, n_obs


def daily_hist(n, end=AS_OF, step_days=1, start_val=0.0):
    return {((end - timedelta(days=step_days * (n - 1 - i))).isoformat()):
            start_val + float(i) for i in range(n)}


class FakeStore:
    def __init__(self, series):
        self.series = series
        self.docs = {}

    def points(self, series_id, since=None):
        return self.series.get(series_id, {})

    def put_doc(self, key, payload, source):
        self.docs[key] = payload


# --------------------------------------------------------------------------
# HAND-CHECK 1: daily stale boundary — stale iff age > lag + tolerance
# --------------------------------------------------------------------------
def test_handcheck_daily_stale_boundary():
    # as_of 2026-10-08; latest 2026-10-05 -> age 3d; threshold = 1 + 2 = 3.
    # stale iff age > 3, so age 3 is NOT stale (strict inequality).
    assert (AS_OF - date(2026, 10, 5)).days == 3
    e, _ = _entry("D", 1, 4, date(2026, 10, 5))
    stale, reason = sf.is_stale(e, AS_OF_ISO)
    assert stale is False, reason
    assert "ok" in reason
    # latest 2026-10-04 -> age 4 > 3 -> stale.
    assert (AS_OF - date(2026, 10, 4)).days == 4
    e2, _ = _entry("D", 1, 4, date(2026, 10, 4))
    stale2, reason2 = sf.is_stale(e2, AS_OF_ISO)
    assert stale2 is True
    assert "stale" in reason2 and "4d" in reason2


def test_daily_failed_band():
    # age 5 > max_age_days 4 -> hard fail, not merely stale.
    e, _ = _entry("D", 1, 4, AS_OF - timedelta(days=5))
    stale, reason = sf.is_stale(e, AS_OF_ISO)
    assert stale is True
    assert "failed" in reason and "max_age" in reason


def test_daily_fresh():
    e, _ = _entry("D", 1, 4, AS_OF - timedelta(days=1))
    stale, reason = sf.is_stale(e, AS_OF_ISO)
    assert stale is False
    assert reason.startswith("ok:")


# --------------------------------------------------------------------------
# HAND-CHECK 2: STAR (7d publication lag) is NOT stale at age 8d
# --------------------------------------------------------------------------
def test_handcheck_star_not_stale_at_8d():
    # LAG_STAR = (7, 21), freq D -> tolerance 2 -> stale iff age > 9.
    # latest 2026-09-30 -> age 8 <= 9 -> ok.
    assert (AS_OF - date(2026, 9, 30)).days == 8
    e, _ = _entry("D", 7, 21, date(2026, 9, 30))
    stale, reason = sf.is_stale(e, AS_OF_ISO)
    assert stale is False, reason
    # boundary: age 9 -> still ok; age 10 -> stale; age 22 -> failed.
    e9, _ = _entry("D", 7, 21, AS_OF - timedelta(days=9))
    assert sf.is_stale(e9, AS_OF_ISO)[0] is False
    e10, _ = _entry("D", 7, 21, AS_OF - timedelta(days=10))
    s10, r10 = sf.is_stale(e10, AS_OF_ISO)
    assert s10 is True and "stale" in r10
    e22, _ = _entry("D", 7, 21, AS_OF - timedelta(days=22))
    s22, r22 = sf.is_stale(e22, AS_OF_ISO)
    assert s22 is True and "failed" in r22


# --------------------------------------------------------------------------
# HAND-CHECK 3: OFR quarterly is NOT stale at age 100d
# --------------------------------------------------------------------------
def test_handcheck_ofr_quarterly_not_stale_at_100d():
    # LAG_Q = (120, 240), freq Q -> tolerance 30 -> stale iff age > 150.
    e, _ = _entry("Q", 120, 240, AS_OF - timedelta(days=100))
    stale, reason = sf.is_stale(e, AS_OF_ISO)
    assert stale is False, reason
    fresh = sf.classify_freshness(e, AS_OF_ISO, n_obs=300)
    assert fresh["status"] == "no_velocity"  # healthy Q row: scored, no velocity
    assert fresh["age_days"] == 100
    # age 151 -> stale; age 241 -> failed.
    e151, _ = _entry("Q", 120, 240, AS_OF - timedelta(days=151))
    assert sf.is_stale(e151, AS_OF_ISO)[0] is True
    e241, _ = _entry("Q", 120, 240, AS_OF - timedelta(days=241))
    s241, r241 = sf.is_stale(e241, AS_OF_ISO)
    assert s241 is True and "failed" in r241


def test_event_series_12d_not_stale():
    # LAG_E = (10, 45), freq E -> tolerance 5 -> stale iff age > 15.
    e, _ = _entry("E", 10, 45, AS_OF - timedelta(days=12))
    assert sf.is_stale(e, AS_OF_ISO)[0] is False
    e16, _ = _entry("E", 10, 45, AS_OF - timedelta(days=16))
    s16, r16 = sf.is_stale(e16, AS_OF_ISO)
    assert s16 is True and "stale" in r16


def test_weekly_and_monthly_tolerances():
    # W: stale iff age > 7 + 3 = 10.
    ew, _ = _entry("W", 7, 14, AS_OF - timedelta(days=10))
    assert sf.is_stale(ew, AS_OF_ISO)[0] is False
    ew2, _ = _entry("W", 7, 14, AS_OF - timedelta(days=11))
    assert sf.is_stale(ew2, AS_OF_ISO)[0] is True
    # M: stale iff age > 35 + 7 = 42.
    em, _ = _entry("M", 35, 70, AS_OF - timedelta(days=42))
    assert sf.is_stale(em, AS_OF_ISO)[0] is False
    em2, _ = _entry("M", 35, 70, AS_OF - timedelta(days=43))
    assert sf.is_stale(em2, AS_OF_ISO)[0] is True


def test_missing_latest_is_fail_closed():
    e, _ = _entry("D", 1, 4, None)
    stale, reason = sf.is_stale(e, AS_OF_ISO)
    assert stale is True  # cannot verify -> WS2 must skip
    fresh = sf.classify_freshness(e, AS_OF_ISO)
    assert fresh["status"] == "no_data"


def test_future_dated_obs_never_stale():
    # negative age clamps to 0.
    e, _ = _entry("D", 1, 4, AS_OF + timedelta(days=2))
    assert sf.is_stale(e, AS_OF_ISO)[0] is False


def test_default_lag_by_freq_when_keys_missing():
    e = {"id": "t", "freq": "W", "latest_obs": (AS_OF - timedelta(days=9)).isoformat()}
    # falls back to LAG_W (7, 14): age 9 <= 7 + 3 -> ok.
    assert sf.is_stale(e, AS_OF_ISO)[0] is False
    e2 = {"id": "t", "freq": "W", "latest_obs": (AS_OF - timedelta(days=11)).isoformat()}
    assert sf.is_stale(e2, AS_OF_ISO)[0] is True


def test_as_of_alias_key():
    # refresh_stress writes "as_of" for the latest obs date; accept it.
    e = {"id": "t", "freq": "D", "expected_lag_days": 1, "max_age_days": 4,
         "as_of": (AS_OF - timedelta(days=2)).isoformat()}
    assert sf.is_stale(e, AS_OF_ISO)[0] is False


def test_classify_building_under_60_obs():
    e, _ = _entry("D", 1, 4, AS_OF)  # fresh feed...
    fresh = sf.classify_freshness(e, AS_OF_ISO, n_obs=22)  # ...but thin history
    assert fresh["status"] == "building"
    assert "22" in fresh["reason"]


def test_classify_ok():
    e, _ = _entry("D", 1, 4, AS_OF - timedelta(days=2))
    fresh = sf.classify_freshness(e, AS_OF_ISO, n_obs=300)
    assert fresh["status"] == "ok"
    assert fresh["age_days"] == 2
    assert fresh["expected_lag_days"] == 1
    assert fresh["tolerance_days"] == 2
    assert fresh["max_age_days"] == 4


# --------------------------------------------------------------------------
# event_carried_value
# --------------------------------------------------------------------------
def test_event_carried_value():
    pts = {"2026-09-10": 2.1, "2026-09-17": 2.4, "2026-09-24": 2.2}
    out = sf.event_carried_value(pts)
    assert out["value"] == 2.2
    assert out["event_date"] == "2026-09-24"
    assert out["label"].startswith("E · 2026-09-24")
    assert out["n_events"] == 3
    assert out["carried"] is True


def test_event_carried_value_empty():
    out = sf.event_carried_value({})
    assert out["value"] is None and out["carried"] is False
    assert out["n_events"] == 0


# --------------------------------------------------------------------------
# HAND-CHECK 4: weekly step — velocity from the last two observations only
# --------------------------------------------------------------------------
def test_handcheck_weekly_step_velocity():
    # 30 weekly obs, direction "+": only the d1 step may be populated.
    vals = [100.0 + i * 1.5 + (2.0 if i % 2 else 0.0) for i in range(30)]
    h = {((AS_OF - timedelta(days=7 * (29 - i))).isoformat()): v
         for i, v in enumerate(vals)}
    out = ss.velocity(h, AS_OF, freq="W", direction="+")
    assert out["status"] == "ok"
    assert out["step"] == "weekly_step"
    # MANUAL: d1 raw = last two observations only.
    assert out["raw"]["d1"] == pytest.approx(vals[-1] - vals[-2])
    # d5/d20/d60 must be n/a — never a weekly "5d" change.
    assert out["raw"]["d5"] is None
    assert out["sigma"]["d5"] is None
    assert out["pctile"]["d5"] is None
    assert out["raw"]["d20"] is None and out["raw"]["d60"] is None
    # acceleration needs a 5-obs change: n/a for step-only series.
    assert out["accel"] is None


def test_event_step_velocity():
    # 6 auction events: last two events only, labelled "event step".
    vals = [2.40, 2.55, 2.38, 2.61, 2.47, 2.58]
    h = {((AS_OF - timedelta(days=7 * (5 - i))).isoformat()): v
         for i, v in enumerate(vals)}
    out = ss.velocity(h, AS_OF, freq="E", direction="-")
    assert out["step"] == "event_step"
    # direction "-" inverts so + = more stress: raw = -(last - prev).
    assert out["raw"]["d1"] == pytest.approx(-(vals[-1] - vals[-2]))
    assert out["raw"]["d5"] is None and out["accel"] is None


def test_q_no_velocity_unchanged():
    vals = [1.0 + 0.1 * i for i in range(12)]
    h = {((AS_OF - timedelta(days=90 * (11 - i))).isoformat()): v
         for i, v in enumerate(vals)}
    out = ss.velocity(h, AS_OF, freq="Q")
    assert out["status"] == "no_velocity"
    assert out["raw"] == {} and out["accel"] is None


def test_daily_velocity_unaffected():
    # freq D still computes all windows (regression: the step-only change
    # must not leak into daily series).
    vals = [i * 1.7 + (i % 5) + (3.0 if i % 11 else 0.0) for i in range(100)]
    h = {((AS_OF - timedelta(days=99 - i)).isoformat()): v
         for i, v in enumerate(vals)}
    out = ss.velocity(h, AS_OF, freq="D", direction="+")
    assert out["step"] is None
    assert out["raw"]["d5"] is not None
    assert out["accel"] is not None


# --------------------------------------------------------------------------
# history building: <60 obs -> no score, never a fake score
# --------------------------------------------------------------------------
def test_building_status_no_fake_score():
    # put/call-like: 22 daily obs. Engine gate is 252; spec §2 needs 60+.
    out = ss.score_series(daily_hist(22), "+", AS_OF)
    assert out["status"] == "building"
    assert out["score"] is None
    assert out["percentile"] is None
    assert out["n_obs"] == 22


# --------------------------------------------------------------------------
# refresh_stress integration: freshness contract for WS2
# --------------------------------------------------------------------------
def _reg(sid, freq, exp_lag, max_age, iid=None, direction="+", tag="primary"):
    return {"id": iid or sid, "category": "test", "name": sid, "unit": "u",
            "direction": direction, "freq": freq, "tier": "core",
            "series_id": sid, "source": "synthetic", "tag": tag,
            "expected_lag_days": exp_lag, "max_age_days": max_age}


def test_refresh_stress_freshness_contract():
    series = {
        # fresh daily: 300 obs ending today
        "cycle:fresh": daily_hist(300),
        # stale daily: 300 obs ending 4d ago (stale band: 3 < age 4 <= max_age 4)
        "cycle:stale": daily_hist(300, end=AS_OF - timedelta(days=4)),
        # failed daily: 300 obs ending 30d ago (> max_age 4)
        "cycle:failed": daily_hist(300, end=AS_OF - timedelta(days=30)),
        # STAR-like: 300 obs ending 8d ago (lag 7 + tol 2 = 9) -> ok
        "cycle:star": daily_hist(300, end=AS_OF - timedelta(days=8)),
        # quarterly: 260 obs ending 30d ago -> no_velocity (healthy)
        "cycle:q": {((AS_OF - timedelta(days=30 + 90 * (259 - i))).isoformat()):
                    float(i) for i in range(260)},
        # event series: 5 auction events, latest 6d ago -> building + E tag
        "cycle:ev": {((AS_OF - timedelta(days=6 + 7 * (4 - i))).isoformat()):
                     v for i, v in enumerate([2.40, 2.55, 2.38, 2.61, 2.47])},
        # weekly: 300 weekly obs ending today -> weekly step
        "cycle:w": {((AS_OF - timedelta(days=7 * (299 - i))).isoformat()):
                    100.0 + float(i) for i in range(300)},
        # thin history: 22 daily obs -> building
        "cycle:thin": daily_hist(22),
    }
    registry = [
        _reg("cycle:fresh", "D", 1, 4),
        _reg("cycle:stale", "D", 1, 4),
        _reg("cycle:failed", "D", 1, 4),
        _reg("cycle:star", "D", 7, 21),
        _reg("cycle:q", "Q", 120, 240),
        _reg("cycle:ev", "E", 10, 45, direction="-"),
        _reg("cycle:w", "W", 7, 14),
        _reg("cycle:thin", "D", 1, 4),
    ]
    store = FakeStore(series)
    ss.refresh_stress(store, registry, as_of=AS_OF)
    matrix = store.docs["stress_matrix"]
    vel = store.docs["stress_velocity"]
    ind = matrix["indicators"]

    assert ind["cycle:fresh"]["status"] == "ok"
    assert ind["cycle:fresh"]["freshness"]["status"] == "ok"

    # stale row: status written, WS2 skips (status != "ok"), listed in gaps.
    assert ind["cycle:stale"]["status"] == "stale"
    assert ind["cycle:stale"]["freshness"]["status"] == "stale"
    assert any(g["id"] == "cycle:stale" for g in matrix["gaps"])

    # failed row.
    assert ind["cycle:failed"]["status"] == "failed"
    assert "max_age" in ind["cycle:failed"]["freshness"]["reason"]

    # STAR regression: 8d old with a 7d lag is NOT stale.
    assert ind["cycle:star"]["status"] == "ok"
    assert ind["cycle:star"]["freshness"]["age_days"] == 8

    # healthy quarterly row: scored, no velocity.
    assert ind["cycle:q"]["status"] == "no_velocity"
    assert vel["indicators"]["cycle:q"]["status"] == "no_velocity"

    # event row: building (thin history), carried value tagged "E" + date.
    ev = ind["cycle:ev"]
    assert ev["status"] == "building"
    assert ev["event_carry"]["carried"] is True
    assert ev["event_carry"]["label"].startswith("E · ")
    assert ev["event_carry"]["n_events"] == 5
    assert ev["event_carry"]["event_date"] == (AS_OF - timedelta(days=6)).isoformat()
    assert vel["indicators"]["cycle:ev"]["step"] == "event_step"
    assert vel["indicators"]["cycle:ev"]["raw"]["d5"] is None

    # weekly row: only the weekly step; no d5 score-change or velocity.
    w = ind["cycle:w"]
    assert w["status"] == "ok"
    assert w["d1_score"] is not None
    assert w["d5_score"] is None and w["d20_score"] is None and w["d60_score"] is None
    assert vel["indicators"]["cycle:w"]["step"] == "weekly_step"
    assert vel["indicators"]["cycle:w"]["raw"]["d5"] is None
    assert vel["indicators"]["cycle:w"]["accel"] is None

    # thin-history row: building, no fake score.
    assert ind["cycle:thin"]["status"] == "building"
    assert ind["cycle:thin"]["score"] is None

    # WS2 contract: alert engine skips everything not "ok".
    skippable = {i for i, v in ind.items() if v["status"] != "ok"}
    assert skippable == {"cycle:stale", "cycle:failed", "cycle:q",
                         "cycle:ev", "cycle:thin"}


def test_refresh_stress_building_but_dead_feed_is_failed():
    # 22 obs (building) whose feed died 30d ago -> failed wins over building.
    series = {"cycle:thin-dead": daily_hist(22, end=AS_OF - timedelta(days=30))}
    store = FakeStore(series)
    ss.refresh_stress(store, [_reg("cycle:thin-dead", "D", 1, 4)], as_of=AS_OF)
    ind = store.docs["stress_matrix"]["indicators"]["cycle:thin-dead"]
    assert ind["status"] == "failed"  # 30d > max_age 4
    assert ind["score"] is None  # still no fake score
    # stale band (not failed): 22 obs ending 4d ago -> "stale", not "building".
    series2 = {"cycle:thin-stale": daily_hist(22, end=AS_OF - timedelta(days=4))}
    store2 = FakeStore(series2)
    ss.refresh_stress(store2, [_reg("cycle:thin-stale", "D", 1, 4)], as_of=AS_OF)
    ind2 = store2.docs["stress_matrix"]["indicators"]["cycle:thin-stale"]
    assert ind2["status"] == "stale"
    assert ind2["score"] is None
