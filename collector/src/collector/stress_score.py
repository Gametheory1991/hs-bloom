"""Stress Monitor v2 scoring engine (Phase 1).

Pure functions, stdlib only. No look-ahead: every statistic uses only
observations with date <= as_of. No interpolation across gaps: changes are
always computed between consecutive *stored* observations.

Conventions (documented so the UI and tests can rely on them):
- ``history`` is a dict keyed by ISO date string or ``datetime.date``;
  values are floats. A single observation == one trading observation.
- Percentile = 100 * (# window obs <= value) / n  (inclusive rank percentile).
- z-score = (x - mean) / pstdev (population std) of the window.
- Velocity windows k in (1, 5, 20, 60) are *trading observations*, not
  calendar days: raw change = v(t) - v(t-k) using the last k+1 stored
  observations at-or-before ``as_of``.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timezone
from typing import Any

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

MIN_OBS_SCORE = 252          # minimum observations before a score is computed
MAX_WINDOW = 5 * 252         # expanding window cap: 5 trading years
VEL_WINDOWS = (1, 5, 20, 60)
TRAIL_1Y = 252               # trailing-1Y baseline for sigma / percentile
MIN_CHANGES = 20             # minimum k-changes needed for sigma / percentile
FREQ_NO_VELOCITY = {"Q"}     # quarterly series: no velocity per spec
SPEED_WEEKLY_STEP = 5        # weekly sampling of trailing dates for speed sigma

# Overall Gauge default category weights (spec section 6; editable in UI)
DEFAULT_WEIGHTS = {
    "funding": 0.20,
    "credit": 0.20,
    "rates": 0.20,
    "volatility": 0.15,
    "equity": 0.10,
    "banks": 0.10,
    "global": 0.05,
}

# Registry fields Worker B's config.yaml registry must provide (Phase 1).
REGISTRY_FIELDS = (
    "id", "category", "name", "unit", "direction", "freq",
    "tier", "series_id", "source", "tag",
)

# A tiny inline registry for tests. Worker B owns the real one in config.yaml.
SAMPLE_REGISTRY = [
    {"id": "vix", "category": "volatility", "name": "VIX",
     "unit": "index", "direction": "+", "freq": "D", "tier": "core",
     "series_id": "cycle:vix", "source": "FRED VIXCLS", "tag": "primary"},
    {"id": "hy_oas", "category": "credit", "name": "US HY OAS",
     "unit": "bp", "direction": "+", "freq": "D", "tier": "core",
     "series_id": "cycle:hy-oas", "source": "FRED BAMLH0A0HYM2", "tag": "primary"},
    {"id": "sofr_iorb", "category": "funding", "name": "SOFR minus IORB",
     "unit": "bp", "direction": "+", "freq": "D", "tier": "core",
     "series_id": "cycle:sofr-iorb", "source": "DERIVED", "tag": "derived"},
    {"id": "claims", "category": "banks", "name": "Initial jobless claims",
     "unit": "k", "direction": "+", "freq": "W", "tier": "core",
     "series_id": "cycle:claims", "source": "FRED ICSA", "tag": "primary"},
]

SOURCE_LABEL = "stress-v2"

# --------------------------------------------------------------------------
# history normalization
# --------------------------------------------------------------------------


def _to_date(d: Any) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    return date.fromisoformat(str(d))


def _sorted_obs(history: dict[Any, float]) -> tuple[list[date], list[float]]:
    """Return (dates, values) sorted ascending by date."""
    pairs = sorted((_to_date(d), float(v)) for d, v in history.items())
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _as_of_date(as_of: Any) -> date:
    return _to_date(as_of)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def _score_at(dates: list[date], values: list[float], idx: int,
              direction: str, max_window: int = MAX_WINDOW) -> dict:
    """Score the observation at position ``idx`` against its expanding window.

    Window = the ``max_window`` most recent observations at-or-before idx
    (idx included). No look-ahead by construction: positions > idx are never
    touched.
    """
    start = max(0, idx - max_window + 1)
    window = values[start:idx + 1]
    n = len(window)
    if n < MIN_OBS_SCORE:
        return {"score": None, "percentile": None, "z": None,
                "window_used": n, "n_obs": n, "status": "building"}
    latest = values[idx]

    if direction == "+":
        pct = 100.0 * sum(1 for x in window if x <= latest) / n
        z = (latest - statistics.fmean(window)) / (statistics.pstdev(window) or float("nan"))
        score = pct
    elif direction == "-":
        pct = 100.0 * sum(1 for x in window if x <= latest) / n
        z = (latest - statistics.fmean(window)) / (statistics.pstdev(window) or float("nan"))
        score = 100.0 - pct
    elif direction == "±":
        med = statistics.median(window)
        sd = statistics.pstdev(window)
        if sd == 0:
            z = 0.0
            score = 0.0
            pct = 0.0
        else:
            z_vals = [abs((x - med) / sd) for x in window]
            z_latest = abs((latest - med) / sd)
            pct = 100.0 * sum(1 for zc in z_vals if zc <= z_latest) / n
            z = (latest - med) / sd
            score = pct
    else:
        raise ValueError(f"unknown direction {direction!r}")

    if z != z:  # nan from zero stdev on flat window
        z = 0.0

    window_label = (f"5Y ({n}/{max_window})" if n >= max_window
                    else f"expanding ({n}/{max_window})")
    confidence = ("high" if n >= max_window else
                  "medium" if n >= 2 * 252 else "low")
    return {"score": score, "percentile": pct, "z": z,
            "window_used": window_label, "n_obs": n,
            "status": "ok", "confidence": confidence}


def score_series(history: dict[Any, float], direction: str, as_of: Any,
                 max_window: int = MAX_WINDOW) -> dict:
    """0-100 direction-adjusted percentile of the latest observation vs its own
    history (observations with date <= as_of only)."""
    dates, values = _sorted_obs(history)
    ao = _as_of_date(as_of)
    idx = _last_le(dates, ao)
    if idx < 0:
        return {"score": None, "percentile": None, "z": None,
                "window_used": "none", "n_obs": 0, "status": "no_data",
                "confidence": None}
    out = _score_at(dates, values, idx, direction, max_window)
    out["as_of"] = dates[idx].isoformat()
    out["value"] = values[idx]
    return out


def _last_le(dates: list[date], ao: date) -> int:
    """Index of the last date <= as_of, or -1."""
    lo, hi, ans = 0, len(dates) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= ao:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


def score_change(history: dict[Any, float], direction: str, as_of: Any, k: int,
                 max_window: int = MAX_WINDOW) -> float | None:
    """Change in the 0-100 score over the last k trading observations."""
    dates, values = _sorted_obs(history)
    ao = _as_of_date(as_of)
    idx = _last_le(dates, ao)
    if idx < 0 or idx - k < 0:
        return None
    now = _score_at(dates, values, idx, direction, max_window)
    then = _score_at(dates, values, idx - k, direction, max_window)
    if now["status"] != "ok" or then["status"] != "ok":
        return None
    return now["score"] - then["score"]


# --------------------------------------------------------------------------
# velocity
# --------------------------------------------------------------------------


def _k_changes(values: list[float], i: int, k: int, back: int = TRAIL_1Y) -> list[float]:
    """All k-step changes with endpoint j in (i-back, i], requiring j-k >= 0."""
    out = []
    for j in range(max(k, i - back + 1), i + 1):
        out.append(values[j] - values[j - k])
    return out


def velocity(history: dict[Any, float], as_of: Any, freq: str = "D",
             direction: str = "+") -> dict:
    """Velocity layer for one indicator.

    For each k in (1, 5, 20, 60) trading observations:
      raw    = v(t) - v(t-k) in native units (direction-adjusted so + = more
               stress for '-' indicators; '±' is left unadjusted)
      sigma  = raw / trailing-1Y std of k-changes  ("x sigma")
      pctile = rank of |raw| vs the last 1Y of |k-changes| ("unusually fast?")
    accel = change in 5-obs normalized velocity over the last 5 observations.
    Windows with fewer than k+1 observations are n/a (None), never interpolated.
    """
    dates, values = _sorted_obs(history)
    ao = _as_of_date(as_of)
    i = _last_le(dates, ao)

    if freq in FREQ_NO_VELOCITY:
        return {"raw": {}, "sigma": {}, "pctile": {}, "accel": None,
                "status": "no_velocity", "n_obs": len(values)}
    if i < 0:
        return {"raw": {}, "sigma": {}, "pctile": {}, "accel": None,
                "status": "no_data", "n_obs": 0}

    sign = -1.0 if direction == "-" else 1.0
    raw: dict[str, float | None] = {}
    sigma: dict[str, float | None] = {}
    pctile: dict[str, float | None] = {}
    _sig = {}  # full per-k sigma series info for accel

    for k in VEL_WINDOWS:
        key = f"d{k}"
        if i - k < 0:
            raw[key] = sigma[key] = pctile[key] = None
            _sig[key] = None
            continue
        r = sign * (values[i] - values[i - k])
        raw[key] = r
        ch = _k_changes(values, i, k)
        if len(ch) < MIN_CHANGES:
            sigma[key] = pctile[key] = None
            _sig[key] = None
            continue
        sd = statistics.pstdev(ch)
        if sd == 0:
            sigma[key] = 0.0 if r == 0 else None
        else:
            sigma[key] = r / sd
        pctile[key] = 100.0 * sum(1 for c in ch if abs(c) <= abs(r)) / len(ch)
        _sig[key] = sigma[key]

    # acceleration: 5-obs velocity change over the last 5 observations
    accel = None
    if _sig.get("d5") is not None and i - 5 >= 0:
        ch5_then = _k_changes(values, i - 5, 5)
        if len(ch5_then) >= MIN_CHANGES:
            sd_then = statistics.pstdev(ch5_then)
            if sd_then:
                r_then = sign * (values[i - 5] - values[i - 10])
                accel = _sig["d5"] - r_then / sd_then

    return {"raw": raw, "sigma": sigma, "pctile": pctile, "accel": accel,
            "status": "ok", "n_obs": i + 1}


# --------------------------------------------------------------------------
# composites + overall gauge
# --------------------------------------------------------------------------


def composite(scores: list[float | None]) -> dict:
    """Equal-weight mean of available scores.

    coverage = n_avail / n_total. Coverage below 50% -> status
    "low_coverage" (the composite is computed but excluded from the overall
    gauge). Missing scores are never filled with a neutral 50.
    """
    n_total = len(scores)
    avail = [s for s in scores if s is not None]
    n_avail = len(avail)
    if n_total == 0 or n_avail == 0:
        return {"score": None, "coverage": 0.0, "n_avail": 0,
                "n_total": n_total, "status": "no_data"}
    coverage = n_avail / n_total
    status = "ok" if coverage >= 0.5 else "low_coverage"
    return {"score": sum(avail) / n_avail, "coverage": coverage,
            "n_avail": n_avail, "n_total": n_total, "status": status}


def regime_label(score: float | None) -> str | None:
    if score is None:
        return None
    if score < 30:
        return "Calm"
    if score < 50:
        return "Normal"
    if score < 70:
        return "Elevated"
    if score <= 85:
        return "High"
    return "Acute"


def overall_gauge(composites: dict[str, dict], weights: dict[str, float]) -> dict:
    """Weighted mean over available categories, reweighted when a category is
    dropped (low coverage or no data)."""
    avail = {c: r for c, r in composites.items()
             if r.get("status") == "ok" and r.get("score") is not None}
    dropped = sorted(set(composites) - set(avail))
    total_w = sum(weights.get(c, 0.0) for c in avail)
    if not avail or total_w <= 0:
        return {"score": None, "regime": None, "coverage": 0.0,
                "dropped": dropped, "reweighted": False, "weights_used": {}}
    w_used = {c: weights.get(c, 0.0) / total_w for c in avail}
    score = sum(w_used[c] * avail[c]["score"] for c in avail)
    return {"score": score, "regime": regime_label(score),
            "coverage": len(avail) / len(composites),
            "dropped": dropped, "reweighted": bool(dropped),
            "weights_used": w_used}


# --------------------------------------------------------------------------
# refresh_stress: compute + cache docs
# --------------------------------------------------------------------------


def _overall_speed_sigma(store, registry, ao):
    """Trailing-1Y stdev of 5-obs overall-score changes.

    Past dates are sampled every SPEED_WEEKLY_STEP observations to bound
    cost. Returns None when the trailing sample is too short (< 10 points).
    """
    # per-indicator sorted histories (only scored indicators feed the gauge)
    hists = {}
    for e in registry:
        if e.get("tag") == "proxy" or e.get("freq") == "Q":
            continue  # proxies/Q excluded from composites
        hist = store.points(e["series_id"])
        dates, values = _sorted_obs(hist)
        i = _last_le(dates, ao)
        if i >= 0:
            hists[e["id"]] = (dates, values, e["direction"], e["category"])

    if not hists:
        return None

    # use the longest history to anchor the sampled past dates
    longest = max(hists.values(), key=lambda t: len(t[0]))
    i_long = _last_le(longest[0], ao)
    idxs = list(range(i_long - 5, max(4, i_long - TRAIL_1Y) - 1,
                      -SPEED_WEEKLY_STEP))
    idxs.reverse()
    if len(idxs) < 10:
        return None

    weights = dict(DEFAULT_WEIGHTS)
    deltas = []
    for li in idxs:
        d = longest[0][li]
        comp_scores = {}
        for iid, (dates, values, direction, cat) in hists.items():
            j = _last_le(dates, d)
            if j - 5 < 0:
                continue
            s_now = _score_at(dates, values, j, direction)
            s_then = _score_at(dates, values, j - 5, direction)
            if s_now["status"] == "ok" and s_then["status"] == "ok":
                comp_scores.setdefault(cat, []).append(
                    s_now["score"] - s_then["score"])
        cat_delta = {c: sum(v) / len(v) for c, v in comp_scores.items() if v}
        avail = [c for c in cat_delta if c in weights]
        tw = sum(weights[c] for c in avail)
        if avail and tw > 0:
            deltas.append(sum(weights[c] / tw * cat_delta[c] for c in avail))
    if len(deltas) < 10:
        return None
    return statistics.pstdev(deltas)


def refresh_stress(store: Any, registry: list[dict],
                   weights: dict[str, float] | None = None,
                   include_proxies: bool = False,
                   as_of: Any | None = None) -> None:
    """Compute scores + velocity for every registry indicator and cache the
    ``stress_matrix`` and ``stress_velocity`` docs via ``store.put_doc``.

    One bad indicator never kills the run (per-indicator isolation, same
    contract as risk.py's build_summary).
    """
    weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ao = _as_of_date(as_of) if as_of is not None else date.today()

    indicators: dict[str, dict] = {}
    vel_indicators: dict[str, dict] = {}
    gaps: list[dict] = []
    by_cat: dict[str, list[float]] = {}        # category -> indicator scores
    cat_meta: dict[str, list[dict]] = {}       # category -> indicator entries
    # per-category score-change samples for composite velocity
    cat_d: dict[str, dict[str, list[float]]] = {}
    cat_accel: dict[str, list[float]] = {}

    for entry in registry:
        iid = entry["id"]
        try:
            hist = store.points(entry["series_id"])
            sc = score_series(hist, entry["direction"], ao)
            vel = velocity(hist, ao, freq=entry.get("freq", "D"),
                           direction=entry["direction"])
            d_scores = {f"d{k}": score_change(hist, entry["direction"], ao, k)
                        for k in VEL_WINDOWS}

            excluded = ((entry.get("tag") == "proxy" and not include_proxies)
                        or entry.get("freq") == "Q")
            cat = entry["category"]
            if not excluded and sc["score"] is not None:
                by_cat.setdefault(cat, []).append(sc["score"])
            cat_meta.setdefault(cat, []).append(entry)
            if sc["score"] is not None and not excluded:
                for k in VEL_WINDOWS:
                    if d_scores[f"d{k}"] is not None:
                        cat_d.setdefault(cat, {}).setdefault(f"d{k}", []).append(
                            d_scores[f"d{k}"])
                if vel.get("accel") is not None:
                    cat_accel.setdefault(cat, []).append(vel["accel"])

            status = sc["status"]
            if sc["score"] is None:
                reason = ("insufficient history (<252 obs)"
                          if status == "building" else
                          "no data in store" if status == "no_data" else status)
                gaps.append({"id": iid, "reason": reason,
                             "ladder_rung": entry.get("ladder_rung", "n/a")})

            indicators[iid] = {
                "id": iid, "category": cat, "name": entry["name"],
                "unit": entry["unit"], "direction": entry["direction"],
                "freq": entry.get("freq", "D"), "tier": entry.get("tier", "core"),
                "value": sc.get("value"), "as_of": sc.get("as_of"),
                "source": entry.get("source"), "tag": entry.get("tag"),
                "score": sc["score"], "percentile": sc["percentile"],
                "z": sc["z"],
                "d1_score": d_scores["d1"], "d5_score": d_scores["d5"],
                "d20_score": d_scores["d20"], "d60_score": d_scores["d60"],
                "status": status, "window_used": sc["window_used"],
                "confidence": sc.get("confidence"), "history_n": sc["n_obs"],
            }
            vel_indicators[iid] = {
                "raw": {k: vel["raw"].get(k) for k in ("d1", "d5", "d20", "d60")},
                "sigma": {k: vel["sigma"].get(k) for k in ("d1", "d5", "d20", "d60")},
                "pctile": {k: vel["pctile"].get(k) for k in ("d1", "d5", "d20", "d60")},
                "accel": vel["accel"], "status": vel["status"],
            }
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            gaps.append({"id": iid, "reason": f"compute error: {type(exc).__name__}",
                         "ladder_rung": entry.get("ladder_rung", "n/a")})
            indicators[iid] = {"id": iid, "category": entry.get("category"),
                               "name": entry.get("name"), "status": "error",
                               "score": None}
            vel_indicators[iid] = {"status": "error", "accel": None}

    # category composites (equal-weight, >=50% coverage rule)
    composites: dict[str, dict] = {}
    for cat, entries in cat_meta.items():
        n_total = len([e for e in entries
                       if not ((e.get("tag") == "proxy" and not include_proxies)
                               or e.get("freq") == "Q")])
        scores = by_cat.get(cat, [])
        res = composite(scores + [None] * max(0, n_total - len(scores)))
        composites[cat] = res

    overall = overall_gauge(composites, weights)
    as_ofs = [v["as_of"] for v in indicators.values() if v.get("as_of")]
    matrix = {
        "updated_at": now,
        "indicators": indicators,
        "composites": composites,
        "overall": {"score": overall["score"], "regime": overall["regime"],
                    "as_of": max(as_ofs) if as_ofs else None,
                    "coverage": overall["coverage"]},
        "gaps": gaps,
    }

    # ---- velocity doc ----------------------------------------------------
    vel_composites: dict[str, dict] = {}
    for cat, ds in cat_d.items():
        def _mean(vals):
            return sum(vals) / len(vals) if vals else None
        vel_composites[cat] = {
            "d1": _mean(ds.get("d1", [])),
            "d5": _mean(ds.get("d5", [])),
            "d20": _mean(ds.get("d20", [])),
            "sigma5": None,  # filled below from sampled trailing distribution
            "accel": _mean(cat_accel.get(cat, [])),
        }

    overall_d = {"d1": None, "d5": None, "d20": None}
    for k, kk in (("d1", "d1"), ("d5", "d5"), ("d20", "d20")):
        vals = [(weights.get(c, 0.0), vel_composites[c][kk])
                for c in vel_composites if vel_composites[c][kk] is not None
                and composites.get(c, {}).get("status") == "ok"]
        tw = sum(w for w, _ in vals)
        overall_d[k] = (sum(w / tw * v for w, v in vals)
                        if vals and tw > 0 else None)

    speed_sigma = _overall_speed_sigma(store, registry, ao)
    sigma5_overall = (abs(overall_d["d5"]) / speed_sigma
                      if overall_d["d5"] is not None and speed_sigma else None)
    if sigma5_overall is None:
        speed_label = None
    elif sigma5_overall < 0.5:
        speed_label = "Stable"
    elif sigma5_overall < 1.0:
        speed_label = "Drifting"
    elif sigma5_overall < 2.0:
        speed_label = "Rapid"
    else:
        speed_label = "Violent"

    for cat, vc in vel_composites.items():
        if vc["d5"] is not None and speed_sigma:
            vc["sigma5"] = abs(vc["d5"]) / speed_sigma
        # per-category accel sigma-move uses the same overall speed sigma scale

    overall_accel_vals = [a for cat in cat_accel for a in cat_accel[cat]
                          if composites.get(cat, {}).get("status") == "ok"]
    breadth_n = sum(1 for v in indicators.values() if v.get("score") is not None)
    breadth = {
        "share_above_75": (sum(1 for v in indicators.values()
                                if (v.get("score") or 0) > 75) / breadth_n
                            if breadth_n else None),
        "share_fast_90": (sum(1 for v in vel_indicators.values()
                               if (v.get("pctile") or {}).get("d5") is not None
                               and v["pctile"]["d5"] > 90) / breadth_n
                           if breadth_n else None),
        "n": breadth_n,
    }

    velocity_doc = {
        "updated_at": now,
        "indicators": vel_indicators,
        "composites": vel_composites,
        "overall": {"d1": overall_d["d1"], "d5": overall_d["d5"],
                    "d20": overall_d["d20"], "sigma5": sigma5_overall,
                    "accel": (sum(overall_accel_vals) / len(overall_accel_vals)
                              if overall_accel_vals else None),
                    "speed_label": speed_label},
        "breadth": breadth,
    }

    store.put_doc("stress_matrix", matrix, source=SOURCE_LABEL)
    store.put_doc("stress_velocity", velocity_doc, source=SOURCE_LABEL)
