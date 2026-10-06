"""Tests for the debt-cube wiring (batch 13 part C).

Covers:
  (a) /api/debt-cube with a fake debt_cube doc returns filtered cells;
  (b) missing doc -> 200 with an empty-cells payload (never a 500);
  (c) config loads; every new holdings series id + both new QUANT panel
      sections resolve (no unknown-series references);
  (d) scheduler registers the four new jobs (z1_holdings, mspd, soma_cusip,
      debt_cube) with the expected cadences.
"""
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient

from collector.api import create_app
from collector.config import load_config
from collector.newsletter import load_smtp_cfg
from collector.scheduler import register_jobs
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]

UST_IDS = [
    "z1-ust-fed", "z1-ust-banks", "z1-ust-mutual-funds", "z1-ust-mmf",
    "z1-ust-foreign", "z1-ust-priv-pension", "z1-ust-sl-retire",
    "z1-ust-life-ins", "z1-ust-pc-ins", "z1-ust-households",
    "z1-ust-nonfin-corp", "z1-ust-sl-govt", "z1-ust-hedge-funds-net",
]
CORP_IDS = [
    "z1-corp-banks", "z1-corp-mutual-funds", "z1-corp-mmf",
    "z1-corp-priv-pension", "z1-corp-life-ins", "z1-corp-pc-ins",
    "z1-corp-foreign", "z1-corp-households", "z1-corp-nonfin-corp",
    "z1-corp-sl-govt", "z1-corp-sl-retire",
]
AGENCY_IDS = [
    "z1-agency-fed", "z1-agency-banks", "z1-agency-mutual-funds",
    "z1-agency-mmf", "z1-agency-priv-pension", "z1-agency-sl-retire",
    "z1-agency-life-ins", "z1-agency-pc-ins", "z1-agency-foreign",
    "z1-agency-households", "z1-agency-nonfin-corp", "z1-agency-sl-govt",
]
MUNI_IDS = [
    "z1-muni-banks", "z1-muni-mutual-funds", "z1-muni-mmf",
    "z1-muni-life-ins", "z1-muni-pc-ins", "z1-muni-foreign",
    "z1-muni-households", "z1-muni-nonfin-corp", "z1-muni-sl-govt",
    "z1-muni-sl-retire",
]
EXTRA_IDS = ["fed-soma-weekly", "mspd-total-outstanding", "soma-total-par"]
ALL_NEW_IDS = UST_IDS + CORP_IDS + AGENCY_IDS + MUNI_IDS + EXTRA_IDS


class FakeDoc:
    def __init__(self, payload, updated_at=None, source="test"):
        self.payload = payload
        self.updated_at = updated_at
        self.source = source


class FakeStore:
    def __init__(self, docs=None):
        self._docs = docs or {}

    def points(self, key):
        return {}

    def upsert_points(self, key, pts):
        pass

    def doc(self, key):
        return self._docs.get(key)

    def put_doc(self, key, payload, source=None):
        self._docs[key] = FakeDoc(payload)


FAKE_CUBE = {
    "asof": "2026-08-31",
    "denominator_mn": 40_175_600.0,
    "denominator_desc": "MSPD Table 1 Total Public Debt Outstanding",
    "cells": [
        {"product": "bills", "maturity": "<1Y", "holder": "public",
         "notional_bn": 7000.0, "pct_of_total": 17.4241},
        {"product": "bills", "maturity": "<1Y", "holder": "soma",
         "notional_bn": 248.1, "pct_of_total": 0.6175},
        {"product": "notes", "maturity": "5-10Y", "holder": "public",
         "notional_bn": 5200.0, "pct_of_total": 12.9431},
        {"product": "tips", "maturity": "10-20Y", "holder": "soma",
         "notional_bn": 300.0, "pct_of_total": 0.7467},
        {"product": "nonmarketable", "maturity": "all", "holder": "nonmarketable",
         "notional_bn": 8347.6, "pct_of_total": 20.7778},
    ],
}


def _client(docs=None):
    store = FakeStore(docs)
    cfg = load_config(REPO_ROOT / "config.yaml")
    return TestClient(create_app(store, cfg))


