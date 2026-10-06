"""Debt-outstanding cube: pure-function tests + mspd fetcher-level test.

Network is fully faked. The cube math is tested against synthetic CUSIP rows
covering all five marketable products, non-marketables, boundary maturities,
and a soma-exceeds-outstanding data quirk.
"""
from __future__ import annotations

import json

import pytest

from collector import debt_cube
from collector.debt_cube import build_debt_cube, query_cube, refresh_debt_cube
from collector.fetchers import mspd
from collector.fetchers import soma_cusip
from collector.store import Store

ASOF = "2026-08-31"


def _m(cusip, product_raw, maturity, outstanding_mn, marketable=True):
    return {
        "cusip": cusip,
        "product_raw": product_raw,
        "maturity_date": maturity,
        "issue_date": "2020-01-01",
        "outstanding_mn": outstanding_mn,
        "inflation_adj_mn": outstanding_mn,
        "security_type": "Marketable" if marketable else "Nonmarketable",
    }


def _s(cusip, par_dollars):
    return {"cusip": cusip, "security_type": "Notes", "maturity_date": "2029-08-31",
            "par_value_dollars": par_dollars, "pct_outstanding": 0.0}


def _fixture_rows():
    mspd_rows = [
        _m("91282AAA1", "Notes", "2029-08-31", 1000.0),          # 3-5Y
        _m("91282BBB2", "Notes", "2029-09-01", 500.0),           # 3-5Y (boundary)
        _m("912797CCC", "Bills Maturity Value", "2026-12-31", 300.0),  # <1Y
        _m("912810DDD", "Inflation-Protected Securities", "2036-08-31", 400.0),  # 10-20Y
        _m("91282EEEE", "Floating Rate Notes", "2027-02-27", 200.0),   # <1Y
        _m("912810FFF", "Bonds", "2056-08-31", 600.0),           # 20Y+
        _m("GAS-AGG", "Government Account Series", "", 700.0, marketable=False),
    ]
    soma_rows = [
        _s("91282AAA1", 200_000_000),   # 200 mn of the 1000 mn notes cell
        _s("912797CCC", 50_000_000),
        _s("912810DDD", 100_000_000),
        _s("912810FFF", 600_000_000),   # fully held by SOMA -> public floors to 0
    ]
    return mspd_rows, soma_rows


@pytest.fixture()
def cube():
    mspd_rows, soma_rows = _fixture_rows()
    # marketable 3000 mn + non-marketable 700 mn = 3700 mn denominator
    return build_debt_cube(mspd_rows, soma_rows, 3700.0, ASOF)


# ----------------------------------------------------------- (a) no double-count

def test_soma_plus_public_equals_marketable_per_cell(cube):
    soma_public = [c for c in cube["cells"] if c["holder"] in ("soma", "public")]
    total_bn = sum(c["notional_bn"] for c in soma_public)
    assert total_bn == pytest.approx(3.0)  # exactly the 3000 mn of marketable stock
    # per-product spot check: the two notes rows share the 3-5Y cell
    notes_35 = query_cube(cube, product="notes", maturity="3-5Y")
    by_holder = {c["holder"]: c["notional_bn"] for c in notes_35}
    assert by_holder == {"soma": 0.2, "public": 1.3}  # 200 + (800+500) mn


# ----------------------------------------------------------- (b) pct sums to 100

def test_pct_of_total_sums_to_100(cube):
    assert sum(c["pct_of_total"] for c in cube["cells"]) == pytest.approx(100.0, abs=0.01)


# ----------------------------------------------------------- (c) bucket assignment

def test_bucket_assignment_tips_and_frn(cube):
    tips = query_cube(cube, product="tips")
    assert {c["maturity"] for c in tips} == {"10-20Y"}
    frn = query_cube(cube, product="frns")
    assert {c["maturity"] for c in frn} == {"<1Y"}
    assert {c["maturity"] for c in query_cube(cube, product="bills")} == {"<1Y"}
    assert {c["maturity"] for c in query_cube(cube, product="bonds")} == {"20Y+"}


