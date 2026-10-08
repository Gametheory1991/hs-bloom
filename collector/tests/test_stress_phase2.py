"""Tests for Stress Monitor v2 Phase 2 (fetchers/stress.py, WS4).

Hermetic: no network. Every new series_id is resolved against config.yaml or
a fetcher's source text — never invented. OFR mnemonics are checked against
the config entries that were themselves verified live against the hf/v1
metadata endpoint (2026-10-08); the test only asserts the registry agrees
with the config, not with the live API.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
import yaml

from collector.fetchers import stress as st
from collector.fetchers.ishares_etf import ETF_UNIVERSE
from collector.fetchers import trace_treasury, trace_monthly, finra_ids_star

REPO = Path(__file__).resolve().parents[2]
CFG = yaml.safe_load((REPO / "config.yaml").read_text())

CYCLE_IDS = {e["id"] for e in CFG["cycle_series"]}
MACRO_IDS = {e["id"] for e in CFG.get("series", [])}  # stored as macro:<id> by the FRED job
OFR_MNEMONICS = {e["mnemonic"] for e in CFG.get("ofr_series", []) if "mnemonic" in e}
OFR_MNEMONICS |= {e["ofr"] for e in CFG["cycle_series"] if "ofr" in e}
AUCTION_BUCKETS = set(CFG["auctions"]["buckets"])
AUCTION_METRICS = {"bid_to_cover", "indirect_pct", "direct_pct", "dealer_pct",
                   "offering", "high_yield"}
ETF_TICKERS = {t for t, _ in ETF_UNIVERSE}

# series the stress job itself fetches (MISSING_FRED + ACM + OFR FSI)
STRESS_FETCHED = set(st.MISSING_FRED.values()) | {"cycle:term-prem", "cycle:ofr-fsi"}

TRACE_KEYS = set(trace_treasury.ALL_SERIES) | set(trace_monthly.SERIES) | \
    set(trace_monthly.DERIVED_SERIES)
STAR_KEYS = set(finra_ids_star.SERIES_IDS)

VALID_CATEGORIES = {"Volatility", "Credit", "Official refs", "Funding",
                    "Treasury plumbing", "Equity internals", "Banks", "Global",
                    "Market activity", "Hedge fund leverage"}


def _resolves(sid: str) -> bool:
    """True when a raw store series_id exists in config or a fetcher."""
    if sid.startswith("cycle:"):
        key = sid[len("cycle:"):]
        if key in CYCLE_IDS or sid in STRESS_FETCHED:
            return True
        if re.fullmatch(r"etf-[A-Z]+-price", key) and key[4:-6] in ETF_TICKERS:
            return True  # ishares_etf writes cycle:etf-<T>-price
        return key in TRACE_KEYS or key in STAR_KEYS
    if sid.startswith("auction:"):
        _, bucket, metric = sid.split(":", 2)
        return bucket in AUCTION_BUCKETS and metric in AUCTION_METRICS
    if sid.startswith("ofr:"):
        return sid[len("ofr:"):] in OFR_MNEMONICS
    if sid.startswith("macro:"):
        return sid[len("macro:"):] in MACRO_IDS
    # internally computed engine series (risk:/movers:) and idx: index closes
    # are produced by the collector's own jobs, not by config entries
    if re.match(r"^(risk|movers|idx):", sid):
        return True
    # CFTC net-noncommercial series fetched by the cycle job's cftc: source key
    if sid.startswith("cftc:"):
        return True
    return False


def test_every_primary_series_id_resolves():
    bad = [i["id"] for i in st.INDICATORS
           if i["tag"] == "primary" and not _resolves(i["series_id"])]
    assert not bad, f"unresolvable primary series_ids: {bad}"


def test_every_derived_input_resolves():
    bad = []
    for i in st.INDICATORS:
        if i["tag"] not in {"derived", "proxy"}:
            continue
        assert i["series_id"] == f"stress:{i['id']}", i["id"]
        for inp in i["inputs"]:
            # inputs are raw store series (cycle:/idx:) or stress: outputs of
            # other derived rows — both must be real
            if inp.startswith("stress:"):
                ok = any(j["id"] == inp[len("stress:"):]
                         for j in st.INDICATORS)
            else:
                ok = _resolves(inp)
            if not ok:
                bad.append((i["id"], inp))
    assert not bad, f"derived rows with unresolvable inputs: {bad}"


def test_trace_star_keys_exist_in_fetchers():
    for rid, _nm, skey, _lag in st.TRACE_ROWS:
        assert skey in TRACE_KEYS, f"{rid}: cycle:{skey} not written by trace fetchers"
    for rid, _nm, skey in st.STAR_SECTIONS:
        assert skey in STAR_KEYS, f"{rid}: cycle:{skey} not written by finra_ids_star"


def test_etf_momentum_inputs_are_in_universe():
    for t in st.ETF_MOM_TICKERS:
        assert t in ETF_TICKERS, f"{t} not in ishares_etf ETF_UNIVERSE"
    for t in st.ETF_MOM_MISSING:
        assert t not in ETF_TICKERS, f"{t} unexpectedly in ETF_UNIVERSE"
    mom_ids = {i["id"] for i in st.INDICATORS if i["id"].startswith("etf_mom")}
    expected = {f"etf_mom{lag}d_{t.lower()}"
                for t in st.ETF_MOM_TICKERS + st.ETF_MOM_MISSING
                for lag in st.ETF_MOM_LAGS}
    assert mom_ids == expected


def test_auction_detail_rows_per_bucket():
    buckets = {b.lower().replace("-", "_") for b in st.AUCTION_BUCKETS}
    assert set(st.AUCTION_BUCKETS) == AUCTION_BUCKETS  # repo config is the source of truth
    ind = {i["id"] for i in st.INDICATORS if i["id"].startswith("auction_ind_")}
    drc = {i["id"] for i in st.INDICATORS if i["id"].startswith("auction_dir_")}
    dlr = {i["id"] for i in st.INDICATORS if i["id"].startswith("auction_dlr_")}
    tail = {i["id"] for i in st.INDICATORS if i["id"].startswith("auction_tail_")}
    want = {f"auction_{p}_{b}" for p in ("ind", "dir", "dlr", "tail") for b in buckets}
    assert (ind | drc | dlr | tail) == want
    # directions per spec 16.4
    dirs = {i["id"]: i["direction"] for i in st.INDICATORS}
    assert all(dirs[f"auction_ind_{b}"] == "-" for b in buckets)
    assert all(dirs[f"auction_dir_{b}"] == "±" for b in buckets)
    assert all(dirs[f"auction_dlr_{b}"] == "+" for b in buckets)


def test_put_call_four_series():
    pcs = {i["id"]: i["series_id"] for i in st.INDICATORS
           if i["id"].startswith("put_call")}
    assert pcs == {"put_call": "cycle:pc-spx",
                   "put_call_total": "cycle:pc-total",
                   "put_call_equity": "cycle:pc-equity",
                   "put_call_index": "cycle:pc-index"}
    assert all(i["direction"] == "+" for i in st.INDICATORS
               if i["id"].startswith("put_call"))


def test_hf_leverage_forty_rows_quarterly():
    hf = [i for i in st.INDICATORS if i["category"] == "Hedge fund leverage"]
    assert len(hf) == 40
    assert all(i["freq"] == "Q" for i in hf)
    assert all(i["tag"] == "primary" for i in hf)
    assert all(i["expected_lag_days"] == 120 for i in hf)  # LAG_Q
    ids = [i["id"] for i in hf]
    assert len(ids) == len(set(ids))
    # every cell names its metric + source table
    assert all("OFR Hedge Fund Monitor" in i["source"] for i in hf)
    assert all("mnemonic" in i["note"] and "dataset=fpf" in i["note"] for i in hf)


def test_activity_rows_are_derived_log_volume():
    act = [i for i in st.INDICATORS if i["category"] == "Market activity"
           and i["id"] != "offrun_share"]
    assert len(act) == 27  # 19 TRACE + 8 STAR
    assert all(i["tag"] == "derived" for i in act)
    assert all(i["direction"] == "±" for i in act)
    assert all(i["formula"] == "log(volume)" for i in act)
    assert all(len(i["inputs"]) == 1 for i in act)
    assert all(i["redistributable"] == "unknown" for i in act)  # 16.9


def test_redistributable_values_and_spot_checks():
    vals = {i["redistributable"] for i in st.INDICATORS}
    assert vals <= {"yes", "no", "unknown"}
    by_id = {i["id"]: i for i in st.INDICATORS}
    assert by_id["us10y"]["redistributable"] == "yes"      # FRED
    assert by_id["hf-gav"]["redistributable"] == "yes"     # OFR
    assert by_id["auction_btc_note_10y"]["redistributable"] == "yes"  # FiscalData
    assert by_id["trace_ust_total"]["redistributable"] == "unknown"  # TRACE
    assert by_id["star_tba"]["redistributable"] == "unknown"          # STAR
    assert by_id["put_call"]["redistributable"] == "unknown"          # Cboe
    assert by_id["vvix"]["redistributable"] == "unknown"             # Yahoo
    assert by_id["aaii_bull_bear"]["redistributable"] == "no"        # 16.9
    assert by_id["cdx_ig_spread"]["redistributable"] == "no"         # 16.9


def test_abs_velocity_only_on_rates_levels():
    flagged = {i["id"] for i in st.INDICATORS if i["abs_velocity"]}
    assert flagged == {"us2y", "us10y", "us30y", "sofr"}


def test_categories_and_weights_consistent():
    cats = {i["category"] for i in st.INDICATORS}
    assert cats <= VALID_CATEGORIES
    assert set(st.STRESS_WEIGHTS) == cats
    assert abs(sum(st.STRESS_WEIGHTS.values()) - 100.0) < 1e-9
    # the two new categories carry their 16.5 weights
    assert st.STRESS_WEIGHTS["Market activity"] == 10.0
    assert st.STRESS_WEIGHTS["Hedge fund leverage"] == 10.0


def test_no_duplicate_ids_and_freqs_valid():
    ids = [i["id"] for i in st.INDICATORS]
    assert len(ids) == len(set(ids))
    assert all(i["freq"] in {"D", "W", "M", "Q", "E"} for i in st.INDICATORS)


def test_materialize_log_and_momentum():
    from datetime import date, timedelta

    class FakeStore:
        def __init__(self, data):
            self.data = data
            self.written = {}

        def points(self, sid):
            return self.data.get(sid, {})

        def upsert_points(self, sid, pts):
            self.written[sid] = dict(pts)

    base = date(2026, 9, 1)
    days = [base + timedelta(days=k) for k in range(40)]
    store = FakeStore({
        "cycle:trace-ust-par": {d: 800000.0 + k * 1000 for k, d in enumerate(days)},
        "cycle:star-clo-par": {d: 12000.0 for d in days},
        "cycle:etf-GLD-price": {d: 200.0 * (1.001 ** k) for k, d in enumerate(days)},
        "cycle:trace-ust-offrun-par": {d: 300000.0 for d in days},
        "cycle:trace-ust-onrun-par": {d: 500000.0 for d in days},
    })
    res = st.materialize_derived(store)
    assert res["trace_ust_total"].startswith("ok")
    assert res["star_clo"].startswith("ok")
    assert res["etf_mom1d_gld"].startswith("ok")
    assert res["etf_mom5d_gld"].startswith("ok")
    # log pre-transform is exact
    got = list(store.written["stress:trace_ust_total"].values())[0]
    assert got == pytest.approx(math.log(800000.0))
    # momentum math: 1.1% daily compounding -> 1d ~0.1%, 5d ~(1.001^5-1)
    assert list(store.written["stress:etf_mom1d_gld"].values())[-1] == \
        pytest.approx(0.1, abs=1e-9)
    assert list(store.written["stress:etf_mom5d_gld"].values())[-1] == \
        pytest.approx((1.001 ** 5 - 1) * 100, abs=1e-9)
    # a missing raw series degrades per-indicator, never raises
    assert res["trace_corp"].startswith("skipped")
