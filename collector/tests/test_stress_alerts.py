"""Tests for collector.stress_alerts (Phase 2, WS2). Spec section 9.

Hand-checks transition rules with synthetic cached docs via a FakeStore.
Run: PYTHONPATH=<repo>/collector/src python -m pytest collector/tests/test_stress_alerts.py
"""

from types import SimpleNamespace

from collector import stress_alerts


class FakeStore:
    """Minimal store double: doc()/put_doc() only (alerts never touch points)."""

    def __init__(self):
        self.docs = {}

    def doc(self, key):
        return self.docs.get(key)

    def put_doc(self, key, payload, source):
        self.docs[key] = SimpleNamespace(payload=payload,
                                         updated_at="2026-10-08T12:00:00Z",
                                         source=source)


def _ind(iid, name, category, score, status="ok"):
    return {"id": iid, "category": category, "name": name,
            "score": score, "percentile": score, "status": status}


def _vel(sigma_d1=0.5, sigma_d5=0.4, pct_d5=50.0, accel=None, status="ok"):
    return {"raw": {"d1": 0.1, "d5": 0.2, "d20": None, "d60": None},
            "sigma": {"d1": sigma_d1, "d5": sigma_d5,
                      "d20": None, "d60": None},
            "pctile": {"d1": 40.0, "d5": pct_d5, "d20": None, "d60": None},
            "accel": accel, "status": status}


def _docs(indicators, composites, vel_inds, vel_comps=None,
          breadth_share=0.10, speed="Stable", as_of="2026-10-07"):
    matrix = {"updated_at": "2026-10-08T12:00:00Z",
              "indicators": indicators,
              "composites": composites,
              "overall": {"score": 55.0, "regime": "Elevated",
                          "as_of": as_of, "coverage": 1.0},
              "gaps": []}
    velocity = {"updated_at": "2026-10-08T12:00:00Z",
                "indicators": vel_inds,
                "composites": vel_comps or {},
                "overall": {"d1": 0.1, "d5": 0.4, "d20": None,
                            "sigma5": 0.3, "accel": 0.0,
                            "speed_label": speed},
                "breadth": {"share_above_75": breadth_share,
                            "share_fast_90": 0.05, "n": 10}}
    return matrix, velocity


def _run(indicators, composites, vel_inds, **kw):
    store = FakeStore()
    matrix, velocity = _docs(indicators, composites, vel_inds, **kw)
    store.docs["stress_matrix"] = SimpleNamespace(
        payload=matrix, updated_at=matrix["updated_at"], source="stress-v2")
    store.docs["stress_velocity"] = SimpleNamespace(
        payload=velocity, updated_at=velocity["updated_at"], source="stress-v2")
    return store, stress_alerts.evaluate_alerts(store)


def _quiet(iid="abc", name="ABC Test", cat="Credit", score=60.0):
    return _ind(iid, name, cat, score), _vel()


# ---------------------------------------------------------------------------
# rule (1): new stress, first time in 4 weeks
# ---------------------------------------------------------------------------

def test_new_stress_fires_once_then_silent():
    ind, vel = _quiet(score=95.0)
    store, res = _run({"abc": ind},
                      {"Credit": {"score": 60.0, "status": "ok"}},
                      {"abc": vel})
    rules = [a["rule"] for a in res["alerts"]]
    assert rules == ["new_stress"], rules
    a = res["alerts"][0]
    assert a["message"] == "New stress in ABC Test"
    assert a["severity"] == "critical"

    # Second run, same docs: no duplicate fire.
    res2 = stress_alerts.evaluate_alerts(store)
    assert res2["alerts"] == []
    # State recorded the firing date.
    state = store.docs["stress_alerts_state"].payload
    assert state["red_last_fired"]["abc"] == "2026-10-07"


def test_new_stress_rearms_after_4_weeks():
    ind, vel = _quiet(score=95.0)
    store, _res = _run({"abc": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"abc": vel})
    state = store.docs["stress_alerts_state"].payload
    # Pretend the last firing was 29 days before the matrix as_of.
    state["red_last_fired"]["abc"] = "2026-09-08"
    res = stress_alerts.evaluate_alerts(store)
    assert [a["rule"] for a in res["alerts"]] == ["new_stress"]


