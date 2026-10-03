"""CoinGecko crypto market breadth — keyless.

One batched call per day (well inside the free tier's ~5-15 calls/min):

  https://api.coingecko.com/api/v3/coins/markets
      ?vs_currency=usd&order=market_cap_desc&per_page=50&page=1
      &price_change_percentage=24h,7d

Verified live 2026-10-03. The DEFI tab previously covered vault yields and
Morpho markets only; this fills the missing spot-market breadth: top-50
coins by market cap with price, market cap, 24h volume, and 24h/7d change.

Stored: `coingecko` doc (full top-50 table for the UI) plus daily price
and market-cap points at cycle:coingecko:<id>:price and
cycle:coingecko:<id>:mcap for every coin returned.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "coingecko-markets"
URL = ("https://api.coingecko.com/api/v3/coins/markets"
       "?vs_currency=usd&order=market_cap_desc&per_page=50&page=1"
       "&price_change_percentage=24h,7d")


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def parse_markets(text: str) -> list[dict]:
    """CoinGecko /coins/markets array -> normalized coin rows."""
    body = json.loads(text)
    if not isinstance(body, list):
        raise ValueError("coingecko markets payload is not a list")
    rows = []
    for c in body:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        rows.append({
            "id": str(c["id"]),
            "symbol": str(c.get("symbol") or "").upper(),
            "name": str(c.get("name") or ""),
            "rank": c.get("market_cap_rank"),
            "price": _f(c.get("current_price")),
            "mcap": _f(c.get("market_cap")),
            "vol24h": _f(c.get("total_volume")),
            "chg24h": _f(c.get("price_change_percentage_24h")),
            "chg7d": _f(c.get("price_change_percentage_7d_in_currency")),
        })
    # the endpoint is already rank-ordered; be defensive anyway
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0))
    return rows


async def fetch_coingecko(store: Store, get_text: GetText) -> str:
    coins = parse_markets(await get_text(URL))
    if not coins:
        raise RuntimeError("coingecko returned no coins")
    today = date.today()
    for c in coins:
        if c["price"] is not None:
            store.upsert_points(f"cycle:coingecko:{c['id']}:price", [(today, c["price"])])
        if c["mcap"] is not None:
            store.upsert_points(f"cycle:coingecko:{c['id']}:mcap", [(today, c["mcap"])])
    btc = next((c for c in coins if c["id"] == "bitcoin"), None)
    store.put_doc("coingecko", {
        "as_of": today.isoformat(),
        "coins": coins,
        "btc_dominance_pct": (round(100 * btc["mcap"] / sum(c["mcap"] or 0 for c in coins), 2)
                              if btc and btc["mcap"] else None),
    }, SOURCE)
    return SOURCE
