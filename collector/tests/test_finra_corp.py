"""Tests for the FINRA most-active corporate bonds fetcher.

Uses inline samples shaped like the real dynarep API responses
(MostActiveCorporateSecurities) — no binary fixtures, no network.
"""
from __future__ import annotations

import asyncio
from datetime import date

from collector.fetchers import finra_corp
from collector.fetchers.finra_dynarep import DynarepSession  # noqa: F401 (import surface)

import pytest


def _row(issuer, symbol, cat, last, yld, chg, day="2026-10-02"):
    return {
        "issuerName": issuer,
        "issueSymbolIdentifier": symbol,
        "securityTypeCode": cat,
        "couponRate": 4.5,
        "maturityDate": "2030-01-15 00:00:00.000",
        "moodysRating": "Baa2",
        "standardAndPoorsRating": "BBB",
        "highPrice": last + 1,
        "lowPrice": last - 1,
        "lastPrice": last,
        "priceChangeAmount": chg,
        "yieldPercent": yld,
        "reportDate": f"{day} 00:00:00.000",
    }


SAMPLE_ROWS = [
    _row("ACME CORP", "ACME123", "inv", 102.5, 4.2, 0.3),
    _row("ACME CORP 2", "ACME124", "inv", 99.0, 4.8, -0.2),
    _row("RISKY INC", "RSK999", "hy", 87.5, 8.1, 1.1),
    _row("RISKY INC 2", "RSK998", "hy", 91.0, 7.4, -0.5),
    _row("CONV CO", "CNV111", "conv", 110.0, 2.5, 2.0),
    # garbage prints are filtered, not fatal
    _row("BAD DATA", "BAD000", "conv", 1050.0, -84.5, 0.0),
    _row("UNKNOWN CAT", "UNK000", "muni", 100.0, 5.0, 0.0),
]


def test_parse_most_active_averages():
    series, lists = finra_corp.parse_most_active(SAMPLE_ROWS, "corp")
    # IG: (102.5+99)/2=100.75 price, (4.2+4.8)/2=4.5 yield, (0.3-0.2)/2=0.05 chg
    ig_y = series["finra-corp-ig-avgyield"][date(2026, 10, 2)]
    assert ig_y == 4.5
    assert series["finra-corp-ig-avgprice"][date(2026, 10, 2)] == 100.75
    assert series["finra-corp-ig-avgchg"][date(2026, 10, 2)] == pytest.approx(0.05)
    # HY
    assert series["finra-corp-hy-avgyield"][date(2026, 10, 2)] == 7.75
    # converts: BAD DATA row filtered by price guard; only CONV CO counts
    assert series["finra-corp-conv-avgyield"][date(2026, 10, 2)] == 2.5
    assert series["finra-corp-conv-avgprice"][date(2026, 10, 2)] == 110.0
    # unknown category skipped
    assert not any("muni" in k for k in series)
    # bond lists captured for the snapshot doc (BAD DATA + UNK skipped)
    assert len(lists["2026-10-02"]) == 5


def test_parse_most_active_empty():
    series, lists = finra_corp.parse_most_active([], "corp")
    assert series == {}
    assert lists == {}


def test_corp_series_ids():
    ids = finra_corp.corp_series_ids()
    assert len(ids) == 18  # 2 datasets x 3 cats x 3 metrics
    assert "finra-corp-ig-avgyield" in ids
    assert "finra-corp144a-conv-avgchg" in ids


class FakeSession:
    """Fake DynarepSession returning canned rows per dataset."""

    def __init__(self, rows_by_ds):
        self._rows = rows_by_ds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def query(self, dataset, fields, start, end):
        return self._rows.get(dataset, [])


class FakeStore:
    def __init__(self):
        self.series: dict[str, dict] = {}
        self.docs: dict[str, dict] = {}

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def points(self, key, since=None):
        return self.series.get(key, {})

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        return None if d is None else type("Doc", (), d)()


def test_fetch_finra_corp_job():
    rows = {"MostActiveCorporateSecurities": SAMPLE_ROWS,
            "MostActiveCorporate144ASecurities": []}
    store = FakeStore()
    out = asyncio.run(finra_corp.fetch_finra_corp(
        store, today=date(2026, 10, 3),
        session_factory=lambda: FakeSession(rows)))
    assert out == finra_corp.SOURCE
    assert store.points("cycle:finra-corp-ig-avgyield")
    # 144A empty is fine; corp data stored
    doc = store.docs["finra_corp"]["payload"]
    assert doc["as_of"] == "2026-10-02"
    assert doc["status"] == "ok"
    assert "finra-corp-ig-avgyield" in doc["series"]
    assert doc["lists"]["corp"]["bonds"]


