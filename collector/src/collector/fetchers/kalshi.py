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

Stored: per-market Yes-price history at cycle:kal-<ticker>-yes, per-market
daily volume at cycle:kal-<ticker>-vol (ESTIMATED dollars = contracts x
price; Kalshi's native unit is contracts), per-market daily CONTRACT counts
at cycle:kal-<ticker>-volct, venue daily tracked-universe totals at
cycle:predvol-kalshi (est. $) and cycle:predvolct-kalshi (contracts), daily
active tracked-market count at cycle:predact-kalshi (all sums of tracked
top-30 — labeled as tracked-universe, NOT full-venue volume), plus a
`kalshi` doc with the current top-market snapshot for the PREDICT tab and
the edge engine.

Volume/price history backfill: public daily candlesticks reach back to
each market's inception; backfill_kalshi_volume fills cycle:kal-*-vol and
cycle:kal-*-yes for newly-seen top-30 tickers (idempotent, resume-safe,
once/day, bounded HTTP) and rebuilds the venue-total history.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "kalshi-trade-api"
BASES = (
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://external-api.kalshi.com/trade-api/v2",
)

TOP_N = 30  # markets kept per run; bounds series churn

# Volume-history backfill (public candlesticks; verified live 2026-10-07):
#   /markets/candlesticks?market_tickers={csv}&start_ts=&end_ts=&period_interval=1440
# returns per-market daily candles with volume_fp (contracts) and yes_bid/
# yes_ask close_dollars. Dollar volume is estimated as contracts x close-mid
# (Kalshi's native volume unit is contracts; Polymarket's is dollars — the
# estimate keeps the venue chart in common units and matches the UI's $
# formatting of Kalshi volumes). Candles reach back to market inception.
BACKFILL_START_TS = 1609459200  # 2021-01-01 UTC; the API returns what it has
BACKFILL_BATCH = 10            # tickers per candlesticks call
BACKFILL_PAUSE = 0.5           # seconds between backfill calls

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


def _candle_day_usd(c: dict) -> tuple[date, float, float | None, float]:
    """One daily candlestick -> (day, est. dollar volume, close-mid yes price,
    contract volume).

    volume_fp is contracts; dollar volume is estimated as contracts x the
    day's close-mid. Raises on unparseable timestamps.
    """
    day = datetime.fromtimestamp(int(c["end_period_ts"]), tz=timezone.utc).date()
    try:
        contracts = float(c.get("volume_fp") or 0)
    except (TypeError, ValueError):
        contracts = 0.0
    yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}
    closes = [p for p in (_f(yb.get("close_dollars")), _f(ya.get("close_dollars")))
              if p is not None]
    mid = sum(closes) / len(closes) if closes else None
    usd = contracts * mid if mid is not None else 0.0
    return day, usd, mid, contracts


def _rebuild_venue_total(store: Store, done: dict[str, str]) -> int:
    """Rebuild cycle:predvol-kalshi history as the per-day sum of all
    backfilled per-ticker vol series. Excludes today (the live job owns it).
    Labeled as the current-universe sum, not full-venue volume."""
    if not done:
        return 0
    today = date.today()
    many = store.points_many([f"cycle:kal-{key}-vol" for key in done.values()])
    totals: dict[date, float] = {}
    for pts in many.values():
        for d, v in pts.items():
            if d < today:
                totals[d] = totals.get(d, 0.0) + v
    if totals:
        store.upsert_points("cycle:predvol-kalshi", sorted(totals.items()))
    # Same for contract counts (cycle:kal-<key>-volct -> cycle:predvolct-kalshi).
    many_ct = store.points_many([f"cycle:kal-{key}-volct" for key in done.values()])
    ct_totals: dict[date, float] = {}
    for pts in many_ct.values():
        for d, v in pts.items():
            if d < today:
                ct_totals[d] = ct_totals.get(d, 0.0) + v
    if ct_totals:
        store.upsert_points("cycle:predvolct-kalshi", sorted(ct_totals.items()))
    return len(totals)


