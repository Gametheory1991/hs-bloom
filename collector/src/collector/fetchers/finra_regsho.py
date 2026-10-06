"""FINRA Reg SHO daily short-sale volume + OTC threshold list (keyless).

Dataset 1 — Reg SHO daily short volume (flow; complements the biweekly
finra_short.py short-interest snapshot):
  https://cdn.finra.org/equity/regsho/daily/{CNMS,FNYX,FNSQ}shvol{YYYYMMDD}.txt
  CNMS = consolidated, FNYX = NYSE, FNSQ = FINRA TRF (venue labels per FINRA).
  Pipe-delimited, CRLF, header row then a bare record-count trailer:
    Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
  Published daily for the prior trading day; verified HTTP 200 2026-10-04.

Stored (per market m in cnms/fnyx/fnsq):
  cycle:regsho-<m>-shortvol    total short-sale volume (shares)
  cycle:regsho-<m>-shortexempt short-exempt volume (shares)
  cycle:regsho-<m>-shortratio  short volume / total volume (0..1)
plus per-ticker daily short volume (summed across the 3 venues) for the
hyperscaler watchlist (same names as finra_short.py):
  cycle:regsho-short-<TICKER>  — distinct from cycle:short-<TICKER>, which
  holds biweekly short-interest *levels*; this is daily short *flow*.
Snapshot doc "regsho_daily": latest day's aggregates + top-50 tickers by
short volume.

Dataset 2 — OTC Reg SHO threshold list via FINRA's public Query API
(keyless; verified 2026-10-04):
  GET  https://api.finra.org/partitions/group/otcMarket/name/vwthresholdList
  POST https://api.finra.org/data/group/otcMarket/name/vwthresholdList
       {"offset":0,"compareFilters":[{"fieldName":"tradeDate","fieldValue":
       "<YYYY-MM-DD>","compareType":"EQUAL"}],"delimiter":"|","limit":5000,
       "quoteValues":false,"fields":[...]}
Stored: cycle:regsho-threshold-count (securities on the list per trade date)
plus snapshot doc "regsho_threshold" holding the list itself.

Threshold failure degrades gracefully (daily volume still lands); a total
Reg SHO file failure raises so the job surfaces in the health strip.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import date, timedelta

from collector.http import GetText, PostText
from collector.store import Store

import re

log = logging.getLogger(__name__)

SHVOL_URL = "https://cdn.finra.org/equity/regsho/daily/{mkt}shvol{day}.txt"
# (file prefix, config suffix, venue label)
MARKETS = (("CNMS", "cnms", "Consolidated"),
           ("FNYX", "fnyx", "NYSE"),
           ("FNSQ", "fnsq", "FINRA TRF"))

SOURCE = "finra-regsho"
# seconds between CDN requests; cdn.finra.org 403s rapid bursts.
REQUEST_GAP = 1.0
API_GAP = 0.3

# Same watchlist as finra_short.py so flow sits next to snapshot.
TICKERS = ("MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "META")

EXPECTED_HEADER = ["Date", "Symbol", "ShortVolume", "ShortExemptVolume",
                   "TotalVolume", "Market"]

# backfill bounds: one year of trading days, bounded request count
BACKFILL_DAYS = 252
BACKFILL_ATTEMPTS = 400
THRESHOLD_BACKFILL = 60

PARTITIONS_URL = "https://api.finra.org/partitions/group/otcMarket/name/vwthresholdList"
THRESHOLD_URL = "https://api.finra.org/data/group/otcMarket/name/vwthresholdList"
THRESHOLD_FIELDS = ["issueSymbolIdentifier", "issueName",
                    "marketCategoryDescription", "regShoThresholdFlag",
                    "rule4320Flag"]


def parse_shvol(text: str) -> list[dict]:
    """Parse one daily short-volume file -> [{symbol, short, exempt, total}].

    Skips the bare record-count trailer row; raises ValueError on a bad
    header so callers can probe the next date.
    """
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader, None)
    if not header or [c.strip() for c in header[:6]] != EXPECTED_HEADER:
        raise ValueError(f"unexpected Reg SHO header: {header}")
    rows: list[dict] = []
    for row in reader:
        if len(row) < 6:
            continue  # trailer row (bare count)
        try:
            rows.append({
                "symbol": row[1].strip().upper(),
                "short": float(row[2] or 0),
                "exempt": float(row[3] or 0),
                "total": float(row[4] or 0),
            })
        except ValueError:
            continue
    return rows


def aggregate(rows: list[dict]) -> dict:
    """(short, exempt, total, short_ratio) over a set of rows."""
    short = sum(r["short"] for r in rows)
    exempt = sum(r["exempt"] for r in rows)
    total = sum(r["total"] for r in rows)
    return {"short": short, "exempt": exempt, "total": total,
            "ratio": short / total if total > 0 else 0.0}


def parse_threshold(text: str) -> list[dict]:
    """Parse the Query API pipe response -> [{symbol, name, category, flags}]."""
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader, None)
    if not header or "issueSymbolIdentifier" not in "|".join(header):
        raise ValueError(f"unexpected threshold header: {header}")
    out: list[dict] = []
    for row in reader:
        if len(row) < 5:
            continue
        out.append({
            "symbol": row[0].strip().upper(),
            "name": row[1].strip(),
            "category": row[2].strip(),
            "reg_sho": row[3].strip().upper() == "Y",
            "rule4320": row[4].strip().upper() == "Y",
        })
    return out


async def _fetch_day(get_text: GetText, day: date) -> dict[str, list[dict]] | None:
    """Fetch the 3 venue files for one date; None if the day isn't published."""
    ymd = day.strftime("%Y%m%d")
    per_market: dict[str, list[dict]] = {}
    for prefix, suffix, _label in MARKETS:
        try:
            text = await get_text(SHVOL_URL.format(mkt=prefix, day=ymd))
        except Exception as exc:  # noqa: BLE001 — 404 = not published
            log.debug("regsho %s %s: %s", ymd, prefix, exc)
            await asyncio.sleep(REQUEST_GAP)
            continue
        if len(text) < 50:
            await asyncio.sleep(REQUEST_GAP)
            continue
        try:
            per_market[suffix] = parse_shvol(text)
        except ValueError as exc:
            log.debug("regsho %s %s parse: %s", ymd, prefix, exc)
        await asyncio.sleep(REQUEST_GAP)
    return per_market or None


