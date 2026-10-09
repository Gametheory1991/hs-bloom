"""SEC EDGAR Form 4 insider trading monitor (keyless).

Officers, directors, and 10%+ owners must file Form 4 within 2 business
days of trading their company's stock. Cluster buys by multiple insiders
are one of the strongest bullish signals in equities.

Flow (verified pattern from thirteenf.py):
  1. EDGAR daily master index:
     https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{n}/master.{YYYYMMDD}.idx
     -> all Form 4 filings for the day (CIK, accession, filing date)
  2. For each filing:
     https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json
     -> the Form 4 XML document
  3. Parse ownershipDocument XML:
     - reportingOwner: name, CIK, isOfficer/isDirector/isTenPercentOwner
     - issuer: name, CIK, tradingSymbol
     - nonDerivativeTable/nonDerivativeTransaction:
       transactionDate, transactionShares, transactionPricePerShare,
       transactionAcquiredDisposedCode (A/D), transactionCode (P/S),
       directOrIndirectOwnership (D/I)

Focus: OPEN MARKET transactions only (code P = purchase, S = sale).
Grants, exercises, gifts (codes A, M, G, etc.) are excluded — they don't
signal insider sentiment the way open-market buys/sells do.

Cluster detection: 2+ distinct insiders buying the same issuer within
7 calendar days = "cluster buy" (strong bullish). Same for sells.

Output:
  Doc key form4:{accession-no-dashes} with full transaction detail.
  Doc key form4:clusters:{YYYYMMDD} with detected clusters for the day.
  Series cycle:form4-buy-volume (daily aggregate open-market buy $ volume)
  Series cycle:form4-sell-volume (daily aggregate open-market sell $ volume)
  Series cycle:form4-buy-count / cycle:form4-sell-count (transaction counts)

MANDATORY SEC fair-access rules: descriptive User-Agent (NOT product/(+url)
style — SEC 403s that), <=10 req/sec, 0.5s pause between calls.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

PAUSE = 0.5  # seconds between EDGAR calls; well under the 10 req/sec limit
USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "hs-bloom/1.0 contact harrysugamakc@gmail.com"
)

# Form 4 XML namespace
NS4 = {"o": "http://www.sec.gov/edgar/document/four"}

# We only care about open-market transactions
OPEN_MARKET_CODES = {"P", "S"}  # P = purchase, S = sale

# Lookback: how many days of daily indexes to scan per run
SCAN_DAYS = 3


def _qtr(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _master_index_url(d: date) -> str:
    """EDGAR daily master index URL for a given date."""
    return (
        f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/"
        f"QTR{_qtr(d)}/master.{d.strftime('%Y%m%d')}.idx"
    )


def parse_master_index(text: str, form_filter: str = "4") -> list[dict]:
    """Parse EDGAR daily master index, returning Form 4 entries.

    Index format (pipe-delimited after header):
    CIK|Company Name|Form Type|Date Filed|Filename (accession path)
    """
    entries = []
    # Skip header lines until we hit the dashed separator
    lines = text.splitlines()
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("---"):
            data_start = i + 1
            break
    for line in lines[data_start:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        cik, name, form, filed, path = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()
        if form == form_filter:
            # Extract accession from path: edgar/data/{cik}/{accession}/{doc}
            m = re.search(r"/(\d{18})/", path)
            accession = m.group(1) if m else ""
            entries.append({
                "cik": cik.strip(),
                "filer_name": name,
                "form": form,
                "filed": filed,
                "accession": accession,
                "path": path,
            })
    return entries


def _oxml(el: ET.Element | None, tag: str) -> str:
    """Get text from a namespaced Form 4 XML element."""
    if el is None:
        return ""
    child = el.find(f"o:{tag}", NS4)
    if child is not None and child.text:
        return child.text.strip()
    # Try without namespace (some filings omit it)
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def parse_form4_xml(text: str) -> dict:
    """Parse a Form 4 ownershipDocument XML into structured data.

    Returns dict with issuer, owners, and open-market transactions.
    """
    # Strip XML declaration issues; handle both namespaced and plain
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Try cleaning common issues
        text = re.sub(r"<\?xml[^?]*\?>", "", text).strip()
        root = ET.fromstring(text)

    # Detect namespace from root tag
    ns = NS4
    if root.tag.startswith("{"):
        uri = root.tag.split("}")[0].strip("{")
        ns = {"o": uri}

    def _t(parent: ET.Element | None, tag: str) -> str:
        """Get text from a child element, handling <value> wrappers.

        Form 4 XML nests values like <transactionShares><value>10000</value></transactionShares>.
        """
        if parent is None:
            return ""
        el = parent.find(f"o:{tag}", ns)
        if el is None:
            el = parent.find(tag)  # fallback: no namespace
        if el is None:
            return ""
        # Check for <value> child first (Form 4 standard)
        val = el.find(f"o:value", ns)
        if val is None:
            val = el.find("value")
        if val is not None and val.text:
            return val.text.strip()
        return el.text.strip() if el.text else ""

    def _find(parent: ET.Element | None, tag: str) -> ET.Element | None:
        """Find child element, trying namespaced then plain tag."""
        if parent is None:
            return None
        el = parent.find(f"o:{tag}", ns)
        if el is not None:
            return el
        return parent.find(tag)

    # Issuer
    issuer_el = _find(root, "issuer")
    issuer = {
        "name": _t(issuer_el, "issuerName"),
        "cik": _t(issuer_el, "issuerCik"),
        "ticker": _t(issuer_el, "issuerTradingSymbol"),
    }

    # Reporting owners (can be multiple)
    owners = []
    for owner_el in root.findall("o:reportingOwner", ns) or root.findall("reportingOwner"):
        rel = _find(owner_el, "reportingOwnerRelationship")
        owners.append({
            "name": _t(_find(owner_el, "reportingOwnerId"), "rptOwnerName"),
            "cik": _t(_find(owner_el, "reportingOwnerId"), "rptOwnerCik"),
            "is_officer": _t(rel, "isOfficer") == "1",
            "is_director": _t(rel, "isDirector") == "1",
            "is_ten_pct": _t(rel, "isTenPercentOwner") == "1",
            "officer_title": _t(rel, "officerTitle"),
        })

    # Non-derivative transactions (the stock itself, not options)
    transactions = []
    ndt = _find(root, "nonDerivativeTable")
    if ndt is not None:
        for txn in ndt.findall("o:nonDerivativeTransaction", ns) or ndt.findall("nonDerivativeTransaction"):
            code_el = _find(txn, "transactionCoding")
            txn_code = _t(code_el, "transactionCode")
            # Only open-market P/S
            if txn_code not in OPEN_MARKET_CODES:
                continue
            amounts = _find(txn, "transactionAmounts")
            ownership = _find(txn, "ownershipNature")
            try:
                shares = float(_t(amounts, "transactionShares").replace(",", "") or 0)
            except ValueError:
                shares = 0
            try:
                price_str = _t(amounts, "transactionPricePerShare").replace(",", "")
                price = float(price_str) if price_str else 0
            except ValueError:
                price = 0
            acquired = _t(amounts, "transactionAcquiredDisposedCode")  # A or D
            transactions.append({
                "date": _t(txn, "transactionDate"),
                "code": txn_code,
                "side": "buy" if txn_code == "P" else "sell",
                "shares": shares,
                "price": price,
                "value_usd": shares * price,
                "acquired_disposed": acquired,
                "direct_indirect": _t(ownership, "directOrIndirectOwnership"),
            })

    return {
        "issuer": issuer,
        "owners": owners,
        "transactions": transactions,
        "n_open_market": len(transactions),
    }


def detect_clusters(filings: list[dict], window_days: int = 7) -> list[dict]:
    """Detect cluster buys/sells: 2+ distinct insiders, same issuer, within window.

    filings: list of parsed Form 4 dicts with 'issuer', 'owners', 'transactions',
             and 'filed' date.
    """
    # Group by issuer ticker (or CIK if no ticker)
    by_issuer: dict[str, list[dict]] = {}
    for f in filings:
        if not f["transactions"]:
            continue
        key = f["issuer"]["ticker"] or f["issuer"]["cik"] or f["issuer"]["name"]
        if not key:
            continue
        by_issuer.setdefault(key, []).append(f)

    clusters = []
    for issuer_key, issuer_filings in by_issuer.items():
        # Collect all buy/sell events with dates and insider names
        buys: list[tuple[str, str, str]] = []   # (date, insider, side)
        sells: list[tuple[str, str, str]] = []
        for f in issuer_filings:
            for t in f["transactions"]:
                insider = f["owners"][0]["name"] if f["owners"] else "unknown"
                entry = (t["date"], insider, f["issuer"]["name"])
                if t["side"] == "buy":
                    buys.append(entry)
                else:
                    sells.append(entry)
        # Check for clusters: 2+ distinct insiders within window
        for events, side in ((buys, "cluster_buy"), (sells, "cluster_sell")):
            if len(events) < 2:
                continue
            # Sort by date
            events.sort()
            insiders = set()
            dates = []
            for d, insider, _ in events:
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    continue
                insiders.add(insider)
                dates.append(dt)
            if len(insiders) >= 2 and dates:
                span = (max(dates) - min(dates)).days
                if span <= window_days:
                    clusters.append({
                        "issuer": issuer_key,
                        "issuer_name": events[0][2],
                        "side": side,
                        "n_insiders": len(insiders),
                        "insiders": sorted(insiders),
                        "date_from": min(dates).isoformat(),
                        "date_to": max(dates).isoformat(),
                        "n_transactions": len(events),
                    })
    return clusters


async def fetch_form4(
    store: Store, get_text: GetText, today: date | None = None
) -> str:
    """Daily Form 4 insider trading pull from EDGAR.

    Scans the last SCAN_DAYS of daily master indexes for Form 4 filings,
    parses open-market transactions, detects clusters, and stores docs
    + aggregate series.
    """
    today = today or date.today()
    headers = {"User-Agent": USER_AGENT}

    all_filings: list[dict] = []
    total_buy_usd = 0.0
    total_sell_usd = 0.0
    n_buys = 0
    n_sells = 0

    for delta in range(SCAN_DAYS):
        d = today - timedelta(days=delta)
        # Skip weekends (no filings)
        if d.weekday() >= 5:
            continue
        try:
            idx_text = await get_text(_master_index_url(d), headers=headers)
        except Exception as e:
            log.warning("form4: master index fetch failed for %s: %s", d, e)
            continue
        entries = parse_master_index(idx_text, "4")
        log.info("form4: %d Form 4 filings on %s", len(entries), d)
        await asyncio.sleep(PAUSE)

        for entry in entries:
            accession = entry["accession"]
            if not accession:
                continue
            doc_key = f"form4:{accession}"
            if store.doc(doc_key) is not None:
                continue  # already processed
            cik_nopad = entry["cik"].lstrip("0") or "0"
            try:
                # Get filing index to find the XML document
                idx_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik_nopad}/{accession}/index.json"
                )
                idx_data = await get_text(idx_url, headers=headers)
                await asyncio.sleep(PAUSE)
                import json
                idx_json = json.loads(idx_data)
                # Find the Form 4 XML (not the primary doc if it's HTML)
                xml_url = None
                for item in idx_json.get("directory", {}).get("item", []):
                    name = item.get("name", "")
                    if name.endswith(".xml") and "primary" not in name.lower():
                        xml_url = (
                            f"https://www.sec.gov/Archives/edgar/data/"
                            f"{cik_nopad}/{accession}/{name}"
                        )
                        break
                if not xml_url:
                    continue
                xml_text = await get_text(xml_url, headers=headers)
                await asyncio.sleep(PAUSE)
                parsed = parse_form4_xml(xml_text)
                parsed["filed"] = entry["filed"]
                parsed["accession"] = accession
                parsed["filer_name"] = entry["filer_name"]
                store.put_doc(doc_key, parsed, "sec-form4")
                all_filings.append(parsed)
                for t in parsed["transactions"]:
                    if t["side"] == "buy":
                        total_buy_usd += t["value_usd"]
                        n_buys += 1
                    else:
                        total_sell_usd += t["value_usd"]
                        n_sells += 1
            except Exception as e:
                log.warning("form4: failed to parse %s: %s", accession, e)
                continue

    # Cluster detection
    clusters = detect_clusters(all_filings)
    if clusters:
        store.put_doc(
            f"form4:clusters:{today.strftime('%Y%m%d')}",
            {"date": today.isoformat(), "clusters": clusters},
            "sec-form4",
        )

    # Aggregate series (daily points)
    if all_filings or True:  # always write, even if zero (shows no activity)
        store.upsert_points("cycle:form4-buy-volume", [(today, total_buy_usd)])
        store.upsert_points("cycle:form4-sell-volume", [(today, total_sell_usd)])
        store.upsert_points("cycle:form4-buy-count", [(today, float(n_buys))])
        store.upsert_points("cycle:form4-sell-count", [(today, float(n_sells))])

    return (
        f"form4: {len(all_filings)} filings, {n_buys} buys (${total_buy_usd:,.0f}), "
        f"{n_sells} sells (${total_sell_usd:,.0f}), {len(clusters)} clusters"
    )
