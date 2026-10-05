"""Tests for the bank_fixed_income universe (data file + graph wiring) — synthetic."""
from __future__ import annotations

import json
from importlib.resources import files

import pytest

from collector.fetchers.ai_graph import fetch_universe_graph, load_universe

UID = "bank_fixed_income"

# Tickers we assert are real, tradeable symbols (spot-checked against
# public knowledge; Yahoo spot-check is best-effort behind rate limits).
EXPECTED_TICKERS = {
    "JPM", "BAC", "C", "WFC", "GS", "MS",
    "USB", "PNC", "TFC", "FITB", "KEY", "RF", "HBAN", "COF",
    "STT", "BK", "JEF", "FNMA", "FMCC",
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


def test_ticker_format_sane():
    u = _universe()
    for v in u["verticals"]:
        for c in v["companies"]:
            t = c.get("ticker")
            if t:
                assert t == t.strip().upper(), f"ticker not normalized: {t!r}"
                assert 1 <= len(t) <= 6, f"suspicious ticker length: {t!r}"
                assert t.isalpha() or "." in t, f"suspicious ticker chars: {t!r}"


def test_edges_reference_existing_companies():
    u = _universe()
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    assert u["edges"], "universe has no edges"
    for e in u["edges"]:
        assert e["from"] in ids, f"edge from unknown node: {e['from']}"
        assert e["to"] in ids, f"edge to unknown node: {e['to']}"
        assert e.get("kind"), f"edge missing kind: {e}"
        assert e.get("confidence") in VALID_CONFIDENCE, f"bad confidence: {e}"
        assert e.get("source"), f"edge missing source: {e}"
        assert e.get("label"), f"edge missing label: {e}"


def test_estimated_edges_flagged_honestly():
    u = _universe()
    estimated = [e for e in u["edges"] if e["confidence"] == "estimated"]
    assert estimated, "expected some estimated structural edges"
    for e in estimated:
        assert "structural" in e["label"].lower() or "aggregate" in e["source"].lower(), \
            f"estimated edge should say so: {e['label'][:60]}"


def test_aggregates_reference_existing_members():
    u = _universe()
    ids = {c["id"] for v in u["verticals"] for c in v["companies"]}
    for agg in u.get("aggregates", []):
        assert agg["id"] and agg["label"] and agg["metric"]
        for m in agg["members"]:
            assert m in ids, f"aggregate {agg['id']} references unknown {m}"


def test_gse_conservatorship_noted():
    u = _universe()
    gse = next(v for v in u["verticals"] if v["id"] == "gse")
    names = " ".join(c["name"] for c in gse["companies"])
    assert "conservatorship" in names.lower()
    notes = " ".join(n["text"] for n in u["risk_notes"])
    assert "conservatorship" in notes.lower()


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
async def test_graph_fetcher_runs_on_bank_universe():
    store = FakeStore()
    out = await fetch_universe_graph(store, UID)
    doc = store.docs[f"{UID}_graph"]
    assert doc["universe_id"] == UID
    assert doc["rollups"]["node_count"] >= 25
    assert doc["rollups"]["edge_count"] >= 8
    assert "nodes," in out
    # no capex doc yet -> pending overlay, must not crash
    assert doc["capex_overlay"].startswith("pending")


@pytest.mark.asyncio
async def test_load_universe_uses_generic_loader():
    u = load_universe(UID)
    assert u["universe_id"] == UID
