import json

import pytest

from collector.fetchers.etf import fetch_etf_catalog
from collector.store import Store


@pytest.mark.asyncio
async def test_fetch_etf_catalog_normalizes_dedupes_and_stores(tmp_path):
    store = Store(tmp_path / "t.db")
    payload = json.dumps([
        {"symbol": "spy", "name": "SPDR S&P 500 ETF Trust", "url": "https://etfdb.com/etf/SPY/"},
        {"symbol": "SPY", "name": "duplicate", "url": "ignore"},
        {"symbol": "ivv", "name": "iShares Core S&P 500 ETF", "url": "https://etfdb.com/etf/IVV/"},
        {"symbol": "", "name": "bad"},
        {"symbol": None, "name": "missing symbol"},
        {"symbol": "BAD", "name": None},
    ])

    async def fake_get_text(url, params=None, headers=None):  # noqa: ANN001
        return payload

    source = await fetch_etf_catalog("https://example.com/etfdb.json", store, fake_get_text)
    assert source == "etfdb"
    doc = store.doc("etf_catalog")
    assert doc is not None
    assert doc.source == "etfdb"
    assert doc.payload["count"] == 2
    assert [r["symbol"] for r in doc.payload["rows"]] == ["IVV", "SPY"]
    assert doc.payload["by_symbol"]["SPY"]["name"] == "SPDR S&P 500 ETF Trust"


@pytest.mark.asyncio
async def test_fetch_etf_catalog_errors_on_non_list_payload(tmp_path):
    store = Store(tmp_path / "t.db")

    async def fake_get_text(url, params=None, headers=None):  # noqa: ANN001
        return '{"rows": []}'

    with pytest.raises(RuntimeError, match="not a list"):
        await fetch_etf_catalog("https://example.com/etfdb.json", store, fake_get_text)
