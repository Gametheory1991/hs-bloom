"""FINRA-ICE Data Services Structured Trading Activity Reports (STAR) — daily.

Harry found: https://www.finra.org/finra-data/browse-catalog/structured-product-activity-reports-and-tables/historic-reports
This is the PUBLIC equivalent of the ICE Vantage structured aggregates
(login-walled) — daily structured-product trading activity, free on the
FINRA CDN with no auth:

  ZIP: https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-YYYYMM.zip
  Each ZIP holds one XLSX per trading day:
    FINRA_IDS_STAR-YYYYMMDD.xlsx    (Structured Trading Activity Reports)
    FINRA_IDS_PXTABLES-YYYYMMDD.xlsx (Pricing Tables — parsed by
                                      collector.fetchers.finra_ids_px)

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

Stored (daily) — every published cell is a series:
  cycle:star-{section}-{par,trades,secids}     section totals (tba/spec/agcmo/
      nagcmo/nagcmbs/agcmbs/abs/clo/oth); par in $, secids = unique SEC IDs
  cycle:star-tba-{umbs,fnma,fhlmc,gnma,other-agency}-{par,trades,secids}
  cycle:star-{tba,spec}-{15y,30y,adj,other}-{par,trades}
  cycle:star-{agcmo,nagcmo,nagcmbs,agcmbs}-{pi,iopo}[-{ig,nonig}]-{par,trades,secids}
  cycle:star-{abs,clo}-{ig,nonig}-{par,secids}
plus a "finra_ids_star" snapshot doc with the latest day's summary.
Pricing tables (PXTABLES: avg/wtd-avg/quartile prices by coupon/vintage)
are parsed by finra_ids_px.py.

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
from collector.fetchers import finra_ids_px
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

ZIP_URL = "https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-{}.zip"
STAR_RE = re.compile(r"FINRA_IDS_STAR-(\d{8})\.xlsx")
SOURCE = "finra-ids-star"
REQUEST_GAP = 1.0
BACKFILL_MONTHS = 36

SERIES_IDS = [
    "star-tba-par", "star-tba-trades", "star-tba-secids",
    "star-tba-umbs-par", "star-tba-umbs-trades", "star-tba-umbs-secids",
    "star-tba-fnma-par", "star-tba-fnma-trades", "star-tba-fnma-secids",
    "star-tba-fhlmc-par", "star-tba-fhlmc-trades", "star-tba-fhlmc-secids",
    "star-tba-gnma-par", "star-tba-gnma-trades", "star-tba-gnma-secids",
    "star-tba-other-agency-par", "star-tba-other-agency-trades",
    "star-tba-other-agency-secids",
    "star-tba-15y-par", "star-tba-15y-trades",
    "star-tba-30y-par", "star-tba-30y-trades",
    "star-tba-other-par", "star-tba-other-trades",
    "star-spec-par", "star-spec-trades", "star-spec-secids",
    "star-spec-15y-par", "star-spec-15y-trades",
    "star-spec-30y-par", "star-spec-30y-trades",
    "star-spec-adj-par", "star-spec-adj-trades",
    "star-spec-other-par", "star-spec-other-trades",
    "star-agcmo-par", "star-agcmo-trades", "star-agcmo-secids",
    "star-agcmo-pi-par", "star-agcmo-pi-trades",
    "star-agcmo-iopo-par", "star-agcmo-iopo-trades",
    "star-nagcmo-ig-par", "star-nagcmo-nonig-par",
    "star-nagcmo-par", "star-nagcmo-trades", "star-nagcmo-secids",
    "star-nagcmo-pi-ig-par", "star-nagcmo-pi-nonig-par",
    "star-nagcmo-pi-par", "star-nagcmo-pi-trades",
    "star-nagcmo-iopo-ig-par", "star-nagcmo-iopo-nonig-par",
    "star-nagcmo-iopo-par", "star-nagcmo-iopo-trades",
    "star-nagcmbs-ig-par", "star-nagcmbs-nonig-par",
    "star-nagcmbs-par", "star-nagcmbs-trades", "star-nagcmbs-secids",
    "star-nagcmbs-pi-ig-par", "star-nagcmbs-pi-nonig-par",
    "star-nagcmbs-pi-par", "star-nagcmbs-pi-trades",
    "star-nagcmbs-iopo-ig-par", "star-nagcmbs-iopo-nonig-par",
    "star-nagcmbs-iopo-par", "star-nagcmbs-iopo-trades",
    "star-agcmbs-par", "star-agcmbs-trades", "star-agcmbs-secids",
    "star-agcmbs-pi-ig-par", "star-agcmbs-pi-nonig-par",
    "star-agcmbs-pi-par", "star-agcmbs-pi-trades",
    "star-agcmbs-iopo-ig-par", "star-agcmbs-iopo-nonig-par",
    "star-agcmbs-iopo-par", "star-agcmbs-iopo-trades",
    "star-abs-ig-par", "star-abs-nonig-par", "star-abs-par", "star-abs-trades",
    "star-abs-ig-secids", "star-abs-nonig-secids", "star-abs-secids",
    "star-clo-ig-par", "star-clo-nonig-par", "star-clo-par", "star-clo-trades",
    "star-clo-ig-secids", "star-clo-nonig-secids", "star-clo-secids",
    "star-oth-ig-par", "star-oth-nonig-par", "star-oth-par", "star-oth-trades",
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
    "OTHER": "oth",
}
# sub-breakdown label -> key suffix (for granular series)
SUB_LABELS = {
    "SINGLE FAMILY 15Y": "15y",
    "SINGLE FAMILY 30Y": "30y",
    "ADJUSTABLE/HYBRID": "adj",
    "OTHER": "other",
    "P&I": "pi",
    "IO/PO": "iopo",
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

    Granularity (2026-10-06): captures every published cell —
    trade count, unique SEC IDs, and $ trades for each issuer/grade AND each
    sub-breakdown (15Y/30Y/adj-hybrid/other, P&I/IO/PO), plus the OTHER AGENCY
    issuer column and the OTHER single-row category.
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
        if label in SINGLE_ROWS and layout == "ig":
            # single-row IG-block category: 6 data cols starting at col C.
            # ("OTHER" is also a TBA/Specified sub-breakdown — only treat it
            # as a single row inside the IG-grade block.)
            key = SINGLE_ROWS[label]
            vals = [_num(c) for c in row[2:8]]
            if len(vals) < 6:
                continue
            add(f"{key}-ig-trades", vals[0])
            add(f"{key}-ig-secids", vals[1])
            add(f"{key}-ig-par", vals[2] * 1000.0)
            add(f"{key}-nonig-trades", vals[3])
            add(f"{key}-nonig-secids", vals[4])
            add(f"{key}-nonig-par", vals[5] * 1000.0)
            add(f"{key}-trades", vals[0] + vals[3])
            add(f"{key}-secids", vals[1] + vals[4])
            add(f"{key}-par", (vals[2] + vals[5]) * 1000.0)
            continue
        if section is None or layout is None:
            continue
        sub = SUB_LABELS.get(label)
        # data row inside a section (data starts at col C, index 2)
        if layout == "agency":
            # 5 issuers x (trades, secids, $000s) = 15 cols
            vals = [_num(c) for c in row[2:17]]
            known = ("SINGLE FAMILY 15Y", "SINGLE FAMILY 30Y",
                     "ADJUSTABLE/HYBRID", "OTHER", "P&I", "IO/PO")
            if label not in known:
                continue
            vals = (vals + [0.0] * 15)[:15]
            issuers = ("umbs", "fnma", "fhlmc", "gnma", "other-agency")
            for i, iss in enumerate(issuers):
                tr, sec, par3 = vals[3 * i], vals[3 * i + 1], vals[3 * i + 2]
                add(f"{section}-trades", tr)
                add(f"{section}-secids", sec)
                add(f"{section}-par", par3 * 1000.0)
                if sub:
                    add(f"{section}-{sub}-trades", tr)
                    add(f"{section}-{sub}-secids", sec)
                    add(f"{section}-{sub}-par", par3 * 1000.0)
                if section == "tba":
                    add(f"tba-{iss}-par", par3 * 1000.0)
                    add(f"tba-{iss}-trades", tr)
                    add(f"tba-{iss}-secids", sec)
        elif layout == "ig":
            vals = [_num(c) for c in row[2:8]]
            if label not in ("P&I", "IO/PO"):
                continue
            vals = (vals + [0.0] * 6)[:6]
            for gi, grade in enumerate(("ig", "nonig")):
                tr, sec, par3 = vals[3 * gi], vals[3 * gi + 1], vals[3 * gi + 2]
                add(f"{section}-{grade}-trades", tr)
                add(f"{section}-{grade}-secids", sec)
                add(f"{section}-{grade}-par", par3 * 1000.0)
                add(f"{section}-trades", tr)
                add(f"{section}-secids", sec)
                add(f"{section}-par", par3 * 1000.0)
                if sub:
                    add(f"{section}-{sub}-{grade}-trades", tr)
                    add(f"{section}-{sub}-{grade}-secids", sec)
                    add(f"{section}-{sub}-{grade}-par", par3 * 1000.0)
                    add(f"{section}-{sub}-trades", tr)
                    add(f"{section}-{sub}-secids", sec)
                    add(f"{section}-{sub}-par", par3 * 1000.0)
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
    """Daily job: STAR structured-product activity + PXTABLES pricing.

    On an empty store, backfills `backfill_months` of monthly ZIPs (3Y)
    for STAR; PXTABLES (new dataset) only backfills the most recent
    PX_BACKFILL_MONTHS months. Otherwise re-fetches the current +
    previous month ZIPs (idempotent). The monthly ZIP is downloaded once
    and both the STAR and PXTABLES daily files are parsed from it.
    """
    today = today or date.today()
    errors: list[str] = []

    async def process_month(y: int, m: int, parse_px: bool) -> int:
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
        if parse_px:
            for day, xlsx in finra_ids_px.iter_pxtables_files(data):
                try:
                    finra_ids_px.store_pxtables(store, day, xlsx)
                except Exception as exc:  # noqa: BLE001 — one bad day skips
                    log.warning("ids_px bad day %s: %s", day, exc)
                    continue
        return n

    def prev_month(y: int, m: int) -> tuple[int, int]:
        return (12, y - 1) if m == 1 else (m - 1, y)

    star_backfill = len(store.points("cycle:star-tba-par")) < 20
    px_backfill = finra_ids_px.needs_backfill(store)
    # (year, month, parse_pxtables)
    plan: list[tuple[int, int, bool]] = []
    # backfill on empty store (previous month first, then older)
    if star_backfill:
        y, m = today.year, today.month
        log.info("finra_ids_star: starting %d-month backfill (store has <20 days)",
                 backfill_months)
        for i in range(backfill_months):
            pm_, py_ = prev_month(y, m)
            y, m = py_, pm_
            plan.append((y, m, i < finra_ids_px.PX_BACKFILL_MONTHS))
    # always (re)fetch current + previous month (idempotent upserts;
    # catches late postings as FINRA updates the monthly ZIP in place)
    y, m = today.year, today.month
    plan.append((y, m, True))
    pm, py = prev_month(y, m)
    if (py, pm) != (y, m):
        plan.append((py, pm, True))
    if px_backfill and not star_backfill:
        # STAR already populated but PXTABLES is new: one extra older
        # month so PX gets ~3 months of history from the daily job
        em, ey = prev_month(py, pm)
        plan.insert(0, (ey, em, True))

    total = 0
    for i, (yy, mm, ppx) in enumerate(plan):
        n = await process_month(yy, mm, ppx)
        total += n
        if star_backfill and (i + 1) % 6 == 0:
            log.info("finra_ids_star: backfill %d/%d months, %d files so far",
                     i + 1, backfill_months, total)
        await asyncio.sleep(REQUEST_GAP)
    if star_backfill:
        log.info("finra_ids_star backfilled %d daily files", total)

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
    # PXTABLES snapshot doc: latest day summary (series are dynamic, so
    # the doc carries counts + a few sentinel values, not the full list)
    px_latest: date | None = None
    px_sheets = sorted(finra_ids_px.SHEET_KEYS.values())
    try:
        pts = store.points(finra_ids_px.SENTINEL)
        if pts:
            px_latest = max(pts)
    except Exception:  # noqa: BLE001 — doc is best-effort
        pass
    store.put_doc("finra_ids_px", {
        "as_of": px_latest.isoformat() if px_latest else None,
        "sheets": px_sheets,
        "series_prefix": "cycle:starpx-",
        "note": ("per-sheet pricing metrics; weekly CMBS sheet dated at "
                 "week end"),
    }, source=finra_ids_px.SOURCE)
    if errors:
        log.warning("finra_ids_star errors: %s", "; ".join(errors[:3]))
    return SOURCE
