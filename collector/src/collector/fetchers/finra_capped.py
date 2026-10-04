"""FINRA capped volume report — monthly corporate/agency capped trade volume (keyless CSV).

Harry asked for: https://www.finra.org/finra-data/browse-catalog/fact-sheet/capped-volume-report
("Corporate and Agencies Capped Volume Report" — monthly capped par volume
and average trade size by grade, published the 1st business day of the month
for the prior month; verified HTTP 200 2026-10-04).

  CSV: https://cdn.finra.org/trace/cta/monthly/CA_CTA.csv
  Two header rows (grouped labels, then MONTH,Grade,AVG Size (000s),Total,...),
  one data row per MONTH x Grade. Grades: Investment Grade, High Yield,
  Agency, 144A - IG, 144A - HY. 12 rolling months per file.

This is a different feed from the TRACE monthly volume report (trace_monthly.py,
currently CDN-blocked): that one is per-product TRACE aggregates, this one is
capped-size trade statistics for corporate/agency bonds.

Stored:
  cycle:finra-cap-ig-avgsize      Investment Grade avg trade size ($000s)
  cycle:finra-cap-ig-total        Investment Grade total capped par ($)
  cycle:finra-cap-hy-avgsize      High Yield avg trade size ($000s)
  cycle:finra-cap-hy-total        High Yield total capped par ($)
  cycle:finra-cap-agcy-avgsize    Agency avg trade size ($000s)
  cycle:finra-cap-agcy-total      Agency total capped par ($)
  cycle:finra-cap-144a-ig-total   144A IG total capped par ($)
  cycle:finra-cap-144a-hy-total   144A HY total capped par ($)
plus a "finra_capped" snapshot doc holding the latest month's per-grade rows.
"""
from __future__ import annotations

import calendar
import csv
import io
import logging
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

CAPPED_URL = "https://cdn.finra.org/trace/cta/monthly/CA_CTA.csv"

SOURCE = "finra-capped"

# CSV Grade label -> series slug
GRADE_SLUGS = {
    "Investment Grade": "ig",
    "High Yield": "hy",
    "Agency": "agcy",
    "144A - IG": "144a-ig",
    "144A - HY": "144a-hy",
}

SERIES_IDS = [
    "finra-cap-ig-avgsize",
    "finra-cap-ig-total",
    "finra-cap-hy-avgsize",
    "finra-cap-hy-total",
    "finra-cap-agcy-avgsize",
    "finra-cap-agcy-total",
    "finra-cap-144a-ig-total",
    "finra-cap-144a-hy-total",
]

EXPECTED_COLS = ["MONTH", "Grade", "AVG Size (000s)", "Total"]


def parse_num(v: object) -> float | None:
    """Parse a number that may carry comma thousands separators."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_month(s: str) -> date:
    """'Sep-2026' -> 2026-09-30 (month-end dating, like other monthly feeds)."""
    try:
        mon, yr = s.strip().split("-")
        dt = date(int(yr), _month_num(mon), 1)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"unparseable capped-volume month: {s!r}") from exc
    _, last = calendar.monthrange(dt.year, dt.month)
    return date(dt.year, dt.month, last)


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _month_num(abbr: str) -> int:
    return _MONTHS[abbr.strip()[:3].title()]


def parse_capped(text: str) -> dict[date, dict[str, dict[str, float]]]:
    """Parse the CA_CTA.csv -> {month_end: {grade_slug: {avgsize, total}}}.

    Skips the two header rows; raises ValueError on an unexpected header so
    a format change surfaces in the health strip instead of silently landing
    empty.
    """
    reader = csv.reader(io.StringIO(text))
    grouped = next(reader, None)  # grouped column labels, not parsed
    header = next(reader, None)
    if (not grouped or not header
            or [c.strip() for c in header[:4]] != EXPECTED_COLS):
        raise ValueError(f"unexpected capped-volume header: {header}")
    out: dict[date, dict[str, dict[str, float]]] = {}
    for row in reader:
        if len(row) < 4 or not row[0].strip():
            continue
        slug = GRADE_SLUGS.get(row[1].strip())
        if slug is None:
            log.debug("finra_capped: skipping unknown grade %r", row[1])
            continue
        avgsize = parse_num(row[2])
        total = parse_num(row[3])
        if avgsize is None or total is None:
            log.debug("finra_capped: skipping %s %s (bad numbers)",
                      row[0], row[1])
            continue
        month = parse_month(row[0])
        out.setdefault(month, {})[slug] = {"avgsize": avgsize, "total": total}
    if not out:
        raise ValueError("capped-volume CSV parsed to zero months")
    return out


async def fetch_finra_capped(store: Store, get_text: GetText,
                             today: date | None = None) -> str:
    """Monthly: fetch the capped volume CSV and store all 12 rolling months.

    The file always carries the full 12-month window, so every run is a
    complete refresh — idempotent via upsert, no separate backfill needed.
    """
    today = today or date.today()
    text = await get_text(CAPPED_URL)
    if len(text) < 100:
        raise ValueError("capped-volume CSV suspiciously short")
    parsed = parse_capped(text)

    months = sorted(parsed)
    for month in months:
        grades = parsed[month]
        for slug, vals in grades.items():
            if slug in ("ig", "hy", "agcy"):
                store.upsert_points(f"cycle:finra-cap-{slug}-avgsize",
                                    [(month, vals["avgsize"])])
            store.upsert_points(f"cycle:finra-cap-{slug}-total",
                                [(month, vals["total"])])

    latest = months[-1]
    store.put_doc("finra_capped", {
        "as_of": latest.isoformat(),
        "grades": {slug: {"avgsize": vals["avgsize"],
                          "total": round(vals["total"], 2)}
                   for slug, vals in parsed[latest].items()},
        "months": len(months),
    }, source=SOURCE)
    log.info("finra_capped: %d months through %s", len(months), latest)
    return SOURCE
