"""Tests for the FINRA-ICE PXTABLES pricing-tables fetcher.

Builds minimal multi-sheet PXTABLES-shaped XLSX files in-memory (no
network, no fixtures) and verifies parse_pxtables series naming/values,
weekly as-of dating, ZIP filename filtering, and the shared fetch flow
in fetch_finra_ids_star (STAR + PXTABLES parsed from one monthly ZIP).
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from collector.fetchers import finra_ids_px, finra_ids_star
from collector.fetchers.finra_ids_px import (
    PXTABLES_RE,
    _slug,
    iter_pxtables_files,
    needs_backfill,
    parse_pxtables,
    pxtables_asof,
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


def _xlsx_multi(sheets: dict[int, list[list[str]]]) -> bytes:
    """Minimal multi-sheet XLSX with inline strings.

    sheets: {1-indexed sheet number: rows}; rows are lists of cell
    values (col A, B, C, ...).
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{sn}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for sn in sheets) +
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{NS}"><sheets>'
        + "".join(
            f'<sheet name="Sheet{sn}" sheetId="{sn}" r:id="rId1" '
            f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            for sn in sheets) +
        f'</sheets></workbook>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        for sn, rows in sheets.items():
            z.writestr(f"xl/worksheets/sheet{sn}.xml", _sheet_xml(rows))
    return buf.getvalue()


def _tba_sheet() -> list[list[str]]:
    """Sheet-1-shaped: 15Y title + September settlement, UMBS/FHLMC blocks."""
    return [
        ["", "PRICING TABLE: AGENCY PASS-THRU (TBA, STIP, $ ROLLS) - SINGLE FAMILY 15Y"],
        ["", "September Settlement"],
        ["", "Asset Sub-Class / Metric", "COUPON"],
        ["", "UMBS", "<= 3.5", "5", "> 6"],
        ["", "AVERAGE PRICE", "99.1", "101.2", "*"],
        ["", "WEIGHTED AVG. PRICE", "99.0", "101.0", "0.0"],
        ["", "AVG. PRICE BOTTOM 5 TRADES", "98.5", "100.8", "0.0"],
        ["", "2ND QUARTILE PRICE", "98.9", "101.0", "0.0"],
        ["", "3RD QUARTILE PRICE", "99.2", "101.3", "0.0"],
        ["", "4TH QUARTILE PRICE", "99.5", "101.6", "0.0"],
        ["", "AVG. PRICE TOP 5 TRADES", "99.8", "101.9", "0.0"],
        ["", "STANDARD DEVIATION", "0.4", "0.5", "0.0"],
        ["", "VOLUME OF TRADES (000'S)", "1234.5", "0.0", "*"],
        ["", "NUMBER OF TRADES", "10", "0", "0"],
        ["", "FHLMC", "<= 3.5", "5", "> 6"],
        ["", "AVERAGE PRICE", "0.0", "98.8", "0.0"],
        ["", "VOLUME OF TRADES (000'S)", "0.0", "555.0", "0.0"],
        ["", "NUMBER OF TRADES", "0", "7", "0"],
        ["", "* Indicates trade count is less than 5"],
    ]


def _cboclo_sheet() -> list[list[str]]:
    """Sheet-7-shaped: two-level headers (product + vintage)."""
    return [
        ["", "PRICING TABLE: CBO/CDO/CLO"],
        ["", "Metric", "CBO/CDO/CLO", "", "AAA", ""],
        ["", "", "", "", "PRE-2023", "2023-2026"],
        ["", "AVERAGE PRICE", "98.2", "", "99.9", "100.0"],
        ["", "WEIGHTED AVG. PRICE", "95.9", "", "100.0", "100.0"],
        ["", "VOLUME OF TRADES (000'S)", "2000.0", "", "500.0", "700.0"],
        ["", "CUSTOMER BUY", "1000.0", "", "250.0", "350.0"],
        ["", "NUMBER OF TRADES", "300", "", "50", "90"],
        ["", "DEALER TO DEALER", "17", "", "3", "4"],
        ["", "<= $1MM", "49", "", "10", "12"],
    ]


def _nag_sheet() -> list[list[str]]:
    """Sheet-4-shaped: grade blocks; NON-IG header omits dim labels."""
    return [
        ["", "PRICING TABLE: NON-AGENCY CMO | ABS"],
        ["", "Investment Grade / Metric", "NONAGENCY CMO (P&I)", "ABS"],
        ["", "AVERAGE PRICE", "95.2", "98.7"],
        ["", "VOLUME OF TRADES (000'S)", "677.1", "1319.0"],
        ["", "Non-Investment Grade / Metric \u2020"],
        ["", "AVERAGE PRICE", "67.7", "0.0"],
        ["", "VOLUME OF TRADES (000'S)", "10.0", "0.0"],
    ]