def _store_day(store: Store, day: date, per_market: dict[str, list[dict]]) -> dict:
    """Write aggregate points for one date; return combined ticker rows."""
    for _prefix, suffix, _label in MARKETS:
        rows = per_market.get(suffix)
        if rows is None:
            continue
        agg = aggregate(rows)
        store.upsert_points(f"cycle:regsho-{suffix}-shortvol", [(day, agg["short"])])
        store.upsert_points(f"cycle:regsho-{suffix}-shortexempt", [(day, agg["exempt"])])
        store.upsert_points(f"cycle:regsho-{suffix}-shortratio", [(day, agg["ratio"])])
    combined: dict[str, dict] = {}
    for rows in per_market.values():
        for r in rows:
            c = combined.setdefault(r["symbol"],
                                    {"short": 0.0, "exempt": 0.0, "total": 0.0})
            c["short"] += r["short"]
            c["exempt"] += r["exempt"]
            c["total"] += r["total"]
    for sym in TICKERS:
        if sym in combined:
            store.upsert_points(f"cycle:regsho-short-{sym}",
                                [(day, combined[sym]["short"])])
    # Per-ticker daily history for the day's top-50 shorted names — backs the
    # Top Shorted table's 1D/1W % changes (the snapshot doc alone is one day).
    for sym, v in sorted(combined.items(), key=lambda kv: kv[1]["short"],
                         reverse=True)[:50]:
        safe = re.sub(r"[^A-Z0-9]", "", sym.upper())
        if not safe:
            continue
        store.upsert_points(f"cycle:regsho-top-{safe}-shortvol",
                            [(day, v["short"])])
        store.upsert_points(f"cycle:regsho-top-{safe}-totalvol",
                            [(day, v["total"])])
    return combined


def _pct_chg(pts: dict[date, float], day: date, days: int) -> float | None:
    """% change on `day` vs the nearest prior point at least `days` back."""
    cur = pts.get(day)
    if cur is None:
        return None
    ref = None
    for d in sorted(pts):
        if d < day and (day - d).days >= days:
            ref = d
    if ref is None:
        return None
    prev = pts[ref]
    return (cur - prev) / prev if prev else None


async def _threshold_count(post_text: PostText, trade_date: str) -> list[dict]:
    body = {
        "offset": 0,
        "compareFilters": [{"fieldName": "tradeDate", "fieldValue": trade_date,
                            "compareType": "EQUAL"}],
        "delimiter": "|", "limit": 5000, "quoteValues": False,
        "fields": THRESHOLD_FIELDS,
    }
    text = await post_text(THRESHOLD_URL, json=body)
    if len(text) < 20:
        raise ValueError("empty threshold response")
    return parse_threshold(text)


