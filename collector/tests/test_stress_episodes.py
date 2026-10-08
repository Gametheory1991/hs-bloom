"""Unit tests for collector.stress_episodes (Stress Monitor v2, Phase 2 WS3).

All data is synthetic and deterministic. Sections marked HAND-CHECK reimplement
the expected value with plain inline math, independent of the code under test
(never calling the module's own change functions for the expectation).
"""

import ast
import math
import os
from datetime import date, timedelta

import pytest

from collector import stress_episodes as se
from collector import stress_score as ss

VELOCITY_PY = os.path.expanduser("~/workspace/velocity/velocity.py")
AS_OF = date(2026, 10, 8)


def d(y, m, day):
    return date(y, m, day)


def drange(start, n, step=1):
    return [start + timedelta(days=i * step) for i in range(n)]


def _reg_entry(iid, direction="+", freq="D", sid=None, tag="primary",
               category="Cat"):
    return {"id": iid, "category": category, "name": f"syn {iid}",
            "unit": "x", "direction": direction, "freq": freq,
            "tier": "core", "series_id": sid or f"s:{iid}",
            "source": "synthetic", "tag": tag}


REG = [
    _reg_entry("aapl", "+", "D"),
    _reg_entry("bneg", "-", "D"),
    _reg_entry("wser", "+", "W"),
    _reg_entry("qser", "+", "Q"),
    _reg_entry("eser", "+", "E"),
]
WEIGHTS = {"Cat": 1.0}


class FakeStore:
    def __init__(self, data):
        self.data = data
        self.docs = {}

    def points(self, sid):
        return self.data.get(sid, {})

    def put_doc(self, key, payload, source):
        self.docs[key] = (payload, source)


def synth_store():
    """Deterministic daily/weekly/quarterly/event series, 2007-01-01 -> AS_OF."""
    start = d(2007, 1, 1)
    n = (AS_OF - start).days + 1
    days = drange(start, n)
    data = {
        "s:aapl": {t: 10.0 + 3.0 * math.sin(i * 0.11) + 0.004 * i
                   for i, t in enumerate(days)},
        "s:bneg": {t: 50.0 - 2.0 * math.sin(i * 0.07) - 0.002 * i
                   for i, t in enumerate(days)},
        "s:wser": {t: 5.0 + math.sin(i * 0.3) for i, t in enumerate(days[::7])},
        "s:qser": {t: 1.0 + 0.01 * i for i, t in enumerate(days[::91])},
        "s:eser": {t: 2.0 for t in days[::30]},
    }
    return FakeStore(data)


# ---------------------------------------------------------------------------
# 1. registry fidelity: embedded EPISODES == the source file, parsed at test time
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(VELOCITY_PY),
                    reason="velocity.py registry source not present")
def test_registry_fidelity():
    src = open(VELOCITY_PY).read()
    tree = ast.parse(src)
    parsed = None
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") == "EPISODES"):
            parsed = ast.literal_eval(node.value)
    assert parsed is not None, "EPISODES not found in velocity.py"
    assert se.EPISODES == parsed
    assert len(se.EPISODES) == 8
    # every episode carries start/finish; peak is honestly "dates missing"
    for meta in se.EPISODE_META:
        assert meta["start"] and meta["finish"] and meta["trigger"]
        assert meta["peak"] == "dates missing"


# ---------------------------------------------------------------------------
# 2. no-history behavior: < 30 obs in the window -> "no history", never zero
# ---------------------------------------------------------------------------

def test_no_history():
    win = [d(2008, 9, 1) + timedelta(days=i) for i in range(10)]  # 10 < 30
    store = FakeStore({"s:thin": {t: 1.0 + 0.1 * i for i, t in enumerate(win)}})
    reg = [_reg_entry("thin", "+", "D")]
    payload = se.episodes_payload(store, reg, WEIGHTS, as_of=AS_OF)
    cell = payload["indicators"]["thin"]["episodes"]["GFC"]["30d"]
    assert cell["status"] == "no_history"
    assert cell["episode_max"] is None and cell["ratio_pct"] is None
    assert cell["episode_max"] != 0  # never zero
    assert "30" in cell["note"]