async def backfill_kalshi_volume(store: Store, get_text: GetText,
                                 rows: list[dict]) -> dict:
    """Daily volume (+price) backfill for newly-seen top-30 tickers via the
    public candlesticks endpoint (history to market inception).

    Idempotent (upserts), resume-safe (completed tickers recorded in the
    kalshi_vol_backfill doc), runs at most once per day, bounded HTTP
    (BACKFILL_BATCH tickers per call). Never raises — callers treat it as
    best-effort.
    """
    today = date.today()
    doc = store.doc("kalshi_vol_backfill")
    dp = doc.payload if doc else {}
    if dp.get("asof") == today.isoformat():
        return {"status": "already ran today"}
    done: dict[str, str] = dict(dp.get("done") or {})  # ticker -> series key
    todo = [r for r in rows[:TOP_N] if r["ticker"] not in done]
    now_ts = int(datetime.now(timezone.utc).timestamp())
    filled, n_vol = 0, 0
    try:
        for i in range(0, len(todo), BACKFILL_BATCH):
            batch = todo[i:i + BACKFILL_BATCH]
            tickers = ",".join(r["ticker"] for r in batch)
            body = await _get_first(
                get_text,
                f"/markets/candlesticks?market_tickers={tickers}"
                f"&start_ts={BACKFILL_START_TS}&end_ts={now_ts}&period_interval=1440")
            if not isinstance(body, dict):
                break
            for m in (body.get("markets") or []):
                tk = m.get("market_ticker")
                row = next((r for r in batch if r["ticker"] == tk), None)
                if row is None:
                    continue
                vol_pts, px_pts, ct_pts = [], [], []
                for c in m.get("candlesticks") or []:
                    try:
                        day, usd, mid, contracts = _candle_day_usd(c)
                    except (TypeError, ValueError, KeyError):
                        continue
                    if usd > 0:
                        vol_pts.append((day, usd))
                    if contracts > 0:
                        ct_pts.append((day, contracts))
                    if mid is not None:
                        px_pts.append((day, mid))
                if vol_pts:
                    store.upsert_points(f"cycle:kal-{row['key']}-vol", vol_pts)
                    n_vol += len(vol_pts)
                if ct_pts:
                    store.upsert_points(f"cycle:kal-{row['key']}-volct", ct_pts)
                if px_pts:
                    store.upsert_points(f"cycle:kal-{row['key']}-yes", px_pts)
                done[tk] = row["key"]
                filled += 1
            await asyncio.sleep(BACKFILL_PAUSE)
        n_days = _rebuild_venue_total(store, done)
    except Exception as exc:  # noqa: BLE001 — best-effort backfill
        log.warning("kalshi backfill batch failed: %s", exc)
        n_days = 0
    store.put_doc("kalshi_vol_backfill", {
        "asof": today.isoformat(), "done": done,
        "filled_this_run": filled, "vol_points": n_vol,
        "venue_total_days": n_days,
        "note": ("Per-ticker daily volume (est. $) backfilled to market "
                 "inception via public candlesticks. Venue total = sum of "
                 "backfilled tickers (current universe), not full venue."),
    }, source=SOURCE)
    return {"status": f"{filled} tickers, {n_vol} vol points, {n_days} venue days"}


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
    vol_total = 0.0
    ct_total = 0.0
    for row in rows[:TOP_N]:
        px = mid_price(row)
        if px is None:
            continue
        store.upsert_points(f"cycle:kal-{row['key']}-yes", [(today, px)])
        # daily volume point, estimated dollars (contracts x mid price —
        # Kalshi's native unit is contracts; the estimate keeps parity with
        # Polymarket's dollar volumes). Upsert overwrites same-day: idempotent.
        vol_usd = row["volume24h"] * px
        store.upsert_points(f"cycle:kal-{row['key']}-vol", [(today, vol_usd)])
        # native contract count (Kalshi's own unit) — powers the activity panel.
        store.upsert_points(f"cycle:kal-{row['key']}-volct", [(today, row["volume24h"])])
        vol_total += vol_usd
        ct_total += row["volume24h"]
        kept += 1
    if not kept:
        raise RuntimeError("kalshi: no usable prices in top markets")
    # tracked-universe venue total (top-30 by 24h volume), not full venue.
    store.upsert_points("cycle:predvol-kalshi", [(today, vol_total)])
    store.upsert_points("cycle:predvolct-kalshi", [(today, ct_total)])
    # active tracked markets today (for the activity panel).
    store.upsert_points("cycle:predact-kalshi", [(today, kept)])
    try:
        bf = await backfill_kalshi_volume(store, get_text, rows)
        log.info("kalshi backfill: %s", bf.get("status"))
    except Exception as exc:  # noqa: BLE001 — backfill never fails the job
        log.warning("kalshi volume backfill skipped: %s", exc)
    store.put_doc("kalshi", {
        "as_of": today.isoformat(),
        "markets": rows[:TOP_N],
        "note": "Yes-price mid/last snapshot; model inputs only, not trading advice.",
    }, source=SOURCE)
    log.info("kalshi: %d markets, %d series updated", len(rows[:TOP_N]), kept)
    return SOURCE
