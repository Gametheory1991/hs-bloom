"""Tests for the Stress Monitor v2 Phase-1 Tier-1 registry (fetchers/stress.py).

Hermetic: no network. Registry schema checks + CSV parser checks with canned
payloads + gaps builder.
"""
from __future__ import annotations

import pytest

from collector.fetchers import stress as st


REQUIRED_KEYS = {"id", "category", "name", "unit", "direction", "freq", "tier",
                 "series_id", "source", "tag", "inputs", "formula", "reason",
                 "rung", "note", "composite_excluded", "expected_lag_days",
                 "max_age_days"}

VALID_CATEGORIES = {"Volatility", "Credit", "Official refs", "Funding",
                    "Treasury plumbing", "Equity internals", "Banks", "Global",
                    "Market activity", "Hedge fund leverage"}

NA_IDS = {"skew", "vix9d", "vix1d", "sox_spx", "cdx_ig_spread", "cdx_hy_price"}


def test_registry_schema():
    ids = [i["id"] for i in st.INDICATORS]
    assert len(ids) == len(set(ids)), "duplicate indicator ids"
    for i in st.INDICATORS:
        assert REQUIRED_KEYS <= set(i), f"{i['id']} missing keys"
        assert i["direction"] in {"+", "-", "±"}, i["id"]
        assert i["freq"] in {"D", "W", "M", "Q", "E"}, i["id"]
        assert i["tier"] in {"core", "extended"}, i["id"]
        assert i["tag"] in {"primary", "derived", "proxy", "manual", "n/a"}, i["id"]
        assert i["category"] in VALID_CATEGORIES, i["id"]
        if i["tag"] == "n/a":
            assert i["series_id"] is None, i["id"]
            assert i["reason"], f"{i['id']} n/a without reason"
            assert i["rung"], f"{i['id']} n/a without ladder rung"
        else:
            assert i["series_id"] or i["inputs"], f"{i['id']} live without series or inputs"
        if i["tag"] in {"derived", "proxy"}:
            assert i["formula"], f"{i['id']} derived/proxy without formula"


def test_na_set_matches_gaps():
    gaps = st.build_gaps()
    gap_ids = {g["id"] for g in gaps}
    assert NA_IDS == gap_ids
    for g in gaps:
        assert g["reason"] and g["rung"] and g["category"]


def test_derived_and_proxy_have_materialized_series_ids():
    for i in st.INDICATORS:
        if i["tag"] in {"derived", "proxy"}:
            assert i["series_id"] == f"stress:{i['id']}", i["id"]
            assert i["inputs"], f"{i['id']} derived without inputs"
    for i in st.INDICATORS:
        if i["tag"] == "n/a":
            assert i["series_id"] is None
            assert i["ladder_rung"] == i["rung"]


def test_stress_weights_cover_categories_and_sum_100():
    cats = {i["category"] for i in st.INDICATORS}
    assert set(st.STRESS_WEIGHTS) == cats
    assert abs(sum(st.STRESS_WEIGHTS.values()) - 100.0) < 1e-9
    assert st.STRESS_WEIGHTS["Official refs"] == 0.0  # excluded from gauge (3.3)


def test_materialize_derived_spreads_and_drawdown():
    from datetime import date, timedelta

    class FakeStore:
        def __init__(self, data):
            self.data = data
            self.written = {}
        def points(self, sid):
            return self.data.get(sid, {})
        def upsert_points(self, sid, pts):
            self.written[sid] = dict(pts)

    base = date(2026, 1, 5)
    days = [base + timedelta(days=k) for k in range(30)]
    store = FakeStore({
        "cycle:sofr": {d: 4.0 for d in days},
        "cycle:iorb": {d: 3.9 for d in days},
        "idx:SPX": {d: 6000.0 + k for k, d in enumerate(days)},
    })
    results = st.materialize_derived(store)
    assert results["sofr_iorb"].startswith("ok")
    pts = store.written["stress:sofr_iorb"]
    assert all(abs(v - 0.1) < 1e-9 for v in pts.values())
    # drawdown of a monotonically rising series is ~0
    assert results["spx_vs_high"].startswith("ok")
    assert max(store.written["stress:spx_vs_high"].values()) < 1e-9
    # realized vol needs 22 obs: 30 days -> 9 points
    assert results["spx_rv21"].startswith("ok")
    assert len(store.written["stress:spx_rv21"]) == 30 - 21
    # missing inputs degrade per-indicator, never raise
    assert results["vix_term"].startswith("skipped")


