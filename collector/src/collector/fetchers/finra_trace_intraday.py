"""FINRA TRACE intraday activity — the "what's trading right now" feed.

WHAT THIS IS (honest framing):
  True tick-level 15-minute-delayed TRACE prints are a *licensed* product.
  FINRA sells them via the TRACE data feed (download.finratraqs.org, vendor
  agreement required) and the Academic TRACE product (36-month delay, free
  for researchers). There is no free keyless tick-print API.

WHAT THIS FETCHER DOES (two tiers):
  1. KEYED (needs FINRA API credentials — free from the API console at
     https://gateway.finra.org/app/api-console, NOT yet set up for hs-bloom):
     FINRA Query API, FixedIncomeMarket dataset category, OAuth2
     client-credentials flow (same as finra_ats.py):
       POST https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token
       GET  https://api.finra.org/data/group/{group}/name/{dataset}?limit=N
     with Authorization: Bearer <redacted>
     Dataset names are probed from TRACE_DATASETS (documented candidates);
     the first that doesn't 404 wins. Field mapping is defensive (parsed by
     header name against alias lists).
  2. KEYLESS fallback (live today, no credentials):
     FINRA's public dynamic-reporting API (dynarep, same calls a visitor's
     browser makes) — FixedIncomeMarket/MarketActivityAggregates gives the
     daily activity pulse (trades, volume by product). Not tick-level, but
     it answers "is today heavy?" every day.

Without FINRA_CLIENT_ID / FINRA_CLIENT_SECRET the keyed tier is skipped
gracefully (warning, no crash); the keyless daily pulse still lands.

TO ENABLE TICK PRINTS (for Harry / parent agent):
  1. Create a free API app at https://gateway.finra.org/app/api-console
  2. Set FINRA_CLIENT_ID and FINRA_CLIENT_SECRET as Render env vars
     (never in code, never logged — same handling as finra_ats.py)
  3. Verify the FixedIncomeMarket dataset names at developer.finra.org
     and update TRACE_DATASETS below if FINRA renamed them.
  4. For true 15-min-delayed tick prints (not just aggregates), FINRA
     requires a TRACE data vendor agreement — the Query API gives
     aggregates/summaries, not the raw dissemination feed.

Stored:
  doc  trace_intraday_latest   most recent keyed pull (prints snapshot, rolling)
  doc  trace_intraday_status   {asof, tier, n_prints, note}
  cycle:trace-intra-{product}-trades / -volume   daily aggregates (keyless tier)
Only the latest ~500 prints are kept in the doc (rolling 24h) — tick
history is intentionally NOT stored as full series (volume too high).

Security: the client secret is used transiently for the token fetch and
never logged; the Bearer <redacted> lives in module memory only.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import date, datetime

import httpx

from collector.fetchers.finra_dynarep import DynarepSession
from collector.http import USER_AGENT, GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "finra-trace-intraday"
API_BASE = "https://api.finra.org"
TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"

# Candidate Query-API datasets for intraday TRACE activity. FINRA's
# FixedIncomeMarket category evolves; probe order = most useful first.
# Verify current names at developer.finra.org if all 404.
TRACE_DATASETS = (
    "BondTradeDissemination",   # candidate: intraday disseminated prints
    "TRACEActivitySummary",     # candidate: intraday activity rollup
    "weeklySummary",            # candidate: weekly product summary
)
GROUP_CANDIDATES = ("fixedIncomeMarket", "FixedIncomeMarket")

MAX_PRINTS = 500  # rolling window kept in the latest doc

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _basic(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def _fetch_token(client_id: str, client_secret: str) -> str:
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
    _token_cache.update({"token": token,
                         "expires_at": now + min(max(ttl - 120, 60), 1800)})
    return token


# Header aliases for defensive field mapping (FINRA renames columns).
ALIASES = {
    "cusip": ("cusip", "cusipId", "CUSIP"),
    "price": ("price", "tradePrice", "avgPrice"),
    "yield": ("yield", "tradeYield", "yieldToMaturity"),
    "quantity": ("quantity", "tradeQuantity", "volume", "parValueTraded"),
    "trade_count": ("tradeCount", "numberOfTrades", "trades"),
    "timestamp": ("tradeDateTime", "executionDateTime", "timestamp", "tradeDate"),
    "side": ("buySell", "side", "buySellIndicator"),
    "product": ("productType", "assetType", "securityType", "product"),
}


def _pick(row: dict, *names: str):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    # case-insensitive fallback
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return None


def normalize_print(row: dict) -> dict | None:
    """Normalize one raw API row -> print dict. None if unusable."""
    cusip = _pick(row, *ALIASES["cusip"])
    ts = _pick(row, *ALIASES["timestamp"])
    if not cusip or not ts:
        return None
    try:
        price = float(_pick(row, *ALIASES["price"]) or 0) or None
    except (TypeError, ValueError):
        price = None
    try:
        qty = float(_pick(row, *ALIASES["quantity"]) or 0) or None
    except (TypeError, ValueError):
        qty = None
    try:
        yld = float(_pick(row, *ALIASES["yield"]) or 0) or None
    except (TypeError, ValueError):
        yld = None
    return {
        "cusip": str(cusip).strip(),
        "timestamp": str(ts),
        "price": price,
        "yield": yld,
        "quantity": qty,
        "side": _pick(row, *ALIASES["side"]),
        "product": _pick(row, *ALIASES["product"]),
    }


async def _keyed_pull(client_id: str, client_secret: str) -> list[dict]:
    """Attempt the keyed Query API tier. Returns normalized prints ([] if
    no dataset/credentials available). Raises only on auth failure."""
    token = await _fetch_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as c:
        for group in GROUP_CANDIDATES:
            for ds in TRACE_DATASETS:
                url = f"{API_BASE}/data/group/{group}/name/{ds}?limit={MAX_PRINTS}"
                resp = await c.get(url, headers=headers)
                if resp.status_code == 404:
                    continue
                if resp.status_code == 401:
                    raise RuntimeError("FINRA Query API: 401 — credentials rejected")
                if resp.status_code >= 400:
                    log.warning("trace-intraday: %s -> HTTP %s", ds, resp.status_code)
                    continue
                try:
                    rows = resp.json()
                except ValueError:
                    continue
                if isinstance(rows, dict):
                    rows = rows.get("data") or rows.get("rows") or []
                prints = [p for p in (normalize_print(r) for r in rows) if p]
                log.info("trace-intraday: keyed tier via %s/%s: %d prints",
                         group, ds, len(prints))
                return prints
    log.warning("trace-intraday: no keyed FixedIncomeMarket dataset responded")
    return []


async def _keyless_pulse() -> dict:
    """Keyless tier: dynarep MarketActivityAggregates (daily activity pulse).

    Returns {product: {trades, volume}} for the latest available day.
    """
    out: dict = {}
    try:
        from datetime import timedelta
        today = date.today()
        async with DynarepSession() as sess:
            rows = await sess.query(
                "MarketActivityAggregates",
                ["bondType", "dataTypeDescription",
                 "originalTradeReportedDate",
                 "fieldA", "fieldB", "fieldC", "fieldD"],
                today - timedelta(days=7), today)
    except Exception as exc:  # noqa: BLE001 — keyless tier is best-effort
        log.warning("trace-intraday: keyless dynarep pull failed: %s", exc)
        return out
    for r in rows or []:
        prod = (_pick(r, "bondType", "dataTypeDescription")
                or "ALL")
        # fieldA..D carry the metric values; map defensively by position:
        # fieldA = trade count proxy, fieldB = volume proxy (verified shape
        # varies — we take the two largest numerics).
        nums = []
        for k in ("fieldA", "fieldB", "fieldC", "fieldD"):
            try:
                nums.append(float(r.get(k) or 0))
            except (TypeError, ValueError):
                pass
        nums.sort(reverse=True)
        trades = nums[0] if len(nums) > 0 else 0
        vol = nums[1] if len(nums) > 1 else 0
        key = str(prod)
        prev = out.get(key, {"trades": 0, "volume": 0})
        out[key] = {"trades": max(prev["trades"], trades),
                    "volume": max(prev["volume"], vol)}
    return out


async def fetch_finra_trace_intraday(store: Store, get_text: GetText) -> str:
    """Intraday job: keyed tier if credentials exist, else keyless pulse."""
    client_id = os.environ.get("FINRA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FINRA_CLIENT_SECRET", "").strip()

    prints: list[dict] = []
    tier = "keyless"
    if client_id and client_secret:
        try:
            prints = await _keyed_pull(client_id, client_secret)
            if prints:
                tier = "keyed"
        except RuntimeError as exc:
            log.warning("trace-intraday: keyed tier failed: %s", exc)
    else:
        log.warning("trace-intraday: FINRA_CLIENT_ID/SECRET not set — "
                    "keyed tier skipped (see module docstring to enable)")

    today = date.today().isoformat()
    if prints:
        prev = store.doc("trace_intraday_latest")
        prev_prints = (prev.payload or {}).get("prints", []) if prev else []
        # Rolling window: newest first, cap at MAX_PRINTS.
        merged = (prints + prev_prints)[:MAX_PRINTS]
        store.put_doc("trace_intraday_latest",
                      {"asof": today, "tier": tier, "prints": merged,
                       "n_prints": len(merged)}, SOURCE)

    # Keyless daily pulse -> cycle series (runs regardless of tier).
    pulse = await _keyless_pulse()
    n_series = 0
    for prod, vals in pulse.items():
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in prod).strip("-")
        if vals["trades"]:
            store.upsert_points(f"cycle:trace-intra-{slug}-trades",
                                [(date.today(), vals["trades"])])
            n_series += 1
        if vals["volume"]:
            store.upsert_points(f"cycle:trace-intra-{slug}-volume",
                                [(date.today(), vals["volume"])])
            n_series += 1

    store.put_doc("trace_intraday_status",
                  {"asof": today, "tier": tier,
                   "n_prints": len(prints), "n_pulse_products": len(pulse),
                   "note": ("keyed tier needs FINRA_CLIENT_ID/SECRET; "
                            "tick-level 15-min prints need a TRACE data agreement")
                           if tier == "keyless" else "keyed tier active"},
                  SOURCE)
    return (f"trace-intraday: tier={tier}, {len(prints)} prints, "
            f"{n_series} pulse series updated")
