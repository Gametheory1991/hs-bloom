"""Stress Monitor v2 validation panel (Phase 2, WS2). Spec section 10.

``validation_payload(store) -> dict`` builds the info-panel payload for
GET /api/stress/validation (wired by WS1).

Sections:
  correlations   OSM Overall vs OFR FSI and STLFSI4 over 1Y and 5Y.
  lead_lag       Correlations at lags 0/1/2/5/10 trading days for OSM level
                 and OSM velocity vs each official index.
  banner         Flags when OSM disagrees with an official index by > 20
                 points on a common 0-100 percentile scale (official indexes
                 are percentile-normalized first; the normalization is
                 documented in the payload).
  sensitivity    Category weights shifted +/-5 points (score change);
                 velocity recomputed with 3d/5d/10d windows (top-10 fastest
                 ranking stability via rank correlation).
  false_alarm_rate  Share of "Fast move" alerts in the last 1Y not followed
                 by a higher composite within 10 days (from the alerts state
                 doc written by stress_alerts.evaluate_alerts).

Never invents data: any section lacking sufficient history reports
"insufficient history" with a reason. stdlib only.

Imports: ``collector.stress_score`` (the Phase-1 engine API: same scoring /
composite conventions, no reimplementation drift). Category weights come
from ``collector.fetchers.stress.STRESS_WEIGHTS`` when importable, else an
equal-weight fallback that is labeled in the payload.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

from collector import stress_score

STATE_DOC = "stress_alerts_state"
SOURCE_LABEL = "stress-v2-validation"

# Official-index series resolution. ids/series_ids are the Tier-1 registry
# facts from collector/fetchers/stress.py (read-only here).
OFFICIAL = {
    "ofr_fsi": {"name": "OFR Financial Stress Index",
                "series_candidates": ["cycle:ofr-fsi"]},
    "stlfsi4": {"name": "St. Louis Fed Financial Stress Index (STLFSI4)",
                "series_candidates": ["cycle:stlfsi"]},
}

MIN_OBS_SCORE = stress_score.MIN_OBS_SCORE  # 252: engine's scoring floor
MAX_WINDOW = stress_score.MAX_WINDOW        # 5 trading years
HISTORY_1Y_DAYS = 365
HISTORY_5Y_DAYS = 5 * 365
MIN_PAIRS_1Y = 30     # minimum aligned pairs for a 1Y correlation
MIN_PAIRS_5Y = 100    # minimum aligned pairs for a 5Y correlation
MIN_PAIRS_LAG = 30    # minimum pairs per lead/lag cell
BANNER_THRESHOLD = 20.0  # points on the common 0-100 scale
WEIGHT_SHIFT = 0.05   # +/-5 points of the 100-point weight scale
STABLE_RHO = 0.7      # rank-correlation stability threshold (documented)
STABLE_OVERLAP = 7    # top-10 overlap stability threshold (documented)
FALSE_ALARM_WINDOW_DAYS = 10
FALSE_ALARM_LOOKBACK_DAYS = 365


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r, or None when undefined (constant series / <2 points)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation via Pearson on rank vectors."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None

    def _ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        ranks = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    return _pearson(_ranks(xs), _ranks(ys))


def _category_weights() -> tuple[dict[str, float], str]:
    """(weights summing to 1.0, source label)."""
    try:
        from collector.fetchers.stress import STRESS_WEIGHTS  # noqa: PLC0415
        total = sum(STRESS_WEIGHTS.values())
        if total > 0:
            return ({c: v / total for c, v in STRESS_WEIGHTS.items()},
                    "STRESS_WEIGHTS")
    except Exception:  # noqa: BLE001 - fall back to equal weights, labeled
        pass
    return ({}, "equal_fallback")


# ---------------------------------------------------------------------------
# series access
# ---------------------------------------------------------------------------

def _resolve_points(store: Any, iid: str,
                    extra_candidates: list[str] | None = None) -> tuple[str | None, dict]:
    """Find a stored series for indicator ``iid``; never invents data."""
    candidates = list(extra_candidates or [])
    candidates += [f"cycle:{iid}", f"cycle:{iid.replace('_', '-')}",
                   f"stress:{iid}"]
    for sid in dict.fromkeys(candidates):
        try:
            pts = store.points(sid)
        except Exception:  # noqa: BLE001 - try next candidate
            continue
        if pts:
            return sid, pts
    return None, {}


def _sorted_xy(points: dict) -> tuple[list[date], list[float]]:
    dates, values = stress_score._sorted_obs(points)  # noqa: SLF001 - engine API
    return dates, values


def _sample_dates(all_dates: list[date], end: date, days_back: int,
                  step: int) -> list[date]:
    """Every ``step``-th date within [end - days_back, end]."""
    start = end - timedelta(days=days_back)
    window = [d for d in all_dates if start <= d <= end]
    return window[::step] or window[-1:]


# ---------------------------------------------------------------------------
# OSM overall history reconstruction
# ---------------------------------------------------------------------------

def _osm_history(store: Any, matrix: dict, weights: dict,
                 end: date, days_back: int = HISTORY_5Y_DAYS,
                 step: int = 5) -> tuple[list[tuple[date, float]], dict]:
    """Reconstruct the OSM Overall score history from indicator histories.

    For each sampled date: per-indicator 0-100 scores via the engine's
    ``_score_at`` (same conventions as the cached matrix: expanding window,
    252-obs floor, direction adjustment), equal-weight category composites
    with the engine's >=50% coverage rule, then the engine's
    ``overall_gauge`` with the given weights. One pass only; no look-ahead.
    """
    indicators = matrix.get("indicators") or {}
    # Eligible members mirror the engine's composite inclusion rule
    # (proxies and quarterly series are excluded from composites).
    members: list[tuple[str, str, str]] = []  # (iid, category, direction)
    series: dict[str, tuple[list[date], list[float]]] = {}
    unresolved: list[str] = []
    for iid, ind in indicators.items():
        if not isinstance(ind, dict):
            continue
        if ind.get("tag") == "proxy" or ind.get("freq") == "Q":
            continue
        _sid, pts = _resolve_points(store, iid)
        if not pts:
            unresolved.append(iid)
            continue
        dates, values = _sorted_xy(pts)
        if len(dates) < MIN_OBS_SCORE:
            unresolved.append(iid)
            continue
        members.append((iid, ind.get("category") or "?", ind.get("direction") or "+"))
        series[iid] = (dates, values)

    meta: dict[str, Any] = {"n_members": len(members),
                           "n_unresolved": len(unresolved)}
    if not members:
        return [], meta

    all_dates = sorted({d for dates, _ in series.values() for d in dates
                        if d <= end})
    sample = _sample_dates(all_dates, end, days_back, step)
    meta["n_sampled_dates"] = len(sample)

    cats: dict[str, list[str]] = {}
    for iid, cat, _dir in members:
        cats.setdefault(cat, []).append(iid)

    history: list[tuple[date, float]] = []
    for d in sample:
        composites: dict[str, dict] = {}
        for cat, iids in cats.items():
            scores: list[float | None] = []
            for iid in iids:
                dates, values = series[iid]
                idx = stress_score._last_le(dates, d)  # noqa: SLF001
                if idx < 0:
                    scores.append(None)
                    continue
                _dir = next(dd for i, _c, dd in members if i == iid)
                res = stress_score._score_at(dates, values, idx, _dir)  # noqa: SLF001
                scores.append(res["score"] if res["status"] == "ok" else None)
            n_total = len(iids)
            res_c = stress_score.composite(
                scores + [None] * max(0, n_total - len(scores)))
            composites[cat] = res_c
        gauge = stress_score.overall_gauge(composites, weights)
        if gauge["score"] is not None:
            history.append((d, gauge["score"]))
    meta["n_history_points"] = len(history)
    return history, meta


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def _common_pairs(xs: dict[date, float], ys: dict[date, float],
                  start: date, end: date) -> tuple[list[float], list[float]]:
    keys = sorted(set(xs) & set(ys))
    keys = [k for k in keys if start <= k <= end]
    return [xs[k] for k in keys], [ys[k] for k in keys]


def _section_correlations(store: Any, matrix: dict, osm_hist: list,
                          end: date) -> dict:
    out: dict[str, Any] = {}
    osm = dict(osm_hist)
    for key, spec in OFFICIAL.items():
        _sid, pts = _resolve_points(store, key, spec["series_candidates"])
        entry: dict[str, Any] = {"name": spec["name"], "series_id": _sid}
        if not pts:
            entry["status"] = "insufficient history"
            entry["reason"] = "official index has no stored history"
            out[key] = entry
            continue
        dates, values = _sorted_xy(pts)
        off = dict(zip(dates, values))
        entry["n_obs"] = len(dates)
        entry["as_of"] = dates[-1].isoformat()
        for label, days_back, min_pairs in (("1Y", HISTORY_1Y_DAYS, MIN_PAIRS_1Y),
                                            ("5Y", HISTORY_5Y_DAYS, MIN_PAIRS_5Y)):
            start = end - timedelta(days=days_back)
            xs, ys = _common_pairs(osm, off, start, end)
            cell: dict[str, Any] = {"n": len(xs)}
            if len(xs) < min_pairs:
                cell["r"] = "insufficient history"
                cell["reason"] = (f"only {len(xs)} common observations "
                                  f"(need {min_pairs})")
            else:
                cell["r"] = _pearson(xs, ys)
            entry[label] = cell
        out[key] = entry
    return out


def _section_lead_lag(osm_hist: list, official: dict[date, list],
                      end: date) -> dict:
    """Correlations at lags 0/1/2/5/10 trading days.

    Positive lag L means OSM *leads* the official index by L trading days:
    pairs are (OSM[t_i], official[t_{i+L}]) on the common trading calendar.
    OSM velocity = 5-observation change of the OSM overall score; official
    velocity = 5-observation change of the raw official index (both "+" =
    more stress, so changes are directly comparable).
    """
    out: dict[str, Any] = {}
    osm = dict(osm_hist)
    for key, off in official.items():
        common = sorted(set(osm) & set(off))
        entry: dict[str, Any] = {"n_common": len(common), "level": {},
                                 "velocity": {}}
        if len(common) < MIN_PAIRS_LAG + 10:
            entry["status"] = "insufficient history"
            entry["reason"] = (f"only {len(common)} common observations")
            out[key] = entry
            continue
        ox = [osm[d] for d in common]
        oy = [off[d] for d in common]
        # 5-observation changes aligned to the same common calendar
        vx = [ox[i] - ox[i - 5] for i in range(5, len(ox))]
        vy = [oy[i] - oy[i - 5] for i in range(5, len(oy))]
        for lag in (0, 1, 2, 5, 10):
            cell_l: dict[str, Any] = {}
            xs, ys = ox[:len(ox) - lag], oy[lag:]
            cell_l["n"] = len(xs)
            cell_l["r"] = (_pearson(xs, ys) if len(xs) >= MIN_PAIRS_LAG
                           else "insufficient history")
            entry["level"][str(lag)] = cell_l
            cell_v: dict[str, Any] = {}
            xs, ys = vx[:len(vx) - lag], vy[lag:]
            cell_v["n"] = len(xs)
            cell_v["r"] = (_pearson(xs, ys) if len(xs) >= MIN_PAIRS_LAG
                           else "insufficient history")
            entry["velocity"][str(lag)] = cell_v
        out[key] = entry
    return out


def _percentile_normalize(points: dict, end: date) -> dict:
    """Map an official index to a 0-100 stress scale.

    Normalization: rank percentile of the latest observation against its own
    expanding history (up to 5 trading years, minimum 252 observations),
    direction "+" (higher index value = more stress). This is the same
    rank-percentile convention the OSM engine uses for indicator scores,
    which is what makes the two comparable on a common 0-100 scale.
    """
    dates, values = _sorted_xy(points)
    idx = stress_score._last_le(dates, end)  # noqa: SLF001
    if idx < 0:
        return {"percentile": None, "reason": "no observations at or before as_of"}
    start = max(0, idx - MAX_WINDOW + 1)
    window = values[start:idx + 1]
    n = len(window)
    if n < MIN_OBS_SCORE:
        return {"percentile": None,
                "reason": f"only {n} observations (need {MIN_OBS_SCORE})"}
    latest = values[idx]
    pct = 100.0 * sum(1 for x in window if x <= latest) / n
    return {"percentile": pct, "n_obs": n,
            "as_of": dates[idx].isoformat(), "value": latest}


def _section_banner(store: Any, matrix: dict, end: date) -> dict:
    osm = (matrix.get("overall") or {}).get("score")
    details: dict[str, Any] = {}
    disagree = False
    messages: list[str] = []
    for key, spec in OFFICIAL.items():
        _sid, pts = _resolve_points(store, key, spec["series_candidates"])
        norm = _percentile_normalize(pts, end) if pts else \
            {"percentile": None, "reason": "no stored history"}
        pct = norm.get("percentile")
        detail: dict[str, Any] = {"name": spec["name"], "official_percentile": pct,
                                  "osm_overall": osm}
        detail.update({k: v for k, v in norm.items() if k != "percentile"})
        if pct is not None and osm is not None:
            diff = osm - pct
            detail["diff"] = diff
            if abs(diff) > BANNER_THRESHOLD:
                disagree = True
                messages.append(
                    f"OSM Overall ({osm:.0f}) disagrees with {spec['name']} "
                    f"({pct:.0f}) by {abs(diff):.0f} points (> {BANNER_THRESHOLD:.0f})")
        else:
            detail["diff"] = None
        details[key] = detail
    return {
        "disagree": disagree,
        "threshold": BANNER_THRESHOLD,
        "messages": messages,
        "normalization": (
            "Official indexes are mapped to 0-100 by rank percentile of the "
            "latest observation against their own expanding history (up to 5 "
            "trading years, min 252 obs), direction '+' (higher = more "
            "stress) - the same percentile convention the OSM engine uses "
            "for indicator scores, making OSM Overall directly comparable."),
        "details": details,
    }


def _section_sensitivity_weights(matrix: dict, weights: dict,
                                 weights_source: str) -> dict:
    """Shift each category weight by +/-5 points, renormalizing the rest
    proportionally; recompute the Overall gauge from current composites."""
    composites = matrix.get("composites") or {}
    ok = {c: comp for c, comp in composites.items()
          if isinstance(comp, dict) and comp.get("status") == "ok"
          and comp.get("score") is not None}
    if not ok:
        return {"status": "insufficient history",
                "reason": "no category composites with status ok"}
    if not weights:
        cats = sorted(ok)
        weights = {c: 1.0 / len(cats) for c in cats}
        weights_source = "equal_fallback (STRESS_WEIGHTS unavailable)"
    base = stress_score.overall_gauge(
        {c: {"score": ok[c]["score"], "status": "ok"} for c in ok}, weights)
    if base["score"] is None:
        return {"status": "insufficient history",
                "reason": "overall gauge undefined at current composites"}
    per_cat: dict[str, Any] = {}
    max_abs = 0.0
    for cat in sorted(weights):
        if cat not in ok:
            continue
        row: dict[str, Any] = {}
        for direction in (+WEIGHT_SHIFT, -WEIGHT_SHIFT):
            w_new = max(0.0, weights[cat] + direction)
            denom = 1.0 - weights[cat]
            scale = (1.0 - w_new) / denom if denom > 0 else 0.0
            shifted = {c: (w_new if c == cat else weights[c] * scale)
                       for c in weights}
            g = stress_score.overall_gauge(
                {c: {"score": ok[c]["score"], "status": "ok"} for c in ok},
                shifted)
            delta = (g["score"] - base["score"]) if g["score"] is not None else None
            row["up5" if direction > 0 else "down5"] = delta
            if delta is not None:
                max_abs = max(max_abs, abs(delta))
        per_cat[cat] = row
    return {"base_overall": base["score"], "weights_source": weights_source,
            "per_category": per_cat, "max_abs_change": max_abs}


def _velocity_pctile_k(dates: list[date], values: list[float], k: int,
                       direction: str) -> float | None:
    """5d-style velocity percentile for an arbitrary window k, using the
    engine's conventions: direction-adjusted raw change, rank of |raw| vs the
    trailing 1Y of |k-changes| (min 20 changes). Section 10 explicitly asks
    to recompute velocity with 3d/5d/10d windows."""
    i = len(values) - 1
    if i - k < 0:
        return None
    sign = -1.0 if direction == "-" else 1.0
    raw = sign * (values[i] - values[i - k])
    changes = stress_score._k_changes(values, i, k)  # noqa: SLF001
    if len(changes) < stress_score.MIN_CHANGES:  # noqa: SLF001
        return None
    return 100.0 * sum(1 for c in changes if abs(c) <= abs(raw)) / len(changes)


def _section_sensitivity_windows(store: Any, matrix: dict) -> dict:
    indicators = matrix.get("indicators") or {}
    pct: dict[str, dict[int, float]] = {}
    for iid, ind in indicators.items():
        if not isinstance(ind, dict) or ind.get("status") != "ok":
            continue
        _sid, pts = _resolve_points(store, iid)
        if not pts:
            continue
        dates, values = _sorted_xy(pts)
        row = {}
        for k in (3, 5, 10):
            p = _velocity_pctile_k(dates, values, k, ind.get("direction") or "+")
            if p is not None:
                row[k] = p
        if len(row) == 3:
            pct[iid] = row
    if len(pct) < 10:
        return {"status": "insufficient history",
                "reason": f"only {len(pct)} indicators with 3d/5d/10d velocity"}
    # Rank indicators fastest-first per window; Spearman over rank vectors.
    ranks: dict[int, dict[str, float]] = {}
    for k in (3, 5, 10):
        ordered = sorted(pct, key=lambda i: (-pct[i][k], i))
        ranks[k] = {iid: float(r + 1) for r, iid in enumerate(ordered)}
    rho_3v5 = _spearman([ranks[3][i] for i in pct], [ranks[5][i] for i in pct])
    rho_10v5 = _spearman([ranks[10][i] for i in pct], [ranks[5][i] for i in pct])

    def _top10(k: int) -> list[str]:
        return sorted(pct, key=lambda i: (-pct[i][k], i))[:10]

    t5, t3, t10 = set(_top10(5)), set(_top10(3)), set(_top10(10))
    overlap_3 = len(t5 & t3)
    overlap_10 = len(t5 & t10)
    stable = (rho_3v5 is not None and rho_10v5 is not None
              and rho_3v5 >= STABLE_RHO and rho_10v5 >= STABLE_RHO
              and overlap_3 >= STABLE_OVERLAP and overlap_10 >= STABLE_OVERLAP)
    return {
        "n_indicators": len(pct),
        "spearman_3d_vs_5d": rho_3v5,
        "spearman_10d_vs_5d": rho_10v5,
        "top10_overlap_3d": overlap_3,
        "top10_overlap_10d": overlap_10,
        "stable": stable,
        "stability_rule": (
            f"stable when both rank correlations >= {STABLE_RHO} and both "
            f"top-10 overlaps >= {STABLE_OVERLAP}/10"),
        "note": ("Engine caches 1/5/20/60d velocity; 3d/10d recomputed here "
                 "with identical conventions (direction-adjusted raw change, "
                 "|change| rank vs trailing 1Y)."),
    }


def _category_composite_at(members: list[tuple[str, str]],
                           series: dict[str, tuple[list[date], list[float]]],
                           d: date) -> float | None:
    """Equal-weight composite of member scores at date d (engine conventions,
    >=50% coverage rule). members: (iid, direction)."""
    scores: list[float | None] = []
    for iid, direction in members:
        dates, values = series[iid]
        idx = stress_score._last_le(dates, d)  # noqa: SLF001
        if idx < 0:
            scores.append(None)
            continue
        res = stress_score._score_at(dates, values, idx, direction)  # noqa: SLF001
        scores.append(res["score"] if res["status"] == "ok" else None)
    n_total = len(members)
    res_c = stress_score.composite(scores + [None] * max(0, n_total - len(scores)))
    if res_c["status"] != "ok":
        return None
    return res_c["score"]


def _section_false_alarm(store: Any, matrix: dict, end: date) -> dict:
    """Share of 'Fast move' alerts in the last 1Y not followed by a higher
    category composite within 10 trading days of firing."""
    try:
        sdoc = store.doc(STATE_DOC)
    except Exception:  # noqa: BLE001
        sdoc = None
    log = (sdoc.payload.get("fast_move_alerts", [])
           if sdoc is not None and isinstance(sdoc.payload, dict) else [])
    cutoff = end - timedelta(days=FALSE_ALARM_LOOKBACK_DAYS)
    recent = end - timedelta(days=FALSE_ALARM_WINDOW_DAYS)
    cands = [a for a in log
             if isinstance(a, dict) and _to_date(a.get("fired_at")) is not None
             and cutoff <= _to_date(a["fired_at"]) <= recent]
    if not cands:
        return {"status": "insufficient history",
                "reason": ("no evaluable Fast-move alerts: need alerts fired "
                           "10-365 days ago"),
                "n_alerts_logged": len(log)}

    indicators = matrix.get("indicators") or {}
    series_cache: dict[str, tuple[list[date], list[float]]] = {}
    n_false = 0
    n_eval = 0
    skipped = 0
    for a in cands:
        cat = a.get("category")
        base = a.get("composite_at_fire")
        fired = _to_date(a["fired_at"])
        if cat is None or base is None or fired is None:
            skipped += 1
            continue
        members: list[tuple[str, str]] = []
        ok_members = True
        for iid, ind in indicators.items():
            if not isinstance(ind, dict) or ind.get("category") != cat:
                continue
            if ind.get("tag") == "proxy" or ind.get("freq") == "Q":
                continue
            if iid not in series_cache:
                _sid, pts = _resolve_points(store, iid)
                if not pts:
                    ok_members = False
                    break
                dates, values = _sorted_xy(pts)
                if len(dates) < MIN_OBS_SCORE:
                    ok_members = False
                    break
                series_cache[iid] = (dates, values)
            members.append((iid, ind.get("direction") or "+"))
        if not ok_members or not members:
            skipped += 1
            continue
        # Trading dates (fire, fire+10d]: any composite above base?
        cal = sorted({d for dates, _ in series_cache.values() for d in dates
                      if fired < d <= fired + timedelta(days=FALSE_ALARM_WINDOW_DAYS)})
        peaked = False
        any_ok = False
        for d in cal:
            c = _category_composite_at(
                [(iid, dd) for iid, dd in members if iid in series_cache],
                series_cache, d)
            if c is None:
                continue
            any_ok = True
            if c > base:
                peaked = True
                break
        if not any_ok:
            skipped += 1
            continue
        n_eval += 1
        if not peaked:
            n_false += 1
    if n_eval == 0:
        return {"status": "insufficient history",
                "reason": "no alerts with computable composite history",
                "n_alerts_logged": len(log)}
    return {"rate": n_false / n_eval, "n_false": n_false,
            "n_evaluable": n_eval, "n_skipped": skipped,
            "definition": ("share of Fast-move alerts (fired 10-365d ago) whose "
                           "category composite never printed above its "
                           "at-fire level in the following 10 trading days")}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _doc_as_of(matrix: dict) -> date:
    overall = matrix.get("overall") or {}
    d = _to_date(overall.get("as_of")) or _to_date(matrix.get("updated_at"))
    return d or date.today()


def validation_payload(store: Any) -> dict:
    """Payload for GET /api/stress/validation (wired by WS1)."""
    notes: list[str] = []
    try:
        mdoc = store.doc("stress_matrix")
    except Exception:  # noqa: BLE001
        mdoc = None
    if mdoc is None or not isinstance(mdoc.payload, dict):
        return {"as_of": None, "status": "no_data",
                "reason": "stress_matrix doc missing",
                "correlations": {}, "lead_lag": {}, "banner": {},
                "sensitivity": {}, "false_alarm_rate": {},
                "notes": notes}

    matrix = mdoc.payload
    end = _doc_as_of(matrix)
    weights, weights_source = _category_weights()
    if not weights:
        cats = sorted({(ind.get("category") or "?")
                       for ind in (matrix.get("indicators") or {}).values()
                       if isinstance(ind, dict)})
        weights = {c: 1.0 / len(cats) for c in cats} if cats else {}
        weights_source = "equal_fallback (STRESS_WEIGHTS unavailable)"
        notes.append("STRESS_WEIGHTS import failed; equal weights used "
                     "for the OSM history reconstruction.")

    osm_hist, hist_meta = _osm_history(store, matrix, weights, end)
    notes.append(f"OSM history: {hist_meta.get('n_history_points')} weekly "
                 f"points from {hist_meta.get('n_members')} indicators "
                 f"({hist_meta.get('n_unresolved')} unresolved/skipped).")
    if not osm_hist:
        notes.append("OSM overall history could not be reconstructed: "
                     "insufficient indicator history.")

    official: dict[str, dict[date, float]] = {}
    for key, spec in OFFICIAL.items():
        _sid, pts = _resolve_points(store, key, spec["series_candidates"])
        if pts:
            dates, values = _sorted_xy(pts)
            official[key] = dict(zip(dates, values))

    payload = {
        "as_of": end.isoformat(),
        "status": "ok" if osm_hist else "partial",
        "weights_source": weights_source,
        "correlations": _section_correlations(store, matrix, osm_hist, end),
        "lead_lag": _section_lead_lag(osm_hist, official, end),
        "banner": _section_banner(store, matrix, end),
        "sensitivity": {
            "weights": _section_sensitivity_weights(matrix, weights, weights_source),
            "windows": _section_sensitivity_windows(store, matrix),
        },
        "false_alarm_rate": _section_false_alarm(store, matrix, end),
        "notes": notes,
    }
    return payload
