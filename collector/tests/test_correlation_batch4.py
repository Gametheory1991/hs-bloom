"""Tests for the batch-4 correlation extensions: CBOE-style grouped MATRIX,
pair histories in the doc, and the new regime pairs."""
import math
from datetime import date, timedelta

import pytest

from collector.fetchers import correlation as xc
from collector.store import Store


def _store():
    return Store(":memory:")


def _daily(n, start, daily_pct, phase=0.0, start_date=date(2024, 1, 2)):
    out = []
    px = start
    d = start_date
    i = 0
    while len(out) < n:
        if d.weekday() < 5:
            px *= (1 + daily_pct + 0.002 * math.sin((i + phase) / 9.0))
            out.append((d, round(px, 4)))
            i += 1
        d += timedelta(days=1)
    return out


def _seed(store, n=300):
    # prices for the CBOE-style matrix keys
    store.upsert_points("idx:SPX", _daily(n, 5000, 0.0008))
    store.upsert_points("cycle:iwm", _daily(n, 220, 0.0007))
    store.upsert_points("idx:SX5E", _daily(n, 4800, 0.0006))
    store.upsert_points("idx:NKX", _daily(n, 38000, 0.0005))
    store.upsert_points("cycle:eem", _daily(n, 45, 0.0006))
    store.upsert_points("cycle:lqd", _daily(n, 110, 0.0002))
    store.upsert_points("cycle:hyg", _daily(n, 80, 0.0002))
    store.upsert_points("cycle:uso", _daily(n, 75, 0.001, phase=30))
    store.upsert_points("cycle:gld", _daily(n, 200, 0.0005, phase=60))
    store.upsert_points("cycle:cu", _daily(n, 4.5, 0.0006, phase=45))
    store.upsert_points("cycle:eur-usd", _daily(n, 1.08, 0.0001, phase=90))
    store.upsert_points("cycle:usd-jpy", _daily(n, 150, 0.0001, phase=120))
    store.upsert_points("cycle:gbp-usd", _daily(n, 1.27, 0.0001, phase=100))
    # yields as diffs
    y10 = []
    y30 = []
    d = date(2024, 1, 2)
    i = 0
    while len(y10) < n:
        if d.weekday() < 5:
            y10.append((d, round(4.2 + 0.3 * math.sin(i / 30.0), 3)))
            y30.append((d, round(4.6 + 0.3 * math.sin(i / 30.0), 3)))
            i += 1
        d += timedelta(days=1)
    store.upsert_points("yield:US10Y", y10)
    store.upsert_points("cycle:ust30y", y30)


def test_cboe_matrix_order_and_groups():
    store = _store()
    _seed(store)
    xc.refresh_xcorr(store, today=date(2026, 10, 3))
    doc = store.doc("xcorr").payload
    # CBOE group order: Equities(5), Credit(2), Rates(2), Commodities(3), FX(3)
    assert doc["labels"][:5] == ["SPX", "RTY", "SX5E", "NKY", "MXEF"]
    assert [g[0] for g in doc["groups"]] == [
        "Equities", "Corporate Credit", "Rates", "Commodities", "Foreign Exchange"]
    n = len(doc["labels"])
    assert n == 15
    assert len(doc["matrix_60d"]) == n
    assert all(len(row) == n for row in doc["matrix_60d"])
    # diagonal is 1.0, matrix symmetric
    assert doc["matrix_60d"][0][0] == 1.0
    assert doc["matrix_60d"][2][5] == doc["matrix_60d"][5][2]


def test_pair_hist_present():
    store = _store()
    _seed(store)
    # run twice so the pair history accumulates
    xc.refresh_xcorr(store, today=date(2026, 10, 2))
    xc.refresh_xcorr(store, today=date(2026, 10, 3))
    doc = store.doc("xcorr").payload
    assert "pair_hist" in doc
    assert "spx-ust10y" in doc["pair_hist"]
    hist = doc["pair_hist"]["spx-ust10y"]
    assert len(hist) == 2
    assert hist[-1][0] == "2026-10-03"
    # CBOE-style pairs exist
    ids = {p["id"] for p in doc["pairs"]}
    for pid in ("spx-ust10y", "spx-lqd", "spx-uso", "spx-gld",
                "sx5e-eurusd", "nkx-usdjpy"):
        assert pid in ids, pid


def test_missing_series_degrades_matrix():
    store = _store()
    _seed(store)
    # drop copper: matrix shrinks, job still succeeds
    store.conn.execute("DELETE FROM series_points WHERE series_id='cycle:cu'")
    store.conn.commit()
    xc.refresh_xcorr(store, today=date(2026, 10, 3))
    doc = store.doc("xcorr").payload
    assert "Copper" not in doc["labels"]
    assert len(doc["labels"]) == 14
