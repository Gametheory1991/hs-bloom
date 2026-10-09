"""SEC XBRL historical backfill for crypto-ETF shares / AUM / NAV.

The iShares/Yahoo feeds only give current snapshots, so daily accumulation
starts now. This job pulls quarter-end history from SEC companyfacts
(free, no auth — descriptive contact UA required, <=10 req/s):

  https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json

Concepts (vary by filer — priority order per metric):
  shares: TemporaryEquitySharesOutstanding > CommonStockSharesOutstanding
          > SharesOutstanding
  aum:    FairValueNetAssetLiability (else derived as shares x NAV)
  nav:    NetAssetValuePerShare

Dedup: XBRL repeats each period-end in every later filing; we keep the
earliest-filed value per period-end.

Stored as quarterly checkpoints in the SAME series the daily job writes
(cycle:etf-{T}-shares / -aum / -nav). The UI's flow derivation skips
intervals > 10 days, so these sparse checkpoints extend the AUM/share
history without polluting daily flow math.

DoltHub is a possible future source for historical *options* chains —
not built here (CBOE has no history; daily aggregates accumulate going
forward).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from collector.http import GetText

log = logging.getLogger(__name__)

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

# ticker -> 10-digit CIK (verified 2026-10-06 via SEC company_tickers.json;
# ETHA is 2000638, not 2013710). DEFI (Hashdex) has no SEC ticker mapping —
# CoinLaw covers it weekly instead.
ETF_CIKS: dict[str, str] = {
    "IBIT": "0001980994", "ETHA": "0002000638", "ETHB": "0002099103",
    "GBTC": "0001588489", "BTC": "0002015034",
    "FBTC": "0001852317", "FETH": "0002000046",
    "BITB": "0001763415", "ETHW": "0002013744",
    "ARKB": "0001869699", "HODL": "0001838028",
    "BRRR": "0001841175", "EZBC": "0001992870",
    "BTCO": "0001855781", "BTCW": "0001850391",
    "ETHE": "0001725210", "ETH": "0002020455",
    "TETH": "0001992508", "EZET": "0002011535",
    "ETHV": "0001860788", "QETH": "0001995569",
}

SHARE_CONCEPTS = ("TemporaryEquitySharesOutstanding",
                  "CommonStockSharesOutstanding", "SharesOutstanding")
AUM_CONCEPT = "FairValueNetAssetLiability"
NAV_CONCEPT = "NetAssetValuePerShare"


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _concept_points(facts: dict, concept: str) -> list[tuple[date, float]]:
    """Earliest-filed value per period-end for a us-gaap concept."""
    entry = (facts.get("us-gaap") or {}).get(concept)
    if not entry:
        return []
    best: dict[date, tuple[str, float]] = {}
    for unit_pts in entry.get("units", {}).values():
        for p in unit_pts:
            try:
                end = date.fromisoformat(str(p.get("end"))[:10])
            except (TypeError, ValueError):
                continue
            val = _f(p.get("val"))
            if val is None:
                continue
            filed = str(p.get("filed") or "")
            if end not in best or filed < best[end][0]:
                best[end] = (filed, val)
    return sorted((d, v) for d, (_, v) in best.items())


def extract_history(payload: dict) -> dict[str, list[tuple[date, float]]]:
    """{shares, aum, nav} quarterly checkpoints from a companyfacts payload."""
    facts = payload.get("facts") or {}
    shares: list[tuple[date, float]] = []
    for c in SHARE_CONCEPTS:
        shares = _concept_points(facts, c)
        if shares:
            break
    aum = _concept_points(facts, AUM_CONCEPT)
    nav = _concept_points(facts, NAV_CONCEPT)
    if not aum and shares and nav:
        # derive AUM = shares x NAV on matching quarter-ends
        nav_by_d = dict(nav)
        aum = [(d, s * nav_by_d[d]) for d, s in shares if d in nav_by_d]
    return {"shares": shares, "aum": aum, "nav": nav}


async def fetch_sec_xbrl_etf(store, get_text: GetText,
                             user_agent: str) -> str:
    """Backfill quarterly ETF checkpoints from SEC XBRL. Returns status."""
    from collector.store import Store
    assert isinstance(store, Store)
    headers = {"User-Agent": user_agent}
    done = fell = 0
    pts_total = 0
    for ticker, cik in ETF_CIKS.items():
        try:
            payload = json.loads(
                await get_text(FACTS_URL.format(cik10=cik), headers=headers))
        except Exception as exc:  # noqa: BLE001 — one bad CIK skips
            log.warning("sec_xbrl_etf: %s (%s) failed: %s", ticker, cik, exc)
            fell += 1
            continue
        hist = extract_history(payload)
        for metric, pts in hist.items():
            if pts:
                # Quarterly XBRL checkpoints live in their own -q- namespace so they
                # never collide with the daily iShares series (cycle:etf-{t}-{metric}).
                store.upsert_points(f"cycle:etf-{ticker}-q-{metric}", pts)
                pts_total += len(pts)
        done += 1
        await asyncio.sleep(0.3)  # SEC fair access: well under 10 req/s
    return (f"sec_xbrl_etf: {done}/{len(ETF_CIKS)} funds, "
            f"{pts_total} quarterly checkpoints, {fell} failed")


async def repair_etf_collision(store, get_text: GetText,
                               user_agent: str) -> dict:
    """One-time repair: remove quarterly XBRL points that were written into the
    daily iShares series (cycle:etf-{t}-{metric}) before the -q- namespace fix.
    Only deletes a date when the stored value exactly matches the XBRL value,
    so legitimate daily iShares points are never touched. Returns a report."""
    from collector.store import Store
    assert isinstance(store, Store)
    headers = {"User-Agent": user_agent}
    report: dict = {"tickers": {}, "total_deleted": 0, "failed": []}
    for ticker, cik in ETF_CIKS.items():
        try:
            payload = json.loads(
                await get_text(FACTS_URL.format(cik10=cik), headers=headers))
        except Exception as exc:  # noqa: BLE001
            report["failed"].append(ticker)
            continue
        hist = extract_history(payload)
        deleted_here = 0
        for metric, pts in hist.items():
            if not pts:
                continue
            sid = f"cycle:etf-{ticker}-{metric}"
            daily = store.points(sid)
            # Only delete dates where the stored value IS the XBRL value.
            kill = [d for d, v in pts
                    if d in daily and daily[d] == v]
            if kill:
                deleted_here += store.delete_points(sid, kill)
        report["tickers"][ticker] = deleted_here
        report["total_deleted"] += deleted_here
        await asyncio.sleep(0.3)
    return report
