"""Parser unit tests for the FINRA + Fed fetchers.

Network is fully faked — all tests run against synthetic payloads shaped
like the real APIs (verified live 2026-10-09).
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from collector.fetchers import nyfed_primary_dealers as pdmod
from collector.fetchers import finra_trace_intraday as timod
from collector.fetchers import finra_margin_firms as mfmod


# ---------------------------------------------------------------------------
# NY Fed Primary Dealer Statistics
# ---------------------------------------------------------------------------

PD_CSV = (
    '"As Of Date","Time Series","Value (millions)"\n'
    '"2026-09-30","PDPOSGST-TOT","-12345"\n'
    '"2026-09-30","PDPOSCS-TOT","6789"\n'
    '"2026-09-30","PDGSWOEXTTOT","456789"\n'
    '"2026-09-30","PDPOSCSBND-BELL13C","101"\n'   # not curated -> ignored
    '"2026-09-23","PDPOSGST-TOT","-12000"\n'
    '"2026-09-23","PDGSWOEXTTOT","."\n'           # missing value -> skipped
    '"bad-date","PDPOSGST-TOT","5"\n'              # bad date -> skipped
)


def test_pd_csv_parses_curated_series():
    out = pdmod.parse_pd_csv(PD_CSV)
    ust = out["pd-pos-ust"]
    assert [(d.isoformat(), v) for d, v in ust] == [
        ("2026-09-23", -12000.0), ("2026-09-30", -12345.0)]
    assert out["pd-pos-corp"] == [(date(2026, 9, 30), 6789.0)]
    assert out["pd-txn-ust"] == [(date(2026, 9, 30), 456789.0)]
    # uncurated bucket series ignored, bad rows skipped
    assert all("BELL13C" not in k for k in out)


def test_pd_csv_empty():
    out = pdmod.parse_pd_csv('"As Of Date","Time Series","Value (millions)"\n')
    assert all(v == [] for v in out.values())


def test_pd_asof_list():
    text = json.dumps({"pd": {"asofdates": [
        {"asof": "2026-09-30", "seriesbreak": "SBN2024"},
        {"asof": "2026-09-23", "seriesbreak": "SBN2024"},
        {"asof": None, "seriesbreak": "SBN2024"},
    ]}})
    assert pdmod.parse_asof_list(text) == ["2026-09-23", "2026-09-30"]


# ---------------------------------------------------------------------------
# FINRA TRACE intraday
# ---------------------------------------------------------------------------

def test_normalize_print_full_row():
    row = {"cusip": " 037833100 ", "tradeDateTime": "2026-10-09T14:30:00",
           "tradePrice": "99.5", "tradeYield": "4.25",
           "tradeQuantity": "1000000", "buySell": "B",
           "productType": "Corporate"}
    p = timod.normalize_print(row)
    assert p == {"cusip": "037833100", "timestamp": "2026-10-09T14:30:00",
                 "price": 99.5, "yield": 4.25, "quantity": 1000000.0,
                 "side": "B", "product": "Corporate"}


def test_normalize_print_aliases_and_missing():
    # alternate header spellings + missing optionals
    row = {"CUSIP": "X123", "executionDateTime": "2026-10-09",
           "price": "101"}
    p = timod.normalize_print(row)
    assert p["cusip"] == "X123"
    assert p["yield"] is None and p["quantity"] is None
    # unusable rows -> None
    assert timod.normalize_print({"price": "99"}) is None
    assert timod.normalize_print({"cusip": "X"}) is None


def test_pick_case_insensitive():
    assert timod._pick({"TradePrice": "5"}, "tradeprice", "price") == "5"
    assert timod._pick({"a": ""}, "a", "b") is None


# ---------------------------------------------------------------------------
# FINRA margin concentration
# ---------------------------------------------------------------------------

def _mrow(y, m, debit, cc, cm):
    return {"date": date(y, m, 1), "debit_m": debit,
            "credit_cash_m": cc, "credit_margin_m": cm}


def test_derive_risk_math():
    rows = [_mrow(2025, 10, 1000.0, 300.0, 200.0),   # lev 2.0, net 500
            _mrow(2025, 11, 1100.0, 300.0, 200.0),   # mom +10%
            _mrow(2026, 10, 1200.0, 300.0, 200.0)]   # yoy +20% vs 2025-10
    d = mfmod.derive_risk(rows)
    assert d["finra-margin-levratio"][0] == (date(2025, 10, 1), 2.0)
    assert d["finra-margin-netdebit"][0] == (date(2025, 10, 1), 500.0)
    assert d["finra-margin-debit-mom"][0][1] == pytest.approx(10.0)
    assert d["finra-margin-debit-yoy"][0][1] == pytest.approx(20.0)


def test_derive_risk_zero_credit_safe():
    rows = [_mrow(2026, 1, 500.0, 0.0, 0.0)]
    d = mfmod.derive_risk(rows)
    assert d["finra-margin-levratio"] == []          # no div-by-zero
    assert d["finra-margin-netdebit"] == [(date(2026, 1, 1), 500.0)]


def test_firm_concentration_math():
    firms = [{"firm": f"F{i}", "debit_m": float(100 - i * 10)} for i in range(10)]
    c = mfmod.firm_concentration(firms)
    assert c["n_firms"] == 10
    assert c["top5_share"] == pytest.approx(400.0 / 550.0)
    assert c["top5_firms"][0] == "F0"


def test_firm_table_stub_raises():
    with pytest.raises(NotImplementedError):
        mfmod.parse_firm_table(b"")