def test_stale_indicator_never_fires():
    ind = _ind("stale1", "Stale One", "Credit", 95.0, status="stale")
    vel = _vel(status="stale")
    _store, res = _run({"stale1": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"stale1": vel})
    assert res["alerts"] == []


def test_building_indicator_never_fires():
    ind = _ind("bld1", "Building One", "Credit", None, status="building")
    _store, res = _run({"bld1": ind}, {}, {"bld1": _vel(status="no_velocity")})
    assert res["alerts"] == []


# ---------------------------------------------------------------------------
# rule (2): category composite crosses 70
# ---------------------------------------------------------------------------

def test_category_high_fires_on_cross_only():
    ind, vel = _quiet()
    comps = {"Credit": {"score": 65.0, "status": "ok", "coverage": 1.0}}
    store = FakeStore()
    m, v = _docs({"abc": ind}, comps, {"abc": vel})
    store.docs["stress_matrix"] = SimpleNamespace(
        payload=m, updated_at=m["updated_at"], source="stress-v2")
    store.docs["stress_velocity"] = SimpleNamespace(
        payload=v, updated_at=v["updated_at"], source="stress-v2")
    res1 = stress_alerts.evaluate_alerts(store)
    assert "category_high" not in [a["rule"] for a in res1["alerts"]]

    m["composites"]["Credit"]["score"] = 71.0
    res2 = stress_alerts.evaluate_alerts(store)
    fired = [a for a in res2["alerts"] if a["rule"] == "category_high"]
    assert len(fired) == 1
    assert fired[0]["message"] == "Credit High"

    # Still above 70 next run: no re-fire.
    res3 = stress_alerts.evaluate_alerts(store)
    assert "category_high" not in [a["rule"] for a in res3["alerts"]]


# ---------------------------------------------------------------------------
# rule (4): fast move
# ---------------------------------------------------------------------------

def test_fast_move_fires_on_sigma_and_rarms():
    ind = _ind("fm", "Fast Mover", "Credit", 80.0)
    vel = _vel(sigma_d1=3.2, sigma_d5=1.0)
    store, res = _run({"fm": ind},
                      {"Credit": {"score": 60.0, "status": "ok"}},
                      {"fm": vel})
    fired = [a for a in res["alerts"] if a["rule"] == "fast_move"]
    assert len(fired) == 1
    assert "1d +3.2" in fired[0]["detail"]

    # Ongoing condition: no duplicate.
    res2 = stress_alerts.evaluate_alerts(store)
    assert "fast_move" not in [a["rule"] for a in res2["alerts"]]

    # Logged for the false-alarm check, with the at-fire composite.
    log = store.docs["stress_alerts_state"].payload["fast_move_alerts"]
    assert log[-1]["indicator"] == "fm"
    assert log[-1]["composite_at_fire"] == 60.0


def test_fast_move_5d_leg():
    ind = _ind("fm5", "Slow Burn", "Credit", 80.0)
    vel = _vel(sigma_d1=1.0, sigma_d5=2.7)
    _store, res = _run({"fm5": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"fm5": vel})
    assert [a["rule"] for a in res["alerts"]] == ["fast_move"]


# ---------------------------------------------------------------------------
# rule (3): breadth, rule (5): cluster, rule (8): speed
# ---------------------------------------------------------------------------

def test_breadth_crosses_30():
    ind, vel = _quiet()
    _store, res = _run({"abc": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"abc": vel}, breadth_share=0.35)
    fired = [a for a in res["alerts"] if a["rule"] == "breadth"]
    assert len(fired) == 1
    assert fired[0]["message"] == "Stress broadening"


def test_velocity_cluster():
    inds, vels = {}, {}
    for n in range(5):
        iid = f"c{n}"
        inds[iid] = _ind(iid, f"C {n}", "Credit", 60.0)
        vels[iid] = _vel(pct_d5=95.0)
    _store, res = _run(inds, {"Credit": {"score": 60.0, "status": "ok"}},
                       vels)
    fired = [a for a in res["alerts"] if a["rule"] == "velocity_cluster"]
    assert len(fired) == 1
    assert fired[0]["message"] == "Velocity cluster in Credit"


def test_velocity_cluster_needs_five():
    inds, vels = {}, {}
    for n in range(4):
        iid = f"c{n}"
        inds[iid] = _ind(iid, f"C {n}", "Credit", 60.0)
        vels[iid] = _vel(pct_d5=95.0)
    _store, res = _run(inds, {"Credit": {"score": 60.0, "status": "ok"}},
                       vels)
    assert "velocity_cluster" not in [a["rule"] for a in res["alerts"]]


