"""Tests for collector.scorecard (briefing-style 1D/1M/3M/1Y + 1Y z-score).

Pure function: no HTTP, no store. Dates are business days so the
on-or-before horizon lookups are exact.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from collector.scorecard import scorecard_row

D0 = date(2026, 10, 2)  # Friday


def bizdays(n: int) -> list[date]:
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def flat_series(n: int, start: float = 100.0, step: float = 0.0) -> dict:
    days = bizdays(n)
    return {d: round(start + i * step, 4) for i, d in enumerate(days)}


class ScorecardTest(unittest.TestCase):
    def test_pct_moves(self):
        pts = flat_series(300, start=100.0, step=0.1)  # +0.1/day, ends 129.9
        r = scorecard_row(pts, "pct")
        self.assertEqual(r["last"], 129.9)
        self.assertEqual(r["asof"], "2026-10-02")
        self.assertAlmostEqual(r["d1"], round((129.9 / 129.8 - 1) * 100, 2))
        self.assertIsNotNone(r["m1"])
        self.assertIsNotNone(r["m3"])
        self.assertIsNotNone(r["y1"])
        self.assertGreater(r["y1"], 20)  # ~+30% over the year

    def test_bp_moves(self):
        pts = flat_series(300, start=4.00, step=0.005)  # +0.5bp/day
        r = scorecard_row(pts, "bp")
        self.assertAlmostEqual(r["d1"], 0.5, places=1)
        self.assertAlmostEqual(r["m1"], 0.5 * 21, places=0)  # ~21 biz days

    def test_z_score_sign_and_scale(self):
        pts = flat_series(300, start=100.0, step=0.1)  # steady climb
        r = scorecard_row(pts, "pct")
        self.assertIsNotNone(r["z_1y"])
        self.assertGreater(r["z_1y"], 1.5)  # last well above trailing mean
        down = {d: 200.0 - v for d, v in pts.items()}
        rd = scorecard_row(down, "pct")
        self.assertLess(rd["z_1y"], -1.5)

    def test_flat_series_z_is_none(self):
        r = scorecard_row(flat_series(300, start=50.0), "pct")
        self.assertIsNone(r["z_1y"])  # zero stdev -> undefined

    def test_short_history_nones(self):
        r = scorecard_row(flat_series(10, start=100.0, step=1.0), "pct")
        self.assertIsNotNone(r["d1"])
        self.assertIsNone(r["m1"])   # <30d of history
        self.assertIsNone(r["y1"])
        self.assertIsNone(r["z_1y"])  # <20 obs

    def test_empty(self):
        r = scorecard_row({}, "pct")
        self.assertEqual(r, {"last": None, "asof": None, "d1": None,
                             "m1": None, "m3": None, "y1": None, "z_1y": None})

    def test_bad_kind_raises(self):
        with self.assertRaises(ValueError):
            scorecard_row(flat_series(30), "log")

    def test_weekend_gap_resolves(self):
        # 1D on a Monday must compare against the prior Friday.
        mon = date(2026, 10, 5)
        pts = {date(2026, 10, 2): 100.0, mon: 101.0}
        r = scorecard_row(pts, "pct")
        self.assertEqual(r["d1"], 1.0)


if __name__ == "__main__":
    unittest.main()
