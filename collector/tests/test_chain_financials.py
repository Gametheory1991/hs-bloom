"""Tests for the Blockchain Financials tab.

Covers: the _chain_financials_panel (empty state + passthrough), the
crypto: /api/series branch, and crypto.json universe validity
(public companies carry CIKs; new verified additions present;
SMLR replaced by ASST after the Jan-2026 merger close).
Synthetic only, no network.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from collector.api import create_app
from collector.config import load_config
from collector.panels import _chain_financials_panel
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Doc:
    def __init__(self, payload):
        self.payload = payload
        self.updated_at = None
        self.source = "test"


class _Store:
    def __init__(self, docs=None):
        self._docs = docs or {}

    def doc(self, key):
        d = self._docs.get(key)
        return _Doc(d) if d is not None else None


def test_panel_empty_state():
    p = _chain_financials_panel(_Store())
    assert p["companies"] == {} and p["as_of"] is None
    assert p["universe_id"] == "crypto"


def test_panel_passthrough():
    payload = {"as_of": "2026-10-07", "universe_id": "crypto",
               "companies": {"COIN": {"revenue": 1.0}},
               "aggregates": {}}
    p = _chain_financials_panel(_Store(docs={"crypto_capex": payload}))
    assert p["companies"]["COIN"]["revenue"] == 1.0
    assert p["as_of"] == "2026-10-07"


def test_series_branch_serves_crypto_metrics(tmp_path):
    store = Store(tmp_path / "t.db")
    cfg = load_config(REPO_ROOT / "config.yaml")
    client = TestClient(create_app(store, cfg))
    store.upsert_points("crypto:COIN:revenue",
                        [(date(2026, 6, 30), 1.4e9)])
    body = client.get("/api/series/crypto:COIN:revenue?range=max").json()
    assert body["unit"] == "$"
    assert body["points"] == [["2026-06-30", 1.4e9]]
    body = client.get("/api/series/crypto:COIN:net_margin?range=max").json()
    assert body["unit"] == "ratio"
    assert client.get("/api/series/crypto:coin:revenue").status_code == 404
    assert client.get("/api/series/crypto:TOOLONGTICKER:revenue").status_code == 404
    assert client.get("/api/dashboard?hub=structure").json()[
        "panels"].keys() >= {"chain_financials"}


def _crypto_universe():
    return json.loads(
        (REPO_ROOT / "collector" / "src" / "collector" / "data" / "crypto.json").read_text())


def test_crypto_universe_public_companies_have_ciks():
    u = _crypto_universe()
    missing = []
    for v in u["verticals"]:
        for c in v.get("companies", []):
            if c.get("kind") == "public" and c.get("ticker") != "3350.T":
                if not c.get("cik"):
                    missing.append(c.get("ticker"))
    assert missing == [], f"public companies missing CIKs: {missing}"


def test_crypto_universe_verified_additions():
    u = _crypto_universe()
    tickers = {c.get("ticker"): c for v in u["verticals"]
               for c in v.get("companies", [])}
    # New verified additions (web-verified 2026-10-07, CIKs from SEC)
    for t, cik in {"GLXY": "0001859392", "BMNR": "0001829311",
                   "SBET": "0001981535", "WULF": "0001083301"}.items():
        assert tickers[t]["cik"] == cik, f"{t} CIK mismatch"
        assert tickers[t]["kind"] == "public"
    # SMLR merged into Strive (ASST) Jan 2026 — replaced, not duplicated
    assert "SMLR" not in tickers
    assert tickers["ASST"]["cik"] == "0001920406"
    # Honest gap: Metaplanet is Tokyo-listed, no SEC CIK
    assert tickers["3350.T"]["kind"] == "public"
    assert not tickers["3350.T"].get("cik")
    # Private/offshore players stay private (no financials)
    assert tickers["BITF"]["cik"] == "0001812477"