def test_slug():
    assert _slug("<= 3.5") == "le3_5"
    assert _slug("> 6") == "gt6"
    assert _slug("POST-2016") == "post2016"
    assert _slug("PRE-2023") == "pre2023"
    assert _slug("2023-2026") == "2023-2026"
    assert _slug("4.5") == "4_5"
    assert _slug("AGENCY CMBS (P&I)") == "agency-cmbs-pi"
    assert _slug("NONAGENCY CMO (IO/PO)") == "nonagency-cmo-io-po"
    assert _slug("NON-AAA IG") == "non-aaa-ig"
    assert _slug("CBO/CDO/CLO") == "cbo-cdo-clo"


def test_parse_tba_block_coupons_and_settlement():
    agg = parse_pxtables(_xlsx_multi({1: _tba_sheet()}))
    tba = agg["tba"]
    assert tba["15y-sep-umbs-le3_5-avgpx"] == pytest.approx(99.1)
    assert tba["15y-sep-umbs-5-avgpx"] == pytest.approx(101.2)
    assert tba["15y-sep-umbs-le3_5-wavgpx"] == pytest.approx(99.0)
    assert tba["15y-sep-umbs-le3_5-q2"] == pytest.approx(98.9)
    assert tba["15y-sep-umbs-le3_5-top5"] == pytest.approx(99.8)
    assert tba["15y-sep-umbs-le3_5-stdev"] == pytest.approx(0.4)
    # VOLUME OF TRADES (000'S) stored x1000
    assert tba["15y-sep-umbs-le3_5-vol"] == pytest.approx(1234.5 * 1000)
    assert tba["15y-sep-umbs-le3_5-ntrades"] == pytest.approx(10.0)
    # '*' / '0.0' / '0' cells skipped: no gt6 series, no zero series
    assert not any("gt6" in k for k in tba)
    assert "15y-sep-umbs-5-vol" not in tba
    # second block
    assert tba["15y-sep-fhlmc-5-avgpx"] == pytest.approx(98.8)
    assert tba["15y-sep-fhlmc-5-vol"] == pytest.approx(555.0 * 1000)
    assert tba["15y-sep-fhlmc-5-ntrades"] == pytest.approx(7.0)
    # footer row ignored
    assert not any("indicates" in k for k in tba)


def test_parse_tba_second_settlement_month():
    rows = _tba_sheet()
    rows[1] = ["", "October Settlement"]
    agg = parse_pxtables(_xlsx_multi({1: rows}))
    assert agg["tba"]["15y-oct-umbs-le3_5-avgpx"] == pytest.approx(99.1)


def test_parse_two_level_headers_cboclo():
    agg = parse_pxtables(_xlsx_multi({7: _cboclo_sheet()}))
    clo = agg["cboclo"]
    assert clo["cbo-cdo-clo-avgpx"] == pytest.approx(98.2)
    assert clo["aaa-pre2023-avgpx"] == pytest.approx(99.9)
    assert clo["aaa-2023-2026-avgpx"] == pytest.approx(100.0)
    assert clo["aaa-2023-2026-wavgpx"] == pytest.approx(100.0)
    assert clo["cbo-cdo-clo-vol"] == pytest.approx(2000.0 * 1000)
    assert clo["aaa-2023-2026-ntrades"] == pytest.approx(90.0)
    # breakdown rows: vol-section (par, $000s x1000) and ntrades-section
    # (counts) both parsed
    assert clo["cbo-cdo-clo-vol-custbuy"] == pytest.approx(1000.0 * 1000)
    assert clo["aaa-2023-2026-vol-custbuy"] == pytest.approx(350.0 * 1000)
    assert clo["cbo-cdo-clo-ntrades-d2d"] == pytest.approx(17.0)
    assert clo["aaa-pre2023-ntrades-d2d"] == pytest.approx(3.0)
    assert clo["cbo-cdo-clo-ntrades-tick-le1mm"] == pytest.approx(49.0)
    assert clo["aaa-2023-2026-ntrades-tick-le1mm"] == pytest.approx(12.0)


