"""Tests for fetchers/ai_capex.py (universe-driven XBRL) — synthetic, no network."""
from __future__ import annotations

from datetime import date

import pytest

from collector.fetchers.ai_capex import (
    _flags,
    _one_company,
    _parse_rows,
    fetch_universe_capex,
    load_universe,
    universe_companies,
)


class FakeStore:
    def __init__(self, series=None):
        self.series = series or {}
        self.docs = {}

    def points(self, key, since=None):
        return self.series.get(key, {})

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), d)()


def test_load_universe_generic_schema():
    u = load_universe("ai_buildout")
    assert u["universe_id"] == "ai_buildout"
    assert len(u["verticals"]) >= 10
    # generic: verticals carry group labels, companies carry kind
    for v in u["verticals"]:
        assert {"id", "label", "group", "companies"} <= set(v)
        for c in v["companies"]:
            assert {"id", "name", "kind"} <= set(c)
    ids = [c["id"] for v in u["verticals"] for c in v["companies"]]
    assert len(ids) == len(set(ids)), "duplicate company ids"
    pub = universe_companies(u)
    assert len(pub) >= 55
    assert all(c["cik"] for c in pub)
    # no AI-specific hardcoding needed: edges reference the same ids
    for e in u["edges"]:
        assert e["from"] in ids and e["to"] in ids


def _xbrl(val_map, form="10-Q"):
    return {"units": {"USD": [
        {"end": d.isoformat(), "val": v, "form": form,
         "accn": f"0000000000-26-00000{i}", "filed": d.isoformat()}
        for i, (d, v) in enumerate(val_map)
    ]}}


Q1 = date(2026, 3, 31)
Q2 = date(2026, 6, 30)


def test_parse_rows_keeps_latest_accession():
    rows = [
        {"end": "2026-06-30", "val": 100, "form": "10-Q",
         "accn": "0000000000-26-000001", "filed": "2026-07-01"},
        {"end": "2026-06-30", "val": 110, "form": "10-Q",
         "accn": "0000000000-26-000002", "filed": "2026-08-01"},
        {"end": "2026-06-30", "val": 999, "form": "8-K",
         "accn": "0000000000-26-000003", "filed": "2026-07-01"},
    ]
    assert _parse_rows(rows) == {date(2026, 6, 30): 110.0}


def test_parse_rows_accepts_20f():
    rows = [{"end": "2026-06-30", "val": 5, "form": "20-F",
             "accn": "a", "filed": "2026-07-01"}]
    assert _parse_rows(rows) == {date(2026, 6, 30): 5.0}


def _make_getter():
    import json

    async def get_text(url, headers=None, params=None):
        tag = url.rsplit("/", 1)[-1].replace(".json", "")
        if tag == "Revenues":
            return json.dumps(_xbrl([(Q1, 50e9), (Q2, 60e9)]))
        if tag == "PaymentsToAcquirePropertyPlantAndEquipment":
            return json.dumps(_xbrl([(Q1, 20e9), (Q2, 25e9)]))
        if tag == "NetCashProvidedByUsedInOperatingActivities":
            return json.dumps(_xbrl([(Q1, 10e9), (Q2, 5e9)]))
        if tag == "LongTermDebt":
            return json.dumps(_xbrl([(Q1, 40e9), (Q2, 45e9)]))
        if tag == "Assets":
            return json.dumps(_xbrl([(Q1, 400e9), (Q2, 420e9)]))
        raise AssertionError(f"unexpected tag {tag}")

    return get_text


@pytest.mark.asyncio
async def test_one_company_derived_metrics():
    co = {"id": "nvidia", "name": "Nvidia", "ticker": "NVDA",
          "cik": "0001045810", "kind": "public", "vertical": "chips"}
    res = await _one_company(co, "ua", _make_getter())
    q2 = res["quarters"][Q2]
    assert q2["fcf"] == pytest.approx(5e9 - 25e9)
    assert q2["capex_intensity"] == pytest.approx(25e9 / 60e9)
    assert q2["funding_gap"] == pytest.approx(25e9 - 5e9)


@pytest.mark.asyncio
async def test_ifrs_fallback():
    import json

    async def get_text(url, headers=None, params=None):
        if "us-gaap/Revenues" in url:
            raise RuntimeError("404")
        if "ifrs-full/Revenue" in url:
            return json.dumps(_xbrl([(Q2, 90e9)]))
        if "PaymentsToAcquirePropertyPlantAndEquipment" in url:
            raise RuntimeError("404")
        if "CapitalExpenditures" in url:
            raise RuntimeError("404")
        if "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities" in url:
            return json.dumps(_xbrl([(Q2, 30e9)]))
        raise RuntimeError("404 " + url)

    co = {"id": "tsmc", "name": "TSMC", "ticker": "TSM",
          "cik": "0001046179", "kind": "public", "vertical": "chips"}
    res = await _one_company(co, "ua", get_text)
    assert res["quarters"][Q2]["revenue"] == 90e9
    assert res["quarters"][Q2]["capex"] == 30e9


def test_flags():
    q = {Q1: {"revenue": 60e9, "fcf": 1e9, "capex_intensity": 0.1,
              "funding_gap": 1e9},
         Q2: {"revenue": 60e9, "fcf": -1e9, "capex_intensity": 0.45,
              "funding_gap": 6e9}}
    flags = _flags(q)
    assert "capex_intensity_high" in flags
    assert "funding_gap_large" in flags
    assert "cashflow_negative" not in flags  # only 1 negative quarter
    assert _flags({}) == []


def test_flags_skips_intensity_when_prerevenue():
    # OKLO-style: ~zero revenue makes capex/revenue meaningless
    q = {Q2: {"revenue": 1e6, "fcf": -1e9, "capex_intensity": 100.0,
              "funding_gap": 1e9}}
    assert "capex_intensity_high" not in _flags(q)


@pytest.mark.asyncio
async def test_fetch_universe_capex_uses_universe_not_hardcoded():
    import collector.fetchers.ai_capex as m

    calls = []

    async def fake_one(co, ua, get_text):
        calls.append(co["ticker"])
        return {"quarters": {Q2: {"revenue": 10e9, "capex": 1e9, "ocf": 5e9,
                                 "fcf": 4e9, "capex_intensity": 0.1,
                                 "funding_gap": -4e9}}}

    real = m._one_company
    m._one_company = fake_one
    try:
        store = FakeStore()
        cfg = type("Cfg", (), {"thirteenf": type("T", (), {"user_agent": "ua"})()})()
        out = await fetch_universe_capex(cfg, store, _make_getter(), "ai_buildout")
    finally:
        m._one_company = real
    assert "companies" in out
    assert len(calls) >= 55  # driven by the universe file, not a hardcoded list
    assert "NVDA" in calls and "TSM" in calls
    doc = store.docs["ai_buildout_capex"]["payload"]
    assert doc["universe_id"] == "ai_buildout"
    assert "HYPER6" in doc["aggregates"]
