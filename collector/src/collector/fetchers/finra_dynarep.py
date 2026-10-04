"""Shared session for FINRA's public dynamic-reporting API (dynarep).

Used by finra_breadth.py (MarketActivityAggregates, MarketSentimentAggregates)
and finra_corp.py (MostActiveCorporateSecurities,
MostActiveCorporate144ASecurities).

No auth, no API key — the same calls a visitor's browser makes. Flow: GET
the dataset's template composite (sets the XSRF-TOKEN cookie), then POST
data queries with X-XSRF-TOKEN. A browser User-Agent is required:
Cloudflare returns 1010 for non-browser UAs on this host (verified
2026-10-04). Template IDs are embedded in the finra.org page HTML / app JS
(finraDynamicReportingExplorer config).
"""
from __future__ import annotations

import json
import logging
from datetime import date

import httpx

log = logging.getLogger(__name__)

HOST = "https://services-dynarep.ddwa.finra.org"
DATA_URL = (HOST + "/public/reporting/v2/data/group/FixedIncomeMarket"
            "/name/{dataset}")
TEMPLATE_URL = HOST + "/public/reporting/v2/template/{template}/composite"

# dataset -> template composite ID
TEMPLATES = {
    "MarketActivityAggregates":
        "template-bfb38ac6-3c00-4678-b405-f4e66f4003b4",
    "MarketSentimentAggregates":
        "template-ea9803c2-20d4-49a9-b408-c7eaa75dccdd",
    # market-corp page ("Most Active Corporate Bonds"); template IDs from the
    # page's app-dynamic-reporting.js bundle (verified 2026-10-04)
    "MostActiveCorporateSecurities":
        "template-307363e8-4f36-4f21-b409-2eb6ae46be31",
    "MostActiveCorporate144ASecurities":
        "template-ac079c32-d740-4282-b410-d5a0bb513f18",
}

# dataset -> date field used in dateRangeFilters (differs by dataset)
DATE_FIELDS = {
    "MarketActivityAggregates": "originalTradeReportedDate",
    "MarketSentimentAggregates": "originalTradeReportedDate",
    "MostActiveCorporateSecurities": "reportDate",
    "MostActiveCorporate144ASecurities": "reportDate",
}

PAGE_LIMIT = 5000  # server-side Record-Max-Limit
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class DynarepSession:
    """httpx client holding the XSRF/Cloudflare cookies for the FINRA
    public reporting API. Pass a fake in tests via session_factory."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=60, follow_redirects=True,
            headers={"User-Agent": BROWSER_UA,
                     "Accept": "application/json",
                     "Origin": "https://www.finra.org",
                     "Referer": "https://www.finra.org/finra-data/"},
            cookies=httpx.Cookies())

    async def __aenter__(self) -> "DynarepSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _xsrf(self, dataset: str) -> str:
        """GET the template composite; returns the XSRF-TOKEN cookie."""
        url = TEMPLATE_URL.format(template=TEMPLATES[dataset])
        resp = await self._client.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} for template "
                               f"composite {dataset}")
        token = self._client.cookies.get("XSRF-TOKEN")
        if not token:
            raise RuntimeError("no XSRF-TOKEN cookie from FINRA dynarep")
        return token

    async def query(self, dataset: str, fields: list[str],
                    start: date, end: date) -> list[dict]:
        token = await self._xsrf(dataset)
        date_field = DATE_FIELDS[dataset]
        rows: list[dict] = []
        offset = 0
        while True:
            body = {
                "fields": fields,
                "dateRangeFilters": [{
                    "startDate": start.strftime("%Y-%m-%d 00:00:00.000"),
                    "endDate": end.strftime("%Y-%m-%d 23:59:59.000"),
                    "fieldName": date_field,
                }],
                "sortFields": [date_field],
                "offset": offset,
                "limit": PAGE_LIMIT,
            }
            resp = await self._client.post(
                DATA_URL.format(dataset=dataset), json=body,
                headers={"Content-Type": "application/json",
                         "X-XSRF-TOKEN": token})
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} for "
                                   f"{dataset} offset={offset}")
            payload = resp.json()
            rb = payload.get("returnBody", {})
            data = json.loads(rb.get("data", "[]"))
            rows.extend(data)
            total = int(rb.get("headers", {}).get("Record-Total", ["0"])[0])
            offset += len(data)
            if offset >= total or not data:
                break
        return rows
