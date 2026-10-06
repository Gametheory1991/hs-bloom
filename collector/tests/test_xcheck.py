"""UST positioning cross-check: OFR (quarterly) vs CFTC TFF (weekly) vs OI.

Triple-confirm scoring: each week counts 0-3 legs confirming an unwind
(leg 1 = OFR quarterly short-UST shrinking, leg 2 = CFTC 13-week net rising
toward zero, leg 3 = aggregate OI falling over ~13 weeks).

Pure-derivation tests use fabricated fixtures (never real fund data).
"""
from __future__ import annotations

from datetime import date, timedelta

from collector.fetchers.risk import (
    derive_ust_agreement,
    derive_ust_nets,
    derive_ust_oi,
    refresh_ust_xcheck,
)
from collector.store import Store

Q = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
     date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
# shorts shrinking two quarters running (the "unwinding" shape), $ values
SHORT_DOWN = [1.70e12, 1.68e12, 1.66e12, 1.645e12, 1.568e12, 1.498e12]
SHORT_UP = [1.40e12, 1.45e12, 1.50e12, 1.56e12, 1.62e12, 1.70e12]  # rebuilding
LONG_FLAT = [2.40e12] * 6


def _weeks(n, start=date(2026, 5, 12)):
    return [start + timedelta(weeks=i) for i in range(n)]


def _cftc_by_tenor(agg):
    """Split each week's aggregate evenly across the four tenors."""
    weeks = _weeks(len(agg))
    out = {t: {} for t in ("2y", "5y", "10y", "30y")}
    for w, a in zip(weeks, agg):
        for t in out:
            out[t][w] = a / 4.0
    return out


