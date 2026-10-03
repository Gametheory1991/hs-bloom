"""Briefing-style scorecard math: 1D/1M/3M/1Y changes + 1-year z-score.

The daily briefing's signature is a standardized scorecard on every series:
percent (or basis-point) moves over 1D/1M/3M/1Y plus a z-score of the current
level against its trailing 1-year history. This module is pure (no I/O, no
clock) — the API route feeds it stored points.

Conventions (shared with collector.changes):
- horizons use ref_close semantics: latest observation ON OR BEFORE the
  target date, so weekends/holidays resolve to the prior observation;
  1D is strictly before asof.
- kind "pct": changes are percent; kind "bp": changes are basis points
  (for yields, spreads, ratios quoted in percent — 100bp = 1pp).
- z_1y: (last - mean) / stdev of the LEVEL over the trailing 252
  observations (~1 trading year; fewer if history is short, min 20).
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, pstdev

from collector.changes import pct_change, ref_close

Points = dict[date, float]

MIN_HISTORY = 20
YEAR_WINDOW = 252


def scorecard_row(points: Points, kind: str = "pct") -> dict:
    """Score one series. Returns None-valued fields when history is short."""
    if kind not in ("pct", "bp"):
        raise ValueError(f"unknown scorecard kind: {kind!r}")
    if not points:
        return {"last": None, "asof": None, "d1": None, "m1": None,
                "m3": None, "y1": None, "z_1y": None}
    asof = max(points)
    last = points[asof]

    def move(ref: float | None) -> float | None:
        if ref is None:
            return None
        if kind == "pct":
            return pct_change(last, ref)
        return round((last - ref) * 100, 1)

    hist = [points[d] for d in sorted(points)[-YEAR_WINDOW:]]
    z = None
    if len(hist) >= MIN_HISTORY:
        sd = pstdev(hist)
        if sd:
            z = round((last - mean(hist)) / sd, 2)

    return {
        "last": round(last, 4),
        "asof": asof.isoformat(),
        "d1": move(ref_close(points, asof, "1d")),
        "m1": move(ref_close(points, asof, "1m")),
        "m3": move(_latest_on_or_before(points, asof, 91)),
        "y1": move(ref_close(points, asof, "1y")),
        "z_1y": z,
    }


def _latest_on_or_before(points: Points, asof: date, days_back: int) -> float | None:
    """ref_close-style lookup for horizons changes.py doesn't name (3M = 91d)."""
    target = asof - timedelta(days=days_back)
    prior = [d for d in points if d <= target]
    return points[max(prior)] if prior else None
