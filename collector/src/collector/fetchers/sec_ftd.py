"""SEC CNS fails-to-deliver data (twice-monthly batch; deep backfill to 2004).

Source index (verified live 2026-10-08):
  https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data
Files:
  https://www.sec.gov/files/data/fails-deliver-data/cnsfails<YYYYMM><a|b>.zip
(a = 1st half of month, b = 2nd half). Each zip holds one pipe-delimited
text file:
  SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE
with a two-line trailer ("Trailer record count ...") after the data rows.

Two market-wide series are aggregated per settlement date and upserted as
daily points:
  cycle:ftd-dollar-volume  — sum(quantity x price) per settlement date ($)
  cycle:ftd-share-count    — sum(quantity) per settlement date (shares)

Incremental: completed YYYYMMa/b keys are tracked in the `sec_ftd_files`
store doc; never re-downloaded. On first run the job walks back from 2004
to the present (~500 files), oldest first, with a politeness delay between
downloads and 404 tolerance for months that never published.

Publication timing (per the SEC page): the 1st-half file for month M
appears around the end of M; the 2nd-half file around the 15th of M+1.

SEC fair-access: declared-contact UA from cfg.sec_data (SEC 403s generic
UAs). Downloads are small (~1-4 MB zips) so the injected get_bytes is fine.
"""
from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from datetime import date

from collector.config import SecDataCfg
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

BASE_URL = "https://www.sec.gov/files/data/fails-deliver-data/"
FILES_DOC = "sec_ftd_files"
SOURCE = "sec-ftd"

SERIES_DOLLAR = "cycle:ftd-dollar-volume"
SERIES_SHARES = "cycle:ftd-share-count"

START_YEAR, START_MONTH = 2004, 1
POLITENESS_S = 1.2  # SEC fair-access: stay well under 1 req/2s guidance
# Recent-window length (in expected periods) for which a 404 is treated as
# "not published yet" and retried next run. Older 404s are treated as
# permanently missing so they are not retried forever.
RETRY_404_WINDOW = 4


def file_key(year: int, month: int, half: str) -> str:
    """e.g. 'cnsfails202608b' (half 'a' = 1st half, 'b' = 2nd half)."""
    return f"cnsfails{year:04d}{month:02d}{half}"


def file_url(key: str) -> str:
    return f"{BASE_URL}{key}.zip"


def periods_expected(today: date | None = None) -> list[tuple[int, int, str]]:
    """All (year, month, half) files that should exist by `today`.

    1st-half of M is published ~end of M -> expected once we are past M.
    2nd-half of M is published ~15th of M+1 -> expected once today >= that.
    """
    today = today or date.today()
    out: list[tuple[int, int, str]] = []
    y, m = START_YEAR, START_MONTH
    while (y, m) <= (today.year, today.month):
        # 1st half: available once month M has ended (i.e. we are past M).
        if (y, m) < (today.year, today.month):
            out.append((y, m, "a"))
            # 2nd half: available around the 15th of the following month.
            ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
            if date(ny, nm, 15) <= today:
                out.append((y, m, "b"))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def parse_ftd_text(text: str) -> dict[date, list[float]]:
    """Aggregate one half-month file: settlement date -> [dollar_vol, shares].

    Field positions: 0 = settlement date (YYYYMMDD), 3 = quantity (fails),
    last = price. DESCRIPTION can itself contain a pipe, so price is read
    from the LAST field, not index 5. Header and trailer lines are skipped
    (they do not start with an 8-digit date).
    """
    agg: dict[date, list[float]] = {}
    skipped = 0
    no_price = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        sdate = parts[0].strip()
        if len(sdate) != 8 or not sdate.isdigit():
            continue  # header / trailer
        try:
            dt = date(int(sdate[:4]), int(sdate[4:6]), int(sdate[6:8]))
            qty = int(parts[3].strip().replace(",", ""))
        except (ValueError, IndexError):
            skipped += 1
            continue
        try:
            price = float(parts[-1].strip().replace(",", ""))
        except (ValueError, IndexError):
            price = None  # '.' = no price (warrants/foreign ords): count shares anyway
            no_price += 1
        cell = agg.setdefault(dt, [0.0, 0.0])
        if price is not None:
            cell[0] += qty * price
        cell[1] += qty
    if skipped:
        log.warning("sec-ftd: skipped %d malformed data rows", skipped)
    if no_price:
        log.info("sec-ftd: %d rows had no price (shares counted, $ skipped)", no_price)
    return agg


