"""Tests for the etf universe (data file + graph wiring) — synthetic."""
from __future__ import annotations

import json
from importlib.resources import files

import pytest

from collector.fetchers.ai_graph import fetch_universe_graph, load_universe

UID = "etf"

# Tickers we assert are real, tradeable symbols (spot-checked against
# public knowledge).
EXPECTED_TICKERS = {
    "BLK", "STT", "IVZ", "SCHW", "WT",
    "VIRT", "JPM", "GS", "MS", "BAC", "C",
    "MSCI", "NDAQ",
}

VALID_KINDS = {"public", "private"}
VALID_CONFIDENCE = {"confirmed", "reported", "estimated"}


def _universe() -> dict:
    raw = (files("collector") / "data" / f"{UID}.json").read_text(encoding="utf-8")
    return json.loads(raw)


def test_universe_file_loads_and_has_required_keys():
    u = _universe()
    for key in ("universe_id", "title", "verticals", "edges", "risk_notes"):
        assert key in u, f"missing top-level key {key}"
    assert u["universe_id"] == UID


def test_verticals_have_valid_companies():
    u = _universe()
    assert len(u["verticals"]) >= 5
    seen = set()
    for v in u["verticals"]:
        assert v["id"] and v["label"], f"vertical missing id/label: {v}"
        assert v["companies"], f"vertical {v['id']} has no companies"
        for c in v["companies"]:
            assert c["id"] and c["name"], f"company missing id/name: {c}"
            assert c.get("kind") in VALID_KINDS, f"bad kind: {c}"
            assert c["id"] not in seen, f"duplicate company id {c['id']}"
            seen.add(c["id"])
            if c["kind"] == "public":
                assert c.get("ticker"), f"public company {c['id']} missing ticker"


def test_expected_tickers_present():
    u = _universe()
    tickers = {c.get("ticker") for v in u["verticals"]
               for c in v["companies"] if c.get("ticker")}
    missing = EXPECTED_TICKERS - tickers
    assert not missing, f"missing expected tickers: {missing}"


def test_expected_verticals_present():
    u = _universe()
    vids = {v["id"] for v in u["verticals"]}
    for expected in ("issuers", "market_makers", "authorized_participants",
                     "liquidity_providers", "index_providers", "buy_side"):
        assert expected in vids, f"missing vertical {expected}"


def test_edges_reference_valid_companies():
    u = _universe()
    all_ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    for e in u["edges"]:
        assert e["from"] in all_ids, f"edge from {e['from']} not a company"
        assert e["to"] in all_ids, f"edge to {e['to']} not a company"
        assert e.get("confidence") in VALID_CONFIDENCE, f"bad confidence: {e}"
        assert e.get("source"), f"edge missing source: {e}"


def test_aggregates_reference_valid_members():
    u = _universe()
    all_ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    for agg in u.get("aggregates", []):
        assert agg["id"] and agg["label"], f"aggregate missing id/label: {agg}"
        for m in agg["members"]:
            assert m in all_ids, f"aggregate member {m} not a company"


def test_no_tech_vendor_duplication():
    """ETF universe should not duplicate technology.json/vendor.json companies."""
    u = _universe()
    etf_ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    # Technology universe has trading-tech companies; vendor has data vendors.
    # ETF-specific overlaps that are OK (shared real-world entities):
    allowed_overlap = {"blackrock", "state-street", "msci", "nasdaq-index"}
    # These should NOT be in etf.json as they belong to tech/vendor:
    tech_vendor_only = {"fis", "stripe", "exegy", "spgi", "factset", "moodys"}
    overlap = etf_ids & tech_vendor_only
    assert not overlap, f"etf.json duplicates tech/vendor companies: {overlap}"


def test_graph_job_runs_end_to_end(tmp_path):
    """The generic graph fetcher works on the ETF universe file."""
    import asyncio
    from collector.store import Store
    store = Store(str(tmp_path / "test.db"))
    result = asyncio.run(fetch_universe_graph(store, UID))
    assert UID in result
    doc = store.doc(f"{UID}_graph")
    assert doc is not None
    payload = doc.payload
    assert payload["universe_id"] == UID
    assert len(payload["verticals"]) == 6
    assert payload["rollups"]["node_count"] == 34


def test_load_universe_returns_etf():
    u = load_universe(UID)
    assert u["universe_id"] == UID
    assert u["title"] == "ETF ecosystem"
