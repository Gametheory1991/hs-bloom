"""FINRA Treasury TRACE aggregates — daily + monthly backfill (keyless).

FINRA publishes aggregate Treasury trading activity from TRACE (par in
$billions, trade counts), free on its CDN with no auth:

  Daily:   https://cdn.finra.org/trace/treasury-aggregates/daily/
             ts-daily-aggregates-YYYY-MM-DD.xlsx
           (each trading day, ~8pm ET; history since 2023-02-13)
  Monthly: https://cdn.finra.org/trace/treasury-aggregates/monthly/
             ts-monthly-aggregates-YYYY-MM.xlsx
           (same layout minus VWAP; used for history backfill)

Layout (verified live 2026-10-03): row 1 "TRACE Volumes - <Month DD, YYYY>",
row 5 headers [Trades, Par Value] x3 (ATS&Interdealer, Dealer-to-Customer,
Total) + VWAP; category rows: Bills / FRNs / Nominal Coupons (bucket +
on/off-the-run detail) / TIPS / Total. Columns: A=Category, F=Total trades,
G=Total par ($bn).

Stored as cycle:<id> ($bn / counts): totals, bills, FRNs, coupons, TIPS
plus market-wide on-the-run / off-the-run par summed across every coupon
and TIPS maturity bucket.

Stored as cycle:<id> ($bn / counts) so the MARKET STRUCTURE tab renders
them; the cycle job skips them (external: true in config).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from collector.fetchers.xlsx import find_row, read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

DAILY = ("https://cdn.finra.org/trace/treasury-aggregates/daily/"
         "ts-daily-aggregates-{}.xlsx")
MONTHLY = ("https://cdn.finra.org/trace/treasury-aggregates/monthly/"
           "ts-monthly-aggregates-{}.xlsx")
SOURCE = "finra-trace-treasury"

# seconds between HTTP requests; the CDNs 403 rapid bursts.
REQUEST_GAP = 1.0

TITLE_RE = re.compile(r"TRACE Volumes - (\w+ \d{1,2}, \d{4})")

# store key -> (category label, column, unit-note); single-row categories
SERIES = {
    "trace-ust-par": ("Total", 6, "$bn"),
    "trace-ust-trades": ("Total", 5, "count"),
    "trace-ust-bills-par": ("Bills", 6, "$bn"),
    "trace-ust-frns-par": ("FRNs", 6, "$bn"),
    "trace-ust-coupons-par": ("Nominal Coupons", 6, "$bn"),
    "trace-ust-tips-par": ("TIPS", 6, "$bn"),
}
# store key -> (repeated row label, column, unit-note); values are summed
# over every row with that exact label (coupons + TIPS bucket detail).
SUM_SERIES = {
    "trace-ust-onrun-par": ("On-the-run", 6, "$bn"),
    "trace-ust-offrun-par": ("Off-the-run", 6, "$bn"),
}
ALL_SERIES = {**SERIES, **SUM_SERIES}


def parse_workbook(data: bytes) -> tuple[date, dict[str, float]]:
    """Return (as-of date, {store-key: value}) from a daily/monthly file."""
    rows = read_sheet(data)
    title = (rows.get(1) or [""])[0]
    m = TITLE_RE.search(title)
    if not m:
        raise ValueError(f"unexpected title row: {title!r}")
    asof = datetime.strptime(m.group(1), "%B %d, %Y").date()
    out: dict[str, float] = {}
    for key, (label, col, _unit) in SERIES.items():
        row = find_row(rows, label)
        if row is None or len(row) <= col:
            raise ValueError(f"category row {label!r} missing")
        val = to_float(row[col])
        if val is None:
            raise ValueError(f"non-numeric value for {label!r}: {row[col]!r}")
        out[key] = val
    # repeated bucket-detail rows ("On-the-run"/"Off-the-run" under every
    # coupon and TIPS maturity bucket) are summed to a market-wide total.
    for key, (label, col, _unit) in SUM_SERIES.items():
        total = 0.0
        hits = 0
        for r in sorted(rows):
            row = rows[r]
            if len(row) > col and row[0] == label:  # label lives in col A
                val = to_float(row[col])
                if val is None:
                    raise ValueError(f"non-numeric value for {label!r}: {row[col]!r}")
                total += val
                hits += 1
        if hits == 0:
            raise ValueError(f"no rows with label {label!r}")
        out[key] = total
    return asof, out


async def _fetch_url(url: str, get_bytes: GetBytes) -> bytes:
    data = await get_bytes(url)
    if len(data) < 1000:
        raise ValueError(f"suspiciously small payload ({len(data)}B): {url}")
    return data


async def fetch_trace_treasury(store: Store, get_bytes: GetBytes,
                               today: date | None = None) -> str:
    """Daily job: latest daily file (probe back up to 5 days for weekends/
    holidays); on an empty store, backfill 24 months via monthly files."""
    today = today or date.today()
    errors: list[str] = []
    got_daily = False
    daily_asof: date | None = None
    for back in range(6):
        day = today - timedelta(days=back)
        url = DAILY.format(day.isoformat())
        try:
            asof, vals = parse_workbook(await _fetch_url(url, get_bytes))
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            daily_asof = asof
            got_daily = True
            break
        except Exception as exc:  # noqa: BLE001 — weekends/holidays have no file
            errors.append(f"{day}: {exc}")
            await asyncio.sleep(REQUEST_GAP)  # polite: the CDN 403s rapid bursts
    if not got_daily:
        raise RuntimeError("no daily TRACE Treasury file in last 6 days: "
                           + "; ".join(errors[:3]))
    # monthly backfill only when we have almost no history
    if len(store.points("cycle:trace-ust-par")) < 20:
        backfilled = 0
        y, m = today.year, today.month
        for _ in range(24):
            m -= 1
            if m == 0:
                m, y = 12, y - 1
            url = MONTHLY.format(f"{y}-{m:02d}")
            try:
                asof, vals = parse_workbook(await _fetch_url(url, get_bytes))
            except Exception as exc:  # noqa: BLE001 — old months may 404
                log.debug("trace_treasury monthly %s skipped: %s", url, exc)
                await asyncio.sleep(REQUEST_GAP)
                continue
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            backfilled += 1
            await asyncio.sleep(REQUEST_GAP)
        log.info("trace_treasury backfilled %d months", backfilled)
    store.put_doc("trace_treasury", {"as_of": (daily_asof or asof).isoformat(),
                                     "series": sorted(ALL_SERIES)},
                  source=SOURCE)
    return SOURCE
