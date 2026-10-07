"""FINRA ATS Transparency — weekly dark-pool volume (the "dark pool feed").

Two sources, one job:

KEYLESS — monthly ATS block summary (live today, no API key):
  GET  https://api.finra.org/partitions/group/otcMarket/name/blocksSummary
  GET  https://api.finra.org/data/group/otcMarket/name/blocksSummary?limit=N
Verified 2026-10-06: 123 monthly partitions, 2016-06-01 .. 2026-08-01,
30 fields per venue-month: MPID, marketParticipantName, summaryStartDate,
monthStartDate, totalShareQuantity, totalTradeCount, ATSSharePercent,
ATSTradePercent, block-trade detail (ATSBlockCount/Quantity/SharePercent…),
averageTradeSize, ranks. Per-venue monthly ATS totals — the dark-pool
venue leaderboard, free and keyless.

KEYED — weekly ATS datasets (FINRA OAuth2 client-credentials; 401 without
a key):
  ATS_W_FIRM       weekly ATS volume by firm/venue
  ATS_W_SMBL       weekly by security
  ATS_W_SMBL_FIRM  weekly by security x firm
  + "<dataset>HISTORIC" variants for history beyond the rolling-12-month
    production window (per FINRA docs, history back to May 2014).
Token endpoint (verified from FINRA's own docs + finra-py):
  POST https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token
  with Authorization: Basic base64(client_id:client_secret) and
  grant_type=client_credentials in the form body.
Free key: https://gateway.finra.org/app/api-console

Without FINRA_CLIENT_ID / FINRA_CLIENT_SECRET the keyed pull is skipped
gracefully (warning, no crash, keyless monthly data still lands).

Field mapping is defensive: requests ask for ALL fields (no "fields"
filter, so a renamed column can't 400 the call) and rows are parsed by
header name against alias lists derived from FINRA's documented examples
(developer.finra.org Query API docs: weeklysummary, blocksSummary).

Stored:
  cycle:ats-{mpid}-shares / -trades        weekly per-venue (keyed)
  cycle:ats-total-shares / -trades         weekly market-wide (keyed)
  cycle:ats-sym-{TICKER}-shares / -trades  weekly per-symbol, top-500 by
                                           volume per week (keyed)
  cycle:ats-m-{mpid}-shares / -trades / -sharepct
                                           monthly per-venue (keyless)
  cycle:ats-m-total-shares / -trades       monthly market-wide (keyless)
Docs: ats_venues (MPID -> ATS name master list), finra_ats (status),
ats_blocks_latest (latest monthly venue rows), ats-week-{YYYY-MM-DD}
(latest keyed weekly snapshot: top venues + top-50 symbols w/ top venues).

Security: the client secret is used transiently for the token fetch and
never logged; the bearer token lives in module memory only and is never
written to disk or to the store. Error messages strip query strings
(via the injected http helpers); the token request itself carries no
secret in the URL.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import io
import logging
import os
import re
import time
from datetime import date

import httpx

from collector.http import USER_AGENT
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "finra-ats"
API_BASE = "https://api.finra.org"
GROUP_CANDIDATES = ("otcMarket", "OTCMarket")  # discovered at runtime
TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"

KEYED_DATASETS = ("ATS_W_FIRM", "ATS_W_SMBL", "ATS_W_SMBL_FIRM")
HISTORIC_SUFFIXES = ("HISTORIC", "_HISTORIC")  # per FINRA docs: appended
BLOCKS_DATASET = "blocksSummary"

PAGE_LIMIT = 5000
API_GAP = 0.25
TOKEN_SKEW = 300  # refresh this many seconds before expiry
# FINRA docs: "cache the access_token for 30 minutes before regenerating".
TOKEN_CACHE_MAX = 1800

# Column aliases — first match wins. Derived from FINRA's documented
# examples (developer.finra.org): blocksSummary mock sample, weeklysummary
# example, and the generic Query API filter examples (ats_mp_id).
_ALIAS_WEEK = ("weekStartDate", "summaryStartDate")
_ALIAS_MONTH = ("monthStartDate", "summaryStartDate")
_ALIAS_MPID = ("ats_mp_id", "MPID", "mpid", "marketParticipantId",
               "firmMpid", "atsMpid")
_ALIAS_NAME = ("marketParticipantName", "atsName", "firmName",
               "participantName", "venueName")
_ALIAS_SYMBOL = ("issueSymbolIdentifier", "symbol", "issueSymbol")
_ALIAS_ISSUE_NAME = ("issueName", "securityName", "issueDescription")
_ALIAS_SHARES = ("totalShareQuantity", "totalWeeklyShareQuantity",
                 "weeklyShareQuantity", "shareQuantity")
_ALIAS_TRADES = ("totalTradeCount", "totalWeeklyTradeCount",
                 "weeklyTradeCount", "totalTradeQuantity", "tradeCount")
_ALIAS_SHAREPCT = ("ATSSharePercent", "atsSharePercent", "sharePercent")
_ALIAS_TRADEPCT = ("ATSTradePercent", "atsTradePercent", "tradePercent")

SYMBOL_LIMIT = 500        # per-week symbols kept (bound storage)
SNAPSHOT_SYMBOLS = 50     # symbols in the weekly snapshot doc
SNAPSHOT_VENUES = 3       # top venues per symbol in the snapshot doc
BACKFILL_WEEKS_PER_RUN = 52   # bound one run's historic pull
# Granular venue x security history is ~100x the firm/symbol volume;
# bound its backfill and record the cursor honestly in the status doc.
SMBL_FIRM_BACKFILL_WEEKS = 52

KEY_MSG = ("FINRA API key required for weekly ATS detail — free at "
           "gateway.finra.org/app/api-console "
           "(FINRA_CLIENT_ID / FINRA_CLIENT_SECRET)")


def _pick(row: dict, aliases: tuple[str, ...]):
    for a in aliases:
        v = row.get(a)
        if v not in (None, ""):
            return v
    return None


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _slug(mpid: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (mpid or "").lower())


def _sym(sym: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (sym or "").upper())


# ---------------------------------------------------------------------------
# OAuth2 client-credentials (in-memory token cache, never logged to disk)
# ---------------------------------------------------------------------------

_token_cache: dict = {"token": None, "expires_at": 0.0, "group": None}


def _basic(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def fetch_token(client_id: str, client_secret: str) -> str:
    """Bearer token, cached in memory until ~expiry (30-min cap per docs).

    The secret travels only in the Authorization header of this one call
    and is never logged, cached, or written anywhere.
    """
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=20,
                                 headers={"User-Agent": USER_AGENT}) as c:
        resp = await c.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": _basic(client_id, client_secret)},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"FINRA token request failed: HTTP {resp.status_code}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError("FINRA token response missing access_token")
    ttl = body.get("expires_in") or 3600
    _token_cache.update({
        "token": token,
        "expires_at": now + min(max(ttl - TOKEN_SKEW, 60), TOKEN_CACHE_MAX),
    })
    return token


# ---------------------------------------------------------------------------
# Query API plumbing (auth-aware; keyless datasets pass headers=None)
# ---------------------------------------------------------------------------

async def _api_get(url: str, headers: dict | None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as c:
        return await c.get(url, headers=headers)


async def _api_post(url: str, body: dict, headers: dict | None) -> str:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as c:
        resp = await c.post(url, json=body, headers=headers)
    if resp.status_code >= 400:
        safe = str(resp.url).split("?")[0]
        raise RuntimeError(f"HTTP {resp.status_code} for {safe}")
    return resp.text


async def _discover_group(headers: dict | None) -> str:
    """Group path case varies by dataset (otcMarket vs OTCMarket); the
    first candidate whose partitions endpoint doesn't 404 wins. Cached
    in the module token cache so it runs once per process."""
    if _token_cache.get("group"):
        return _token_cache["group"]
    for group in GROUP_CANDIDATES:
        url = f"{API_BASE}/partitions/group/{group}/name/{BLOCKS_DATASET}"
        resp = await _api_get(url, headers)
        if resp.status_code == 404:
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} for {url}")
        _token_cache["group"] = group
        return group
    raise RuntimeError("no usable FINRA group path for blocksSummary")


async def _partitions(group: str, dataset: str,
                      headers: dict | None) -> list[str]:
    url = f"{API_BASE}/partitions/group/{group}/name/{dataset}"
    resp = await _api_get(url, headers)
    if resp.status_code == 404:
        return []  # historic variant doesn't exist — not an error
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
    data = resp.json()
    return [p["partitions"][0]
            for p in data.get("availablePartitions", [])]


async def _pull_week(group: str, dataset: str, week: str,
                     headers: dict | None) -> list[dict]:
    """All rows for one week partition (paginated). All fields requested
    (no 'fields' filter) so renamed columns can't 400 the call; rows are
    mapped defensively by header aliases."""
    rows: list[dict] = []
    offset = 0
    while True:
        body = {
            "offset": offset,
            "compareFilters": [{"fieldName": "weekStartDate",
                                "fieldValue": week,
                                "compareType": "EQUAL"}],
            "delimiter": "|", "limit": PAGE_LIMIT, "quoteValues": False,
        }
        text = await _api_post(
            f"{API_BASE}/data/group/{group}/name/{dataset}", body, headers)
        reader = csv.reader(io.StringIO(text), delimiter="|")
        header = next(reader, None)
        if not header:
            break
        page = [dict(zip(header, r)) for r in reader if len(r) == len(header)]
        rows.extend(page)
        await asyncio.sleep(API_GAP)
        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        if offset > 500_000:  # platform offset ceiling
            log.warning("ats %s %s hit offset ceiling", dataset, week)
            break
    return rows


async def _pull_blocks_month(group: str, month: str) -> list[dict]:
    """Keyless: one monthly blocksSummary partition via compareFilter."""
    rows: list[dict] = []
    offset = 0
    while True:
        body = {
            "offset": offset,
            "compareFilters": [{"fieldName": "monthStartDate",
                                "fieldValue": month,
                                "compareType": "EQUAL"}],
            "delimiter": "|", "limit": PAGE_LIMIT, "quoteValues": False,
        }
        text = await _api_post(
            f"{API_BASE}/data/group/{group}/name/{BLOCKS_DATASET}", body, None)
        reader = csv.reader(io.StringIO(text), delimiter="|")
        header = next(reader, None)
        if not header:
            break
        page = [dict(zip(header, r)) for r in reader if len(r) == len(header)]
        rows.extend(page)
        await asyncio.sleep(API_GAP)
        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return rows


# ---------------------------------------------------------------------------
# Row normalization
# ---------------------------------------------------------------------------

def norm_firm_row(row: dict) -> dict | None:
    """ATS_W_FIRM row -> {week, mpid, name, shares, trades}."""
    week = _pick(row, _ALIAS_WEEK)
    mpid = _pick(row, _ALIAS_MPID)
    if not week or not mpid:
        return None
    return {"week": str(week)[:10], "mpid": str(mpid).strip().upper(),
            "name": (str(_pick(row, _ALIAS_NAME) or "").strip()
                     or str(mpid).strip().upper()),
            "shares": _num(_pick(row, _ALIAS_SHARES)),
            "trades": _num(_pick(row, _ALIAS_TRADES))}


def norm_symbol_row(row: dict) -> dict | None:
    """ATS_W_SMBL row -> {week, symbol, name, shares, trades}."""
    week = _pick(row, _ALIAS_WEEK)
    sym = _pick(row, _ALIAS_SYMBOL)
    if not week or not sym:
        return None
    return {"week": str(week)[:10], "symbol": _sym(str(sym)),
            "name": str(_pick(row, _ALIAS_ISSUE_NAME) or "").strip(),
            "shares": _num(_pick(row, _ALIAS_SHARES)),
            "trades": _num(_pick(row, _ALIAS_TRADES))}


def norm_sf_row(row: dict) -> dict | None:
    """ATS_W_SMBL_FIRM row -> {week, symbol, mpid, name, shares, trades}."""
    base = norm_symbol_row(row)
    if not base:
        return None
    mpid = _pick(row, _ALIAS_MPID)
    if not mpid:
        return None
    base["mpid"] = str(mpid).strip().upper()
    base["venue"] = (str(_pick(row, _ALIAS_NAME) or "").strip()
                     or base["mpid"])
    return base


def norm_blocks_row(row: dict) -> dict | None:
    """blocksSummary row -> {month, mpid, name, shares, trades, sharepct,
    tradepct, block_shares, block_trades, avg_trade_size} (all source
    cells preserved in `raw`)."""
    month = _pick(row, _ALIAS_MONTH)
    mpid = _pick(row, _ALIAS_MPID)
    if not month or not mpid:
        return None
    return {
        "month": str(month)[:10], "mpid": str(mpid).strip().upper(),
        "name": (str(_pick(row, _ALIAS_NAME) or "").strip()
                 or str(mpid).strip().upper()),
        "slice": str(row.get("summaryTypeCode") or "").strip(),
        "slice_desc": str(row.get("summaryTypeDescription") or "").strip(),
        "shares": _num(_pick(row, _ALIAS_SHARES)),
        "trades": _num(_pick(row, _ALIAS_TRADES)),
        "sharepct": _num(_pick(row, _ALIAS_SHAREPCT)),
        "tradepct": _num(_pick(row, _ALIAS_TRADEPCT)),
        "block_shares": _num(row.get("ATSBlockQuantity")),
        "block_trades": _num(row.get("ATSBlockCount")),
        "avg_trade_size": _num(row.get("averageTradeSize")),
        "raw": {k: v for k, v in row.items() if v not in (None, "")},
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_weekly_firm(store: Store, week: date,
                       rows: list[dict]) -> dict:
    """Store one week's ATS_W_FIRM rows; return {mpid: {name, shares, trades}}."""
    venues: dict[str, dict] = {}
    tot_sh, tot_tr = 0.0, 0.0
    for r in rows:
        n = norm_firm_row(r)
        if not n or n["shares"] is None:
            continue
        slug = _slug(n["mpid"])
        if not slug:
            continue
        venues[n["mpid"]] = {"name": n["name"], "shares": n["shares"],
                             "trades": n["trades"] or 0.0}
        store.upsert_points(f"cycle:ats-{slug}-shares", [(week, n["shares"])])
        store.upsert_points(f"cycle:ats-{slug}-trades",
                            [(week, n["trades"] or 0.0)])
        tot_sh += n["shares"]
        tot_tr += n["trades"] or 0.0
    if venues:
        store.upsert_points("cycle:ats-total-shares", [(week, tot_sh)])
        store.upsert_points("cycle:ats-total-trades", [(week, tot_tr)])
    return venues


