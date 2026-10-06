"""FINRA-ICE Data Services Structured Trading Activity Reports (STAR) — daily.

Harry found: https://www.finra.org/finra-data/browse-catalog/structured-product-activity-reports-and-tables/historic-reports
This is the PUBLIC equivalent of the ICE Vantage structured aggregates
(login-walled) — daily structured-product trading activity, free on the
FINRA CDN with no auth:

  ZIP: https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-YYYYMM.zip
  Each ZIP holds one XLSX per trading day:
    FINRA_IDS_STAR-YYYYMMDD.xlsx    (Structured Trading Activity Reports)
    FINRA_IDS_PXTABLES-YYYYMMDD.xlsx (Pricing Tables — not parsed here)

STAR layout (verified 2026-09-30): sheet "TradingActivity".
  Agency block — columns per issuer (UMBS / FNMA / FHLMC / GNMA), each with
  TRADE COUNT | UNIQUE SEC IDs | $ TRADES (000s):
    AGENCY PASS-THRU (TBA, STIP, $ ROLLS): SINGLE FAMILY 15Y / 30Y / OTHER
    AGENCY PASS-THRU (SPECIFIED): SINGLE FAMILY 15Y / 30Y / ADJUSTABLE/HYBRID / OTHER
    AGENCY CMO: P&I / IO/PO
  IG / Non-IG block — columns per grade (INVESTMENT GRADE / NON-INVESTMENT
  GRADE), each with TRADE COUNT | UNIQUE SEC IDs | $ TRADES (000s):
    NON-AGENCY CMO: P&I / IO/PO
    NON-AGENCY CMBS: P&I / IO/PO
    AGENCY CMBS: P&I / IO/PO
    ABS / CBO/CDO/CLO / OTHER (single rows)

"*" in a cell = trade count < 5 (suppressed); treated as 0 in aggregates.
$ TRADES are in $000s — stored as $ (x1000).

Stored (daily):
  cycle:star-tba-par / -trades            TBA total ($ / trades)
  cycle:star-tba-umbs-par / -fnma-par / -fhlmc-par / -gnma-par   TBA by issuer ($)
  cycle:star-spec-par / -trades           specified pools
  cycle:star-agcmo-par / -trades          agency CMO
  cycle:star-nagcmo-ig-par / -nonig-par   non-agency CMO by grade ($)
  cycle:star-nagcmbs-ig-par / -nonig-par  non-agency CMBS by grade ($)
  cycle:star-agcmbs-par / -trades         agency CMBS
  cycle:star-abs-ig-par / -nonig-par      ABS by grade ($)
  cycle:star-clo-ig-par / -nonig-par      CBO/CDO/CLO by grade ($)
plus a "finra_ids_star" snapshot doc with the latest day's summary.

ZIPs go back to 2011; on an empty store we backfill 36 months (3Y, matching
Harry's historical-grid requirement), then the daily job re-fetches the
current + previous month ZIPs (idempotent upserts).
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import re
import zipfile
from datetime import date

from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

ZIP_URL = "https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-{}.zip"
STAR_RE = re.compile(r"FINRA_IDS_STAR-(\d{8})\.xlsx")
SOURCE = "finra-ids-star"
REQUEST_GAP = 1.0
BACKFILL_MONTHS = 36

SERIES_IDS = [
    "star-tba-par", "star-tba-trades",
    "star-tba-umbs-par", "star-tba-fnma-par",
    "star-tba-fhlmc-par", "star-tba-gnma-par",
    "star-spec-par", "star-spec-trades",
    "star-agcmo-par", "star-agcmo-trades",
    "star-nagcmo-ig-par", "star-nagcmo-nonig-par",
    "star-nagcmo-par", "star-nagcmo-trades",
    "star-nagcmbs-ig-par", "star-nagcmbs-nonig-par",
    "star-nagcmbs-par", "star-nagcmbs-trades",
    "star-agcmbs-par", "star-agcmbs-trades",
    "star-abs-ig-par", "star-abs-nonig-par", "star-abs-par", "star-abs-trades",
    "star-clo-ig-par", "star-clo-nonig-par", "star-clo-par", "star-clo-trades",
]

# section header label -> (key, column layout)
# "agency": 12 cols = 4 issuers x (trades, secids, $000s)
# "ig": 6 cols = 2 grades x (trades, secids, $000s)
SECTIONS = {
    "AGENCY PASS-THRU (TBA, STIP, $ ROLLS)": ("tba", "agency"),
    "AGENCY PASS-THRU (SPECIFIED)": ("spec", "agency"),
    "AGENCY CMO": ("agcmo", "agency"),
    "NON-AGENCY CMO": ("nagcmo", "ig"),
    "NON-AGENCY CMBS": ("nagcmbs", "ig"),
    "AGENCY CMBS": ("agcmbs", "ig"),
}
# single-row categories live in the IG-grade block
SINGLE_ROWS = {
    "ABS": "abs",
    "CBO/CDO/CLO": "clo",
}


def _num(raw: str) -> float:
    """Parse a STAR cell: '*' (suppressed <5 trades) -> 0.0."""
    if raw is None:
        return 0.0
    s = raw.strip()
    if s == "" or s == "*":
        return 0.0
    v = to_float(s)
    return v if v is not None else 0.0


def parse_star(data: bytes) -> dict[str, float]:
    """Return {series-suffix-key: value} aggregates for one daily STAR file.

    Keys: (section, metric) e.g. ("tba", "par"), ("tba-umbs", "par"),
    ("nagcmo-ig", "par"), ("abs-nonig", "trades"), ...
    par is in $ (000s x 1000).
    """
    rows = read_sheet(data, sheet=1)
    section: str | None = None
    layout: str | None = None
    out: dict[str, float] = {}

    def add(key: str, v: float) -> None:
        out[key] = out.get(key, 0.0) + v

    for r in sorted(rows):
        row = rows[r]
        if not row:
            continue
        # labels live in column B (index 1); column A is empty (merged cells)
        label = html.unescape(row[1].strip()) if len(row) > 1 else ""
        if not label:
            continue
        if label in SECTIONS:
            section, layout = SECTIONS[label]
            continue
        if label in SINGLE_ROWS:
            # single-row IG-block category: 6 data cols starting at col C
            key = SINGLE_ROWS[label]
            vals = [_num(c) for c in row[2:8]]
            if len(vals) < 6:
                continue
            add(f"{key}-ig-trades", vals[0])
            add(f"{key}-ig-par", vals[2] * 1000.0)
            add(f"{key}-nonig-trades", vals[3])
            add(f"{key}-nonig-par", vals[5] * 1000.0)
            add(f"{key}-trades", vals[0] + vals[3])
            add(f"{key}-par", (vals[2] + vals[5]) * 1000.0)
            continue
        if section is None or layout is None:
            continue
        # data row inside a section (data starts at col C, index 2)
        if layout == "agency":
            vals = [_num(c) for c in row[2:14]]
            if len(vals) < 12 or all(v == 0 for v in vals):
                # still record zeros for known subcategory rows so every
                # trading day has a point; skip unknown labels
                known = ("SINGLE FAMILY 15Y", "SINGLE FAMILY 30Y",
                         "ADJUSTABLE/HYBRID", "OTHER", "P&I", "IO/PO")
                if label not in known:
                    continue
                vals = (vals + [0.0] * 12)[:12]
            issuers = ("umbs", "fnma", "fhlmc", "gnma")
            for i, iss in enumerate(issuers):
                tr, _sec, par3 = vals[3 * i], vals[3 * i + 1], vals[3 * i + 2]
                add(f"{section}-trades", tr)
                add(f"{section}-par", par3 * 1000.0)
                if section == "tba":
                    add(f"tba-{iss}-par", par3 * 1000.0)
                    add(f"tba-{iss}-trades", tr)
        elif layout == "ig":
            vals = [_num(c) for c in row[2:8]]
            known = ("P&I", "IO/PO")
            if label not in known:
                continue
            vals = (vals + [0.0] * 6)[:6]
            add(f"{section}-ig-trades", vals[0])
            add(f"{section}-ig-par", vals[2] * 1000.0)
            add(f"{section}-nonig-trades", vals[3])
            add(f"{section}-nonig-par", vals[5] * 1000.0)
            add(f"{section}-trades", vals[0] + vals[3])
            add(f"{section}-par", (vals[2] + vals[5]) * 1000.0)
    return out


async def _fetch_zip(url: str, get_bytes: GetBytes) -> bytes:
    data = await get_bytes(url)
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError(f"not a ZIP (likely an error page): {url}")
    return data


def _iter_star_files(zip_data: bytes):
    """Yield (date, xlsx_bytes) for each STAR file in a monthly ZIP."""
    z = zipfile.ZipFile(io.BytesIO(zip_data))
    for name in z.namelist():
        m = STAR_RE.fullmatch(name.rsplit("/", 1)[-1])
        if not m:
            continue
        ds = m.group(1)
        try:
            day = date(int(ds[0:4]), int(ds[4:6]), int(ds[6:8]))
        except ValueError:
            continue
        yield day, z.read(name)


async def fetch_finra_ids_star(store: Store, get_bytes: GetBytes,
                               today: date | None = None,
                               backfill_months: int = BACKFILL_MONTHS) -> str:
    """Daily job: STAR structured-product activity.

    On an empty store, backfills `backfill_months` of monthly ZIPs (3Y).
    Otherwise re-fetches the current + previous month ZIPs (idempotent).
    """
    today = today or date.today()
    errors: list[str] = []

    async def process_month(y: int, m: int) -> int:
        url = ZIP_URL.format(f"{y}{m:02d}")
        try:
            data = await _fetch_zip(url, get_bytes)
        except Exception as exc:  # noqa: BLE001 — future months 404
            log.debug("ids_star %s skipped: %s", url, exc)
            return 0
        n = 0
        for day, xlsx in _iter_star_files(data):
            try:
                agg = parse_star(xlsx)
            except Exception as exc:  # noqa: BLE001 — one bad day skips
                log.warning("ids_star bad day %s: %s", day, exc)
                continue
            by_series: dict[str, list[tuple[date, float]]] = {}
            for k, v in agg.items():
                by_series.setdefault(f"cycle:star-{k}", []).append((day, v))
            for sid, spts in by_series.items():
                store.upsert_points(sid, spts)
            n += 1
        return n

    # backfill on empty store (older months first)
    if len(store.points("cycle:star-tba-par")) < 20:
        y, m = today.year, today.month
        total = 0
        log.info("finra_ids_star: starting %d-month backfill (store has <20 days)",
                 backfill_months)
        for i in range(backfill_months):
            m -= 1
            if m == 0:
                m, y = 12, y - 1
            n = await process_month(y, m)
            total += n
            if (i + 1) % 6 == 0:
                log.info("finra_ids_star: backfill %d/%d months, %d files so far",
                         i + 1, backfill_months, total)
            await asyncio.sleep(REQUEST_GAP)
        log.info("finra_ids_star backfilled %d daily files", total)
    # always (re)fetch current + previous month (idempotent upserts;
    # catches late postings as FINRA updates the monthly ZIP in place)
    y, m = today.year, today.month
    await process_month(y, m)
    pm, py = (12, y - 1) if m == 1 else (m - 1, y)
    if (py, pm) != (y, m):
        await process_month(py, pm)

    # snapshot doc: latest day summary
    latest: date | None = None
    summary: dict[str, float] = {}
    try:
        pts = store.points("cycle:star-tba-par")
        if pts:
            latest = max(pts)
            for sid in SERIES_IDS:
                p = store.points(f"cycle:{sid}")
                if p:
                    dmax = max(p)
                    if latest is None or dmax > latest:
                        latest = dmax
            for sid in SERIES_IDS:
                p = store.points(f"cycle:{sid}")
                summary[sid] = p.get(latest, 0.0) if (p and latest) else 0.0
    except Exception:  # noqa: BLE001 — doc is best-effort
        pass
    store.put_doc("finra_ids_star", {
        "as_of": latest.isoformat() if latest else None,
        "series": SERIES_IDS,
        "latest": summary,
    }, source=SOURCE)
    if errors:
        log.warning("finra_ids_star errors: %s", "; ".join(errors[:3]))
    return SOURCE
