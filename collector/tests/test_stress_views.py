"""Unit tests for collector.fetchers.stress_views (Stress Monitor v2, Phase 2).

All data is synthetic. Tests marked HAND-CHECK reimplement the expected
value with plain inline math (independent of the module under test).
"""

import math
import random
from datetime import date, timedelta
from statistics import fmean, pstdev

import pytest

from collector.fetchers import stress_views as sv
from collector.store import Store


def d(i: int) -> date:
    return date(2023, 1, 2) + timedelta(days=i)


def _entry(iid, cat, sid, direction="+", freq="D", tier="core", tag="primary"):
    return {"id": iid, "category": cat, "name": iid, "unit": "u",
            "direction": direction, "freq": freq, "tier": tier,
            "series_id": sid, "source": "SYNTH", "tag": tag}


def _gen_ab(n=600):
    """Deterministic correlated pair: random-walk vol with b tracking a
    (seed 1 chosen so: a's last 5-obs change > 0, 1Y score corr > 0.7,
    and b's end-of-sample crash opens a >30pt gap)."""
    rng = random.Random(1)
    a, x = [], 0.0
    for _ in range(n):
        x += rng.gauss(0.05, 1.0)
        a.append(x)
    rng2 = random.Random(101)
    b = [0.8 * v + 20.0 + rng2.gauss(0, 0.4) for v in a]
    b[-1] = min(a) - 100.0  # crash below history -> b's score ~0
    return a, b


def _store_with(store, sid, vals):
    store.upsert_points(sid, [(d(i), v) for i, v in enumerate(vals)])


@pytest.fixture()
def reg():
    return [
        _entry("a_vol", "Volatility", "cycle:a"),
        _entry("b_vol", "Volatility", "cycle:b"),
        _entry("c_cred", "Credit", "cycle:c"),
        _entry("q_hf", "Hedge fund leverage", "cycle:q", freq="Q"),
        {"id": "na_x", "category": "Global", "name": "na_x", "unit": "u",
         "direction": "+", "freq": "D", "tier": "extended",
         "series_id": None, "source": "n/a", "tag": "n/a",
         "reason": "no free or reachable source"},
    ]


@pytest.fixture()
def store6(tmp_path, reg):
    """600-obs synthetic store: a/b correlated vol, c crashing credit."""
    store = Store(tmp_path / "t.db")
    n = 600
    a, b = _gen_ab(n)
    c = [float(i) for i in range(n - 1)] + [-1.0]  # latest is the window min
    q = [float(i) for i in range(40)]  # quarterly, short
    _store_with(store, "cycle:a", a)
    _store_with(store, "cycle:b", b)
    _store_with(store, "cycle:c", c)
    _store_with(store, "cycle:q", q)
    return store


# --------------------------------------------------------------------------
# HAND-CHECK 1: horizon 1M/5Y percentiles, independent inline math
# --------------------------------------------------------------------------
def test_handcheck_horizon_percentiles(store6, reg):
    ao = d(599)
    p = sv.horizon_payload(store6, reg, as_of=ao)
    row = p["indicators"]["c_cred"]
    # series c: 0..598 then -1. 1M window = last 21 obs = [579..598, -1];
    # latest -1 is the only obs <= -1 -> 100 * 1/21.
    assert row["n_1M"] == 21
    assert abs(row["pctl_1M"] - 100.0 / 21) < 1e-9
    # 5Y window = all 600 obs; still exactly one obs <= -1 -> 100/600.
    assert row["n_5Y"] == 600
    assert abs(row["pctl_5Y"] - 100.0 / 600) < 1e-9
    assert row["status"] == "ok"
    # n/a indicator carries its reason, never a made-up percentile
    na = p["indicators"]["na_x"]
    assert na["status"] == "no_data"
    assert "pctl_1M" not in na