def _store_weekly_symbols(store: Store, week: date,
                          rows: list[dict]) -> dict:
    """Top-SYMBOL_LIMIT symbols by weekly shares -> per-symbol series."""
    syms: dict[str, dict] = {}
    for r in rows:
        n = norm_symbol_row(r)
        if not n or n["shares"] is None or not n["symbol"]:
            continue
        cur = syms.setdefault(n["symbol"],
                              {"shares": 0.0, "trades": 0.0,
                               "name": n["name"]})
        cur["shares"] += n["shares"]
        cur["trades"] += n["trades"] or 0.0
    top = sorted(syms.items(), key=lambda kv: kv[1]["shares"],
                 reverse=True)[:SYMBOL_LIMIT]
    for sym, v in top:
        store.upsert_points(f"cycle:ats-sym-{sym}-shares",
                            [(week, v["shares"])])
        store.upsert_points(f"cycle:ats-sym-{sym}-trades",
                            [(week, v["trades"])])
    return {sym: v for sym, v in top}


def _store_weekly_sf(store: Store, week: date,
                     rows: list[dict]) -> dict:
    """Granular venue x security: keep top venues per symbol (in-memory),
    return {symbol: {name, shares, venues: [{mpid, name, shares}]}} for the
    week's snapshot doc."""
    per_sym: dict[str, dict] = {}
    for r in rows:
        n = norm_sf_row(r)
        if not n or n["shares"] is None or not n["symbol"]:
            continue
        cur = per_sym.setdefault(n["symbol"],
                                 {"name": n["name"], "shares": 0.0,
                                  "venues": {}})
        cur["shares"] += n["shares"]
        v = cur["venues"].setdefault(n["mpid"],
                                     {"name": n["venue"], "shares": 0.0})
        v["shares"] += n["shares"]
    top = sorted(per_sym.items(), key=lambda kv: kv[1]["shares"],
                 reverse=True)[:SNAPSHOT_SYMBOLS]
    return {sym: {"name": v["name"], "shares": v["shares"],
                  "venues": [{"mpid": m, **d} for m, d in
                             sorted(v["venues"].items(),
                                    key=lambda kv: kv[1]["shares"],
                                    reverse=True)[:SNAPSHOT_VENUES]]}
            for sym, v in top}