def test_fetch_finra_corp_error_recorded():
    class BoomSession(FakeSession):
        async def query(self, dataset, fields, start, end):
            raise RuntimeError("HTTP 403 blocked")

    store = FakeStore()
    try:
        asyncio.run(finra_corp.fetch_finra_corp(
            store, today=date(2026, 10, 3),
            session_factory=lambda: BoomSession({})))
    except RuntimeError as e:
        assert "finra_corp fetch failed" in str(e)
    else:
        raise AssertionError("should have raised")
    assert store.docs["finra_corp"]["payload"]["status"] == "error"


def test_fetch_finra_corp_empty_raises():
    store = FakeStore()
    try:
        asyncio.run(finra_corp.fetch_finra_corp(
            store, today=date(2026, 10, 3),
            session_factory=lambda: FakeSession({})))
    except RuntimeError as e:
        assert "empty response" in str(e)
    else:
        raise AssertionError("should have raised")


class FakeCurveStore(FakeStore):
    """Store with a flat 4% Treasury curve at every tenor."""

    def points(self, key, since=None):
        if key.startswith("cycle:us-") or key == "cycle:us10y":
            return {date(2026, 10, 1): 4.0, date(2026, 10, 2): 4.0}
        return super().points(key, since)


def _bond(**kw):
    b = {"issuer": "ACME", "symbol": "ACME123", "cat": "ig",
         "coupon": 4.5, "maturity": "2031-10-02", "moodys": "Baa2",
         "sp": "BBB", "last": 102.5, "change": 0.5, "yield": 5.0}
    b.update(kw)
    return b


def test_derive_bond_stats_basic():
    store = FakeCurveStore()
    s = finra_corp.derive_bond_stats(_bond(), store, date(2026, 10, 2))
    # chg_pct = 0.5 / 102.0 * 100
    assert s["chg_pct"] == pytest.approx(0.4902, rel=1e-3)
    assert s["ytm_yrs"] == pytest.approx(5.0, rel=1e-2)
    assert s["rating"] == "Baa2/BBB"
    # flat 4% curve -> 100 bps spread
    assert s["spread_bps"] == pytest.approx(100.0)


def test_derive_bond_stats_conv_no_spread():
    store = FakeCurveStore()
    s = finra_corp.derive_bond_stats(_bond(cat="conv"), store,
                                     date(2026, 10, 2))
    assert s["spread_bps"] is None


def test_derive_bond_stats_same_rating():
    store = FakeCurveStore()
    s = finra_corp.derive_bond_stats(_bond(moodys="Baa2", sp="Baa2"),
                                     store, date(2026, 10, 2))
    assert s["rating"] == "Baa2"


def test_treasury_yield_interpolation():
    store = FakeStore()
    store.upsert_points("cycle:us-2y-yield",
                        [(date(2026, 10, 2), 3.0)])
    store.upsert_points("cycle:us-3y-yield",
                        [(date(2026, 10, 2), 4.0)])
    # 2.5y -> midpoint 3.5
    assert finra_corp.treasury_yield_at(store, 2.5,
                                       date(2026, 10, 2)) == pytest.approx(3.5)
    # below shortest tenor -> clamp to shortest
    assert finra_corp.treasury_yield_at(store, 0.01,
                                       date(2026, 10, 2)) == pytest.approx(3.0)
    # above longest -> clamp to longest
    assert finra_corp.treasury_yield_at(store, 30,
                                       date(2026, 10, 2)) == pytest.approx(4.0)


def test_treasury_yield_missing():
    store = FakeStore()
    assert finra_corp.treasury_yield_at(store, 5,
                                       date(2026, 10, 2)) is None


def test_merge_bond_history_accumulates():
    h = finra_corp.merge_bond_history({}, {
        "2026-10-01": [_bond()],
        "2026-10-02": [_bond(last=103.0)],
    })
    assert h["ACME123"]["d"] == ["2026-10-01", "2026-10-02"]
    assert h["ACME123"]["p"] == [102.5, 103.0]
    # merging again with the same days is idempotent
    h2 = finra_corp.merge_bond_history(h, {"2026-10-02": [_bond(last=999)]})
    assert h2["ACME123"]["p"] == [102.5, 103.0]


