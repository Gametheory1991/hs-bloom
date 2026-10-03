"""Tests for the vol dashboard compute job (batch 4)."""
import math
from datetime import date, timedelta

import pytest

from collector.fetchers import voldash
from collector.store import Store


def _store():
    return Store(":memory:")


def _daily(n, start, daily_pct, start_date=date(2024, 1, 2)):
    out = []
    px = start
    d = start_date
    while len(out) < n:
        if d.weekday() < 5:
            px *= (1 + daily_pct)
            out.append((d, round(px, 2)))
        d += timedelta(days=1)
    return out


def _seed(store):
    # ~300 trading days of prices + implied vols
    spx = _daily(300, 5000, 0.0008)
    vix = [(d, 15 + 3 * math.sin(i / 20.0)) for i, (d, _) in enumerate(spx)]
    vxn = [(d, 20 + 3 * math.sin(i / 20.0)) for i, (d, _) in enumerate(spx)]
    iwm = _daily(300, 220, 0.0006)
    store.upsert_points("idx:SPX", spx)
    store.upsert_points("idx:NDX", spx)
    store.upsert_points("cycle:vix", vix)
    store.upsert_points("cycle:vxn", vxn)
    store.upsert_points("cycle:iwm", iwm)
    store.upsert_points("cycle:gld", _daily(300, 200, 0.0005))
    store.upsert_points("cycle:gvz", [(d, 18.0) for d, _ in spx])
    return spx


def test_rows_and_spread():
    store = _store()
    _seed(store)
    assert voldash.refresh_voldash(store, today=date(2026, 10, 3)) == "voldash"
    doc = store.doc("voldash")
    assert doc is not None
    rows = {r["ticker"]: r for r in doc.payload["rows"]}
    spx = rows["SPX"]
    assert spx["implied"] is not None and spx["realized"] is not None
    assert spx["spread"] == round(spx["implied"] - spx["realized"], 1)
    assert 0 <= spx["pctile_1y"] <= 100
    assert 0 <= spx["spread_pctile_1y"] <= 100
    assert spx["wkly_chg"] is not None
    # RTY has no implied leg (RVX dead) -> realized only
    rty = rows["RTY"]
    assert rty["implied"] is None and rty["realized"] is not None
    assert rty["spread"] is None
    # regime line + counts
    assert doc.payload["regime"]
    assert doc.payload["n_rich"] + doc.payload["n_cheap"] <= len(rows)


def test_vix_vvix_and_beta():
    store = _store()
    spx = _seed(store)
    store.upsert_points("cycle:vvix", [(d, 90 + 5 * math.sin(i / 25.0))
                                       for i, (d, _) in enumerate(spx)])
    voldash.refresh_voldash(store, today=date(2026, 10, 3))
    doc = store.doc("voldash").payload
    assert len(doc["vix_hist"]) > 200
    assert len(doc["vvix_hist"]) > 200
    # VIX falls when SPX rises in this synthetic -> negative beta
    assert doc["beta"]["vix_spx"] is not None
    assert len(doc["beta_hist"]["vix_spx"]) > 100
    assert doc["beta"]["vvix_vix"] is not None
    # betas also written as histories
    assert store.points("voldash:beta-vix-spx")


def test_degrades_without_data():
    store = _store()
    assert voldash.refresh_voldash(store, today=date(2026, 10, 3)) == "voldash"
    doc = store.doc("voldash").payload
    assert doc["rows"] == []
    assert doc["beta"] == {"vix_spx": None, "vvix_vix": None}


def test_rich_regime_line():
    store = _store()
    spx = _seed(store)
    # pin implied vols at extreme highs for the last 12 sessions only, so the
    # trailing-252d percentile still reads >= 90th
    n = len(spx)
    store.upsert_points("cycle:vix", [(d, 60.0) for d, _ in spx[-12:]])
    store.upsert_points("cycle:vxn", [(d, 65.0) for d, _ in spx[-12:]])
    store.upsert_points("cycle:gvz", [(d, 50.0) for d, _ in spx[-12:]])
    voldash.refresh_voldash(store, today=date(2026, 10, 3))
    doc = store.doc("voldash").payload
    assert doc["n_rich"] >= 3
    assert "rich" in doc["regime"]
