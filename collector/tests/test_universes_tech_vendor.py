"""Tests for the technology and vendor universe data files — schema validation.

Synthetic (no network): validates the vendored JSON coverage-map schema
(verticals -> companies, edges, aggregates, risk_notes), id uniqueness,
edge endpoint resolution, aggregate member resolution, and that every
public company carries a ticker (needed by the generic XBRL capex job).
Also exercises fetch_universe_graph against both files with a FakeStore.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.fetchers.ai_graph import fetch_universe_graph, load_universe

DATA = Path(__file__).resolve().parent.parent / "src" / "collector" / "data"

UNIVERSES = ["technology", "vendor"]


def _load(uid):
    with open(DATA / f"{uid}.json") as f:
        return json.load(f)


class FakeStore:
    def __init__(self):
        self.docs = {}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), {"payload": d, "updated_at": "", "source": ""})()

    def points(self, key, since=None):
        return {}

    def put_doc(self, key, payload, source=None):
        self.docs[key] = payload


@pytest.mark.parametrize("uid", UNIVERSES)
def test_universe_top_level_schema(uid):
    u = _load(uid)
    assert u["universe_id"] == uid
    assert u["title"]
    assert u["verticals"], "at least one vertical"
    assert isinstance(u.get("edges"), list)
    assert isinstance(u.get("aggregates"), list)
    assert isinstance(u.get("risk_notes"), list)


@pytest.mark.parametrize("uid", UNIVERSES)
def test_company_ids_unique(uid):
    u = _load(uid)
    ids = [c["id"] for v in u["verticals"] for c in v["companies"]]
    assert len(ids) == len(set(ids)), "duplicate company ids"
    assert len(ids) >= 10, "universe should have meaningful coverage"


@pytest.mark.parametrize("uid", UNIVERSES)
def test_edges_resolve(uid):
    u = _load(uid)
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    assert u["edges"], "universe should carry press-reported edges"
    for e in u["edges"]:
        assert e["from"] in ids, f"edge from {e['from']} unresolved"
        assert e["to"] in ids, f"edge to {e['to']} unresolved"
        assert e.get("label"), "edge needs a label"
        assert e.get("source"), "edge needs a source"
        assert e.get("confidence") in ("confirmed", "reported", "estimated"), \
            f"edge {e['from']}->{e['to']} needs confidence"


@pytest.mark.parametrize("uid", UNIVERSES)
def test_aggregates_resolve(uid):
    u = _load(uid)
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    assert u["aggregates"], "universe should define aggregates for the stacked chart"
    for a in u["aggregates"]:
        assert a["members"], f"aggregate {a['id']} has no members"
        for m in a["members"]:
            assert m in ids, f"aggregate member {m} unresolved"


@pytest.mark.parametrize("uid", UNIVERSES)
def test_public_companies_have_tickers(uid):
    u = _load(uid)
    pubs = [c for v in u["verticals"] for c in v["companies"]
            if c.get("kind") == "public"]
    assert pubs, "universe should include public companies"
    for c in pubs:
        assert c.get("ticker"), f"public {c['id']} missing ticker"
        # cik key must exist (may be empty for foreign filers like LSEG,
        # which the XBRL job then skips)
        assert "cik" in c, f"public {c['id']} missing cik key"


@pytest.mark.parametrize("uid", UNIVERSES)
def test_no_ats_venue_duplication(uid):
    """ATS operators and exchange venues belong to market_structure;
    these universes must not re-list them as companies."""
    u = _load(uid)
    ms = _load_market_structure_ids()
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    # venue-ish ids that must stay exclusive to market_structure
    forbidden = {"ubs-ats", "sigma-x", "ms-pool", "barclays-lx", "citadel-connect",
                 "luminex", "cme", "ice", "nasdaq", "cboe", "memx"}
    assert not (ids & forbidden), f"venue duplication: {ids & forbidden}"
    _ = ms  # loaded for future cross-checks


def _load_market_structure_ids():
    with open(DATA / "market_structure.json") as f:
        ms = json.load(f)
    return {c["id"] for v in ms["verticals"] for c in v["companies"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", UNIVERSES)
async def test_graph_job_runs_on_vendored_file(uid):
    store = FakeStore()
    out = await fetch_universe_graph(store, uid)
    doc = store.docs[f"{uid}_graph"]
    assert doc["universe_id"] == uid
    assert doc["verticals"], "graph doc should carry verticals"
    assert doc["rollups"]["edge_count"] == len(doc["edges"])
    assert doc["rollups"]["node_count"] > 0
    assert "nodes," in out


def test_technology_verticals():
    u = _load("technology")
    vids = {v["id"] for v in u["verticals"]}
    assert {"ems", "market_data_tech", "fintech_infra", "regtech",
            "crypto_infra", "post_trade"} <= vids


def test_vendor_verticals():
    u = _load("vendor")
    vids = {v["id"] for v in u["verticals"]}
    assert {"market_data", "index_providers", "rating_agencies",
            "alt_data", "research"} <= vids


def test_technology_key_tickers():
    u = _load("technology")
    tickers = {c["ticker"] for v in u["verticals"] for c in v["companies"]
               if c.get("kind") == "public"}
    assert {"FIS", "FISV", "V", "MA", "PYPL", "NICE", "COIN"} <= tickers


def test_vendor_key_tickers():
    u = _load("vendor")
    tickers = {c["ticker"] for v in u["verticals"] for c in v["companies"]
               if c.get("kind") == "public"}
    assert {"SPGI", "MCO", "MSCI", "FDS", "MORN"} <= tickers


def test_load_universe_works_for_both():
    for uid in UNIVERSES:
        u = load_universe(uid)
        assert u["universe_id"] == uid
