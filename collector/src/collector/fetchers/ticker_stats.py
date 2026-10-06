"""Speculator-style per-ticker stats for the top-shorted table.

For each ticker in the current Reg SHO top-50 (the panel shows the first
25), pulls from Finnhub (FINNHUB_API_KEY env):
  - GET /quote                     -> price, %1D change
  - GET /stock/metric?metric=all   -> market cap, P/E (TTM), 52w high/low
  - GET /stock/candle?resolution=D -> 1Y daily closes -> sparkline, %YTD,
                                      20/50/200 SMAs, 1M return

Derived per ticker:
  - off_high52 = price / 52wHigh - 1  (% off the 52-week high)
  - rs_1m      = percentile rank (0-99) of the 21-trading-day return within
                 this stat universe — an honest, self-contained relative-
                 strength rank, not vs the whole market
  - sma20/50/200 above/below flags for the triangle indicators

Stored as the `ticker_stats` doc: {as_of, tickers: {SYM: {...}}}.
Sparklines are downsampled to weekly (~52 points) to keep the doc small.

Runs daily after finra_regsho (reads its top-50 symbols). Skips cleanly
when no FINNHUB_API_KEY is set; one bad ticker never kills the job.
Free tier: 60 calls/min — 3 calls/ticker with 1.2s pacing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "ticker-stats"
DOC_KEY = "ticker_stats"
BASE = "https://finnhub.io/api/v1"

REQUEST_GAP = 1.2
MAX_TICKERS = 25  # matches the panel's top-50 slice


def parse_quote(body: dict) -> dict | None:
    """Finnhub /quote -> {price, pct_1d}. None when the quote is empty."""
    try:
        price = float(body.get("c") or 0)
        if price <= 0:
            return None
        return {"price": price,
                "pct_1d": float(body.get("dp") or 0) / 100.0}
    except (TypeError, ValueError):
        return None


def parse_metric(body: dict) -> dict:
    """Finnhub /stock/metric -> {mcap, pe, high52, low52} (Nones when absent)."""
    m = body.get("metric") or {}

    def _f(key: str) -> float | None:
        try:
            v = m.get(key)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    pe = _f("peTTM")
    if pe is None or pe <= 0:
        pe = _f("peNormalizedAnnual")
        if pe is not None and pe <= 0:
            pe = None
    return {"mcap": _f("marketCapitalization"),
            "pe": pe,
            "high52": _f("52WeekHigh"),
            "low52": _f("52WeekLow")}


def parse_candle(body: dict) -> list[tuple[date, float]]:
    """Finnhub /stock/candle -> sorted [(date, close)]."""
    if (body.get("s") or "").lower() != "ok":
        return []
    out = []
    for ts, c in zip(body.get("t") or [], body.get("c") or []):
        try:
            if c is None:
                continue
            out.append((datetime.fromtimestamp(int(ts), tz=timezone.utc).date(),
                        float(c)))
        except (TypeError, ValueError, OSError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def _sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def downsample_weekly(closes: list[tuple[date, float]],
                      n: int = 52) -> list[float]:
    """Last close per ISO week, most recent `n` weeks (for sparklines)."""
    weeks: dict[tuple[int, int], float] = {}
    for d, c in closes:
        iso = d.isocalendar()
        weeks[(iso.year, iso.week)] = c  # closes are sorted: last wins
    keys = sorted(weeks)[-n:]
    return [round(weeks[k], 2) for k in keys]


def compute_stats(closes: list[tuple[date, float]],
                  quote: dict, metric: dict) -> dict:
    """Assemble the stat row from quote + metric + daily closes."""
    prices = [c for _, c in closes]
    price = quote["price"]
    high52 = metric.get("high52")
    stats = {
        "price": round(price, 2),
        "pct_1d": round(quote["pct_1d"], 4),
        "mcap": metric.get("mcap"),
        "pe": round(metric["pe"], 1) if metric.get("pe") else None,
        "high52": high52,
        "off_high52": (round(price / high52 - 1, 4)
                       if high52 else None),
        "sma20": None, "sma50": None, "sma200": None,
        "ytd": None, "ret_1m": None,
        "spark": downsample_weekly(closes),
    }
    for n in (20, 50, 200):
        v = _sma(prices, n)
        stats[f"sma{n}"] = round(v, 2) if v else None
    # %YTD: vs the first close of the current calendar year in the series
    yr = date.today().year
    ytd_base = next((c for d, c in closes if d.year == yr), None)
    if ytd_base:
        stats["ytd"] = round(price / ytd_base - 1, 4)
    # 1M return: 21 trading days back
    if len(prices) >= 22:
        stats["ret_1m"] = round(prices[-1] / prices[-22] - 1, 4)
    return stats


def assign_rs_ranks(tickers: dict[str, dict]) -> None:
    """Percentile rank (0-99) of each ticker's 1M return within the universe.

    Mutates the per-ticker dicts in place, adding "rs_1m". Tickers without a
    1M return get None.
    """
    rets = sorted((s["ret_1m"] for s in tickers.values()
                   if s.get("ret_1m") is not None))
    n = len(rets)
    for s in tickers.values():
        r = s.get("ret_1m")
        if r is None or n < 2:
            s["rs_1m"] = None
            continue
        # percentile: share of universe at or below this return, scaled 0-99
        rank = sum(1 for x in rets if x <= r)
        s["rs_1m"] = min(99, int(round((rank - 1) / (n - 1) * 99)))


async def _fetch_one(sym: str, get_text: GetText, api_key: str,
                     today: date) -> dict | None:
    """The 3 Finnhub calls for one ticker; None when unusable."""
    try:
        q = await get_text(f"{BASE}/quote",
                           params={"symbol": sym, "token": api_key})
        quote = parse_quote(json.loads(q) if isinstance(q, str) else q)
        if not quote:
            return None
        await asyncio.sleep(REQUEST_GAP)
        m = await get_text(f"{BASE}/stock/metric",
                           params={"symbol": sym, "metric": "all",
                                   "token": api_key})
        metric = parse_metric(json.loads(m) if isinstance(m, str) else m)
        await asyncio.sleep(REQUEST_GAP)
        frm = int(time.mktime((today - timedelta(days=400)).timetuple()))
        to = int(time.mktime(today.timetuple()))
        c = await get_text(f"{BASE}/stock/candle",
                           params={"symbol": sym, "resolution": "D",
                                   "from": frm, "to": to, "token": api_key})
        closes = parse_candle(json.loads(c) if isinstance(c, str) else c)
        await asyncio.sleep(REQUEST_GAP)
    except Exception as exc:  # noqa: BLE001 — one ticker never kills the job
        log.debug("ticker_stats %s failed: %s", sym, exc)
        return None
    if not closes:
        return None
    return compute_stats(closes, quote, metric)


async def fetch_ticker_stats(store: Store, get_text: GetText,
                             today: date | None = None) -> str:
    """Daily: speculator-style stats for the current top-shorted tickers."""
    today = today or date.today()
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        log.info("ticker_stats: no FINNHUB_API_KEY set — skipping")
        return "ticker-stats-skipped-no-key"

    # universe: today's top-shorted symbols (panel shows the first 25)
    symbols: list[str] = []
    doc = store.doc("regsho_daily")
    if doc:
        symbols = [t.get("symbol") for t in
                   (doc.payload.get("top50") or [])[:MAX_TICKERS]
                   if t.get("symbol")]
    if not symbols:
        log.warning("ticker_stats: no regsho_daily top50 — skipping")
        return "ticker-stats-skipped-no-universe"

    tickers: dict[str, dict] = {}
    for sym in symbols:
        stats = await _fetch_one(sym, get_text, api_key, today)
        if stats:
            tickers[sym] = stats
        else:
            log.debug("ticker_stats: no stats for %s", sym)
    if not tickers:
        log.warning("ticker_stats: no tickers resolved — skipping doc write")
        return "ticker-stats-skipped-empty"
    assign_rs_ranks(tickers)
    store.put_doc(DOC_KEY, {
        "as_of": today.isoformat(),
        "count": len(tickers),
        "tickers": tickers,
    }, source=SOURCE)
    log.info("ticker_stats: %d/%d tickers", len(tickers), len(symbols))
    return SOURCE
