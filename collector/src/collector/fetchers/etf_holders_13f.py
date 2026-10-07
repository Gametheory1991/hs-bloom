"""Inverse 13F: for each ETF in the tracked universe, WHO HOLDS IT.

Bloomberg Holdings-tab style: given a ticker, list the institutional
holders (Form 13F filers) with shares, change, % of shares outstanding,
and market value. This is the inverse of thirteenf.py (which starts from
a filer CIK and lists what they hold).

Primary source -- FMP (free key in FMP_API_KEY env var, 250 calls/day):
  GET https://financialmodelingprep.com/stable/institutional-ownership/extract-analytics/holder
    ?symbol={T}&year={Y}&quarter={Q}&page=0&limit=100
  Rows carry investorName, cik, sharesNumber, changeInSharesNumber,
  ownership (%), marketValue, filingDate, date, isNew/isSoldOut.

Fallback (same key): the legacy
  GET https://financialmodelingprep.com/api/v3/institutional-holder/{T}
  used only when the stable path fails with a plan-tier error (402/403);
  it carries no year/quarter filter, so rows are attributed to the current
  target quarter and flagged `via_fallback: true`.

Endpoint verification 2026-10-07: the stable path is documented on
site.financialmodelingprep.com and in the FMP developer docs
("Filings Extract With Analytics By Holder"). FMP's own docs FAQ places
the institutional-ownership family in the Ultimate plan tier, so
free-tier access for Harry's key is NOT guaranteed; the v3 legacy path is
kept as the free-tier escape hatch. Nasdaq's api.nasdaq.com
institutional-holdings endpoints were probed the same day and are
bot-blocked for server-side use (HTTP 404/empty from this host), so they
are not a viable scheduler fallback and are not used -- do not add them.

13F has a 45-day reporting lag: the job targets the most recent quarter
whose end + 45 days has passed (e.g. on 2026-10-07 the target is Q2 2026,
not Q3 2026).

Rate limit / resume: 1 request per symbol-quarter. The ~240-ticker
universe (and growing) is spread across runs: each run processes at most
DAILY_BUDGET symbols, keeping each run's FMP spend under the 250/day free
budget (room left for the ishares_etf FMP fallback, ~28/day). Progress is
marked in the store
(doc key etfholders:{TICKER}:{Y}Q{Q}); a rerun skips completed
symbol-quarters, so killed/partial runs resume cleanly. A new target
quarter resets progress naturally because the doc keys are quarter-scoped.

Stored per symbol-quarter (doc key etfholders:{T}:{Y}Q{Q}):
  holders[] (top TOP_N=50 by sharesNumber desc) with {investorName, cik,
  sharesNumber, changeInSharesNumber, ownership, marketValue, filingDate}
  plus summary {inst_shares_held, pct_of_os, n_holders, n_buyers,
  n_sellers, pct_chg_inst (vs the previous stored quarter, else null),
  asof, source}.

Aggregate doc "etfholders": per-symbol latest-quarter summary (powers the
ETF picker and the 6 Bloomberg columns), the target quarter, coverage
counts, and a note. Quarterly time-series points:
  cycle:etfhold-{T}-instpct  -- Inst Holdings % of o/s
  cycle:etfhold-{T}-n        -- Num of Inst Holders
dated at quarter-end.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date
from urllib.parse import quote

from collector.fetchers.ishares_etf import ETF_UNIVERSE
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

STABLE_URL = (
    "https://financialmodelingprep.com/stable/institutional-ownership/"
    "extract-analytics/holder"
)
V3_URL = "https://financialmodelingprep.com/api/v3/institutional-holder"

TOP_N = 50          # holders stored per ETF per quarter
PAGE_LIMIT = 100    # rows requested per call (one call per symbol-quarter)
DAILY_BUDGET = 200  # symbols per run; leaves headroom under FMP free 250/day
PAUSE = 0.3         # seconds between FMP calls
FILING_LAG_DAYS = 45


# ---------------------------------------------------------------------------
# quarter targeting (45-day 13F lag)
# ---------------------------------------------------------------------------

def quarter_end(year: int, quarter: int) -> date:
    month = {1: 3, 2: 6, 3: 9, 4: 12}[quarter]
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return first_next - date.resolution


def target_quarter(today: date) -> tuple[int, int]:
    """Most recent quarter whose end + 45 days has passed."""
    y, q = today.year, (today.month - 1) // 3 + 1
    while True:
        end = quarter_end(y, q)
        if (end.toordinal() + FILING_LAG_DAYS) <= today.toordinal():
            return y, q
        q -= 1
        if q == 0:
            y, q = y - 1, 4


def _qkey(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def _doc_key(symbol: str, year: int, quarter: int) -> str:
    return f"etfholders:{symbol}:{_qkey(year, quarter)}"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def normalize_holder(row: dict, via_fallback: bool = False) -> dict | None:
    """One FMP holder row -> the stored shape. None if unusable."""
    shares = _f(row.get("sharesNumber"))
    if shares is None:
        return None
    return {
        "investorName": row.get("investorName") or "Unknown",
        "cik": str(row.get("cik") or "").zfill(10) if row.get("cik") else None,
        "sharesNumber": int(shares),
        "changeInSharesNumber": (
            int(c) if (c := _f(row.get("changeInSharesNumber"))) is not None else None
        ),
        "ownership": _f(row.get("ownership")),          # % of shares outstanding
        "marketValue": _f(row.get("marketValue")),       # $
        "filingDate": row.get("filingDate"),
        "via_fallback": via_fallback,
    }


def parse_stable(text: str) -> list[dict]:
    """Parse the stable extract-analytics/holder payload (list of rows)."""
    data = json.loads(text)
    if isinstance(data, dict):  # FMP sometimes wraps in {"holders": [...]}
        data = data.get("holders") or data.get("data") or []
    holders = [h for h in (normalize_holder(r) for r in data) if h]
    holders.sort(key=lambda h: -h["sharesNumber"])
    return holders[:TOP_N]


def parse_v3(text: str) -> list[dict]:
    """Parse the legacy v3 institutional-holder payload (latest quarter)."""
    data = json.loads(text)
    holders = [h for h in (normalize_holder(r, via_fallback=True) for r in data) if h]
    holders.sort(key=lambda h: -h["sharesNumber"])
    return holders[:TOP_N]


def summarize(holders: list[dict], prev_inst_shares: float | None) -> dict:
    """The 6 Bloomberg Holdings-tab columns for one symbol-quarter."""
    inst_shares = sum(h["sharesNumber"] for h in holders)
    pct_of_os = sum(h["ownership"] for h in holders if h["ownership"] is not None)
    buyers = sum(1 for h in holders if (h["changeInSharesNumber"] or 0) > 0)
    sellers = sum(1 for h in holders if (h["changeInSharesNumber"] or 0) < 0)
    pct_chg = None
    if prev_inst_shares:
        pct_chg = (inst_shares / prev_inst_shares - 1) * 100
    return {
        "inst_shares_held": inst_shares,
        "pct_chg_inst": pct_chg,          # % change of inst holdings vs prev quarter
        "pct_of_os": pct_of_os,           # inst holdings % of o/s shares
        "n_holders": len(holders),
        "n_buyers": buyers,
        "n_sellers": sellers,
    }


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------

def _plan_blocked(exc: Exception) -> bool:
    """FMP raises HTTP 402/403 (query stripped from the message) when the
    key's plan does not cover an endpoint."""
    msg = str(exc)
    return "HTTP 402" in msg or "HTTP 403" in msg


