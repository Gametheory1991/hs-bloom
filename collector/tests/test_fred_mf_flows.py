"""Tests for collector.fetchers.fred_mf_flows (Z.1 mutual-fund flows via FRED).

The FRED series IDs below were verified live 2026-10-09 via FRED's search
index (Board of Governors / Z.1 Financial Accounts release); any typo in the
fetcher's SERIES table fails here loudly rather than silently fetching the
wrong series.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from collector.fetchers import fred_mf_flows
from collector.fetchers.fred_mf_flows import SERIES, fetch_mf_flows
from collector.store import Store


async def _noop(*a, **k):
    return None


# series key -> FRED id, verbatim from the verified 2026-10-09 research.
EXPECTED_IDS = {
    "cycle:mf-flow-total": "BOGZ1FU653164205Q",
    "cycle:mf-flow-total-annual": "BOGZ1FU653164205A",
    "cycle:mf-flow-hybrid": "BOGZ1FU654091403Q",
}


def test_series_table_matches_verified_ids():
    got = {key: fred_id for key, fred_id, _label in SERIES}
    assert got == EXPECTED_IDS
    # no duplicate keys or FRED ids
    assert len({key for key, _, _ in SERIES}) == len(SERIES)
    assert len({fid for _, fid, _ in SERIES}) == len(SERIES)


def obs_json(*pairs):
    return json.dumps({
        "observations": [{"date": d, "value": v} for d, v in pairs]
    })


async def test_fetch_mf_flows_upserts_all_series_no_network(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")

    async def fake_get(url, params=None, headers=None):
        fred_id = (params or {}).get("series_id")
        assert url == "https://api.stlouisfed.org/fred/series/observations"
        assert params["api_key"] == "TESTKEY"
        if fred_id == "BOGZ1FU653164205Q":
            return obs_json(("2026-03-31", "-82622.0"), ("2026-06-30", "-134984.0"))
        if fred_id == "BOGZ1FU653164205A":
            return obs_json(("2025-01-01", "-787748.0"))
        if fred_id == "BOGZ1FU654091403Q":
            return obs_json(("2026-03-31", "-12915.0"), ("2026-06-30", "-12090.0"))
        raise RuntimeError(f"unexpected series {fred_id}")

    monkeypatch.setattr(fred_mf_flows.asyncio, "sleep", lambda *a, **k: _noop())
    result = await fetch_mf_flows(store, "TESTKEY", fake_get)

    assert result == "fred-z1-mf-flows"
    assert store.points("cycle:mf-flow-total") == {
        date(2026, 3, 31): -82622.0,
        date(2026, 6, 30): -134984.0,
    }
    assert store.points("cycle:mf-flow-total-annual") == {date(2025, 1, 1): -787748.0}
    assert store.points("cycle:mf-flow-hybrid") == {
        date(2026, 3, 31): -12915.0,
        date(2026, 6, 30): -12090.0,
    }
    doc = store.doc("mf_flows")
    assert doc.source == "fred-z1-mf-flows"
    assert doc.payload["as_of"] == "2026-06-30"
    assert doc.payload["units"].startswith("USD millions")
    assert len(doc.payload["series"]) == 3


async def test_fetch_mf_flows_per_series_isolation(tmp_path, monkeypatch):
    """One bad FRED id must not starve the other series; the error names it."""
    store = Store(tmp_path / "t.db")

    async def fake_get(url, params=None, headers=None):
        fred_id = (params or {}).get("series_id")
        if fred_id == "BOGZ1FU653164205Q":
            raise RuntimeError("FRED 400: bad series")
        return obs_json(("2026-06-30", "1.0"))

    monkeypatch.setattr(fred_mf_flows.asyncio, "sleep", lambda *a, **k: _noop())
    with pytest.raises(RuntimeError, match="BOGZ1FU653164205Q"):
        await fetch_mf_flows(store, "TESTKEY", fake_get)
    # the good series still landed
    assert store.points("cycle:mf-flow-total-annual") == {date(2026, 6, 30): 1.0}
    assert store.points("cycle:mf-flow-hybrid") == {date(2026, 6, 30): 1.0}
    assert store.points("cycle:mf-flow-total") == {}
