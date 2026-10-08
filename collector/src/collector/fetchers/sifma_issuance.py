"""SIFMA US corporate bond issuance - monthly IG / HY $ volumes (keyless).

Source page: https://www.sifma.org/research/statistics/us-corporate-bonds-statistics
("Data is downloadable by monthly, quarterly and annual statistics"; the
workbook's Issuance sheet carries issuance in $B broken out into investment
grade / high yield, nonconvertible / convertible, ...).

Primary-market credit stress signal: when HY issuance shuts, that is real
stress. The workbook's live monthly block only covers ~13 months, so the
first run deep-backfills Jan 2020 -> present by merging archived workbook
snapshots (Internet Archive, newest snapshot wins on overlapping months
because SIFMA revises history - e.g. the Dec 2024 convertibles methodology
revision). 15 months (Dec 2022-Feb 2023, Apr 2024-Mar 2025) exist in no
captured snapshot and are honestly absent.

Machine-readable workbook (verified live 2026-10-08):
  https://www.sifma.org/wp-content/uploads/2024/01/US-Corporate-Bonds-Statistics-SIFMA.xlsx
Parsed with the stdlib-only xlsx reader (collector.fetchers.xlsx); the
Issuance sheet is located by its "Series: Issuance" tag row, the IG/HY
columns by the header row reading "Investment Grade" / "High Yield", and
monthly rows by Excel date serials in column A (annual/quarterly/YTD rows
are skipped).

SIFMA posts new data with ~1 month lag and renames the workbook URL when
they post new quarterlies. resolve_workbook() tries the primary URL first,
then falls back to scraping the statistics page for any .xlsx link.
If nothing parses, it raises a clear RuntimeError (and the scheduler job
stays red) rather than silently writing nothing.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "sifma"
SOURCE_PAGE = "https://www.sifma.org/research/statistics/us-corporate-bonds-statistics"
# Verified live 2026-10-08 (through Sep 2026 data). SIFMA moves this file
# when they post new quarterlies - the page-scrape fallback covers that.
PRIMARY_URL = (
    "https://www.sifma.org/wp-content/uploads/2024/01/"
    "US-Corporate-Bonds-Statistics-SIFMA.xlsx"
)
XLSX_MAGIC = b"PK\x03\x04"

HY_SID = "cycle:hy-issuance-monthly"
IG_SID = "cycle:ig-issuance-monthly"

# Archived snapshots for the one-time deep backfill (newest wins on overlap).
# Captures of the live workbook URL; data months verified per snapshot.
SNAPSHOT_URLS = [
    # 2022-01-29 capture of the 2021/12 URL: monthly Jan 2020 - Nov 2022
    "https://web.archive.org/web/20220129164152id_/https://www.sifma.org/"
    "wp-content/uploads/2021/12/US-Corporate-Bonds-Statistics-SIFMA.xlsx",
    # 2024-04-18 capture of the 2021/12 URL: monthly Mar 2023 - Mar 2024
    "https://web.archive.org/web/20240418064700id_/https://www.sifma.org/"
    "wp-content/uploads/2021/12/US-Corporate-Bonds-Statistics-SIFMA.xlsx",
    # 2026-05-30 capture of the 2024/01 URL: monthly Apr 2025 - Apr 2026
    "https://web.archive.org/web/20260530003523id_/https://www.sifma.org/"
    "wp-content/uploads/2024/01/US-Corporate-Bonds-Statistics-SIFMA.xlsx",
]

_XLSX_HREF_RE = re.compile(r'href="([^"]+?\.xlsx?)(?:\?[^"]*)?"', re.IGNORECASE)
_DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_EXCEL_EPOCH = date(1899, 12, 30)


def _parse_month(raw: str) -> date | None:
    """Column-A label -> month-end date.

    Monthly rows arrive either as Excel serial strings ("45930") or as
    ISO-ish strings ("2025-09-30 00:00:00"). Annual ("2015"), quarterly
    ("3Q26"), and YTD labels are rejected (return None).
    """
    raw = (raw or "").strip()
    m = _DATE_PREFIX_RE.match(raw)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return d if 1990 <= d.year <= date.today().year + 1 else None
    try:
        serial = float(raw)
    except (TypeError, ValueError):
        return None
    if not 30000 <= serial <= 73050:  # ~1982 .. ~2100; sanity bound
        return None
    d = _EXCEL_EPOCH + timedelta(days=int(serial))
    return d if 1990 <= d.year <= date.today().year + 1 else None


def _issuance_sheet_rows(content: bytes) -> dict[int, list[str]]:
    """Read the workbook's Issuance sheet (located by tag row)."""
    for sheet_no in range(1, 7):
        try:
            rows = read_sheet(content, sheet=sheet_no)
        except ValueError:
            continue
        for r in sorted(rows):
            cells = rows[r]
            if len(cells) >= 2 and cells[0].strip() == "Series:" \
                    and cells[1].strip().lower().startswith("issuance"):
                return rows
    raise ValueError("no sheet with a 'Series: Issuance' tag row found")


