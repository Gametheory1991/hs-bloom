"""ICI estimated long-term mutual fund flows  -  weekly (estimated) + monthly (actual) aggregates (keyless).

Source: https://www.ici.org/research/stats/flows ("Estimated Long-Term Mutual
Fund Flows", released most Wednesdays for the week ended the prior Wednesday).
Machine-readable workbook (verified live 2026-10-07):
  https://www.ici.org/flows_data_<YEAR>.xls

BLOCK STATUS 2026-10-09: ici.org serves an Akamai edge "Access Denied" to
our hosts regardless of URL — https://www.ici.org/flows_data_2026.xls,
..._2025.xls and ..._2024.xls all return HTTP 403 (browser User-Agent,
~1.4s; reference: errors.edgesuite.net, so this is an IP/host block, not
a missing-file 404). Alternate URL https://www.ici.org/estimated_flows_data_2026.xls
also 403s, the landing page https://www.ici.org/research/stats/flows also
403s, https://ici.org/flows_data_2026.xls (no www) gets an empty
reply, the full browser-navigation header set (Sec-Fetch-*) still 403s,
and staging domain https://ici-dev.ici.org also 403s. DORMANT as of
2026-10-09: replaced in the scheduler by fetchers.fred_mf_flows (FRED Z.1
quarterly net share issuance, same economic concept, official source). Do not fake data from this source; resolve_workbook keeps raising
honestly ("no ICI flows workbook available") until the block lifts.
Only the CURRENT-year workbook is published; prior-year URLs 404/403, so
history is whatever the current workbook carries (monthly back to Jan 2024,
weekly for the current year only). The workbook is a legacy .xls with one
sheet, "Weekly MF Flow Estimates" (xlrd is already a dependency for AAII).

Layout: header rows 4-6 are a 3-level category tree; data lives in the odd
columns 1..39 (even columns are blank spacers). Two data sections:
  "Monthly Net New Cash Flow"       -  ACTUAL net new cash flow, month-end dates
  "Estimated Weekly Net New Cash Flow"  -  ESTIMATED weekly flows, ~1 week lag
Values are millions of USD; positive = net inflow.

COVERAGE (labelled honestly on every series): aggregate industry flows only,
no per-fund detail. ETFs and funds that invest primarily in other mutual funds
are excluded. Estimates cover ~98% of industry assets.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import xlrd

from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "ici"
URL_TEMPLATE = "https://www.ici.org/flows_data_{year}.xls"
SHEET = "Weekly MF Flow Estimates"
XLS_MAGIC = b"\xd0\xcf\x11\xe0"  # real .xls, not an HTML error page

# column index -> series slug, from the workbook's 3-level header
# (row 4: group, row 5: subgroup, row 6: detail). 20 categories.
COL_SLUG = {
    1: "total-longterm",
    3: "equity-total",
    5: "equity-domestic-total",
    7: "equity-domestic-large-cap",
    9: "equity-domestic-mid-cap",
    11: "equity-domestic-small-cap",
    13: "equity-domestic-multi-cap",
    15: "equity-domestic-other",
    17: "equity-world-total",
    19: "equity-world-developed",
    21: "equity-world-emerging",
    23: "hybrid",
    25: "bond-total",
    27: "bond-taxable-total",
    29: "bond-taxable-investment-grade",
    31: "bond-taxable-high-yield",
    33: "bond-taxable-government",
    35: "bond-taxable-multisector",
    37: "bond-taxable-global",
    39: "bond-municipal",
}

_DATE_FORMATS = ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d")


def _cell_date(cell: xlrd.sheet.Cell, datemode: int) -> date | None:
    """xlrd cell -> date, handling both Excel date cells and date strings."""
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(cell.value, datemode).date()
        except Exception:  # noqa: BLE001
            return None
    if cell.ctype == xlrd.XL_CELL_TEXT:
        text = cell.value.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _cell_float(cell: xlrd.sheet.Cell) -> float | None:
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return float(cell.value)
    if cell.ctype == xlrd.XL_CELL_TEXT:
        try:
            return float(cell.value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def parse_ici_workbook(content: bytes) -> dict[str, list[tuple[date, float]]]:
    """Parse the ICI flows workbook bytes into series_id -> [(date, $M)].

    Keys are "cycle:ici-mf-<slug>" for the estimated weekly section and
    "cycle:ici-mf-<slug>-monthly" for the actual monthly section.
    """
    if content[:4] != XLS_MAGIC:
        raise ValueError("not a legacy .xls workbook (magic bytes mismatch)")
    try:
        book = xlrd.open_workbook(file_contents=content)
    except Exception as exc:
        raise ValueError(f"xlrd could not open ICI workbook: {exc}") from exc
    try:
        sheet = book.sheet_by_name(SHEET)
    except xlrd.XLRDError as exc:
        raise ValueError(f"sheet {SHEET!r} missing: {book.sheet_names()}") from exc

    out: dict[str, list[tuple[date, float]]] = {}
    section: str | None = None  # "monthly" | "weekly"
    for r in range(sheet.nrows):
        first = sheet.cell(r, 0)
        label = first.value.strip() if first.ctype == xlrd.XL_CELL_TEXT else ""
        if "Monthly Net New Cash Flow" in label:
            section = "monthly"
            continue
        if "Estimated Weekly Net New Cash Flow" in label:
            section = "weekly"
            continue
        if section is None:
            continue
        d = _cell_date(first, book.datemode)
        if d is None:
            continue  # section header, note row, or blank
        for col, slug in COL_SLUG.items():
            v = _cell_float(sheet.cell(r, col))
            if v is None:
                continue
            sid = f"cycle:ici-mf-{slug}" + ("-monthly" if section == "monthly" else "")
            out.setdefault(sid, []).append((d, v))
    if not out:
        raise ValueError("no data rows parsed from ICI workbook")
    for pts in out.values():
        pts.sort()
    return out


async def resolve_workbook(get_bytes: GetBytes, today: date | None = None) -> tuple[str, bytes]:
    """Newest available ICI workbook: current year, then previous year.

    The year rolls over in early January before the new workbook exists, so
    the fallback keeps the job green across the transition.
    """
    today = today or date.today()
    errors: list[str] = []
    for year in (today.year, today.year - 1):
        url = URL_TEMPLATE.format(year=year)
        try:
            content = await get_bytes(url)
        except Exception as exc:  # noqa: BLE001  -  try the fallback year
            errors.append(f"{year}: {exc}")
            continue
        if content[:4] == XLS_MAGIC:
            return url, content
        errors.append(f"{year}: not an .xls file ({len(content)} bytes)")
    raise RuntimeError("no ICI flows workbook available: " + "; ".join(errors))


async def fetch_ici_mutual_flows(
    store: Store, get_bytes: GetBytes, today: date | None = None
) -> str:
    """Weekly job: ICI long-term mutual fund flow aggregates.

    Weekly (estimated) series at cycle:ici-mf-<slug>; monthly (actual) series
    at cycle:ici-mf-<slug>-monthly. Values in $M net new cash flow.
    Upserts are idempotent, so re-runs never duplicate history.
    """
    url, content = await resolve_workbook(get_bytes, today)
    series = parse_ici_workbook(content)
    items = [
        (sid, d, v) for sid, pts in series.items() for d, v in pts
    ]
    store.upsert_points_batch(items)
    weekly = {sid: pts[-1] for sid, pts in series.items() if not sid.endswith("-monthly")}
    latest = max((d for d, _ in weekly.values()), default=None)
    store.put_doc("ici_mutual_flows", {
        "workbook": url,
        "source_page": "https://www.ici.org/research/stats/flows",
        "note": ("Weekly rows are ESTIMATED net new cash flow (~1 week lag, "
                 "released Wednesdays for the week ended the prior Wednesday); "
                 "monthly rows are ACTUAL net new cash flow. "
                 "AGGREGATE industry flows only: no per-fund detail. "
                 "ETFs and funds that invest primarily in other mutual funds "
                 "are excluded; estimates cover ~98% of industry assets. "
                 "Only the current-year workbook is published, so history is "
                 "bounded by what it carries (monthly back to Jan 2024; "
                 "weekly for the current year)."),
        "unit": "millions of USD, positive = net inflow",
        "categories": sorted(COL_SLUG.values()),
        "latest_weekly": latest.isoformat() if latest else None,
        "latest_weekly_total_longterm_usd_m": weekly.get("cycle:ici-mf-total-longterm", (None, None))[1],
    }, source=SOURCE)
    log.info("ici: %d series, %d points from %s", len(series), len(items), url)
    return SOURCE