def test_week52_stats():
    prices = [100.0 + (i % 50) for i in range(300)]
    stats = finra_corp._week52_stats({"p": prices})
    assert stats["hi52"] == 149.0
    assert stats["lo52"] == 100.0
    last = prices[-1]
    assert stats["d52hi_pct"] == pytest.approx((last - 149.0) / 149.0 * 100)
    assert finra_corp._week52_stats({})["hi52"] is None


def _reg_bond(symbol, coupon, maturity, moodys, sp, yld, spread=None, day="2026-10-02"):
    return {"symbol": symbol, "issuer": "T", "coupon": coupon, "maturity": maturity,
            "moodys": moodys, "sp": sp, "cat": "ig", "yield": yld, "last": 100.0,
            "spread_bps": spread}


def test_rating_bucket():
    rb = finra_corp.rating_bucket
    assert rb("Aaa", "AAA") == "AAA"
    assert rb("Aa2", None) == "AA"
    assert rb(None, "A-") == "A"
    assert rb("Baa3", "BBB-") == "BBB"
    assert rb("Ba1", None) == "BB"
    assert rb(None, "B+") == "B"
    assert rb("Caa2", "CCC") == "CCC"
    assert rb(None, None) == "NR"
    assert rb("WR", "NR") == "NR"


def test_maturity_bucket_label():
    mb = finra_corp.maturity_bucket_label
    assert mb(0.5) == "0-1Y"
    assert mb(1.0) == "1-2Y"
    assert mb(4.9) == "3-5Y"
    assert mb(9.99) == "7-10Y"
    assert mb(30.0) == "10Y+"
    assert mb(None) is None
    assert mb(-1) is None


def test_merge_cusip_registry_accumulates():
    lists = {
        "2026-10-01": [_reg_bond("C1", 3.0, "2028-01-01", "Baa2", "BBB", 5.0, 120)],
        "2026-10-02": [_reg_bond("C1", 3.0, "2028-01-01", "Baa2", "BBB", 5.5, 150),
                       _reg_bond("C2", 6.0, "2027-06-01", "Ba2", "BB", 8.0)],
    }
    reg = finra_corp.merge_cusip_registry(None, lists)
    assert set(reg) == {"C1", "C2"}
    assert reg["C1"]["yield"] == 5.5  # latest wins
    assert reg["C1"]["spread_bps"] == 150
    assert reg["C1"]["last_seen"] == "2026-10-02"
    # second merge keeps prior entries
    reg2 = finra_corp.merge_cusip_registry(reg, {"2026-10-03": [_reg_bond("C3", 4.0, "2030-01-01", "A2", "A", 4.5)]})
    assert set(reg2) == {"C1", "C2", "C3"}


def test_merge_cusip_registry_no_eviction():
    # No cap: every CUSIP ever seen is kept, oldest first.
    lists = {f"2026-09-{d:02d}": [_reg_bond(f"C{d}", 4.0, "2030-01-01", "A2", "A", 4.5)]
             for d in range(1, 10)}
    reg = finra_corp.merge_cusip_registry(None, lists)
    assert len(reg) == 9
    assert "C1" in reg and "C9" in reg  # oldest AND newest kept


def test_build_refi_wall():
    asof = date(2026, 10, 5)
    reg = {
        # BBB 1-2Y: coupon 3%, now yields 6% -> +300bp refi stress
        "A": {"coupon": 3.0, "maturity": "2028-03-01", "moodys": "Baa2",
              "sp": "BBB", "yield": 6.0, "spread_bps": 180},
        # BB 0-1Y: coupon 7%, yields 9% -> +200bp
        "B": {"coupon": 7.0, "maturity": "2027-02-01", "moodys": "Ba2",
              "sp": "BB", "yield": 9.0, "spread_bps": 450},
        # AAA 10Y+: no stress
        "C": {"coupon": 4.0, "maturity": "2040-01-01", "moodys": "Aaa",
              "sp": "AAA", "yield": 4.2, "spread_bps": 40},
    }
    wall = finra_corp.build_refi_wall(reg, asof)
    assert wall["issues"] == 3
    bbb = wall["buckets"]["1-2Y"]["BBB"]
    assert bbb["n"] == 1
    assert bbb["avg_coupon"] == 3.0
    assert bbb["avg_ytw"] == 6.0
    assert bbb["refi_delta_bps"] == 300
    bb = wall["buckets"]["0-1Y"]["BB"]
    assert bb["refi_delta_bps"] == 200
    aaa = wall["buckets"]["10Y+"]["AAA"]
    assert aaa["refi_delta_bps"] == 20
    yrs = {y["year"]: y for y in wall["yearly"]}
    assert yrs[2027]["hy"] == 1 and yrs[2028]["ig"] == 1


