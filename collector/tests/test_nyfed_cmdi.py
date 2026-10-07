"""NY Fed CMDI fetcher tests: workbook parsing, series storage."""
import io
import zipfile
from datetime import date

import pytest

from collector.fetchers.nyfed_cmdi import _parse, fetch_nyfed_cmdi
from collector.store import Store


def _mini_xlsx() -> bytes:
    """Minimal single-sheet workbook with the CMDI header layout."""
    def cell(ref, val, inline=False):
        if inline:
            return f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>'
        return f'<c r="{ref}"><v>{val}</v></c>'

    hdr = ["eow_friday", "Market CMDI", "IG CMDI", "HY CMDI"]
    rows_xml = ["<row r=\"1\">" + "".join(
        cell(f"{chr(65 + i)}1", h, inline=True) for i, h in enumerate(hdr)) + "</row>"]
    # 2005-01-07 = serial 38359; 2026-10-02 = serial 46318
    for i, (serial, m, ig, hy) in enumerate([
        (38359, 0.35, 0.25, 0.35),
        (38366, 0.27, 0.26, 0.33),
        (46297, 0.20, 0.25, 0.11),
    ], start=2):
        rows_xml.append("<row r=\"%d\">" % i + "".join([
            cell(f"A{i}", serial), cell(f"B{i}", m),
            cell(f"C{i}", ig), cell(f"D{i}", hy)]) + "</row>")
    sheet = ('<?xml version="1.0"?><worksheet '
             'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>' + "".join(rows_xml) + '</sheetData></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_parse_cmdi_workbook():
    series = _parse(_mini_xlsx())
    assert set(series) == {"cmdi-market", "cmdi-ig", "cmdi-hy"}
    assert series["cmdi-market"][0] == (date(2005, 1, 7), 0.35)
    assert series["cmdi-ig"][-1] == (date(2026, 10, 2), 0.25)
    assert series["cmdi-hy"][-1] == (date(2026, 10, 2), 0.11)
    assert len(series["cmdi-market"]) == 3


def test_parse_rejects_bad_header():
    buf = io.BytesIO()
    sheet = ('<?xml version="1.0"?><worksheet '
             'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>nope</t></is></c>'
             '</row></sheetData></worksheet>')
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    with pytest.raises(ValueError, match="header"):
        _parse(buf.getvalue())


async def _fake_bytes(url, params=None, headers=None):
    assert "newyorkfed.org" in url
    return _mini_xlsx()


@pytest.mark.asyncio
async def test_fetch_stores_three_series(tmp_path):
    store = Store(tmp_path / "t.db")
    assert await fetch_nyfed_cmdi(store, _fake_bytes) == "nyfed-cmdi"
    pts = store.points("cycle:cmdi-market")
    assert pts[date(2005, 1, 7)] == 0.35
    assert pts[date(2026, 10, 2)] == 0.20
    assert store.points("cycle:cmdi-ig")[date(2005, 1, 7)] == 0.25
    assert store.points("cycle:cmdi-hy")[date(2026, 10, 2)] == 0.11
