"""Tests for collector.fetchers.wei (Dallas Fed Weekly Economic Index .xlsx).

HTTP is never touched: parse_wei_xlsx takes raw xlsx bytes. The test
builds a minimal workbook in-memory with the real layout (sheet3 holds the
history: header row, date/value rows, plus junk author/contact rows that
must be skipped).
"""
from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

from collector.fetchers.wei import fetch_wei, parse_wei_xlsx

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(rows: list[list]) -> bytes:
    """rows: list of [date_str_or_junk, value_or_junk]; strings go through
    sharedStrings like the real Dallas Fed file."""
    strings: list[str] = []
    sidx = {}

    def s(v: str) -> int:
        if v not in sidx:
            sidx[v] = len(strings)
            strings.append(v)
        return sidx[v]

    body = []
    for i, (a, b) in enumerate(rows, start=1):
        cells = []
        for j, v in enumerate((a, b)):
            ref = f"{chr(65 + j)}{i}"
            if isinstance(v, str):
                cells.append(f'<c r="{ref}" t="s"><v>{s(v)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        body.append(f"<row r=\"{i}\">{''.join(cells)}</row>")
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{NS}">'
             f"<sheetData>{''.join(body)}</sheetData></worksheet>")
    sst = "".join(f"<si><t>{x}</t></si>" for x in strings)
    shared = (f'<?xml version="1.0"?><sst xmlns="{NS}">'
              f"{sst}</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/worksheets/sheet3.xml", sheet)
        zf.writestr("xl/sharedStrings.xml", shared)
    return buf.getvalue()


SAMPLE_ROWS = [
    [" Lewis, Daniel J., Mertens, Karel", ""],   # junk author row
    ["Date", ""],
    ["01/05/2008", 1.16],
    ["01/12/2008", 1.14],
    ["Tyler Atkinson", 1.92],                     # junk contact row
    ["01/19/2008", 1.64],
    ["08/29/2026", 2.93],
]


class ParseWeiTest(unittest.TestCase):
    def test_parses_history_oldest_first(self):
        pts = parse_wei_xlsx(_xlsx(SAMPLE_ROWS))
        self.assertEqual(pts, [
            (date(2008, 1, 5), 1.16),
            (date(2008, 1, 12), 1.14),
            (date(2008, 1, 19), 1.64),
            (date(2026, 8, 29), 2.93),
        ])

    def test_junk_rows_skipped(self):
        pts = parse_wei_xlsx(_xlsx(SAMPLE_ROWS))
        names = [d for d, _ in pts]
        self.assertNotIn("Tyler Atkinson", [str(d) for d in names])
        self.assertEqual(len(pts), 4)

    def test_not_xlsx_raises(self):
        with self.assertRaises(ValueError):
            parse_wei_xlsx(b"definitely not a zip file")

    def test_no_usable_rows_raises(self):
        with self.assertRaises(ValueError):
            parse_wei_xlsx(_xlsx([["hello", "world"]]))

    def test_fetch_wei_uses_get_bytes(self):
        async def fake(url: str) -> bytes:
            assert "dallasfed.org" in url and url.endswith(".xlsx")
            return _xlsx(SAMPLE_ROWS)
        import asyncio
        pts = asyncio.run(fetch_wei(fake))
        self.assertEqual(pts[-1], (date(2026, 8, 29), 2.93))


class LiveShapeTest(unittest.TestCase):
    """Pin the real file's shape assumptions (runs offline on the sample
    captured 2026-10-03; guards against Dallas Fed layout drift)."""

    def test_real_sample_parses(self):
        try:
            raw = open("/tmp/wei.xlsx", "rb").read()
        except OSError:
            self.skipTest("no captured /tmp/wei.xlsx in this environment")
        pts = parse_wei_xlsx(raw)
        self.assertGreater(len(pts), 500)          # full history since 2008
        self.assertEqual(pts[0][0], date(2008, 1, 5))
        self.assertGreaterEqual(pts[-1][0], date(2026, 8, 1))


if __name__ == "__main__":
    unittest.main()
