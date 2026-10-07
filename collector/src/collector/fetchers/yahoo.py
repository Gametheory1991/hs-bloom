"""Yahoo Finance chart API: keyless daily closes + a fresher last quote.

The keyless equity source. Uses http.USER_AGENT like every other fetcher --
verified 2026-09-06 that Yahoo serves this endpoint to an honest UA; only the
bare `curl/x.y` default is throttled.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from datetime import date, datetime, timezone

from collector.http import GetText


# Yahoo's unpublished rate limits 429 under burst load (seen at boot when the
# equity + cycle jobs fire near-simultaneously). Enforce a minimum gap between
# requests process-wide. Locks are cached per event loop so tests (fresh loop
# per asyncio.run) never trip "bound to a different event loop".
YAHOO_MIN_GAP = 0.5  # seconds; tests may monkeypatch to 0
_yahoo_next_at = 0.0
_yahoo_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


async def _pace_yahoo() -> None:
    loop = asyncio.get_running_loop()
    lock = _yahoo_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _yahoo_locks[loop] = lock
        for dead in [lp for lp in _yahoo_locks if lp.is_closed()]:
            del _yahoo_locks[dead]
    async with lock:
        global _yahoo_next_at
        now = time.monotonic()
        if now < _yahoo_next_at:
            await asyncio.sleep(_yahoo_next_at - now)
        _yahoo_next_at = time.monotonic() + YAHOO_MIN_GAP


class Quote:  # simple carrier: closes + a possibly-fresher last
    def __init__(self, closes: list[tuple[date, float]], last: float | None, last_ts: str | None):
        self.closes = closes
        self.last = last
        self.last_ts = last_ts


def parse_chart(text: str) -> Quote:
    chart = json.loads(text)["chart"]
    results = chart.get("result")
    if not results:
        # unknown/delisted symbol: {"chart": {"result": null, "error": {...}}}
        err = (chart.get("error") or {}).get("description", "no result")
        raise ValueError(f"yahoo chart error: {err}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    closes_raw = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    closes = [
        (datetime.fromtimestamp(ts, tz=timezone.utc).date(), float(c))
        for ts, c in zip(timestamps, closes_raw)
        if c is not None
    ]
    if not closes:
        raise ValueError("yahoo chart contained no usable points")
    closes.sort(key=lambda p: p[0])  # callers rely on closes[-1] being latest
    meta = result.get("meta") or {}
    last = meta.get("regularMarketPrice")
    market_time = meta.get("regularMarketTime")
    last_ts = (
        datetime.fromtimestamp(market_time, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if market_time
        else None
    )
    return Quote(closes, float(last) if last is not None else None, last_ts)


async def fetch_chart(symbol: str, get_text: GetText, range_: str = "1y") -> Quote:
    await _pace_yahoo()
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
    return parse_chart(await get_text(url, params={"range": range_, "interval": "1d"}))


def ratio_points(
    closes_a: list[tuple[date, float]], closes_b: list[tuple[date, float]]
) -> list[tuple[date, float]]:
    """Numerator/denominator closes aligned on common dates (cycle ratio series)."""
    b_by_date = dict(closes_b)
    out = []
    for d, a in closes_a:
        b = b_by_date.get(d)
        if b:  # skip missing dates and zero denominators
            out.append((d, round(a / b, 4)))
    return out
