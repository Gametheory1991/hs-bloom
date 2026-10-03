"""Finnhub earnings calendar + insider sentiment — free API key required.

No signup is performed by this job; it reads FINNHUB_API_KEY from the
environment and skips cleanly when absent:

  - key absent  -> log + skip (no doc, no failure)
  - key present -> earnings calendar + insider sentiment, polite pacing

Free key signup: https://finnhub.io/register (free tier: 60 calls/min;
endpoints used verified in Finnhub docs 2026-10-03).

Endpoints:
  GET /calendar/earnings?from=&to=&token=
      free tier = 1 month of history + new updates. One call for a 21-day
      forward window, filtered to watchlist tickers (hyperscalers + the
      three coverage universes). Backup for the 429-prone ForexFactory
      econ calendar, scoped to names the terminal actually tracks.
  GET /stock/insider-sentiment?symbol=&from=&to=&token=
      monthly share purchase ratio (MSPR, -100..+100) per ticker. Called
      only for tickers with an upcoming earnings date in the window —
      earnings + insider flow for the same names, and it keeps the call
      count small (~1.5s pacing, typically <20 calls).

Stored: `finnhub` doc {as_of, earnings: [...], insider: [...]}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, timedelta

from collector.fetchers.watchlist import watchlist_tickers
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "finnhub-v1"
BASE = "https://finnhub.io/api/v1"

REQUEST_GAP = 1.5  # free tier: 60 calls/min; stay well under it
EARNINGS_WINDOW_DAYS = 21
INSIDER_LOOKBACK_DAYS = 90


def parse_earnings(body: dict, watch: set[str]) -> list[dict]:
    out = []
    for e in (body.get("earningsCalendar") or []):
        try:
            sym = str(e.get("symbol") or "").upper()
            if sym not in watch:
                continue
            out.append({
                "symbol": sym,
                "date": str(e.get("date")),
                "hour": e.get("hour"),
                "quarter": e.get("quarter"),
                "year": e.get("year"),
                "eps_estimate": e.get("epsEstimate"),
                "eps_actual": e.get("epsActual"),
                "revenue_estimate": e.get("revenueEstimate"),
                "revenue_actual": e.get("revenueActual"),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: (r["date"], r["symbol"]))
    return out


def parse_insider_sentiment(body: dict) -> list[dict]:
    out = []
    for m in (body.get("data") or []):
        try:
            out.append({
                "symbol": str(body.get("symbol") or "").upper(),
                "year": int(m["year"]),
                "month": int(m["month"]),
                "mspr": None if m.get("mspr") is None else float(m["mspr"]),
                "net_change": m.get("change"),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: (r["year"], r["month"]))
    return out


async def fetch_finnhub(store: Store, get_text: GetText) -> str:
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        log.info("finnhub: no FINNHUB_API_KEY set — skipping (free key: finnhub.io/register)")
        return "finnhub-skipped-no-key"
    watch = set(watchlist_tickers())
    today = date.today()
    frm = today.isoformat()
    to = (today + timedelta(days=EARNINGS_WINDOW_DAYS)).isoformat()

    earnings: list[dict] = []
    try:
        body = await get_text(f"{BASE}/calendar/earnings",
                              params={"from": frm, "to": to, "token": api_key})
        payload = json.loads(body) if isinstance(body, str) else body
        earnings = parse_earnings(payload if isinstance(payload, dict) else {}, watch)
    except Exception as exc:  # noqa: BLE001 — earnings never kills the job
        log.warning("finnhub earnings calendar failed: %s", exc)

    # insider sentiment only for names reporting in the window (bounded calls)
    earn_syms = sorted({e["symbol"] for e in earnings})
    insider: list[dict] = []
    since = (today - timedelta(days=INSIDER_LOOKBACK_DAYS)).isoformat()
    for i, sym in enumerate(earn_syms):
        if i:
            await asyncio.sleep(REQUEST_GAP)
        try:
            body = await get_text(f"{BASE}/stock/insider-sentiment",
                                  params={"symbol": sym, "from": since, "to": frm,
                                          "token": api_key})
            payload = json.loads(body) if isinstance(body, str) else body
            insider.extend(parse_insider_sentiment(payload if isinstance(payload, dict) else {}))
        except Exception as exc:  # noqa: BLE001 — one ticker never kills the job
            log.warning("finnhub insider sentiment %s failed: %s", sym, exc)

    if not earnings and not insider:
        log.warning("finnhub returned nothing — skipping doc write")
        return "finnhub-skipped-empty"
    store.put_doc("finnhub", {
        "as_of": today.isoformat(),
        "window": {"from": frm, "to": to},
        "earnings": earnings,
        "insider": insider,
    }, SOURCE)
    return SOURCE
