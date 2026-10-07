"""Polymarket prediction-market data — keyless public Gamma API.

One batched call per run (well inside Gamma's generous public limits):

  https://gamma-api.polymarket.com/markets
      ?closed=false&active=true&order=volume24hr&ascending=false&limit=60

Verified live 2026-10-03. `outcomePrices`/`outcomes`/`clobTokenIds` arrive
as double-encoded JSON strings; volume/liquidity as floats; bestBid/bestAsk
give the CLOB top-of-book without extra calls.

Stored: per-market Yes-price history at cycle:pm-<slug>-yes (slug
sanitized, truncated) plus a `polymarket` doc with the current top-market
snapshot for the PREDICT tab and the edge engine.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "polymarket-gamma"
GAMMA = "https://gamma-api.polymarket.com"
URL = (f"{GAMMA}/markets?closed=false&active=true"
       "&order=volume24hr&ascending=false&limit=60")

TOP_N = 30  # markets kept per run; bounds series churn


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _jstr(x) -> list:
    """Gamma double-encodes several fields as JSON strings."""
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def slug_key(slug: str | None, question: str) -> str:
    """Stable, store-safe series key fragment from a market slug."""
    base = (slug or question or "unknown").lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:60] or "unknown"


def parse_markets(text: str) -> list[dict]:
    """Gamma /markets array -> normalized market rows."""
    body = json.loads(text)
    if not isinstance(body, list):
        raise ValueError("polymarket markets payload is not a list")
    rows = []
    for m in body:
        if not isinstance(m, dict):
            continue
        prices = _jstr(m.get("outcomePrices"))
        outcomes = _jstr(m.get("outcomes"))
        yes_price = _f(prices[0]) if prices else None
        # top-of-book from Gamma directly — no extra CLOB calls needed
        rows.append({
            "venue": "polymarket",
            "slug": str(m.get("slug") or ""),
            "key": slug_key(m.get("slug"), str(m.get("question") or "")),
            "question": str(m.get("question") or ""),
            "yes_price": yes_price,
            "no_price": _f(prices[1]) if len(prices) > 1 else None,
            "outcomes": [str(o) for o in outcomes],
            "best_bid": _f(m.get("bestBid")),
            "best_ask": _f(m.get("bestAsk")),
            "last_trade": _f(m.get("lastTradePrice")),
            "volume24h": _f(m.get("volume24hr")) or 0.0,
            "volume_total": _f(m.get("volumeNum") or m.get("volume")) or 0.0,
            "liquidity": _f(m.get("liquidityNum") or m.get("liquidity")) or 0.0,
            "chg_1d": _f(m.get("oneDayPriceChange")),
            "end_date": m.get("endDate"),
            "url": f"https://polymarket.com/event/{m.get('slug')}" if m.get("slug") else None,
        })
    rows.sort(key=lambda r: r["volume24h"], reverse=True)
    return rows


def mid_price(row: dict) -> float | None:
    """Best tradable Yes-price estimate: book mid, else last trade, else quote."""
    b, a = row.get("best_bid"), row.get("best_ask")
    if b is not None and a is not None and a >= b:
        return (b + a) / 2
    if row.get("last_trade") is not None:
        return row["last_trade"]
    return row.get("yes_price")


async def fetch_polymarket(store: Store, get_text: GetText) -> str:
    rows = parse_markets(await get_text(URL))
    if not rows:
        raise RuntimeError("polymarket returned no markets")
    today = date.today()
    kept = 0
    for row in rows[:TOP_N]:
        px = mid_price(row)
        if px is None:
            continue
        store.upsert_points(f"cycle:pm-{row['key']}-yes", [(today, px)])
        kept += 1
    if not kept:
        raise RuntimeError("polymarket: no usable prices in top markets")
    store.put_doc("polymarket", {
        "as_of": today.isoformat(),
        "markets": rows[:TOP_N],
        "note": "Yes-price mid/last-trade snapshot; model inputs only, not trading advice.",
    }, source=SOURCE)
    log.info("polymarket: %d markets, %d series updated", len(rows[:TOP_N]), kept)
    return SOURCE
