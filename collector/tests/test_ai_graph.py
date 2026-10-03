"""Tests for fetchers/ai_graph.py (universe money-flow graph) — synthetic."""
from __future__ import annotations

import pytest

from collector.fetchers.ai_graph import fetch_universe_graph, load_universe


class FakeStore:
    def __init__(self, docs=None, series=None):
        self.docs = docs or {}
        self.series = series or {}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), {"payload": d, "updated_at": "",
                                "source": ""})()

    def points(self, key, since=None):
        return self.series.get(key, {})

    def put_doc(self, key, payload, source=None):
        self.docs[key] = payload


@pytest.mark.asyncio
async def test_graph_overlays_financials_from_capex_doc():
    store = FakeStore(docs={
        "ai_buildout_capex": {
            "as_of": "2026-10-03", "universe_id": "ai_buildout",
            "companies": {
                "NVDA": {"id": "nvidia", "name": "Nvidia",
                         "latest_quarter": "2026-07-26", "revenue": 96e9,
                         "capex": 10e9, "fcf": 60e9,
                         "flags": []},
            },
            "aggregates": {},
        }
    })
    out = await fetch_universe_graph(store, "ai_buildout")
    doc = store.docs["ai_buildout_graph"]
    nv = next(c for v in doc["verticals"] for c in v["companies"]
              if c["id"] == "nvidia")
    assert nv["financials"]["revenue"] == 96e9
    assert nv["financials"]["fcf"] == 60e9
    assert doc["capex_overlay"] == "live"
    assert "nodes," in out
    # private lab nodes get no financials but stay present
    oai = next(c for v in doc["verticals"] for c in v["companies"]
               if c["id"] == "openai")
    assert oai["financials"] is None
    assert oai["flags"] == ["private_no_financials"]


@pytest.mark.asyncio
async def test_graph_without_capex_degrades():
    store = FakeStore()
    out = await fetch_universe_graph(store, "ai_buildout")
    doc = store.docs["ai_buildout_graph"]
    assert doc["capex_overlay"].startswith("pending")
    assert "nodes," in out


@pytest.mark.asyncio
async def test_graph_missing_universe_file():
    store = FakeStore()
    out = await fetch_universe_graph(store, "nope_not_a_universe")
    assert "missing" in out
    assert store.docs["nope_not_a_universe_graph"]["edges"] == []


def test_rollups_sane():
    u = load_universe("ai_buildout")
    from collector.fetchers.ai_graph import _rollups
    r = _rollups(u)
    assert r["node_count"] >= 65
    assert r["edge_count"] == 27
    assert r["total_reported_bn"] > 1000
