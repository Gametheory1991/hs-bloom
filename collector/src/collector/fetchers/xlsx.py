"""Minimal .xlsx reader on the stdlib (zipfile + ElementTree).

openpyxl is not a project dependency, and these FINRA/ICE workbooks are
simple single-sheet grids. Supports the cell types they use: shared
strings, inline strings, and numerics. Missing sharedStrings.xml (inline-
only workbooks like the FINRA Treasury aggregates) is handled.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _col_to_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _cell_value(cell: ET.Element, strings: list[str]) -> str:
    t = cell.get("t")
    if t == "s":
        v = cell.findtext("m:v", "", NS)
        try:
            return strings[int(v)] if v != "" else ""
        except (ValueError, IndexError):
            return ""
    if t == "inlineStr":
        return "".join(x.text or "" for x in cell.findall(".//m:t", NS))
    if t == "b":
        return cell.findtext("m:v", "", NS)
    return cell.findtext("m:v", "", NS)


def read_sheet(data: bytes, sheet: int = 1) -> dict[int, list[str]]:
    """Return {row_number: [cell values as str, 0-indexed by column]}.

    Rows are 1-based like Excel; the list is dense from column A to the
    last non-empty cell in that row. Raises ValueError on a corrupt file.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip/xlsx file: {exc}") from exc
    try:
        strings: list[str] = []
        try:
            ss_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            strings = [
                "".join(t.text or "" for t in si.findall(".//m:t", NS))
                for si in ss_root.findall("m:si", NS)
            ]
        except KeyError:
            pass  # inline-only workbook
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise ValueError(f"xlsx missing/unparseable sheet{sheet}: {exc}") from exc
    rows: dict[int, list[str]] = {}
    for row in root.findall(".//m:row", NS):
        try:
            r = int(row.get("r"))
        except (TypeError, ValueError):
            continue
        cells: dict[int, str] = {}
        for c in row.findall("m:c", NS):
            m = _CELL_RE.match(c.get("r", ""))
            if not m:
                continue
            cells[_col_to_index(m.group(1))] = _cell_value(c, strings)
        if cells:
            last = max(cells)
            rows[r] = [cells.get(i, "") for i in range(last + 1)]
    if not rows:
        raise ValueError("xlsx sheet contained no rows")
    return rows


def find_row(rows: dict[int, list[str]], label: str,
             col: int = 0) -> list[str] | None:
    """First row whose column `col` exactly matches `label`."""
    for r in sorted(rows):
        if len(rows[r]) > col and rows[r][col] == label:
            return rows[r]
    return None


def to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
