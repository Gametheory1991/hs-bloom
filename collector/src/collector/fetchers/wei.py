"""Weekly Economic Index (WEI) — Dallas Fed successor to the NY Fed WEI.

The NY Fed discontinued its WEI; the Dallas Fed (Lewis/Mertens/Stock)
publishes the continuation as an .xlsx at
https://www.dallasfed.org/-/media/documents/research/wei/weekly-economic-index.xlsx

The workbook layout (verified 2026-10-03): sheet3 holds the full history —
row 5 is the header ("Date", then one column per vintage), rows 6+ are
weekly dates (MM/DD/YYYY) with WEI values (percent, 4-quarter GDP-growth
scale). We take column B (first vintage column) as the series. Junk rows
(author names, contact info) are skipped: a row is kept only when column A
parses as a date and column B as a float.

Parsed with stdlib zipfile+xml (no openpyxl dependency). As of 2026-10-03
the published file lagged ~5 weeks (latest week 2026-08-29) — the series
is weekly but the file refresh cadence is the Dallas Fed's, not ours.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET

from collector.http import GetBytes

WEI_URL = "https://www.dallasfed.org/-/media/documents/research/wei/weekly-economic-index.xlsx"

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _cell_text(cell: ET.Element, strings: list[str]) -> str:
    v = cell.find("m:v", _NS)
    val = v.text if v is not None and v.text else ""
    if cell.get("t") == "s" and val:
        try:
            return strings[int(val)]
        except (ValueError, IndexError):
            return ""
    return val


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_wei_xlsx(raw: bytes) -> list[tuple[date, float]]:
    """Parse the Dallas Fed WEI workbook -> [(week, wei)] oldest first."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"wei: not a valid xlsx: {exc}") from exc
    try:
        names = zf.namelist()
        sheet = next(n for n in names if re.fullmatch(r"xl/worksheets/sheet3\.xml", n))
        ss_raw = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
    except (StopIteration, KeyError) as exc:
        raise ValueError(f"wei: workbook missing sheet3/sharedStrings: {exc}") from exc
    strings = re.findall(r"<t[^>]*>([^<]*)</t>", ss_raw)
    root = ET.fromstring(zf.read(sheet))
    out: list[tuple[date, float]] = []
    for row in root.findall(".//m:row", _NS):
        cells = row.findall("m:c", _NS)
        if len(cells) < 2:
            continue
        d = _parse_date(_cell_text(cells[0], strings))
        if d is None:
            continue
        try:
            out.append((d, float(_cell_text(cells[1], strings).replace(",", ""))))
        except ValueError:
            continue
    if not out:
        raise ValueError("wei: no usable date/value rows in sheet3")
    out.sort(key=lambda p: p[0])
    return out


async def fetch_wei(get_bytes: GetBytes) -> list[tuple[date, float]]:
    return parse_wei_xlsx(await get_bytes(WEI_URL))
