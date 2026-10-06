"""Ticker master mapping — dynamic company name + GICS sector resolver.

The Reg SHO short-volume files carry no company names. The frontend used to
rely on a static TICKER_META map, but heavily-shorted small caps and
leveraged/inverse ETFs rotate in and out of the top-shorted list faster than
a hardcoded list can track. This module resolves unknown tickers on demand
and caches the results in the `ticker_master` store doc (persisted in the
DB, so it accumulates over time).

Resolution order per unknown ticker:
  1. Finnhub stock/profile2 (FINNHUB_API_KEY env) — name + finnhubIndustry,
     which maps onto GICS sectors below.
  2. OpenFIGI mapping (OPENFIGI_API_KEY env) — name; securityType hints at
     ETF vs equity for the sector bucket.
  3. Unresolved — left out of the cache; the frontend shows "—".

API keys are read from the environment only and never stored. Calls are
paced politely (Finnhub free tier: 60/min; OpenFIGI keyed tier: 25/min —
batched 25/request with 3s gaps, same as the openfigi fetcher).

The SEED below bootstraps the cache on a fresh DB: the long-standing static
frontend map plus names resolved 2026-10-05 for the then-current top-shorted
list (SEC company_tickers.json for equities, Yahoo Finance chart meta for
ETFs, which the SEC file doesn't cover).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date

from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "ticker-master"
DOC_KEY = "ticker_master"

FINNHUB_PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

FINNHUB_GAP = 1.2   # 60/min free tier; stay well under
OPENFIGI_BATCH = 25
OPENFIGI_GAP = 3.0

# finnhubIndustry -> GICS sector (best effort; Finnhub's taxonomy is close
# to GICS but not identical).
FINNHUB_INDUSTRY_TO_GICS = {
    "Technology": "Technology",
    "Healthcare": "Healthcare",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Communication Services": "Communication Services",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Basic Materials": "Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}

# Substrings that mark a resolved name as an ETF/ETN rather than an equity.
ETF_HINTS = (" ETF", " ETN", "Shares", " 2X", " 3X", "2x ", "3x ",
             "Inverse", "Bear ", "Bull ", "Leveraged", "ProShares",
             "Direxion", "GraniteShares", "T-Rex")

# Seed: [symbol, name, GICS sector]. Merged under anything already cached.
SEED: tuple[tuple[str, str, str], ...] = (
    # resolved 2026-10-05 for the then-current top-shorted list
    ("FNGR", "FingerMotion", "Technology"),
    ("FLUX", "Flux Power", "Industrials"),
    ("QTEX", "QTREX Quantum", "Technology"),
    ("AMOD", "Alpha Modus", "Technology"),
    ("SDEV", "Stablecoin Development", "Financials"),
    ("NIVF", "NewGenIvf Group", "Healthcare"),
    ("AAL", "American Airlines", "Industrials"),
    ("SCKT", "Socket Mobile", "Technology"),
    ("SPCX", "Space Exploration Technologies", "Industrials"),
    ("NU", "Nu Holdings", "Financials"),
    ("SCNX", "Scienture Holdings", "Healthcare"),
    ("NVD", "2x Short NVDA ETF", "ETF"),
    ("BITO", "ProShares Bitcoin ETF", "ETF"),
    ("SOXS", "Semiconductor Bear 3X", "ETF"),
    ("DDC", "DDC Enterprise", "Consumer Staples"),
    ("MSTZ", "2X Inverse MSTR ETF", "ETF"),
    ("RWM", "Short Russell 2000", "ETF"),
    ("CYCU", "Cycurion", "Technology"),
    ("ONDS", "Ondas Holdings", "Technology"),
    ("PLUG", "Plug Power", "Industrials"),
    ("CTVA", "Corteva", "Materials"),
    # long-standing frontend static map, carried over as seed
    ("NVDA", "Nvidia", "Technology"), ("AAPL", "Apple", "Technology"),
    ("MSFT", "Microsoft", "Technology"), ("AMD", "AMD", "Technology"),
    ("AVGO", "Broadcom", "Technology"), ("INTC", "Intel", "Technology"),
    ("QCOM", "Qualcomm", "Technology"), ("TXN", "Texas Instruments", "Technology"),
    ("AMAT", "Applied Materials", "Technology"), ("LRCX", "Lam Research", "Technology"),
    ("MU", "Micron", "Technology"), ("KLAC", "KLA", "Technology"),
    ("ADI", "Analog Devices", "Technology"), ("MRVL", "Marvell", "Technology"),
    ("ARM", "Arm Holdings", "Technology"), ("SMCI", "Super Micro", "Technology"),
    ("DELL", "Dell", "Technology"), ("HPQ", "HP", "Technology"),
    ("IBM", "IBM", "Technology"), ("ORCL", "Oracle", "Technology"),
    ("CRM", "Salesforce", "Technology"), ("ADBE", "Adobe", "Technology"),
    ("INTU", "Intuit", "Technology"), ("NOW", "ServiceNow", "Technology"),
    ("PANW", "Palo Alto Networks", "Technology"), ("CRWD", "CrowdStrike", "Technology"),
    ("FTNT", "Fortinet", "Technology"), ("PLTR", "Palantir", "Technology"),
    ("SNOW", "Snowflake", "Technology"), ("DDOG", "Datadog", "Technology"),
    ("NET", "Cloudflare", "Technology"), ("MDB", "MongoDB", "Technology"),
    ("SHOP", "Shopify", "Technology"), ("XYZ", "Block", "Technology"),
    ("PYPL", "PayPal", "Technology"), ("COIN", "Coinbase", "Financials"),
    ("MSTR", "Strategy", "Technology"),
    ("TSLA", "Tesla", "Consumer Discretionary"),
    ("AMZN", "Amazon", "Consumer Discretionary"),
    ("HD", "Home Depot", "Consumer Discretionary"),
    ("MCD", "McDonald's", "Consumer Discretionary"),
    ("NKE", "Nike", "Consumer Discretionary"),
    ("SBUX", "Starbucks", "Consumer Discretionary"),
    ("BKNG", "Booking", "Consumer Discretionary"),
    ("ABNB", "Airbnb", "Consumer Discretionary"),
    ("RIVN", "Rivian", "Consumer Discretionary"),
    ("LCID", "Lucid", "Consumer Discretionary"),
    ("F", "Ford", "Consumer Discretionary"),
    ("GM", "General Motors", "Consumer Discretionary"),
    ("GME", "GameStop", "Consumer Discretionary"),
    ("AMC", "AMC Entertainment", "Communication Services"),
    ("GOOGL", "Alphabet", "Communication Services"),
    ("GOOG", "Alphabet", "Communication Services"),
    ("META", "Meta", "Communication Services"),
    ("NFLX", "Netflix", "Communication Services"),
    ("DIS", "Disney", "Communication Services"),
    ("T", "AT&T", "Communication Services"),
    ("VZ", "Verizon", "Communication Services"),
    ("TMUS", "T-Mobile", "Communication Services"),
    ("EA", "Electronic Arts", "Communication Services"),
    ("TTWO", "Take-Two", "Communication Services"),
    ("RBLX", "Roblox", "Communication Services"),
    ("DJT", "Trump Media", "Communication Services"),
    ("SNAP", "Snap", "Communication Services"),
    ("PINS", "Pinterest", "Communication Services"),
    ("JPM", "JPMorgan", "Financials"),
    ("BAC", "Bank of America", "Financials"),
    ("WFC", "Wells Fargo", "Financials"),
    ("C", "Citigroup", "Financials"),
    ("GS", "Goldman Sachs", "Financials"),
    ("MS", "Morgan Stanley", "Financials"),
    ("AXP", "Amex", "Financials"),
    ("V", "Visa", "Financials"),
    ("MA", "Mastercard", "Financials"),
    ("COF", "Capital One", "Financials"),
    ("SCHW", "Charles Schwab", "Financials"),
    ("HOOD", "Robinhood", "Financials"),
    ("SOFI", "SoFi", "Financials"),
    ("BX", "Blackstone", "Financials"),
    ("KKR", "KKR", "Financials"),
    ("ARES", "Ares", "Financials"),
    ("JNJ", "Johnson & Johnson", "Healthcare"),
    ("UNH", "UnitedHealth", "Healthcare"),
    ("LLY", "Eli Lilly", "Healthcare"),
    ("PFE", "Pfizer", "Healthcare"),
    ("MRK", "Merck", "Healthcare"),
    ("ABBV", "AbbVie", "Healthcare"),
    ("AMGN", "Amgen", "Healthcare"),
    ("GILD", "Gilead", "Healthcare"),
    ("BIIB", "Biogen", "Healthcare"),
    ("REGN", "Regeneron", "Healthcare"),
    ("VRTX", "Vertex", "Healthcare"),
    ("ISRG", "Intuitive Surgical", "Healthcare"),
    ("TMO", "Thermo Fisher", "Healthcare"),
    ("DHR", "Danaher", "Healthcare"),
    ("CVS", "CVS Health", "Healthcare"),
    ("HCA", "HCA", "Healthcare"),
    ("XOM", "Exxon Mobil", "Energy"),
    ("CVX", "Chevron", "Energy"),
    ("COP", "ConocoPhillips", "Energy"),
    ("EOG", "EOG Resources", "Energy"),
    ("SLB", "SLB", "Energy"),
    ("OXY", "Occidental", "Energy"),
    ("MPC", "Marathon Petroleum", "Energy"),
    ("VLO", "Valero", "Energy"),
    ("MARA", "MARA Holdings", "Energy"),
    ("RIOT", "Riot Platforms", "Energy"),
    ("BA", "Boeing", "Industrials"),
    ("CAT", "Caterpillar", "Industrials"),
    ("GE", "GE Aerospace", "Industrials"),
    ("HON", "Honeywell", "Industrials"),
    ("UPS", "UPS", "Industrials"),
    ("FDX", "FedEx", "Industrials"),
    ("LMT", "Lockheed Martin", "Industrials"),
    ("RTX", "RTX", "Industrials"),
    ("NOC", "Northrop Grumman", "Industrials"),
    ("DAL", "Delta", "Industrials"),
    ("UAL", "United Airlines", "Industrials"),
    ("WMT", "Walmart", "Consumer Staples"),
    ("COST", "Costco", "Consumer Staples"),
    ("PG", "Procter & Gamble", "Consumer Staples"),
    ("KO", "Coca-Cola", "Consumer Staples"),
    ("PEP", "PepsiCo", "Consumer Staples"),
    ("NEE", "NextEra", "Utilities"),
    ("DUK", "Duke Energy", "Utilities"),
    ("LIN", "Linde", "Materials"),
    ("FCX", "Freeport-McMoRan", "Materials"),
    ("NEM", "Newmont", "Materials"),
    ("AMT", "American Tower", "Real Estate"),
    ("PLD", "Prologis", "Real Estate"),
    ("SPY", "S&P 500 ETF", "ETF"),
    ("QQQ", "Nasdaq 100 ETF", "ETF"),
    ("IWM", "Russell 2000 ETF", "ETF"),
    ("TLT", "20Y+ Treasury ETF", "ETF"),
    ("HYG", "HY Bond ETF", "ETF"),
    ("LQD", "IG Bond ETF", "ETF"),
)


def _clean_name(raw: str) -> str:
    """Tidy an API-returned name: collapse whitespace; title-case ALLCAPS."""
    name = " ".join(str(raw or "").split())
    if name and name == name.upper() and any(c.isalpha() for c in name):
        name = name.title()
    return name


def _sector_for(name: str, finnhub_industry: str | None,
                security_type: str | None) -> str:
    if any(h.lower() in name.lower() for h in ETF_HINTS):
        return "ETF"
    if finnhub_industry:
        return FINNHUB_INDUSTRY_TO_GICS.get(finnhub_industry.strip(),
                                            "Other")
    st = (security_type or "").lower()
    if "etf" in st or "fund" in st or "adr" in st:
        return "ETF"
    return "Other"


def load_master(store: Store) -> dict[str, dict]:
    """Cached {SYMBOL: {name, sector, source, updated}} merged over the seed."""
    master: dict[str, dict] = {
        sym: {"name": name, "sector": sector, "source": "seed",
              "updated": "2026-10-05"}
        for sym, name, sector in SEED
    }
    doc = store.doc(DOC_KEY)
    if doc:
        cached = (doc.payload or {}).get("tickers") or {}
        for sym, entry in cached.items():
            if isinstance(entry, dict) and entry.get("name"):
                master[str(sym).upper()] = entry
    return master


def save_master(store: Store, tickers: dict[str, dict]) -> None:
    store.put_doc(DOC_KEY, {
        "as_of": date.today().isoformat(),
        "count": len(tickers),
        "tickers": tickers,
    }, source=SOURCE)


async def _resolve_via_finnhub(symbols: list[str], get_text) -> dict[str, dict]:
    """Finnhub stock/profile2 per ticker: name + finnhubIndustry."""
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        log.warning("ticker_master: FINNHUB_API_KEY not set — cannot resolve "
                    "unknown ticker names via Finnhub; falling back to seed only")
        return {}
    out: dict[str, dict] = {}
    for i, sym in enumerate(symbols):
        if i:
            await asyncio.sleep(FINNHUB_GAP)
        try:
            body = await get_text(
                FINNHUB_PROFILE_URL,
                params={"symbol": sym, "token": api_key},
            )
            import json as _json
            prof = _json.loads(body) if isinstance(body, str) else body
            name = _clean_name(prof.get("name") or "")
            if not name:
                continue
            sector = _sector_for(name, prof.get("finnhubIndustry"),
                                 prof.get("type"))
            out[sym] = {"name": name, "sector": sector,
                        "source": "finnhub",
                        "updated": date.today().isoformat()}
        except Exception as exc:  # noqa: BLE001 — one bad ticker never blocks
            log.debug("ticker_master finnhub %s: %s", sym, exc)
    return out


async def _resolve_via_openfigi(symbols: list[str], post_json) -> dict[str, dict]:
    """OpenFIGI mapping POST, batched: name (+ securityType for the sector)."""
    api_key = os.environ.get("OPENFIGI_API_KEY", "").strip()
    if not api_key:
        log.warning("ticker_master: OPENFIGI_API_KEY not set — cannot resolve "
                    "unknown ticker names via OpenFIGI")
        return {}
    import json as _json
    out: dict[str, dict] = {}
    headers = {"X-OPENFIGI-APIKEY": api_key}
    for i in range(0, len(symbols), OPENFIGI_BATCH):
        if i:
            await asyncio.sleep(OPENFIGI_GAP)
        jobs = [{"idType": "TICKER", "idValue": s, "exchCode": "US"}
                for s in symbols[i:i + OPENFIGI_BATCH]]
        try:
            body = await post_json(OPENFIGI_URL, jobs, headers=headers)
            results = body if isinstance(body, list) else _json.loads(body)
        except Exception as exc:  # noqa: BLE001
            log.debug("ticker_master openfigi batch %d: %s", i, exc)
            continue
        for job in results:
            if not isinstance(job, dict):
                continue
            data = job.get("data")
            if not data:
                continue
            first = data[0]
            ticker = str(first.get("ticker") or "").upper()
            name = _clean_name(first.get("name") or "")
            if not ticker or not name:
                continue
            sector = _sector_for(name, None, first.get("securityType"))
            out[ticker] = {"name": name, "sector": sector,
                           "source": "openfigi",
                           "updated": date.today().isoformat()}
    return out


async def resolve_tickers(store: Store, symbols: list[str],
                          get_text=None, post_json=None) -> dict[str, dict]:
    """Ensure `symbols` are in the master cache; return the full master map.

    Cache-first: anything already known (seed or previously resolved) costs
    no API calls. Misses go to Finnhub, then OpenFIGI for whatever remains.
    Newly resolved entries are persisted to the `ticker_master` doc.
    Degrades cleanly when no keys/clients are available (seed only).
    """
    master = load_master(store)
    missing = [s for s in {s.upper() for s in symbols}
               if s and s not in master]
    if not missing:
        return master
    log.info("ticker_master: resolving %d unknown tickers: %s",
             len(missing), ",".join(sorted(missing)[:10]))
    resolved: dict[str, dict] = {}
    if get_text is not None:
        resolved.update(await _resolve_via_finnhub(missing, get_text))
    still_missing = [s for s in missing if s not in resolved]
    if still_missing and post_json is not None:
        resolved.update(await _resolve_via_openfigi(still_missing, post_json))
    if resolved:
        cached = {sym: entry for sym, entry in master.items()
                  if entry.get("source") != "seed"}
        cached.update(resolved)
        save_master(store, cached)
        master.update(resolved)
        log.info("ticker_master: cached %d new (%d total)",
                 len(resolved), len(master))
    else:
        log.info("ticker_master: no new resolutions (no keys or all failed)")
    return master
