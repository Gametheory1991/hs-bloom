"""Risk/prediction engine: compute-only composites from stored history.

No HTTP. Reads series from the store, writes five composite signals plus a
`risk_summary` doc consumed by /api/insights, and (via refresh_ust_xcheck) the
three UST positioning cross-check series below. Every input is optional:
missing inputs are skipped (never zero-filled) and each composite degrades
to a documented status instead of crashing.

METHODOLOGY
-----------
All composites are 0-100 (higher = more stress/risk) unless noted.

0. UST positioning cross-check (not a 0-100 composite; four derived series):
   - xcheck-ust-net-ofr: OFR qualifying-HF net UST exposure = long minus
     short (quarterly), dates present in both legs.
   - xcheck-ust-net-cftc: sum of CFTC TFF leveraged-money nets across 2Y/5Y/
     10Y/30Y (weekly, long-minus-short per contract), weeks present in all
     four tenors.
   - xcheck-ust-oi: sum of CFTC TFF Treasury-futures open interest across
     2Y/5Y/10Y/30Y (weekly), weeks present in all four tenors.
   - xcheck-ust-agree: weekly count (0-3) of legs confirming an unwind.
     Leg 1 = OFR QoQ short-UST delta < -$1B (shorts shrinking). Leg 2 =
     CFTC 13-week aggregate-net delta > +1,000 contracts (net rising toward
     zero). Leg 3 = aggregate-OI 13-week delta < -100,000 contracts
     (positions closed, not rolled). Moves under the noise floors confirm
     nothing. Min 2 OFR quarters and 14 weeks of CFTC/OI history.

1. Basis-trade stress — mean of available sub-scores (each 0-100):
   - sofr_iorb_spread: percentile rank of latest (SOFR - IORB) vs its own
     history. Positive spread = cash funds paying above the Fed's floor =
     funding pressure. Min 20 observations.
   - rrp: mean of (a) 100 - pct_rank(RRP level) — a drained ON-RRP facility
     means a thinner liquidity buffer — and (b) pct_rank of the latest
     63-day drain vs historical 63-day drains (0 when not draining).
     Min 20 obs for (a), 70 for (b).
   - hf_repo_borrowing: pct_rank of latest qualifying-HF repo borrowing vs
     history (quarterly OFR data; min 8 obs). Reads `ofr:FPF-BORROW_REPO_SUM`
     first, falls back to the cycle-series mirror `cycle:hf-repo-borrow`.
   - dealer_fails: pct_rank of latest primary-dealer settlement fails.
     Sibling dealer fetcher stores as `dealer:<id>` where the id comes from
     its config; probed defensively across likely ids
     (dealer:fails, dealer:fails_deliver, ...). Optional, min 12 obs.
   Status "insufficient_data" when no sub-score is computable.

2. Auction stress — per benchmark coupon bucket in {Note-2Y, Note-3Y,
   Note-5Y, Note-7Y, Note-10Y, Bond-20Y, Bond-30Y}, reading the sibling
   auctions fetcher's keys `auction:<bucket>:bid_to_cover` and
   `auction:<bucket>:dealer_pct` (percent of accepted). Buckets the sibling
   did not configure are silently absent (store.points on a missing key is
   safe); bills are excluded — the composite tracks duration supply:
   - bid_to_cover stress = 100 * clip((6m_avg - latest) / 6m_avg, 0, 1).
     Weak demand vs recent norm = stress. Min 3 obs in trailing 6 months.
   - dealer_takedown stress = takedown share in percent, clipped to
     [0, 100] (x100 when stored as a fraction — detected via median < 1.5).
     Dealers warehousing supply = indigestion.
   Bucket score = mean of available sub-scores; composite = mean over
   buckets with data. No auction series at all -> {"value": None,
   "status": "awaiting_auction_feed"} (never crashes, never fabricates).

3. CTA crowdedness — per tenor z-score of Treasury futures net positioning.
   Prefers the TFF leveraged-funds series (`cftc:tff:<code>:lev_money`,
   sibling cftc_pos wiring — the sharper "who is short" gauge), falling back
   to legacy COT net non-commercial (`cycle:cot-ust-{2y,5y,10y,30y}`):
   z = (latest - mean(history)) / pstdev(history), expanding window,
   minimum 26 weekly observations else "insufficient_history".
   Negative z on 10Y = crowded short (squeeze risk if yields fall).
   Stored as `risk:cta_z_<tenor>` (one point per run).

4. Regime engine — rule-based score with a documented, auditable table
   (a missing input contributes 0 points):
     VIX level:              >= 35 -> 3 | >= 25 -> 2 | >= 18 -> 1
     HY OAS (%):             >= 8  -> 3 | >= 5.5 -> 2 | >= 4  -> 1
     US 10Y-2Y curve (%):    <= -1.00 -> 2 | <= -0.25 -> 1
     Duration momentum
       (TLT/SHY, 63d % chg): <= -8% -> 2 | <= -4% -> 1
   Score -> regime: 0-1 CALM | 2-3 LATE_CYCLE | 4-5 RISK_OFF |
                    6-7 STRESS | >= 8 CRISIS.
   Fixed thresholds are a v1 simplification (vol regimes drift over time);
   the table above is the audit trail. Numeric score stored as
   `risk:regime_score`; label lives in the summary doc.

5. Recession probability — Sahm rule computed locally from
   `cycle:us-unemployment` (monthly): sahm = 3m avg - min(trailing 12m of
   3m avg); classic trigger >= 0.5. Combined via logistic curves with
   documented weights (renormalized over available inputs):
     sahm_score   = 100/(1+exp(-12*(sahm-0.40)))    weight 0.35
     curve_score  = 100/(1+exp(-6*(max(0,-t10y3m)-0.75)))  weight 0.25
     hy_score     = 100/(1+exp(-1.5*(hy_oas-5.0)))  weight 0.25
     claims_score = 100/(1+exp(-15*(claims4w/claims52w-1.10)))  weight 0.15
   Minima: 9 unemployment obs, 60 claims obs; others need a latest value.

6. "Where are we / is it risky" — `risk_summary` doc: as-of timestamp, each
   composite's value + one-line plain-English read, regime label, and a
   headline verdict, e.g. "ELEVATED: basis-trade stress 72/100 with crowded
   Treasury shorts". Verdict level from the max of the available 0-100
   composites: <30 CONTAINED | <55 ELEVATED | <75 HIGH | else SEVERE.

Histories are stored as `risk:<name>` via upsert_points (one point per run).
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from collector.store import Store

log = logging.getLogger(__name__)

# ---- store keys read (ALL optional; missing keys simply yield no points) ----
K_SOFR = "cycle:sofr"
K_IORB = "cycle:iorb"
K_RRP = "cycle:rrp-on"
K_VIX = "cycle:vix"
K_HY = "cycle:hy-oas"
K_T10Y2Y = "cycle:t10y2y"
K_T10Y3M = "cycle:t10y3m"
K_TLTSHY = "cycle:tlt-shy"
K_UNRATE = "cycle:us-unemployment"
K_CLAIMS = "cycle:claims"
K_CTA = {t: f"cycle:cot-ust-{t}" for t in ("2y", "5y", "10y", "30y")}
# TFF leveraged-funds Treasury futures (sibling cftc_pos wiring, codes verified
# live): the sharper "who is short" gauge. Preferred over the legacy COT series
# above when present; store keys cftc:tff:<code>:lev_money.
K_CTA_TFF = {
    "2y": "cftc:tff:042601:lev_money",
    "5y": "cftc:tff:044601:lev_money",
    "10y": "cftc:tff:043602:lev_money",
    "30y": "cftc:tff:020601:lev_money",
}
# OFR repo borrowing: dedicated ofr job key first, cycle-series mirror second.
K_REPO = ["ofr:FPF-BORROW_REPO_SUM", "cycle:hf-repo-borrow"]
# NY Fed dealer fails: exact config ids first (sibling dealer task wires
# ust-fail-deliver / ust-fail-receive), then likely alternates.
# store.points on a missing key is safe.
_FAILS_DELIVER = ["dealer:ust-fail-deliver", "dealer:fails_deliver",
                  "dealer:fails-deliver", "dealer:fails"]
_FAILS_RECEIVE = ["dealer:ust-fail-receive", "dealer:fails_receive",
                  "dealer:fails-receive"]
# Treasury auctions (sibling auctions fetcher): keys are
#   auction:<Type>-<Tenor>:<metric>, e.g. auction:Note-10Y:bid_to_cover
# with metrics bid_to_cover / dealer_pct (percent). Probe benchmark coupon
# buckets; whatever the sibling configured gets picked up, the rest are
# silently absent. Bills excluded: the composite tracks duration supply.
AUCTION_BUCKETS = [
    "Note-2Y", "Note-3Y", "Note-5Y", "Note-7Y", "Note-10Y",
    "Bond-20Y", "Bond-30Y",
]
AUCTION_BTC = "bid_to_cover"
AUCTION_DEALER = "dealer_pct"

# ---- store keys written ----
RISK_BASIS = "risk:basis_stress"
RISK_AUCTION = "risk:auction_stress"
RISK_CTA = {t: f"risk:cta_z_{t}" for t in ("2y", "5y", "10y", "30y")}
RISK_REGIME = "risk:regime_score"
RISK_RECESSION = "risk:recession_prob"
DOC_SUMMARY = "risk_summary"

# ---- UST positioning cross-check: OFR (quarterly) vs CFTC TFF (weekly) ----
# Reads the sibling ofr job's keys plus K_CTA_TFF above; writes cycle: keys so
# the dashboard resolves them as ordinary external cycle series (no source key
# needed in config). Full-history recompute each run; idempotent.
K_OFR_UST_LONG = "ofr:FPF-ASSETCLASS_LTREASURY_SUM"
K_OFR_UST_SHORT = "ofr:FPF-ASSETCLASS_STREASURY_SUM"
# Treasury-futures open interest per tenor (sibling cycle.py cftc_oi series)
K_OI = {t: f"cycle:oi-ust-{t}" for t in ("2y", "5y", "10y", "30y")}
XCHECK_OFR_NET = "cycle:xcheck-ust-net-ofr"     # OFR net UST exposure $ (long - short)
XCHECK_CFTC_NET = "cycle:xcheck-ust-net-cftc"   # CFTC TFF lev-money net, 2Y+5Y+10Y+30Y (contracts)
XCHECK_OI = "cycle:xcheck-ust-oi"               # CFTC TFF aggregate UST futures OI (contracts)
XCHECK_AGREE = "cycle:xcheck-ust-agree"         # confirming legs, 0-3
# Noise floors: moves smaller than these score as flat (0). OFR QoQ short-UST
# moves run in the tens of $B; CFTC weekly aggregate moves run in the 10k
# contracts; aggregate OI 13-week moves run in the millions of contracts.
_XCHECK_OFR_EPS = 1e9
_XCHECK_CFTC_EPS = 1_000.0
_XCHECK_OI_EPS = 100_000.0
_XCHECK_CFTC_WEEKS = 13  # 13-week trend window on the weekly CFTC aggregate

SOURCE_LABEL = "risk-engine"


# --------------------------------------------------------------------------
# helpers (pure; unit-tested)
# --------------------------------------------------------------------------

def _hist(points: dict[date, float]) -> list[tuple[date, float]]:
    """Sorted, finite-only history. Missing/corrupt points never raise."""
    return sorted(
        ((d, v) for d, v in points.items()
         if isinstance(v, (int, float)) and math.isfinite(v)),
        key=lambda p: p[0],
    )


def _read_first(store: Store, keys: list[str]) -> list[tuple[date, float]]:
    """First non-empty history among candidate keys (primary + fallbacks)."""
    for k in keys:
        h = _hist(store.points(k))
        if h:
            return h
    return []


def _read_fails(store: Store) -> list[tuple[date, float]]:
    """Total UST settlement fails = fails-to-deliver + fails-to-receive,
    aligned on common dates. Falls back to whichever leg exists alone."""
    d = _read_first(store, _FAILS_DELIVER)
    r = _read_first(store, _FAILS_RECEIVE)
    if d and r:
        dd, rr = dict(d), dict(r)
        common = sorted(set(dd) & set(rr))
        if len(common) >= 12:
            return [(dt, dd[dt] + rr[dt]) for dt in common]
    return d or r


def _pct_rank(x: float, hist: list[float]) -> float | None:
    """Percentile rank of x within hist, 0-100. Higher x -> higher rank."""
    if not hist:
        return None
    return 100.0 * sum(1 for h in hist if h <= x) / len(hist)


def _logistic(x: float, x0: float, k: float) -> float:
    try:
        return 100.0 / (1.0 + math.exp(-k * (x - x0)))
    except OverflowError:
        return 100.0 if x > x0 else 0.0


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _latest_before(hist: list[tuple[date, float]], cutoff: date) -> float | None:
    """Most recent value on or before cutoff, else None."""
    v = None
    for d, val in hist:
        if d <= cutoff:
            v = val
        else:
            break
    return v


# --------------------------------------------------------------------------
# 1. basis-trade stress
# --------------------------------------------------------------------------

def basis_stress(store: Store) -> dict:
    subs: dict[str, float] = {}

    sofr = dict(_hist(store.points(K_SOFR)))
    iorb = dict(_hist(store.points(K_IORB)))
    common = sorted(set(sofr) & set(iorb))
    if len(common) >= 20:
        spreads = [sofr[d] - iorb[d] for d in common]
        r = _pct_rank(spreads[-1], spreads)
        if r is not None:
            subs["sofr_iorb_spread"] = r

    rrp = _hist(store.points(K_RRP))
    if len(rrp) >= 20:
        vals = [v for _, v in rrp]
        level = 100.0 - (_pct_rank(vals[-1], vals) or 0.0)
        drain_score = 0.0
        if len(rrp) >= 70:
            drains = [
                rrp[i - 63][1] - rrp[i][1]
                for i in range(63, len(rrp))
            ]
            pos_drains = [d for d in drains if d > 0]
            latest_drain = rrp[-64][1] - rrp[-1][1] if len(rrp) >= 64 else 0.0
            if latest_drain > 0 and pos_drains:
                drain_score = _pct_rank(latest_drain, pos_drains) or 0.0
        subs["rrp"] = (level + drain_score) / 2.0

    repo = _read_first(store, K_REPO)
    if len(repo) >= 8:
        vals = [v for _, v in repo]
        r = _pct_rank(vals[-1], vals)
        if r is not None:
            subs["hf_repo_borrowing"] = r

    fails = _read_fails(store)
    if len(fails) >= 12:
        vals = [v for _, v in fails]
        r = _pct_rank(vals[-1], vals)
        if r is not None:
            subs["dealer_fails"] = r

    if not subs:
        return {"value": None, "status": "insufficient_data", "detail": {}}
    value = sum(subs.values()) / len(subs)
    return {
        "value": round(value, 1),
        "status": "ok",
        "detail": {k: round(v, 1) for k, v in subs.items()},
    }


# --------------------------------------------------------------------------
# 2. auction stress
# --------------------------------------------------------------------------

def auction_stress(store: Store) -> dict:
    per_bucket: dict[str, float] = {}
    for bucket in AUCTION_BUCKETS:
        btc = _hist(store.points(f"auction:{bucket}:{AUCTION_BTC}"))
        dealer = _hist(store.points(f"auction:{bucket}:{AUCTION_DEALER}"))
        subs: dict[str, float] = {}
        if len(btc) >= 3:
            cutoff = btc[-1][0] - timedelta(days=182)
            base = [v for d, v in btc if d >= cutoff and v > 0]
            if len(base) >= 3 and mean(base) > 0:
                avg = mean(base)
                subs["bid_to_cover"] = _clip(
                    100.0 * (avg - btc[-1][1]) / avg, 0.0, 100.0)
        if dealer:
            # dealer_pct is stored as percent (0-100); tolerate fractions
            vals = [v for _, v in dealer]
            med = sorted(vals)[len(vals) // 2]
            latest = vals[-1] * (100.0 if med < 1.5 else 1.0)
            subs["dealer_takedown"] = _clip(latest, 0.0, 100.0)
        if subs:
            per_bucket[bucket] = sum(subs.values()) / len(subs)
    if not per_bucket:
        return {"value": None, "status": "awaiting_auction_feed", "detail": {}}
    value = sum(per_bucket.values()) / len(per_bucket)
    return {
        "value": round(value, 1),
        "status": "ok",
        "detail": {b: round(v, 1) for b, v in per_bucket.items()},
    }


# --------------------------------------------------------------------------
# 3. CTA crowdedness z-scores
# --------------------------------------------------------------------------

CTA_MIN_OBS = 26  # ~6 months of weekly COT reports


def cta_crowdedness(store: Store) -> dict:
    out: dict[str, dict] = {}
    for t in ("2y", "5y", "10y", "30y"):
        # prefer TFF leveraged-funds positioning; fall back to legacy COT
        h = _hist(store.points(K_CTA_TFF[t]))
        source = "tff:lev_money"
        if not h:
            h = _hist(store.points(K_CTA[t]))
            source = "legacy"
        if len(h) < CTA_MIN_OBS:
            out[t] = {"z": None, "status": "insufficient_history",
                      "n": len(h), "source": source}
            continue
        vals = [v for _, v in h]
        sd = pstdev(vals)
        if sd <= 0:
            out[t] = {"z": None, "status": "no_variance",
                      "n": len(h), "source": source}
            continue
        z = (vals[-1] - mean(vals)) / sd
        out[t] = {"z": round(z, 2), "status": "ok",
                  "n": len(h), "source": source}
    return out


# --------------------------------------------------------------------------
# 4. regime engine
# --------------------------------------------------------------------------

REGIMES = ["CALM", "LATE_CYCLE", "RISK_OFF", "STRESS", "CRISIS"]


def regime(store: Store) -> dict:
    score = 0
    used: list[str] = []

    vix = _hist(store.points(K_VIX))
    if vix:
        v = vix[-1][1]
        s = 3 if v >= 35 else 2 if v >= 25 else 1 if v >= 18 else 0
        score += s
        used.append(f"vix={v:.1f}")

    hy = _hist(store.points(K_HY))
    if hy:
        v = hy[-1][1]
        s = 3 if v >= 8 else 2 if v >= 5.5 else 1 if v >= 4 else 0
        score += s
        used.append(f"hy_oas={v:.2f}%")

    curve = _hist(store.points(K_T10Y2Y))
    if curve:
        v = curve[-1][1]
        s = 2 if v <= -1.0 else 1 if v <= -0.25 else 0
        score += s
        used.append(f"t10y2y={v:+.2f}%")

    tlts = _hist(store.points(K_TLTSHY))
    if len(tlts) >= 70:
        past = _latest_before(tlts, tlts[-1][0] - timedelta(days=70))
        if past:
            chg = 100.0 * (tlts[-1][1] - past) / past if past != 0 else 0.0
            s = 2 if chg <= -8 else 1 if chg <= -4 else 0
            score += s
            used.append(f"tlt/shy_70d={chg:+.1f}%")

    label = (
        "CALM" if score <= 1 else
        "LATE_CYCLE" if score <= 3 else
        "RISK_OFF" if score <= 5 else
        "STRESS" if score <= 7 else "CRISIS"
    )
    return {"score": score, "label": label, "inputs": used}


# --------------------------------------------------------------------------
# 5. recession probability
# --------------------------------------------------------------------------

def _sahm(unrate: list[tuple[date, float]]) -> float | None:
    """Sahm rule from monthly unemployment: 3m avg minus 12m low of 3m avg."""
    if len(unrate) < 9:
        return None
    vals = [v for _, v in unrate]
    ma3 = [mean(vals[i - 2:i + 1]) for i in range(2, len(vals))]
    window = ma3[-12:] if len(ma3) >= 12 else ma3
    return ma3[-1] - min(window)


def recession_prob(store: Store) -> dict:
    comps: dict[str, tuple[float, float]] = {}  # name -> (score, weight)

    sahm = _sahm(_hist(store.points(K_UNRATE)))
    if sahm is not None:
        comps["sahm"] = (_logistic(sahm, 0.40, 12.0), 0.35)

    c = _hist(store.points(K_T10Y3M))
    if c:
        inv = max(0.0, -c[-1][1])
        comps["curve"] = (_logistic(inv, 0.75, 6.0), 0.25)

    hy = _hist(store.points(K_HY))
    if hy:
        comps["hy_oas"] = (_logistic(hy[-1][1], 5.0, 1.5), 0.25)

    cl = _hist(store.points(K_CLAIMS))
    if len(cl) >= 60:
        vals = [v for _, v in cl]
        avg52 = mean(vals[-52:])
        if avg52 > 0:
            comps["claims"] = (_logistic(mean(vals[-4:]) / avg52, 1.10, 15.0), 0.15)

    if not comps:
        return {"value": None, "status": "insufficient_data", "detail": {}}
    wsum = sum(w for _, w in comps.values())
    value = sum(v * w for v, w in comps.values()) / wsum
    detail = {k: round(v, 1) for k, (v, _) in comps.items()}
    if sahm is not None:
        detail["sahm_rule"] = round(sahm, 2)
    return {"value": round(value, 1), "status": "ok", "detail": detail}


# --------------------------------------------------------------------------
# UST positioning cross-check — OFR (quarterly) vs CFTC TFF (weekly)
# --------------------------------------------------------------------------
# The basis trade (long cash UST funded in repo, short UST futures) shows up
# as simultaneously large long-UST and short-UST exposures on hedge-fund
# balance sheets. OFR's Form PF aggregates give the quarterly balance-sheet
# view; CFTC TFF leveraged-money nets give the weekly futures view. When both
# agree the short leg is shrinking, the unwind call is corroborated.
#
# Conventions: OFR trend = sign of quarter-over-quarter change in SHORT UST
# exposure (negative delta = shorts shrinking = "unwinding" = +1). CFTC nets
# are long-minus-short (negative = net short), so a POSITIVE 13-week delta
# (net rising toward zero = shorts shrinking) is also "unwinding".
# OI leg: a NEGATIVE 13-week delta (open interest falling) = +1 — positions
# being closed, not rolled.

def derive_ust_nets(
    long_pts: dict[date, float],
    short_pts: dict[date, float],
    cftc_by_tenor: dict[str, dict[date, float]],
) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    """OFR net UST exposure (long - short) and the CFTC TFF aggregate net.

    OFR net covers dates present in BOTH long and short series. The CFTC
    aggregate covers weeks present in ALL FOUR tenor series, so a missing
    tenor never silently shifts the level.
    """
    both = sorted(set(long_pts) & set(short_pts))
    net_ofr = [(d, long_pts[d] - short_pts[d]) for d in both]
    tenor_dates = [set(p) for p in cftc_by_tenor.values() if p]
    common = sorted(set.intersection(*tenor_dates)) if tenor_dates else []
    net_cftc = [(d, sum(p[d] for p in cftc_by_tenor.values())) for d in common]
    return net_ofr, net_cftc


def derive_ust_oi(
    oi_by_tenor: dict[str, dict[date, float]],
) -> list[tuple[date, float]]:
    """Aggregate Treasury-futures open interest across the four tenors.

    Covers weeks present in ALL FOUR tenor series so a missing tenor never
    silently shifts the level. Falling aggregate OI = positions being closed,
    not rolled — the third unwind-confirming leg.
    """
    tenor_dates = [set(p) for p in oi_by_tenor.values() if p]
    common = sorted(set.intersection(*tenor_dates)) if tenor_dates else []
    return [(d, sum(p[d] for p in oi_by_tenor.values())) for d in common]


def derive_ust_agreement(
    net_ofr: list[tuple[date, float]],
    short_pts: dict[date, float],
    net_cftc: list[tuple[date, float]],
    net_oi: list[tuple[date, float]],
) -> list[tuple[date, float]]:
    """Weekly trend-agreement history: COUNT (0-3) of legs confirming an
    unwind. Leg 1 = OFR quarterly short-UST exposure shrinking (QoQ delta <
    -eps at the latest quarter on/before the week). Leg 2 = CFTC 13-week
    aggregate-net delta > +eps (net rising toward zero = shorts shrinking).
    Leg 3 = aggregate-OI 13-week delta < -eps (positions closed, not rolled).
    Moves under the noise floors score as flat and confirm nothing. Weeks
    with insufficient history are skipped."""
    if (len(net_cftc) <= _XCHECK_CFTC_WEEKS or len(short_pts) < 2
            or len(net_oi) < 2):
        return []
    short_hist = sorted(short_pts.items())
    ofr_dates = [d for d, _ in short_hist]
    out: list[tuple[date, float]] = []
    for i in range(_XCHECK_CFTC_WEEKS, len(net_cftc)):
        w = net_cftc[i][0]
        # latest OFR quarter on/before this week, plus its predecessor
        qi = -1
        for j, qd in enumerate(ofr_dates):
            if qd <= w:
                qi = j
            else:
                break
        if qi < 1:
            continue
        legs = 0
        # leg 1: OFR quarterly short-UST exposure shrinking
        if short_hist[qi][1] - short_hist[qi - 1][1] < -_XCHECK_OFR_EPS:
            legs += 1
        # leg 2: CFTC 13-week aggregate net rising toward zero
        if net_cftc[i][1] - net_cftc[i - _XCHECK_CFTC_WEEKS][1] > _XCHECK_CFTC_EPS:
            legs += 1
        # leg 3: aggregate OI falling over ~13 weeks (closed, not rolled)
        oi_now = _latest_before(net_oi, w)
        oi_then = _latest_before(net_oi, w - timedelta(weeks=_XCHECK_CFTC_WEEKS))
        if (oi_now is not None and oi_then is not None
                and oi_now - oi_then < -_XCHECK_OI_EPS):
            legs += 1
        out.append((w, float(legs)))
    return out


def refresh_ust_xcheck(store: Store) -> None:
    """Compute-only: full-history OFR/CFTC/OI UST cross-check. Never raises —
    missing inputs simply yield no points, per the module contract."""
    try:
        long_pts = store.points(K_OFR_UST_LONG)
        short_pts = store.points(K_OFR_UST_SHORT)
        cftc = {t: store.points(k) for t, k in K_CTA_TFF.items()}
        oi = {t: store.points(k) for t, k in K_OI.items()}
        net_ofr, net_cftc = derive_ust_nets(long_pts, short_pts, cftc)
        net_oi = derive_ust_oi(oi)
        if net_ofr:
            store.upsert_points(XCHECK_OFR_NET, net_ofr)
        if net_cftc:
            store.upsert_points(XCHECK_CFTC_NET, net_cftc)
        if net_oi:
            store.upsert_points(XCHECK_OI, net_oi)
        agree = derive_ust_agreement(net_ofr, short_pts, net_cftc, net_oi)
        if agree:
            store.upsert_points(XCHECK_AGREE, agree)
    except Exception as exc:  # noqa: BLE001 — cross-check must never break risk
        log.warning("ust xcheck skipped: %s", exc)


# --------------------------------------------------------------------------
# 6. summary + verdict
# --------------------------------------------------------------------------

def _read_basis(b: dict) -> str:
    if b["value"] is None:
        return "Basis-trade stress: not enough history yet."
    driver = max(b["detail"], key=b["detail"].get) if b["detail"] else "n/a"
    return (f"Basis-trade stress {b['value']:.0f}/100 "
            f"(driven by {driver.replace('_', ' ')}).")


def _read_auction(a: dict) -> str:
    if a["value"] is None:
        return "Auction feed not live yet — supply stress unwatched."
    n = len(a["detail"])
    return f"Auction stress {a['value']:.0f}/100 across {n} tenor{'s' if n != 1 else ''}."


def _read_cta(cta: dict) -> str:
    z10 = cta.get("10y", {}).get("z")
    if z10 is None:
        return "Treasury positioning history too short for crowdedness read."
    if z10 <= -1.5:
        return (f"Treasury shorts crowded (10Y z={z10:.2f}) — "
                "squeeze risk if yields fall.")
    if z10 >= 1.5:
        return f"Positioning crowded long (10Y z={z10:+.2f})."
    return f"Treasury positioning neutral (10Y z={z10:+.2f})."


def _read_recession(r: dict) -> str:
    if r["value"] is None:
        return "Recession gauge: insufficient labor data."
    sahm = r["detail"].get("sahm_rule")
    extra = f", Sahm {sahm:.2f}" if sahm is not None else ""
    return f"Recession probability {r['value']:.0f}%{extra}."


def build_summary(store: Store) -> dict:
    """Compute every composite with per-composite isolation and assemble the
    risk_summary doc payload. One bad composite never kills the summary."""
    def _safe(fn, name):
        try:
            return fn(store)
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            log.warning("risk composite %s failed: %s", name, exc)
            return {"value": None, "status": f"error: {type(exc).__name__}",
                    "detail": {}}

    basis = _safe(basis_stress, "basis")
    auction = _safe(auction_stress, "auction")
    cta = _safe(cta_crowdedness, "cta")
    reg = _safe(regime, "regime")
    rec = _safe(recession_prob, "recession")

    cands = []
    if basis["value"] is not None:
        cands.append(("basis-trade stress", basis["value"]))
    if auction["value"] is not None:
        cands.append(("auction stress", auction["value"]))
    if rec["value"] is not None:
        cands.append(("recession risk", rec["value"]))

    if not cands:
        verdict = "NO DATA: risk engine has no inputs yet."
    else:
        top = max(cands, key=lambda x: x[1])
        level = ("CONTAINED" if top[1] < 30 else "ELEVATED" if top[1] < 55
                 else "HIGH" if top[1] < 75 else "SEVERE")
        bits = [f"{name} {val:.0f}/100"
                for name, val in sorted(cands, key=lambda x: -x[1])[:2]]
        z10 = cta.get("10y", {}).get("z") if isinstance(cta, dict) else None
        note = ""
        if isinstance(z10, (int, float)):
            if z10 <= -1.5:
                note = f"; crowded Treasury shorts (10Y z={z10:.2f})"
            elif z10 >= 1.5:
                note = f"; crowded Treasury longs (10Y z={z10:+.2f})"
        verdict = f"{level}: " + ", ".join(bits) + note

    return {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "regime": reg.get("label", "UNKNOWN"),
        "regime_score": reg.get("score"),
        "components": {
            "basis_stress": {
                "value": basis["value"], "status": basis["status"],
                "read": _read_basis(basis), "detail": basis["detail"],
            },
            "auction_stress": {
                "value": auction["value"], "status": auction["status"],
                "read": _read_auction(auction), "detail": auction["detail"],
            },
            "cta_crowdedness": {
                "z_10y": cta.get("10y", {}).get("z"),
                "status": ("ok" if any(
                    isinstance(v, dict) and v.get("status") == "ok"
                    for v in cta.values()) else "insufficient_history"),
                "read": _read_cta(cta),
                "detail": {t: v.get("z") for t, v in cta.items()
                           if isinstance(v, dict)},
            },
            "recession_prob": {
                "value": rec["value"], "status": rec["status"],
                "read": _read_recession(rec), "detail": rec["detail"],
            },
        },
        "verdict": verdict,
    }


async def refresh_risk(store: Store) -> str:
    """Compute-only job: one history point per composite per run + summary doc.
    Follows the fetcher contract (returns source label); run_fetcher wraps it
    with status recording and total error isolation."""
    today = datetime.now(timezone.utc).date()
    summary = build_summary(store)

    comp = summary["components"]
    if comp["basis_stress"]["value"] is not None:
        store.upsert_points(RISK_BASIS, [(today, comp["basis_stress"]["value"])])
    if comp["auction_stress"]["value"] is not None:
        store.upsert_points(RISK_AUCTION, [(today, comp["auction_stress"]["value"])])
    for t, key in RISK_CTA.items():
        z = comp["cta_crowdedness"]["detail"].get(t)
        if isinstance(z, (int, float)):
            store.upsert_points(key, [(today, z)])
    if isinstance(summary["regime_score"], (int, float)):
        store.upsert_points(RISK_REGIME, [(today, float(summary["regime_score"]))])
    if comp["recession_prob"]["value"] is not None:
        store.upsert_points(RISK_RECESSION, [(today, comp["recession_prob"]["value"])])

    # UST positioning cross-check (OFR vs CFTC TFF): compute-only, never raises,
    # full-history idempotent recompute — runs inside the daily risk cadence,
    # which starts 5 min after the data jobs (incl. cftc_pos) each day.
    refresh_ust_xcheck(store)

    store.put_doc(DOC_SUMMARY, summary, source=SOURCE_LABEL)

    failed = [k for k, v in comp.items() if str(v.get("status", "")).startswith("error")]
    if failed:
        raise RuntimeError(f"risk composites errored: {', '.join(failed)}")
    return SOURCE_LABEL
