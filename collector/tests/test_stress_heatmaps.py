"""Stress Monitor 8 heatmaps: math correctness on synthetic data.

Network is fully faked (in-memory SQLite store). Tests cover:
- episode peak detection (7D/30D average levels)
- velocity computation (signed n-day rises)
- z-score series (velocity and level)
- graceful degradation on missing/short series (null cells, never crash)
- JSON serializability of the payload
- the scheduler job caches the doc
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from collector import stress_heatmaps as sh
from collector.store import Store

BASE = date(2023, 1, 1)
NDAYS = 1400  # ~3.8y: covers 2023-03 SVB through 2026-10 NOW


@pytest.fixture()
def store():
    s = Store(":memory:")
    return s


def _daily(s, sid, fn):
    s.upsert_points(sid, [(BASE + timedelta(days=i), fn(i)) for i in range(NDAYS)])


def _spike_store(s):
    # us10y climbs 3.5 -> ~4.9 with a +0.8 spike inside the Iran-war window
    def f(i):
        dt = BASE + timedelta(days=i)
        v = 3.5 + i * 0.001
        if date(2026, 3, 10) <= dt <= date(2026, 3, 20):
            v += 0.8
        return v
    _daily(s, "us10y", f)
    _daily(s, "breakeven-10y", lambda i: 2.3)
    _daily(s, "vix", lambda i: 18.0 + (i % 7))


def test_level_peak_captures_episode_spike(store):
    _spike_store(store)
    p = sh.build_matrices(store)
    iran = p["columns"].index("Iran war")
    now = p["columns"].index("NOW")
    # US 10Y is row 1
    lvl7 = p["matrices"]["lvl7"][1]
    assert lvl7[iran] is not None and lvl7[iran] > 5.0, lvl7[iran]
    assert lvl7[now] is not None
    assert abs(lvl7[now] - 4.899) < 0.01, lvl7[now]
    # text label carries native units
    assert p["texts"]["lvl7"][1][now].endswith("%")


def test_real_10y_synthesized(store):
    _spike_store(store)
    p = sh.build_matrices(store)
    now = p["columns"].index("NOW")
    real = p["matrices"]["lvl7"][3][now]  # Real 10Y row
    assert abs(real - (4.899 - 2.3)) < 0.01, real


def test_velocity_signed_and_zscore(store):
    _spike_store(store)
    p = sh.build_matrices(store)
    now = p["columns"].index("NOW")
    iran = p["columns"].index("Iran war")
    # Iran-war 7D velocity should reflect the +0.8 spike (in bp via vel_mult=100)
    v7 = p["matrices"]["vel7"][1][iran]
    assert v7 is not None and v7 > 50, v7
    # velocity z-score is finite where history is deep
    vz = p["matrices"]["velz7"][1][now]
    assert vz is None or abs(vz) < 10


def test_claims_excluded_from_velocity(store):
    _daily(store, "claims", lambda i: 220.0)
    p = sh.build_matrices(store)
    claims_row = [r["name"] for r in p["rows"]].index("Claims")
    assert all(v is None for v in p["matrices"]["vel7"][claims_row])
    assert all(v is None for v in p["matrices"]["vel30"][claims_row])
    # but levels still computed
    assert p["matrices"]["lvl7"][claims_row][-1] == 220.0


def test_missing_series_yields_nulls_not_crash(store):
    # empty store: every cell null, payload still valid JSON
    p = sh.build_matrices(store)
    assert p["asof"] is None
    for m in p["matrices"].values():
        assert all(v is None for row in m for v in row)
    json.dumps(p)


def test_short_history_no_zscores(store):
    # 30 points: levels OK, z-scores need 60+ trailing points -> null
    _daily(store, "vix", lambda i: 18.0)
    pts = list(store.points("vix").items())[:30]
    s2 = Store(":memory:")
    s2.upsert_points("vix", pts)
    p = sh.build_matrices(s2)
    vix_row = [r["name"] for r in p["rows"]].index("VIX")
    assert p["matrices"]["lvl7"][vix_row][-1] == 18.0
    assert p["matrices"]["lvlz1y"][vix_row][-1] is None


def test_columns_and_episodes_shape(store):
    _spike_store(store)
    p = sh.build_matrices(store)
    assert p["columns"] == [e[0] for e in sh.EPISODES] + ["NOW"]
    assert len(p["episodes"]) == 8
    for key in sh.MATRICES:
        m = p["matrices"][key]
        assert len(m) == 12 and all(len(r) == 9 for r in m), key


def test_refresh_job_caches_doc(store):
    _spike_store(store)
    import asyncio
    msg = asyncio.run(sh.refresh_stress_heatmaps(store))
    assert "stress_heatmaps" in msg
    doc = store.doc("stress_heatmaps")
    assert doc is not None and doc.source == "stress_heatmaps"
    assert doc.payload["asof"] is not None