def test_parse_breakdown_sections_vol_and_trades():
    # sheet-4-shaped with full breakdown rows in both the vol section
    # (par $000s) and the ntrades section (counts)
    rows = [
        ["", "PRICING TABLE: NON-AGENCY CMO | ABS"],
        ["", "Investment Grade / Metric", "NONAGENCY CMO (P&I)", "ABS"],
        ["", "VOLUME OF TRADES (000'S)", "677.1", "1319.0"],
        ["", "CUSTOMER BUY", "432.5", "800.0"],
        ["", "CUSTOMER SELL", "200.7", "400.0"],
        ["", "DEALER TO DEALER", "43.8", "119.0"],
        ["", "<= $1MM", "21.5", "100.0"],
        ["", "<= $10MM", "273.2", "500.0"],
        ["", "<= $100MM", "382.3", "719.0"],
        ["", "> $100MM", "0.0", "0.0"],
        ["", "NUMBER OF TRADES", "160", "200"],
        ["", "CUSTOMER BUY", "83", "120"],
        ["", "CUSTOMER SELL", "69", "70"],
        ["", "DEALER TO DEALER", "8", "10"],
        ["", "<= $1MM", "79", "90"],
        ["", "<= $10MM", "61", "80"],
        ["", "<= $100MM", "20", "30"],
        ["", "> $100MM", "0", "0"],
    ]
    agg = parse_pxtables(_xlsx_multi({4: rows}))
    nag = agg["nag"]
    # vol-section: par in $000s stored x1000
    assert nag["ig-nonagency-cmo-pi-vol-custbuy"] == pytest.approx(432.5 * 1000)
    assert nag["ig-abs-vol-custsell"] == pytest.approx(400.0 * 1000)
    assert nag["ig-nonagency-cmo-pi-vol-d2d"] == pytest.approx(43.8 * 1000)
    assert nag["ig-nonagency-cmo-pi-vol-tick-le1mm"] == pytest.approx(21.5 * 1000)
    assert nag["ig-nonagency-cmo-pi-vol-tick-le10mm"] == pytest.approx(273.2 * 1000)
    assert nag["ig-nonagency-cmo-pi-vol-tick-le100mm"] == pytest.approx(382.3 * 1000)
    # zero ticket-bucket cells skipped
    assert "ig-nonagency-cmo-pi-vol-tick-gt100mm" not in nag
    # ntrades-section: counts stored as-is
    assert nag["ig-nonagency-cmo-pi-ntrades-custbuy"] == pytest.approx(83.0)
    assert nag["ig-abs-ntrades-custsell"] == pytest.approx(70.0)
    assert nag["ig-nonagency-cmo-pi-ntrades-d2d"] == pytest.approx(8.0)
    assert nag["ig-nonagency-cmo-pi-ntrades-tick-le1mm"] == pytest.approx(79.0)
    assert nag["ig-nonagency-cmo-pi-ntrades-tick-le100mm"] == pytest.approx(20.0)


def test_parse_breakdown_stray_rows_ignored():
    # a breakdown label appearing before any metric row (layout drift)
    # must not attach to the previous block's metric
    rows = [
        ["", "PRICING TABLE: NON-AGENCY CMO | ABS"],
        ["", "Investment Grade / Metric", "NONAGENCY CMO (P&I)", "ABS"],
        ["", "CUSTOMER BUY", "432.5", "800.0"],
        ["", "VOLUME OF TRADES (000'S)", "677.1", "1319.0"],
        ["", "CUSTOMER BUY", "400.0", "700.0"],
    ]
    agg = parse_pxtables(_xlsx_multi({4: rows}))
    nag = agg["nag"]
    # stray row (before vol) ignored; the one after vol parsed
    assert "ig-nonagency-cmo-pi-vol-custbuy" in nag
    assert nag["ig-nonagency-cmo-pi-vol-custbuy"] == pytest.approx(400.0 * 1000)


def test_parse_tba_sheets_have_no_breakdowns():
    # sheets 1/2 (TBA/specified) carry no breakdown rows — FINRA notes
    # transaction volume is unavailable there; nothing may be invented
    agg = parse_pxtables(_xlsx_multi({1: _tba_sheet()}))
    tba = agg["tba"]
    assert tba  # the 10 metrics still parse
    assert not any("-custbuy" in k or "-custsell" in k or "-d2d" in k
                   or "-tick-" in k for k in tba)


def test_parse_grade_blocks_reuse_dims():
    agg = parse_pxtables(_xlsx_multi({4: _nag_sheet()}))
    nag = agg["nag"]
    assert nag["ig-nonagency-cmo-pi-avgpx"] == pytest.approx(95.2)
    assert nag["ig-abs-avgpx"] == pytest.approx(98.7)
    assert nag["ig-nonagency-cmo-pi-vol"] == pytest.approx(677.1 * 1000)
    # NON-IG header omits dim labels -> reuses IG dims
    assert nag["nonig-nonagency-cmo-pi-avgpx"] == pytest.approx(67.7)
    assert nag["nonig-nonagency-cmo-pi-vol"] == pytest.approx(10.0 * 1000)
    # zero cells skipped
    assert "nonig-abs-avgpx" not in nag