async def fetch_one(
    symbol: str, year: int, quarter: int, key: str, get_text: GetText
) -> list[dict]:
    """Holder rows for one symbol-quarter; stable primary, v3 fallback."""
    try:
        text = await get_text(
            STABLE_URL,
            params={"symbol": symbol, "year": year, "quarter": quarter,
                    "page": 0, "limit": PAGE_LIMIT, "apikey": key},
        )
        return parse_stable(text)
    except Exception as exc:  # noqa: BLE001 -- plan-blocked -> try legacy
        if not _plan_blocked(exc):
            raise
        log.info("etf_holders_13f: stable endpoint plan-blocked for %s (%s); "
                 "trying legacy v3", symbol, exc)
        text = await get_text(f"{V3_URL}/{quote(symbol)}",
                              params={"apikey": key})
        return parse_v3(text)


def _prev_inst_shares(store: Store, symbol: str, year: int, quarter: int) -> float | None:
    """inst_shares_held from the most recent stored quarter before (y, q)."""
    y, q = year, quarter
    for _ in range(8):  # look back up to 2 years
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        doc = store.doc(_doc_key(symbol, y, q))
        if doc is not None:
            s = (doc.payload or {}).get("summary") or {}
            return s.get("inst_shares_held")
    return None


async def fetch_etf_holders_13f(store: Store, get_text: GetText) -> str:
    """Quarterly inverse-13F pull for the tracked ETF universe.

    Targets the most recent quarter past the 45-day filing lag; skips
    symbol-quarters already stored (resume-safe); spreads work across
    runs at DAILY_BUDGET symbols per run. Returns a status string.
    """
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise RuntimeError(
            "FMP_API_KEY is not set; etf_holders_13f needs the FMP free key "
            "(Render env var). Set it and rerun."
        )
    today = date.today()
    year, quarter = target_quarter(today)
    qend = quarter_end(year, quarter)
    universe = [t for t, _ in ETF_UNIVERSE]

    todo = [t for t in universe if store.doc(_doc_key(t, year, quarter)) is None]
    batch = todo[:DAILY_BUDGET]
    log.info("etf_holders_13f: target %s, %d/%d symbol-quarters pending, "
             "processing %d this run", _qkey(year, quarter),
             len(todo), len(universe), len(batch))

    done, failed = 0, []
    summaries: dict[str, dict] = {}
    for symbol in batch:
        try:
            holders = await fetch_one(symbol, year, quarter, key, get_text)
        except Exception as exc:  # noqa: BLE001 -- per-symbol isolation
            failed.append(f"{symbol}: {exc}")
            continue
        prev = _prev_inst_shares(store, symbol, year, quarter)
        summary = summarize(holders, prev)
        store.put_doc(
            _doc_key(symbol, year, quarter),
            {
                "symbol": symbol,
                "year": year,
                "quarter": quarter,
                "asof": qend.isoformat(),
                "holders": holders,
                "summary": summary,
                "source": "fmp",
                "note": ("Quarterly Form 13F filings; up to 45-day reporting "
                         "lag; institutions with >$100M discretionary AUM "
                         "only (no retail)."),
            },
            "fmp",
        )
        store.upsert_points(f"cycle:etfhold-{symbol}-instpct",
                            [(qend, summary["pct_of_os"])])
        store.upsert_points(f"cycle:etfhold-{symbol}-n",
                            [(qend, float(summary["n_holders"]))])
        summaries[symbol] = {"year": year, "quarter": quarter, **summary}
        done += 1
        await asyncio.sleep(PAUSE)

    # refresh the aggregate doc (picker + Bloomberg columns) from everything
    # stored for the target quarter, not just this run's batch
    symbols: dict[str, dict] = {}
    covered = 0
    for symbol in universe:
        doc = store.doc(_doc_key(symbol, year, quarter))
        if doc is None:
            continue
        s = (doc.payload or {}).get("summary") or {}
        symbols[symbol] = {"year": year, "quarter": quarter, **s}
        covered += 1
    store.put_doc(
        "etfholders",
        {
            "target": _qkey(year, quarter),
            "target_quarter_end": qend.isoformat(),
            "symbols": symbols,
            "coverage": {
                "covered": covered,
                "universe": len(universe),
                "pending": len(universe) - covered,
            },
            "note": ("Inverse 13F: institutional holders per ETF. Quarterly "
                     "Form 13F filings, up to 45-day reporting lag. "
                     "Institutions only: Form 13F filers are managers with "
                     ">$100M discretionary AUM; retail and non-filing "
                     "holders are not included."),
            "asof": today.isoformat(),
        },
        "fmp",
    )
    if failed and not done:
        raise RuntimeError(
            f"etf_holders_13f: all {len(failed)} attempted symbol-quarters "
            f"failed: {'; '.join(failed)}"
        )
    if failed:
        log.warning("etf_holders_13f: %d failures: %s", len(failed),
                    "; ".join(failed))
    return f"etf_holders_13f:{done}"
