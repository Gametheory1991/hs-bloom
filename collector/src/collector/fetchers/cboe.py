"""CBOE daily market statistics — put/call ratios (keyless CDN JSON).

The endpoint is per-day, so history accumulates: the daily cycle job fetches
today's file ONCE and derives all configured ratios from it (see
``fetch_daily_ratios``). ``fetch_ratio_history`` still walks the last ``days``
weekdays, but only as a thin-history bootstrap (see cycle.py). Holidays /
missing days raise on the CDN (403/404) and are skipped silently — only a
fully-empty walk raises.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from collector.http import GetText

BASE = "https://cdn.cboe.com/data/us/options/market_statistics/daily"
DEFAULT_DAYS = 30


def parse_daily(text: str, ratio_name: str) -> float:
    for ratio in json.loads(text).get("ratios") or []:
        if ratio.get("name") == ratio_name:
            return float(ratio["value"])
    raise ValueError(f"cboe daily stats missing ratio {ratio_name!r}")


async def fetch_daily_ratios(
    ratio_names: list[str], get_text: GetText, today: date | None = None
) -> dict[str, tuple[date, float]]:
    """Fetch the newest daily file ONCE and derive every requested ratio.

    All configured ratios live in the same daily file, so one run needs one
    fetch — not one 30-day walk per ratio. Walks back over weekends/holidays
    (403/404 on the CDN) until one usable file is found. Raises if no usable
    file appears in the lookback window or a requested ratio is absent from it.
    Returns {ratio_name: (date, value)}.
    """
    today = today or date.today()
    last_exc: Exception | None = None
    for back in range(14):  # ~10 weekdays: covers long holiday stretches
        d = today - timedelta(days=back)
        if d.weekday() >= 5:  # no stats published on weekends
            continue
        try:
            text = await get_text(f"{BASE}/{d.isoformat()}_daily_options")
        except Exception as exc:  # noqa: BLE001 — holiday or not-yet-published day
            last_exc = exc
            continue
        ratios = json.loads(text).get("ratios") or []
        out: dict[str, tuple[date, float]] = {}
        for name in ratio_names:
            for ratio in ratios:
                if ratio.get("name") == name:
                    out[name] = (d, float(ratio["value"]))
                    break
            else:
                raise ValueError(f"cboe daily stats missing ratio {name!r}")
        return out
    raise ValueError(f"cboe found no usable daily file in the lookback window: {last_exc}")


async def fetch_ratio_history(
    ratio_name: str, get_text: GetText, days: int = DEFAULT_DAYS, today: date | None = None
) -> list[tuple[date, float]]:
    today = today or date.today()
    out: list[tuple[date, float]] = []
    for back in range(days):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:  # no stats published on weekends
            continue
        try:
            out.append((d, parse_daily(await get_text(f"{BASE}/{d.isoformat()}_daily_options"), ratio_name)))
        except Exception:  # noqa: BLE001 — holiday or not-yet-published day
            continue
    if not out:
        raise ValueError(f"cboe returned no usable days for {ratio_name!r}")
    out.sort(key=lambda p: p[0])
    return out