def test_parse_missing_sheets_skipped():
    # only sheet 7 present: sheets 1-6 absent must not raise
    agg = parse_pxtables(_xlsx_multi({7: _cboclo_sheet()}))
    assert set(agg) == {"cboclo"}


def test_pxtables_asof_weekly_uses_week_end():
    xlsx = _xlsx_multi({
        6: [
            ["", "DATA AS OF:", "2026-09-21 to 2026-09-25"],
            ["", "PRICING TABLE: AGENCY CMBS - BY DEAL VINTAGE"],
            ["", "Metric", "AGENCY CMBS (P&I)"],
            ["", "", "PRE-2021"],
            ["", "AVERAGE PRICE", "92.3"],
        ],
    })
    dates = pxtables_asof(xlsx, date(2026, 9, 30))
    assert dates["wcmbs"] == date(2026, 9, 25)
    assert dates["tba"] == date(2026, 9, 30)


def test_iter_pxtables_files_filters_names():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("FINRA_IDS_PXTABLES-20260930.xlsx", b"x")
        z.writestr("FINRA_IDS_STAR-20260930.xlsx", b"x")
        z.writestr("notes.txt", b"x")
    days = [d for d, _ in iter_pxtables_files(buf.getvalue())]
    assert days == [date(2026, 9, 30)]
    assert PXTABLES_RE.fullmatch("FINRA_IDS_PXTABLES-20260930.xlsx")


def test_needs_backfill():
    store = Store(":memory:")
    assert needs_backfill(store) is True  # no doc yet
    store.put_doc("finra_ids_px", {"as_of": "2026-09-30"}, source="x")
    assert needs_backfill(store) is True  # doc but no points
    store.upsert_points(finra_ids_px.SENTINEL,
                        [(date(2026, 9, 30), 98.2)])
    assert needs_backfill(store) is True  # <20 points
    store.upsert_points(finra_ids_px.SENTINEL,
                        [(date(2026, 9, d), 98.0) for d in range(1, 21)])
    assert needs_backfill(store) is False


def _star_xlsx() -> bytes:
    rows = [
        ["", "AGENCY PASS-THRU (TBA, STIP, $ ROLLS)"] + [""] * 12,
        ["", "SINGLE FAMILY 30Y", "10", "5", "1000.0",
         "0", "0", "0.0", "0", "0", "0.0", "0", "0", "0.0"],
    ]
    return _xlsx_multi({1: rows})


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("FINRA_IDS_STAR-20260930.xlsx", _star_xlsx())
        z.writestr("FINRA_IDS_PXTABLES-20260930.xlsx",
                   _xlsx_multi({1: _tba_sheet(), 7: _cboclo_sheet()}))
    return buf.getvalue()


def test_fetch_writes_px_series_from_shared_zip():
    store = Store(":memory:")

    async def fake_get_bytes(url: str) -> bytes:
        if "202609" in url:
            return _zip_bytes()
        raise ValueError(f"404: {url}")

    asyncio.run(finra_ids_star.fetch_finra_ids_star(
        store, fake_get_bytes, today=date(2026, 10, 5),
        backfill_months=0))
    # STAR still parsed from the same ZIP
    assert store.points("cycle:star-tba-par")[date(2026, 9, 30)] == \
        pytest.approx(1000000.0)
    # PXTABLES series stored
    pts = store.points("cycle:starpx-tba-15y-sep-umbs-le3_5-avgpx")
    assert pts[date(2026, 9, 30)] == pytest.approx(99.1)
    pts = store.points("cycle:starpx-cboclo-aaa-2023-2026-avgpx")
    assert pts[date(2026, 9, 30)] == pytest.approx(100.0)
    doc = store.doc("finra_ids_px")
    assert doc is not None
    assert doc.payload["as_of"] == "2026-09-30"


def test_fetch_px_backfill_adds_third_month():
    # STAR already populated, PXTABLES new: daily job fetches one extra
    # older month so PX gets ~3 months of history
    store = Store(":memory:")
    store.upsert_points("cycle:star-tba-par",
                        [(date(2026, 9, d), 1e9) for d in range(1, 25)])
    seen: list[str] = []

    async def fake_get_bytes(url: str) -> bytes:
        seen.append(url)
        if "202609" in url:
            return _zip_bytes()
        raise ValueError(f"404: {url}")

    asyncio.run(finra_ids_star.fetch_finra_ids_star(
        store, fake_get_bytes, today=date(2026, 10, 5),
        backfill_months=0))
    assert any("202610" in u for u in seen)  # current month
    assert any("202609" in u for u in seen)  # previous month
    assert any("202608" in u for u in seen)  # extra month for PX backfill
