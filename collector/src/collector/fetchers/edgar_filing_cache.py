"""Shared read-through cache for SEC EDGAR 13F-HR holdings XML documents.

thirteenf.py (filer watchlist, top-15 diff) and edgar_13f_holders.py (~90
filers, full holdings) both download the same 13F-HR filing XMLs from
SEC EDGAR. Filings are immutable per accession number, so the XML is
cached on disk keyed by accession (dashes stripped) and shared by both
fetchers. Each fetcher keeps its own parse logic and output schema.

get_filing_xml(cik10, accession, get_text, headers) resolves the filing's
index.json to the holdings XML name on a cache miss, downloads it, and
refuses to cache error payloads (JSON error envelopes / HTML error pages
served with HTTP 200). Callers must still validate the parsed result
before storing (see edgar_13f_holders.detect guards).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from collector.http import GetText

log = logging.getLogger(__name__)

PAUSE = 0.5  # seconds between EDGAR calls on a cache miss (fair access)

# Cached XMLs older than this (mtime) are pruned; both jobs only ever read
# the current quarter's filings, so anything older is a leftover.
PRUNE_AFTER_SECONDS = 120 * 24 * 3600

_locks: dict[str, asyncio.Lock] = {}


def cache_dir() -> Path:
    """Shared cache location; overridable for tests via EDGAR_XML_CACHE_DIR."""
    d = Path(os.environ.get("EDGAR_XML_CACHE_DIR",
                            Path(tempfile.gettempdir()) / "edgar-13f-xml"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def detect_error_payload(text: str) -> str | None:
    """Detect a 200-with-error body served instead of the expected document.

    SEC EDGAR occasionally answers HTTP 200 with a JSON error envelope or
    an HTML error page. Returns a human-readable reason when the payload
    is not usable data, else None. (get_text returns text only, so the
    content-type check the task calls for is done by inspecting the body.)
    """
    stripped = text.lstrip()
    if not stripped:
        return "empty body"
    if stripped[0] == "{":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return "body starts with '{' but is not valid JSON"
        if isinstance(data, dict):
            keys = {k.lower() for k in data}
            if keys & {"error", "errors", "message", "status", "code",
                       "fault", "exception"}:
                return f"JSON error envelope: {stripped[:200]}"
            return f"unexpected JSON object (keys: {sorted(keys)[:5]})"
        return "unexpected JSON array payload"
    head = stripped[:15].lower()
    if head.startswith(("<html", "<!doctype")):
        return "HTML error page"
    return None


def _cache_path(accession: str) -> Path:
    return cache_dir() / f"{accession.replace('-', '')}.xml"


def _prune_old() -> None:
    """Best-effort: drop cached XMLs older than PRUNE_AFTER_SECONDS."""
    now = time.time()
    try:
        for p in cache_dir().glob("*.xml"):
            try:
                if now - p.stat().st_mtime > PRUNE_AFTER_SECONDS:
                    p.unlink()
                    log.info("edgar-xml-cache: pruned stale %s", p.name)
            except OSError:
                pass
    except OSError:
        pass


async def get_filing_xml(cik10: str, accession: str, get_text: GetText,
                         headers: dict) -> str:
    """Holdings XML for one 13F-HR filing; read-through cache by accession.

    On a cache miss: fetches the filing's index.json, picks the holdings
    XML (the .xml that is not primary_doc.xml), downloads it, validates
    it is XML (not a 200-with-error body), and caches it atomically.
    Raises on any failure — the caller logs and skips the filer (never
    stores a partial/empty result).
    """
    key = accession.replace("-", "")
    path = _cache_path(accession)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            reason = detect_error_payload(text)
            if reason is None:
                try:
                    ET.fromstring(text)
                except ET.ParseError as exc:
                    reason = f"cached XML does not parse: {exc}"
            if reason is None:
                log.debug("edgar-xml-cache: reusing %s", path.name)
                return text
            log.warning("edgar-xml-cache: %s unusable (%s); re-downloading",
                        path.name, reason)
            try:
                path.unlink()
            except OSError:
                pass

        cik_nopad = str(int(cik10))  # EDGAR archive path uses the unpadded CIK
        index = json.loads(
            await get_text(
                f"https://www.sec.gov/Archives/edgar/data/{cik_nopad}/{key}/index.json",
                headers=headers,
            )
        )
        items = index.get("directory", {}).get("item") or []
        xml_name = next(
            (it["name"] for it in items
             if it["name"].endswith(".xml") and it["name"] != "primary_doc.xml"),
            None,
        )
        if xml_name is None:
            raise ValueError(f"no holdings XML in index for accession {accession}")
        await asyncio.sleep(PAUSE)
        text = await get_text(
            f"https://www.sec.gov/Archives/edgar/data/{cik_nopad}/{key}/{xml_name}",
            headers=headers,
        )
        reason = detect_error_payload(text)
        if reason is not None:
            raise ValueError(
                f"filing XML for {accession} is an error payload ({reason})")
        try:
            ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError(
                f"filing XML for {accession} does not parse: {exc}") from exc
        tmp = path.with_suffix(".xml.part")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)  # atomic publish
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        _prune_old()
        log.info("edgar-xml-cache: cached %s (%d bytes)", path.name, len(text))
        return text
