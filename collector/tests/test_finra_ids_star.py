"""Tests for the FINRA-ICE STAR structured-product activity fetcher.

Builds a minimal STAR-shaped XLSX in-memory (no network, no fixtures)
and verifies parse_star aggregates, then exercises fetch_finra_ids_star
against a fake get_bytes serving a hand-built monthly ZIP.
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from collector.fetchers import finra_ids_star
from collector.fetchers.finra_ids_star import (
    SERIES_IDS,
    STAR_RE,
    _iter_star_files,
    fetch_finra_ids_star,
    parse_star,
)
from collector.store import Store

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)


def _xlsx(rows: list[list[str]]) -> bytes:
    """Minimal single-sheet XLSX with inline strings.

    rows: list of rows; each row is a list of cell values (col A, B, C, ...).
    """
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;"))

    sheet_rows = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, val in enumerate(row):
            col = chr(65 + ci)
            cells.append(
                f'<c r="{col}{ri}" t="inlineStr"><is><t>{esc(val)}</t></is></c>')
        sheet_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8"?>'
             f'<worksheet xmlns="{NS}"><sheetData>{"".join(sheet_rows)}'
             f'</sheetData></worksheet>')
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
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{NS}"><sheets>'
        f'<sheet name="TradingActivity" sheetId="1" r:id="rId1" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        f'</sheets></workbook>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _star_rows() -> list[list[str]]:
    """STAR-shaped rows mirroring the real 2026-09-30 file (subset)."""
    pad12 = [""] * 12
    pad6 = [""] * 6
    return [
        ["", "FINRA-ICE DATA SERVICES: Structured Trading Activity Reports"],
        ["", "DATA AS OF:", "46295.0"],
        ["", "ASSET CLASS", "UMBS", "", "", "FNMA"],
        ["", "AGENCY PASS-THRU (TBA, STIP, $ ROLLS)"] + pad12,
        # TBA 15Y: UMBS 447 trades / $6,444,980.5k ; GNMA zeros here
        ["", "SINGLE FAMILY 15Y", "447", "27", "6444980.5",
         "0", "0", "0.0", "0", "0", "0.0", "0", "0", "0.0"],
        # TBA 30Y: UMBS 6856 / $270,170,237.1k ; GNMA 1963 trades / $50,000k
        ["", "SINGLE FAMILY 30Y", "6856", "38", "270170237.1",
         "0", "0", "0.0", "0", "0", "0.0", "1963", "31", "50000.0"],
        ["", "AGENCY PASS-THRU (SPECIFIED)"] + pad12,
        ["", "SINGLE FAMILY 30Y", "4046", "1561", "8684501.4",
         "13", "13", "5276.3", "179", "86", "15533.3", "1083", "437", "900000.0"],
        ["", "AGENCY CMO"] + pad12,
        ["", "P&I", "0", "0", "0.0", "216", "96", "191542.5",
         "187", "91", "247738.1", "361", "171", "100000.0"],
        ["", "ASSET CLASS", "INVESTMENT GRADE"] + pad6,
        ["", "NON-AGENCY CMO"] + pad6,
        ["", "P&I", "160", "114", "677173.1", "155", "109", "45605.4"],
        ["", "ABS"] + ["597", "387", "1319021.0", "72", "52", "261790.3"],
        ["", "CBO/CDO/CLO"] + ["253", "177", "1704263.8", "60", "35", "299352.8"],
    ]


def test_parse_star_aggregates():
    agg = parse_star(_xlsx(_star_rows()))
    # TBA UMBS par = (6,444,980.5 + 270,170,237.1) * 1000
    assert agg["tba-umbs-par"] == pytest.approx(276615217600.0)
    # TBA GNMA par = 50,000.0 * 1000 (30Y) + 0 (15Y)
    assert agg["tba-gnma-par"] == pytest.approx(50000000.0)
    # TBA total trades = 447 + 6856 + 1963
    assert agg["tba-trades"] == pytest.approx(9266.0)
    # Specified par incl. GNMA 900,000k
    exp_spec = (8684501.4 + 5276.3 + 15533.3 + 900000.0) * 1000
    assert agg["spec-par"] == pytest.approx(exp_spec)
    # Agency CMO par
    exp_agcmo = (191542.5 + 247738.1 + 100000.0) * 1000
    assert agg["agcmo-par"] == pytest.approx(exp_agcmo)
    # Non-agency CMO IG / Non-IG
    assert agg["nagcmo-ig-par"] == pytest.approx(677173.1 * 1000)
    assert agg["nagcmo-nonig-par"] == pytest.approx(45605.4 * 1000)
    # ABS + CLO
    assert agg["abs-par"] == pytest.approx((1319021.0 + 261790.3) * 1000)
    assert agg["clo-ig-par"] == pytest.approx(1704263.8 * 1000)


def test_parse_star_suppressed_star_is_zero():
    rows = _star_rows()
    # inject a suppressed row: IO/PO with '*' values in agency CMO block
    rows.insert(9, ["", "IO/PO", "*", "*", "*", "0", "0", "0.0",
                    "0", "0", "0.0", "0", "0", "0.0"])
    agg = parse_star(_xlsx(rows))
    # '*' contributes 0 — agcmo-par unchanged from the P&I-only value
    exp_agcmo = (191542.5 + 247738.1 + 100000.0) * 1000
    assert agg["agcmo-par"] == pytest.approx(exp_agcmo)


def _zip_bytes(dates: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for ds in dates:
            z.writestr(f"FINRA_IDS_STAR-{ds}.xlsx", _xlsx(_star_rows()))
    return buf.getvalue()


def test_iter_star_files_filters_names():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("FINRA_IDS_STAR-20260930.xlsx", b"x")
        z.writestr("FINRA_IDS_PXTABLES-20260930.xlsx", b"x")
        z.writestr("notes.txt", b"x")
    days = [d for d, _ in _iter_star_files(buf.getvalue())]
    assert days == [date(2026, 9, 30)]


def test_fetch_ids_star_writes_series_and_doc():
    store = Store(":memory:")

    async def fake_get_bytes(url: str) -> bytes:
        if "202609" in url:
            return _zip_bytes(["20260930", "20260929"])
        raise ValueError(f"404: {url}")

    src = asyncio.run(finra_ids_star.fetch_finra_ids_star(
        store, fake_get_bytes, today=date(2026, 10, 5),
        backfill_months=0))
    assert src == finra_ids_star.SOURCE
    pts = store.points("cycle:star-tba-par")
    assert date(2026, 9, 30) in pts
    assert pts[date(2026, 9, 30)] == pytest.approx(276665217600.0)
    assert len(store.points("cycle:star-clo-ig-par")) == 2
    doc = store.doc("finra_ids_star")
    assert doc is not None
    assert doc.payload["as_of"] == "2026-09-30"
    assert set(doc.payload["series"]) == set(SERIES_IDS)
    assert STAR_RE.fullmatch("FINRA_IDS_STAR-20260930.xlsx")


def test_fetch_ids_star_backfills_on_empty_store():
    store = Store(":memory:")
    seen: list[str] = []

    async def fake_get_bytes(url: str) -> bytes:
        seen.append(url)
        if "202609" in url:
            return _zip_bytes(["20260930"])
        if "202608" in url:
            return _zip_bytes(["20260831"])
        raise ValueError(f"404: {url}")

    asyncio.run(finra_ids_star.fetch_finra_ids_star(
        store, fake_get_bytes, today=date(2026, 10, 5),
        backfill_months=2))
    # backfill asked for Sep + Aug 2026; incremental also hits Oct + Sep
    assert any("202608" in u for u in seen)
    pts = store.points("cycle:star-tba-par")
    assert date(2026, 9, 30) in pts
    assert date(2026, 8, 31) in pts
