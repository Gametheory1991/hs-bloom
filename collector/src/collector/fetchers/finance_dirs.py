"""Finance directories: primary dealers, broker-dealers, ATS venues,
depository-institution MPIDs. Curated lists are verified against public
sources; FINRA pages are scraped politely (monthly cadence) and cached in
docs labeled with source + as-of date.

Docs: finance_dir:primary_dealers | finance_dir:broker_dealers |
       finance_dir:ats | finance_dir:depository
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from datetime import datetime, timezone

from lxml import html as lxml_html

from collector.config import Config
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "finance-dirs-monthly"
UA = {"User-Agent": "os-bloom/1.0 (finance directory refresh; contact)"}

# ---------------------------------------------------------------- primary
# dealers: NY Fed primary dealer list, verified 2026-10-07 via web search
# (NY Fed page is JS-rendered; roster cross-checked against NY Fed
# announcements: MUFG added 2026, SMBC Nikko added 2025, ASL added 2022,
# Amherst Pierpont -> Santander US Capital Markets 2023).
PRIMARY_DEALERS = [
    "ASL Capital Markets Inc.",
    "Bank of Montreal, Chicago Branch",
    "Bank of Nova Scotia, New York Agency",
    "Barclays Capital Inc.",
    "BNP Paribas Securities Corp.",
    "BofA Securities, Inc.",
    "Cantor Fitzgerald & Co.",
    "Citigroup Global Markets Inc.",
    "Daiwa Capital Markets America Inc.",
    "Deutsche Bank Securities Inc.",
    "Goldman Sachs & Co. LLC",
    "HSBC Securities (USA) Inc.",
    "Jefferies LLC",
    "J.P. Morgan Securities LLC",
    "Mizuho Securities USA LLC",
    "Morgan Stanley & Co. LLC",
    "MUFG Securities Americas Inc.",
    "NatWest Markets Securities Inc.",
    "Nomura Securities International, Inc.",
    "RBC Capital Markets, LLC",
    "Santander US Capital Markets LLC",
    "SMBC Nikko Securities America, Inc.",
    "Societe Generale, New York Branch",
    "TD Securities (USA) LLC",
    "UBS Securities LLC",
    "Wells Fargo Securities, LLC",
]

# ------------------------------------------------------- broker-dealer seeds
# Curated major firms; CRDs resolved via the free FINRA BrokerCheck firm
# search API. Not exhaustive (FINRA lists ~3,400 firms) — labeled as such.
BROKER_DEALER_SEEDS = [
    # primary dealers (also BDs)
    "Goldman Sachs & Co. LLC", "J.P. Morgan Securities LLC",
    "Morgan Stanley & Co. LLC", "BofA Securities, Inc.",
    "Citigroup Global Markets Inc.", "Barclays Capital Inc.",
    "Deutsche Bank Securities Inc.", "UBS Securities LLC",
    "Wells Fargo Securities, LLC", "Jefferies LLC",
    "RBC Capital Markets, LLC", "BMO Capital Markets Corp.",
    "BNP Paribas Securities Corp.", "Mizuho Securities USA LLC",
    "MUFG Securities Americas Inc.", "Nomura Securities International, Inc.",
    "TD Securities (USA) LLC", "Cantor Fitzgerald & Co.",
    "HSBC Securities (USA) Inc.", "Santander US Capital Markets LLC",
    "SMBC Nikko Securities America, Inc.", "NatWest Markets Securities Inc.",
    "Daiwa Capital Markets America Inc.", "Societe Generale",
    "Bank of Nova Scotia", "ASL Capital Markets Inc.",
    # wirehouses / large retail
    "Merrill Lynch, Pierce, Fenner & Smith Incorporated",
    "Wells Fargo Clearing Services, LLC", "UBS Financial Services Inc.",
    "Morgan Stanley Smith Barney LLC", "Raymond James & Associates, Inc.",
    "LPL Financial LLC", "Edward Jones", "Ameriprise Financial Services, LLC",
    "Stifel, Nicolaus & Company, Incorporated",
    # large independents / regionals
    "Charles Schwab & Co., Inc.", "Fidelity Brokerage Services LLC",
    "E*TRADE Securities LLC", "Robinhood Securities, LLC",
    "Interactive Brokers LLC", "Piper Sandler & Co.",
    "Stifel Nicolaus", "Oppenheimer & Co. Inc.",
    "Stephens Inc.", "Keefe, Bruyette & Woods, Inc.",
]

# ------------------------------------------------- NBLP / market-maker tags
# Curated: non-bank liquidity providers / electronic market makers,
# identified by industry standing. Labeled "curated" in the directory.
NBLP_CURATED = {
    "Citadel Securities LLC": ["nblp", "market_maker"],
    "Jane Street Capital, LLC": ["nblp", "market_maker"],
    "Virtu Americas LLC": ["nblp", "market_maker"],
    "Hudson River Trading LLC": ["nblp", "market_maker"],
    "Two Sigma Securities, LLC": ["nblp", "market_maker"],
    "GTS Securities LLC": ["nblp", "market_maker"],
    "IMC Financial Markets": ["nblp", "market_maker"],
    "Optiver US LLC": ["nblp", "market_maker"],
}

BROKERCHECK = "https://api.brokercheck.finra.org/search/firm?query={}"

ATS_PAGE = ("https://www.finra.org/filing-reporting/otc-transparency/"
            "finra-equity-ats-firms-list")
MPID_PAGE = ("https://www.finra.org/filing-reporting/trace/"
             "depository-institutions-mpids")


def _table_rows(page: str) -> list[list[str]]:
    """Extract all HTML table rows as text-cell lists."""
    doc = lxml_html.fromstring(page)
    rows: list[list[str]] = []
    for tr in doc.xpath("//table//tr"):
        cells = [c.text_content().strip() for c in tr.xpath("./th|./td")]
        if cells:
            rows.append(cells)
    return rows


async def _fetch_primary_dealers(store: Store, today: str) -> int:
    firms = [{"name": n, "type": "primary_dealer",
              "nyfed_stats": True} for n in PRIMARY_DEALERS]
    # tag NBLPs / bank-affiliated where identifiable
    for f in firms:
        for nblp, tags in NBLP_CURATED.items():
            if nblp.lower() in f["name"].lower():
                f["tags"] = tags
                f["tags_source"] = "curated"
    store.put_doc("finance_dir:primary_dealers", {
        "as_of": today,
        "source": "Federal Reserve Bank of New York primary dealer list "
                  "(verified 2026-10-07; NY Fed page is JS-rendered, roster "
                  "cross-checked against NY Fed dealer announcements)",
        "count": len(firms),
        "firms": firms,
        "note": "All primary dealers report positions/transactions to the "
                "NY Fed; see the existing dealer statistics feed "
                "(dealer:<id> series).",
    }, source=SOURCE)
    return len(firms)


async def _brokercheck_crd(name: str, get_text: GetText) -> dict | None:
    """Resolve one firm name to its CRD via the free BrokerCheck API."""
    url = BROKERCHECK.format(urllib.parse.quote(name))
    try:
        raw = await get_text(url, headers=UA)
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.debug("brokercheck: %s failed: %s", name, exc)
        return None
    hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) \
        else []
    if not hits:
        return None
    src = hits[0].get("_source", {})
    return {
        "crd": src.get("firm_source_id"),
        "name": src.get("firm_name") or name,
        "scope": src.get("firm_ia_scope"),
        "branches": src.get("firm_branches_count"),
    }


async def _fetch_broker_dealers(store: Store, get_text: GetText,
                                today: str) -> int:
    firms: list[dict] = []
    for name in BROKER_DEALER_SEEDS:
        hit = await _brokercheck_crd(name, get_text)
        await asyncio.sleep(2.0)  # polite: free API, no key
        entry = {"query": name,
                 "crd": hit["crd"] if hit else None,
                 "name": hit["name"] if hit else name,
                 "scope": hit.get("scope") if hit else None,
                 "branches": hit.get("branches") if hit else None,
                 "type": "broker_dealer"}
        for nblp, tags in NBLP_CURATED.items():
            if nblp.lower() in entry["name"].lower():
                entry["tags"] = tags
                entry["tags_source"] = "curated"
        if "primary_dealer" in name.lower() or entry["name"] in \
                [p for p in PRIMARY_DEALERS]:
            pass
        if entry["name"] in PRIMARY_DEALERS:
            entry["type"] = "primary_dealer+broker_dealer"
        firms.append(entry)
    # add curated NBLPs not already covered
    have = {f["name"].lower() for f in firms}
    for nblp, tags in NBLP_CURATED.items():
        if nblp.lower() not in have:
            hit = await _brokercheck_crd(nblp, get_text)
            await asyncio.sleep(2.0)
            firms.append({
                "query": nblp,
                "crd": hit["crd"] if hit else None,
                "name": hit["name"] if hit else nblp,
                "scope": hit.get("scope") if hit else None,
                "branches": hit.get("branches") if hit else None,
                "type": "broker_dealer",
                "tags": tags, "tags_source": "curated",
            })
    store.put_doc("finance_dir:broker_dealers", {
        "as_of": today,
        "source": "FINRA BrokerCheck firm search API "
                  "(api.brokercheck.finra.org, free, no key)",
        "count": len(firms),
        "firms": firms,
        "note": "Curated major firms, not exhaustive (FINRA lists ~3,400 "
                "broker-dealers). CRD = Central Registration Depository "
                "number. Search by name or CRD in the UI.",
    }, source=SOURCE)
    return len(firms)


async def _fetch_ats(store: Store, get_text: GetText, today: str) -> int:
    """Scrape the FINRA ATS identifiers table (TRACE ATS list)."""
    try:
        page = await get_text(ATS_PAGE, headers=UA)
        rows = _table_rows(page)
    except Exception as exc:  # noqa: BLE001
        log.warning("finance_dirs: ATS page fetch failed: %s", exc)
        return 0
    firms: list[dict] = []
    for cells in rows[1:]:  # skip header
        if len(cells) < 3:
            continue
        name, ats_id, firm = cells[0].strip(), cells[1].strip(), \
            cells[2].strip()
        if not ats_id or "ATS" in name.upper() and "NAME" in name.upper():
            continue
        entry = {"ats_name": name, "ats_id": ats_id, "firm_name": firm,
                 "type": "ats",
                 "exemption_6732": cells[3].strip() if len(cells) > 3
                 else None}
        for nblp, tags in NBLP_CURATED.items():
            if nblp.lower() in (name + " " + firm).lower():
                entry["tags"] = tags
                entry["tags_source"] = "curated"
        firms.append(entry)
    # link our ATS transparency volume data (ats_venues doc + monthly series)
    _venues_doc = store.doc("ats_venues")
    venues_payload = _venues_doc.payload if _venues_doc else {}
    venues = venues_payload.get("venues", {}) if isinstance(venues_payload, dict) \
        else {}
    for f in firms:
        mpid = f["ats_id"].upper()
        if mpid in venues:
            f["volume_series"] = f"cycle:ats-m-{mpid.lower()}-shares"
            f["has_volume"] = True
    store.put_doc("finance_dir:ats", {
        "as_of": today,
        "source": "FINRA ATS firms list (finra.org/filing-reporting/"
                  "otc-transparency/finra-equity-ats-firms-list). "
                  "Not exhaustive per FINRA — supplemented by our ATS "
                  "transparency volume data where MPIDs match.",
        "count": len(firms),
        "firms": firms,
        "note": "TRACE ATS identifiers (fixed income). Venue volume links "
                "to cycle:ats-m-{mpid}-shares monthly series.",
    }, source=SOURCE)
    return len(firms)


async def _fetch_depository(store: Store, get_text: GetText,
                            today: str) -> int:
    """Scrape the FINRA TRACE depository-institutions MPID table."""
    try:
        page = await get_text(MPID_PAGE, headers=UA)
        rows = _table_rows(page)
    except Exception as exc:  # noqa: BLE001
        log.warning("finance_dirs: MPID page fetch failed: %s", exc)
        return 0
    firms: list[dict] = []
    for cells in rows[1:]:  # skip header
        if len(cells) < 2:
            continue
        mpid, bank = cells[0].strip(), cells[1].strip()
        if not mpid or "MPID" in mpid.upper():
            continue
        firms.append({
            "mpid": mpid, "bank_name": bank, "type": "depository",
            "treasury_ts": cells[2].strip() if len(cells) > 2 else None,
            "agency": cells[3].strip() if len(cells) > 3 else None,
        })
    store.put_doc("finance_dir:depository", {
        "as_of": today,
        "source": "FINRA TRACE depository institutions MPID list "
                  "(finra.org/filing-reporting/trace/"
                  "depository-institutions-mpids)",
        "count": len(firms),
        "firms": firms,
        "note": "MPIDs reserved for depository institutions reporting "
                "Treasury (TS) and agency (CA/SP) trades to TRACE.",
    }, source=SOURCE)
    return len(firms)


async def fetch_finance_dirs(cfg: Config, store: Store,
                             get_text: GetText) -> str:
    """Monthly job: refresh all four finance directories."""
    today = datetime.now(timezone.utc).date().isoformat()
    n_pd = await _fetch_primary_dealers(store, today)
    n_bd = await _fetch_broker_dealers(store, get_text, today)
    n_ats = await _fetch_ats(store, get_text, today)
    n_dep = await _fetch_depository(store, get_text, today)
    return (f"finance_dirs: {n_pd} primary dealers, {n_bd} broker-dealers, "
            f"{n_ats} ATS, {n_dep} depository MPIDs")
