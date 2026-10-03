"""Tests for batch 8: MARKET STRUCTURE universe (second coverage map on the
generic universe engine from batch 7). Synthetic; no network."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector.fetchers.ai_capex import load_universe
from collector.fetchers.ai_graph import fetch_universe_graph
from collector.insights import _ms_universe_note
from collector.panels import build_dashboard, _universe_panel

DATA = Path(__file__).resolve().parents[1] / "src" / "collector" / "data" / "market_structure.json"
UI = Path(__file__).resolve().parents[2] / "ui"

# Ground truth verified live 2026-10-03: Yahoo chart API HTTP 200 + CIKs from
# sec.gov/files/company_tickers.json. The test pins the JSON to this mapping.
VERIFIED = {
    "STT": "0000093751", "BLK": "0002012383", "SSNC": "0001402436",
    "BR": "0001383312", "TW": "0001758730", "MKTX": "0001278021",
    "VIRT": "0001592386", "SCHW": "0000316709", "IBKR": "0001381197",
    "HOOD": "0001783879", "MS": "0000895421", "GS": "0000886982",
    "JPM": "0000019617", "BAC": "0000070858", "C": "0000831001",
    "BCS": "0000312069", "UBS": "0001610520", "LPLA": "0001397911",
    "CME": "0001156375", "ICE": "0001571949", "NDAQ": "0001120193",
    "CBOE": "0001374310",
}
# Edge endpoints that are deliberately NOT universe nodes (acquired/delisted).
EXTERNAL_IDS = {"itg", "kcg", "td-ameritrade", "etrade", "bats", "adenza"}

EDGE_KINDS = {"acquire", "invest", "pfof", "owns", "spinoff", "affiliate"}


class FakeStore:
    def __init__(self, docs=None, series=None):
        self.docs = docs or {}
        self.series = series or {}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), {"payload": d, "updated_at": "2026-10-03T00:00:00Z",
                                "source": "test"})()

    def points(self, key, since=None):
        return self.series.get(key, {})

    def put_doc(self, key, payload, source=None):
        self.docs[key] = payload

    def status(self, key):
        return None


def _universe():
    return json.loads(DATA.read_text())


def _companies(u):
    return [c for v in u["verticals"] for c in v["companies"]]


def test_universe_file_schema():
    u = _universe()
    assert u["universe_id"] == "market_structure"
    assert u["_schema_note"]
    verticals = {v["id"] for v in u["verticals"]}
    assert verticals == {"oms", "ats", "market-makers", "nblp", "quant", "brokers", "exchanges"}
    ids = [c["id"] for c in _companies(u)]
    assert len(ids) == len(set(ids)) == 55
    for c in _companies(u):
        assert c["id"] and c["name"] and c["kind"] in ("public", "private")
        if c["kind"] == "public" and c.get("cik"):
            assert c.get("ticker")
    for e in u["edges"]:
        assert set(e) >= {"from", "to", "kind", "label", "source", "confidence"}
        assert e["kind"] in EDGE_KINDS
        assert e["confidence"] in ("confirmed", "reported")
    assert 15 <= len(u["edges"]) <= 30


def test_verified_tickers_and_ciks():
    u = _universe()
    by_ticker = {c["ticker"]: c for c in _companies(u)
                 if c["kind"] == "public" and c.get("ticker")}
    assert set(by_ticker) >= set(VERIFIED)
    for ticker, cik in VERIFIED.items():
        assert by_ticker[ticker]["cik"] == cik, f"{ticker} CIK mismatch"
    # Deutsche Börse: public but no US CIK — kept out of XBRL, still a node
    db = next(c for c in _companies(u) if c["id"] == "deutsche-boerse")
    assert db["kind"] == "public" and db["ticker"] == "DB1.DE" and not db.get("cik")


def test_edge_endpoints_valid():
    u = _universe()
    ids = {c["id"] for c in _companies(u)}
    for e in u["edges"]:
        assert e["from"] in ids, f"edge from unknown node {e['from']}"
        assert e["to"] in ids or e["to"] in EXTERNAL_IDS, f"edge to unknown {e['to']}"
    for a in u["aggregates"]:
        assert set(a) >= {"id", "label", "members", "metric"}
        missing = [m for m in a["members"] if m not in ids]
        assert not missing, f"aggregate {a['id']} missing members {missing}"
    assert {a["id"] for a in u["aggregates"]} == {"MSB6", "EXCH4"}


def test_load_universe_market_structure():
    u = load_universe("market_structure")
    assert u["universe_id"] == "market_structure"
    assert len(_companies(u)) == 55


@pytest.mark.asyncio
async def test_graph_job_writes_market_structure_graph():
    store = FakeStore(docs={
        "market_structure_capex": {
            "as_of": "2026-10-03", "universe_id": "market_structure",
            "companies": {
                "VIRT": {"id": "virtu", "name": "Virtu Financial",
                         "latest_quarter": "2026-06-30", "revenue": 1.2e9,
                         "capex": 5e6, "ocf": 0.4e9, "fcf": 0.39e9,
                         "flags": []},
            },
            "aggregates": {},
        }
    })
    out = await fetch_universe_graph(store, "market_structure")
    doc = store.docs["market_structure_graph"]
    assert doc["universe_id"] == "market_structure"
    vt = next(c for v in doc["verticals"] for c in v["companies"] if c["id"] == "virtu")
    assert vt["financials"]["revenue"] == 1.2e9
    assert doc["capex_overlay"] == "live"
    assert doc["rollups"]["node_count"] == 55
    assert "nodes," in out


def test_panels_expose_ms_flow():
    doc_payload = {**_universe(), "as_of": "2026-10-03",
                   "rollups": {"node_count": 55, "edge_count": 29},
                   "capex_stack": {}, "capex_overlay": "live"}
    store = FakeStore(docs={"market_structure_graph": doc_payload})
    panel = _universe_panel(store, "market_structure")
    assert panel["universe_id"] == "market_structure"
    assert len(panel["verticals"]) == 7
    assert len(panel["edges"]) == 29
    dash = build_dashboard(store, [], datetime.now(timezone.utc))
    assert dash["panels"]["ms_flow"]["universe_id"] == "market_structure"
    assert dash["panels"]["ai_flow"]["universe_id"] == "ai_buildout"


def test_ms_universe_note():
    store = FakeStore(docs={"market_structure_graph": {
        "as_of": "2026-10-03",
        "rollups": {"nodes_cashflow_negative": ["virtu"],
                    "total_reported_bn": 58.3, "node_count": 55},
        "capex_stack": {
            "__agg__MSB6": [["2026-06-30", 120e9]],
            "__agg__EXCH4": [["2026-06-30", 8e9]],
        },
    }})
    note = _ms_universe_note(store)
    assert note["neg_fcf"] == ["virtu"]
    assert note["banks_last_q"].startswith("$120.0B")
    assert note["exch_last_q"].startswith("$8.0B")
    assert note["total_reported_bn"] == 58.3
    assert _ms_universe_note(FakeStore()) is None


def test_universe_selector_js_parses():
    for f in ("js/panels/ai_flow.js", "js/main.js"):
        r = subprocess.run(["node", "--check", str(UI / f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{f}: {r.stderr}"


def test_selector_renderer_has_no_ai_hardcoding():
    src = (UI / "js" / "panels" / "ai_flow.js").read_text()
    assert "renderUniverseSelector" in src
    assert "market_structure" in src
    assert "CITgroup" not in src
    # new batch-8 edge kinds are styled
    for kind in ("acquire", "invest", "pfof", "owns", "spinoff"):
        assert f'"{kind}"' in src or f"'{kind}'" in src
    # per-universe state (no single global ego default leaking across universes)
    assert "stateFor" in src


def test_config_ms_ids_unique():
    from collector.config import load_config
    cfg = load_config(Path(__file__).resolve().parents[2] / "config.yaml")
    ids = [s.id for s in cfg.cycle_series if s.id.startswith("ms-")]
    assert len(ids) == len(set(ids)) == 22
    assert "ms-ms" in ids  # Morgan Stanley; msft is Microsoft (batch 5)
