"""FINRA equity short interest — twice-monthly consolidated file (keyless).

FINRA publishes short interest for all exchange-listed stocks, settled on
the 15th and the last business day of each month, ~8 business days later:

  https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv

Pipe-delimited, quoted: settlement id | symbol | name | exchange |
market class | current short | previous short | split flag | avg daily
volume | days to cover | revision | change % | change | settlement date.
Verified live 2026-10-03 (2026-09-15: 22,596 rows; month-end file works).

Stored: cycle:finra-short-total (sum of current short, shares) plus
cycle:short:<TICKER> for the six hyperscalers (MSFT NVDA AAPL AMZN GOOGL
META — wired into the HYPER tab via the finra_short doc).
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

TICKERS = ("MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "META")


def _settlement_candidates(today: date) -> list[date]:
    """Recent settlement dates (15th / month-end), newest first."""
    out: list[date] = []
    y, m = today.year, today.month
    for _ in range(4):
        if date(y, m, 15) <= today:
            out.append(date(y, m, 15))
        # last business day of month
        last = calendar.monthrange(y, m)[1]
        d = date(y, m, last)
        while d.weekday() > 4:
            d -= timedelta(days=1)
        if d <= today:
            out.append(d)
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    out.sort(reverse=True)
    return out


def parse_short(text: str) -> tuple[date, dict[str, dict]]:
    """Return (settlement date, {symbol: {short, prev, adv, dtc, chg_pct, chg_nom, name}})."""
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader, None)
    if not header or "currentShortPositionQuantity" not in "|".join(header):
        raise ValueError("unexpected short-interest header")
    tickers: dict[str, dict] = {}
    total = 0.0
    asof: date | None = None
    for row in reader:
        if len(row) < 14:
            continue
        try:
            sym = row[1].strip().upper()
            name = row[2].strip()
            short = float(row[5] or 0)
            prev = float(row[6] or 0)
            adv = float(row[8] or 0)
            dtc = row[9].strip()
            chg = row[11].strip()
            chg_nom = row[12].strip()
            sdate = date.fromisoformat(row[13].strip())
        except (ValueError, IndexError):
            continue
        asof = sdate
        total += short
        if sym in TICKERS:
            tickers[sym] = {
                "short": short, "prev": prev, "adv": adv,
                "dtc": float(dtc) if dtc else None,
                "chg_pct": float(chg) if chg else None,
                "chg_nom": float(chg_nom) if chg_nom else None,
                "name": name or None,
            }
    if asof is None:
        raise ValueError("no data rows parsed")
    return asof, {"_total": total, "_asof": asof, **tickers}


async def fetch_finra_short(store: Store, get_text: GetText,
                            today: date | None = None) -> str:
    """Weekly poll: latest settlement file (probe recent candidates);
    bounded 12-period backfill on an empty store."""
    today = today or date.today()
    got: tuple[date, dict] | None = None
    errors: list[str] = []
    for day in _settlement_candidates(today):
        url = URL.format(day.strftime("%Y%m%d"))
        try:
            text = await get_text(url)
            if len(text) < 10000:
                raise ValueError(f"suspiciously small ({len(text)}B)")
            asof, parsed = parse_short(text)
            total = parsed.pop("_total")
            parsed.pop("_asof")
            store.upsert_points("cycle:finra-short-total", [(asof, total)])
            for sym, vals in parsed.items():
                store.upsert_points(f"cycle:short-{sym}",
                                    [(asof, vals["short"])])
            store.put_doc("finra_short", {
                "as_of": asof.isoformat(),
                "total_short_shares": total,
                "tickers": parsed,
            }, source=SOURCE)
            got = (asof, parsed)
            break
        except Exception as exc:  # noqa: BLE001 — not yet published
            errors.append(f"{day}: {exc}")
            await asyncio.sleep(REQUEST_GAP)
    if got is None:
        raise RuntimeError("no short-interest file for recent settlements: "
                           + "; ".join(errors[:3]))
    if len(store.points("cycle:finra-short-total")) < 6:
        backfilled = 0
        for day in _settlement_candidates(today)[1:13]:
            try:
                text = await get_text(URL.format(day.strftime("%Y%m%d")))
                asof, parsed = parse_short(text)
            except Exception as exc:  # noqa: BLE001 — tolerate gaps
                log.debug("finra_short %s skipped: %s", day, exc)
                await asyncio.sleep(REQUEST_GAP)
                continue
            total = parsed.pop("_total")
            parsed.pop("_asof", None)
            store.upsert_points("cycle:finra-short-total", [(asof, total)])
            for sym, vals in parsed.items():
                store.upsert_points(f"cycle:short-{sym}",
                                    [(asof, vals["short"])])
            backfilled += 1
            await asyncio.sleep(REQUEST_GAP)
        log.info("finra_short backfilled %d settlements", backfilled)
    return SOURCE
