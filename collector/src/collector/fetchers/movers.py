"""Single-stock sigma movers: top-10 positive/negative 5-day and 20-day
standard-deviation price moves for S&P 500 and Nasdaq-100 constituents.

Replicates the tasty*live* "largest std-dev moves" visual Harry follows.

Data: constituent lists are vendored (collector/data/sp500.txt, ndx100.txt —
SPX list from a daily-updated public CSV, NDX from Wikipedia's constituent
page; both verified 2026-10-03). Prices come from the Yahoo chart API
(range=6mo, daily), one request per symbol, run WEEKLY (Saturday) with polite
spacing because Yahoo throttles aggressively. Per-symbol isolation: a dead
symbol or a 429 degrades that symbol only, never the job.

Method per symbol:
  - daily log returns x100 over the trailing window
  - 5d window: 5d return / trailing-20d std of daily returns  -> z5
  - 20d window: 20d return / trailing-60d std of daily returns -> z20
  - rank all constituents per (index, window); keep top-10 positive and
    top-10 negative by |z|

Writes doc "movers": {asof, indexes: {spx: {win5d: {up, down}, win20d: ...},
ndx: {...}}, all: {SYM: {idx, z5, ret5, z20, ret20}}, dispersion_5d}.
Each top-10 entry: {symbol, z, ret_pct}. dispersion_5d (std of 5d log-returns
across the scored universe) is also upserted to history
"movers:dispersion-5d" for percentile ranking. Also upserts the weekly run
date to history "movers:asof" so the UI can show staleness.

A symbol needs >=65 daily closes or it is skipped (60d trailing sigma + 20d
return); splits/dividends are NOT adjusted — Yahoo serves split-adjusted
closes by default on the chart endpoint, which is what we want.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone
from importlib.resources import files
from statistics import pstdev

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "movers-weekly"
DOC_KEY = "movers"

# (index id, label, data file). Files are vendored; membership is a snapshot.
INDEXES: list[tuple[str, str, str]] = [
    ("spx", "S&P 500", "sp500.txt"),
    ("ndx", "Nasdaq 100", "ndx100.txt"),
]

RANGE = "6mo"        # enough for 60d trailing sigma + 20d return
MIN_CLOSES = 65      # 60 trailing + a few spare
TOP_N = 10
POLITE_GAP = 0.35    # seconds between Yahoo requests (weekly run: ~3.5 min)


def _load_tickers(filename: str) -> list[str]:
    try:
        text = files("collector.data").joinpath(filename).read_text()
    except Exception as exc:  # noqa: BLE001 — missing data file degrades the index
        log.warning("movers: cannot read %s: %s", filename, exc)
        return []
    return [line.strip().upper() for line in text.splitlines() if line.strip()]


def _zscore_moves(closes: list[tuple[date, float]]) -> dict | None:
    """z5 (5d move / 20d sigma) and z20 (20d move / 60d sigma), or None when
    the history is too short or degenerate."""
    if len(closes) < MIN_CLOSES:
        return None
    ordered = sorted(closes)
    rets: list[tuple[date, float]] = []
    for (d0, v0), (d1, v1) in zip(ordered, ordered[1:]):
        if v0 > 0 and v1 > 0:
            try:
                r = math.log(v1 / v0) * 100.0
            except (ValueError, TypeError):
                continue
            if math.isfinite(r):
                rets.append((d1, r))
    if len(rets) < 61:
        return None
    vals = [r for _, r in rets]

    def zscore(ret_days: int, sigma_days: int) -> tuple[float, float] | None:
        if len(vals) < ret_days + sigma_days:
            return None
        move = sum(vals[-ret_days:])
        sigma = pstdev(vals[-(ret_days + sigma_days):-ret_days])
        if sigma <= 0:
            return None
        return move / sigma, move

    z5 = zscore(5, 20)
    z20 = zscore(20, 60)
    if z5 is None or z20 is None:
        return None
    return {"z5": round(z5[0], 2), "ret5": round(z5[1], 2),
            "z20": round(z20[0], 2), "ret20": round(z20[1], 2)}


async def _fetch_chart(symbol: str, get_text: GetText):
    """Local import: the staging tree carries only batch files, and the real
    yahoo module lives on live main. Import lazily so tests can inject a stub
    via sys.modules, same pattern as test_cycle_yahoo.py."""
    from collector.fetchers import yahoo  # noqa: PLC0415
    return await yahoo.fetch_chart(symbol, get_text, range_=RANGE)


async def _one_symbol(symbol: str, get_text: GetText) -> tuple[str, dict] | None:
    try:
        quote = await _fetch_chart(symbol, get_text)
        moves = _zscore_moves(quote.closes)
    except Exception as exc:  # noqa: BLE001 — per-symbol isolation
        log.debug("movers: %s skipped: %s", symbol, exc)
        return None
    if moves is None:
        return None
    return symbol, moves


async def fetch_movers(store: Store, get_text: GetText,
                       today: date | None = None) -> str:
    """Weekly job: rank sigma-movers for SPX + NDX, store the top-10 lists."""
    today = today or datetime.now(timezone.utc).date()
    payload: dict = {"asof": today.isoformat(), "source": SOURCE, "indexes": {}}
    universe: dict[str, dict] = {}
    disp_rets: list[float] = []
    for idx_id, label, filename in INDEXES:
        tickers = _load_tickers(filename)
        scored: list[tuple[str, dict]] = []
        for i, symbol in enumerate(tickers):
            res = await _one_symbol(symbol, get_text)
            if res is not None:
                scored.append(res)
            if (i + 1) % 25 == 0:
                log.info("movers: %s %d/%d symbols", idx_id, i + 1, len(tickers))
            await asyncio.sleep(POLITE_GAP)
        for symbol, m in scored:
            universe[symbol] = {"idx": idx_id, "z5": m["z5"], "ret5": m["ret5"],
                                "z20": m["z20"], "ret20": m["ret20"]}
            disp_rets.append(m["ret5"])
        up5 = sorted((s for s in scored if s[1]["z5"] > 0),
                     key=lambda s: -s[1]["z5"])[:TOP_N]
        dn5 = sorted((s for s in scored if s[1]["z5"] < 0),
                     key=lambda s: s[1]["z5"])[:TOP_N]
        up20 = sorted((s for s in scored if s[1]["z20"] > 0),
                      key=lambda s: -s[1]["z20"])[:TOP_N]
        dn20 = sorted((s for s in scored if s[1]["z20"] < 0),
                      key=lambda s: s[1]["z20"])[:TOP_N]

        # explicit packing per window
        def pack5(rows):
            return [{"symbol": s, "z": m["z5"], "ret_pct": m["ret5"]} for s, m in rows]

        def pack20(rows):
            return [{"symbol": s, "z": m["z20"], "ret_pct": m["ret20"]} for s, m in rows]

        payload["indexes"][idx_id] = {
            "label": label,
            "n_scored": len(scored),
            "n_universe": len(tickers),
            "win5d": {"up": pack5(up5), "down": pack5(dn5)},
            "win20d": {"up": pack20(up20), "down": pack20(dn20)},
        }
    # cross-sectional dispersion (std of 5d log-returns across the scored
    # universe) — feeds the radar breadth cell; also stored as history so the
    # radar can percentile-rank it. The full universe map powers single-stock
    # lookup in chat.
    payload["all"] = universe
    if len(disp_rets) >= 20:
        disp = round(pstdev(disp_rets), 2)
        payload["dispersion_5d"] = disp
        store.upsert_points("movers:dispersion-5d", [(today, disp)])
    store.put_doc(DOC_KEY, payload, source=SOURCE)
    store.upsert_points("movers:asof", [(today, 1.0)])
    return "movers"