def _store_blocks_month(store: Store, month: date,
                        rows: list[dict]) -> list[dict]:
    """Store one monthly blocksSummary partition; return normalized rows.

    Dedupe: the API repeats each venue's monthly totals in all 6
    block-size/dollar-size slices (summaryTypeCode) — identical venue
    totals, different bucket detail. Series take ONE row per venue (summing
    would 6x-count); the returned list keeps every slice for the doc.
    """
    normed = [n for r in rows if (n := norm_blocks_row(r))]
    by_venue: dict[str, dict] = {}
    for n in normed:
        by_venue.setdefault(n["mpid"], n)
    deduped = list(by_venue.values())
    tot_sh = sum(n["shares"] or 0.0 for n in deduped)
    tot_tr = sum(n["trades"] or 0.0 for n in deduped)
    for n in deduped:
        slug = _slug(n["mpid"])
        if not slug:
            continue
        store.upsert_points(f"cycle:ats-m-{slug}-shares",
                            [(month, n["shares"] or 0.0)])
        store.upsert_points(f"cycle:ats-m-{slug}-trades",
                            [(month, n["trades"] or 0.0)])
        if n["sharepct"] is not None:
            store.upsert_points(f"cycle:ats-m-{slug}-sharepct",
                                [(month, n["sharepct"])])
    if deduped:
        store.upsert_points("cycle:ats-m-total-shares", [(month, tot_sh)])
        store.upsert_points("cycle:ats-m-total-trades", [(month, tot_tr)])
    return normed


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def fetch_finra_ats(store: Store) -> str:
    """Weekly job. Keyless monthly ATS block summary always runs; weekly
    ATS detail runs only with FINRA_CLIENT_ID/FINRA_CLIENT_SECRET set
    (skips gracefully otherwise). Full history then incremental."""
    # ---- 1) keyless monthly blocksSummary (always) ----
    group = await _discover_group(None)
    months = await _partitions(group, BLOCKS_DATASET, None)
    have_m = set(store.points("cycle:ats-m-total-shares"))
    new_months = [m for m in months
                  if date.fromisoformat(m) not in have_m]
    venues: dict[str, str] = {}
    latest_rows: list[dict] = []
    for m in sorted(new_months):
        rows = await _pull_blocks_month(group, m)
        normed = _store_blocks_month(store, date.fromisoformat(m), rows)
        for n in normed:
            venues[n["mpid"]] = n["name"]
        if m == max(months):
            latest_rows = normed
        log.info("ats blocksSummary stored %s (%d venues)", m, len(normed))
    if not new_months and months:
        # keep the latest-month snapshot fresh on no-op runs
        rows = await _pull_blocks_month(group, max(months))
        latest_rows = [n for r in rows if (n := norm_blocks_row(r))]
        for n in latest_rows:
            venues[n["mpid"]] = n["name"]

    # ---- 2) keyed weekly detail (graceful skip) ----
    client_id = os.environ.get("FINRA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FINRA_CLIENT_SECRET", "").strip()
    status = store.doc("finra_ats")
    prev = status.payload if status else {}
    keyed = {"configured": bool(client_id and client_secret)}
    if not keyed["configured"]:
        log.warning(KEY_MSG)
    else:
        try:
            keyed = await _fetch_keyed(store, client_id, client_secret,
                                       venues, dict(prev))
        except Exception as exc:  # noqa: BLE001 — keyless data still lands
            log.warning("finra_ats weekly detail failed: %s", exc)
            keyed = {"configured": True, "error": str(exc)[:200]}

    # ---- 3) docs ----
    store.put_doc("ats_venues",
                  {"mpids": {m: {"name": n} for m, n in sorted(venues.items())}},
                  source=SOURCE)
    if latest_rows:
        store.put_doc("ats_blocks_latest", {
            "as_of": max(n["month"] for n in latest_rows),
            "count": len(latest_rows),
            "venues": [
                {"mpid": n["mpid"], "name": n["name"],
                 "slice": n["slice"], "slice_desc": n["slice_desc"],
                 "shares": n["shares"], "trades": n["trades"],
                 "share_pct": n["sharepct"], "trade_pct": n["tradepct"],
                 "block_shares": n["block_shares"],
                 "block_trades": n["block_trades"],
                 "avg_trade_size": n["avg_trade_size"],
                 "raw": n["raw"]}
                for n in sorted(latest_rows,
                                key=lambda x: x["shares"] or 0.0,
                                reverse=True)],
        }, source=SOURCE)
    store.put_doc("finra_ats", {
        "configured": keyed.get("configured", False),
        "keyless_months": len(months),
        "keyless_range": ([min(months), max(months)] if months else []),
        **{k: v for k, v in keyed.items() if k != "configured"},
    }, source=SOURCE)
    return SOURCE


