"""Tests for the FINRA Treasury TRACE fetcher (venue splits, maturity
buckets, VWAP, deep backfill).

Builds minimal Treasury-aggregates-shaped XLSX files in-memory (no
network, no fixtures) and verifies parse_workbook series naming/values,
the month-end ownership rule, and the full-backfill flow with a fake
get_bytes. The batch6 fixture files are not in the repo, so these tests
do not depend on them.
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from collector.fetchers import trace_treasury
from collector.fetchers.trace_treasury import (
    _is_month_end,
    _needs_backfill,
    _store_day,
    fetch_trace_treasury,
    parse_workbook,
)
from collector.store import Store

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)


def _sheet_xml(rows: list[list[str]]) -> str:
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;"))

    out = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, val in enumerate(row):
            col = chr(65 + ci)
            cells.append(
                f'<c r="{col}{ri}" t="inlineStr"><is><t>{esc(val)}</t></is></c>')
        out.append(f'<row r="{ri}">{"".join(cells)}</row>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<worksheet xmlns="{NS}"><sheetData>{"".join(out)}'
            f'</sheetData></worksheet>')


def _xlsx(rows: list[list[str]]) -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{NS}"><sheets>'
        f'<sheet name="Sheet1" sheetId="1" r:id="rId1" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        f'</sheets></workbook>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))
    return buf.getvalue()


def _rows(title: str, vwap: bool = True, scale: float = 1.0) -> list[list[str]]:
    """Treasury-aggregates-shaped grid. Columns: Category | Trades ATS&ID |
    Par ATS&ID | Trades D2C | Par D2C | Trades Total | Par Total | [VWAP]."""
    def sc(v: str) -> str:
        if scale == 1.0:
            return v
        try:
            return str(float(v) * scale)
        except ValueError:
            return v

    head = ["Category", "ATS & Interdealer", "", "Dealer to Customer", "",
            "Total", ""]
    sub = ["", "Trades", "Par Value", "Trades", "Par Value", "Trades",
           "Par Value"]
    body = [
        ["Bills", "100", "10.0", "200", "20.0", "300", "30.0", ""],
        ["FRNs", "10", "0.1", "20", "0.2", "30", "0.3", ""],
        ["Nominal Coupons", "1000", "100.0", "500", "50.0", "1500", "150.0", ""],
        ["<= 2 years", "400", "40.0", "200", "20.0", "600", "60.0", ""],
        ["On-the-run", "300", "30.0", "100", "10.0", "400", "40.0", "99.5"],
        ["Off-the-run", "100", "10.0", "100", "10.0", "200", "20.0", ""],
        ["> 3 years and <= 5 years", "600", "60.0", "300", "30.0", "900", "90.0", ""],
        ["On-the-run", "500", "50.0", "200", "20.0", "700", "70.0", "98.25"],
        ["Off-the-run", "100", "10.0", "100", "10.0", "200", "20.0", ""],
        ["TIPS", "50", "5.0", "60", "6.0", "110", "11.0", ""],
        ["<= 5 years", "50", "5.0", "60", "6.0", "110", "11.0", ""],
        ["On-the-run", "30", "3.0", "40", "4.0", "70", "7.0", ""],
        ["Off-the-run", "20", "2.0", "20", "2.0", "40", "4.0", ""],
        ["Total", "1160", "115.1", "780", "76.2", "1940", "191.3", ""],
    ]
    body = [[c if i == 0 else sc(c) for i, c in enumerate(r)] for r in body]
    if not vwap:
        head = head[:-1]
        sub = sub[:-1]
        body = [r[:-1] for r in body]
    return ([["TRACE Volumes - " + title]] + [[]] * 2 + [head, sub] + body)


def test_parse_daily_categories_venues_buckets_vwap():
    asof, vals = parse_workbook(_xlsx(_rows("October 05, 2026")))
    assert asof == date(2026, 10, 5)
    # original keys keep exact IDs/values
    assert vals["trace-ust-par"] == pytest.approx(191.3)
    assert vals["trace-ust-trades"] == pytest.approx(1940.0)
    assert vals["trace-ust-bills-par"] == pytest.approx(30.0)
    assert vals["trace-ust-frns-par"] == pytest.approx(0.3)
    assert vals["trace-ust-coupons-par"] == pytest.approx(150.0)
    assert vals["trace-ust-tips-par"] == pytest.approx(11.0)
    assert vals["trace-ust-onrun-par"] == pytest.approx(40.0 + 70.0 + 7.0)
    assert vals["trace-ust-offrun-par"] == pytest.approx(20.0 + 20.0 + 4.0)
    # venue splits
    assert vals["trace-ust-par-ats"] == pytest.approx(115.1)
    assert vals["trace-ust-par-d2c"] == pytest.approx(76.2)
    assert vals["trace-ust-trades-ats"] == pytest.approx(1160.0)
    assert vals["trace-ust-trades-d2c"] == pytest.approx(780.0)
    assert vals["trace-ust-bills-par-ats"] == pytest.approx(10.0)
    assert vals["trace-ust-bills-trades-d2c"] == pytest.approx(200.0)
    assert vals["trace-ust-coupons-trades"] == pytest.approx(1500.0)
    assert vals["trace-ust-tips-trades-ats"] == pytest.approx(50.0)
    # maturity buckets (incl. the 5Y bucket Harry requires)
    assert vals["trace-ust-coupons-le2y-par"] == pytest.approx(60.0)
    assert vals["trace-ust-coupons-le2y-trades"] == pytest.approx(600.0)
    assert vals["trace-ust-coupons-le2y-par-ats"] == pytest.approx(40.0)
    assert vals["trace-ust-coupons-le2y-trades-d2c"] == pytest.approx(200.0)
    assert vals["trace-ust-coupons-3y5y-par"] == pytest.approx(90.0)
    assert vals["trace-ust-coupons-3y5y-trades"] == pytest.approx(900.0)
    assert vals["trace-ust-tips-le5y-par"] == pytest.approx(11.0)
    # VWAP from the on-the-run sub-rows (daily files only)
    assert vals["trace-ust-coupons-le2y-vwap"] == pytest.approx(99.5)
    assert vals["trace-ust-coupons-3y5y-vwap"] == pytest.approx(98.25)
    # on-the-run / off-the-run trade counts summed across buckets
    assert vals["trace-ust-onrun-trades"] == pytest.approx(400 + 700 + 70)
    assert vals["trace-ust-offrun-trades"] == pytest.approx(200 + 200 + 40)


def test_parse_monthly_has_no_vwap():
    asof, vals = parse_workbook(_xlsx(_rows("September 30, 2026", vwap=False)))
    assert asof == date(2026, 9, 30)
    assert vals["trace-ust-par"] == pytest.approx(191.3)
    assert vals["trace-ust-coupons-3y5y-par"] == pytest.approx(90.0)
    assert vals["trace-ust-par-ats"] == pytest.approx(115.1)
    assert not any(k.endswith("-vwap") for k in vals)


def test_parse_missing_category_raises():
    rows = _rows("October 05, 2026")
    rows = [r for r in rows if not (r and r[0] == "Bills")]
    with pytest.raises(ValueError, match="bills"):
        parse_workbook(_xlsx(rows))


def test_is_month_end():
    assert _is_month_end(date(2026, 9, 30)) is True
    assert _is_month_end(date(2026, 10, 5)) is False
    assert _is_month_end(date(2024, 2, 29)) is True  # leap day


def test_store_day_skips_month_end():
    store = Store(":memory:")
    _store_day(store, date(2026, 10, 5),
               {"trace-ust-par": 100.0, "trace-ust-par-ats": 60.0})
    assert store.points("cycle:trace-ust-par") == {date(2026, 10, 5): 100.0}
    # month-end dates are owned by the monthly files: daily values must not
    # clobber the monthly totals the grid divides by trading days
    _store_day(store, date(2026, 9, 30),
               {"trace-ust-par": 999.0, "trace-ust-par-ats": 999.0})
    assert store.points("cycle:trace-ust-par") == {date(2026, 10, 5): 100.0}
    assert store.points("cycle:trace-ust-par-ats") == {date(2026, 10, 5): 60.0}


def test_needs_backfill_triggers_on_missing_venue_series():
    store = Store(":memory:")
    assert _needs_backfill(store) is True  # empty
    # core series populated (old 24-month backfill) but venue splits missing
    store.upsert_points("cycle:trace-ust-par",
                        [(date(2026, m, 28), 1.0) for m in range(1, 13)]
                        + [(date(2025, m, 28), 1.0) for m in range(1, 13)])
    assert _needs_backfill(store) is True
    store.upsert_points("cycle:trace-ust-bills-par-ats",
                        [(date(2026, 9, 30), 1.0)])
    assert _needs_backfill(store) is False


def _fake_bytes_factory(daily: bytes, monthly: bytes):
    async def fake_get_bytes(url: str, params=None) -> bytes:
        if "/daily/" in url:
            if "2026-10-05" in url or "2026-10-04" in url or "2026-10-03" in url:
                return daily
            raise RuntimeError("404: " + url)
        if "2026-09" in url or "2026-08" in url:
            return monthly
        raise RuntimeError("404: " + url)
    return fake_get_bytes


def test_fetch_daily_and_monthly_upsert_and_doc(monkeypatch):
    monkeypatch.setattr(trace_treasury, "REQUEST_GAP", 0)
    daily = _xlsx(_rows("October 05, 2026"))
    monthly = _xlsx(_rows("September 30, 2026", vwap=False))
    store = Store(":memory:")
    out = asyncio.run(fetch_trace_treasury(
        store, _fake_bytes_factory(daily, monthly),
        today=date(2026, 10, 6)))
    assert out == trace_treasury.SOURCE
    # daily points (non-month-end) stored with venue splits + buckets + VWAP
    assert store.points("cycle:trace-ust-par")[date(2026, 10, 5)] == pytest.approx(191.3)
    assert store.points("cycle:trace-ust-par-ats")[date(2026, 10, 5)] == pytest.approx(115.1)
    assert store.points("cycle:trace-ust-coupons-3y5y-par")[date(2026, 10, 5)] == pytest.approx(90.0)
    assert store.points("cycle:trace-ust-coupons-le2y-vwap")[date(2026, 10, 5)] == pytest.approx(99.5)
    # monthly backfill: month-end points carry monthly totals
    assert store.points("cycle:trace-ust-par")[date(2026, 9, 30)] == pytest.approx(191.3)
    assert store.points("cycle:trace-ust-par-ats")[date(2026, 9, 30)] == pytest.approx(115.1)
    doc = store.doc("trace_treasury")
    assert doc.payload["as_of"] == "2026-10-05"
    assert "trace-ust-coupons-3y5y-par" in doc.payload["series"]
    assert "trace-ust-coupons-le2y-vwap" in doc.payload["series"]
    assert "trace-ust-tips-le5y-par" in doc.payload["series"]


def test_fetch_daily_month_end_does_not_clobber_monthly_total(monkeypatch):
    monkeypatch.setattr(trace_treasury, "REQUEST_GAP", 0)
    # daily file dated at month-end: its values must NOT overwrite the
    # monthly file's monthly totals at the same date
    daily_me = _xlsx(_rows("September 30, 2026"))  # daily values, month-end date
    monthly = _xlsx(_rows("September 30, 2026", vwap=False, scale=100.0))

    async def fake_get_bytes(url: str, params=None) -> bytes:
        if "/daily/" in url:
            return daily_me
        if "2026-09" in url:
            return monthly
        raise RuntimeError("404: " + url)

    store = Store(":memory:")
    # store is NOT thin (production state): 24 months of monthly totals +
    # venue splits, incl. the 2026-09 monthly total at 2026-09-30
    store.upsert_points("cycle:trace-ust-par",
                        [(date(2026, m, 28), 20000.0 + m) for m in range(1, 9)]
                        + [(date(2025, m, 28), 19000.0 + m) for m in range(1, 13)])
    store.upsert_points("cycle:trace-ust-bills-par-ats",
                        [(date(2026, 9, 30), 1502.9)])
    store.upsert_points("cycle:trace-ust-par", [(date(2026, 9, 30), 28142.0)])
    asyncio.run(fetch_trace_treasury(store, fake_get_bytes,
                                     today=date(2026, 10, 1)))
    # daily probe found the 2026-09-30 daily file but did not overwrite the
    # monthly total; no backfill ran (store not thin)
    assert store.points("cycle:trace-ust-par")[date(2026, 9, 30)] == pytest.approx(28142.0)
    doc = store.doc("trace_treasury")
    assert doc.payload["as_of"] == "2026-09-30"
