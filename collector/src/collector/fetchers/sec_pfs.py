"""SEC Private Fund Statistics — IM Analytics Office (quarterly batch).

Index (verified live 2026-10-05):
  https://www.sec.gov/data-research/investment-management-data/division-investment-management-private-fund-statistics
Latest at research time: 2025 Q3 report + supporting XLSX
  https://www.sec.gov/files/investment/private-funds-statistics-2025-q3-supporting-data.xlsx
  (HTTP 200, 592 KB, posted 2026-04-01).

The workbook holds ~100+ tables — the only *public* Form PF aggregates:
hedge-fund leverage, borrowing distribution, gross-notional-exposure/NAV,
strategy exposures, investor-vs-portfolio liquidity mismatch. It directly
fills the "private funds" hole in the risk tab and cross-checks the OFR HF
series (same underlying Form PF). Data is typically >=6 months old at
publication (the 2025 Q3 report covers 2025-Q3 filings).

CAVEAT (honest): SEC 403s anonymous fetches, so the real workbook's sheet
and table layout could not be inspected in the research session. The parser
below is therefore anchor-driven, not cell-address-driven: it scans every
sheet for label rows matching keyword anchors and takes the last numeric
cell of the matched row as the latest quarter. The doc records exactly
which tables were found vs missed, so a layout drift degrades to a thinner
panel, never a crash or a misread number.

SEC fair-access rules: declared contact UA, <=1 req/2s.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

from collector.config import SecDataCfg
from collector.fetchers.sec_ncen import quarter_end  # shared SEC batch plumbing
from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

INDEX_URL = ("https://www.sec.gov/data-research/investment-management-data/"
             "division-investment-management-private-fund-statistics")
XLSX_RE = re.compile(
    r'href="([^"]*private-funds-statistics-(\d{4})-q(\d)-supporting-data\.xlsx)"',
    re.IGNORECASE,
)
FALLBACK_URL = ("https://www.sec.gov/files/investment/"
                "private-funds-statistics-2025-q3-supporting-data.xlsx")
SOURCE = "sec-pfs"

# metric -> (anchor keywords, prefer-hedge-fund row)
METRICS = {
    "pfs-hf-leverage": (("leverage",), True),
    "pfs-hf-borrowing": (("borrowing",), True),
    "pfs-hf-gne-nav": (("gross notional",), True),
    "pfs-hf-liq-mismatch": (("liquidity",), True),
}

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def resolve_xlsx_url(index_html: str) -> tuple[str, int, int]:
    """Latest supporting-data XLSX from the index page; (url, year, quarter)."""
    base = "https://www.sec.gov"
    best: tuple[int, int, str] | None = None
    for href, year, q in XLSX_RE.findall(index_html):
        key = (int(year), int(q))
        url = href if href.startswith("http") else base + href
        if best is None or key > best[:2]:
            best = (key[0], key[1], url)
    if best is None:
        log.warning("pfs: no xlsx link on index page; using fallback %s", FALLBACK_URL)
        return FALLBACK_URL, 2025, 3
    return best[2], best[0], best[1]


def list_sheet_names(data: bytes) -> list[str]:
    """Sheet names from xl/workbook.xml (1-based order for read_sheet)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("xl/workbook.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"xlsx workbook.xml unreadable: {exc}") from exc
    return [s.get("name", "") for s in root.findall(".//m:sheet", _NS)]


def _row_values(rows: dict[int, list[str]], r: int) -> list[str]:
    return [c.strip() for c in rows.get(r, [])]


def find_metric(rows_by_sheet: list[dict[int, list[str]]], sheet_names: list[str],
                anchors: tuple[str, ...], prefer_hedge: bool
                ) -> tuple[float | None, str | None]:
    """Scan all sheets for a label row matching the anchors.

    Returns (latest numeric value, matched label) — the latest value is the
    LAST numeric cell of the matched row. Among candidate rows, prefer one
    whose label also mentions hedge funds.
    """
    candidates: list[tuple[bool, str, list[str]]] = []
    for name, rows in zip(sheet_names, rows_by_sheet):
        for r in sorted(rows):
            cells = _row_values(rows, r)
            if not cells:
                continue
            label = cells[0].lower()
            if not label or not any(a in label for a in anchors):
                continue
            # skip header-ish rows with no numerics at all
            if not any(to_float(c) is not None for c in cells[1:]):
                continue
            candidates.append(("hedge" in label, cells[0], cells))
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: (not c[0], len(c[2])))  # hedge rows first
    _, label, cells = candidates[0]
    numerics = [to_float(c) for c in cells[1:]]
    numerics = [v for v in numerics if v is not None]
    return (numerics[-1] if numerics else None), label


def parse_pfs_workbook(data: bytes, metrics: dict | None = None) -> dict:
    """Anchor-driven extraction of the key risk tables.

    Returns {"values": {metric: value}, "found": {metric: label},
    "missed": [metric]}. `metrics` defaults to METRICS; tests inject their
    own so they never mutate the module global.
    """
    metrics = METRICS if metrics is None else metrics
    names = list_sheet_names(data)
    rows_by_sheet = [read_sheet(data, sheet=i + 1) for i in range(len(names))]
    values: dict[str, float] = {}
    found: dict[str, str] = {}
    missed: list[str] = []
    for metric, (anchors, prefer_hedge) in metrics.items():
        val, label = find_metric(rows_by_sheet, names, anchors, prefer_hedge)
        if val is None:
            missed.append(metric)
            log.warning("pfs: anchor %r not found in workbook", anchors)
        else:
            values[metric] = val
            found[metric] = label or ""
    if not values:
        raise ValueError("no PFS risk tables matched any anchor")
    return {"values": values, "found": found, "missed": missed}


async def fetch_sec_pfs(cfg: SecDataCfg, store: Store, get_bytes: GetBytes) -> str:
    """Monthly poll: resolve the latest Private Fund Statistics XLSX from the
    index page, extract the key risk tables, store quarterly points."""
    headers = {"User-Agent": cfg.user_agent}
    index_html = (await get_bytes(INDEX_URL, headers=headers)).decode(
        "utf-8", errors="replace")
    url, year, q = resolve_xlsx_url(index_html)
    data = await get_bytes(url, headers=headers)
    if len(data) < 50000:
        raise ValueError(f"suspiciously small PFS xlsx ({len(data)}B)")
    parsed = parse_pfs_workbook(data)
    asof = quarter_end(year, q)
    for metric, val in parsed["values"].items():
        store.upsert_points(f"cycle:{metric}", [(asof, float(val))])
    store.put_doc("sec_pfs", {
        "report": f"{year}q{q}",
        "as_of": asof.isoformat(),
        "note": ("Public Form PF aggregates; data typically >=6 months old at "
                 "publication. Anchor-driven parse — 'found' lists the matched "
                 "table labels, 'missed' the tables not found in this edition."),
        "found": parsed["found"],
        "missed": parsed["missed"],
        **{k: v for k, v in parsed["values"].items()},
    }, source=SOURCE)
    return SOURCE