def parse_sifma_issuance(content: bytes) -> dict[str, list[tuple[date, float]]]:
    """Parse the SIFMA corporates workbook -> {series_id: [(month_end, $B)]}.

    Series: cycle:hy-issuance-monthly, cycle:ig-issuance-monthly
    (nonconvertible IG / HY issuance, $ billions).
    """
    if content[:4] != XLSX_MAGIC:
        raise ValueError("not an .xlsx workbook (magic bytes mismatch)")
    rows = _issuance_sheet_rows(content)

    # Header row: column B "Investment Grade", column C "High Yield".
    header_row = None
    for r in sorted(rows):
        cells = rows[r]
        if len(cells) >= 3 and cells[1].strip() == "Investment Grade" \
                and cells[2].strip() == "High Yield":
            header_row = r
            break
    if header_row is None:
        raise ValueError("IG/HY header row not found in Issuance sheet")

    out: dict[str, list[tuple[date, float]]] = {HY_SID: [], IG_SID: []}
    for r in sorted(rows):
        if r <= header_row:
            continue
        cells = rows[r]
        if not cells:
            continue
        d = _parse_month(cells[0])
        if d is None:
            continue  # annual / quarterly / YTD / note row
        ig = to_float(cells[1]) if len(cells) > 1 else None
        hy = to_float(cells[2]) if len(cells) > 2 else None
        if ig is not None:
            out[IG_SID].append((d, round(ig, 3)))
        if hy is not None:
            out[HY_SID].append((d, round(hy, 3)))
    if not out[HY_SID]:
        raise ValueError("no monthly HY issuance rows parsed from SIFMA workbook")
    for pts in out.values():
        pts.sort()
    return out


async def _try_url(get_bytes: GetBytes, url: str) -> tuple[str, bytes] | None:
    """Fetch one workbook URL; return (url, content) iff it parses cleanly."""
    try:
        content = await get_bytes(url)
    except Exception as exc:  # noqa: BLE001 - fall through to next candidate
        log.warning("sifma: %s fetch failed: %s", url, exc)
        return None
    if content[:4] != XLSX_MAGIC:
        log.warning("sifma: %s is not an .xlsx file (%d bytes)", url, len(content))
        return None
    try:
        parse_sifma_issuance(content)
    except ValueError as exc:
        log.warning("sifma: %s downloaded but unusable: %s", url, exc)
        return None
    return url, content


async def resolve_workbook(get_bytes: GetBytes) -> tuple[str, bytes]:
    """Newest usable SIFMA corporates workbook.

    Tries the known primary URL first (it moves when SIFMA posts new
    quarterlies), then scrapes the statistics page for any .xlsx download
    link. Raises RuntimeError with the full failure trail if nothing works.
    """
    errors: list[str] = []
    hit = await _try_url(get_bytes, PRIMARY_URL)
    if hit:
        return hit
    errors.append(f"primary URL failed: {PRIMARY_URL}")
    log.warning("sifma: primary workbook URL failed; scraping %s for .xlsx links",
                SOURCE_PAGE)
    try:
        page = await get_bytes(SOURCE_PAGE)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"sifma: primary workbook failed and statistics page unreachable "
            f"({exc}); SIFMA may have moved the file again - check {SOURCE_PAGE}"
        ) from exc
    seen: set[str] = set()
    for m in _XLSX_HREF_RE.finditer(page.decode("utf-8", "replace")):
        url = m.group(1)
        if url.startswith("/"):
            url = "https://www.sifma.org" + url
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        hit = await _try_url(get_bytes, url)
        if hit:
            log.info("sifma: primary URL dead; fell back to scraped workbook %s", url)
            return hit
        errors.append(f"scraped URL failed: {url}")
    raise RuntimeError(
        "sifma: no usable issuance workbook found - " + "; ".join(errors) +
        f". SIFMA moved/renamed the file again; inspect {SOURCE_PAGE} "
        "(the download currently sits behind a HubSpot form, so look for a "
        "direct wp-content/uploads/*.xlsx link)."
    )