# ------------------------------------------------------------------ (a) route
def test_debt_cube_full_payload():
    client = _client({"debt_cube": FakeDoc(FAKE_CUBE, updated_at="t")})
    body = client.get("/api/debt-cube").json()
    assert body["asof"] == "2026-08-31"
    assert body["denominator"]["notional_bn"] == 40175.6
    assert "MSPD Table 1" in body["denominator"]["desc"]
    assert len(body["cells"]) == 5
    for c in body["cells"]:
        assert set(c) == {"product", "maturity", "holder", "notional_bn",
                          "pct_of_total"}


def test_debt_cube_filters():
    client = _client({"debt_cube": FakeDoc(FAKE_CUBE)})
    body = client.get("/api/debt-cube", params={"product": "bills"}).json()
    assert {c["holder"] for c in body["cells"]} == {"public", "soma"}
    assert all(c["product"] == "bills" for c in body["cells"])

    body = client.get("/api/debt-cube", params={"holder": "soma"}).json()
    assert len(body["cells"]) == 2

    body = client.get("/api/debt-cube",
                      params={"product": "notes", "maturity": "5-10Y",
                              "holder": "public"}).json()
    assert len(body["cells"]) == 1
    assert body["cells"][0]["notional_bn"] == 5200.0

    body = client.get("/api/debt-cube", params={"product": "frns"}).json()
    assert body["cells"] == []


# ------------------------------------------------------------------ (b) empty
def test_debt_cube_missing_doc_returns_200_empty():
    client = _client({})
    r = client.get("/api/debt-cube")
    assert r.status_code == 200
    body = r.json()
    assert body == {"asof": None, "denominator": None, "cells": [],
                    "updated_at": None}


# ------------------------------------------------------------------ (c) config
def test_holdings_series_all_defined():
    cfg = load_config(REPO_ROOT / "config.yaml")
    by_id = {s.id: s for s in cfg.cycle_series}
    assert len(ALL_NEW_IDS) == 49
    for sid in ALL_NEW_IDS:
        assert sid in by_id, f"cycle series missing: {sid}"
        assert by_id[sid].external, f"{sid} should be external (dedicated job)"
    # unit check: Z.1 + FRED + MSPD are $mn; SOMA total-par is $bn by fetcher
    for sid in ALL_NEW_IDS:
        want = "$bn" if sid == "soma-total-par" else "$m"
        assert by_id[sid].unit == want, f"{sid} unit"
    # hedge-fund name must say NET of shorts
    assert "NET" in by_id["z1-ust-hedge-funds-net"].name


def test_quant_panels_reference_known_series():
    cfg = load_config(REPO_ROOT / "config.yaml")
    by_id = {s.id: s for s in cfg.cycle_series}
    quant = next(t for t in cfg.cycle_tabs if t.id == "quant")
    by_title = {p.title: p for p in quant.panels}
    tre = by_title["HOLDINGS BY HOLDER — TREASURIES (Z.1, QUARTERLY $m)"]
    oth = by_title["HOLDINGS BY HOLDER — CORP/AGENCY/MUNI (Z.1, QUARTERLY $m)"]
    assert [r.series for r in tre.rows] == UST_IDS
    assert [r.series for r in oth.rows] == CORP_IDS + AGENCY_IDS + MUNI_IDS
    unknown = [r.series for p in (tre, oth) for r in p.rows
               if r.series not in by_id]
    assert unknown == [], f"unknown series referenced: {unknown}"


# ------------------------------------------------------------------ (d) jobs
async def _fake_get(url, params=None):
    raise AssertionError("no network in tests")


async def _fake_post(url, json=None, headers=None):
    raise AssertionError("no network in tests")


async def _fake_bytes(url, params=None, headers=None):
    raise AssertionError("no network in tests")


def test_scheduler_registers_new_jobs(tmp_path):
    cfg = load_config(REPO_ROOT / "config.yaml")
    store = Store(tmp_path / "t.db")
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_jobs(
        scheduler, cfg, store, get_text=_fake_get, post_json=_fake_post,
        get_bytes=_fake_bytes, fred_api_key="k",
        smtp_cfg=load_smtp_cfg({"SMTP_PORT": "465"}))
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert jobs["z1_holdings"].trigger.interval.total_seconds() == 2592000
    assert jobs["mspd"].trigger.interval.total_seconds() == 2592000
    assert jobs["soma_cusip"].trigger.interval.total_seconds() == 604800
    assert jobs["debt_cube"].trigger.interval.total_seconds() == 86400
    assert jobs["debt_cube"].next_run_time is not None
    assert jobs["newsletter"].trigger.interval.total_seconds() == 1800
