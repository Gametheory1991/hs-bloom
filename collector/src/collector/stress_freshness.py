"""Stress Monitor v2 — lag-aware freshness (Phase 2, WS5; spec §16.6).

Stdlib only. The scoring engine (``collector.stress_score.refresh_stress``)
calls into this module once per indicator and writes the returned freshness
status into the ``stress_matrix`` doc. The alert engine (WS2) reads that doc
and must skip every indicator whose freshness status is not ``"ok"``.

Freshness rule (§16.6): an observation is stale only when its age exceeds
``expected_lag_days + tolerance_days``. An age beyond ``max_age_days`` is a
hard fail (status ``"failed"``). This replaces the flat "stale after 2
trading days" rule, which would grey out STAR (7-day publication lag) and
OFR quarterly rows permanently.

Per-frequency tolerances (documented choices):
  D → 2 days: daily feeds publish T+1; a single missed day (holiday, weekend
      adjacency, FRED revision delay) is routine. Stale line = 3 days, which
      matches the old "grey after 2 trading days" rule (§9).
  W → 3 days: weekly releases (FRED weekly as-of dates, CFTC TFF, AAII)
      routinely slip a few days around holidays. Stale line = 10 days.
  M → 7 days: monthly statistical releases (UMich, CMBS distress, TRACE
      monthly) routinely slip about a week. Stale line = 42 days.
  Q → 30 days: quarterly aggregates (OFR Hedge Fund Monitor / Form PF) are
      published with a multi-month lag; a month of grace keeps healthy rows
      green. OFR rows carry expected_lag_days=120, so a 100-day-old quarter
      is still "ok". Stale line = 150 days; hard fail at 240.
  E → 5 days: event series (Treasury auctions) fire on a weekly/biweekly
      cadence per bucket with schedule gaps; 5 days of grace avoids flagging
      a normal inter-auction gap. Stale line = 15 days.

Expected-lag / max-age defaults mirror the fetcher LAG_* dicts in
``collector.fetchers.stress`` (LAG_D/W/M/Q/E, LAG_STAR); entries that lack
the keys fall back to these defaults by frequency.

Freshness statuses (the WS2 contract):
  "ok"          fresh: age <= expected_lag_days + tolerance_days
  "stale"       age > expected_lag_days + tolerance_days (no velocity; §9:
                "a stale series has no velocity")
  "failed"      age > max_age_days (hard fail; feed presumed dead)
  "building"    fewer than MIN_OBS_BUILDING (60) observations — §2/§16.6
                history-building rule (put/call ~22 obs, auctions ~5 weeks)
  "no_velocity" quarterly series with healthy data: scored, but per §8
                Q-series carry no velocity (velocity doc reports the same)
  "no_data"     no latest observation on record (extension outside the
                five-state set; mirrors the matrix doc's existing "no_data")

Indicator dicts passed to ``is_stale`` / ``classify_freshness`` are registry
entries (which carry ``expected_lag_days`` / ``max_age_days`` / ``freq``)
with the latest observation date in ``"latest_obs"`` (ISO) — ``"as_of"``
is accepted as an alias, matching the key ``refresh_stress`` writes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Tolerance (grace days) added to expected_lag_days before a series is stale.
TOLERANCE_DAYS = {"D": 2, "W": 3, "M": 7, "Q": 30, "E": 5}

# Fallback (expected_lag_days, max_age_days) per frequency, mirroring the
# fetcher LAG_* dicts. Used when a registry entry lacks the keys.
DEFAULT_LAG = {
    "D": (1, 4),
    "W": (7, 14),
    "M": (35, 70),
    "Q": (120, 240),
    "E": (10, 45),
}

# §2/§16.6 history-building rule: no percentiles until N >= 60.
MIN_OBS_BUILDING = 60

FRESH_STATUSES = ("ok", "stale", "failed", "building", "no_velocity", "no_data")


def _to_date(d: Any) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def _lag(entry: dict) -> tuple[int, int]:
    """(expected_lag_days, max_age_days) from the entry, else freq default."""
    freq = entry.get("freq", "D")
    exp, mx = DEFAULT_LAG.get(freq, DEFAULT_LAG["D"])
    return (int(entry.get("expected_lag_days", exp)),
            int(entry.get("max_age_days", mx)))


def _latest_obs_date(entry: dict) -> date | None:
    raw = entry.get("latest_obs", entry.get("as_of"))
    if raw is None:
        return None
    return _to_date(raw)


def _age_days(latest: date, as_of: date) -> int:
    """Calendar-day age of the latest observation. Future-dated observations
    (negative age) are clamped to 0 — never stale."""
    return max(0, (as_of - latest).days)


def is_stale(indicator: dict, as_of_iso: str) -> tuple[bool, str]:
    """Lag-aware staleness check (§16.6).

    ``indicator`` is a registry entry carrying ``expected_lag_days``,
    ``max_age_days``, ``freq``, and the latest observation date under
    ``"latest_obs"`` (``"as_of"`` accepted as an alias).

    Returns ``(is_stale, reason)``. ``True`` covers both the stale band
    (age > expected_lag_days + tolerance) and the hard-fail band
    (age > max_age_days); the reason string distinguishes them.
    A series with no latest observation on record is treated as stale
    (fail-closed: WS2 must not alert on unverifiable data).
    """
    as_of = _to_date(as_of_iso)
    freq = indicator.get("freq", "D")
    exp_lag, max_age = _lag(indicator)
    tol = TOLERANCE_DAYS.get(freq, TOLERANCE_DAYS["D"])
    latest = _latest_obs_date(indicator)
    if latest is None:
        return True, "no latest observation on record (fail-closed)"
    age = _age_days(latest, as_of)
    if age > max_age:
        return True, (f"failed: age {age}d > max_age_days {max_age} "
                      f"(feed presumed dead)")
    if age > exp_lag + tol:
        return True, (f"stale: age {age}d > expected_lag_days {exp_lag} + "
                      f"tolerance {tol} (freq {freq})")
    return False, (f"ok: age {age}d <= expected_lag_days {exp_lag} + "
                   f"tolerance {tol} (freq {freq})")


def classify_freshness(indicator: dict, as_of_iso: str,
                       n_obs: int | None = None,
                       score_status: str | None = None,
                       latest_obs: Any | None = None) -> dict:
    """Classify one indicator into the WS2 freshness contract.

    Returns a dict with ``status`` in
    {"ok","stale","failed","building","no_velocity"} (plus "no_data" when
    there is no latest observation), ``reason``, ``age_days``,
    ``expected_lag_days``, ``tolerance_days``, ``max_age_days`` and ``freq``.

    Precedence: no_data > failed > stale > building > no_velocity (healthy
    quarterly rows) > ok. ``score_status="building"`` from the scorer (its
    <252-obs gate) is honoured as "building" as well — the scorer's gate is
    stricter than the 60-obs freshness rule, and a series with no score must
    never read as fresh-ok.
    """
    as_of = _to_date(as_of_iso)
    freq = indicator.get("freq", "D")
    exp_lag, max_age = _lag(indicator)
    tol = TOLERANCE_DAYS.get(freq, TOLERANCE_DAYS["D"])
    latest = _to_date(latest_obs) if latest_obs is not None else _latest_obs_date(indicator)
    age = _age_days(latest, as_of) if latest is not None else None

    base = {"reason": "", "age_days": age, "expected_lag_days": exp_lag,
            "tolerance_days": tol, "max_age_days": max_age, "freq": freq,
            "latest_obs": latest.isoformat() if latest else None}

    if latest is None:
        return {**base, "status": "no_data",
                "reason": "no latest observation in store"}
    if age > max_age:
        return {**base, "status": "failed",
                "reason": f"age {age}d > max_age_days {max_age}"}
    if age > exp_lag + tol:
        return {**base, "status": "stale",
                "reason": f"age {age}d > expected_lag_days {exp_lag} + tolerance {tol}"}
    if score_status == "building" or (n_obs is not None and n_obs < MIN_OBS_BUILDING):
        return {**base, "status": "building",
                "reason": (f"history building ({n_obs} obs < {MIN_OBS_BUILDING}; "
                            "no score yet)" if n_obs is not None
                            else "history building (scorer status)")}
    if freq == "Q":
        return {**base, "status": "no_velocity",
                "reason": "quarterly series: scored, no velocity per §8"}
    return {**base, "status": "ok",
            "reason": f"age {age}d within expected_lag_days {exp_lag} + tolerance {tol}"}


def event_carried_value(points: dict[Any, float]) -> dict:
    """Carry-forward for event-frequency series (§8, §16.6).

    Event series (Treasury auctions) have observations only on event dates.
    Between events the current value IS the last event's value — carried
    forward, never interpolated. Velocity for these series is computed from
    the last two EVENTS only and labelled "event step" (see
    ``stress_score.velocity`` with freq="E").

    ``points`` maps ISO date (or date) -> value, like the store returns.
    Returns {"value", "event_date", "label", "n_events", "carried"}; empty
    history gives value None and carried False.
    """
    pairs = []
    for d, v in (points or {}).items():
        try:
            pairs.append((_to_date(d), float(v)))
        except (ValueError, TypeError):
            continue
    if not pairs:
        return {"value": None, "event_date": None, "label": "E · no events yet",
                "n_events": 0, "carried": False}
    pairs.sort(key=lambda p: p[0])
    ev_date, ev_value = pairs[-1]
    iso = ev_date.isoformat()
    return {"value": ev_value, "event_date": iso,
            "label": f"E · {iso} (carried from last event)",
            "n_events": len(pairs), "carried": True}
