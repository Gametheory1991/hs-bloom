"""FINRA equity short interest — twice-monthly consolidated file (keyless).

FINRA publishes short interest for all exchange-listed stocks, settled on
the 15th (or the preceding Friday when the 15th is a weekend) and the last
business day of each month, ~8 business days later:

  https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv

Pipe-delimited, quoted: settlement id | symbol | name | exchange |
market class | current short | previous short | split flag | avg daily
volume | days to cover | revision | change % | change | settlement date.
Verified live 2026-10-06: 22,596 rows (2026-09-15); files available back
to at least 2020-01-15 (2020 files ~14.7k rows).

Stored:
  cycle:finra-short-total  sum of current short (shares) per settlement
  cycle:short:<TICKER>     biweekly short-interest level, 6 hyperscalers
  short_interest table     FULL per-ticker universe per settlement
                           (settlement_date, symbol) keyed: current short,
                           previous short, change, change %, ADV,
                           days-to-cover, name, exchange, market class,
                           split flag, revision.
Backfill: every settlement from 2020-01-15 to the latest published file,
idempotent and resumable — progress is the settlement dates already in
the table, so an interrupted run continues where it left off.
~160 settlements x ~20k tickers ~= 3M rows (under the 5M full-universe
budget, so no top-N truncation).
"""
from __future__ import annotations

import calendar
import csv
import io
import logging
import asyncio
from datetime import date, timedelta

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{}.csv"
SOURCE = "finra-short-interest"

# seconds between HTTP requests; the CDNs 403 rapid bursts.
REQUEST_GAP = 1.0

# Full-universe backfill starts here (files verified back to 2020-01-15).
BACKFILL_SINCE = date(2020, 1, 15)

