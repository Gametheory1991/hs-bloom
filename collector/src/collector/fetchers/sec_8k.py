"""SEC EDGAR 8-K material event monitor (keyless).

Form 8-K reports material corporate events within 4 business days:
  1.01  Entry into a Material Definitive Agreement
  1.02  Termination of a Material Definitive Agreement
  2.01  Completion of Acquisition or Disposition of Assets
  2.02  Results of Operations and Financial Condition (earnings)
  3.01  Notice of Delisting or Failure to Satisfy Listing Rule
  4.01  Changes in Registrant's Certifying Accountant
  5.01  Changes in Control of Registrant
  5.02  Departure/Election of Directors or Officers
  5.03  Amendments to Articles/Corporation or Bylaws
  7.01  Regulation FD Disclosure
  8.01  Other Events (catch-all for material news)

High-signal items for the terminal: 1.01, 2.01, 5.02, 7.01, 8.01.

Flow:
  1. EDGAR daily master index (same as sec_form4.py):
     https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{n}/master.{YYYYMMDD}.idx
     -> filter for "8-K", "8-K/A"
  2. For each filing, fetch the primary document and extract:
     - Issuer name, CIK, ticker (from index or document header)
     - Item numbers filed (regex on document text)
     - Filing date
  3. Classify by item: M&A (2.01), management change (5.02),
     earnings (2.02), agreement (1.01), Reg FD (7.01), other (8.01).

Output:
  Doc key 8k:{accession-no-dashes} with items and classification.
  Doc key 8k:events:{YYYYMMDD} with high-signal events for the day.
  Series cycle:8k-count (daily total 8-K filings)
  Series cycle:8k-ma-count (daily 2.01 M&A events)
  Series cycle:8k-mgmt-count (daily 5.02 management changes)

MANDATORY SEC fair-access rules: descriptive User-Agent, <=10 req/sec,
0.5s pause between calls.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta

from collector.fetchers.sec_form4 import (
    PAUSE,
    USER_AGENT,
    _master_index_url,
    parse_master_index,
)
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SCAN_DAYS = 3

# Item number -> description
ITEM_DESCRIPTIONS = {
    "1.01": "Entry into Material Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Acquisition/Disposition of Assets",
    "2.02": "Results of Operations (Earnings)",
    "2.03": "Creation of Direct Financial Obligation",
    "2.04": "Triggering Events (Acceleration)",
    "2.05": "Costs Associated with Exit/Disposal",
    "2.06": "Material Impairments",
    "3.01": "Delisting Notice",
    "3.02": "Unregistered Sales of Equity",
    "3.03": "Material Modification to Rights",
    "4.01": "Changes in Certifying Accountant",
    "4.02": "Non-Reliance on Financial Statements",
    "5.01": "Changes in Control",
    "5.02": "Departure/Election of Officers/Directors",
    "5.03": "Amendments to Articles/Bylaws",
    "5.04": "Temporary Suspension of Trading (401k Blackout)",
    "5.05": "Amendments to Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to Vote",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "6.02": "Change of Servicer or Trustee (ABS)",
    "6.03": "Change in Credit Enhancement (ABS)",
    "6.04": "Failure to Make Distribution (ABS)",
    "6.05": "Securities Act Updating Disclosure (ABS)",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# High-signal items for alerts
HIGH_SIGNAL = {"1.01", "2.01", "5.02", "7.01", "8.01"}

# Classification buckets
ITEM_CLASSES = {
    "1.01": "agreement",
    "1.02": "agreement",
    "2.01": "ma",
    "2.02": "earnings",
    "5.01": "control",
    "5.02": "management",
    "7.01": "regfd",
    "8.01": "other_material",
}


def parse_8k_items(text: str) -> list[str]:
    """Extract 8-K item numbers from filing document text.

    Items appear as "Item 1.01", "ITEM 5.02", etc. in the document.
    Returns sorted unique list of item numbers found.
    """
    # Strip HTML for cleaner matching
    clean = re.sub(r"<[^>]+>", " ", text)
    # Match "Item X.XX" patterns (case-insensitive)
    found = set()
    for m in re.finditer(r"\b[Ii][Tt][Ee][Mm]\s+(\d\.\d{2})", clean):
        item = m.group(1)
        if item in ITEM_DESCRIPTIONS:
            found.add(item)
    return sorted(found)


def parse_8k_header(text: str) -> dict:
    """Extract issuer info from 8-K document header."""
    clean = re.sub(r"<[^>]+>", " ", text[:10000])  # header is at top
    clean = re.sub(r"\s+", " ", clean)
    result = {"issuer_name": "", "ticker": ""}
    # Company name often in title or first lines
    m = re.search(r"([A-Z][A-Za-z\s\.,&'\-]+?)\s*(?:\(Exact name|$)", clean[:2000])
    if m:
        result["issuer_name"] = m.group(1).strip()[:100]
    # Ticker: look for "Trading Symbol" or similar
    m = re.search(
        r"[Tt]rading\s+[Ss]ymbol[\s:]*([A-Z]{1,5})\b", clean[:5000]
    )
    if m:
        result["ticker"] = m.group(1).upper()
    return result


async def fetch_8k(
    store: Store, get_text: GetText, today: date | None = None
) -> str:
    """Daily 8-K material event pull from EDGAR.

    Scans the last SCAN_DAYS of daily master indexes, extracts item
    numbers, classifies events, flags high-signal items.
    """
    today = today or date.today()
    headers = {"User-Agent": USER_AGENT}

    n_total = 0
    n_ma = 0
    n_mgmt = 0
    high_signal_events: list[dict] = []

    for delta in range(SCAN_DAYS):
        d = today - timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        try:
            idx_text = await get_text(_master_index_url(d), headers=headers)
        except Exception as e:
            log.warning("8k: master index fetch failed for %s: %s", d, e)
            continue
        await asyncio.sleep(PAUSE)

        entries = []
        entries.extend(parse_master_index(idx_text, "8-K"))
        entries.extend(parse_master_index(idx_text, "8-K/A"))
        log.info("8k: %d 8-K filings on %s", len(entries), d)

        for entry in entries:
            accession = entry["accession"]
            if not accession:
                continue
            doc_key = f"8k:{accession}"
            if store.doc(doc_key) is not None:
                continue
            cik_nopad = entry["cik"].lstrip("0") or "0"
            try:
                idx_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik_nopad}/{accession}/index.json"
                )
                import json
                idx_data = await get_text(idx_url, headers=headers)
                await asyncio.sleep(PAUSE)
                idx_json = json.loads(idx_data)
                doc_url = None
                for item in idx_json.get("directory", {}).get("item", []):
                    name = item.get("name", "")
                    if name.endswith((".htm", ".html", ".txt")):
                        doc_url = (
                            f"https://www.sec.gov/Archives/edgar/data/"
                            f"{cik_nopad}/{accession}/{name}"
                        )
                        break
                if not doc_url:
                    continue
                doc_text = await get_text(doc_url, headers=headers)
                await asyncio.sleep(PAUSE)

                items = parse_8k_items(doc_text[:100000])  # first 100KB
                header = parse_8k_header(doc_text)
                classes = list({ITEM_CLASSES.get(i, "other") for i in items})

                parsed = {
                    "issuer_name": header["issuer_name"] or entry["filer_name"],
                    "issuer_cik": entry["cik"],
                    "ticker": header["ticker"],
                    "filed": entry["filed"],
                    "accession": accession,
                    "is_amendment": entry["form"] == "8-K/A",
                    "items": items,
                    "item_descriptions": [ITEM_DESCRIPTIONS.get(i, i) for i in items],
                    "classes": classes,
                    "high_signal": [i for i in items if i in HIGH_SIGNAL],
                }
                store.put_doc(doc_key, parsed, "sec-8k")
                n_total += 1
                if "2.01" in items:
                    n_ma += 1
                if "5.02" in items:
                    n_mgmt += 1
                if parsed["high_signal"] and not parsed["is_amendment"]:
                    high_signal_events.append(parsed)
            except Exception as e:
                log.warning("8k: failed to parse %s: %s", accession, e)
                continue

    # Store high-signal events
    if high_signal_events:
        store.put_doc(
            f"8k:events:{today.strftime('%Y%m%d')}",
            {"date": today.isoformat(), "events": high_signal_events},
            "sec-8k",
        )

    # Daily series
    store.upsert_points("cycle:8k-count", [(today, float(n_total))])
    store.upsert_points("cycle:8k-ma-count", [(today, float(n_ma))])
    store.upsert_points("cycle:8k-mgmt-count", [(today, float(n_mgmt))])

    return (
        f"8k: {n_total} filings, {n_ma} M&A (2.01), {n_mgmt} mgmt changes (5.02), "
        f"{len(high_signal_events)} high-signal"
    )
