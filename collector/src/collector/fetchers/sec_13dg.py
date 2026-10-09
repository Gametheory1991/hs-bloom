"""SEC EDGAR 13D/13G beneficial ownership monitor (keyless).

When anyone crosses 5% ownership of a public company, they must file:
  - Schedule 13D: activist intent (wants to influence/change the company)
  - Schedule 13G: passive investor (index funds, no intent to influence)

A NEW 13D filing is often the first public signal of an activist campaign
or takeover attempt. 13D/A and 13G/A are amendments (position updates).

Flow:
  1. EDGAR daily master index (same as sec_form4.py):
     https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{n}/master.{YYYYMMDD}.idx
     -> filter for "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"
  2. For each filing, fetch the primary document and parse:
     - Filer (reporting person) name and CIK
     - Subject company (issuer) name, CIK, CUSIP
     - Percent of class owned
     - Filing date, event date
     - 13D vs 13G (from form type)
  3. Flag NEW 13D filings (form == "SC 13D", not "/A") as activist signals.

13D/13G filings are typically text or HTML (not structured XML like Form 4),
so parsing uses regex on the document text. The key fields appear in
standardized cover-page format.

Output:
  Doc key 13dg:{accession-no-dashes} with filing detail.
  Doc key 13dg:activist:{YYYYMMDD} with new 13D filings for the day.
  Series cycle:13d-new-count (daily count of new 13D filings)
  Series cycle:13g-new-count (daily count of new 13G filings)

MANDATORY SEC fair-access rules: descriptive User-Agent, <=10 req/sec,
0.5s pause between calls.
"""
from __future__ import annotations

import asyncio
import logging
import os
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

# Form types we track
FORMS_13D = {"SC 13D", "SC 13D/A"}
FORMS_13G = {"SC 13G", "SC 13G/A"}
ALL_FORMS = FORMS_13D | FORMS_13G

# High-signal: new activist filings (not amendments)
ACTIVIST_FORMS = {"SC 13D"}


def parse_13dg_text(text: str, form: str) -> dict:
    """Parse a 13D/13G filing document (text/HTML) for key fields.

    13D/13G cover pages follow a standard format:
      Subject company name, CUSIP, filing person name, % owned.
    We use regex to extract these from the raw text.
    """
    # Strip HTML tags for text search
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean)

    result: dict = {
        "form": form,
        "is_activist": form in ACTIVIST_FORMS,
        "is_amendment": form.endswith("/A"),
        "subject_name": "",
        "subject_cik": "",
        "cusip": "",
        "filer_name": "",
        "filer_cik": "",
        "percent_owned": None,
        "shares_owned": None,
        "event_date": "",
    }

    # CUSIP: 9-character alphanumeric, often labeled
    m = re.search(r"CUSIP\s*(?:Number|No\.?)?\s*:?\s*([A-Z0-9]{9})", clean, re.I)
    if m:
        result["cusip"] = m.group(1).upper()

    # Percent of class: "Percent of class: 7.3%" or "represents 7.3 percent"
    m = re.search(
        r"[Pp]ercent\s+of\s+(?:class|Class)[\s:]*(\d+\.?\d*)\s*%?", clean
    )
    if m:
        try:
            result["percent_owned"] = float(m.group(1))
        except ValueError:
            pass
    if result["percent_owned"] is None:
        m = re.search(r"(\d+\.?\d*)\s*%\s+of\s+(?:the\s+)?(?:class|outstanding)", clean, re.I)
        if m:
            try:
                result["percent_owned"] = float(m.group(1))
            except ValueError:
                pass

    # Shares beneficially owned
    m = re.search(
        r"[Ss]hares\s+[Bb]eneficially\s+[Oo]wned[\s:]*([\d,]+)", clean
    )
    if m:
        try:
            result["shares_owned"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Reporting person name (usually near top: "Name of Reporting Person")
    m = re.search(
        r"Name\s+of\s+Reporting\s+Person[\s:]*([A-Z][A-Za-z\s\.,&'\-]+?)(?:\s{2,}|Check|$)",
        clean,
    )
    if m:
        result["filer_name"] = m.group(1).strip()[:100]

    # Subject company (issuer) name
    m = re.search(
        r"Name\s+of\s+Issuer[\s:]*([A-Z][A-Za-z\s\.,&'\-]+?)(?:\s{2,}|Title|$)",
        clean,
    )
    if m:
        result["subject_name"] = m.group(1).strip()[:100]

    # Event date (when the 5% threshold was crossed)
    m = re.search(
        r"Date\s+of\s+Event\s+(?:Requiring|Which Requires)[\s\w]*?(\w+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})",
        clean,
        re.I,
    )
    if m:
        result["event_date"] = m.group(1).strip()

    return result


async def fetch_13dg(
    store: Store, get_text: GetText, today: date | None = None
) -> str:
    """Daily 13D/13G beneficial ownership pull from EDGAR.

    Scans the last SCAN_DAYS of daily master indexes, parses filings,
    flags new activist (13D) filings.
    """
    today = today or date.today()
    headers = {"User-Agent": USER_AGENT}

    n_13d_new = 0
    n_13g_new = 0
    n_amendments = 0
    activist_filings: list[dict] = []

    for delta in range(SCAN_DAYS):
        d = today - timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        try:
            idx_text = await get_text(_master_index_url(d), headers=headers)
        except Exception as e:
            log.warning("13dg: master index fetch failed for %s: %s", d, e)
            continue
        await asyncio.sleep(PAUSE)

        # Collect all 13D/13G forms
        entries = []
        for form in ALL_FORMS:
            entries.extend(parse_master_index(idx_text, form))
        log.info("13dg: %d 13D/13G filings on %s", len(entries), d)

        for entry in entries:
            accession = entry["accession"]
            if not accession:
                continue
            doc_key = f"13dg:{accession}"
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
                # Primary document (usually .txt or .htm)
                doc_url = None
                for item in idx_json.get("directory", {}).get("item", []):
                    name = item.get("name", "")
                    # Skip XML (that's for structured forms); want the filing doc
                    if name.endswith((".txt", ".htm", ".html")):
                        doc_url = (
                            f"https://www.sec.gov/Archives/edgar/data/"
                            f"{cik_nopad}/{accession}/{name}"
                        )
                        break
                if not doc_url:
                    continue
                doc_text = await get_text(doc_url, headers=headers)
                await asyncio.sleep(PAUSE)
                # Limit to first 50KB (cover page has what we need)
                parsed = parse_13dg_text(doc_text[:50000], entry["form"])
                parsed["filed"] = entry["filed"]
                parsed["accession"] = accession
                parsed["filer_cik"] = entry["cik"]
                store.put_doc(doc_key, parsed, "sec-13dg")

                if parsed["is_amendment"]:
                    n_amendments += 1
                elif entry["form"] in FORMS_13D:
                    n_13d_new += 1
                    activist_filings.append(parsed)
                elif entry["form"] in FORMS_13G:
                    n_13g_new += 1
            except Exception as e:
                log.warning("13dg: failed to parse %s: %s", accession, e)
                continue

    # Store activist alerts
    if activist_filings:
        store.put_doc(
            f"13dg:activist:{today.strftime('%Y%m%d')}",
            {"date": today.isoformat(), "new_13d": activist_filings},
            "sec-13dg",
        )

    # Daily series
    store.upsert_points("cycle:13d-new-count", [(today, float(n_13d_new))])
    store.upsert_points("cycle:13g-new-count", [(today, float(n_13g_new))])

    return (
        f"13dg: {n_13d_new} new 13D (activist), {n_13g_new} new 13G, "
        f"{n_amendments} amendments"
    )