def test_maturity_bucket_boundaries():
    from collector.debt_cube import maturity_bucket
    from datetime import date
    asof = date(2026, 8, 31)
    assert maturity_bucket(date(2027, 8, 30), asof) == "<1Y"
    assert maturity_bucket(date(2027, 9, 1), asof) == "1-3Y"  # 366d / 365.25y > 1
    assert maturity_bucket(date(2029, 8, 31), asof) == "3-5Y"
    assert maturity_bucket(date(2036, 8, 31), asof) == "10-20Y"
    assert maturity_bucket(date(2046, 8, 31), asof) == "20Y+"


# ----------------------------------------------------------- (d) non-marketable never soma

def test_nonmarketable_rows_never_get_soma_holder(cube):
    nm = query_cube(cube, product="nonmarketable")
    assert len(nm) == 1
    assert nm[0]["holder"] == "nonmarketable"
    assert nm[0]["maturity"] == "all"
    assert nm[0]["notional_bn"] == pytest.approx(0.7)
    assert not [c for c in cube["cells"] if c["holder"] == "nonmarketable"
                and c["product"] != "nonmarketable"]
    assert all(c["product"] in debt_cube.PRODUCTS + ("nonmarketable",)
               for c in cube["cells"])


# ----------------------------------------------------------- (e) soma > outstanding floor

def test_public_floored_at_zero_when_soma_exceeds_outstanding():
    rows = [_m("91282ZZZ9", "Notes", "2028-08-31", 100.0)]
    soma = [_s("91282ZZZ9", 150_000_000)]  # data quirk: SOMA par > outstanding
    c = build_debt_cube(rows, soma, 100.0, ASOF)
    assert query_cube(c, product="notes", holder="public") == []  # floored, not negative
    soma_cells = query_cube(c, product="notes", holder="soma")
    assert len(soma_cells) == 1 and soma_cells[0]["notional_bn"] == pytest.approx(0.15)
    assert all(cell["notional_bn"] >= 0 for cell in c["cells"])


# ----------------------------------------------------------- (f) query filters

def test_query_cube_filters(cube):
    assert len(query_cube(cube, product="notes")) == 2
    assert len(query_cube(cube, holder="soma")) == 4
    assert len(query_cube(cube, maturity="<1Y")) == 3  # bills soma/public + frn public
    assert len(query_cube(cube)) == len(cube["cells"])
    assert query_cube(cube, product="tips", holder="soma")[0]["notional_bn"] == 0.1
    assert query_cube(cube, product="zzz") == []


# ----------------------------------------------------------- refresh_debt_cube

def _write_docs(store):
    mspd_rows, soma_rows = _fixture_rows()
    store.put_doc("mspd_cusips", {"asof": ASOF, "rows": mspd_rows}, source="t")
    store.put_doc("soma_cusips", {"asof": ASOF, "rows": soma_rows}, source="t")
    store.put_doc("mspd_table1", {"asof": ASOF, "rows": [
        {"security_class": "Total Public Debt Outstanding", "total_mn": 3700.0},
    ]}, source="t")


def test_refresh_debt_cube_skips_silently_when_docs_missing(tmp_path):
    store = Store(tmp_path / "t.db")
    assert refresh_debt_cube(store) == "debt-cube-skipped"
    assert store.doc("debt_cube") is None  # no error raised, nothing written


def test_refresh_debt_cube_writes_doc(tmp_path):
    store = Store(tmp_path / "t.db")
    _write_docs(store)
    assert refresh_debt_cube(store) == "debt-cube"
    doc = store.doc("debt_cube")
    assert doc is not None and doc.payload["asof"] == ASOF
    assert sum(c["pct_of_total"] for c in doc.payload["cells"]) == pytest.approx(100.0, abs=0.01)


# ----------------------------------------------------------- mspd fetcher-level test

