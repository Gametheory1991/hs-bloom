"""Tests for bank quarterly financials (bank_financials.py) and the
_quarterly_points YTD derivation in ai_capex.py."""
from __future__ import annotations

import json
from datetime import date

import pytest

from collector.fetchers.ai_capex import (
    BANK_TAGS,
    _quarterly_points,
)
from collector.fetchers.bank_financials import _one_bank

Q1 = date(2026, 3, 31)
Q2 = date(2026, 6, 30)
Q3 = date(2026, 9, 30)
Q4 = date(2026, 12, 31)


def _row(end, val, form="10-Q", start=None, accn="a1"):
    start = start or f"{end.year}-01-01"
    return {"end": end.isoformat(), "start": start, "val": val,
            "form": form, "accn": accn}


def test_quarterly_points_q1_passthrough():
    rows = [_row(Q1, 10.0)]
    assert _quarterly_points(rows) == {Q1: 10.0}


def test_quarterly_points_deannualizes():
    rows = [_row(Q1, 10.0), _row(Q2, 22.0), _row(Q3, 35.0),
            _row(Q4, 50.0, form="10-K", start="2026-01-01")]
    out = _quarterly_points(rows)
    assert out[Q1] == pytest.approx(10.0)
    assert out[Q2] == pytest.approx(12.0)
    assert out[Q3] == pytest.approx(13.0)
    assert out[Q4] == pytest.approx(15.0)


def test_quarterly_points_gap_is_honest():
    # Q2 missing: Q3 YTD must NOT be differenced against Q1.
    rows = [_row(Q1, 10.0), _row(Q3, 35.0)]
    out = _quarterly_points(rows)
    assert out[Q1] == pytest.approx(10.0)
    assert Q3 not in out


def test_quarterly_points_true_quarterly_facts_used_asis():
    rows = [_row(Q1, 10.0), _row(Q2, 12.0, start="2026-04-01"),
            _row(Q3, 13.0, start="2026-07-01")]
    out = _quarterly_points(rows)
    assert out[Q2] == pytest.approx(12.0)
    assert out[Q3] == pytest.approx(13.0)


def _xbrl_flow(vals):
    """vals: [(end, ytd_val)] -> companyconcept-like payload (YTD facts)."""
    units = [{"end": e.isoformat(), "start": f"{e.year}-01-01",
              "val": v, "form": "10-Q", "accn": "a1"} for e, v in vals]
    return {"units": {"USD": units}}


def _xbrl_stock(vals):
    units = [{"end": e.isoformat(), "start": e.isoformat(),
              "val": v, "form": "10-Q", "accn": "a1"} for e, v in vals]
    return {"units": {"USD": units}}


def _make_getter():
    async def get_text(url, headers=None, params=None):
        tag = url.rsplit("/", 1)[-1].replace(".json", "")
        if tag == "InterestIncomeExpenseNet":
            return json.dumps(_xbrl_flow(
                [(Q1, 10e9), (Q2, 21e9), (Q3, 33e9), (Q4, 46e9)]))
        if tag == "NoninterestIncome":
            return json.dumps(_xbrl_flow(
                [(Q1, 5e9), (Q2, 11e9), (Q3, 16e9), (Q4, 22e9)]))
        if tag == "NoninterestExpense":
            return json.dumps(_xbrl_flow(
                [(Q1, 8e9), (Q2, 17e9), (Q3, 26e9), (Q4, 35e9)]))
        if tag == "NetIncomeLoss":
            return json.dumps(_xbrl_flow(
                [(Q1, 4e9), (Q2, 9e9), (Q3, 14e9), (Q4, 20e9)]))
        if tag == "Deposits":
            return json.dumps(_xbrl_stock(
                [(Q1, 200e9), (Q2, 210e9), (Q3, 215e9), (Q4, 220e9)]))
        if tag == "LoansAndLeasesReceivableNetOfDeferredIncome":
            return json.dumps(_xbrl_stock(
                [(Q1, 120e9), (Q2, 125e9), (Q3, 128e9), (Q4, 130e9)]))
        if tag == "StockholdersEquity":
            return json.dumps(_xbrl_stock(
                [(Q1, 50e9), (Q2, 52e9), (Q3, 54e9), (Q4, 56e9)]))
        if tag == "Assets":
            return json.dumps(_xbrl_stock(
                [(Q1, 400e9), (Q2, 410e9), (Q3, 415e9), (Q4, 420e9)]))
        if tag == "LongTermDebt":
            return json.dumps(_xbrl_stock(
                [(Q1, 60e9), (Q2, 62e9), (Q3, 63e9), (Q4, 64e9)]))
        if tag == "Revenues":
            return json.dumps(_xbrl_flow(
                [(Q1, 20e9), (Q2, 42e9), (Q3, 64e9), (Q4, 88e9)]))
        raise AssertionError(f"unexpected tag {tag}")
    return get_text


@pytest.mark.asyncio
async def test_one_bank_derives_quarters_and_ratios():
    co = {"id": "jpmorgan", "name": "JPMorgan Chase", "ticker": "JPM",
          "cik": "0000019617", "kind": "public", "vertical": "money_center"}
    res = await _one_bank(co, "ua", _make_getter())
    q4 = res["quarters"][Q4]
    # de-annualized: Q4 NII = 46 - 33 = 13
    assert q4["nii"] == pytest.approx(13e9)
    assert q4["net_income"] == pytest.approx(6e9)
    # point-in-time stock items untouched
    assert q4["deposits"] == pytest.approx(220e9)
    # ratios
    assert q4["loan_to_deposit"] == pytest.approx(130e9 / 220e9)
    assert q4["debt_to_equity"] == pytest.approx(64e9 / 56e9)
    # TTM: NII quarters = 10, 11, 12, 13 -> 46
    assert q4["nim_proxy"] == pytest.approx(46e9 / 411.25e9)
    # ROE TTM: net income quarters 4,5,5,6 = 20; avg equity 53
    assert q4["roe_ttm"] == pytest.approx(20e9 / 53e9)


def test_bank_tags_cover_expected_metrics():
    assert set(BANK_TAGS) >= {"nii", "nonint_income", "net_income",
                              "deposits", "loans", "equity", "assets",
                              "debt", "revenue"}
    # JPM-verified NII concept first
    assert BANK_TAGS["nii"][0] == ("us-gaap", "InterestIncomeExpenseNet")


def test_bank_universe_ciks_present():
    import json as _json
    u = _json.load(open("collector/src/collector/data/bank_fixed_income.json"))
    missing = []
    for v in u["verticals"]:
        for c in v["companies"]:
            if c.get("kind") == "public" and not c.get("cik"):
                missing.append(c.get("ticker"))
    assert missing == []
