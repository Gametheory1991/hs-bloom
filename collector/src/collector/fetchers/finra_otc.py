"""FINRA OTC Market data — keyless api.finra.org (group otcMarket).

All 8 datasets verified working 2026-10-06 with NO authentication
(the otce.finra.org site calls this API directly from the browser).

Datasets:
  YearlyMarketStatistics   annual, 2014 -> 2025 (114 rows)
  monthlyMarketStatistics  monthly, 2014-05 -> 2026-08 (1360 rows)
  monthlyTop100            monthly top-100 issues, 2014-05 ->
  otcDailyList             daily corporate-action feed (additions, deletions,
                           symbol/name changes, attribute changes, bankruptcy,
                           dividends/distributions/splits), partition calendarDay
  thresholdList            OTC threshold securities (Reg SHO / FINRA 4320),
                           partition tradeDate
  tradingHaltsCurrent      current halts/resumes snapshot, partition asOfDate
  otcSecurityMaster        symbol directory (~10k symbols), partition asOfDate
  MarketParticipantList    MPID directory, partition asOfDate

Stored:
  cycle:otc-yearly-{mkt}-{sec}-{metric}     annual points (date = Jan 1)
  cycle:otc-monthly-{mkt}-{sec}-{metric}    monthly points
  cycle:otc-top100-shares / -dollarvol / -trades? (monthly market totals)
  docs:
    otc-top100-{YYYY-MM}        top-100 rows for the month
    otc-dailylist-{YYYY-MM-DD}  categorized daily-list events
    otc-threshold-{YYYY-MM-DD}  threshold securities snapshot
    otc-halts-current          current halts snapshot
    otc-secmaster              symbol -> {name, issuer, market} map
    otc-mplist                 MPID -> name map
    finra_otc                  job status doc

Full history on first run, then incremental (new months/days only).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from collector.store import Store

log = logging.getLogger(__name__)

BASE = "https://api.finra.org"
GROUP = "otcMarket"

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

# metric fields we turn into series for the statistics datasets
STAT_METRICS = (
    "totalShareVolume",
    "totalDollarVolume",
    "totalTransactionCount",
    "totalIssueCount",
    "totalIssueTradedCount",
    "averageShareVolume",
    "averageDollarVolume",
    "averagePrice",
)

# daily-list event categories from the flag fields
def _categorize_dl(row: dict) -> str:
    if (row.get("securityAddFlag") or "").strip().upper() == "Y":
        return "additions"
    if (row.get("securityDeleteFlag") or "").strip().upper() == "Y":
        return "deletions"
    if (row.get("changeSymbolFlag") or "").strip().upper() == "Y":
        return "symbol_changes"
    if (row.get("bankruptcyFlag") or "").strip().upper() == "Y":
        return "bankruptcy"
    if row.get("dividendTypeCode") or row.get("dividendTypeDescription"):
        return "dividends"
    if (row.get("changeSecurityAttributeFlag") or "").strip().upper() == "Y":
        return "attribute_changes"
    if (row.get("dailyListReasonDescription") or "").strip():
        return "other"
    return "other"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-") or "unknown"


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


async def _post(client: httpx.AsyncClient, dataset: str, body: dict) -> list[dict]:
    r = await client.post(
        f"{BASE}/data/group/{GROUP}/name/{dataset}", headers=HEADERS, json=body,
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        # error envelope
        raise RuntimeError(f"FINRA OTC {dataset}: {data.get('message', data)}")
    return data


async def _pull_all(client: httpx.AsyncClient, dataset: str,
                    extra: dict | None = None) -> list[dict]:
    """Paginate through a whole dataset (limit/offset)."""
    rows: list[dict] = []
    offset = 0
    while True:
        body = {"limit": 5000, "offset": offset}
        if extra:
            body.update(extra)
        batch = await _post(client, dataset, body)
        rows.extend(batch)
        if len(batch) < 5000:
            break
        offset += 5000
    return rows


def _store_stats(store: Store, rows: list[dict], date_field: str,
                 prefix: str) -> int:
    """Store per (market, securityType, metric) series. Returns point count."""
    n = 0
    for row in rows:
        dstr = (row.get(date_field) or "")[:10]
        try:
            d = date.fromisoformat(dstr)
        except ValueError:
            continue
        mkt = _slug(row.get("marketDescription"))
        sec = _slug(row.get("securityTypeDescription"))
        for metric in STAT_METRICS:
            v = _num(row.get(metric))
            if v is None:
                continue
            store.upsert_points(f"cycle:otc-{prefix}-{mkt}-{sec}-{metric}", [(d, v)])
            n += 1
    return n


async def _fetch_yearly(client, store) -> str:
    rows = await _pull_all(client, "YearlyMarketStatistics")
    n = _store_stats(store, rows, "yearStartDate", "yearly")
    yrs = sorted({(r.get("yearStartDate") or "")[:4] for r in rows if r.get("yearStartDate")})
    return f"{len(rows)} rows, {yrs[0] if yrs else '?'}->{yrs[-1] if yrs else '?'}, {n} points"


async def _fetch_monthly(client, store) -> str:
    have = set(store.points("cycle:otc-monthly-other-otc-all-totalShareVolume"))
    rows = await _pull_all(client, "monthlyMarketStatistics")
    new_rows = [r for r in rows
                if date.fromisoformat((r.get("monthStartDate") or "")[:10]) not in have]
    n = _store_stats(store, new_rows, "monthStartDate", "monthly")
    # market-wide monthly totals series for the trend chart
    by_month: dict[date, dict] = {}
    for r in new_rows:
        try:
            d = date.fromisoformat((r.get("monthStartDate") or "")[:10])
        except ValueError:
            continue
        agg = by_month.setdefault(d, {"shares": 0.0, "dollar": 0.0, "trades": 0.0})
        for k, f in (("shares", "totalShareVolume"), ("dollar", "totalDollarVolume"),
                     ("trades", "totalTransactionCount")):
            v = _num(r.get(f))
            if v:
                agg[k] += v
    for d, agg in by_month.items():
        store.upsert_points("cycle:otc-monthly-total-shares", [(d, agg["shares"])])
        store.upsert_points("cycle:otc-monthly-total-dollarvol", [(d, agg["dollar"])])
        store.upsert_points("cycle:otc-monthly-total-trades", [(d, agg["trades"])])
    return f"{len(rows)} rows ({len(new_rows)} new), {n} points"


async def _fetch_top100(client, store) -> str:
    # find months we already have via the totals series
    have = {d.strftime("%Y-%m") for d in store.points("cycle:otc-top100-total-shares")}
    rows = await _pull_all(client, "monthlyTop100")
    months: dict[str, list[dict]] = {}
    for r in rows:
        m = (r.get("monthStartDate") or "")[:7]
        if m and m not in have:
            months.setdefault(m, []).append(r)
    for m, mrows in sorted(months.items()):
        tot_sh = sum(_num(r.get("numberOfSharesTraded")) or 0 for r in mrows)
        tot_dv = sum(_num(r.get("dollarVolume")) or 0 for r in mrows)
        d = date.fromisoformat(m + "-01")
        store.upsert_points("cycle:otc-top100-total-shares", [(d, tot_sh)])
        store.upsert_points("cycle:otc-top100-total-dollarvol", [(d, tot_dv)])
        # top 100 by shares for the doc
        top = sorted(mrows, key=lambda r: _num(r.get("numberOfSharesTraded")) or 0,
                     reverse=True)[:100]
        store.put_doc(f"otc-top100-{m}", {
            "month": m,
            "rows": [
                {"symbol": r.get("issueSymbolIdentifier"),
                 "name": r.get("issueName"),
                 "market": r.get("marketDescription"),
                 "shares": _num(r.get("numberOfSharesTraded")),
                 "dollarVol": _num(r.get("dollarVolume")),
                 "close": _num(r.get("closingPrice"))}
                for r in top
            ],
        }, source="finra_otc")
    return f"{len(rows)} rows, {len(months)} new months"


async def _fetch_dailylist(client, store, days: int = 45) -> str:
    """Incremental: last `days` calendar days, skip ones already stored."""
    total_new = 0
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        ds = d.isoformat()
        if store.doc(f"otc-dailylist-{ds}") is not None:
            continue
        rows = await _pull_all(client, "otcDailyList", {
            "compareFilters": [{"fieldName": "calendarDay", "fieldValue": ds,
                                "compareType": "equal"}]})
        cats: dict[str, list[dict]] = {}
        for r in rows:
            cat = _categorize_dl(r)
            cats.setdefault(cat, []).append({
                "symbol": r.get("newSymbolCode") or r.get("oldSymbolCode"),
                "oldSymbol": r.get("oldSymbolCode"),
                "event": r.get("dailyListEventCode"),
                "reason": r.get("dailyListReasonDescription"),
                "desc": r.get("newSecurityDescription") or r.get("oldSecurityDescription"),
                "exDate": (r.get("exDate") or "")[:10],
                "comment": (r.get("commentText") or "")[:200],
            })
        store.put_doc(f"otc-dailylist-{ds}", {
            "date": ds, "count": len(rows),
            "categories": {k: v for k, v in cats.items()},
        }, source="finra_otc")
        total_new += 1
    return f"{total_new} new daily-list days stored"


async def _fetch_threshold(client, store, days: int = 30) -> str:
    today = date.today()
    stored = 0
    latest = None
    for i in range(days):
        d = today - timedelta(days=i)
        ds = d.isoformat()
        if store.doc(f"otc-threshold-{ds}") is not None:
            if latest is None:
                latest = ds  # first hit walking back = latest stored
            continue
        rows = await _pull_all(client, "thresholdList", {
            "compareFilters": [{"fieldName": "tradeDate", "fieldValue": ds,
                                "compareType": "equal"}]})
        if not rows:
            continue
        store.put_doc(f"otc-threshold-{ds}", {
            "date": ds,
            "rows": [{"symbol": r.get("issueSymbolIdentifier"),
                      "name": r.get("issueName"),
                      "market": r.get("marketCategoryDescription"),
                      "regSho": r.get("regShoThresholdFlag"),
                      "rule4320": r.get("rule4320Flag")} for r in rows],
        }, source="finra_otc")
        stored += 1
        if latest is None:
            latest = ds
    return f"{stored} new threshold snapshots" + (f", latest {latest}" if latest else "")


async def _fetch_halts(client, store) -> str:
    rows = await _pull_all(client, "tradingHaltsCurrent")
    store.put_doc("otc-halts-current", {
        "asOf": date.today().isoformat(),
        "rows": [{
            "symbol": r.get("issueSymbolIdentifier"),
            "name": r.get("securityDescription"),
            "action": r.get("haltActionCode"),
            "reason": r.get("haltReasonDescription"),
            "haltTime": r.get("haltActionTimestamp"),
            "resumeTime": r.get("tradeResumptionTimestamp"),
            "originator": r.get("originatingRegulatorCode"),
        } for r in rows],
    }, source="finra_otc")
    return f"{len(rows)} current halt/resume rows"


async def _fetch_secmaster(client, store) -> str:
    rows = await _pull_all(client, "otcSecurityMaster", {
        "fields": ["issueSymbolIdentifier", "securityDescription", "issuerName",
                   "market", "issueType", "asOfDate"]})
    symmap = {}
    for r in rows:
        s = r.get("issueSymbolIdentifier")
        if s:
            symmap[s] = {"name": r.get("securityDescription"),
                         "issuer": r.get("issuerName"),
                         "market": r.get("market"),
                         "type": r.get("issueType")}
    asof = (rows[0].get("asOfDate") or "")[:10] if rows else ""
    store.put_doc("otc-secmaster", {"asOf": asof, "count": len(symmap),
                                   "symbols": symmap}, source="finra_otc")
    return f"{len(symmap)} symbols (as of {asof})"


async def _fetch_mplist(client, store) -> str:
    rows = await _pull_all(client, "MarketParticipantList")
    store.put_doc("otc-mplist", {
        "rows": [{"mpid": r.get("MPID"), "name": r.get("marketParticipantName")}
                 for r in rows],
    }, source="finra_otc")
    return f"{len(rows)} market participants"


async def fetch_finra_otc(store: Store) -> str:
    """Daily job. Full history on first run, then incremental. Keyless."""
    results = []
    async with httpx.AsyncClient() as client:
        for name, fn in (
            ("yearly", _fetch_yearly),
            ("monthly", _fetch_monthly),
            ("top100", _fetch_top100),
            ("dailylist", _fetch_dailylist),
            ("threshold", _fetch_threshold),
            ("halts", _fetch_halts),
            ("secmaster", _fetch_secmaster),
            ("mplist", _fetch_mplist),
        ):
            try:
                results.append(f"{name}: {await fn(client, store)}")
            except Exception as e:  # noqa: BLE001 — one dataset must not kill the job
                log.warning("finra_otc %s failed: %s", name, e)
                results.append(f"{name}: ERROR {e}")
    store.put_doc("finra_otc", {"updated": date.today().isoformat(),
                                "results": results}, source="finra_otc")
    store.record_success("finra_otc", "api.finra.org")
    return "; ".join(results)