# --------------------------------------------------------------------------
# HAND-CHECK 2: quadrant assignment, independent inline math
# --------------------------------------------------------------------------
def test_handcheck_quadrant_escalating(store6, reg):
    ao = d(599)
    p = sv.quadrant_payload(store6, reg, as_of=ao)
    pt = {q["id"]: q for q in p["points"]}["a_vol"]
    # INLINE: score = inclusive rank of a[599] vs last 1260 obs (all 600).
    a, _b = _gen_ab(600)
    n_le = sum(1 for x in a if x <= a[-1])
    expected_score = 100.0 * n_le / len(a)
    assert abs(pt["score"] - expected_score) < 1e-9
    # INLINE: 5d sigma = (a[599]-a[594]) / pstdev of trailing 5-changes.
    ch = [a[j] - a[j - 5] for j in range(max(5, 599 - 252 + 1), 600)]
    expected_sig = (a[599] - a[594]) / pstdev(ch)
    assert abs(pt["sigma5"] - expected_sig) < 1e-9
    assert pt["score"] >= 50 and pt["sigma5"] > 0
    assert pt["quadrant"] == "Escalating"
    assert p["counts"]["Escalating"] >= 1
    assert (p["counts"]["Emerging"] + p["counts"]["Escalating"]
            == p["emerging_escalating"])
    # quarterly series: score ok, velocity n/a with reason
    qq = {q["id"]: q for q in p["points"]}["q_hf"]
    assert qq["score"] is None  # 40 obs < 252 -> building
    assert qq["quadrant"] == "n/a"
    assert qq["reason"] is not None


# --------------------------------------------------------------------------
# HAND-CHECK 3: no look-ahead — an as_of-limited windowed stat ignores later
# observations that would change the value
# --------------------------------------------------------------------------
def test_no_lookahead_horizon(tmp_path, reg):
    store = Store(tmp_path / "t.db")
    # spike at obs 50; as_of=d(60) must not see anything past obs 60
    vals = [0.0] * 50 + [1000.0] + [0.0] * 249
    _store_with(store, "cycle:a", vals)
    _store_with(store, "cycle:b", [float(i) for i in range(300)])
    p = sv.horizon_payload(store, reg, as_of=d(60))
    row = p["indicators"]["a_vol"]
    # INLINE over obs 40..60 only: [0]*11 + [1000] + [0]*9 -> 20 of 21 <= 0
    assert abs(row["pctl_1M"] - 100.0 * 20 / 21) < 1e-9
    # if later observations leaked in, the 1M window would be all zeros -> 100
    assert row["pctl_1M"] != 100.0


# --------------------------------------------------------------------------
# building fallback: short history -> n/a, never invented
# --------------------------------------------------------------------------
def test_building_fallback(tmp_path, reg):
    store = Store(tmp_path / "t.db")
    _store_with(store, "cycle:a", [1.0, 2.0, 3.0, 4.0, 5.0])
    h = sv.horizon_payload(store, reg, as_of=d(4))
    row = h["indicators"]["a_vol"]
    assert row["status"] == "building"
    assert row["pctl_1M"] is None and row["n_1M"] == 5
    q = sv.quadrant_payload(store, reg, as_of=d(4))
    pt = {p["id"]: p for p in q["points"]}["a_vol"]
    assert pt["score"] is None and pt["quadrant"] == "n/a"
    assert "building" in (pt["reason"] or "")


# --------------------------------------------------------------------------
# divergence: correlated same-category pair with a current gap
# --------------------------------------------------------------------------
def test_divergence_pair_found(store6, reg):
    p = sv.divergence_payload(store6, reg, as_of=d(599))
    ids = {(x["a_id"], x["b_id"]) for x in p["pairs"]}
    assert ("a_vol", "b_vol") in ids or ("b_vol", "a_vol") in ids
    pair = next(x for x in p["pairs"]
                if {x["a_id"], x["b_id"]} == {"a_vol", "b_vol"})
    assert pair["category"] == "Volatility"
    assert pair["corr_1y"] > 0.7
    # INLINE: gap == |score_a - score_b| at the current week
    a, b = _gen_ab(600)

    def score(vals):
        w = vals[-1260:]
        n_le = sum(1 for x in w if x <= vals[-1])
        return 100.0 * n_le / len(w)

    expected_gap = abs(score(a) - score(b))
    assert abs(pair["gap"] - expected_gap) < 1e-9
    assert pair["gap"] > 30  # the crash opens a real gap
    assert pair["gap_pctl_1y"] is not None
    # biggest gaps first
    gaps = [x["gap"] for x in p["pairs"]]
    assert gaps == sorted(gaps, reverse=True)


