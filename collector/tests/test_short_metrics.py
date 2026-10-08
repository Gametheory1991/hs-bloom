"""Tests for computed short-selling stress metrics."""

from datetime import date

import pytest

from collector.short_metrics import _pct_change, refresh_short_metrics


class FakeStore:
    def __init__(self):
        self._si_rows = []
        self._points = {}
        self.upserted = {}

    def _execute(self, sql, args=()):
        # GROUP BY settlement_date query
        if "GROUP BY settlement_date" in sql:
            by_date = {}
            for sdate, short, adv in self._si_rows:
                d = by_date.setdefault(sdate, [0.0, 0.0])
                d[0] += short
                d[1] += adv
            return [(k, v[0], v[1]) for k, v in sorted(by_date.items())]
        return []

    def points(self, sid):
        return self._points.get(sid, {})

    def upsert_points(self, sid, pts):
        self.upserted[sid] = list(pts)


def test_pct_change():
    assert _pct_change(110.0, 100.0) == pytest.approx(10.0)
    assert _pct_change(90.0, 100.0) == pytest.approx(-10.0)
    assert _pct_change(100.0, 0.0) is None
    assert _pct_change(None, 100.0) is None


def test_days_to_cover():
    store = FakeStore()
    # two tickers, one settlement: 1M short / 100k adv + 500k short / 100k adv
    store._si_rows = [
        ("2026-09-15", 1_000_000.0, 100_000.0),
        ("2026-09-15", 500_000.0, 100_000.0),
        ("2026-09-30", 1_600_000.0, 200_000.0),
    ]
    store._points["cycle:finra-short-total"] = {
        date(2026, 9, 15): 1_500_000.0,
        date(2026, 9, 30): 1_600_000.0,
    }
    out = refresh_short_metrics(store)
    assert "short-metrics" in out
    dtc = dict(store.upserted["cycle:short-days-to-cover"])
    # 1.5M / 200k = 7.5 ; 1.6M / 200k = 8.0
    assert dtc[date(2026, 9, 15)] == pytest.approx(7.5)
    assert dtc[date(2026, 9, 30)] == pytest.approx(8.0)
    # vel30: 1.6M vs 1.5M 15 days apart -> within 30d window
    vel30 = dict(store.upserted["cycle:short-interest-vel30"])
    assert vel30[date(2026, 9, 30)] == pytest.approx(100 * 100_000 / 1_500_000)
    # vel7: no two settlements within 7 days -> empty
    assert "cycle:short-interest-vel7" not in store.upserted


def test_empty_store_no_crash():
    store = FakeStore()
    out = refresh_short_metrics(store)
    assert "0 dtc settlements" in out
    assert store.upserted == {}
