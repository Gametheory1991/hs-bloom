"""OpenFIGI symbology enrichment — free API key required.

Probing 2026-10-03: GETs to api.openfigi.com work keyless, but POST
/v3/mapping (the endpoint that resolves tickers/CUSIPs to FIGI) consistently
times out without a key — the WAF appears to drop unauthenticated POSTs.
So this job reads OPENFIGI_API_KEY from the environment:

  - key absent  -> log + skip cleanly (no doc, no failure)
  - key present -> POST batched ticker mappings (25 per request, 3s gaps)
                   with the X-OPENFIGI-APIKEY header; any request failure
                   degrades to a clean skip, never a failed job

Free key signup: https://www.openfigi.com/ (free registration; the key
unlocks the documented 25-requests-per-minute tier).

The job: resolve every watchlist ticker (hyperscalers + the three coverage
universes — see watchlist.py) to its FIGI/ISIN/CUSIP/exchange via
idType=ID_TICKER, and store the mapping table as the `openfigi_map` doc.
This backs the TRACE bond work and the universe builders: once per-bond
CUSIPs exist, the same table resolves issuer identity without guessing.
Refreshed weekly; symbology barely moves.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date

from collector.fetchers.watchlist import watchlist_tickers
from collector.http import PostJson
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "openfigi-mapping"
URL = "https://api.openfigi.com/v3/mapping"

BATCH = 25
REQUEST_GAP = 3.0


def parse_mappings(results: list) -> dict[str, dict]:
    """OpenFIGI mapping response -> {TICKER: {figi, name, exchCode, ...}}.

    Response is a list parallel to the request jobs: [{data: [{...}]}].
    Takes the first (primary) result per job; jobs with no data are dropped.
    """
    out: dict[str, dict] = {}
    for job in results:
        if not isinstance(job, dict):
            continue
        data = job.get("data")
        if not data:
            continue
        first = data[0]
        ticker = str(first.get("ticker") or "").upper()
        if not ticker:
            continue
        out[ticker] = {
            "figi": first.get("figi"),
            "compositeFIGI": first.get("compositeFIGI"),
            "name": first.get("name"),
            "exchCode": first.get("exchCode"),
            "securityType": first.get("securityType"),
            "marketSector": first.get("marketSector"),
            "shareClassFIGI": first.get("shareClassFIGI"),
        }
    return out


async def fetch_openfigi(store: Store, post_json: PostJson) -> str:
    api_key = os.environ.get("OPENFIGI_API_KEY", "").strip()
    if not api_key:
        log.info("openfigi: no OPENFIGI_API_KEY set — skipping (free key: openfigi.com)")
        return "openfigi-skipped-no-key"
    tickers = watchlist_tickers()
    headers = {"X-OPENFIGI-APIKEY": api_key}
    mappings: dict[str, dict] = {}
    try:
        for i in range(0, len(tickers), BATCH):
            if i:
                await asyncio.sleep(REQUEST_GAP)
            jobs = [{"idType": "TICKER", "idValue": t, "exchCode": "US"}  # ID_TICKER returns nothing (verified live 2026-10-03)
                    for t in tickers[i:i + BATCH]]
            body = await post_json(URL, jobs, headers=headers)
            results = body if isinstance(body, list) else json.loads(body)
            mappings.update(parse_mappings(results))
    except Exception as exc:  # noqa: BLE001 — symbology is enrichment, never fatal
        log.warning("openfigi mapping failed, skipping: %s", exc)
        return "openfigi-skipped-error"
    if not mappings:
        log.warning("openfigi returned no mappings — skipping doc write")
        return "openfigi-skipped-empty"
    store.put_doc("openfigi_map", {
        "as_of": date.today().isoformat(),
        "tickers": tickers,
        "mappings": mappings,
        "coverage": f"{len(mappings)}/{len(tickers)}",
    }, SOURCE)
    return SOURCE
