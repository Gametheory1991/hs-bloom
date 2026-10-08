"""Stress Monitor v2 — Phase 2 WS3: episode-relative velocity matrix (spec §16.2).

For every Tier-1 indicator (registry: collector.fetchers.stress.INDICATORS),
finds the largest calendar 7-day and 30-day direction-adjusted change inside
each of the 8 stress-episode windows, then shows today's 7d/30d change as a
percentage of that episode peak (capped at 150%).

Conventions (kept identical to the Phase-1 engine on purpose):
- Changes are computed between stored observations only — never interpolated
  (same rule as collector.stress_score).
- Calendar 7d/30d (this module) and trading 1d/5d/20d/60d (stored by
  collector.stress_score.refresh_stress in the ``stress_velocity`` doc) are
  two separate families; 5d is never equated with 7d in the maths (§16.3).
- Direction-adjusted so + = more stress: "+" keeps the raw change, "-"
  flips it, "±" takes the absolute value (a big move either way is stress,
  same spirit as the ± absolute-z scoring).
- Composite and Overall Gauge series are built from member *scores* (0-100,
  already direction-adjusted), reusing collector.stress_score._score_at so
  the scores are bit-identical to refresh_stress's.
- Q-tagged and event (E) series: no velocity. Weekly/monthly series: the 30d
  family uses the last two observations (weekly/monthly step); 7d is n/a.

Scheduler note: this module only computes and caches. WS1/coordinator must
chain ``refresh_episodes(store, registry)`` in the scheduler (after
refresh_stress / refresh_stress_full) so GET /api/stress/episodes serves a
fresh ``stress_episodes`` doc.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import Any

from collector import stress_score as _ss
from collector.fetchers.stress import INDICATORS as _DEFAULT_REGISTRY
from collector.fetchers.stress import STRESS_WEIGHTS as _DEFAULT_WEIGHTS

# ---------------------------------------------------------------------------
# Episode registry — COPIED VERBATIM from ~/workspace/velocity/velocity.py
# (the registry spec §16.2 requires; the confirmed source for View 5).
# Copied on 2026-10-08. Do NOT edit the dates here: edit the source file and
# re-copy. The source tuples are (name, start, finish, trigger) — there is no
# peak date in the source, so peak is reported as "dates missing" (§16.2).
# ---------------------------------------------------------------------------
EPISODES = [
    # (name, start, finish, trigger)
    ("2007 grind", "2007-06-01", "2007-12-31", "Bear Stearns subprime funds implode (Jun 2007)"),
    ("GFC",        "2008-09-01", "2008-12-31", "Fannie/Freddie conservatorship Sep 7; Lehman Sep 15"),
    ("Covid",      "2020-02-20", "2020-03-23", "S&P 500 all-time high Feb 19; crash into Fed bazooka Mar 23"),
    ("2022 hikes", "2022-06-01", "2022-10-31", "QT begins Jun 1; HY peak Jul; gilt crisis Sep-Oct"),
    ("SVB",        "2023-03-08", "2023-03-31", "SVB capital-raise announcement Mar 8 (failed Mar 10)"),
    ("Tariffs",    "2025-03-01", "2025-04-09", "growth-scare widening into Liberation Day; 90-day pause Apr 9"),
    ("Liberation Day", "2025-04-02", "2025-04-09", "reciprocal tariff announcement after close Apr 2; pause Apr 9"),
    ("Iran war",   "2026-03-01", "2026-03-31", "oil/rate spike (per desk records)"),
]

EPISODE_META = [
    {"name": n, "start": s, "finish": f, "trigger": t, "peak": "dates missing"}
    for n, s, f, t in EPISODES
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

MIN_WINDOW_OBS = 30     # "covering": >= 30 observations inside the episode window
RATIO_CAP_PCT = 150.0    # today's change as % of the episode peak, capped
FASTER_THRESHOLD = 100.0  # ratio >= 100% counts as "moving faster than the peak"
LOOKBACK_PAD_DAYS = 35  # pad before a window start so t-n lookbacks resolve
RECENT_PAD_DAYS = 65    # trailing range used for "today" composite values
FREQ_NO_VELOCITY = {"Q", "E"}  # quarterly + event series: no velocity (task §16.2)
ANALOG_LOOKBACK_OBS = 20       # closest-analog pattern length (trading obs)
SOURCE_LABEL = "stress-v2"
DOC_KEY = "stress_episodes"
ANALOG_LABEL = "descriptive comparison, not a forecast"

_W7, _W30 = "7d", "30d"
_WINDOWS = (_W7, _W30)


# ---------------------------------------------------------------------------
# history helpers
# ---------------------------------------------------------------------------

def _to_date(d: Any) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    return date.fromisoformat(str(d))


def _sorted_obs(history: dict[Any, float]) -> tuple[list[date], list[float]]:
    """(dates, values) sorted ascending by date."""
    pairs = sorted((_to_date(d), float(v)) for d, v in history.items())
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _last_le(dates: list[date], ao: date) -> int:
    """Index of the last date <= ao, or -1."""
    return bisect_right(dates, ao) - 1


def _dir_sign(direction: str) -> float | None:
    """+ -> 1.0, - -> -1.0, ± -> None (caller takes abs)."""
    if direction == "+":
        return 1.0
    if direction == "-":
        return -1.0
    if direction == "±":
        return None
    raise ValueError(f"unknown direction {direction!r}")


def _adjust(raw: float, sign: float | None) -> float:
    return abs(raw) if sign is None else sign * raw


# ---------------------------------------------------------------------------
# calendar-day and step changes (raw series, never interpolated)
# ---------------------------------------------------------------------------

def cal_change_at(dates: list[date], values: list[float], ti: int,
                  n_days: int, direction: str) -> float | None:
    """Direction-adjusted change ending at obs index ``ti`` over ``n_days``
    calendar days: v(ti) - v(si) where si is the latest observation with
    date <= dates[ti] - n_days. None when no such observation exists."""
    if ti < 0 or ti >= len(dates):
        return None
    si = _last_le(dates, dates[ti] - timedelta(days=n_days))
    if si < 0:
        return None
    return _adjust(values[ti] - values[si], _dir_sign(direction))


def max_cal_change(dates: list[date], values: list[float],
                   start: date, end: date, n_days: int,
                   direction: str) -> float | None:
    """Max direction-adjusted n-calendar-day change for t in [start, end].

    Same algorithm as ~/workspace/velocity/velocity.py::max_nd_rise (the
    cross-check reference): the lookback observation may precede the window.
    """
    best: float | None = None
    for ti in range(len(dates)):
        if dates[ti] < start:
            continue
        if dates[ti] > end:
            break
        c = cal_change_at(dates, values, ti, n_days, direction)
        if c is not None and (best is None or c > best):
            best = c
    return best


def step_change_at(values: list[float], ti: int,
                   direction: str) -> float | None:
    """Direction-adjusted change between the last two observations
    (weekly/monthly step for sub-daily-cadence series)."""
    if ti < 1 or ti >= len(values):
        return None
    return _adjust(values[ti] - values[ti - 1], _dir_sign(direction))


def max_step_change(dates: list[date], values: list[float],
                    start: date, end: date, direction: str) -> float | None:
    """Max direction-adjusted last-two-observation change for t in window."""
    best: float | None = None
    for ti in range(len(dates)):
        if dates[ti] < start:
            continue
        if dates[ti] > end:
            break
        c = step_change_at(values, ti, direction)
        if c is not None and (best is None or c > best):
            best = c
    return best


def window_obs_count(dates: list[date], start: date, end: date) -> int:
    return sum(1 for d in dates if start <= d <= end)


# ---------------------------------------------------------------------------
# member scores at a calendar date (bit-identical to score_series)
# ---------------------------------------------------------------------------

def _score_at_date(dates: list[date], values: list[float], ao: date,
                   direction: str) -> float | None:
    """0-100 score of the latest observation <= ao, exactly as
    stress_score.score_series(history, direction, as_of=ao) would return.
    Uses the same _score_at so composite scores match refresh_stress."""
    idx = _last_le(dates, ao)
    if idx < 0:
        return None
    return _ss._score_at(dates, values, idx, direction)["score"]  # noqa: SLF001


# ---------------------------------------------------------------------------
# indicator matrix
# ---------------------------------------------------------------------------

def _velocity_kind(entry: dict) -> str:
    """'daily' | 'subdaily' (W/M) | 'none' (Q/E or no series)."""
    if entry.get("series_id") is None:
        return "none"
    freq = entry.get("freq", "D")
    if freq in FREQ_NO_VELOCITY:
        return "none"
    if freq in ("W", "M"):
        return "subdaily"
    return "daily"


def _cell(episode_max: float | None, today: float | None,
          note: str = "") -> dict:
    cell: dict[str, Any] = {"episode_max": episode_max, "today": today,
                            "ratio_pct": None, "status": "ok", "note": note}
    if episode_max is None or today is None:
        cell["status"] = "undefined"
        why = ("today's change undefined (insufficient trailing history)"
               if today is None else "no episode peak")
        cell["note"] = (note + "; " if note else "") + why
        return cell
    if episode_max <= 0:
        # A non-positive "peak" means the episode contained no move in the
        # stress direction at all; a ratio against it would mislead (e.g. two
        # calming moves dividing into a positive-looking percentage).
        cell["status"] = "undefined"
        cell["note"] = (note + "; " if note else "") + \
            "episode peak is not in the stress direction — ratio undefined"
        return cell
    ratio = 100.0 * today / episode_max
    if ratio > RATIO_CAP_PCT:
        ratio = RATIO_CAP_PCT
        cell["note"] = (note + "; " if note else "") + \
            f"capped at {RATIO_CAP_PCT:.0f}%"
    cell["ratio_pct"] = ratio
    return cell


def _na_cell(status: str, note: str) -> dict:
    return {"episode_max": None, "today": None, "ratio_pct": None,
            "status": status, "note": note}


def _indicator_episodes(entry: dict, dates: list[date], values: list[float],
                        as_of: date) -> tuple[dict, str, date | None]:
    """Per-episode {7d, 30d} cells for one indicator.

    Returns (episodes_dict, status, latest_date). status is one of
    "ok" | "no_velocity" | "no_data".
    """
    kind = _velocity_kind(entry)
    direction = entry.get("direction", "+")
    freq = entry.get("freq", "D")
    episodes: dict[str, dict] = {}

    if kind == "none":
        reason = ("no series in store" if entry.get("series_id") is None
                  else f"freq {freq}: no velocity (quarterly/event series)")
        for name, _, _, _ in EPISODES:
            episodes[name] = {_W7: _na_cell("no_velocity", reason),
                              _W30: _na_cell("no_velocity", reason)}
        return episodes, "no_velocity", None

    if not dates:
        for name, _, _, _ in EPISODES:
            episodes[name] = {_W7: _na_cell("no_history", "no data in store"),
                              _W30: _na_cell("no_history", "no data in store")}
        return episodes, "no_data", None

    last_idx = _last_le(dates, as_of)
    latest = dates[last_idx] if last_idx >= 0 else None
    today_7 = cal_change_at(dates, values, last_idx, 7, direction)
    today_30 = (cal_change_at(dates, values, last_idx, 30, direction)
                if kind == "daily"
                else step_change_at(values, last_idx, direction))

    for name, s, f, _ in EPISODES:
        start, end = date.fromisoformat(s), date.fromisoformat(f)
        n_in = window_obs_count(dates, start, end)
        if n_in < MIN_WINDOW_OBS:
            note = (f"only {n_in} observations in window "
                    f"(need {MIN_WINDOW_OBS})")
            episodes[name] = {_W7: _na_cell("no_history", note),
                              _W30: _na_cell("no_history", note)}
            continue
        if kind == "daily":
            c7 = _cell(max_cal_change(dates, values, start, end, 7, direction),
                       today_7)
            c30 = _cell(max_cal_change(dates, values, start, end, 30, direction),
                        today_30)
        else:
            step_note = (f"{freq} series: 7d window is below the sampling "
                         f"cadence")
            c7 = _na_cell("n/a", step_note)
            c30 = _cell(max_step_change(dates, values, start, end, direction),
                        today_30,
                        note=f"{freq} series: 30d family uses the last two "
                             f"observations ({'weekly' if freq == 'W' else 'monthly'} step)")
        episodes[name] = {_W7: c7, _W30: c30}
    return episodes, "ok", latest


# ---------------------------------------------------------------------------
# composites + overall gauge (from member scores)
# ---------------------------------------------------------------------------

def _composite_members(registry: list[dict],
                       include_proxies: bool = False) -> dict[str, list[dict]]:
    """Eligible members per category — mirrors refresh_stress's exclusion
    rule: n/a rows (no series), Q series, and proxies (unless opted in)."""
    out: dict[str, list[dict]] = {}
    for e in registry:
        if e.get("series_id") is None:
            continue
        if e.get("freq") == "Q":
            continue
        if e.get("tag") == "proxy" and not include_proxies:
            continue
        out.setdefault(e["category"], []).append(e)
    return out


def _grid_dates(as_of: date) -> list[date]:
    """Calendar grid covering every episode window (padded for lookbacks)
    plus the trailing recent range."""
    days: set[date] = set()
    for _, s, f, _ in EPISODES:
        start = date.fromisoformat(s) - timedelta(days=LOOKBACK_PAD_DAYS)
        end = date.fromisoformat(f)
        d = start
        while d <= end:
            days.add(d)
            d += timedelta(days=1)
    d = as_of - timedelta(days=RECENT_PAD_DAYS)
    while d <= as_of:
        days.add(d)
        d += timedelta(days=1)
    return sorted(days)


def _composite_series(store: Any, members: dict[str, list[dict]],
                      grid: list[date]) -> dict[str, dict[date, float | None]]:
    """Per-category score series on the grid: equal-weight mean of member
    scores at each date. A date is defined only with >= 50% member coverage
    (same rule as stress_score.composite); else None."""
    hists: dict[str, tuple[list[date], list[float], str]] = {}
    for cat, entries in members.items():
        for e in entries:
            h = store.points(e["series_id"])
            if h:
                dates, values = _sorted_obs(h)
                hists[e["id"]] = (dates, values, e.get("direction", "+"))

    series: dict[str, dict[date, float | None]] = {}
    for cat, entries in members.items():
        n_total = len(entries)
        col: dict[date, float | None] = {}
        for g in grid:
            scores = []
            for e in entries:
                h = hists.get(e["id"])
                if h is None:
                    continue
                sc = _score_at_date(h[0], h[1], g, h[2])
                if sc is not None:
                    scores.append(sc)
            if scores and len(scores) / n_total >= 0.5:
                col[g] = sum(scores) / len(scores)
            else:
                col[g] = None
        series[cat] = col
    return series


def _gauge_series(comp_series: dict[str, dict[date, float | None]],
                  weights: dict[str, float],
                  grid: list[date]) -> dict[date, float | None]:
    """Overall gauge series: weighted mean over defined categories,
    reweighted when a category drops out (mirrors overall_gauge)."""
    out: dict[date, float | None] = {}
    for g in grid:
        avail = [c for c, col in comp_series.items() if col[g] is not None]
        total_w = sum(weights.get(c, 0.0) for c in avail)
        if not avail or total_w <= 0:
            out[g] = None
        else:
            out[g] = sum(weights.get(c, 0.0) / total_w * comp_series[c][g]  # type: ignore[operator]
                         for c in avail)
    return out


def _series_episodes(dates: list[date], values: list[float],
                     as_of: date) -> dict:
    """Episode cells for a derived (composite/gauge) score series.

    Scores are already direction-adjusted (+ = stress), so direction "+".
    """
    episodes: dict[str, dict] = {}
    if not dates:
        for name, _, _, _ in EPISODES:
            episodes[name] = {_W7: _na_cell("no_history", "no defined values"),
                              _W30: _na_cell("no_history", "no defined values")}
        return episodes
    last_idx = _last_le(dates, as_of)
    today_7 = cal_change_at(dates, values, last_idx, 7, "+")
    today_30 = cal_change_at(dates, values, last_idx, 30, "+")
    for name, s, f, _ in EPISODES:
        start, end = date.fromisoformat(s), date.fromisoformat(f)
        n_in = window_obs_count(dates, start, end)
        if n_in < MIN_WINDOW_OBS:
            note = (f"only {n_in} defined values in window "
                    f"(need {MIN_WINDOW_OBS})")
            episodes[name] = {_W7: _na_cell("no_history", note),
                              _W30: _na_cell("no_history", note)}
            continue
        episodes[name] = {
            _W7: _cell(max_cal_change(dates, values, start, end, 7, "+"),
                       today_7),
            _W30: _cell(max_cal_change(dates, values, start, end, 30, "+"),
                        today_30),
        }
    return episodes


def _defined_grid(col: dict[date, float | None]) -> tuple[list[date], list[float]]:
    items = sorted((d, v) for d, v in col.items() if v is not None)
    return [d for d, _ in items], [v for _, v in items]


# ---------------------------------------------------------------------------
# closest analog
# ---------------------------------------------------------------------------

def _analog_vector_recent(dates: list[date], values: list[float],
                          direction: str) -> float | None:
    """Direction-adjusted change over the last ANALOG_LOOKBACK_OBS obs."""
    if len(dates) < ANALOG_LOOKBACK_OBS + 1:
        return None
    return _adjust(values[-1] - values[-ANALOG_LOOKBACK_OBS - 1],
                   _dir_sign(direction))


def _analog_vector_episode(dates: list[date], values: list[float],
                           start: date, end: date,
                           direction: str) -> float | None:
    """Max direction-adjusted 20-observation change inside the window."""
    best: float | None = None
    for ti in range(len(dates)):
        if dates[ti] < start:
            continue
        if dates[ti] > end:
            break
        if ti < ANALOG_LOOKBACK_OBS:
            continue
        c = _adjust(values[ti] - values[ti - ANALOG_LOOKBACK_OBS],
                    _dir_sign(direction))
        if best is None or c > best:
            best = c
    return best


def _cosine(a: list[float], b: list[float]) -> float | None:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


def closest_analog(store: Any, registry: list[dict]) -> dict:
    """Cosine similarity between the last-20-trading-day velocity pattern
    and each episode's 20-observation velocity pattern, over D-frequency
    indicators with history in both (timescale consistency)."""
    cands = [e for e in registry
             if e.get("series_id") is not None
             and e.get("freq", "D") == "D"
             and e.get("freq") not in FREQ_NO_VELOCITY
             and e.get("tag") != "n/a"]
    hists: dict[str, tuple[list[date], list[float], str]] = {}
    for e in cands:
        h = store.points(e["series_id"])
        if not h:
            continue
        dates, values = _sorted_obs(h)
        if len(dates) >= ANALOG_LOOKBACK_OBS + 1:
            hists[e["id"]] = (dates, values, e.get("direction", "+"))

    recent = {i: _analog_vector_recent(d, v, dr) for i, (d, v, dr) in hists.items()}
    per_episode = []
    for name, s, f, _ in EPISODES:
        start, end = date.fromisoformat(s), date.fromisoformat(f)
        pairs = []
        for i, (d, v, dr) in hists.items():
            if window_obs_count(d, start, end) < MIN_WINDOW_OBS:
                continue
            ev = _analog_vector_episode(d, v, start, end, dr)
            rv = recent[i]
            if ev is not None and rv is not None:
                pairs.append((rv, ev))
        sim = _cosine([p[0] for p in pairs], [p[1] for p in pairs]) \
            if pairs else None
        per_episode.append({"episode": name, "similarity": sim,
                            "n_indicators": len(pairs)})
    ranked = sorted([p for p in per_episode if p["similarity"] is not None],
                    key=lambda p: p["similarity"], reverse=True)
    best = ranked[0] if ranked else {"episode": None, "similarity": None,
                                    "n_indicators": 0}
    return {
        "episode": best["episode"],
        "similarity": best["similarity"],
        "n_indicators": best["n_indicators"],
        "all": per_episode,
        "lookback_observations": ANALOG_LOOKBACK_OBS,
        "label": ANALOG_LABEL,
        "note": ("Cosine similarity of per-indicator 20-observation "
                 "direction-adjusted changes (recent vs each episode's max "
                 "20-obs change in-window), over D-frequency indicators with "
                 "history in both. " + ANALOG_LABEL + "."),
    }


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------

def episodes_payload(store: Any, registry: list[dict] | None = None,
                     weights: dict[str, float] | None = None,
                     include_proxies: bool = False,
                     as_of: Any | None = None) -> dict:
    """Episode-relative velocity matrix (§16.2).

    Returns a JSON-serializable dict with the indicator matrix, composite
    and gauge comparisons, header faster-than-peak counts, the closest
    analog, and as_of. Cached by refresh_episodes() as the
    ``stress_episodes`` doc for GET /api/stress/episodes (WS1).
    """
    registry = list(registry) if registry is not None else list(_DEFAULT_REGISTRY)
    weights = dict(weights) if weights is not None else dict(_DEFAULT_WEIGHTS)
    ao = _to_date(as_of) if as_of is not None else date.today()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    indicators: dict[str, dict] = {}
    faster_7 = 0
    faster_30 = 0
    for entry in registry:
        iid = entry["id"]
        try:
            hist = store.points(entry["series_id"]) \
                if entry.get("series_id") else {}
            dates, values = _sorted_obs(hist)
            eps, status, latest = _indicator_episodes(entry, dates, values, ao)
            if status == "ok":
                for ep_cells in eps.values():
                    r7 = ep_cells[_W7].get("ratio_pct")
                    r30 = ep_cells[_W30].get("ratio_pct")
                    if r7 is not None and r7 >= FASTER_THRESHOLD:
                        faster_7 += 1
                        break
                for ep_cells in eps.values():
                    r30 = ep_cells[_W30].get("ratio_pct")
                    if r30 is not None and r30 >= FASTER_THRESHOLD:
                        faster_30 += 1
                        break
            indicators[iid] = {
                "id": iid, "category": entry.get("category"),
                "name": entry.get("name"), "unit": entry.get("unit"),
                "direction": entry.get("direction"),
                "freq": entry.get("freq", "D"),
                "tier": entry.get("tier"), "tag": entry.get("tag"),
                "series_id": entry.get("series_id"),
                "status": status,
                "as_of": latest.isoformat() if latest else None,
                "n_obs": len(dates),
                "episodes": eps,
            }
        except Exception as exc:  # noqa: BLE001 — per-indicator isolation
            indicators[iid] = {"id": iid, "name": entry.get("name"),
                               "status": "error",
                               "error": f"{type(exc).__name__}: {exc}",
                               "episodes": {}}

    # composites + gauge from member scores
    members = _composite_members(registry, include_proxies)
    grid = _grid_dates(ao)
    comp_series = _composite_series(store, members, grid)
    composites: dict[str, dict] = {}
    for cat, col in comp_series.items():
        gd, gv = _defined_grid(col)
        composites[cat] = {
            "category": cat,
            "n_members": len(members[cat]),
            "as_of": gd[-1].isoformat() if gd else None,
            "n_defined": len(gd),
            "status": "ok" if gd else "no_data",
            "episodes": _series_episodes(gd, gv, ao),
        }
    gd_all, gv_all = _defined_grid(
        _gauge_series(comp_series, weights, grid))
    gauge = {
        "as_of": gd_all[-1].isoformat() if gd_all else None,
        "n_defined": len(gd_all),
        "status": "ok" if gd_all else "no_data",
        "weights": weights,
        "episodes": _series_episodes(gd_all, gv_all, ao),
    }

    n_velocity = sum(1 for v in indicators.values()
                     if v.get("status") == "ok")
    payload = {
        "as_of": ao.isoformat(),
        "generated_at": now,
        "episodes": EPISODE_META,
        "indicators": indicators,
        "composites": composites,
        "gauge": gauge,
        "header": {
            "faster_than_peak_7d": faster_7,
            "faster_than_peak_30d": faster_30,
            "n_indicators": len(indicators),
            "n_with_velocity": n_velocity,
        },
        "closest_analog": closest_analog(store, registry),
        "notes": [
            "Calendar 7d/30d family (this doc). The trading 1d/5d/20d/60d "
            "family lives in the stress_velocity doc (refresh_stress); the "
            "two families are never equated in the maths (§16.3).",
            "Episode maxima use raw stored observations only — never "
            "interpolated; the lookback observation may precede the window.",
            "Cells with < 30 observations in the window show 'no history', "
            "never zero. Q/E series show 'no velocity'. Weekly/monthly "
            "series: 7d is n/a (below sampling cadence); the 30d family "
            "uses the last two observations.",
        ],
    }
    return payload


def refresh_episodes(store: Any, registry: list[dict] | None = None,
                     weights: dict[str, float] | None = None,
                     include_proxies: bool = False,
                     as_of: Any | None = None) -> dict:
    """Compute the episode matrix and cache it as the ``stress_episodes`` doc.

    Scheduler note (WS1/coordinator): chain this after refresh_stress /
    refresh_stress_full in the stress job so the cached doc stays fresh for
    GET /api/stress/episodes. Returns the payload.
    """
    payload = episodes_payload(store, registry=registry, weights=weights,
                               include_proxies=include_proxies, as_of=as_of)
    store.put_doc(DOC_KEY, payload, source=SOURCE_LABEL)
    return payload