_TABLE3_ROWS = [
    {"record_date": "2026-08-31", "security_class1_desc": "Notes",
     "security_class2_desc": "91282ABC1", "issue_date": "2025-08-15",
     "maturity_date": "2035-08-15", "outstanding_amt": "100000.5",
     "inflation_adj_amt": "0", "security_type_desc": "Marketable"},
    {"record_date": "2026-08-31", "security_class1_desc": "Inflation-Protected Securities",
     "security_class2_desc": "912810XYZ", "issue_date": "2020-01-15",
     "maturity_date": "2030-01-15", "outstanding_amt": "50000",
     "inflation_adj_amt": "62000.25", "security_type_desc": "Marketable"},
    # total/summary rows must be skipped
    {"record_date": "2026-08-31", "security_class1_desc": "Total Marketable",
     "security_class2_desc": "Total", "outstanding_amt": "99999999",
     "security_type_desc": "Marketable"},
    {"record_date": "2026-08-31", "security_class1_desc": "Notes",
     "security_class2_desc": "", "outstanding_amt": "1",
     "security_type_desc": "Marketable"},
    # stale record_date row must be filtered out
    {"record_date": "2026-07-31", "security_class1_desc": "Notes",
     "security_class2_desc": "91282OLD1", "outstanding_amt": "42",
     "security_type_desc": "Marketable"},
]

_TABLE1_ROWS = [
    {"record_date": "2026-08-31", "security_type_desc": "Marketable",
     "security_class_desc": "Notes", "total_mil_amt": "16218400",
     "debt_held_public_mil_amt": "15000000", "intragov_hold_mil_amt": "1218400"},
    {"record_date": "2026-08-31", "security_type_desc": None,
     "security_class_desc": "Total Public Debt Outstanding",
     "total_mil_amt": "40175600", "debt_held_public_mil_amt": "33000000",
     "intragov_hold_mil_amt": "7175600"},
]


def _fake_mspd_get():
    async def fake(url, params=None, headers=None):
        if "mspd_table_3" in url:
            return json.dumps({"data": _TABLE3_ROWS})
        if "mspd_table_1" in url:
            return json.dumps({"data": _TABLE1_ROWS})
        raise RuntimeError(f"unexpected url {url}")
    return fake


async def test_fetch_mspd_parses_table3_skips_totals(tmp_path):
    from datetime import date
    store = Store(tmp_path / "t.db")
    assert await mspd.fetch_mspd(store, _fake_mspd_get()) == "mspd"

    doc = store.doc("mspd_cusips")
    assert doc.payload["asof"] == "2026-08-31"
    rows = doc.payload["rows"]
    assert [r["cusip"] for r in rows] == ["91282ABC1", "912810XYZ"]
    assert rows[0]["product_raw"] == "Notes"
    assert rows[0]["outstanding_mn"] == 100000.5
    assert rows[1]["inflation_adj_mn"] == 62000.25
    assert rows[1]["security_type"] == "Marketable"

    t1 = store.doc("mspd_table1")
    assert t1.payload["asof"] == "2026-08-31"
    assert mspd.total_public_debt_outstanding(t1.payload["rows"]) == 40175600.0
    # cycle series carries the live Table 1 total as the cube denominator source
    assert store.points("cycle:mspd-total-outstanding")[date(2026, 8, 31)] == 40175600.0


# ----------------------------------------------------------- soma fetcher-level test

_SOMA_CSV = """As Of Date,CUSIP,Security Type,Maturity Date,Coupon,Par Value ($),Current Face Value,Inflation Compensation,Percent Outstanding,Change From Prior Week,Change From Prior Year
09/30/2026,91282ABC1,NotesBonds,08/15/2035,4.0,"200,000,000",200000000,0,0.2,0,0
09/30/2026,912810XYZ,TIPS,01/15/2030,0.125,"100,000,000",100000000,24000000,0.16,0,0
"""


async def test_fetch_soma_cusip_parses_csv(tmp_path):
    from datetime import date
    seen = []

    async def fake(url, params=None, headers=None):
        seen.append(url)
        if url.endswith("latest.json"):
            return json.dumps({"asofdates": [{"asOfDate": "2026-09-30"}]})
        return _SOMA_CSV

    store = Store(tmp_path / "t.db")
    assert await soma_cusip.fetch_soma_cusip(store, fake) == "soma-cusip"
    doc = store.doc("soma_cusips")
    assert doc.payload["asof"] == "2026-09-30"
    rows = doc.payload["rows"]
    assert [r["cusip"] for r in rows] == ["91282ABC1", "912810XYZ"]
    assert rows[0]["par_value_dollars"] == 200_000_000.0
    assert rows[1]["pct_outstanding"] == pytest.approx(0.16)
    assert store.points("cycle:soma-total-par")[date(2026, 9, 30)] == pytest.approx(0.3)
