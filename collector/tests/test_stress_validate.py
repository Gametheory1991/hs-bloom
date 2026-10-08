"""Tests for collector.stress_validate (Phase 2, WS2). Spec section 10.

Hand-checks one correlation and one lead/lag value against independent
inline recomputation: the OSM history is rebuilt here with the engine's
public score_series() (a different code path from the module's batched
_score_at loop) and Pearson r is computed with a hand-rolled loop (not the
module's statistics.correlation wrapper).

Run: PYTHONPATH=<repo>/collector/src python -m pytest collector/tests/test_stress_validate.py
"""

import math
from datetime import date, timedelta
from types import SimpleNamespace

from collector import stress_score
from collector import stress_validate as sv


class FakeStore:
    def __init__(self):
        self.docs = {}
        self._points = {}

    def doc(self, key):
        return self.docs.get(key)

    def put_doc(self, key, payload, source):
        self.docs[key] = SimpleNamespace(payload=payload,
                                         updated_at="2026-10-08T12:00:00Z",
                                         source=source)

    def points(self, series_id, since=None):
        return self._points.get(series_id, {})


# ---------------------------------------------------------------------------
# deterministic synthetic data
# ---------------------------------------------------------------------------

def _bizdays(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _rand(n: int, seed: int) -> list[float]:
    x, out = seed, []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append(x / 2147483648)
    return out


END = date(2026, 10, 7)
DATES = _bizdays(END, 300)


def _build_store(n_days=1400, seed=42):
    """Store with 10 indicators + two official indexes.

    1400 business days (~5.4Y) so the 252-observation scoring floor leaves
    enough valid history for 1Y and 5Y correlations. ofr_fsi is constructed
    to track indicator ind_0, so the 1Y correlation should be clearly
    positive.
    """
    store = FakeStore()
    dates = _bizdays(END, n_days)
    indicators = {}
    for i in range(10):
        iid = f"ind_{i}"
        cat = "Credit" if i % 2 == 0 else "Funding"
        direction = "-" if i == 9 else "+"
        r = _rand(n_days, seed + i)
        vals = [100 + 0.04 * t + (rv - 0.5) * 5 for t, rv in enumerate(r)]
        sid = f"cycle:{iid}"
        store._points[sid] = dict(zip(dates, vals))
        indicators[iid] = {"id": iid, "category": cat, "name": f"Ind {i}",
                           "direction": direction, "freq": "D",
                           "tag": "primary", "status": "ok", "score": 55.0}
    # Official indexes: ofr_fsi tracks the stress-aligned mean of all
    # indicators; stlfsi4 is independent noise.
    aligned = []
    for i in range(10):
        vals = [store._points[f"cycle:ind_{i}"][d] for d in dates]
        if i == 9:  # direction "-": mirror so higher = more stress
            vals = [200 - v for v in vals]
        aligned.append(vals)
    mean_vals = [sum(v) / 10 for v in zip(*aligned)]
    r1 = _rand(n_days, 7)
    store._points["cycle:ofr-fsi"] = dict(
        zip(dates, [0.5 * (m - 100) + (rv - 0.5) * 2
                    for m, rv in zip(mean_vals, r1)]))
    r2 = _rand(n_days, 99)
    store._points["cycle:stlfsi"] = dict(
        zip(dates, [(rv - 0.5) * 6 for rv in r2]))

    matrix = {"updated_at": "2026-10-08T12:00:00Z",
              "indicators": indicators,
              "composites": {
                  "Credit": {"score": 55.0, "status": "ok", "coverage": 1.0},
                  "Funding": {"score": 50.0, "status": "ok", "coverage": 1.0}},
              "overall": {"score": 53.0, "regime": "Elevated",
                          "as_of": END.isoformat(), "coverage": 1.0},
              "gaps": []}
    store.docs["stress_matrix"] = SimpleNamespace(
        payload=matrix, updated_at=matrix["updated_at"], source="stress-v2")
    return store, matrix


def _manual_pearson(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy)


def _independent_osm_history(store, matrix, weights, end):
    """Rebuild the OSM history via the engine's public score_series()
    (per-date scoring) instead of the module's batched _score_at loop."""
    indicators = matrix["indicators"]
    members = [(iid, ind["category"], ind["direction"])
               for iid, ind in indicators.items()
               if ind.get("tag") != "proxy" and ind.get("freq") != "Q"]
    sids = {}
    for iid, _c, _d in members:
        for cand in (f"cycle:{iid}", f"cycle:{iid.replace('_', '-')}",
                     f"stress:{iid}"):
            if store.points(cand):
                sids[iid] = cand
                break
    all_dates = sorted({d for iid in sids
                        for d in store.points(sids[iid]) if d <= end})
    sample = sv._sample_dates(all_dates, end, 5 * 365, 5)
    cats: dict[str, list] = {}
    for iid, cat, direction in members:
        if iid in sids:
            cats.setdefault(cat, []).append((iid, direction))
    hist = []
    for d in sample:
        comps = {}
        for cat, lst in cats.items():
            scores = []
            for iid, direction in lst:
                s = stress_score.score_series(store.points(sids[iid]),
                                              direction, d)
                scores.append(s["score"] if s["status"] == "ok" else None)
            comps[cat] = stress_score.composite(
                scores + [None] * max(0, len(lst) - len(scores)))
        g = stress_score.overall_gauge(comps, weights)
        if g["score"] is not None:
            hist.append((d, g["score"]))
    return hist


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_correlation_handcheck_1Y():
    store, matrix = _build_store()
    payload = sv.validation_payload(store)
    assert payload["status"] == "ok"
    assert payload["weights_source"] == "STRESS_WEIGHTS"

    reported = payload["correlations"]["ofr_fsi"]["1Y"]["r"]
    assert isinstance(reported, float)
    # ofr_fsi was built to track the indicators, so this must be positive
    # (percentile saturation on the OSM side attenuates the magnitude).
    assert reported > 0.15

    # Independent recomputation: per-date score_series + hand-rolled Pearson.
    from collector.fetchers.stress import STRESS_WEIGHTS
    total = sum(STRESS_WEIGHTS.values())
    weights = {c: v / total for c, v in STRESS_WEIGHTS.items()}
    osm = dict(_independent_osm_history(store, matrix, weights, END))
    off = store.points("cycle:ofr-fsi")
    start = END - timedelta(days=365)
    keys = sorted(d for d in set(osm) & set(off) if start <= d <= END)
    xs = [osm[k] for k in keys]
    ys = [off[k] for k in keys]
    expected = _manual_pearson(xs, ys)
    assert abs(reported - expected) < 1e-9, (reported, expected)


def test_lead_lag_handcheck_lag5():
    store, matrix = _build_store()
    payload = sv.validation_payload(store)
    reported = payload["lead_lag"]["ofr_fsi"]["level"]["5"]["r"]
    assert isinstance(reported, float)

    from collector.fetchers.stress import STRESS_WEIGHTS
    total = sum(STRESS_WEIGHTS.values())
    weights = {c: v / total for c, v in STRESS_WEIGHTS.items()}
    osm = dict(_independent_osm_history(store, matrix, weights, END))
    off = dict(store.points("cycle:ofr-fsi"))
    common = sorted(set(osm) & set(off))
    xs = [osm[d] for d in common[:-5]]
    ys = [off[d] for d in common[5:]]
    expected = _manual_pearson(xs, ys)
    assert abs(reported - expected) < 1e-9, (reported, expected)


def test_banner_section_shape():
    store, _matrix = _build_store()
    payload = sv.validation_payload(store)
    banner = payload["banner"]
    assert isinstance(banner["normalization"], str)
    assert len(banner["normalization"]) > 50
    assert banner["threshold"] == 20.0
    assert set(banner["details"]) == {"ofr_fsi", "stlfsi4"}
    for key, detail in banner["details"].items():
        assert detail["official_percentile"] is not None
        assert 0.0 <= detail["official_percentile"] <= 100.0


def test_sensitivity_sections_run():
    store, _matrix = _build_store()
    payload = sv.validation_payload(store)
    w = payload["sensitivity"]["weights"]
    assert isinstance(w["max_abs_change"], float) and w["max_abs_change"] >= 0.0
    assert w["per_category"], "expected per-category weight shifts"
    for cat, row in w["per_category"].items():
        assert "up5" in row and "down5" in row
    win = payload["sensitivity"]["windows"]
    assert win["n_indicators"] == 10
    assert isinstance(win["stable"], bool)
    assert -1.0 <= win["spearman_3d_vs_5d"] <= 1.0


def test_insufficient_history_reported_honestly():
    store, _matrix = _build_store(n_days=100)
    payload = sv.validation_payload(store)
    corr = payload["correlations"]["ofr_fsi"]
    assert corr["1Y"]["r"] == "insufficient history"
    assert corr["5Y"]["r"] == "insufficient history"
    assert payload["lead_lag"]["ofr_fsi"]["status"] == "insufficient history"
    assert payload["banner"]["details"]["ofr_fsi"]["official_percentile"] is None
    assert payload["false_alarm_rate"]["status"] == "insufficient history"


def test_false_alarm_rate_with_seeded_alerts():
    store, matrix = _build_store()
    # Seed a fast-move alert fired 20 days ago (evaluable: within 10-365d).
    fired = (END - timedelta(days=20)).isoformat()
    state = {"fast_move_alerts": [
        {"indicator": "ind_0", "category": "Credit",
         "fired_at": fired, "composite_at_fire": 55.0},
        # Too recent (<10d): not evaluable.
        {"indicator": "ind_1", "category": "Credit",
         "fired_at": (END - timedelta(days=3)).isoformat(),
         "composite_at_fire": 55.0},
    ]}
    store.docs["stress_alerts_state"] = SimpleNamespace(
        payload=state, updated_at="2026-10-08T12:00:00Z",
        source="stress-v2-alerts")
    payload = sv.validation_payload(store)
    fa = payload["false_alarm_rate"]
    assert fa["n_evaluable"] == 1, fa
    assert 0.0 <= fa["rate"] <= 1.0


def test_missing_matrix_no_crash():
    store = FakeStore()
    payload = sv.validation_payload(store)
    assert payload["status"] == "no_data"