def test_core_covers_prompt_existing_13_plus_engine_12():
    core = {i["id"] for i in st.INDICATORS if i["tier"] == "core"}
    existing_13 = {"vix", "vix_term", "spx_rv21", "basis_stress", "auction_stress",
                   "on_rrp", "spx_vs_high", "tlt_mom60", "cta_crowd_10y",
                   "ew_vs_spx", "dispersion5", "bei_10y", "sofr_iorb"}
    engine_12 = {"us2y", "us10y", "us30y", "real_10y", "bei_10y", "sofr",
                 "ig_oas", "hy_oas", "ccc_oas", "vix", "nfci", "claims"}
    assert (existing_13 | engine_12) <= core


def test_official_refs_excluded_from_composites():
    refs = [i for i in st.INDICATORS if i["category"] == "Official refs"]
    assert len(refs) == 5
    assert all(i["composite_excluded"] for i in refs)
    assert not any(i["composite_excluded"] for i in st.INDICATORS
                   if i["category"] != "Official refs")


def test_market_activity_direction_two_sided():
    for i in st.INDICATORS:
        if i["category"] == "Market activity" and i["tag"] != "derived":
            assert i["direction"] == "±", i["id"]


def test_auction_bucket_count_matches_config():
    btc = [i for i in st.INDICATORS if i["id"].startswith("auction_btc_")]
    assert len(btc) == len(st.AUCTION_BUCKETS) == 13


def test_acm_csv_parses_term_premium():
    async def fake_get_text(url, params=None, headers=None):
        return ("RunDates,TERMYld,ACMFITYld,GSWYld\n"
                "31-Aug-2026,0.75936106118762,4.81713389489083,4.82610113666976\n"
                "30-Sep-2026,0.885008256163907,5.25896137383186,5.28356237559148\n")

    class FakeStore:
        def __init__(self):
            self.written = {}
        def upsert_points(self, sid, pts):
            self.written[sid] = list(pts)

    import asyncio
    store = FakeStore()
    n = asyncio.run(st._fetch_acm(store, fake_get_text))
    assert n == 2
    pts = dict(store.written["cycle:term-prem"])
    from datetime import date
    assert pts[date(2026, 9, 30)] == pytest.approx(0.885008256163907)


def test_ofr_fsi_csv_parses():
    async def fake_get_text(url, params=None, headers=None):
        return ("Date,OFR FSI,Credit\n"
                "2026-10-02,-2.088,-0.97\n"
                "2026-10-05,-2.109,-0.96\n")

    class FakeStore:
        def __init__(self):
            self.written = {}
        def upsert_points(self, sid, pts):
            self.written[sid] = list(pts)

    import asyncio
    store = FakeStore()
    n = asyncio.run(st._fetch_ofr_fsi(store, fake_get_text))
    assert n == 2
    pts = dict(store.written["cycle:ofr-fsi"])
    from datetime import date
    assert pts[date(2026, 10, 5)] == pytest.approx(-2.109)


def test_refresh_stress_sources_degrades_per_source():
    async def ok_text(url, params=None, headers=None):
        # minimal FRED observations JSON for the fred.fetch_series path
        if "fred" in url:
            return ('{"observations":[{"date":"2026-10-01","value":"1.5"}]}')
        if "acmPlot" in url:
            return "RunDates,TERMYld\n30-Sep-2026,0.88\n"
        if "fsi.csv" in url:
            raise RuntimeError("boom")
        raise AssertionError(url)

    class FakeStore:
        def __init__(self):
            self.written = {}
        def upsert_points(self, sid, pts):
            self.written[sid] = list(pts)

    import asyncio
    store = FakeStore()
    results = asyncio.run(st.refresh_stress_sources(store, None, "KEY", get_text=ok_text))
    assert len(results) == len(st.MISSING_FRED) + 2
    assert results["cycle:ofr-fsi"].startswith("error:")
    assert results["cycle:term-prem"].startswith("ok")
    assert results["cycle:vix3m"].startswith("ok")
