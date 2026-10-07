"""FINRA Treasury TRACE aggregates — daily + monthly backfill (keyless).

FINRA publishes aggregate Treasury trading activity from TRACE (par in
$billions, trade counts), free on its CDN with no auth:

  Daily:   https://cdn.finra.org/trace/treasury-aggregates/daily/
             ts-daily-aggregates-YYYY-MM-DD.xlsx
           (each trading day, ~8pm ET; history since 2023-02-13)
  Monthly: https://cdn.finra.org/trace/treasury-aggregates/monthly/
             ts-monthly-aggregates-YYYY-MM.xlsx
           (same layout minus the VWAP column; oldest file 2023-02;
           used for history backfill)

Layout (verified live 2026-10-05): row 1 "TRACE Volumes - <Month DD, YYYY>",
row 4 "Category | ATS & Interdealer | Dealer to Customer | Total", row 5
headers [Trades, Par Value] x3 + VWAP; category rows Bills / FRNs /
Nominal Coupons (7 maturity-bucket detail rows, each with on-the-run /
off-the-run sub-rows carrying the bucket's VWAP) / TIPS (3 bucket rows) /
Total. Columns (0-indexed): 1=Trades ATS&ID, 2=Par ATS&ID ($bn),
3=Trades D2C, 4=Par D2C, 5=Trades Total, 6=Par Total, 7=VWAP.

Stored as cycle:<id> ($bn / counts / price):
  categories (Total/Bills/FRNs/Nominal Coupons/TIPS):
    {par,trades} x {total, ats (ATS&Interdealer), d2c (Dealer-to-Customer)}
  coupon maturity buckets (7): trace-ust-coupons-{le2y,2y3y,3y5y,5y7y,
    7y10y,10y20y,gt20y}-{par,trades,par-ats,trades-ats,par-d2c,trades-d2c}
    plus trace-ust-coupons-{bucket}-vwap (on-the-run price, daily files)
  TIPS buckets (3): trace-ust-tips-{le5y,5y10y,gt10y}-{...same 6...}
  market-wide on-the-run / off-the-run par + trades summed across every
  coupon and TIPS maturity bucket.

Month-end convention: calendar month-end dates always carry the MONTHLY
file's monthly totals (the grid treats month-end points as monthly
totals); daily files are never written at month-end dates, so daily pulls
can't clobber them.

Stored as cycle:<id> so the MARKET STRUCTURE tab renders them; the cycle
job skips them (external: true in config).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from collector.fetchers.xlsx import read_sheet, to_float
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

# earliest daily file FINRA publishes
DAILY_START = date(2023, 2, 13)
# earliest monthly file found by probing (ts-monthly-aggregates-2023-02
# exists; 2023-01 does not — probed 2026-10-06)
MONTHLY_START = (2023, 2)

TITLE_RE = re.compile(r"TRACE Volumes - (\w+ \d{1,2}, \d{4})")

# source col-A label -> bucket key (exact labels, verified live 2026-10-05)
COUPON_BUCKETS = {
    "<= 2 years": "le2y",
    "> 2 years and <= 3 years": "2y3y",
    "> 3 years and <= 5 years": "3y5y",
    "> 5 years and <= 7 years": "5y7y",
    "> 7 years and <= 10 years": "7y10y",
    "> 10 years and <= 20 years": "10y20y",
    "> 20 years": "gt20y",
}
TIPS_BUCKETS = {
    "<= 5 years": "le5y",
    "> 5 years and <= 10 years": "5y10y",
    "> 10 years": "gt10y",
}

# 0-indexed value columns: trades-ats | par-ats | trades-d2c | par-d2c |
# trades-total | par-total | vwap(daily files only)
_COLS = {"trades-ats": 1, "par-ats": 2, "trades-d2c": 3, "par-d2c": 4,
         "trades": 5, "par": 6, "vwap": 7}
# store key -> category label in col A; the TOTAL row's keys have no infix
_CATEGORIES = {
    "": "Total",
    "bills": "Bills",
    "frns": "FRNs",
    "coupons": "Nominal Coupons",
    "tips": "TIPS",
}
# keys that must parse or the workbook is rejected (pre-2026-10-06 set);
# everything else (venue splits, buckets, category trades, VWAP) is
# best-effort so a future layout drift can't silently drop the core series.
REQUIRED_KEYS = {
    "trace-ust-par", "trace-ust-trades",
    "trace-ust-bills-par", "trace-ust-frns-par",
    "trace-ust-coupons-par", "trace-ust-tips-par",
    "trace-ust-onrun-par", "trace-ust-offrun-par",
}

ALL_SERIES = set(REQUIRED_KEYS)
for _pfx in list(_CATEGORIES):
    _infix = f"-{_pfx}" if _pfx else ""
    for _k in ("par", "trades", "par-ats", "trades-ats", "par-d2c",
               "trades-d2c"):
        ALL_SERIES.add(f"trace-ust{_infix}-{_k}")
for _b in COUPON_BUCKETS.values():
    for _k in ("par", "trades", "par-ats", "trades-ats", "par-d2c",
               "trades-d2c", "vwap"):
        ALL_SERIES.add(f"trace-ust-coupons-{_b}-{_k}")
for _b in TIPS_BUCKETS.values():
    for _k in ("par", "trades", "par-ats", "trades-ats", "par-d2c",
               "trades-d2c"):
        ALL_SERIES.add(f"trace-ust-tips-{_b}-{_k}")
ALL_SERIES.add("trace-ust-onrun-trades")
ALL_SERIES.add("trace-ust-offrun-trades")


def _is_month_end(d: date) -> bool:
    return d.month != (d + timedelta(days=1)).month


def parse_workbook(data: bytes) -> tuple[date, dict[str, float]]:
    """Return (as-of date, {store-key: value}) from a daily/monthly file.

    Row-ordered scan: category rows store trades/par x venue; coupon/TIPS
    maturity-bucket rows store their own trades/par x venue; each bucket's
    On-the-run sub-row carries the bucket VWAP (daily files only) and feeds
    the market-wide on-the-run / off-the-run sums. Zero/blank cells are
    skipped for optional keys; required keys raise on non-numeric.
    """
    rows = read_sheet(data)
    title = (rows.get(1) or [""])[0]
    m = TITLE_RE.search(title)
    if not m:
        raise ValueError(f"unexpected title row: {title!r}")
    asof = datetime.strptime(m.group(1), "%B %d, %Y").date()
    out: dict[str, float] = {}

    def put(key: str, row: list[str], col: int) -> None:
        raw = row[col] if len(row) > col else ""
        v = to_float(raw)
        if key in REQUIRED_KEYS:
            if v is None:
                raise ValueError(f"non-numeric value for {key!r}: {raw!r}")
            out[key] = v
            return
        # optional key: skip missing/zero (0.0 means "no data" in these
        # tables; monthly files have no VWAP column at all)
        if v:
            out[key] = v

    def store_values(prefix: str, row: list[str]) -> None:
        for k, col in _COLS.items():
            if k == "vwap":
                continue  # buckets handle their own VWAP
            put(f"trace-ust-{prefix}-{k}" if prefix else f"trace-ust-{k}",
                row, col)

    onrun = {"par": 0.0, "trades": 0.0}
    offrun = {"par": 0.0, "trades": 0.0}
    onrun_hits = offrun_hits = 0
    section: str | None = None   # "coupons" | "tips" once inside buckets
    bucket: str | None = None
    seen_cats: set[str] = set()

    for r in sorted(rows):
        row = rows[r]
        if not row:
            continue
        label = row[0].strip()
        if not label:
            continue
        if label == "Total":
            store_values("", row)
            seen_cats.add("")
            section, bucket = None, None
            continue
        if label == "Bills":
            store_values("bills", row)
            seen_cats.add("bills")
            continue
        if label == "FRNs":
            store_values("frns", row)
            seen_cats.add("frns")
            continue
        if label == "Nominal Coupons":
            store_values("coupons", row)
            seen_cats.add("coupons")
            section, bucket = "coupons", None
            continue
        if label == "TIPS":
            store_values("tips", row)
            seen_cats.add("tips")
            section, bucket = "tips", None
            continue
        if section == "coupons":
            b = COUPON_BUCKETS.get(label)
            if b is not None:
                bucket = b
                store_values(f"coupons-{b}", row)
                continue
            if label == "On-the-run" and bucket is not None:
                onrun_hits += 1
                for k, col in (("par", 6), ("trades", 5)):
                    v = to_float(row[col]) if len(row) > col else None
                    if v:
                        onrun[k] += v
                # bucket VWAP lives on the on-the-run row (daily files only)
                put(f"trace-ust-coupons-{bucket}-vwap", row, _COLS["vwap"])
                continue
            if label == "Off-the-run" and bucket is not None:
                offrun_hits += 1
                for k, col in (("par", 6), ("trades", 5)):
                    v = to_float(row[col]) if len(row) > col else None
                    if v:
                        offrun[k] += v
                continue
        elif section == "tips":
            b = TIPS_BUCKETS.get(label)
            if b is not None:
                bucket = b
                store_values(f"tips-{b}", row)
                continue
            if label == "On-the-run" and bucket is not None:
                onrun_hits += 1
                for k, col in (("par", 6), ("trades", 5)):
                    v = to_float(row[col]) if len(row) > col else None
                    if v:
                        onrun[k] += v
                continue
            if label == "Off-the-run" and bucket is not None:
                offrun_hits += 1
                for k, col in (("par", 6), ("trades", 5)):
                    v = to_float(row[col]) if len(row) > col else None
                    if v:
                        offrun[k] += v
                continue
    if onrun_hits == 0:
        raise ValueError("no rows with label 'On-the-run'")
    out["trace-ust-onrun-par"] = onrun["par"]
    out["trace-ust-onrun-trades"] = onrun["trades"]
    if offrun_hits == 0:
        raise ValueError("no rows with label 'Off-the-run'")
    out["trace-ust-offrun-par"] = offrun["par"]
    out["trace-ust-offrun-trades"] = offrun["trades"]
    missing = set(_CATEGORIES) - seen_cats
    if missing:
        raise ValueError(f"category rows missing: {sorted(missing)}")
    return asof, out


async def _fetch_url(url: str, get_bytes: GetBytes) -> bytes:
    data = await get_bytes(url)
    if len(data) < 1000:
        raise ValueError(f"suspiciously small payload ({len(data)}B): {url}")
    return data


def _store_day(store: Store, asof: date, vals: dict[str, float]) -> None:
    """Upsert one file's points. Calendar month-end dates are owned by the
    monthly files (monthly totals); daily files never write them, so the
    grid's month-end = monthly-total convention can't be clobbered."""
    if _is_month_end(asof):
        return
    for key, val in vals.items():
        store.upsert_points(f"cycle:{key}", [(asof, val)])


def _needs_backfill(store: Store) -> bool:
    if len(store.points("cycle:trace-ust-par")) < 20:
        return True
    # venue splits / buckets / VWAP added 2026-10-06: re-parse all history
    # once (idempotent upserts) when they are missing
    return len(store.points("cycle:trace-ust-bills-par-ats")) < 1


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


async def _full_backfill(store: Store, get_bytes: GetBytes,
                         today: date) -> None:
    """Pull every available monthly file (2023-02..latest, probing older)
    and every available daily file (2023-02-13..today). Idempotent
    upserts; ~1s gaps against the CDN. One-time cost on an empty/thin
    store (~17 min for ~950 daily files)."""
    # monthly files, newest first
    y, m = today.year, today.month
    backfilled = 0
    while (y, m) >= MONTHLY_START:
        url = MONTHLY.format(f"{y}-{m:02d}")
        try:
            asof, vals = parse_workbook(await _fetch_url(url, get_bytes))
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            backfilled += 1
        except Exception as exc:  # noqa: BLE001 — tolerate gaps
            log.debug("trace_treasury monthly %s skipped: %s", url, exc)
        await asyncio.sleep(REQUEST_GAP)
        y, m = _prev_month(y, m)
    log.info("trace_treasury backfilled %d monthly files", backfilled)
    # probe for monthly files older than MONTHLY_START (future-proofs
    # against FINRA extending history); stop after 2 consecutive misses
    y, m = _prev_month(*MONTHLY_START)
    misses = 0
    for _ in range(24):
        url = MONTHLY.format(f"{y}-{m:02d}")
        try:
            asof, vals = parse_workbook(await _fetch_url(url, get_bytes))
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            log.info("trace_treasury found older monthly file %s-%02d", y, m)
            misses = 0
        except Exception as exc:  # noqa: BLE001 — 403 = not published
            log.debug("trace_treasury monthly probe %s-%02d: %s", y, m, exc)
            misses += 1
            if misses >= 2:
                break
        await asyncio.sleep(REQUEST_GAP)
        y, m = _prev_month(y, m)
    # daily files since 2023-02-13; weekends/holidays have no file
    # (skip fast). Month-end dates are skipped: the monthly file owns them.
    day = DAILY_START
    n = skipped = 0
    while day <= today:
        url = DAILY.format(day.isoformat())
        try:
            asof, vals = parse_workbook(await _fetch_url(url, get_bytes))
            _store_day(store, asof, vals)
            n += 1
        except Exception:  # noqa: BLE001 — weekends/holidays have no file
            skipped += 1
        await asyncio.sleep(REQUEST_GAP)
        day += timedelta(days=1)
    log.info("trace_treasury backfilled %d daily files (%d skipped)",
             n, skipped)


async def fetch_trace_treasury(store: Store, get_bytes: GetBytes,
                               today: date | None = None) -> str:
    """Daily job: latest daily file (probe back up to 5 days for weekends/
    holidays); on a thin store, full backfill via all monthly + daily
    files since 2023-02."""
    today = today or date.today()
    errors: list[str] = []
    got_daily = False
    daily_asof: date | None = None
    for back in range(6):
        day = today - timedelta(days=back)
        url = DAILY.format(day.isoformat())
        try:
            asof, vals = parse_workbook(await _fetch_url(url, get_bytes))
            _store_day(store, asof, vals)
            daily_asof = asof
            got_daily = True
            break
        except Exception as exc:  # noqa: BLE001 — weekends/holidays have no file
            errors.append(f"{day}: {exc}")
            await asyncio.sleep(REQUEST_GAP)  # polite: the CDN 403s rapid bursts
    if not got_daily:
        raise RuntimeError("no daily TRACE Treasury file in last 6 days: "
                           + "; ".join(errors[:3]))
    if _needs_backfill(store):
        log.info("trace_treasury: starting full history backfill")
        await _full_backfill(store, get_bytes, today)
    store.put_doc("trace_treasury", {"as_of": (daily_asof or asof).isoformat(),
                                     "series": sorted(ALL_SERIES)},
                  source=SOURCE)
    return SOURCE
