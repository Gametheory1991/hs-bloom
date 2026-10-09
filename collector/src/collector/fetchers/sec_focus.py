"""Broker-dealer financials via SEC EDGAR (FOCUS alternative).

HONEST LIMITATION: FOCUS Reports (Financial and Operational Combined Uniform
Single Reports) are filed by broker-dealers with FINRA via the Firm Gateway,
NOT on public EDGAR. There is NO public structured API for firm-level FOCUS
data (net capital, aggregate indebtedness, etc.). FINRA does not publish
individual firm FOCUS filings in machine-readable form.

What IS available:
  - Public broker-dealers (Schwab, Interactive Brokers, etc.) file 10-K/10-Q
    on EDGAR with full balance sheets. We pull key dealer-relevant metrics
    via the EDGAR companyfacts API (keyless, JSON).
  - This gives us: total assets, total liabilities, equity, repo/reverse repo
    (where disclosed), inventory — the dealer balance sheet footprint.

This is NOT a FOCUS replacement (no net capital, no 15c3-1 computations),
but it's the best freely available proxy for dealer financial health.

Public broker-dealers tracked (CIKs):
  - Charles Schwab (SCHW): 0000316709
  - Interactive Brokers (IBKR): 0001381197
  - Raymond James (RJF): 0000720005
  - Stifel (SF): 0000720672
  - Jefferies (JEF): 0001084587

Metrics per firm (quarterly, from 10-Q/10-K):
  - Total assets, total liabilities, stockholders' equity
  - Leverage ratio (assets / equity)

Time series:
  cycle:bd-{ticker}-assets      — total assets ($bn)
  cycle:bd-{ticker}-equity      — stockholders' equity ($bn)
  cycle:bd-{ticker}-leverage    — assets / equity ratio

Docs:
  brokerdealer:{ticker}:{YYYY}Q{Q} — quarterly financials

Source: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json
SEC fair-access: descriptive User-Agent, 0.5s pause between calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "sec-edgar-bd"
PAUSE = 0.5

# Public broker-dealers: (ticker, CIK-10, name)
BROKER_DEALERS = [
    ("SCHW", "0000316709", "Charles Schwab Corp"),
    ("IBKR", "0001381197", "Interactive Brokers Group"),
    ("RJF", "0000720005", "Raymond James Financial"),
    ("SF", "0000720672", "Stifel Financial Corp"),
    ("JEF", "0001084587", "Jefferies Financial Group"),
]

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# US-GAAP tags we care about (in priority order)
ASSET_TAGS = ["Assets", "AssetsCurrent"]
LIABILITY_TAGS = ["Liabilities", "LiabilitiesCurrent"]
EQUITY_TAGS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "PartnerCapital",
]


def _latest_quarterly_point(facts: dict, tags: list[str]) -> tuple[str, float] | None:
    """Get the most recent 10-Q/10-K point for the given tags.

    Returns (end_date_str, value) or None.
    """
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    best = None
    for tag in tags:
        units = (us_gaap.get(tag) or {}).get("units") or {}
        for unit, points in units.items():
            if unit != "USD":
                continue
            for p in points:
                form = p.get("form", "")
                if form not in ("10-Q", "10-K"):
                    continue
                end = p.get("end", "")
                val = p.get("val")
                if not end or val is None:
                    continue
                if best is None or end > best[0]:
                    best = (end, float(val), form)
    if best:
        return (best[0], best[1])
    return None


def _quarter_from_date(end: str) -> str:
    """2026-09-30 -> 2026Q3."""
    y, m = int(end[:4]), int(end[5:7])
    q = (m - 1) // 3 + 1
    return f"{y}Q{q}"


async def fetch_broker_dealers(
    store: Store,
    get_text: GetText,
    today: date | None = None,
) -> str:
    """Quarterly pull of public broker-dealer balance sheets from EDGAR.

    NOTE: This is NOT FOCUS data (see module docstring for why FOCUS
    is not publicly available in structured form). This pulls 10-Q/10-K
    balance sheets for public BDs as the best free proxy.
    """
    today = today or date.today()
    done = 0
    for ticker, cik, name in BROKER_DEALERS:
        url = COMPANYFACTS_URL.format(cik=cik)
        try:
            text = await get_text(url)
        except Exception as e:
            log.warning("brokerdealer %s: fetch failed: %s", ticker, e)
            await asyncio.sleep(PAUSE)
            continue

        try:
            facts = json.loads(text)
        except json.JSONDecodeError:
            log.warning("brokerdealer %s: bad JSON", ticker)
            await asyncio.sleep(PAUSE)
            continue

        assets = _latest_quarterly_point(facts, ASSET_TAGS)
        liabs = _latest_quarterly_point(facts, LIABILITY_TAGS)
        equity = _latest_quarterly_point(facts, EQUITY_TAGS)

        if not assets or not equity:
            log.warning("brokerdealer %s: missing assets/equity", ticker)
            await asyncio.sleep(PAUSE)
            continue

        end_date, assets_usd = assets
        _, equity_usd = equity
        q = _quarter_from_date(end_date)
        asof = date(int(end_date[:4]), int(end_date[5:7]), int(end_date[8:10]))

        doc_key = f"brokerdealer:{ticker}:{q}"
        if store.doc(doc_key) is None:
            leverage = assets_usd / equity_usd if equity_usd else None
            payload = {
                "ticker": ticker,
                "name": name,
                "cik": cik,
                "quarter": q,
                "period_end": end_date,
                "total_assets_usd": assets_usd,
                "total_liabilities_usd": liabs[1] if liabs else None,
                "equity_usd": equity_usd,
                "leverage_ratio": leverage,
                "source": SOURCE,
                "note": "10-Q/10-K balance sheet; NOT FOCUS net capital data",
            }
            store.put_doc(doc_key, payload, SOURCE)

            # Time series
            store.upsert_points(f"cycle:bd-{ticker}-assets", [(asof, assets_usd / 1e9)])
            store.upsert_points(f"cycle:bd-{ticker}-equity", [(asof, equity_usd / 1e9)])
            if leverage:
                store.upsert_points(f"cycle:bd-{ticker}-leverage", [(asof, leverage)])
            done += 1

        await asyncio.sleep(PAUSE)

    return f"brokerdealer: {done} firms updated"