def test_empty_store_is_no_data_not_crash():
    payload = se.episodes_payload(FakeStore({}), REG, WEIGHTS, as_of=AS_OF)
    assert payload["indicators"]["aapl"]["status"] == "no_data"
    assert payload["header"]["faster_than_peak_30d"] == 0
    assert payload["closest_analog"]["episode"] is None


# ---------------------------------------------------------------------------
# 3. 150% cap
# ---------------------------------------------------------------------------

def test_ratio_capped_at_150():
    start = d(2026, 1, 1)
    n = (AS_OF - start).days + 1
    days = drange(start, n)
    # slow drift through the Iran-war window, then a +10 jump near the end
    vals = {}
    for i, t in enumerate(days):
        v = 100.0 + 0.001 * i
        if t >= d(2026, 10, 1):
            v += 10.0
        vals[t] = v
    store = FakeStore({"s:jump": vals})
    payload = se.episodes_payload(store, [_reg_entry("jump", "+", "D")],
                                  WEIGHTS, as_of=AS_OF)
    cell = payload["indicators"]["jump"]["episodes"]["Iran war"]["30d"]
    assert cell["status"] == "ok"
    assert cell["episode_max"] > 0
    assert cell["ratio_pct"] == 150.0
    assert "capped" in cell["note"]
    assert payload["header"]["faster_than_peak_30d"] >= 1


def test_negative_episode_peak_gives_undefined_ratio():
    # direction "-": series rises through the episode (moves AWAY from stress),
    # so the direction-adjusted "max" is negative -> ratio would mislead.
    start = d(2008, 1, 1)
    n = (AS_OF - start).days + 1
    days = drange(start, n)
    store = FakeStore({"s:calm": {t: 100.0 + 0.05 * i for i, t in enumerate(days)}})
    payload = se.episodes_payload(store, [_reg_entry("calm", "-", "D")],
                                  WEIGHTS, as_of=AS_OF)
    cell = payload["indicators"]["calm"]["episodes"]["GFC"]["30d"]
    assert cell["episode_max"] < 0  # honest: no stress-direction move
    assert cell["status"] == "undefined"
    assert cell["ratio_pct"] is None
    assert "not in the stress direction" in cell["note"]
    # must NOT count toward faster-than-peak (a faster calming move is not stress)
    assert payload["header"]["faster_than_peak_30d"] == 0


# ---------------------------------------------------------------------------
# 4. closest-analog label
# ---------------------------------------------------------------------------

def test_closest_analog_label():
    payload = se.episodes_payload(synth_store(), REG, WEIGHTS, as_of=AS_OF)
    ca = payload["closest_analog"]
    assert ca["label"] == "descriptive comparison, not a forecast"
    assert "descriptive comparison, not a forecast" in ca["note"]
    assert isinstance(ca["n_indicators"], int)
    assert len(ca["all"]) == 8
    # Q/E/weekly series are excluded from the analog (D-frequency only)
    assert ca["n_indicators"] <= 2  # aapl + bneg only


# ---------------------------------------------------------------------------
# 5. HAND-CHECKS: fully independent inline recomputation of 3 matrix cells
# ---------------------------------------------------------------------------

def _independent_max_cal(hist, start, end, n_days, sign):
    """Plain reimplementation: max direction-adjusted n-calendar-day change."""
    ds = sorted(hist)
    best = None
    for t in ds:
        if t < start or t > end:
            continue
        s_cands = [x for x in ds if x <= t - timedelta(days=n_days)]
        if not s_cands:
            continue
        s = s_cands[-1]
        c = (hist[t] - hist[s]) * sign
        if best is None or c > best:
            best = c
    return best


def _independent_max_step(hist, start, end, sign):
    ds = sorted(hist)
    best = None
    for j, t in enumerate(ds):
        if t < start or t > end or j < 1:
            continue
        c = (hist[t] - hist[ds[j - 1]]) * sign
        if best is None or c > best:
            best = c
    return best


def test_handcheck_cell_1_aapl_gfc_7d():
    # indicator=aapl (direction +), episode=GFC, window=7d
    store = synth_store()
    payload = se.episodes_payload(store, REG, WEIGHTS, as_of=AS_OF)
    got = payload["indicators"]["aapl"]["episodes"]["GFC"]["7d"]["episode_max"]
    expected = _independent_max_cal(store.data["s:aapl"],
                                    d(2008, 9, 1), d(2008, 12, 31), 7, +1.0)
    assert expected is not None and expected > 0
    assert got is not None
    assert abs(got - expected) < 1e-9, f"got={got} expected={expected}"