def _linspace(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


# ---------------------------------------------------------------- nets ---

def test_derive_ust_nets_math_and_date_alignment():
    long_pts = {Q[0]: 2.4e12, Q[1]: 2.5e12, date(2026, 9, 30): 2.6e12}  # extra date
    short_pts = {Q[0]: 1.7e12, Q[1]: 1.6e12}
    weeks = _weeks(4)
    cftc = {t: {w: -100_000.0 for w in weeks} for t in ("2y", "5y", "10y", "30y")}
    # one tenor missing a week -> that week must drop out of the aggregate
    del cftc["30y"][weeks[1]]
    net_ofr, net_cftc = derive_ust_nets(long_pts, short_pts, cftc)
    assert net_ofr == [(Q[0], 0.7e12), (Q[1], 0.9e12)]  # no 2026-09-30 row
    assert [d for d, _ in net_cftc] == [weeks[0], weeks[2], weeks[3]]
    assert all(v == -400_000.0 for _, v in net_cftc)


def test_derive_ust_nets_empty_inputs():
    assert derive_ust_nets({}, {}, {}) == ([], [])
    assert derive_ust_nets({Q[0]: 1.0}, {}, {"2y": {}}) == ([], [])


# ------------------------------------------------------------- agreement ---

def _agree_case(short_vals, agg_start, agg_end, oi_start, oi_end, n_weeks=20):
    short_pts = dict(zip(Q, short_vals))
    long_pts = dict(zip(Q, LONG_FLAT))
    agg = _linspace(agg_start, agg_end, n_weeks)
    oi_agg = _linspace(oi_start, oi_end, n_weeks)
    net_ofr, net_cftc = derive_ust_nets(long_pts, short_pts, _cftc_by_tenor(agg))
    net_oi = derive_ust_oi(_cftc_by_tenor(oi_agg))
    return derive_ust_agreement(net_ofr, short_pts, net_cftc, net_oi)


def test_agreement_all_three_legs_confirm_scores_three():
    # OFR shorts shrinking + CFTC net rising + OI falling = 3/3 unwind
    out = _agree_case(SHORT_DOWN, -6_900_000.0, -5_400_000.0,
                      14_000_000.0, 12_000_000.0)
    assert out, "expected agreement history"
    assert all(v == 3.0 for _, v in out)
    # 20 weeks - 13-week lookback = 7 scored weeks
    assert len(out) == 7


def test_agreement_two_of_three_legs_scores_two():
    # OFR + CFTC confirm, OI flat (under eps) -> 2/3
    out = _agree_case(SHORT_DOWN, -6_900_000.0, -5_400_000.0,
                      13_000_000.0, 13_000_010.0)
    assert out
    assert all(v == 2.0 for _, v in out)


def test_agreement_none_confirm_scores_zero():
    # all three rebuilding/flat -> 0/3
    out = _agree_case(SHORT_UP, -5_400_000.0, -6_900_000.0,
                      12_000_000.0, 14_000_000.0)
    assert out
    assert all(v == 0.0 for _, v in out)


def test_agreement_flat_within_epsilon_scores_zero():
    # QoQ and 13-week moves under the noise floors -> flat
    out = _agree_case([1.7000e12, 1.7000e12, 1.7000e12, 1.7000e12,
                       1.7000001e12, 1.7000002e12],
                      -6_000_000.0, -6_000_500.0,
                      13_000_000.0, 13_000_010.0)
    assert out
    assert all(v == 0.0 for _, v in out)


def test_derive_ust_oi_aggregation_and_alignment():
    weeks = _weeks(4)
    oi = {t: {w: 3_000_000.0 for w in weeks} for t in ("2y", "5y", "10y", "30y")}
    del oi["5y"][weeks[2]]  # missing tenor drops the week
    out = derive_ust_oi(oi)
    assert [d for d, _ in out] == [weeks[0], weeks[1], weeks[3]]
    assert all(v == 12_000_000.0 for _, v in out)
    assert derive_ust_oi({}) == []


def test_agreement_insufficient_history_yields_nothing():
    # fewer than 14 CFTC weeks
    out = _agree_case(SHORT_DOWN, -6_900_000.0, -5_400_000.0,
                      14_000_000.0, 12_000_000.0, n_weeks=10)
    assert out == []
    # fewer than 2 OFR quarters
    short_pts = {Q[-1]: 1.498e12}
    long_pts = {Q[-1]: 2.362e12}
    agg = _linspace(-6_900_000.0, -5_400_000.0, 20)
    oi_agg = _linspace(14_000_000.0, 12_000_000.0, 20)
    net_ofr, net_cftc = derive_ust_nets(long_pts, short_pts, _cftc_by_tenor(agg))
    net_oi = derive_ust_oi(_cftc_by_tenor(oi_agg))
    assert derive_ust_agreement(net_ofr, short_pts, net_cftc, net_oi) == []
    # missing OI entirely
    net_ofr2, net_cftc2 = derive_ust_nets(
        dict(zip(Q, LONG_FLAT)), dict(zip(Q, SHORT_DOWN)), _cftc_by_tenor(agg))
    assert derive_ust_agreement(net_ofr2, dict(zip(Q, SHORT_DOWN)),
                                net_cftc2, []) == []


# --------------------------------------------------------------- job glue ---

def _seed(store, short_vals, agg_start, agg_end, oi_start, oi_end, n_weeks=20):
    store.upsert_points("ofr:FPF-ASSETCLASS_LTREASURY_SUM",
                        list(zip(Q, LONG_FLAT)))
    store.upsert_points("ofr:FPF-ASSETCLASS_STREASURY_SUM",
                        list(zip(Q, short_vals)))
    codes = {"2y": "042601", "5y": "044601", "10y": "043602", "30y": "020601"}
    agg = _linspace(agg_start, agg_end, n_weeks)
    oi_agg = _linspace(oi_start, oi_end, n_weeks)
    cftc = _cftc_by_tenor(agg)
    oi = _cftc_by_tenor(oi_agg)
    for t, code in codes.items():
        store.upsert_points(f"cftc:tff:{code}:lev_money",
                            sorted(cftc[t].items()))
        store.upsert_points(f"cycle:oi-ust-{t}", sorted(oi[t].items()))


def test_refresh_ust_xcheck_writes_cycle_keys(tmp_path):
    store = Store(tmp_path / "t.db")
    _seed(store, SHORT_DOWN, -6_900_000.0, -5_400_000.0,
          14_000_000.0, 12_000_000.0)
    refresh_ust_xcheck(store)  # must not raise
    net_ofr = store.points("cycle:xcheck-ust-net-ofr")
    net_cftc = store.points("cycle:xcheck-ust-net-cftc")
    net_oi = store.points("cycle:xcheck-ust-oi")
    agree = store.points("cycle:xcheck-ust-agree")
    assert net_ofr[Q[-1]] == LONG_FLAT[-1] - SHORT_DOWN[-1]
    assert len(net_ofr) == 6
    assert len(net_cftc) == 20
    assert net_cftc[max(net_cftc)] == -5_400_000.0
    assert net_oi[max(net_oi)] == 12_000_000.0
    assert agree and all(v == 3.0 for v in agree.values())
    # idempotent: second run changes nothing
    refresh_ust_xcheck(store)
    assert len(store.points("cycle:xcheck-ust-net-ofr")) == 6


def test_refresh_ust_xcheck_empty_store_is_silent(tmp_path):
    store = Store(tmp_path / "t.db")
    refresh_ust_xcheck(store)  # missing inputs: no points, no raise
    assert store.points("cycle:xcheck-ust-net-ofr") == {}
    assert store.points("cycle:xcheck-ust-net-cftc") == {}
    assert store.points("cycle:xcheck-ust-oi") == {}
    assert store.points("cycle:xcheck-ust-agree") == {}
