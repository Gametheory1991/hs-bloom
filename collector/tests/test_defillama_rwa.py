"""DefiLlama RWA fetcher tests: classification, parsing, degradation."""
import json

import pytest

from collector.fetchers import defillama_rwa
from collector.fetchers.defillama_rwa import (
    classify_protocol,
    fetch_defillama_rwa,
)
from collector.store import Store

PROTOS = json.dumps([
    {"slug": "invesco-ustb", "name": "Invesco USTB", "category": "RWA",
     "tvl": 600_000_000, "chains": ["Ethereum"]},
    {"slug": "huma", "name": "Huma", "category": "RWA",
     "tvl": 400_000_000, "chains": ["Solana"]},
    {"slug": "not-rwa", "name": "Some DEX", "category": "Dexs",
     "tvl": 999_000_000, "chains": ["Ethereum"]},
])

PROTO_DETAIL = json.dumps({
    "name": "Invesco USTB", "url": "https://superstate.com/",
    "tvl": [{"date": 1791000000, "totalLiquidityUSD": 600_000_000},
            {"date": 1791086400, "totalLiquidityUSD": 610_000_000}],
    "currentChainTvls": {"Ethereum": 610_000_000},
})

CG_CATS = {
    "tokenized-treasuries": json.dumps([
        {"id": "blackrock-usd-institutional-digital-liquidity-fund",
         "symbol": "buidl", "name": "BlackRock BUIDL",
         "current_price": 1.0, "market_cap": 2_000_000_000,
         "price_change_percentage_24h": 0.1},
    ]),
    "tokenized-gold": json.dumps([
        {"id": "pax-gold", "symbol": "paxg", "name": "PAX Gold",
         "current_price": 2600.0, "market_cap": 1_800_000_000,
         "price_change_percentage_24h": 0.5},
    ]),
    "tokenized-stock": json.dumps([]),
    "tokenized-private-credit": json.dumps([]),
    "real-estate": json.dumps([]),
}

CHART = json.dumps({"market_caps": [
    [1791000000000, 2_000_000_000],
    [1791086400000, 2_100_000_000],
]})


def test_classify_protocol():
    assert classify_protocol("invesco-ustb", "Invesco USTB") == "treasuries"
    assert classify_protocol("blackrock-buidl", "BlackRock BUIDL") == "treasuries"
    assert classify_protocol("huma", "Huma") == "credit"
    assert classify_protocol("realt-tokens", "RealT Tokens") == "realestate"
    assert classify_protocol("pax-gold", "PAX Gold") == "gold"
    assert classify_protocol("xstocks", "xStocks") == "stocks"
    assert classify_protocol("some-random-vault", "Random Vault") == "other"


async def fake_get_text(url, **kw):
    if "api.llama.fi/protocols" in url:
        return PROTOS
    if "api.llama.fi/protocol/" in url:
        return PROTO_DETAIL
    if "coins/markets" in url:
        for cat, body in CG_CATS.items():
            if f"category={cat}" in url:
                return body
        return "[]"
    if "market_chart" in url:
        return CHART
    raise AssertionError(f"unexpected url {url}")


@pytest.mark.asyncio
async def test_fetch_rwa_happy(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    res = await fetch_defillama_rwa(store, fake_get_text)
    assert "llama" in res and "coingecko" in res
    doc = store.doc("rwa")
    assert doc is not None
    assert "rwa.xyz" in doc.payload["scope"]
    # non-RWA protocol excluded
    slugs = [p["slug"] for p in doc.payload["protocols"]]
    assert "not-rwa" not in slugs
    assert "invesco-ustb" in slugs
    # class aggregates stored
    assert store.points("cycle:rwa-tvl-total")
    assert store.points("cycle:rwa-tvl-class-treasuries")
    assert store.points("cycle:rwa-mcap-total")
    assert store.points("cycle:rwa-mcap-class-treasuries")
    assert store.points("cycle:rwa-tvl-invesco-ustb")


@pytest.mark.asyncio
async def test_fetch_rwa_llama_down(tmp_path):
    async def dead(url, **kw):
        if "llama.fi" in url:
            raise RuntimeError("boom")
        return await fake_get_text(url, **kw)

    store = Store(str(tmp_path / "t.db"))
    res = await fetch_defillama_rwa(store, dead)
    # CoinGecko half still works
    assert "coingecko" in res
    assert store.points("cycle:rwa-mcap-total")
