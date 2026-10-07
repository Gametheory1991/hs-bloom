"""Polymarket prediction-market data — keyless public Gamma API.

One batched call per run (well inside Gamma's generous public limits):

  https://gamma-api.polymarket.com/markets
      ?closed=false&active=true&order=volume24hr&ascending=false&limit=60

Verified live 2026-10-03. `outcomePrices`/`outcomes`/`clobTokenIds` arrive
as double-encoded JSON strings; volume/liquidity as floats; bestBid/bestAsk
give the CLOB top-of-book without extra calls.

Stored: per-market Yes-price history at cycle:pm-<slug>-yes (slug
sanitized, truncated; backfilled to market inception via the public CLOB
prices-history endpoint), per-market daily 24h volume at cycle:pm-<slug>-vol
($; NO free historical volume endpoint exists on Polymarket — volume
history starts Oct 2026 and this is labeled as such), venue daily
tracked-universe total at cycle:predvol-polymarket (sum of the tracked
top-30 volume24h — labeled as tracked-universe, NOT full-venue volume),
plus a `polymarket` doc with the current top-market snapshot for the
PREDICT tab and the edge engine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "polymarket-gamma"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
URL = (f"{GAMMA}/markets?closed=false&active=true"
       "&order=volume24hr&ascending=false&limit=60")

TOP_N = 30  # markets kept per run; bounds series churn

# Price-history backfill (verified live 2026-10-07):
#   GET {CLOB}/prices-history?market={YES_clob_token}&interval=max&fidelity=1440
# returns {"history": [{"t": ts, "p": price}]} from market inception.
# PRICE ONLY — Polymarket publishes no free historical volume endpoint,
# so cycle:pm-*-vol history starts Oct 2026 (honest gap, labeled in UI).
PX_BACKFILL_N = 30   # markets per daily backfill run
PX_BACKFILL_PAUSE = 0.5


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
        tids = _jstr(m.get("clobTokenIds"))
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
            "clob_yes_token": str(tids[0]) if tids else None,
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


async def backfill_polymarket_prices(store: Store, get_text: GetText,
                                     rows: list[dict]) -> dict:
    """Daily yes-price backfill for newly-seen top-30 markets via the public
    CLOB prices-history endpoint (history to market inception, price only).

    Idempotent (upserts), resume-safe (completed keys in the poly_px_backfill
    doc), runs at most once per day, bounded HTTP. Never raises.
    """
    today = date.today()
    doc = store.doc("poly_px_backfill")
    dp = doc.payload if doc else {}
    if dp.get("asof") == today.isoformat():
        return {"status": "already ran today"}
    done = set(dp.get("done") or [])
    todo = [r for r in rows[:TOP_N]
            if r.get("clob_yes_token") and r["key"] not in done][:PX_BACKFILL_N]
    filled, n_pts = 0, 0
    for r in todo:
        try:
            body = json.loads(await get_text(
                f"{CLOB}/prices-history?market={r['clob_yes_token']}"
                "&interval=max&fidelity=1440"))
        except Exception as exc:  # noqa: BLE001 — one bad market never fails backfill
            log.warning("polymarket price backfill %s failed: %s", r["key"], exc)
            continue
        if not isinstance(body, dict):
            continue
        pts = []
        for h in (body.get("history") or []):
            try:
                day = datetime.fromtimestamp(int(h["t"]), tz=timezone.utc).date()
                p = float(h["p"])
            except (TypeError, ValueError, KeyError):
                continue
            if 0.0 <= p <= 1.0:
                pts.append((day, p))
        if pts:
            # same-day duplicates collapse to the last point (matches the
            # live job's "latest wins" semantics)
            store.upsert_points(f"cycle:pm-{r['key']}-yes", pts)
            n_pts += len(pts)
        done.add(r["key"])
        filled += 1
        await asyncio.sleep(PX_BACKFILL_PAUSE)
    store.put_doc("poly_px_backfill", {
        "asof": today.isoformat(), "done": sorted(done),
        "filled_this_run": filled, "price_points": n_pts,
        "note": ("Yes-price history backfilled to market inception via public "
                 "CLOB prices-history. No free Polymarket volume-history "
                 "endpoint exists — volume series start Oct 2026."),
    }, source=SOURCE)
    return {"status": f"{filled} markets, {n_pts} price points"}


async def fetch_polymarket(store: Store, get_text: GetText) -> str:
    rows = parse_markets(await get_text(URL))
    if not rows:
        raise RuntimeError("polymarket returned no markets")
    today = date.today()
    kept = 0
    vol_total = 0.0
    for row in rows[:TOP_N]:
        px = mid_price(row)
        if px is None:
            continue
        store.upsert_points(f"cycle:pm-{row['key']}-yes", [(today, px)])
        # daily volume point: upsert overwrites same-day, so every 30-min
        # run refreshes today's value — idempotent, one point per day.
        store.upsert_points(f"cycle:pm-{row['key']}-vol", [(today, row["volume24h"])])
        vol_total += row["volume24h"]
        kept += 1
    if not kept:
        raise RuntimeError("polymarket: no usable prices in top markets")
    # tracked-universe venue total (top-30 by 24h volume), not full venue.
    store.upsert_points("cycle:predvol-polymarket", [(today, vol_total)])
    # active tracked markets today (for the activity panel).
    store.upsert_points("cycle:predact-polymarket", [(today, kept)])
    store.put_doc("polymarket", {
        "as_of": today.isoformat(),
        "markets": rows[:TOP_N],
        "note": "Yes-price mid/last-trade snapshot; model inputs only, not trading advice.",
    }, source=SOURCE)
    log.info("polymarket: %d markets, %d series updated", len(rows[:TOP_N]), kept)
    try:
        bf = await backfill_polymarket_prices(store, get_text, rows)
        log.info("polymarket price backfill: %s", bf.get("status"))
    except Exception as exc:  # noqa: BLE001 — backfill never fails the job
        log.warning("polymarket price backfill skipped: %s", exc)
    return SOURCE