TICKERS = ("MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "META")


def _settlement_candidates(today: date) -> list[date]:
    """Recent settlement dates (15th / month-end), newest first."""
    out: list[date] = []
    y, m = today.year, today.month
    for _ in range(4):
        out.extend(_month_settlements(y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    out = [d for d in out if d <= today]
    out.sort(reverse=True)
    return out


def _month_settlements(y: int, m: int) -> list[date]:
    """The two settlement dates for a calendar month: the 15th (preceding
    Friday when the 15th is a weekend — FINRA publishes e.g. shrt20200814
    for 2020-08-15) and the last business day of the month."""
    d15 = date(y, m, 15)
    while d15.weekday() > 4:
        d15 -= timedelta(days=1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    while last.weekday() > 4:
        last -= timedelta(days=1)
    return [d15, last]


def settlement_dates(start: date, end: date) -> list[date]:
    """All settlement dates in [start, end], oldest first."""
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.extend(d for d in _month_settlements(y, m) if start <= d <= end)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return sorted(out)


def parse_short_rows(text: str) -> tuple[date, float, list[dict]]:
    """Parse the full universe: (settlement date, total current short,
    [row dicts]) — every row, every field. Raises on a bad header or an
    empty file."""
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader, None)
    if not header or "currentShortPositionQuantity" not in "|".join(header):
        raise ValueError("unexpected short-interest header")
    rows: list[dict] = []
    total = 0.0
    asof: date | None = None
    for row in reader:
        if len(row) < 14:
            continue
        try:
            short = float(row[5] or 0)
            sdate = date.fromisoformat(row[13].strip())
            rows.append({
                "symbol": row[1].strip().upper(),
                "name": row[2].strip() or None,
                "exchange": row[3].strip() or None,
                "market_class": row[4].strip() or None,
                "short": short,
                "prev": float(row[6] or 0),
                "split_flag": row[7].strip() or None,
                "adv": float(row[8] or 0),
                "dtc": float(row[9].strip()) if row[9].strip() else None,
                "revision": row[10].strip() or None,
                "chg_pct": float(row[11].strip()) if row[11].strip() else None,
                "chg_nom": float(row[12].strip()) if row[12].strip() else None,
            })
        except (ValueError, IndexError):
            continue
        asof = sdate
        total += short
    if asof is None:
        raise ValueError("no data rows parsed")
    return asof, total, rows


def parse_short(text: str) -> tuple[date, dict[str, dict]]:
    """Legacy shape: (settlement date, {_total, _asof, <6 watchlist tickers>}).

    Kept for the batch-6 tests and the hyperscaler watchlist; the job
    stores the full universe via parse_short_rows.
    """
    asof, total, rows = parse_short_rows(text)
    tickers = {}
    for r in rows:
        if r["symbol"] in TICKERS:
            tickers[r["symbol"]] = {
                "short": r["short"], "prev": r["prev"], "adv": r["adv"],
                "dtc": r["dtc"], "chg_pct": r["chg_pct"],
                "chg_nom": r["chg_nom"], "name": r["name"],
            }
    return asof, {"_total": total, "_asof": asof, **tickers}


def _store_settlement(store: Store, asof: date, total: float,
                      rows: list[dict]) -> None:
    """Write one settlement: full-universe table + aggregate + watchlist."""
    store.upsert_short_interest([(
        asof.isoformat(), r["symbol"], r["name"], r["exchange"],
        r["market_class"], r["short"], r["prev"], r["chg_nom"],
        r["chg_pct"], r["adv"], r["dtc"], r["split_flag"], r["revision"],
    ) for r in rows])
    store.upsert_points("cycle:finra-short-total", [(asof, total)])
    by_sym = {r["symbol"]: r for r in rows}
    for sym in TICKERS:
        if sym in by_sym:
            store.upsert_points(f"cycle:short-{sym}",
                                [(asof, by_sym[sym]["short"])])


async def _fetch_settlement(get_text: GetText, day: date) -> tuple[date, float, list[dict]]:
    text = await get_text(URL.format(day.strftime("%Y%m%d")))
    if len(text) < 10000:
        raise ValueError(f"suspiciously small ({len(text)}B)")
    return parse_short_rows(text)


async def _fetch_with_fallback(get_text: GetText, day: date,
                               back_days: int = 5) -> tuple[date, float, list[dict]] | None:
    """Try `day`, then preceding business days. When the settlement date is
    a market holiday FINRA dates the file on the preceding business day
    (e.g. shrt20210212 for Presidents' Day 2021-02-15, shrt20220414 for
    Good Friday 2022-04-15). The stored settlement date always comes from
    the file's own settlementDate column, so the fallback never mislabels.
    back_days=5 can't cross into a neighboring settlement (~15d apart)."""
    d = day
    for _ in range(back_days):
        try:
            return await _fetch_settlement(get_text, d)
        except Exception as exc:  # noqa: BLE001 — 404 = not this date
            log.debug("finra_short %s: %s", d, exc)
            await asyncio.sleep(REQUEST_GAP)
        d -= timedelta(days=1)
        while d.weekday() > 4:
            d -= timedelta(days=1)
    return None


async def fetch_finra_short(store: Store, get_text: GetText,
                            today: date | None = None) -> str:
    """Weekly poll: latest settlement file, then idempotent full-universe
    backfill from 2020-01-15. Progress = settlements already stored, so an
    interrupted run resumes where it left off."""
    today = today or date.today()

    # 1) latest published settlement: probe recent candidates (with a short
    # holiday fallback — the latest settlement itself may be holiday-shifted)
    latest: tuple[date, float, list[dict]] | None = None
    errors: list[str] = []
    for day in _settlement_candidates(today):
        got = await _fetch_with_fallback(get_text, day, back_days=3)
        if got is not None:
            latest = got
            break
        errors.append(str(day))
    if latest is None:
        raise RuntimeError("no short-interest file for recent settlements: "
                           + "; ".join(errors[:3]))
    asof, total, rows = latest
    _store_settlement(store, asof, total, rows)
    watch = {r["symbol"]: {"short": r["short"], "prev": r["prev"],
                           "adv": r["adv"], "dtc": r["dtc"],
                           "chg_pct": r["chg_pct"], "chg_nom": r["chg_nom"],
                           "name": r["name"]}
             for r in rows if r["symbol"] in TICKERS}
    store.put_doc("finra_short", {
        "as_of": asof.isoformat(),
        "total_short_shares": total,
        "tickers": watch,
    }, source=SOURCE)

    # 2) full-universe backfill from 2020-01-15; skip what's already stored.
    # The holiday fallback covers market-holiday settlement dates; a
    # persistent holiday map remembers enumerated->actual resolutions so a
    # holiday settlement isn't refetched on every run (its enumerated date
    # never appears in the table, only the resolved file date does).
    HOLIDAY_DOC = "finra_short_holiday_map"
    hdoc = store.doc(HOLIDAY_DOC)
    hmap = dict(hdoc.payload) if hdoc and isinstance(hdoc.payload, dict) else {}
    have = {d for d, _ in store.short_interest_settlements()}
    done = have | {date.fromisoformat(k) for k in hmap.keys()
                   if isinstance(k, str)}
    missing = [d for d in settlement_dates(BACKFILL_SINCE, asof) if d not in done]
    backfilled = 0
    for day in missing:
        got = await _fetch_with_fallback(get_text, day)
        if got is None:
            log.debug("finra_short %s skipped: no file", day)
            continue
        s_asof, s_total, s_rows = got
        _store_settlement(store, s_asof, s_total, s_rows)
        if s_asof != day:
            hmap[day.isoformat()] = s_asof.isoformat()
            store.put_doc(HOLIDAY_DOC, hmap, source=SOURCE)
        backfilled += 1
        await asyncio.sleep(REQUEST_GAP)
    log.info("finra_short backfilled %d settlements (%d tickers latest)",
             backfilled, len(rows))
    return SOURCE
