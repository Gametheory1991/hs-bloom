"""Tokenized assets (RWA) — DefiLlama protocol TVL + CoinGecko token market caps.

Two free, keyless sources (rwa.xyz's API is key-gated — not scraped):

1. DefiLlama `/protocols` (category == "RWA", ~183 protocols, ~$4.7B TVL):
   per-protocol daily TVL for the top 20, aggregate total, per-chain
   aggregates, and per-asset-class aggregates via CLASS_MAP.

2. CoinGecko tokenized-asset categories (market caps, ~$39B total —
   close to rwa.xyz's headline number):
     tokenized-treasuries | tokenized-gold | tokenized-stock |
     tokenized-private-credit | real-estate | tokenized-commodities
   Top-3 coins per class get 365d market-cap history
   (`/coins/{id}/market_chart`), giving real class-level history.

Stored:
  cycle:rwa-tvl-{slug}            per-protocol daily TVL (USD)
  cycle:rwa-tvl-total             aggregate RWA protocol TVL
  cycle:rwa-tvl-class-{class}     per-class protocol TVL
  cycle:rwa-tvl-chain-{chain}     per-chain protocol TVL
  cycle:rwa-mcap-{coin}           per-coin market cap (USD)
  cycle:rwa-mcap-class-{class}    per-class token market cap (top-3 sum)
  cycle:rwa-mcap-total            aggregate token market cap
  doc "rwa": league tables + scope labels for the UI.

Daily cadence. One failure never breaks the other source.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "rwa"

LLAMA_PROTOCOLS = "https://api.llama.fi/protocols"
LLAMA_PROTOCOL = "https://api.llama.fi/protocol/{slug}"
CG_MARKETS = ("https://api.coingecko.com/api/v3/coins/markets"
              "?vs_currency=usd&category={cat}&order=market_cap_desc"
              "&per_page={n}&page=1")
CG_CHART = ("https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
            "?vs_currency=usd&days=365&interval=daily")

# CoinGecko category -> our asset class
# NOTE: tokenized-commodities is intentionally excluded — it duplicates
# tokenized-gold (XAUT/PAXG/KAU appear in both).
CG_CLASSES = {
    "tokenized-treasuries": "treasuries",
    "tokenized-gold": "gold",
    "tokenized-stock": "stocks",
    "tokenized-private-credit": "credit",
    "real-estate": "realestate",
}
CLASS_LABELS = {
    "treasuries": "Tokenized Treasuries",
    "gold": "Tokenized Gold",
    "stocks": "Tokenized Stocks",
    "credit": "Private Credit",
    "realestate": "Real Estate",
    "other": "Other",
}
TOP_PROTOCOLS = 20
TOP_COINS_PER_CLASS = 3

# slug/name substring -> asset class (checked in order)
CLASS_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("treasuries", ("ustb", "usdtb", "usdy", "usyc", "buidl", "tbill", "t-bill",
                    "stbt", "ondo", "openeden", "superstate", "franklin",
                    "usd0", "cusdo", "hybond", "benji")),
    ("credit", ("huma", "figure", "anemoy", "opentrade", "heloc", "centrifuge",
                "goldfinch", "maple")),
    ("gold", ("paxg", "xaut", "gold", "xagm", "xaum", "kinesis", "kauf")),
    ("stocks", ("backed", "dinari", "xstocks", "bstocks", "stock")),
    ("realestate", ("realt", "tangible", "lofty", "realtyx", "real estate",
                    "propy", "elysia")),
    ("commodities", ("silver", "uranium", "commodity")),
]


def classify_protocol(slug: str, name: str) -> str:
    hay = f"{slug} {name}".lower()
    for cls, keys in CLASS_MAP:
        if any(k in hay for k in keys):
            return cls
    return "other"


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _day(ts: float) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


async def fetch_defillama_rwa(store: Store, get_text: GetText) -> str:
    """Pull RWA data from DefiLlama + CoinGecko. Returns a status string."""
    today = date.today()
    parts: list[str] = []

    # ---- 1. DefiLlama protocols ----
    try:
        protos = json.loads(await get_text(LLAMA_PROTOCOLS))
        rwa = [p for p in protos if isinstance(p, dict) and p.get("category") == "RWA"]
        rwa.sort(key=lambda p: _f(p.get("tvl")) or 0, reverse=True)
        parts.append(f"llama: {len(rwa)} RWA protocols")
    except Exception as exc:  # noqa: BLE001 — degrade, don't fail
        log.warning("defillama_rwa: protocols failed: %s", exc)
        rwa = []

    league: list[dict] = []
    class_day: dict[str, dict[date, float]] = {}
    chain_day: dict[str, dict[date, float]] = {}
    total_day: dict[date, float] = {}

    for p in rwa[:TOP_PROTOCOLS]:
        slug = p.get("slug")
        if not slug:
            continue
        try:
            det = json.loads(await get_text(LLAMA_PROTOCOL.format(slug=slug)))
        except Exception as exc:  # noqa: BLE001 — one bad protocol skips
            log.warning("defillama_rwa: protocol %s failed: %s", slug, exc)
            continue
        hist = det.get("tvl") or []
        pts = [(_day(h["date"]), _f(h.get("totalLiquidityUSD")))
               for h in hist if isinstance(h, dict)]
        pts = [(d, v) for d, v in pts if v is not None and v > 0]
        if pts:
            store.upsert_points(f"cycle:rwa-tvl-{slug}", pts)
        cur = _f(p.get("tvl")) or 0
        cls = classify_protocol(slug, p.get("name") or "")
        chains = p.get("chains") or []
        cct = det.get("currentChainTvls") or {}
        league.append({
            "slug": slug, "name": p.get("name"), "class": cls,
            "tvl": cur, "chains": chains,
            "chain_tvls": {k: _f(v) for k, v in cct.items() if _f(v)},
            "url": det.get("url"),
        })
        for d, v in pts:
            total_day[d] = total_day.get(d, 0.0) + v
            class_day.setdefault(cls, {}).setdefault(d, 0.0)
            class_day[cls][d] += v
        for ch, cv in cct.items():
            cvf = _f(cv)
            if cvf:
                chain_day.setdefault(ch, {})[today] = \
                    chain_day.setdefault(ch, {}).get(today, 0.0) + cvf

    if total_day:
        store.upsert_points("cycle:rwa-tvl-total",
                            sorted(total_day.items()))
        latest_tvl = total_day[max(total_day)]
    for cls, dd in class_day.items():
        store.upsert_points(f"cycle:rwa-tvl-class-{cls}", sorted(dd.items()))
    for ch, dd in chain_day.items():
        slug_ch = ch.lower().replace(" ", "-")
        store.upsert_points(f"cycle:rwa-tvl-chain-{slug_ch}",
                            sorted(dd.items()))
    parts.append(f"llama: {len(league)} histories, "
                 f"${(latest_tvl if total_day else 0) / 1e9:.2f}B latest total")

    # ---- 2. CoinGecko tokenized categories ----
    cg_classes: list[dict] = []
    mcap_total_day: dict[date, float] = {}
    mcap_class_day: dict[str, dict[date, float]] = {}
    for cg_cat, cls in CG_CLASSES.items():
        try:
            coins = json.loads(await get_text(
                CG_MARKETS.format(cat=cg_cat, n=10)))
        except Exception as exc:  # noqa: BLE001
            log.warning("defillama_rwa: coingecko %s failed: %s", cg_cat, exc)
            continue
        coins = [c for c in coins if isinstance(c, dict) and c.get("id")][:TOP_COINS_PER_CLASS]
        class_rows = []
        for c in coins:
            cid = c["id"]
            class_rows.append({
                "id": cid, "symbol": (c.get("symbol") or "").upper(),
                "name": c.get("name"), "mcap": _f(c.get("market_cap")),
                "price": _f(c.get("current_price")),
                "chg24h": _f(c.get("price_change_percentage_24h")),
            })
            try:
                chart = json.loads(await get_text(CG_CHART.format(coin=cid)))
            except Exception as exc:  # noqa: BLE001
                log.warning("defillama_rwa: chart %s failed: %s", cid, exc)
                continue
            mcaps = chart.get("market_caps") or []
            pts = [(_day(ms / 1000), _f(v)) for ms, v in mcaps
                   if isinstance(ms, (int, float))]
            pts = [(d, v) for d, v in pts if v]
            if pts:
                store.upsert_points(f"cycle:rwa-mcap-{cid}", pts)
                for d, v in pts:
                    mcap_total_day[d] = mcap_total_day.get(d, 0.0) + v
                    mcap_class_day.setdefault(cls, {}).setdefault(d, 0.0)
                    mcap_class_day[cls][d] += v
        cg_classes.append({
            "class": cls, "label": CLASS_LABELS[cls],
            "coins": class_rows,
            "total_mcap": sum((r["mcap"] or 0) for r in class_rows),
        })
    if mcap_total_day:
        store.upsert_points("cycle:rwa-mcap-total",
                            sorted(mcap_total_day.items()))
    for cls, dd in mcap_class_day.items():
        store.upsert_points(f"cycle:rwa-mcap-class-{cls}", sorted(dd.items()))
    parts.append(f"coingecko: {len(cg_classes)} classes, "
                 f"${sum(c['total_mcap'] for c in cg_classes) / 1e9:.1f}B")

    store.put_doc("rwa", {
        "as_of": today.isoformat(),
        "scope": ("DefiLlama-tracked on-chain RWA protocol TVL + "
                  "CoinGecko tokenized-asset market caps (top-3 tokens/class). "
                  "Full institutional dataset: rwa.xyz (API key required)."),
        "protocols": sorted(league, key=lambda r: r["tvl"] or 0, reverse=True),
        "classes": sorted(cg_classes, key=lambda r: r["total_mcap"],
                          reverse=True),
        "class_labels": CLASS_LABELS,
    }, SOURCE)
    return "; ".join(parts)