async def backfill_from_snapshots(store: Store, get_bytes: GetBytes) -> int:
    """One-time deep backfill: merge archived workbook snapshots (newest wins).

    The live workbook only carries ~13 months of monthly history; the
    Internet Archive holds older captures back to Jan 2020. Idempotent
    upserts, so safe to re-run. Returns the number of points written.
    """
    merged: dict[date, tuple[float | None, float | None]] = {}
    order = 0
    for url in SNAPSHOT_URLS:
        order += 1
        hit = await _try_url(get_bytes, url)
        if not hit:
            log.warning("sifma backfill: snapshot %d/%d unavailable, skipping",
                        order, len(SNAPSHOT_URLS))
            continue
        _, content = hit
        try:
            series = parse_sifma_issuance(content)
        except ValueError as exc:
            log.warning("sifma backfill: snapshot %d unparsable: %s", order, exc)
            continue
        for d, v in series[IG_SID]:
            ig, hy = merged.get(d, (None, None))
            merged[d] = (v, hy)
        for d, v in series[HY_SID]:
            ig, hy = merged.get(d, (None, None))
            merged[d] = (ig, v)
        log.info("sifma backfill: snapshot %d contributed %d months", order,
                 len(series[HY_SID]))
    items = []
    for d in sorted(merged):
        ig, hy = merged[d]
        if ig is not None:
            items.append((IG_SID, d, ig))
        if hy is not None:
            items.append((HY_SID, d, hy))
    if items:
        store.upsert_points_batch(items)
    return len(items)


async def fetch_sifma_issuance(store: Store, get_bytes: GetBytes) -> str:
    """Monthly job: SIFMA US corporate issuance, IG + HY, $B.

    Stores cycle:hy-issuance-monthly / cycle:ig-issuance-monthly.
    First run (no history yet) deep-backfills Jan 2020 -> present from
    archived snapshots; later runs just upsert the live workbook's ~13
    months idempotently.
    """
    existing = store.points(HY_SID)
    if not existing:
        n = await backfill_from_snapshots(store, get_bytes)
        log.info("sifma: first run, deep-backfilled %d snapshot points", n)
    url, content = await resolve_workbook(get_bytes)
    series = parse_sifma_issuance(content)
    items = [(sid, d, v) for sid, pts in series.items() for d, v in pts]
    store.upsert_points_batch(items)
    latest = max(d for d, _ in series[HY_SID])
    store.put_doc("sifma_issuance", {
        "workbook": url,
        "source_page": SOURCE_PAGE,
        "note": ("US corporate bond issuance, nonconvertible, $B, Refinitiv via "
                 "SIFMA. Monthly block carries ~13 months; older history "
                 "(Jan 2020 -> ~13mo ago) backfilled from Internet Archive "
                 "snapshots, newest snapshot wins on overlap because SIFMA "
                 "revises history. Months with no captured snapshot "
                 "(Dec 2022-Feb 2023, Apr 2024-Mar 2025) are honestly absent. "
                 "Includes all corporate debt, MTNs and Yankee bonds; excludes "
                 "issues with maturity <= 1 year and CDs. ~1 month publication lag."),
        "unit": "billions of USD",
        "latest_month": latest.isoformat(),
        "latest_hy_usd_b": dict(series[HY_SID])[latest],
        "latest_ig_usd_b": dict(series[IG_SID]).get(latest),
    }, source=SOURCE)
    log.info("sifma: %d series, %d points from %s (latest %s)",
             len(series), len(items), url, latest)
    return SOURCE
