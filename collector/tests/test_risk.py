"""Tests for the risk/prediction engine (compute-only; FakeStore, no HTTP)."""
import math
import random
from datetime import date, timedelta

from collector.fetchers.risk import (
    _logistic,
    _pct_rank,
    _sahm,
    auction_stress,
    basis_stress,
    build_summary,
    cta_crowdedness,
    recession_prob,
    refresh_risk,
    regime,
)


class FakeStore:
    def __init__(self):
        self._series = {}
        self.docs = {}

    def points(self, series_id, since=None):
        return dict(self._series.get(series_id, {}))

    def upsert_points(self, series_id, points):
        d = self._series.setdefault(series_id, {})
        for dt, v in points:
            d[dt] = v

    def put_doc(self, key, payload, source):
        self.docs[key] = payload

    def doc(self, key):
        return None


def daily(store, key, n, start_val, drift=0.0, noise=0.0, end=None, seed=42):
    """Synthetic daily series ending at `end` (default today)."""
    rng = random.Random(seed)
    end = end or date.today()
    v = start_val
    for i in range(n):
        d = end - timedelta(days=n - 1 - i)
        v += drift + rng.uniform(-noise, noise)
        store.upsert_points(key, [(d, v)])


def weekly(store, key, n, start_val, drift=0.0, noise=0.0, seed=42):
    rng = random.Random(seed)
    end = date.today()
    v = start_val
    for i in range(n):
        d = end - timedelta(weeks=n - 1 - i)
        v += drift + rng.uniform(-noise, noise)
        store.upsert_points(key, [(d, v)])


def monthly(store, key, n, start_val, drift=0.0, seed=42):
    rng = random.Random(seed)
    end = date.today().replace(day=1)
    v = start_val
    for i in range(n):  # i=0 is the oldest month
        total = (end.year * 12 + end.month - 1) - (n - 1 - i)
        y, m0 = divmod(total, 12)
        v += drift + rng.uniform(-0.02, 0.02)
        store.upsert_points(key, [(date(y, m0 + 1, 1), v)])


# --- helpers ---

def test_pct_rank():
    assert _pct_rank(5, [1, 2, 3, 4, 5]) == 100.0
    assert _pct_rank(1, [1, 2, 3, 4, 5]) == 20.0
    assert _pct_rank(3, [1, 2, 3, 4, 5]) == 60.0
    assert _pct_rank(0, [1, 2]) == 0.0
    assert _pct_rank(5, []) is None


def test_logistic_bounds():
    assert 0.0 < _logistic(0.4, 0.4, 12.0) < 100.0
    assert abs(_logistic(0.4, 0.4, 12.0) - 50.0) < 1e-9
    assert _logistic(10.0, 0.4, 12.0) > 99.9
    assert _logistic(-10.0, 0.4, 12.0) < 0.1


# --- 1. basis stress ---

def test_basis_stress_partial_inputs():
    s = FakeStore()
    daily(s, "cycle:sofr", 40, 4.3, drift=0.005, noise=0.02)   # rising spread
    daily(s, "cycle:iorb", 40, 4.4, noise=0.005)
    b = basis_stress(s)
    assert b["status"] == "ok"
    assert 0.0 <= b["value"] <= 100.0
    assert set(b["detail"]) == {"sofr_iorb_spread"}  # rrp/repo/fails absent
    # rising spread -> latest near top of its history -> high stress
    assert b["value"] > 80.0


def test_basis_stress_empty_store():
    b = basis_stress(FakeStore())
    assert b["value"] is None and b["status"] == "insufficient_data"


def test_basis_stress_rrp_drain():
    s = FakeStore()
    # RRP draining hard: level falls from 2000 to 200 over 100 days
    daily(s, "cycle:rrp-on", 100, 2000.0, drift=-18.0, noise=5.0)
    b = basis_stress(s)
    assert b["status"] == "ok"
    assert "rrp" in b["detail"]
    assert b["detail"]["rrp"] > 70.0  # drained facility = stressed


# --- 2. auction stress ---

def test_auction_stress_awaiting_feed():
    a = auction_stress(FakeStore())
    assert a["value"] is None and a["status"] == "awaiting_auction_feed"


def test_auction_stress_synthetic():
    s = FakeStore()
    # 10Y: bid-to-cover collapsing 2.6 -> 2.0, dealer takedown 12% -> 25%
    n = 12
    end = date.today()
    for i in range(n):
        d = end - timedelta(days=30 * (n - 1 - i))
        s.upsert_points("auction:Note-10Y:bid_to_cover",
                        [(d, 2.6 - i * 0.05)])
        s.upsert_points("auction:Note-10Y:dealer_pct",
                        [(d, 12.0 + i * 1.1)])
    a = auction_stress(s)
    assert a["status"] == "ok"
    assert 0.0 < a["value"] <= 100.0
    assert "Note-10Y" in a["detail"]
    assert a["value"] > 15.0  # weak demand + rising dealer share


