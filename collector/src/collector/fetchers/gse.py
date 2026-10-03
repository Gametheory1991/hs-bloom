"""GSE retained mortgage portfolios: Fannie Mae + Freddie Mac (keyless).

Both GSEs publish a Monthly Volume Summary as a PDF with fully predictable,
date-based URLs (verified live 2026-10-03):

  Fannie Mae : https://www.fanniemae.com/media/document/pdf/MMDDYY.pdf
               (MMDD = month-end date, YY = year; e.g. 083126.pdf = Aug 2026).
               The URL 301-redirects to /media/<id>/display which serves the
               PDF to any UA as long as redirects are followed. Table 3
               "Retained Mortgage Portfolio Activity" gives the monthly
               retained-portfolio ending balance ($M). Each PDF carries ~13
               months of history rows.
  Freddie Mac: https://www.freddiemac.com/investors/financials/pdf/MMYYmvs.pdf
               (e.g. 0826mvs.pdf). Table 2 "Mortgage-Related Investments
               Portfolio" gives the ending balance ($M); Table 3 gives the
               agency-securities component ($M). ~13 months of history per PDF.

Parsing is via `pdftotext -layout` (poppler-utils): pypdf's extraction
interleaves the side-by-side tables and is unusable. The Dockerfile installs
poppler-utils; if it is missing the job raises a clear error and degrades
(the dashboard shows the GSE panel as empty rather than crashing).

Release lag is ~25 days (August summaries published late September), so the
job probes the last 4 month-ends newest-first and parses the first PDF that
returns real rows. Monthly cadence.

Store keys (values in $ millions, month-end dates):
  gse:fannie-retained    Fannie Mae retained mortgage portfolio, end balance
  gse:freddie-retained   Freddie Mac mortgage-related investments, end balance
  gse:freddie-agency     Freddie Mac agency securities component, end balance
Plus a `gse` doc with the latest values for panels/insights.
"""
from __future__ import annotations

import calendar
import logging
import re
import shutil
import subprocess
from datetime import date

from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "gse-monthly-summary"

FANNIE_URL = "https://www.fanniemae.com/media/document/pdf/{mm}{dd}{yy}.pdf"
FREDDIE_URL = "https://www.freddiemac.com/investors/financials/pdf/{mm}{yy}mvs.pdf"

KEY_FANNIE = "gse:fannie-retained"
KEY_FREDDIE = "gse:freddie-retained"
KEY_FREDDIE_AGENCY = "gse:freddie-agency"

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_FULL = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

_ABBR = "|".join(_MONTH_ABBR)
_FULL = "|".join(_MONTH_FULL)