# --------------------------------------------------------------------------
# contagion: matrix shape, diagonal, breadth line
# --------------------------------------------------------------------------
def test_contagion_shape(store6, reg):
    p = sv.contagion_payload(store6, reg, as_of=d(599))
    assert set(p["categories"]) == {"Volatility", "Credit"}
    for c1 in p["categories"]:
        assert p["matrix"][c1][c1] == 1.0
        for c2 in p["categories"]:
            v = p["matrix"][c1][c2]
            assert v is None or -1.0 <= v <= 1.0
    assert len(p["breadth"]["dates"]) == len(p["breadth"]["share_above_75"])
    assert len(p["breadth"]["dates"]) > 50
    assert all(0.0 <= s <= 1.0 for s in p["breadth"]["share_above_75"])


# --------------------------------------------------------------------------
# replay: overall + composites + verbatim episode registry
# --------------------------------------------------------------------------
def test_replay_structure(store6, reg):
    weights = {"Volatility": 12.0, "Credit": 15.0}
    p = sv.replay_payload(store6, reg, weights, as_of=d(599))
    n = len(p["dates"])
    assert n > 50
    assert len(p["overall"]) == n
    assert set(p["composites"]) == {"Volatility", "Credit"}
    assert all(len(v) == n for v in p["composites"].values())
    names = [e["name"] for e in p["episodes"]]
    assert names == ["2007 grind", "GFC", "Covid", "2022 hikes", "SVB",
                     "Tariffs", "Liberation Day", "Iran war"]
    svb = next(e for e in p["episodes"] if e["name"] == "SVB")
    assert (svb["start"], svb["finish"]) == ("2023-03-08", "2023-03-31")
    # overall is a weighted mean of the composites where both exist
    i = next(i for i, o in enumerate(p["overall"]) if o is not None)
    vols = p["composites"]["Volatility"][i]
    cred = p["composites"]["Credit"][i]
    if vols is not None and cred is not None:
        tw = 27.0
        assert abs(p["overall"][i] - (12.0 / tw * vols + 15.0 / tw * cred)) < 1e-9


# --------------------------------------------------------------------------
# refresh_stress_views: writes all five docs, empty store degrades gracefully
# --------------------------------------------------------------------------
def test_refresh_stress_views_writes_docs(store6, reg):
    weights = {"Volatility": 12.0, "Credit": 15.0}
    status = sv.refresh_stress_views(store6, reg, weights, as_of=d(599))
    assert set(status) == {"stress_horizon", "stress_contagion",
                           "stress_divergence", "stress_replay",
                           "stress_quadrant"}
    assert all(v == "ok" for v in status.values())
    for key in status:
        doc = store6.doc(key)
        assert doc is not None and doc.payload
        assert doc.payload["as_of"] == d(599).isoformat()
        assert "updated_at" in doc.payload


def test_refresh_stress_views_empty_store(tmp_path, reg):
    store = Store(tmp_path / "e.db")
    status = sv.refresh_stress_views(store, reg, {}, as_of=d(599))
    assert all(v == "ok" for v in status.values())
    h = store.doc("stress_horizon").payload
    assert h["indicators"]["a_vol"]["status"] in ("no_data", "building")
    c = store.doc("stress_contagion").payload
    assert c["categories"] == [] and c["matrix"] == {}
    q = store.doc("stress_quadrant").payload
    assert q["points"] == [] and q["emerging_escalating"] == 0