async def fetch_finra_regsho(store: Store, get_text: GetText,
                             post_text: PostText,
                             today: date | None = None) -> str:
    """Daily: latest Reg SHO short-volume day (+ bounded backfill) and the
    OTC threshold list. Threshold failures degrade gracefully."""
    today = today or date.today()

    # 1) latest published short-volume day: probe back up to 10 days
    latest: tuple[date, dict[str, list[dict]]] | None = None
    for back in range(10):
        day = today - timedelta(days=back)
        per_market = await _fetch_day(get_text, day)
        if per_market:
            latest = (day, per_market)
            break
    if latest is None:
        raise RuntimeError("no Reg SHO short-volume files for the last 10 days")
    day, per_market = latest
    combined = _store_day(store, day, per_market)

    # 2) bounded backfill on an empty-ish store (one year of trading days)
    if len(store.points("cycle:regsho-cnms-shortvol")) < 30:
        filled = 0
        d = day - timedelta(days=1)
        attempts = 0
        while filled < BACKFILL_DAYS and attempts < BACKFILL_ATTEMPTS:
            attempts += 1
            pm = await _fetch_day(get_text, d)
            if pm:
                _store_day(store, d, pm)
                filled += 1
            d -= timedelta(days=1)
        log.info("finra_regsho backfilled %d days (%d attempts)", filled, attempts)

    # 3) latest-day snapshot doc: aggregates + top-50 tickers by short volume
    aggs = {suffix: aggregate(per_market[suffix])
            for _p, suffix, _l in MARKETS if suffix in per_market}
    top50 = sorted(combined.items(), key=lambda kv: kv[1]["short"],
                   reverse=True)[:50]
    # 1D/1W/1M/1Q/1Y % changes per ticker from the stored daily top-50 history
    # (Harry's universal horizon standard, 2026-10-05). Falls back to the
    # watchlist daily short-volume history (252d) where the top-50 series has
    # not accumulated yet; ratio changes need both legs so they stay null.
    _HORIZONS = (("1d", 1), ("1w", 7), ("1m", 30), ("1q", 91), ("1y", 365))

    def _ratio_at(sv: dict, tv: dict, day: date, days: int) -> float | None:
        cands = sorted(d for d in sv
                       if d < day and (day - d).days >= days and d in tv)
        if not cands:
            return None
        s0, t0 = sv[cands[-1]], tv[cands[-1]]
        return s0 / t0 if t0 else None

    def _ticker_chg(sym: str, ratio: float) -> dict:
        safe = re.sub(r"[^A-Z0-9]", "", sym.upper())
        sv = store.points(f"cycle:regsho-top-{safe}-shortvol")
        tv = store.points(f"cycle:regsho-top-{safe}-totalvol")
        if not sv:
            sv = store.points(f"cycle:regsho-short-{safe}")
        out: dict[str, float | None] = {}
        for tag, days in _HORIZONS:
            schg = _pct_chg(sv, day, days)
            out[f"short_chg_{tag}"] = (round(schg, 4)
                                       if schg is not None else None)
            rref = _ratio_at(sv, tv, day, days)
            out[f"ratio_chg_{tag}"] = (round((ratio - rref) / rref, 4)
                                       if rref else None)
        return out
    store.put_doc("regsho_daily", {
        "as_of": day.isoformat(),
        "markets": {suffix: {"label": label, **aggs[suffix]}
                    for prefix, suffix, label in MARKETS if suffix in aggs},
        "top50": [{**{"symbol": sym,
                   "short_volume": round(v["short"], 1),
                   "exempt_volume": round(v["exempt"], 1),
                   "total_volume": round(v["total"], 1),
                   "short_ratio": round(v["short"] / v["total"], 4)
                   if v["total"] > 0 else 0.0},
                  **_ticker_chg(sym, v["short"] / v["total"]
                                if v["total"] > 0 else 0.0)}
                  for sym, v in top50],
        "tickers": {sym: round(combined[sym]["short"], 1)
                    for sym in TICKERS if sym in combined},
    }, source=SOURCE)

    # 4) OTC threshold list — graceful degradation, never blocks the job
    try:
        parts = json.loads(await get_text(PARTITIONS_URL))
        dates = [p["partitions"][0]
                 for p in parts.get("availablePartitions", [])][:THRESHOLD_BACKFILL]
        if not dates:
            raise ValueError("no threshold partitions")
        count_pts: list[tuple[date, float]] = []
        latest_rows: list[dict] = []
        for i, td in enumerate(dates):
            rows = await _threshold_count(post_text, td)
            count_pts.append((date.fromisoformat(td), float(len(rows))))
            if i == 0:
                latest_rows = rows
                store.put_doc("regsho_threshold", {
                    "as_of": td,
                    "count": len(rows),
                    "securities": [{"symbol": r["symbol"], "name": r["name"],
                                    "category": r["category"],
                                    "reg_sho": r["reg_sho"],
                                    "rule4320": r["rule4320"]}
                                   for r in rows],
                }, source=SOURCE)
            await asyncio.sleep(API_GAP)
        store.upsert_points("cycle:regsho-threshold-count", count_pts)
        log.info("finra_regsho threshold: %d securities on %s",
                 len(latest_rows), dates[0])
    except Exception as exc:  # noqa: BLE001 — degrade, keep the daily data
        log.warning("finra_regsho threshold list skipped: %s", exc)

    return SOURCE
