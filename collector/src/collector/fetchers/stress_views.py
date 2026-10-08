"""Stress Monitor v2 — Phase 2: Views 2,3,4,5,7 payload builders (WS1).

Stdlib only. The scoring kernel below mirrors the conventions of
collector.stress_score (inclusive rank percentile, direction-adjusted,
expanding <=5Y window, no look-ahead) WITHOUT importing it: the Phase-1
scoring and registry modules are not on origin/main, so this module depends
on nothing but the store and stdlib. The scheduler injects the INDICATORS
registry and STRESS_WEIGHTS at call time (see scheduler.py's stress job).

Score history is reconstructed by re-running the kernel per week with
``as_of`` at-or-before each sampled observation — no look-ahead by
construction. Missing history yields None ("n/a"), never 0, and nothing is
interpolated or forward-filled.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

SOURCE_LABEL = "stress-v2"

# ---------------------------------------------------------------------------
# constants (kernel mirrors collector.stress_score conventions)
# ---------------------------------------------------------------------------
HORIZON_WINDOWS = (("1M", 21), ("3M", 63), ("1Y", 252), ("5Y", 1260))
MIN_HORIZON_OBS = 10          # percentile needs at least this many obs
SCORE_MIN_OBS = 252           # mirrors stress_score.MIN_OBS_SCORE
SCORE_MAX_WINDOW = 5 * 252    # mirrors stress_score.MAX_WINDOW
MAX_SAMPLES = 260             # ~5Y of weekly samples
VEL_K = 5                     # 5-observation velocity (mirrors velocity k=5)
TRAIL_1Y = 252                # trailing-1Y baseline (mirrors stress_score)
MIN_CHANGES = 20              # mirrors stress_score.MIN_CHANGES
CONTAGION_ROLL_WEEKS = 60     # rolling correlation window (weeks)
CONTAGION_MIN_PAIRS = 30      # min overlapping obs for a current corr cell
CONTAGION_FLAG_TRAIL = 52     # trailing weeks for the 90th-pctile baseline
DIV_CORR_MIN_WEEKS = 40       # min overlapping weeks for a 1Y pair corr
DIV_GAP_TRAIL = 52            # trailing weeks for gap history / percentile
DIV_MAX_PAIRS = 150           # doc-size cap on reported divergence pairs
QUADRANT_HIGH = 50.0          # score >= 50 counts as "high"

# Episodes. Verbatim copy of EPISODES from ~/workspace/velocity/velocity.py
# (that file is the source of truth; do not edit here — the prompt forbids
# hard-coding an episode date unless a source confirms it, and that file is
# the desk's confirmed episode registry).
# (name, start, finish, trigger)
EPISODES = [
    ("2007 grind", "2007-06-01", "2007-12-31", "Bear Stearns subprime funds implode (Jun 2007)"),
    ("GFC",        "2008-09-01", "2008-12-31", "Fannie/Freddie conservatorship Sep 7; Lehman Sep 15"),
    ("Covid",      "2020-02-20", "2020-03-23", "S&P 500 all-time high Feb 19; crash into Fed bazooka Mar 23"),
    ("2022 hikes", "2022-06-01", "2022-10-31", "QT begins Jun 1; HY peak Jul; gilt crisis Sep-Oct"),
    ("SVB",        "2023-03-08", "2023-03-31", "SVB capital-raise announcement Mar 8 (failed Mar 10)"),
    ("Tariffs",    "2025-03-01", "2025-04-09", "growth-scare widening into Liberation Day; 90-day pause Apr 9"),
    ("Liberation Day", "2025-04-02", "2025-04-09", "reciprocal tariff announcement after close Apr 2; pause Apr 9"),
    ("Iran war",   "2026-03-01", "2026-03-31", "oil/rate spike (per desk records)"),
]

# ---------------------------------------------------------------------------
# history normalization (same contract as stress_score: date-keyed, sorted)
# ---------------------------------------------------------------------------


def _to_date(d: Any) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def _sorted_obs(history: dict[Any, float]) -> tuple[list[date], list[float]]:
    pairs = sorted((_to_date(d), float(v)) for d, v in history.items())
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _last_le(dates: list[date], ao: date) -> int:
    lo, hi, ans = 0, len(dates) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= ao:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


# ---------------------------------------------------------------------------
# scoring kernel (mirrors stress_score._score_at; parameterized min_obs)
# ---------------------------------------------------------------------------


def _kernel_score(values: list[float], idx: int, direction: str,
                  max_window: int = SCORE_MAX_WINDOW,
                  min_obs: int = SCORE_MIN_OBS) -> dict:
    """Direction-adjusted inclusive-rank percentile of values[idx] vs the
    ``max_window`` most recent observations at-or-before idx. Positions
    after idx are never touched (no look-ahead)."""
    start = max(0, idx - max_window + 1)
    window = values[start:idx + 1]
    n = len(window)
    if n < min_obs:
        return {"score": None, "n": n, "status": "building"}
    latest = values[idx]
    if direction == "+":
        pct = 100.0 * sum(1 for x in window if x <= latest) / n
        score = pct
    elif direction == "-":
        pct = 100.0 * sum(1 for x in window if x <= latest) / n
        score = 100.0 - pct
    elif direction == "\u00b1":
        med = statistics.median(window)
        sd = statistics.pstdev(window)
        if sd == 0:
            score = 0.0
        else:
            zs = [abs((x - med) / sd) for x in window]
            zl = abs((latest - med) / sd)
            score = 100.0 * sum(1 for z in zs if z <= zl) / n
    else:
        raise ValueError(f"unknown direction {direction!r}")
    return {"score": score, "n": n, "status": "ok"}


def _vel_sigma5(values: list[float], idx: int, direction: str) -> float | None:
    """5-observation normalized velocity (mirrors stress_score.velocity k=5).

    raw = sign * (v[i] - v[i-5]); sigma = raw / trailing-1Y pstdev of
    5-changes. None when there are too few observations (never 0)."""
    k = VEL_K
    if idx - k < 0:
        return None
    sign = -1.0 if direction == "-" else 1.0
    raw = sign * (values[idx] - values[idx - k])
    changes = []
    for j in range(max(k, idx - TRAIL_1Y + 1), idx + 1):
        changes.append(values[j] - values[j - k])
    if len(changes) < MIN_CHANGES:
        return None
    sd = statistics.pstdev(changes)
    if sd == 0:
        return 0.0 if raw == 0 else None
    return raw / sd


def _accel5(values: list[float], idx: int, direction: str) -> float | None:
    """Change in 5-observation velocity over the last 5 observations
    (mirrors stress_score.velocity's accel)."""
    now = _vel_sigma5(values, idx, direction)
    if now is None or idx - VEL_K < 0:
        return None
    then = _vel_sigma5(values, idx - VEL_K, direction)
    return None if then is None else now - then


# ---------------------------------------------------------------------------
# small stats helpers
# ---------------------------------------------------------------------------


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sdx, sdy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sdx == 0 or sdy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    return cov / (sdx * sdy)


def _rank_pctl(value: float, history: list[float]) -> float | None:
    """Inclusive rank percentile of value vs history (same convention as the
    kernel)."""
    if not history:
        return None
    return 100.0 * sum(1 for h in history if h <= value) / len(history)


def _weekly_positions(n_obs: int, freq: str) -> list[int]:
    """Newest-first observation indexes for weekly sampling. Daily series
    step 5 observations (trading week); W/M/Q/E step 1 (already sparse).
    Capped at MAX_SAMPLES (~5Y)."""
    step = 5 if freq == "D" else 1
    pos = list(range(n_obs - 1, -1, -step))[:MAX_SAMPLES]
    return pos


# ---------------------------------------------------------------------------
# indicator selection + weekly score histories
# ---------------------------------------------------------------------------


def _scored(entry: dict) -> bool:
    return entry.get("series_id") is not None


def _composite_eligible(entry: dict) -> bool:
    """Mirrors refresh_stress exclusions (include_proxies=False): no proxies,
    no quarterly series."""
    return entry.get("tag") != "proxy" and entry.get("freq") != "Q"


def _load(store: Any, registry: list[dict]) -> dict[str, tuple]:
    """id -> (entry, dates, values) for indicators with a real series."""
    out: dict[str, tuple] = {}
    for entry in registry:
        if not _scored(entry):
            continue
        try:
            dates, values = _sorted_obs(store.points(entry["series_id"]))
        except Exception:  # noqa: BLE001 — one bad series never kills the run
            log.warning("stress_views: cannot read %s", entry["series_id"])
            continue
        if dates:
            out[entry["id"]] = (entry, dates, values)
    return out


def _weekly_scores(hists: dict[str, tuple], as_of: date) -> dict[str, dict]:
    """id -> {"entry": entry, "scores": [(iso_date, score|None), ...]},
    oldest-first, weekly-sampled, kernel re-run per week (no look-ahead)."""
    out: dict[str, dict] = {}
    for iid, (entry, dates, values) in hists.items():
        series: list[tuple[str, float | None]] = []
        for p in _weekly_positions(len(dates), entry.get("freq", "D")):
            if dates[p] > as_of:
                continue
            res = _kernel_score(values, p, entry["direction"])
            series.append((dates[p].isoformat(), res["score"]))
        series.reverse()
        out[iid] = {"entry": entry, "scores": series}
    return out


def _category_composite_series(weekly: dict[str, dict],
                               category: str) -> list[tuple[str, float | None]]:
    """Equal-weight weekly composite of composite-eligible indicators in one
    category; a week is None unless >=50% of the category's eligible
    indicators have a score (mirrors stress_score.composite coverage rule)."""
    members = [(iid, w) for iid, w in weekly.items()
               if w["entry"]["category"] == category
               and _composite_eligible(w["entry"])]
    if not members:
        return []
    n_total = len(members)
    grid = sorted({d for _, w in members for d, _ in w["scores"]})
    by_id = {iid: dict(w["scores"]) for iid, w in members}
    out = []
    for d in grid:
        vals = [by_id[iid][d] for iid in by_id
                if d in by_id[iid] and by_id[iid][d] is not None]
        out.append((d, sum(vals) / len(vals)
                    if vals and len(vals) / n_total >= 0.5 else None))
    return out


def _overall_series(comps: dict[str, list],
                    weights: dict[str, float]) -> list[tuple[str, float | None]]:
    """Weighted-mean overall series, reweighted when a category is dropped
    for low coverage (mirrors stress_score.overall_gauge)."""
    grid = sorted({d for s in comps.values() for d, _ in s})
    by_cat = {c: dict(s) for c, s in comps.items()}
    out = []
    for d in grid:
        avail = {c: by_cat[c][d] for c in by_cat
                 if d in by_cat[c] and by_cat[c][d] is not None}
        tw = sum(weights.get(c, 0.0) for c in avail)
        if avail and tw > 0:
            out.append((d, sum(weights.get(c, 0.0) / tw * v
                               for c, v in avail.items())))
        else:
            out.append((d, None))
    return out


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# View 2: horizon heatmap — percentile vs 1M/3M/1Y/5Y (or longest available)
# ---------------------------------------------------------------------------


def horizon_payload(store: Any, registry: list[dict],
                    weights: dict[str, float] | None = None,
                    as_of: Any | None = None) -> dict:
    ao = _to_date(as_of) if as_of is not None else date.today()
    indicators: dict[str, dict] = {}
    for entry in registry:
        iid = entry["id"]
        row = {
            "id": iid, "category": entry.get("category"),
            "name": entry.get("name"), "unit": entry.get("unit"),
            "direction": entry.get("direction"), "freq": entry.get("freq", "D"),
            "tier": entry.get("tier", "core"), "tag": entry.get("tag"),
        }
        if not _scored(entry):
            row.update({"status": "no_data", "reason": entry.get("reason") or
                        "no free or reachable source"})
            indicators[iid] = row
            continue
        try:
            dates, values = _sorted_obs(store.points(entry["series_id"]))
        except Exception as exc:  # noqa: BLE001
            row.update({"status": "error",
                        "reason": f"read error: {type(exc).__name__}"})
            indicators[iid] = row
            continue
        i = _last_le(dates, ao)
        if i < 0:
            row.update({"status": "no_data", "reason": "no observations"})
            indicators[iid] = row
            continue
        row["as_of"] = dates[i].isoformat()
        row["value"] = values[i]
        ok = True
        for label, wobs in HORIZON_WINDOWS:
            res = _kernel_score(values, i, entry["direction"],
                                max_window=wobs, min_obs=MIN_HORIZON_OBS)
            row[f"pctl_{label}"] = res["score"]
            row[f"n_{label}"] = res["n"]
            if res["status"] != "ok":
                ok = False
        row["status"] = "ok" if ok else "building"
        indicators[iid] = row
    return {
        "updated_at": _now_utc(), "as_of": ao.isoformat(),
        "windows": {label: wobs for label, wobs in HORIZON_WINDOWS},
        "min_obs": MIN_HORIZON_OBS,
        "note": ("Direction-adjusted percentile of the latest observation vs "
                 "its own trailing window (or the longest window that "
                 "exists). 100 = most stressed in that window. No look-ahead; "
                 "missing windows are n/a, never interpolated."),
        "indicators": indicators,
    }


# ---------------------------------------------------------------------------
# View 3: contagion matrix — categories x categories rolling correlation
# ---------------------------------------------------------------------------


def contagion_payload(store: Any, registry: list[dict],
                      weights: dict[str, float] | None = None,
                      as_of: Any | None = None) -> dict:
    ao = _to_date(as_of) if as_of is not None else date.today()
    hists = _load(store, registry)
    weekly = {iid: w for iid, w in _weekly_scores(hists, ao).items()
              if _composite_eligible(w["entry"])}
    categories = sorted({w["entry"]["category"] for w in weekly.values()})
    comps = {c: _category_composite_series(weekly, c) for c in categories}

    # weekly changes of each composite
    changes: dict[str, list[tuple[str, float]]] = {}
    for c, series in comps.items():
        ch = []
        for (d0, s0), (d1, s1) in zip(series, series[1:]):
            if s0 is not None and s1 is not None:
                ch.append((d1, s1 - s0))
        changes[c] = ch

    def rolling_corr(c1: str, c2: str, end: int,
                     weeks: int = CONTAGION_ROLL_WEEKS) -> float | None:
        """Correlation of c1/c2 changes over the `weeks` weeks ending at
        position `end` in c1's change series (date-aligned)."""
        s1 = changes.get(c1, [])
        if end > len(s1) or end <= 0:
            return None
        win1 = s1[max(0, end - weeks):end]
        dmap2 = dict(changes.get(c2, []))
        xs = [v for d, v in win1 if d in dmap2]
        ys = [dmap2[d] for d, _ in win1 if d in dmap2]
        if len(xs) < CONTAGION_MIN_PAIRS:
            return None
        return _pearson(xs, ys)

    n_end = max((len(changes[c]) for c in categories), default=0)
    matrix: dict[str, dict[str, float | None]] = {
        c1: {c2: (1.0 if c1 == c2 else rolling_corr(c1, c2, n_end))
             for c2 in categories}
        for c1 in categories
    }
    # mean off-diagonal correlation series (for the flag baseline).
    # The reference grid is the change series of the category with the most
    # observations; each window's date is that grid's last windowed date.
    ref = max(categories, key=lambda c: len(changes.get(c, [])),
              default=None)
    ref_changes = changes.get(ref, []) if ref else []
    mean_series: list[tuple[str, float]] = []
    for end in range(CONTAGION_ROLL_WEEKS, len(ref_changes) + 1):
        cells = [rolling_corr(c1, c2, end)
                 for i, c1 in enumerate(categories)
                 for c2 in categories[i + 1:]]
        cells = [c for c in cells if c is not None]
        if cells:
            mean_series.append((ref_changes[end - 1][0],
                                sum(cells) / len(cells)))

    # flag: current mean cross-category corr vs its 1Y 90th percentile
    trail = [v for _, v in mean_series[-CONTAGION_FLAG_TRAIL:]]
    current_mean = mean_series[-1][1] if mean_series else None
    p90 = None
    if len(trail) >= 10 and current_mean is not None:
        p90 = sorted(trail)[int(0.9 * (len(trail) - 1))]
    flagged = (current_mean is not None and p90 is not None
               and current_mean > p90)

    # stress breadth: weekly share of scored indicators above 75
    weekly_all = _weekly_scores(hists, ao)
    grid = sorted({d for w in weekly_all.values() for d, _ in w["scores"]})
    by_id = {iid: dict(w["scores"]) for iid, w in weekly_all.items()}
    breadth_dates, breadth_vals = [], []
    for d in grid:
        vals = [by_id[iid][d] for iid in by_id
                if d in by_id[iid] and by_id[iid][d] is not None]
        if vals:
            breadth_dates.append(d)
            breadth_vals.append(sum(1 for v in vals if v > 75) / len(vals))

    return {
        "updated_at": _now_utc(), "as_of": ao.isoformat(),
        "categories": categories,
        "window": (f"{CONTAGION_ROLL_WEEKS}-week rolling Pearson correlation "
                   "of weekly composite-score changes (weekly sampling; "
                   "blue=-1 white=0 red=+1)"),
        "matrix": matrix,
        "mean_cross_corr": current_mean,
        "mean_cross_corr_p90_1y": p90,
        "flagged": flagged,
        "flag_reason": ("mean cross-category correlation above its 1Y 90th "
                        "percentile" if flagged else None),
        "breadth": {"dates": breadth_dates, "share_above_75": breadth_vals},
        "note": ("Scores are re-run per week from stored history (no "
                 "look-ahead); proxies and quarterly series are excluded "
                 "from composites, matching the scoring engine."),
    }


# ---------------------------------------------------------------------------
# View 4: divergence map — pairs that normally move together, now apart
# ---------------------------------------------------------------------------


def divergence_payload(store: Any, registry: list[dict],
                       weights: dict[str, float] | None = None,
                       as_of: Any | None = None) -> dict:
    ao = _to_date(as_of) if as_of is not None else date.today()
    hists = _load(store, registry)
    weekly = _weekly_scores(hists, ao)
    by_id = {iid: dict(w["scores"]) for iid, w in weekly.items()}

    def trail_pair(iid_a: str, iid_b: str,
                   weeks: int = DIV_GAP_TRAIL) -> list[tuple[str, float, float]]:
        sa = by_id.get(iid_a, {})
        sb = by_id.get(iid_b, {})
        common = sorted(set(sa) & set(sb))[-weeks:]
        return [(d, sa[d], sb[d]) for d in common
                if sa[d] is not None and sb[d] is not None]

    pairs: list[dict] = []
    ids = list(weekly)
    for n in range(len(ids)):
        for m in range(n + 1, len(ids)):
            a, b = ids[n], ids[m]
            ea, eb = weekly[a]["entry"], weekly[b]["entry"]
            if ea["category"] != eb["category"]:
                continue
            trail = trail_pair(a, b)
            if len(trail) < DIV_CORR_MIN_WEEKS:
                continue
            corr = _pearson([x[1] for x in trail], [x[2] for x in trail])
            if corr is None or corr <= 0.7:
                continue
            gap_now = abs(trail[-1][1] - trail[-1][2])
            gap_hist = [abs(x[1] - x[2]) for x in trail[:-1]]
            gap_pctl = _rank_pctl(gap_now, gap_hist) if len(gap_hist) >= 20 else None
            pairs.append({
                "a_id": a, "a_name": ea.get("name"), "b_id": b,
                "b_name": eb.get("name"), "category": ea.get("category"),
                "a_tier": ea.get("tier"), "b_tier": eb.get("tier"),
                "corr_1y": corr, "score_a": trail[-1][1],
                "score_b": trail[-1][2], "gap": gap_now,
                "gap_pctl_1y": gap_pctl, "n_weeks": len(trail),
                "as_of": trail[-1][0],
            })
    pairs.sort(key=lambda p: p["gap"], reverse=True)
    pairs = pairs[:DIV_MAX_PAIRS]
    return {
        "updated_at": _now_utc(), "as_of": ao.isoformat(),
        "pairs": pairs,
        "note": ("Same-category indicator pairs with 1Y score correlation "
                 "> 0.7; gap = |score_a - score_b|, colored by the gap's own "
                 "1Y percentile; biggest gaps first. Proxies included (e.g. "
                 "VIX vs MOVE). Scores re-run per week, no look-ahead."),
    }


# ---------------------------------------------------------------------------
# View 5: episode replay — weekly Overall + composite scores, 5Y slider
# ---------------------------------------------------------------------------


def replay_payload(store: Any, registry: list[dict],
                   weights: dict[str, float] | None = None,
                   as_of: Any | None = None) -> dict:
    ao = _to_date(as_of) if as_of is not None else date.today()
    weights = dict(weights) if weights else {}
    hists = _load(store, registry)
    weekly = {iid: w for iid, w in _weekly_scores(hists, ao).items()
              if _composite_eligible(w["entry"])}
    categories = sorted({w["entry"]["category"] for w in weekly.values()})
    comps = {c: _category_composite_series(weekly, c) for c in categories}
    overall = _overall_series(comps, weights)
    dates = [d for d, _ in overall]
    by_cat = {c: dict(s) for c, s in comps.items()}
    return {
        "updated_at": _now_utc(), "as_of": ao.isoformat(),
        "dates": dates,
        "overall": [v for _, v in overall],
        "composites": {c: [by_cat[c].get(d) for d in dates]
                       for c in categories},
        "episodes": [{"name": n, "start": s, "finish": f, "trigger": t}
                     for n, s, f, t in EPISODES],
        "n_weeks": len(dates),
        "note": ("Weekly Overall and category-composite scores (equal-weight "
                 "composites, weighted overall with reweighting on low "
                 "coverage), re-run per week from stored history with no "
                 "look-ahead. Episode windows are the desk's confirmed "
                 "registry; values before a series reaches 252 obs are n/a."),
    }


# ---------------------------------------------------------------------------
# View 7: level vs velocity quadrant
# ---------------------------------------------------------------------------


def quadrant_payload(store: Any, registry: list[dict],
                     weights: dict[str, float] | None = None,
                     as_of: Any | None = None) -> dict:
    ao = _to_date(as_of) if as_of is not None else date.today()
    hists = _load(store, registry)
    points: list[dict] = []
    counts = {"Escalating": 0, "Emerging": 0, "Unwinding": 0, "Calm": 0,
              "n/a": 0}
    for iid, (entry, dates, values) in hists.items():
        i = _last_le(dates, ao)
        pt = {
            "id": iid, "category": entry.get("category"),
            "name": entry.get("name"), "unit": entry.get("unit"),
            "tier": entry.get("tier", "core"), "tag": entry.get("tag"),
            "as_of": dates[i].isoformat() if i >= 0 else None,
            "value": values[i] if i >= 0 else None,
            "score": None, "sigma5": None, "accel": None,
            "quadrant": "n/a", "reason": None,
        }
        if i < 0:
            pt["reason"] = "no data"
        else:
            res = _kernel_score(values, i, entry["direction"])
            pt["score"] = res["score"]
            if res["status"] != "ok":
                pt["reason"] = f"building ({res['n']} obs)"
            elif entry.get("freq") == "Q":
                pt["reason"] = "quarterly: no velocity"
            else:
                pt["sigma5"] = _vel_sigma5(values, i, entry["direction"])
                pt["accel"] = _accel5(values, i, entry["direction"])
                if pt["sigma5"] is None:
                    pt["reason"] = "insufficient history for 5d sigma"
        if pt["score"] is not None and pt["sigma5"] is not None:
            high = pt["score"] >= QUADRANT_HIGH
            rising = pt["sigma5"] > 0
            pt["quadrant"] = ("Escalating" if high and rising else
                              "Unwinding" if high and not rising else
                              "Emerging" if not high and rising else "Calm")
        counts[pt["quadrant"]] += 1
        points.append(pt)
    return {
        "updated_at": _now_utc(), "as_of": ao.isoformat(),
        "points": points,
        "counts": counts,
        "emerging_escalating": counts["Emerging"] + counts["Escalating"],
        "note": ("x = 0-100 score, y = 5d normalized velocity "
                 "(raw 5-obs change / trailing-1Y sigma), size = "
                 "|acceleration|. Escalating = high score & rising; "
                 "Unwinding = high & falling; Emerging = low & rising "
                 "(early warning); Calm = low & falling. No look-ahead; "
                 "quarterly series carry no velocity."),
    }


# ---------------------------------------------------------------------------
# scheduler entry point: build + cache all five docs, per-view isolation
# ---------------------------------------------------------------------------

_BUILDERS = (
    ("stress_horizon", horizon_payload),
    ("stress_contagion", contagion_payload),
    ("stress_divergence", divergence_payload),
    ("stress_replay", replay_payload),
    ("stress_quadrant", quadrant_payload),
)


def refresh_stress_views(store: Any, registry: list[dict],
                         weights: dict[str, float] | None = None,
                         as_of: Any | None = None) -> dict[str, str]:
    """Build the five Phase-2 view payloads and cache them as docs via
    store.put_doc. One bad view never blocks the others (per-view isolation,
    same contract as stress_score.refresh_stress). Returns per-view status."""
    weights = dict(weights) if weights else {}
    status: dict[str, str] = {}
    for doc_key, builder in _BUILDERS:
        try:
            payload = builder(store, registry, weights, as_of)
            store.put_doc(doc_key, payload, source=SOURCE_LABEL)
            status[doc_key] = "ok"
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            log.warning("stress_views: %s failed: %s: %s",
                        doc_key, type(exc).__name__, exc, exc_info=exc)
            status[doc_key] = f"error: {type(exc).__name__}"
    return status
