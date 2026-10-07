"""Tests for the AI Financials extension (three-statement XBRL set).

Covers: new TAGS entries, _quarterly_flow_rows true-quarterly reduction,
derived margins/leverage/YoY in _one_company, the _ai_financials_panel,
and the ai_buildout: /api/series branch. Synthetic only, no network.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from collector.api import create_app
from collector.config import load_config
from collector.fetchers.ai_capex import (
    FLOW_METRICS,
    TAGS,
    _one_company,
    _quarterly_flow_rows,
)
from collector.panels import _ai_financials_panel
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]

Q1 = date(2026, 3, 31)
Q2 = date(2026, 6, 30)
Q3 = date(2025, 9, 30)
Q4 = date(2025, 12, 31)


def _row(end, val, start=None, form="10-Q", frame=None, accn="a"):
    r = {"end": end.isoformat(), "val": val, "form": form,
         "accn": accn, "filed": end.isoformat()}
    if start:
        r["start"] = start.isoformat()
    if frame:
        r["frame"] = frame
    return r


def test_tags_cover_three_statements():
    for m in ("gross_profit", "op_income", "net_income", "rd",
              "cash", "equity", "stdebt"):
        assert m in TAGS, f"missing TAGS entry: {m}"
        assert m in FLOW_METRICS or m in ("cash", "equity", "stdebt")
        for tax, tag in TAGS[m]:
            assert isinstance(tax, str) and isinstance(tag, str)
    assert "revenue" in FLOW_METRICS and "capex" in FLOW_METRICS
    assert "assets" not in FLOW_METRICS  # instant concept keeps old path


def test_quarterly_prefers_standalone_over_ytd():
    rows = [
        _row(Q2, 177.8, start=date(2026, 1, 1)),                    # YTD
        _row(Q2, 96.2, start=date(2026, 4, 1), frame="CY2026Q2"),  # quarter
    ]
    assert _quarterly_flow_rows(rows) == {Q2: 96.2}


def test_quarterly_prefers_frame_over_duration_guess():
    rows = [_row(Q1, 50.0, start=date(2026, 1, 1), frame="CY2026Q1")]
    assert _quarterly_flow_rows(rows) == {Q1: 50.0}


def test_quarterly_derives_fiscal_q4():
    rows = [
        _row(Q3, 147.8, start=date(2025, 1, 1)),                       # 9M YTD
        _row(Q4, 215.9, start=date(2025, 1, 1), form="10-K",
             frame="CY2025"),                                          # annual
    ]
    got = _quarterly_flow_rows(rows)
    assert got[Q4] == pytest.approx(215.9 - 147.8)
    assert Q3 not in got  # YTD-only quarter is an honest gap


def test_quarterly_skips_ytd_only():
    rows = [_row(Q2, 74.4, start=date(2026, 1, 1))]  # 6M YTD, no quarter
    assert _quarterly_flow_rows(rows) == {}


def test_quarterly_handles_synthetic_facts_without_start():
    # the existing _xbrl test helper emits no start/frame: 10-Q => quarterly
    rows = [_row(Q1, 10.0), _row(Q2, 12.0)]
    assert _quarterly_flow_rows(rows) == {Q1: 10.0, Q2: 12.0}


def test_quarterly_latest_accession_wins():
    rows = [_row(Q1, 10.0, accn="a"), _row(Q1, 11.0, accn="b")]
    assert _quarterly_flow_rows(rows) == {Q1: 11.0}


def _xbrl(val_map, form="10-Q"):
    return {"units": {"USD": [
        {"end": d.isoformat(), "val": v, "form": form,
         "accn": f"0000000000-26-00000{i}", "filed": d.isoformat()}
        for i, (d, v) in enumerate(val_map)
    ]}}


def _make_getter():
    async def get_text(url, headers=None, params=None):
        tag = url.rsplit("/", 1)[-1].replace(".json", "")
        table = {
            "Revenues": [(Q1, 100e9), (Q2, 120e9)],
            "GrossProfit": [(Q1, 70e9), (Q2, 84e9)],
            "OperatingIncomeLoss": [(Q1, 40e9), (Q2, 48e9)],
            "NetIncomeLoss": [(Q1, 30e9), (Q2, 36e9)],
            "ResearchAndDevelopmentExpense": [(Q1, 10e9), (Q2, 12e9)],
            "CashAndCashEquivalentsAtCarryingValue": [(Q1, 20e9), (Q2, 22e9)],
            "StockholdersEquity": [(Q1, 200e9), (Q2, 210e9)],
            "LongTermDebt": [(Q1, 40e9), (Q2, 44e9)],
            "DebtCurrent": [(Q1, 5e9), (Q2, 6e9)],
            "Assets": [(Q1, 400e9), (Q2, 420e9)],
            "PaymentsToAcquirePropertyPlantAndEquipment": [(Q1, 15e9), (Q2, 18e9)],
            "NetCashProvidedByUsedInOperatingActivities": [(Q1, 35e9), (Q2, 40e9)],
        }
        if tag in table:
            return json.dumps(_xbrl(table[tag]))
        raise AssertionError(f"unexpected tag {tag}")

    return get_text


@pytest.mark.asyncio
async def test_one_company_derives_margins_and_leverage():
    co = {"id": "nvidia", "name": "Nvidia", "ticker": "NVDA",
          "cik": "0001045810", "kind": "public", "vertical": "chips"}
    res = await _one_company(co, "ua", _make_getter())
    q2 = res["quarters"][Q2]
    assert q2["gross_margin"] == pytest.approx(84e9 / 120e9)
    assert q2["op_margin"] == pytest.approx(48e9 / 120e9)
    assert q2["net_margin"] == pytest.approx(36e9 / 120e9)
    assert q2["rd_intensity"] == pytest.approx(12e9 / 120e9)
    assert q2["fcf"] == pytest.approx(40e9 - 18e9)
    assert q2["fcf_margin"] == pytest.approx((40e9 - 18e9) / 120e9)
    assert q2["total_debt"] == pytest.approx(44e9 + 6e9)
    assert q2["net_debt"] == pytest.approx(44e9 + 6e9 - 22e9)
    assert q2["debt_to_equity"] == pytest.approx(50e9 / 210e9)
    # YoY needs a prior-year quarter: not present in 2-quarter fixture
    assert "rev_yoy" not in q2


@pytest.mark.asyncio
async def test_yoy_matches_same_month_prior_year():
    async def get_text(url, headers=None, params=None):
        tag = url.rsplit("/", 1)[-1].replace(".json", "")
        if tag == "Revenues":
            return json.dumps(_xbrl([(date(2025, 6, 30), 100e9),
                                     (date(2026, 6, 30), 120e9)]))
        # every other tag: single quarter so only revenue YoY is testable
        if tag in ("GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
                   "ResearchAndDevelopmentExpense",
                   "CashAndCashEquivalentsAtCarryingValue",
                   "StockholdersEquity", "LongTermDebt", "DebtCurrent",
                   "Assets", "PaymentsToAcquirePropertyPlantAndEquipment",
                   "NetCashProvidedByUsedInOperatingActivities"):
            return json.dumps(_xbrl([(date(2026, 6, 30), 1e9)]))
        raise AssertionError(tag)

    co = {"id": "x", "name": "X", "ticker": "X",
          "cik": "0000000000", "kind": "public", "vertical": "v"}
    res = await _one_company(co, "ua", get_text)
    q = res["quarters"][date(2026, 6, 30)]
    assert q["rev_yoy"] == pytest.approx(0.20)


class _Doc:
    def __init__(self, payload):
        self.payload = payload
        self.updated_at = None
        self.source = "test"


class _Store:
    def __init__(self, docs=None, series=None):
        self._docs = docs or {}
        self._series = series or {}

    def doc(self, key):
        d = self._docs.get(key)
        return _Doc(d) if d is not None else None

    def points(self, key, since=None):
        return self._series.get(key, {})


def test_panel_empty_state():
    p = _ai_financials_panel(_Store())
    assert p["companies"] == {} and p["as_of"] is None


def test_panel_passthrough():
    payload = {"as_of": "2026-10-07", "universe_id": "ai_buildout",
               "companies": {"NVDA": {"revenue": 1.0}},
               "aggregates": {}}
    p = _ai_financials_panel(_Store(docs={"ai_buildout_capex": payload}))
    assert p["companies"]["NVDA"]["revenue"] == 1.0
    assert p["as_of"] == "2026-10-07"


def test_series_branch_serves_universe_metrics(tmp_path):
    store = Store(tmp_path / "t.db")
    cfg = load_config(REPO_ROOT / "config.yaml")
    client = TestClient(create_app(store, cfg))
    store.upsert_points("ai_buildout:NVDA:revenue",
                        [(date(2026, 6, 30), 120e9)])
    body = client.get("/api/series/ai_buildout:NVDA:revenue?range=max").json()
    assert body["unit"] == "$"
    assert body["points"] == [["2026-06-30", 120e9]]
    body = client.get("/api/series/ai_buildout:NVDA:net_margin?range=max").json()
    assert body["unit"] == "ratio"
    assert client.get("/api/series/ai_buildout:nvda:revenue").status_code == 404
    assert client.get("/api/dashboard?hub=structure").json()[
        "panels"].keys() >= {"ai_financials"}
