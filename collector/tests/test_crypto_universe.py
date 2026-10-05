"""Tests for the crypto universe data file — schema validation.

Synthetic (no network): validates the vendored JSON coverage-map schema
(verticals -> companies, edges, aggregates, risk_notes), id uniqueness,
edge endpoint resolution, aggregate member resolution, and that no
companies duplicate technology.json's crypto_infra vertical (Fireblocks,
Anchorage, BitGo, Bridge/Stripe live there). Also exercises
fetch_universe_graph against the file with a FakeStore.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.fetchers.ai_graph import fetch_universe_graph, load_universe

DATA = Path(__file__).resolve().parent.parent / "src" / "collector" / "data"

UID = "crypto"

# companies owned by technology.json's crypto_infra vertical — must not be
# duplicated here (checked by name, case-insensitive)
TECH_CRYPTO_INFRA = {"fireblocks", "anchorage digital", "anchorage", "bitgo", "bridge / stripe", "bridge"}


def _load(uid=UID):
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


def test_universe_top_level_schema():
    u = _load()
    assert u["universe_id"] == UID
    assert u["title"]
    assert u["verticals"], "at least one vertical"
    assert isinstance(u.get("edges"), list)
    assert isinstance(u.get("aggregates"), list)
    assert isinstance(u.get("risk_notes"), list)


def test_company_ids_unique():
    u = _load()
    ids = [c["id"] for v in u["verticals"] for c in v["companies"]]
    assert len(ids) == len(set(ids)), "duplicate company ids"


def test_expected_verticals_present():
    u = _load()
    vids = {v["id"] for v in u["verticals"]}
    for expected in ["exchanges", "miners", "stablecoins", "etf_issuers", "treasury_cos", "defi"]:
        assert expected in vids, f"missing vertical {expected}"


def test_public_companies_have_tickers():
    u = _load()
    for v in u["verticals"]:
        for c in v["companies"]:
            if c.get("kind") == "public":
                assert c.get("ticker"), f"public company {c['id']} missing ticker"


def test_no_tech_crypto_infra_duplication():
    u = _load()
    names = {c["name"].lower() for v in u["verticals"] for c in v["companies"]}
    overlap = names & TECH_CRYPTO_INFRA
    assert not overlap, f"duplicates technology.json crypto_infra: {overlap}"


def test_edges_resolve():
    u = _load()
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    for e in u["edges"]:
        assert e["from"] in ids, f"edge from unknown {e['from']}"
        assert e["to"] in ids, f"edge to unknown {e['to']}"
        assert e.get("label"), "edge missing label"
        assert e.get("source"), "edge missing source"
        assert e.get("confidence") in ("confirmed", "estimated"), "edge needs confidence"


def test_aggregates_resolve():
    u = _load()
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    for a in u["aggregates"]:
        assert a["members"], f"aggregate {a['id']} has no members"
        for m in a["members"]:
            assert m in ids, f"aggregate {a['id']} references unknown {m}"


def test_key_companies_present():
    u = _load()
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    for expected in ["coinbase", "mara", "circle", "strategy", "blackrock-ibit", "uniswap"]:
        assert expected in ids, f"missing key company {expected}"


def test_load_universe():
    u = load_universe("crypto")
    assert u["universe_id"] == "crypto"
    assert len(u["verticals"]) == 6


@pytest.mark.asyncio
async def test_graph_job_end_to_end():
    store = FakeStore()
    result = await fetch_universe_graph(store, "crypto")
    assert result  # truthy source name
    doc = store.doc("crypto_graph")
    assert doc is not None
    payload = doc.payload
    assert payload["universe_id"] == "crypto"
    assert len(payload["verticals"]) == 6
