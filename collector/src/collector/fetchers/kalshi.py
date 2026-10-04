"""Kalshi prediction-market data — keyless public Trade API v2.

Two batched calls per run (public market-data endpoints need no auth):

  https://api.elections.kalshi.com/trade-api/v2/markets
      ?status=open&limit=1000                      (universe, sorted client-side)
  https://api.elections.kalshi.com/trade-api/v2/markets
      ?series_ticker=<S>&status=open&limit=100      (per matchable series)

Verified live 2026-10-03. Prices arrive as dollar strings ("0.6700");
counts as float strings. /markets default order is newest-first (often
illiquid), so we sort by volume_24h client-side. If the elections host
fails, we retry once against the dedicated external host.

Stored: per-market Yes-price history at cycle:kal-<ticker>-yes plus a
`kalshi` doc with the current top-market snapshot for the PREDICT tab
and the edge engine.
"""
from __future__ import annotations

import logging
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "kalshi-trade-api"
BASES = (
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://external-api.kalshi.com/trade-api/v2",
)

TOP_N = 30  # markets kept per run; bounds series churn

# Series polled explicitly so cross-venue matchable markets are covered even
# when they are not in the top-volume list. Tickers verified live 2026-10-03.
MATCH_SERIES = ("FEDHIKE", "KXCPI", "BTCD", "KXBTCMINY")


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def parse_markets(body: dict) -> list[dict]:
    """Kalshi /markets payload -> normalized market rows."""
    if not isinstance(body, dict) or not isinstance(body.get("markets"), list):
        raise ValueError("kalshi markets payload missing 'markets' list")
    rows = []
    for m in body["markets"]:
        if not isinstance(m, dict) or not m.get("ticker"):
            continue
        if m.get("market_type") not in (None, "binary"):
            continue  # engine only prices binary Yes/No markets
        rows.append({
            "venue": "kalshi",
            "ticker": str(m["ticker"]),
            "key": str(m["ticker"]).lower().replace("-", "_"),
            "title": str(m.get("title") or ""),
            "yes_bid": _f(m.get("yes_bid_dollars")),
            "yes_ask": _f(m.get("yes_ask_dollars")),
            "last_price": _f(m.get("last_price_dollars")),
            "volume24h": _f(m.get("volume_24h_fp")) or 0.0,
            "volume_total": _f(m.get("volume_fp")) or 0.0,
            "open_interest": _f(m.get("open_interest_fp")) or 0.0,
            "status": str(m.get("status") or ""),
            "close_time": m.get("close_time"),
            "event_ticker": str(m.get("event_ticker") or ""),
            "url": f"https://kalshi.com/markets/{m['ticker']}",
        })
    rows.sort(key=lambda r: r["volume24h"], reverse=True)
    return rows


def mid_price(row: dict) -> float | None:
    """Best tradable Yes-price estimate: book mid, else last price."""
    b, a = row.get("yes_bid"), row.get("yes_ask")
    if b is not None and a is not None and a >= b and (a - b) < 1.0:
        return (b + a) / 2
    if row.get("last_price") is not None:
        return row["last_price"]
    return b if b is not None else a


async def _get_first(get_text: GetText, path: str) -> dict:
    """Try each Kalshi host in order; raise the last error if all fail."""
    import json
    last_exc: Exception | None = None
    for base in BASES:
        try:
            return json.loads(await get_text(f"{base}{path}"))
        except Exception as exc:  # noqa: BLE001 — try next host
            last_exc = exc
            log.warning("kalshi host %s failed: %s", base, exc)
    raise RuntimeError(f"all kalshi hosts failed: {last_exc}")


async def fetch_kalshi(store: Store, get_text: GetText) -> str:
    body = await _get_first(get_text, "/markets?status=open&limit=1000")
    rows = parse_markets(body)
    # ensure matchable series are represented even when thin
    seen = {r["ticker"] for r in rows}
    for series in MATCH_SERIES:
        try:
            extra = parse_markets(await _get_first(
                get_text, f"/markets?series_ticker={series}&status=open&limit=100"))
        except Exception as exc:  # noqa: BLE001 — one bad series never fails the job
            log.warning("kalshi series %s skipped: %s", series, exc)
            continue
        for r in extra:
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                rows.append(r)
    if not rows:
        raise RuntimeError("kalshi returned no markets")
    rows.sort(key=lambda r: r["volume24h"], reverse=True)
    today = date.today()
    kept = 0
    for row in rows[:TOP_N]:
        px = mid_price(row)
        if px is None:
            continue
        store.upsert_points(f"cycle:kal-{row['key']}-yes", [(today, px)])
        kept += 1
    if not kept:
        raise RuntimeError("kalshi: no usable prices in top markets")
    store.put_doc("kalshi", {
        "as_of": today.isoformat(),
        "markets": rows[:TOP_N],
        "note": "Yes-price mid/last snapshot; model inputs only, not trading advice.",
    }, source=SOURCE)
    log.info("kalshi: %d markets, %d series updated", len(rows[:TOP_N]), kept)
    return SOURCE