# Freddie Table 2: "Aug 2025  $33,508  ($24,979)  ($894)  $113,546  86.5%  10.1%"
# (continuation rows omit the $ and the year: "Sep  33,451  (29,873) ...").
# groups: mon, year?, purchases, sales, liquidations, END BALANCE.
FREDDIE_T2 = re.compile(
    rf"\b({_ABBR})\s+(\d{{4}})?\s*\$?\s*([\d,]+)\s+\(\$?\s*([\d,]+)\)\s+"
    rf"\(\$?\s*([\d,]+)\)\s+\$?\s*([\d,]+)",
    re.IGNORECASE,
)
# Freddie Table 3: "Aug 2025  $30,630  $885  $82,031  $113,546"
# groups: mon, year?, AGENCY SECURITIES, non-agency, mortgage loans, end balance.
FREDDIE_T3 = re.compile(
    rf"\b({_ABBR})\s+(\d{{4}})?\s*\$?\s*([\d,]+)\s+\$?\s*([\d,]+)\s+\$?\s*([\d,]+)\s+\$?\s*([\d,]+)",
    re.IGNORECASE,
)
# Fannie Table 3: "August 2025  17,509  (10,950)  (837)  93,304"
# The year is mandatory (Table 7/8 footnotes reuse month names with tiny
# parenthesised values) and the ending balance is 5-6 digits.
# groups: mon, year, purchases, sales, liquidations, END BALANCE.
FANNIE_T3 = re.compile(
    rf"\b({_FULL})\s+(\d{{4}})\s+([\d,]+)\s+\(([\d,]+)\)\s+\(([\d,]+)\)\s+([\d,]{{4,}})",
    re.IGNORECASE,
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_ends(n: int = 4, today: date | None = None) -> list[date]:
    """Last n month-ends, newest first (release lag ~25d, so probe back)."""
    today = today or date.today()
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(_month_end(y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def _pdftotext(pdf: bytes) -> str:
    """PDF -> layout-preserved text. Requires poppler-utils (Dockerfile)."""
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext not found: install poppler-utils (see Dockerfile)"
        )
    proc = subprocess.run(
        ["pdftotext", "-layout", "-", "-"],
        input=pdf,
        capture_output=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {proc.stderr.decode()[:200]}")
    return proc.stdout.decode("utf-8", "replace")


def _section_lines(
    text: str, start_marker: str, right_marker: str, end_marker: str
) -> list[tuple[str, str]]:
    """Lines between the start_marker header and the end_marker header, each
    split into (left, right) at the column where the right_marker header
    begins. Table 2/3 (Freddie) and Table 3/4 (Fannie) sit side-by-side, so
    the left part holds the activity table and the right part the composition
    table; the start bound excludes earlier same-shaped tables (Freddie
    Table 1) and the end bound excludes later ones (Freddie Table 5 debt,
    Fannie Table 5+)."""
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if start_marker in line:
            start = i
            break
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if end_marker in lines[i]:
            end = i
            break
    col: int | None = None
    for line in lines[start:end]:
        j = line.find(right_marker)
        if j != -1:
            col = j
            break
    out = []
    for line in lines[start:end]:
        if col is None:
            out.append((line, ""))
        else:
            out.append((line[:col], line[col:]))
    return out


def parse_freddie(text: str) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    """(retained end-balance, agency-securities) monthly series, $M."""
    retained: dict[date, float] = {}
    agency: dict[date, float] = {}
    year: int | None = None
    for left, right in _section_lines(text, "TABLE 2", "TABLE 3", "TABLE 4"):
        if "full-year" in left.lower():
            continue
        m2 = FREDDIE_T2.search(left)
        if m2:
            if m2.group(2):
                year = int(m2.group(2))
            if year:
                mon = _MONTH_ABBR[m2.group(1).lower()]
                retained[_month_end(year, mon)] = _num(m2.group(6))
        m3 = FREDDIE_T3.search(right)
        if m3:
            if m3.group(2):
                year = int(m3.group(2))
            if year:
                mon = _MONTH_ABBR[m3.group(1).lower()]
                agency[_month_end(year, mon)] = _num(m3.group(3))
    if not retained:
        raise ValueError("freddie PDF: no Table 2 rows parsed")
    return sorted(retained.items()), sorted(agency.items())


def parse_fannie(text: str) -> list[tuple[date, float]]:
    """Fannie Mae retained mortgage portfolio end-balance, monthly, $M."""
    out: dict[date, float] = {}
    for left, _ in _section_lines(text, "Table 3", "Table 4", "Table 5"):
        if "full year" in left.lower():
            continue
        m = FANNIE_T3.search(left)
        if not m:
            continue
        mon = _MONTH_FULL[m.group(1).lower()]
        out[_month_end(int(m.group(2)), mon)] = _num(m.group(6))
    if not out:
        raise ValueError("fannie PDF: no Table 3 rows parsed")
    return sorted(out.items())


async def _probe(
    urls: list[str], parse, get_bytes: GetBytes, label: str
):
    """Try candidate URLs newest-first; return parsed series from the first
    PDF that yields rows. Raises if none work."""
    last_err: Exception | None = None
    for url in urls:
        try:
            pdf = await get_bytes(url)
        except Exception as exc:  # noqa: BLE001 — try the previous month
            last_err = exc
            continue
        if not pdf[:5] == b"%PDF-":
            last_err = ValueError(f"{label}: not a PDF ({url})")
            continue
        try:
            return parse(_pdftotext(pdf)), url
        except Exception as exc:  # noqa: BLE001 — unparsable PDF, try older
            last_err = exc
            continue
    raise RuntimeError(f"{label}: no usable monthly summary PDF: {last_err}")


async def fetch_gse(store: Store, get_bytes: GetBytes) -> str:
    """Monthly job: GSE retained-portfolio balances from both enterprises.

    Fannie and Freddie are fetched independently — one bad PDF must not
    starve the other."""
    errors: list[str] = []
    ends = _month_ends()

    fannie_urls = [
        FANNIE_URL.format(mm=f"{d.month:02d}", dd=f"{d.day:02d}", yy=f"{d.year % 100:02d}")
        for d in ends
    ]
    try:
        (fannie_pts, fannie_url) = await _probe(fannie_urls, parse_fannie, get_bytes, "fannie")
        store.upsert_points(KEY_FANNIE, fannie_pts)
    except Exception as exc:  # noqa: BLE001 — per-source isolation
        errors.append(f"fannie: {exc}")
        fannie_pts, fannie_url = [], None

    freddie_urls = [
        FREDDIE_URL.format(mm=f"{d.month:02d}", yy=f"{d.year % 100:02d}")
        for d in ends
    ]
    try:
        ((fred_pts, fred_agency), freddie_url) = await _probe(
            freddie_urls, parse_freddie, get_bytes, "freddie"
        )
        store.upsert_points(KEY_FREDDIE, fred_pts)
        store.upsert_points(KEY_FREDDIE_AGENCY, fred_agency)
    except Exception as exc:  # noqa: BLE001 — per-source isolation
        errors.append(f"freddie: {exc}")
        fred_pts, fred_agency, freddie_url = [], [], None

    def latest(pts):
        return {"date": pts[-1][0].isoformat(), "value_usd_m": pts[-1][1]} if pts else None

    store.put_doc(
        "gse",
        {
            "fannie_retained": latest(fannie_pts),
            "freddie_retained": latest(fred_pts),
            "freddie_agency": latest(fred_agency),
            "fannie_url": fannie_url,
            "freddie_url": freddie_url,
        },
        source=SOURCE,
    )
    if errors and not (fannie_pts or fred_pts):
        raise RuntimeError(f"gse failures: {'; '.join(errors)}")
    if errors:
        log.warning("gse partial failure: %s", "; ".join(errors))
    return "gse"
