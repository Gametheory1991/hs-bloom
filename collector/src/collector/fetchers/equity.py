"""Equity indexes, from Yahoo's keyless chart API.

(Stooq was a fallback until it put its CSV endpoint behind a JavaScript
proof-of-work wall; it returned HTTP 200 with an HTML challenge body rather
than an error, so it failed at parse time and never served a quote. Removed
2026-09-06 along with an optional IBKR path that returned data flagged just as
delayed as Yahoo's, for no gain.)

Writes daily closes to series 'idx:{symbol}' and the latest quotes to the
'equity_quotes' doc: {symbol: {last, ts, source, delayed}}.
"""
from __future__ import annotations

import logging
from datetime import date

from collector.config import IndexCfg
from collector.fetchers import yahoo
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

# Thin-history bootstrap: the per-run pull is a 5d incremental range; a full
# 1y pull runs only when the stored series has fewer points than this
# (preserves the full-history bar), then idempotent upserts keep it topped up.
EQUITY_THIN_POINTS = 30


async def fetch_equity(indexes: list[IndexCfg], store: Store, get_text: GetText) -> str:
    sources_used: set[str] = set()
    # Stale beats gone: seed with the previous doc so a symbol that fails every
    # source this run keeps its last-known quote (its old ts marks it stale).
    # Symbols removed from config are dropped, not carried forward forever.
    wanted = {i.symbol for i in indexes}
    prev = store.doc("equity_quotes")
    quotes: dict[str, dict] = (
        {s: q for s, q in prev.payload.items() if s in wanted} if prev else {}
    )
    fetched = 0
    errors: list[str] = []

    for idx in indexes:
        if idx.yahoo:
            try:
                # Incremental 5d pull once the series is populated; full 1y
                # bootstrap when stored history is thin/missing. If the last
                # stored date is stale (>7d gap, e.g. after an outage), pull
                # 3mo to fill the gap instead of leaving it permanent.
                try:
                    pts = store.points(f"idx:{idx.symbol}")
                    thin = len(pts) < EQUITY_THIN_POINTS
                    if not thin and pts:
                        gap_days = (date.today() - max(pts)).days
                        range_ = "3mo" if gap_days > 7 else "5d"
                    else:
                        range_ = "1y" if thin else "5d"
                except Exception:  # noqa: BLE001 — degrade to bootstrap
                    range_ = "1y"
                chart = await yahoo.fetch_chart(idx.yahoo, get_text, range_=range_)
                store.upsert_points(f"idx:{idx.symbol}", chart.closes)
                last_d, last_v = chart.closes[-1]
                quotes[idx.symbol] = {
                    "last": chart.last if chart.last is not None else last_v,
                    "ts": chart.last_ts or f"{last_d.isoformat()}T00:00:00Z",
                    "source": "yahoo", "delayed": True,
                }
                sources_used.add("yahoo")
                fetched += 1
                continue
            except Exception as exc:  # noqa: BLE001 — one dead symbol must not kill the run
                errors.append(f"{idx.symbol}: {exc}")
        else:
            errors.append(f"{idx.symbol}: no source configured")

    if fetched == 0:
        raise RuntimeError(f"all equity symbols failed: {'; '.join(errors)}")
    store.put_doc("equity_quotes", quotes, source="+".join(sorted(sources_used)))
    return "+".join(sorted(sources_used))