def test_auction_stress_healthy():
    s = FakeStore()
    end = date.today()
    for i in range(12):
        d = end - timedelta(days=30 * (11 - i))
        s.upsert_points("auction:Note-10Y:bid_to_cover", [(d, 2.55)])
        s.upsert_points("auction:Note-10Y:dealer_pct", [(d, 11.0)])
    a = auction_stress(s)
    assert a["status"] == "ok"
    assert a["value"] < 20.0  # steady demand, low dealer share


# --- 3. CTA z-scores ---

def test_cta_insufficient_history():
    s = FakeStore()
    weekly(s, "cycle:cot-ust-10y", 10, -100000.0, noise=5000.0)
    c = cta_crowdedness(s)
    assert c["10y"]["z"] is None
    assert c["10y"]["status"] == "insufficient_history"
    assert c["10y"]["n"] == 10


def test_cta_prefers_tff_over_legacy():
    s = FakeStore()
    # legacy: flat around -50k (39 weekly obs) ; TFF: spiking short
    weekly(s, "cycle:cot-ust-10y", 39, -50000.0, noise=8000.0, seed=7)
    weekly(s, "cftc:tff:043602:lev_money", 39, -200000.0, noise=15000.0, seed=11)
    s.upsert_points("cftc:tff:043602:lev_money",
                    [(date.today() + timedelta(days=3), -900000.0)])
    c = cta_crowdedness(s)
    assert c["10y"]["source"] == "tff:lev_money"
    assert c["10y"]["status"] == "ok"
    assert c["10y"]["z"] < -2.0  # reflects the TFF spike, not legacy


def test_cta_falls_back_to_legacy():
    s = FakeStore()
    weekly(s, "cycle:cot-ust-10y", 30, -50000.0, noise=8000.0, seed=7)
    c = cta_crowdedness(s)
    assert c["10y"]["source"] == "legacy"
    assert c["10y"]["status"] == "ok"


def test_basis_stress_dealer_fails_exact_ids():
    s = FakeStore()
    # exact sibling ids: deliver + receive summed
    daily(s, "dealer:ust-fail-deliver", 60, 100.0, drift=2.0, noise=10.0, seed=21)
    daily(s, "dealer:ust-fail-receive", 60, 120.0, drift=2.5, noise=10.0, seed=22)
    b = basis_stress(s)
    assert b["status"] == "ok"
    assert "dealer_fails" in b["detail"]
    assert b["detail"]["dealer_fails"] > 80.0  # both legs rising


def test_cta_z_score_crowded_short():
    s = FakeStore()
    # 40 weeks around -50k, latest spikes to -400k -> deeply negative z
    weekly(s, "cycle:cot-ust-10y", 39, -50000.0, noise=8000.0, seed=7)
    s.upsert_points("cycle:cot-ust-10y",
                    [(date.today() + timedelta(days=3), -400000.0)])
    c = cta_crowdedness(s)
    assert c["10y"]["status"] == "ok"
    assert c["10y"]["z"] < -2.0
    assert c["10y"]["n"] == 40


# --- 4. regime ---

def test_regime_calm():
    s = FakeStore()
    daily(s, "cycle:vix", 100, 14.0, noise=1.0)
    daily(s, "cycle:hy-oas", 100, 3.2, noise=0.1)
    daily(s, "cycle:t10y2y", 100, 0.8, noise=0.05)
    daily(s, "cycle:tlt-shy", 100, 1.0, noise=0.005)
    r = regime(s)
    assert r["label"] == "CALM", r
    assert r["score"] <= 1


def test_regime_crisis():
    s = FakeStore()
    daily(s, "cycle:vix", 100, 38.0, noise=2.0)
    daily(s, "cycle:hy-oas", 100, 8.5, noise=0.3)
    daily(s, "cycle:t10y2y", 100, -1.2, noise=0.05)
    # tlt/shy collapsing 12% over 70d
    daily(s, "cycle:tlt-shy", 100, 1.0, drift=-0.0018, noise=0.004)
    r = regime(s)
    assert r["label"] == "CRISIS", r
    assert r["score"] >= 8


def test_regime_missing_inputs_ok():
    r = regime(FakeStore())
    assert r["label"] == "CALM" and r["score"] == 0 and r["inputs"] == []


# --- 5. recession probability ---

def test_sahm_trigger():
    s = FakeStore()
    # unemployment 3.5 -> 4.8 over 18 months: classic Sahm trigger
    monthly(s, "cycle:us-unemployment", 18, 3.5, drift=0.075, seed=3)
    from collector.fetchers.risk import _hist
    sahm = _sahm(_hist(s.points("cycle:us-unemployment")))
    assert sahm is not None and sahm >= 0.5, sahm


