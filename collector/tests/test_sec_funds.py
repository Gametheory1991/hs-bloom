"""Tests for sec_nmfp.py and sec_focus.py parsers."""
import pytest

from collector.fetchers.sec_nmfp import (
    bucket_allocation,
    discover_latest_zip,
    parse_holdings_tsv,
    parse_series_tsv,
    zip_month_from_name,
    REPO_CATEGORIES,
    TREASURY_CATEGORIES,
)
from collector.fetchers.sec_focus import (
    _latest_quarterly_point,
    _quarter_from_date,
)


# ---------------------------------------------------------------- N-MFP


def test_discover_latest_zip():
    html = '''
    <a href="/files/dera/data/form-n-mfp-data-sets/20260909-20261007_nmfp.zip">2026 September NMFP</a>
    <a href="/files/dera/data/form-n-mfp-data-sets/20260810-20260908_nmfp.zip">2026 August NMFP</a>
    '''
    url = discover_latest_zip(html)
    assert url == "https://www.sec.gov/files/dera/data/form-n-mfp-data-sets/20260909-20261007_nmfp.zip"


def test_discover_latest_zip_none():
    assert discover_latest_zip("<html>no zips here</html>") is None


def test_zip_month_from_name():
    # Oct 2026 posting window -> ~Aug 2026 data (60-day delay)
    assert zip_month_from_name("https://x/20260909-20261007_nmfp.zip") == "202608"
    assert zip_month_from_name("https://x/20250105-20250204_nmfp.zip") == "202412"


def test_parse_series_tsv():
    tsv = (
        "ACCESSION_NUMBER\tMONEYMARKETFUNDCATEGORY\tGOVMONEYMRKTFUNDFLAG\t"
        "FUNDRETAILMONEYMARKETFLAG\tAVERAGEPORTFOLIOMATURITY\tAVERAGELIFEMATURITY\t"
        "NETASSETOFSERIES\n"
        "000123\tGovernment\tY\tY\t25.5\t60.0\t1000000000\n"
        "000124\tPrime\tN\tN\t30.0\t75.5\t500000000\n"
        "000125\tGovernment\tY\tN\t\t\t0\n"  # zero assets -> skipped
    )
    funds = parse_series_tsv(tsv)
    assert len(funds) == 2
    assert funds[0].net_assets == 1e9
    assert funds[0].is_govt is True
    assert funds[0].wam == 25.5
    assert funds[1].category == "Prime"
    assert funds[1].is_govt is False


def test_parse_holdings_tsv():
    tsv = (
        "ACCESSION_NUMBER\tNAMEOFISSUER\tINVESTMENTCATEGORY\tCUSIP_NUMBER\t"
        "EXCLUDINGVALUEOFANYSPONSORSUPP\n"
        "0001\tUS Treasury\tU.S. Treasury Debt\t912828X1\t1000000\n"
        "0001\tJPMorgan\tFinancial Company Commercial Paper\t12345\t500000\n"
        "0002\tUS Treasury\tU.S. Treasury Debt\t912828X1\t2000000\n"
    )
    totals = parse_holdings_tsv(tsv)
    assert totals["U.S. Treasury Debt"] == 3000000
    assert totals["Financial Company Commercial Paper"] == 500000


def test_bucket_allocation():
    repo_cat = next(iter(REPO_CATEGORIES))
    totals = {
        repo_cat: 400.0,
        "U.S. Treasury Debt": 300.0,
        "U.S. Government Agency Debt (if categorized as coupon-paying notes)": 200.0,
        "Financial Company Commercial Paper": 100.0,
    }
    alloc = bucket_allocation(totals)
    assert alloc["repo"] == pytest.approx(40.0)
    assert alloc["treasury"] == pytest.approx(30.0)
    assert alloc["agency"] == pytest.approx(20.0)
    assert alloc["cp"] == pytest.approx(10.0)


def test_bucket_allocation_empty():
    assert bucket_allocation({}) == {}


# ---------------------------------------------------------------- FOCUS / broker-dealer


def test_latest_quarterly_point():
    facts = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {"end": "2026-06-30", "val": 1000, "form": "10-Q"},
                            {"end": "2026-09-30", "val": 1100, "form": "10-Q"},
                            {"end": "2025-12-31", "val": 900, "form": "10-K"},
                            # annual 10-K should not beat a later 10-Q
                            {"end": "2026-09-30", "val": 1150, "form": "8-K"},
                        ]
                    }
                }
            }
        }
    }
    result = _latest_quarterly_point(facts, ["Assets"])
    assert result == ("2026-09-30", 1100.0)


def test_latest_quarterly_point_none():
    assert _latest_quarterly_point({}, ["Assets"]) is None
    assert _latest_quarterly_point({"facts": {}}, ["Assets"]) is None


def test_quarter_from_date():
    assert _quarter_from_date("2026-09-30") == "2026Q3"
    assert _quarter_from_date("2026-01-15") == "2026Q1"
    assert _quarter_from_date("2026-12-31") == "2026Q4"
