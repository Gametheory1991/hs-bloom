"""Tests for the BDC XBRL fundamentals fetcher (pure extraction logic)."""
from datetime import date

from collector.fetchers.bdc_financials import (
    _instant_points,
    _qtd_points,
    _ytd_points,
    extract_fundamentals,
    quarterize_ytd,
)


def _p(end, val, start=None, filed="2026-08-01", fp="Q2", form="10-Q"):
    return {"end": end, "start": start, "val": val, "filed": filed,
            "fp": fp, "form": form}


def _payload(concepts: dict) -> dict:
    return {"facts": {"us-gaap": {
        c: {"units": {"USD": pts}} for c, pts in concepts.items()}}}


def test_instant_earliest_filed_wins():
    facts = _payload({"Assets": [
        _p("2026-06-30", 31_000_000_000, filed="2026-08-05"),
        _p("2026-06-30", 30_498_000_000, filed="2026-07-29"),
        _p("2026-03-31", 30_679_000_000, filed="2026-04-28"),
    ]})["facts"]
    pts, used = _instant_points(facts, ("Assets",))
    assert used == "Assets"
    assert pts == [(date(2026, 3, 31), 30679000000.0),
                   (date(2026, 6, 30), 30498000000.0)]


def test_instant_falls_back_to_next_concept():
    facts = _payload({"LongTermDebtNoncurrent": [
        _p("2026-06-30", 5_000_000_000),
    ]})["facts"]
    pts, used = _instant_points(facts, ("LongTermDebt",
                                        "LongTermDebtNoncurrent"))
    assert used == "LongTermDebtNoncurrent"
    assert pts == [(date(2026, 6, 30), 5000000000.0)]


def test_qtd_filters_ytd_out():
    facts = _payload({"NetInvestmentIncome": [
        _p("2026-06-30", 771_000_000, start="2026-01-01"),  # 180d YTD
        _p("2026-06-30", 367_000_000, start="2026-04-01"),  # 90d QTD
        _p("2026-03-31", 404_000_000, start="2026-01-01"),  # 89d QTD
    ]})["facts"]
    pts, used = _qtd_points(facts, ("NetInvestmentIncome",))
    assert used == "NetInvestmentIncome"
    assert pts == [(date(2026, 3, 31), 404000000.0),
                   (date(2026, 6, 30), 367000000.0)]


def test_ytd_bands_and_quarterize():
    facts = _payload({"PaymentsOfDividends": [
        _p("2026-03-31", 345_000_000, start="2026-01-01"),
        _p("2026-06-30", 690_000_000, start="2026-01-01"),
        _p("2026-09-30", 945_000_000, start="2026-01-01"),
        _p("2026-12-31", 1_264_000_000, start="2026-01-01"),
    ]})["facts"]
    ytd, used = _ytd_points(facts, ("PaymentsOfDividends",))
    assert used == "PaymentsOfDividends"
    assert ytd[date(2026, 6, 30)][0] == 2  # H1 band
    assert ytd[date(2026, 12, 31)][0] == 4  # FY band
    q = quarterize_ytd(ytd, 12)
    assert q == [(date(2026, 3, 31), 345000000.0),
                 (date(2026, 6, 30), 345000000.0),
                 (date(2026, 9, 30), 255000000.0),
                 (date(2026, 12, 31), 319000000.0)]


def test_quarterize_skips_when_prior_band_missing():
    ytd = {date(2026, 6, 30): (2, 690_000_000.0)}  # H1 without Q1
    assert quarterize_ytd(ytd, 12) == []


def test_extract_fundamentals_end_to_end():
    payload = _payload({
        "Assets": [_p("2026-06-30", 30_498_000_000, fp="Q2")],
        "Liabilities": [_p("2026-06-30", 16_607_000_000, fp="Q2")],
        "StockholdersEquity": [_p("2026-06-30", 13_891_000_000, fp="Q2")],
        "LongTermDebt": [_p("2026-06-30", 15_773_000_000, fp="Q2")],
        "InvestmentOwnedAtFairValue": [_p("2026-06-30", 29_000_000_000, fp="Q2")],
        "NetAssetValuePerShare": [_p("2026-06-30", 19.35, fp="Q2")],
        "NetInvestmentIncome": [
            _p("2026-06-30", 367_000_000, start="2026-04-01", fp="Q2"),
            _p("2026-03-31", 404_000_000, start="2026-01-01", fp="Q1"),
        ],
        "GrossInvestmentIncomeOperating": [
            _p("2026-06-30", 768_000_000, start="2026-04-01", fp="Q2"),
        ],
        "PaymentsOfDividends": [
            _p("2026-03-31", 345_000_000, start="2026-01-01", fp="Q1"),
            _p("2026-06-30", 690_000_000, start="2026-01-01", fp="Q2"),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            _p("2026-03-31", 184_000_000, start="2026-01-01", fp="Q1"),
            _p("2026-06-30", 520_000_000, start="2026-01-01", fp="Q2"),
        ],
        "NetCashProvidedByUsedInFinancingActivities": [
            _p("2026-03-31", -100_000_000, start="2026-01-01", fp="Q1"),
        ],
    })
    res = extract_fundamentals(payload)
    assert res["assets"][0] == [(date(2026, 6, 30), 30498000000.0)]
    assert res["assets"][1] == "Assets"
    assert res["nii"][0] == [(date(2026, 3, 31), 404000000.0),
                             (date(2026, 6, 30), 367000000.0)]
    # dividends quarterized from YTD
    assert res["dist"][0] == [(date(2026, 3, 31), 345000000.0),
                              (date(2026, 6, 30), 345000000.0)]
    # ocf quarterized; fcf Q1 only
    assert res["ocf"][0] == [(date(2026, 3, 31), 184000000.0),
                             (date(2026, 6, 30), 336000000.0)]
    assert res["fcf"][0] == [(date(2026, 3, 31), -100000000.0)]
    # missing concept -> empty points, None tag
    assert res["coverage"][0] == [] and res["coverage"][1] is None
