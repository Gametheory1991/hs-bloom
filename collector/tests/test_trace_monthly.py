"""Tests for the FINRA TRACE monthly fetcher (venue splits, all-product
trade counts, idempotent history re-parse).

Builds minimal TRACE monthly-report-shaped XLSX files in-memory (no
network; the batch6 fixture files are not in the repo).
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from collector.fetchers import trace_monthly
from collector.fetchers.trace_monthly import fetch_trace_monthly, parse_workbook
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


def _rows() -> list[list[str]]:
    """TRACE monthly report shape: label | Trades ATS/ID/Cust/Total |
    Par $M ATS/ID/Cust/Total."""
    return [
        ["TRACE Report For September", "Trades", "", "", "",
         "Par Value (in millions)", "", "", ""],
        ["", "ATS", "Interdealer", "Customer", "Total",
         "ATS", "Interdealer", "Customer", "Total"],
        ["CORP", "465029", "612356", "1942121", "3019506",
         "92956.2", "153598.3", "1017563.6", "1264118.1"],
        ["CONV", "755", "3809", "33867", "38431",
         "62.7", "1561.7", "82797.4", "84421.8"],
        ["CHRC", "21", "11", "205", "237", "0.1", "0", "1", "1.2"],
        ["ELN", "12", "1825", "2500", "4337",
         "0.3", "288.7", "1940.6", "2229.6"],
        ["AGCY", "21180", "20822", "46300", "88302",
         "1689", "11846.6", "80196.7", "93732.3"],
        ["ABS", "436", "1055", "10522", "12013",
         "3.4", "2263.8", "31794.4", "34061.6"],
        ["ABSX", "67", "671", "14120", "14858",
         "447.6", "3283.5", "118744.4", "122475.6"],
        ["CMO", "2101", "5913", "23623", "31637",
         "191.5", "31898.4", "266924.9", "299014.9"],
        ["MBS", "9073", "31193", "106284", "146550",
         "1406.7", "102313.7", "438001.4", "541721.8"],
        ["TBA", "35885", "29194", "79767", "144846",
         "828096.7", "741796.9", "5122127.8", "6692021.5"],
    ]


def test_parse_venue_splits_and_all_trades():
    vals = parse_workbook(_xlsx(_rows()), 2026, 9)
    asof = vals.pop("_asof")
    assert asof == date(2026, 9, 30)
    # original series keep exact IDs/values
    assert vals["trace-corp-par"] == pytest.approx(1264118.1)
    assert vals["trace-corp-trades"] == pytest.approx(3019506)
    assert vals["trace-tba-par"] == pytest.approx(6692021.5)
    assert vals["trace-corp-cust-share"] == pytest.approx(1017563.6 / 1264118.1)
    # venue splits for every product
    assert vals["trace-corp-par-ats"] == pytest.approx(92956.2)
    assert vals["trace-corp-par-d2d"] == pytest.approx(153598.3)
    assert vals["trace-corp-par-cust"] == pytest.approx(1017563.6)
    assert vals["trace-corp-trades-ats"] == pytest.approx(465029)
    assert vals["trace-corp-trades-d2d"] == pytest.approx(612356)
    assert vals["trace-corp-trades-cust"] == pytest.approx(1942121)
    assert vals["trace-tba-par-d2d"] == pytest.approx(741796.9)
    assert vals["trace-mbs-trades-cust"] == pytest.approx(106284)
    # venue splits sum to totals
    assert (vals["trace-corp-par-ats"] + vals["trace-corp-par-d2d"]
            + vals["trace-corp-par-cust"]) == pytest.approx(vals["trace-corp-par"])
    # trade counts now stored for the 6 par-only products
    assert vals["trace-agcy-trades"] == pytest.approx(88302)
    assert vals["trace-abs-trades"] == pytest.approx(12013)
    assert vals["trace-absx-trades"] == pytest.approx(14858)
    assert vals["trace-cmo-trades"] == pytest.approx(31637)
    assert vals["trace-mbs-trades"] == pytest.approx(146550)
    assert vals["trace-tba-trades"] == pytest.approx(144846)
    assert vals["trace-agcy-trades-ats"] == pytest.approx(21180)
    # 80 series + derived
    assert len(vals) == 81


def test_parse_new_keys_best_effort():
    # a workbook with blank venue-split columns still parses the original
    # series (layout drift on new keys must not kill the feed)
    rows = _rows()
    blanked = [[r[0], "", "", "", r[4], "", "", "", r[8]] for r in rows[2:]]
    vals = parse_workbook(_xlsx(rows[:2] + blanked), 2026, 9)
    vals.pop("_asof")
    assert vals["trace-corp-par"] == pytest.approx(1264118.1)
    assert vals["trace-corp-trades"] == pytest.approx(3019506)
    assert "trace-corp-par-ats" not in vals


def test_parse_missing_required_row_raises():
    rows = [r for r in _rows() if not (r and r[0] == "CORP")]
    with pytest.raises(ValueError, match="CORP"):
        parse_workbook(_xlsx(rows), 2026, 9)


def test_fetch_reparses_history_for_missing_venue_splits(monkeypatch):
    """Production state: full core history backfilled pre-2026-10-06, venue
    splits missing -> one idempotent re-parse of all months fills them."""
    monkeypatch.setattr(trace_monthly, "REQUEST_GAP", 0)

    def scaled(factor: float) -> bytes:
        rows = _rows()
        out = []
        for r in rows:
            nr = [r[0]]
            for c in r[1:]:
                try:
                    nr.append(str(float(c) * factor))
                except ValueError:
                    nr.append(c)
            out.append(nr)
        return _xlsx(out)

    data_sep = _xlsx(_rows())
    data_aug = scaled(0.5)
    data_2017 = scaled(0.1)

    async def fake_get_bytes(url: str, params=None) -> bytes:
        if "2026-09" in url:
            return data_sep
        if "2026-08" in url:
            return data_aug
        if "2017-01" in url:
            return data_2017
        raise RuntimeError("404: " + url)

    store = Store(":memory:")
    # pre-2026-10-06 state: core series backfilled, venue splits absent
    store.upsert_points("cycle:trace-corp-par",
                        [(date(2026, 8, 31), 1000000.0)])
    out = asyncio.run(fetch_trace_monthly(store, fake_get_bytes,
                                          today=date(2026, 10, 3)))
    assert out == trace_monthly.SOURCE
    # latest month stored with splits (fail-fast probe path)
    assert store.points("cycle:trace-corp-par")[date(2026, 9, 30)] == \
        pytest.approx(1264118.1)
    assert store.points("cycle:trace-corp-par-ats")[date(2026, 9, 30)] == \
        pytest.approx(92956.2)
    # re-parse filled the venue splits for older months too (idempotent
    # upserts, one point per date — no duplicates)
    assert store.points("cycle:trace-corp-par-ats")[date(2026, 8, 31)] == \
        pytest.approx(92956.2 * 0.5)
    assert store.points("cycle:trace-corp-par-ats")[date(2017, 1, 31)] == \
        pytest.approx(92956.2 * 0.1)
    pts = store.points("cycle:trace-corp-par-ats")
    assert len(pts) == len(set(pts))
    # core series re-parsed with the same data (idempotent)
    assert store.points("cycle:trace-corp-par")[date(2026, 8, 31)] == \
        pytest.approx(1264118.1 * 0.5)
    doc = store.doc("trace_monthly")
    assert "trace-corp-par-ats" in doc.payload["series"]
    assert "trace-tba-trades" in doc.payload["series"]