async def _fetch_keyed(store: Store, client_id: str, client_secret: str,
                       venues: dict[str, str], prev: dict) -> dict:
    """Weekly ATS_W_* pull with OAuth. Returns status fields for the doc."""
    token = await fetch_token(client_id, client_secret)  # never logged
    headers = {"Authorization": f"Bearer {token}"}
    group = await _discover_group(headers)

    out: dict = {"configured": True, "datasets": {}}
    # Historic variants: production covers the rolling 12 months; history
    # to 2014 lives in <DATASET>HISTORIC (per FINRA docs). Probe both
    # spellings; 404 on partitions = variant doesn't exist.
    async def _week_partitions(dataset: str) -> list[str]:
        seen: dict[str, None] = {}
        for ds in (dataset, f"{dataset}HISTORIC", f"{dataset}_HISTORIC"):
            for w in await _partitions(group, ds, headers):
                seen[w] = None
        return sorted(seen)

    # Backfill cursors: full history for FIRM/SMBL, bounded for SMBL_FIRM.
    firm_weeks = await _week_partitions("ATS_W_FIRM")
    smbl_weeks = await _week_partitions("ATS_W_SMBL")
    sf_weeks = await _week_partitions("ATS_W_SMBL_FIRM")

    have_firm = set(store.points("cycle:ats-total-shares"))
    have_sym = set(store.points("cycle:ats-sym-AAPL-shares"))
    missing_firm = {w for w in firm_weeks
                    if date.fromisoformat(w) not in have_firm}
    missing_sym = {w for w in smbl_weeks
                   if date.fromisoformat(w) not in have_sym}
    # Incremental first (latest weeks), then bounded backfill of older weeks.
    # Symbol storage re-runs idempotently for weeks whose firm data exists.
    ordered = sorted(missing_firm | missing_sym,
                     reverse=True)[:BACKFILL_WEEKS_PER_RUN]

    sf_done: set = set(prev.get("sf_weeks_stored") or ())

    async def _safe_pull(dataset: str, week: str) -> list[dict]:
        """One dataset/week pull; a failing dataset (e.g. a historic
        variant with a different partition field) degrades to empty rows
        instead of killing the whole weekly run."""
        try:
            return await _pull_week(group, dataset, week, headers)
        except Exception as exc:  # noqa: BLE001
            log.warning("ats %s %s skipped: %s", dataset, week, exc)
            return []

    for w in ordered:
        wd = date.fromisoformat(w)
        firm_rows = await _safe_pull("ATS_W_FIRM", w)
        ven = _store_weekly_firm(store, wd, firm_rows)
        for m, info in ven.items():
            venues.setdefault(m, info["name"])
        syms: dict = {}
        if w in smbl_weeks:
            smbl_rows = await _safe_pull("ATS_W_SMBL", w)
            syms = _store_weekly_symbols(store, wd, smbl_rows)
        sf_detail: dict = {}
        if w in sf_weeks and w not in sf_done and \
                len(sf_done) < SMBL_FIRM_BACKFILL_WEEKS:
            sf_rows = await _safe_pull("ATS_W_SMBL_FIRM", w)
            sf_detail = _store_weekly_sf(store, wd, sf_rows)
            if sf_rows:
                sf_done.add(w)
        prev["sf_weeks_stored"] = sorted(sf_done)
        store.put_doc(f"ats-week-{w}", {
            "week": w,
            "venues": [{"mpid": m, "name": v["name"], "shares": v["shares"],
                        "trades": v["trades"]}
                       for m, v in sorted(ven.items(),
                                          key=lambda kv: kv[1]["shares"],
                                          reverse=True)],
            "symbols": [{"symbol": s, "name": v["name"], "shares": v["shares"],
                         "top_venues": (sf_detail.get(s) or {})
                         .get("venues", [])}
                        for s, v in sorted(syms.items(),
                                           key=lambda kv: kv[1]["shares"],
                                           reverse=True)[:SNAPSHOT_SYMBOLS]],
        }, source=SOURCE)
        log.info("ats weekly stored %s (%d venues, %d symbols)",
                 w, len(ven), len(syms))

    out["weeks_available"] = len(firm_weeks)
    out["weeks_stored"] = len(store.points("cycle:ats-total-shares"))
    stored_weeks = have_firm | {date.fromisoformat(w) for w in ordered}
    out["latest_week"] = (max(stored_weeks).isoformat()
                          if stored_weeks else None)
    out["backfill_pending"] = max(0, len(firm_weeks) - out["weeks_stored"])
    out["venue_count"] = len(venues)
    # Granular venue x security history is bounded (SMBL_FIRM_BACKFILL_WEEKS
    # latest weeks); the cursor below tells the UI exactly how far it goes.
    out["sf_weeks_stored"] = sorted(set(prev.get("sf_weeks_stored") or ()))
    out["sf_weeks_available"] = len(sf_weeks)
    return out