def test_handcheck_cell_2_bneg_covid_30d():
    # indicator=bneg (direction -), episode=Covid, window=30d
    # direction "-" flips the sign: max of (v(s) - v(t))
    store = synth_store()
    payload = se.episodes_payload(store, REG, WEIGHTS, as_of=AS_OF)
    got = payload["indicators"]["bneg"]["episodes"]["Covid"]["30d"]["episode_max"]
    expected = _independent_max_cal(store.data["s:bneg"],
                                    d(2020, 2, 20), d(2020, 3, 23), 30, -1.0)
    assert expected is not None
    assert got is not None
    assert abs(got - expected) < 1e-9, f"got={got} expected={expected}"


def test_handcheck_cell_3_wser_2007grind_30d_weekly_step():
    # indicator=wser (weekly): the 30d family uses the last-two-observation step
    store = synth_store()
    payload = se.episodes_payload(store, REG, WEIGHTS, as_of=AS_OF)
    cell = payload["indicators"]["wser"]["episodes"]["2007 grind"]["30d"]
    assert cell["status"] == "ok", cell
    assert "weekly step" in cell["note"]
    expected = _independent_max_step(store.data["s:wser"],
                                      d(2007, 6, 1), d(2007, 12, 31), +1.0)
    assert abs(cell["episode_max"] - expected) < 1e-9


def test_weekly_7d_is_na_with_reason():
    payload = se.episodes_payload(synth_store(), REG, WEIGHTS, as_of=AS_OF)
    cell = payload["indicators"]["wser"]["episodes"]["2007 grind"]["7d"]
    assert cell["status"] == "n/a"
    assert "cadence" in cell["note"]


def test_q_and_e_series_have_no_velocity():
    payload = se.episodes_payload(synth_store(), REG, WEIGHTS, as_of=AS_OF)
    for iid in ("qser", "eser"):
        ind = payload["indicators"][iid]
        assert ind["status"] == "no_velocity"
        for ep_cells in ind["episodes"].values():
            for w in ("7d", "30d"):
                assert ep_cells[w]["status"] == "no_velocity"


# ---------------------------------------------------------------------------
# 6. composite score series reuse the exact stress_score maths
# ---------------------------------------------------------------------------

def test_score_at_date_matches_score_series():
    store = synth_store()
    hist = store.data["s:aapl"]
    dates = sorted(hist)
    values = [hist[t] for t in dates]
    for probe in (d(2010, 6, 15), d(2020, 3, 23), d(2026, 10, 8)):
        got = se._score_at_date(dates, values, probe, "+")
        exp = ss.score_series({t.isoformat(): v for t, v in hist.items()},
                              "+", probe)["score"]
        assert got == exp, f"mismatch at {probe}: {got} vs {exp}"


def test_composite_and_gauge_present():
    payload = se.episodes_payload(synth_store(), REG, WEIGHTS, as_of=AS_OF)
    assert "Cat" in payload["composites"]
    comp = payload["composites"]["Cat"]
    assert comp["n_members"] == 4  # aapl+bneg+wser+eser (only Q excluded,
    # mirroring refresh_stress's composite membership)
    assert set(comp["episodes"]) == {e[0] for e in se.EPISODES}
    g = payload["gauge"]
    assert g["weights"] == WEIGHTS
    assert set(g["episodes"]) == {e[0] for e in se.EPISODES}


# ---------------------------------------------------------------------------
# 7. refresh_episodes caches the doc
# ---------------------------------------------------------------------------

def test_refresh_episodes_puts_doc():
    store = synth_store()
    payload = se.refresh_episodes(store, REG, WEIGHTS, as_of=AS_OF)
    assert "stress_episodes" in store.docs
    cached, source = store.docs["stress_episodes"]
    assert source == "stress-v2"
    assert cached is payload
    assert cached["as_of"] == AS_OF.isoformat()
    assert cached["header"]["n_indicators"] == len(REG)