def test_speed_transition_and_rearm():
    ind, vel = _quiet()
    store, res = _run({"abc": ind},
                      {"Credit": {"score": 60.0, "status": "ok"}},
                      {"abc": vel}, speed="Rapid")
    fired = [a for a in res["alerts"] if a["rule"] == "speed"]
    assert len(fired) == 1 and fired[0]["message"] == "Speed: Rapid"

    res2 = stress_alerts.evaluate_alerts(store)
    assert "speed" not in [a["rule"] for a in res2["alerts"]]

    # Drops out of Rapid, then returns -> fires again (re-armed).
    m = store.docs["stress_matrix"].payload
    v = store.docs["stress_velocity"].payload
    v["overall"]["speed_label"] = "Stable"
    stress_alerts.evaluate_alerts(store)
    v["overall"]["speed_label"] = "Violent"
    res4 = stress_alerts.evaluate_alerts(store)
    fired4 = [a for a in res4["alerts"] if a["rule"] == "speed"]
    assert len(fired4) == 1 and fired4[0]["severity"] == "critical"


# ---------------------------------------------------------------------------
# rule (6): acceleration warning, rule (7): emerging stress
# ---------------------------------------------------------------------------

def test_acceleration_warning_needs_three_days():
    ind = _ind("ac", "Accel", "Credit", 60.0)
    vel = _vel(accel=0.5)
    comps = {"Credit": {"score": 60.0, "status": "ok", "coverage": 1.0}}
    vel_comps = {"Credit": {"d1": 0.5, "d5": 1.0, "d20": None,
                            "sigma5": 0.4, "accel": 0.5}}
    store = FakeStore()
    m, v = _docs({"ac": ind}, comps, {"ac": vel}, vel_comps=vel_comps)
    store.docs["stress_matrix"] = SimpleNamespace(
        payload=m, updated_at=m["updated_at"], source="stress-v2")
    store.docs["stress_velocity"] = SimpleNamespace(
        payload=v, updated_at=v["updated_at"], source="stress-v2")
    dates = ["2026-10-05", "2026-10-06", "2026-10-07"]
    fired_runs = []
    for d in dates:
        m["overall"]["as_of"] = d
        res = stress_alerts.evaluate_alerts(store)
        fired_runs.append("acceleration" in [a["rule"] for a in res["alerts"]])
    assert fired_runs == [False, False, True], fired_runs
    # Fourth day, streak alive: no duplicate.
    m["overall"]["as_of"] = "2026-10-08"
    res = stress_alerts.evaluate_alerts(store)
    assert "acceleration" not in [a["rule"] for a in res["alerts"]]


def test_emerging_stress():
    ind = _ind("em", "Emerging", "Credit", 40.0)
    vel = _vel(sigma_d5=1.5, pct_d5=96.0)
    _store, res = _run({"em": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"em": vel})
    fired = [a for a in res["alerts"] if a["rule"] == "emerging_stress"]
    assert len(fired) == 1
    assert fired[0]["message"] == "Emerging stress in Emerging"


def test_emerging_stress_needs_stress_direction():
    # Fast move *away* from stress (negative sigma) must not fire.
    ind = _ind("em2", "Calming", "Credit", 40.0)
    vel = _vel(sigma_d5=-1.5, pct_d5=96.0)
    _store, res = _run({"em2": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"em2": vel})
    assert "emerging_stress" not in [a["rule"] for a in res["alerts"]]


# ---------------------------------------------------------------------------
# payload shape / missing docs
# ---------------------------------------------------------------------------

def test_alerts_payload_shape():
    ind, vel = _quiet(score=95.0)
    store, _res = _run({"abc": ind},
                       {"Credit": {"score": 60.0, "status": "ok"}},
                       {"abc": vel})
    payload = stress_alerts.alerts_payload(store)
    assert payload["as_of"] == "2026-10-07"
    assert payload["counts"]["total"] >= 0
    assert "note" in payload


def test_missing_docs_no_crash():
    store = FakeStore()
    res = stress_alerts.evaluate_alerts(store)
    assert res["status"] == "no_data"
    assert res["alerts"] == []
    assert "stress_alerts_state" not in store.docs
