"""TSV regulatory watch — weekly scan for Tokenized Securities Venue notices.

Background: SEC Release No. 34-106402 (File No. 4-927), issued 2026-09-17,
creates a 5-year Innovation Exemption (through 2031-09-17) for Tokenized
Securities Venues (TSVs) — US persons running permissioned AMM pools in
tokenized NMS stock — and for liquidity providers ("Covered Firms").

There is NO central SEC registry of TSVs. Condition C of the order requires
each TSV to publish a public notice on its OWN website >=30 calendar days
before operating, and to give the SEC written notice at
tradingandmarkets@sec.gov within one business day of publication. So this
job honestly does what is machine-trackable server-side:

  1. SEC press releases listing — scan titles/links for "tokeniz" items
     (new releases, speeches, or notices referencing the exemption).
  2. Federal Register API (free, keyless) — documents mentioning
     "tokenized securities venue" (captures the order itself, amendments,
     comment-period notices under File No. 4-927).

It does NOT scrape firm websites for Condition C notices (that needs a
browser); the vendored universe file tracks operator status from
press research instead. Writes doc "tsv_watch".

Verified live 2026-10-03. One unreachable source never fails the job.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "tsv-watch"

SEC_PRESS_URL = "https://www.sec.gov/newsroom/press-releases"
FR_API = ("https://www.federalregister.gov/api/v1/documents.json"
          "?conditions[term]=tokenized+securities+venue&per_page=20&order=newest")

TOKEN_RE = re.compile(r"tokeniz", re.IGNORECASE)
LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def _strip(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s)).strip()


def parse_press_listing(page_html: str, limit: int = 12) -> list[dict]:
    """Extract press-release links whose title mentions tokenization."""
    hits: list[dict] = []
    for href, inner in LINK_RE.findall(page_html):
        title = _strip(inner)
        if not title or not TOKEN_RE.search(title):
            continue
        if href.startswith("/"):
            href = "https://www.sec.gov" + href
        if not any(h["url"] == href for h in hits):
            hits.append({"title": title, "url": href})
        if len(hits) >= limit:
            break
    return hits


def parse_fr_documents(payload: dict, limit: int = 12) -> list[dict]:
    """Extract Federal Register documents mentioning TSVs."""
    out: list[dict] = []
    for d in (payload.get("results") or [])[:limit]:
        out.append({
            "title": d.get("title"),
            "url": d.get("html_url"),
            "published": d.get("publication_date"),
            "type": d.get("type"),
        })
    return out


def build_payload(press_hits: list[dict], fr_hits: list[dict],
                  errors: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "order": {
            "release": "34-106402",
            "file_no": "4-927",
            "issued": "2026-09-17",
            "expires": "2031-09-17",
            "earliest_trading": "2026-10-17",
        },
        "press_hits": press_hits,
        "federal_register_hits": fr_hits,
        "checks": [
            "SEC press releases listing scanned for 'tokeniz' items",
            "Federal Register API queried for 'tokenized securities venue'",
        ],
        "caveat": ("No central SEC registry exists. Condition C notices live on each "
                   "operator's own website; operator status in the universe file comes "
                   "from press research, not this scan."),
        "errors": errors,
    }


async def fetch_tsv_watch(store: Store, get_text: GetText) -> str:
    press_hits: list[dict] = []
    fr_hits: list[dict] = []
    errors: list[str] = []

    try:
        page = await get_text(SEC_PRESS_URL)
        press_hits = parse_press_listing(page)
    except Exception as exc:  # one bad source never fails the job
        errors.append(f"sec-press: {exc}")
        log.warning("tsv_watch sec press failed: %s", exc)

    try:
        raw = await get_text(FR_API, headers={"Accept": "application/json"})
        fr_hits = parse_fr_documents(json.loads(raw))
    except Exception as exc:
        errors.append(f"federal-register: {exc}")
        log.warning("tsv_watch federal register failed: %s", exc)

    store.put_doc("tsv_watch", build_payload(press_hits, fr_hits, errors),
                  source=SOURCE)
    return SOURCE