def test_recession_prob_full():
    s = FakeStore()
    monthly(s, "cycle:us-unemployment", 18, 3.5, drift=0.075, seed=3)
    daily(s, "cycle:t10y3m", 200, -1.2, noise=0.1)   # deeply inverted
    daily(s, "cycle:hy-oas", 200, 6.0, noise=0.2)     # wide spreads
    daily(s, "cycle:claims", 100, 230.0, drift=0.4, noise=8.0)  # rising
    r = recession_prob(s)
    assert r["status"] == "ok"
    assert 0.0 <= r["value"] <= 100.0
    assert r["value"] > 60.0  # everything firing
    assert "sahm_rule" in r["detail"]


def test_recession_prob_partial_renormalizes():
    s = FakeStore()
    monthly(s, "cycle:us-unemployment", 18, 3.6, drift=0.01, seed=5)
    r = recession_prob(s)  # only sahm available
    assert r["status"] == "ok"
    assert 0.0 <= r["value"] <= 100.0
    assert set(r["detail"]) >= {"sahm"}


def test_recession_prob_empty():
    r = recession_prob(FakeStore())
    assert r["value"] is None and r["status"] == "insufficient_data"


# --- 6. summary + refresh ---

async def test_refresh_risk_empty_store():
    s = FakeStore()
    src = await refresh_risk(s)
    assert src == "risk-engine"
    doc = s.docs["risk_summary"]
    assert doc["verdict"].startswith("NO DATA")
    assert doc["regime"] == "CALM"
    assert doc["components"]["auction_stress"]["status"] == "awaiting_auction_feed"
    assert doc["components"]["basis_stress"]["status"] == "insufficient_data"
    # no history points written when values are null
    assert "risk:basis_stress" not in s._series
    assert "risk:auction_stress" not in s._series


async def test_refresh_risk_full_store():
    s = FakeStore()
    daily(s, "cycle:sofr", 60, 4.30, drift=0.004, noise=0.015)
    daily(s, "cycle:iorb", 60, 4.40, noise=0.004)
    daily(s, "cycle:rrp-on", 100, 1500.0, drift=-10.0, noise=20.0)
    daily(s, "cycle:vix", 100, 22.0, noise=1.5)
    daily(s, "cycle:hy-oas", 200, 4.6, noise=0.15)
    daily(s, "cycle:t10y2y", 200, -0.5, noise=0.05)
    daily(s, "cycle:t10y3m", 200, -0.9, noise=0.08)
    daily(s, "cycle:tlt-shy", 100, 1.0, drift=-0.0006, noise=0.004)
    monthly(s, "cycle:us-unemployment", 18, 3.7, drift=0.03, seed=9)
    daily(s, "cycle:claims", 100, 225.0, drift=0.15, noise=6.0)
    weekly(s, "cycle:cot-ust-10y", 40, -120000.0, drift=-4000.0, noise=9000.0)
    weekly(s, "cycle:cot-ust-2y", 40, 50000.0, noise=9000.0)
    # repo borrowing via cycle mirror (ofr: key absent -> fallback path)
    s.upsert_points("cycle:hf-repo-borrow",
                    [(date(2024, 3, 31), 2.9e12), (date(2024, 6, 30), 3.1e12),
                     (date(2024, 9, 30), 3.2e12), (date(2024, 12, 31), 3.3e12),
                     (date(2025, 3, 31), 3.35e12), (date(2025, 6, 30), 3.38e12),
                     (date(2025, 9, 30), 3.42e12), (date(2025, 12, 31), 3.45e12)])

    src = await refresh_risk(s)
    assert src == "risk-engine"
    doc = s.docs["risk_summary"]
    assert doc["components"]["basis_stress"]["status"] == "ok"
    assert doc["components"]["cta_crowdedness"]["status"] == "ok"
    assert doc["components"]["cta_crowdedness"]["z_10y"] < -1.0
    assert doc["regime"] in ("LATE_CYCLE", "RISK_OFF", "STRESS")
    assert "risk:basis_stress" in s._series
    assert "risk:cta_z_10y" in s._series
    assert "risk:regime_score" in s._series
    assert "risk:recession_prob" in s._series
    assert isinstance(doc["verdict"], str) and ": " in doc["verdict"]


def test_build_summary_verdict_levels():
    s = FakeStore()
    doc = build_summary(s)
    assert doc["verdict"] == "NO DATA: risk engine has no inputs yet."
    assert set(doc["components"]) == {
        "basis_stress", "auction_stress", "cta_crowdedness", "recession_prob"}
    for comp in doc["components"].values():
        assert "read" in comp and isinstance(comp["read"], str)