def read_ftd_zip(data: bytes) -> str:
    """Extract the single pipe-delimited text file from an FTD zip."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
        if not names:
            raise ValueError(f"ftd zip has no .txt member: {zf.namelist()[:5]}")
        return zf.read(names[0]).decode("utf-8", errors="replace")


def _recent_cutoff(expected: list[tuple[int, int, str]]) -> set[tuple[int, int, str]]:
    """The most recent expected periods: still inside the publication window."""
    return set(expected[-RETRY_404_WINDOW:]) if expected else set()


async def fetch_sec_ftd(cfg: SecDataCfg, store: Store, get_bytes: GetBytes,
                        today: date | None = None,
                        delay_s: float = POLITENESS_S) -> str:
    """Twice-monthly poll: download any published-but-unfetched half-month
    files (deep backfill to 2004 on first run), aggregate the two market-wide
    series per settlement date, and upsert idempotently."""
    today = today or date.today()
    headers = {"User-Agent": cfg.user_agent}

    doc = store.doc(FILES_DOC)
    done: set[str] = set((doc.payload.get("files") or []) if doc else [])
    # 404s older than the publication window: recorded once, never retried.
    never_published: set[str] = set((doc.payload.get("never_published") or [])
                                    if doc else [])

    expected = periods_expected(today)
    recent = _recent_cutoff(expected)
    keys = {file_key(y, m, h): (y, m, h) for y, m, h in expected}
    pending = [k for k in (file_key(*p) for p in expected)
               if k not in done and k not in never_published]

    if not pending:
        return "sec-ftd: up-to-date (all published files fetched)"

    dollar: dict[date, float] = {}
    shares: dict[date, float] = {}
    fetched = 0
    first = True
    for key in pending:
        y, m, h = keys[key]
        url = file_url(key)
        try:
            if not first and delay_s > 0:
                await asyncio.sleep(delay_s)
            first = False
            data = await get_bytes(url, headers=headers)
        except RuntimeError as exc:
            if "404" in str(exc):
                if (y, m, h) in recent:
                    # Publication lag: retry on the next run (stays out of done).
                    log.info("sec-ftd: %s 404 (recent window, retry next run)", key)
                else:
                    log.info("sec-ftd: %s 404 (old period, marking never-published)", key)
                    never_published.add(key)
                    done.add(key)
                continue
            log.warning("sec-ftd: %s fetch failed: %s", key, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
            log.warning("sec-ftd: %s fetch failed: %s", key, exc)
            continue
        try:
            agg = parse_ftd_text(read_ftd_zip(data))
        except Exception as exc:  # noqa: BLE001
            log.warning("sec-ftd: %s parse failed: %s", key, exc)
            continue
        for dt, (d, s) in agg.items():
            dollar[dt] = dollar.get(dt, 0.0) + d
            shares[dt] = shares.get(dt, 0.0) + s
        done.add(key)
        fetched += 1
        # checkpoint after every file: a killed run resumes where it stopped.
        store.put_doc(FILES_DOC, {
            "files": sorted(done),
            "never_published": sorted(never_published),
            "fetched_this_run": fetched,
            "last_key": key,
        }, SOURCE)

    if dollar:
        store.upsert_points_batch(
            [(SERIES_DOLLAR, dt, v) for dt, v in sorted(dollar.items())]
        )
        store.upsert_points_batch(
            [(SERIES_SHARES, dt, v) for dt, v in sorted(shares.items())]
        )

    store.put_doc(FILES_DOC, {
        "files": sorted(done),
        "never_published": sorted(never_published),
        "fetched_this_run": fetched,
        "points": len(dollar),
    }, SOURCE)

    return (f"sec-ftd: fetched {fetched} files ({len(pending) - fetched} "
            f"skipped/failed), upserted {len(dollar)} settlement dates")
