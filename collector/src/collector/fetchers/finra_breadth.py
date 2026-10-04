"""FINRA bond market breadth + sentiment (keyless public dynarep API).

Harry asked for the two public FINRA fixed-income pages:

  breadth:   https://www.finra.org/finra-data/fixed-income/market-activity
  sentiment:  https://www.finra.org/finra-data/fixed-income/market-sentiment

Both pages are JS apps that query FINRA's public dynamic-reporting API
(the same calls a visitor's browser makes — no API key, no login):

  POST https://services-dynarep.ddwa.finra.org/public/reporting/v2/data
       /group/FixedIncomeMarket/name/{dataset}

  breadth   -> dataset MarketActivityAggregates
               (advances / declines / unchanged / 52wk high-low /
                dollar volume / total issues traded, per bond type x sector)
  sentiment -> dataset MarketSentimentAggregates
               (dealer buy-from-customer / sell-to-customer / inter-dealer /
                affiliate flows x issue type, with trade/issue/volume counts)

Auth flow: GET the page's template composite first — it sets an
XSRF-TOKEN cookie (and Cloudflare cookies); the data POST then carries
X-XSRF-TOKEN. A browser User-Agent is required: Cloudflare returns
1010 for non-browser UAs on this host (verified 2026-10-04).

History for both datasets starts 2018-01-22 (verified live); published
daily, end-of-day. Full backfill runs once on an empty store (~50
paged requests); afterwards the job refreshes the trailing 10 days.

Stored as cycle:finra-breadth-* / cycle:finra-sent-*, plus a
"finra_breadth" snapshot doc with as_of + series list. On failure the
doc records the error and the job raises (scheduler tracks last_error);
the next daily run retries — same graceful-degradation contract as the
other FINRA fetchers.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import httpx

from collector.store import Store

log = logging.getLogger(__name__)

HOST = "https://services-dynarep.ddwa.finra.org"
DATA_URL = (HOST + "/public/reporting/v2/data/group/FixedIncomeMarket"
            "/name/{dataset}")
# Template composites behind the two public pages (embedded in the page
# HTML as finraDynamicReportingExplorer.templateId). The GET both proves
# the template exists and plants the XSRF-TOKEN cookie.
TEMPLATES = {
    "MarketActivityAggregates":
        "template-bfb38ac6-3c00-4678-b405-f4e66f4003b4",
    "MarketSentimentAggregates":
        "template-ea9803c2-20d4-49a9-b408-c7eaa75dccdd",
}
SOURCE = "finra-bond-breadth"
HISTORY_START = date(2018, 1, 22)  # earliest date in both datasets (live)
PAGE_LIMIT = 5000  # server-side Record-Max-Limit
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------------- breadth (MarketActivityAggregates) ----------------
# fieldB/C/D mean different sectors per bond type (per dataset schema).
BREADTH_BONDS = (("corp", "CORP", "Corp"), ("agency", "AGENCY", "Agency"),
                 ("144a", "CORP_144A", "144A"))
BREADTH_SECTORS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "corp": (("all", "fieldA", "All"), ("conv", "fieldB", "Convertibles"),
             ("hy", "fieldC", "High Yield"), ("ig", "fieldD", "Inv Grade")),
    "144a": (("all", "fieldA", "All"), ("conv", "fieldB", "Convertibles"),
             ("hy", "fieldC", "High Yield"), ("ig", "fieldD", "Inv Grade")),
    "agency": (("all", "fieldA", "All"), ("fannie", "fieldB", "Fannie Mae"),
               ("fhlb", "fieldC", "FHLB"), ("freddie", "fieldD", "Freddie Mac")),
}
BREADTH_METRICS = (("adv", "Advances"), ("dec", "Declines"),
                   ("unch", "Unchanged"), ("hi52", "52 Week High"),
                   ("lo52", "52 Week Low"), ("dvol", "Dollar Volume"),
                   ("issues", "Total Issues Traded"))

# ---------------- sentiment (MarketSentimentAggregates) ----------------
SENT_BONDS = (("corp", "Corp", "Corp"), ("agency", "Agency", "Agency"),
              ("144a", "Corp_144a", "144A"))
SENT_ISSUES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "corp": (("all", "All Securities", "All"),
             ("ig", "Investment Grade", "Inv Grade"),
             ("hy", "High Yield", "High Yield"),
             ("conv", "Convertible Bonds", "Convertibles"),
             ("church", "Church Bonds", "Church"),
             ("eln", "Equity Linked Notes", "ELN")),
    "144a": (("all", "All Securities", "All"),
             ("ig", "Investment Grade", "Inv Grade"),
             ("hy", "High Yield", "High Yield"),
             ("conv", "Convertible Bonds", "Convertibles"),
             ("church", "Church Bonds", "Church"),
             ("eln", "Equity Linked Notes", "ELN")),
    "agency": (("all", "All Securities", "All"),
               ("fhlb", "FHLB", "FHLB"),
               ("fannie", "Fannie Mae", "Fannie Mae"),
               ("freddie", "Freddie Mac", "Freddie Mac")),
}
SENT_FLOWS = (("dbuy", "Dealer Buy from Customer", "Dealer buys"),
              ("dsell", "Dealer Sell to Customer", "Dealer sells"),
              ("inter", "Inter-Dealer", "Inter-dealer"),
              ("total", "All Securities", "Total"))
SENT_METRICS = (("vol", "totalTradedVolume", "$M par"),
                ("trades", "totalTransactionsCount", "trades"))


def breadth_series_ids() -> list[str]:
    ids: list[str] = []
    for bslug, _, _ in BREADTH_BONDS:
        for sslug, _, _ in BREADTH_SECTORS[bslug]:
            for mslug, _ in BREADTH_METRICS:
                ids.append(f"finra-breadth-{bslug}-{sslug}-{mslug}")
            ids.append(f"finra-breadth-{bslug}-{sslug}-adspread")
    return ids


def sentiment_series_ids() -> list[str]:
    ids: list[str] = []
    for bslug, _, _ in SENT_BONDS:
        for islug, _, _ in SENT_ISSUES[bslug]:
            for fslug, _, _ in SENT_FLOWS:
                for mslug, _, _ in SENT_METRICS:
                    ids.append(f"finra-sent-{bslug}-{islug}-{fslug}-{mslug}")
            ids.append(f"finra-sent-{bslug}-{islug}-netflow")
    return ids


class DynarepSession:
    """httpx client holding the XSRF/Cloudflare cookies for the FINRA
    public reporting API. Pass a fake in tests via session_factory."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=60, follow_redirects=True,
            headers={"User-Agent": BROWSER_UA,
                     "Accept": "application/json",
                     "Origin": "https://www.finra.org",
                     "Referer": ("https://www.finra.org/finra-data/"
                                 "fixed-income/market-sentiment")},
            cookies=httpx.Cookies())

    async def __aenter__(self) -> "DynarepSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _xsrf(self, dataset: str) -> str:
        """GET the template composite; returns the XSRF-TOKEN cookie."""
        url = (f"{HOST}/public/reporting/v2/template/"
               f"{TEMPLATES[dataset]}/composite")
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
        rows: list[dict] = []
        offset = 0
        while True:
            body = {
                "fields": fields,
                "dateRangeFilters": [{
                    "startDate": start.strftime("%Y-%m-%d 00:00:00.000"),
                    "endDate": end.strftime("%Y-%m-%d 23:59:59.000"),
                    "fieldName": "originalTradeReportedDate",
                }],
                "sortFields": ["originalTradeReportedDate"],
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


def _parse_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def parse_breadth(rows: list[dict]) -> dict[str, dict[date, float]]:
    """Pivot activity rows -> {series_id: {date: value}}.

    Dollar Volume is $M par; counts are integers. Also derives the
    advance-decline spread (adv - dec) per bond x sector."""
    out: dict[str, dict[date, float]] = {}
    by_key: dict[tuple[str, str, str, date], float] = {}
    for r in rows:
        try:
            bond = r["bondType"]
            dtype = r["dataTypeDescription"]
            asof = _parse_date(r["originalTradeReportedDate"])
        except (KeyError, ValueError):
            continue
        bslug = next((b for b, code, _ in BREADTH_BONDS if code == bond),
                     None)
        mslug = next((m for m, label in BREADTH_METRICS if label == dtype),
                     None)
        if bslug is None or mslug is None:
            continue
        for sslug, field, _ in BREADTH_SECTORS[bslug]:
            try:
                val = float(r[field])
            except (KeyError, TypeError, ValueError):
                continue
            sid = f"finra-breadth-{bslug}-{sslug}-{mslug}"
            out.setdefault(sid, {})[asof] = val
            by_key[(bslug, sslug, mslug, asof)] = val
    # derived advance-decline spread
    for bslug, _, _ in BREADTH_BONDS:
        for sslug, _, _ in BREADTH_SECTORS[bslug]:
            spread: dict[date, float] = {}
            dates = {d for (b, s, m, d) in by_key
                     if b == bslug and s == sslug}
            for d in dates:
                a = by_key.get((bslug, sslug, "adv", d))
                c = by_key.get((bslug, sslug, "dec", d))
                if a is not None and c is not None:
                    spread[d] = a - c
            if spread:
                out[f"finra-breadth-{bslug}-{sslug}-adspread"] = spread
    return out


def parse_sentiment(rows: list[dict]) -> dict[str, dict[date, float]]:
    """Pivot sentiment rows -> {series_id: {date: value}}.

    Also derives net customer flow (dealer-sell minus dealer-buy volume):
    positive = customers net buying from dealers (risk-on)."""
    out: dict[str, dict[date, float]] = {}
    vols: dict[tuple[str, str, str, date], float] = {}
    for r in rows:
        try:
            bond = r["bondType"]
            issue = r["issueTypeCode"]
            flow = r["fieldTypeCode"]
            asof = _parse_date(r["originalTradeReportedDate"])
        except (KeyError, ValueError):
            continue
        bslug = next((b for b, code, _ in SENT_BONDS if code == bond), None)
        if bslug is None:
            continue
        islug = next((i for i, code, _ in SENT_ISSUES[bslug]
                      if code == issue), None)
        fslug = next((f for f, code, _ in SENT_FLOWS if code == flow), None)
        if islug is None or fslug is None:
            continue
        for mslug, field, _ in SENT_METRICS:
            try:
                val = float(r[field])
            except (KeyError, TypeError, ValueError):
                continue
            out.setdefault(
                f"finra-sent-{bslug}-{islug}-{fslug}-{mslug}", {})[asof] = val
            if mslug == "vol":
                vols[(bslug, islug, fslug, asof)] = val
    # derived net customer flow (dealer sells - dealer buys), $M par
    for bslug, _, _ in SENT_BONDS:
        for islug, _, _ in SENT_ISSUES[bslug]:
            net: dict[date, float] = {}
            dates = {d for (b, i, f, d) in vols
                     if b == bslug and i == islug}
            for d in dates:
                sell = vols.get((bslug, islug, "dsell", d))
                buy = vols.get((bslug, islug, "dbuy", d))
                if sell is not None and buy is not None:
                    net[d] = sell - buy
            if net:
                out[f"finra-sent-{bslug}-{islug}-netflow"] = net
    return out


async def fetch_finra_breadth(store: Store,
                              today: date | None = None,
                              session_factory=DynarepSession) -> str:
    """Daily job: FINRA bond breadth + sentiment.

    Backfills to 2018-01-22 on an empty store; otherwise refreshes the
    trailing 10 days (catches late revisions)."""
    today = today or date.today()
    prev = store.doc("finra_breadth")
    have = store.points("cycle:finra-breadth-corp-all-adv")
    start = HISTORY_START if not have else today - timedelta(days=10)
    try:
        async with session_factory() as sess:
            b_rows = await sess.query(
                "MarketActivityAggregates",
                ["bondType", "dataTypeDescription",
                 "originalTradeReportedDate",
                 "fieldA", "fieldB", "fieldC", "fieldD"],
                start, today)
            s_rows = await sess.query(
                "MarketSentimentAggregates",
                ["bondType", "originalTradeReportedDate", "issueTypeCode",
                 "fieldTypeCode", "totalTransactionsCount",
                 "totalTradedSecuritiesCount", "totalTradedVolume"],
                start, today)
    except Exception as exc:  # noqa: BLE001 — record + retry tomorrow
        payload: dict = {}
        if prev and isinstance(prev.payload, dict):
            payload = {k: v for k, v in prev.payload.items()
                       if k in ("as_of", "series")}
        payload["status"] = "error"
        payload["error"] = str(exc)[:300]
        store.put_doc("finra_breadth", payload, source=SOURCE)
        raise RuntimeError(f"finra_breadth fetch failed: {exc}") from exc
    if not b_rows or not s_rows:
        raise RuntimeError("finra_breadth: empty response from FINRA dynarep")
    breadth = parse_breadth(b_rows)
    sentiment = parse_sentiment(s_rows)
    stored = 0
    for sid, pts in {**breadth, **sentiment}.items():
        if pts:
            store.upsert_points(f"cycle:{sid}", sorted(pts.items()))
            stored += 1
    as_of = max(
        (d for pts in {**breadth, **sentiment}.values() for d in pts),
        default=today).isoformat()
    store.put_doc("finra_breadth", {
        "as_of": as_of,
        "status": "ok",
        "breadth_rows": len(b_rows),
        "sentiment_rows": len(s_rows),
        "series": sorted({**breadth, **sentiment}),
    }, source=SOURCE)
    log.info("finra_breadth: %d series, as_of %s", stored, as_of)
    return SOURCE