def test_build_refi_wall_empty():
    wall = finra_corp.build_refi_wall({}, date(2026, 10, 5))
    assert wall["issues"] == 0
    assert wall["buckets"] == {}
    assert wall["yearly"] == []


def test_merge_bond_history_keeps_full_history_no_cap():
    # 500 trading days exceeds the old 400-entry cap: everything must survive.
    from datetime import timedelta
    base = date(2023, 2, 15)
    lists = {}
    for i in range(500):
        d = (base + timedelta(days=i)).isoformat()
        lists[d] = [{"symbol": "ACME123", "last": 100.0 + i * 0.01,
                     "yield": 4.5}]
    hist = finra_corp.merge_bond_history({}, lists)
    h = hist["ACME123"]
    assert len(h["d"]) == 500
    assert len(h["p"]) == 500
    assert len(h["y"]) == 500
    assert h["d"][0] == "2023-02-15"
    assert h["d"][-1] == (base + timedelta(days=499)).isoformat()


def test_merge_bond_history_prev_long_history_not_retrimmed():
    # A previously stored long history must not be re-trimmed on merge.
    prev = {"ACME123": {"d": [f"2020-01-{i:02d}" for i in range(1, 29)] * 16,
                         "p": [100.0] * 448, "y": [4.5] * 448}}
    hist = finra_corp.merge_bond_history(
        prev, {"2026-10-07": [{"symbol": "ACME123", "last": 101.0,
                               "yield": 4.4}]})
    assert len(hist["ACME123"]["d"]) == 449


def test_merge_cusip_registry_keeps_every_cusip_no_cap():
    # 5100 CUSIPs exceeds the old 5000 registry cap: all must survive.
    day = "2026-10-07"
    reg = finra_corp.merge_cusip_registry(
        None,
        {day: [{"symbol": f"CUSIP{i:05d}", "issuer": "ISSUER",
                "coupon": 4.5, "maturity": "2030-01-15",
                "moodys": "Baa2", "sp": "BBB", "cat": "inv",
                "yield": 5.0, "last": 99.0}
               for i in range(5100)]})
    assert len(reg) == 5100
    assert reg["CUSIP00000"]["last_seen"] == day
    assert reg["CUSIP05099"]["issuer"] == "ISSUER"


def _seed_sample():
    seed = finra_corp._load_seed_registry()
    assert seed, "seed registry must ship with the repo"
    return next(iter(seed.items()))


def test_restore_seed_registry_adds_missing():
    cusip, s = _seed_sample()
    reg = {}
    added = finra_corp.restore_seed_registry(reg)
    assert added == len(finra_corp._load_seed_registry())
    assert cusip in reg
    e = reg[cusip]
    for k in ("issuer", "coupon", "maturity", "moodys", "sp", "cat",
              "last_seen"):
        if s.get(k) not in (None, ""):
            assert e[k] == s[k], k
    assert e["seed_restored"] is True
    # live fields stay absent until the bond reappears in daily lists
    assert "yield" not in e and "price" not in e and "spread_bps" not in e


def test_restore_seed_registry_never_overwrites():
    seed = finra_corp._load_seed_registry()
    cusip = next(iter(seed))
    # registry already holds every seed CUSIP with fresher live data
    reg = {c: {"issuer": "LIVE", "coupon": 9.9, "maturity": "2035-01-01",
               "moodys": "Aaa", "sp": "AAA", "cat": "ig",
               "yield": 4.1, "price": 101.5, "spread_bps": 120,
               "last_seen": "2026-10-07"} for c in seed}
    added = finra_corp.restore_seed_registry(reg)
    assert added == 0
    e = reg[cusip]
    assert e["issuer"] == "LIVE"  # untouched
    assert e["yield"] == 4.1 and e["price"] == 101.5
    assert e["last_seen"] == "2026-10-07"
    assert "seed_restored" not in e


def test_restore_seed_registry_idempotent():
    reg = {}
    first = finra_corp.restore_seed_registry(reg)
    assert first > 0
    n = len(reg)
    second = finra_corp.restore_seed_registry(reg)
    assert second == 0
    assert len(reg) == n  # second run changes nothing
