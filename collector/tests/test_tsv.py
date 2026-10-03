"""Batch 9 tests: TSV universe file, watch fetcher, scheduler, panel."""
from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from pathlib import Path

import pytest

from collector.fetchers.tsv_watch import (
    build_payload,
    fetch_tsv_watch,
    parse_fr_documents,
    parse_press_listing,
)
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"


def load_universe():
    raw = (files("collector") / "data" / "tokenized_securities.json").read_text()
    return json.loads(raw)


def test_universe_schema_valid():
    u = load_universe()
    assert u["universe_id"] == "tokenized_securities"
    assert u["verticals"]
    ids = [c["id"] for v in u["verticals"] for c in v["companies"]]
    assert len(ids) == len(set(ids)), "duplicate company ids"
    for e in u["edges"]:
        assert e["from"] in ids and e["to"] in ids
        assert e.get("source") and e.get("confidence")
    for v in u["verticals"]:
        for c in v["companies"]:
            assert c.get("status"), f"{c['id']} missing status"
            if c["kind"] == "public":
                assert c.get("cik"), f"{c['id']} public but no CIK"


def test_universe_order_facts():
    o = load_universe()["order"]
    assert o["release"] == "34-106402"
    assert o["file_no"] == "4-927"
    assert o["issued"] == "2026-09-17"
    assert o["expires"] == "2031-09-17"
    assert o["tier1_symbols"] == 75
    assert o["tier2_symbols"] == 250


def test_no_operators_claimed():
    """Nobody may be marked operating/filed/announced without a real notice."""
    u = load_universe()
    ops = next(v for v in u["verticals"] if v["id"] == "tsv_operators")
    for c in ops["companies"]:
        assert c["status"] in ("positioned", "rumored"), c["id"]


def test_parse_press_listing():
    html_text = (FIX / "tsv_press.html").read_text()
    hits = parse_press_listing(html_text)
    assert len(hits) == 2  # only the tokeniz-titled links
    assert hits[0]["url"].startswith("https://www.sec.gov/news/press-release/2026-90")


def test_parse_fr_documents():
    payload = json.loads((FIX / "tsv_fr.json").read_text())
    docs = parse_fr_documents(payload)
    assert len(docs) == 2
    assert "Tokenized Securities Venues" in docs[0]["title"]


def test_fetch_tsv_watch_writes_doc(tmp_path):
    async def fake_get_text(url, headers=None):
        if "federalregister" in url:
            return (FIX / "tsv_fr.json").read_text()
        return (FIX / "tsv_press.html").read_text()

    store = Store(tmp_path / "t.db")
    src = asyncio.run(fetch_tsv_watch(store, fake_get_text))
    assert src == "tsv-watch"
    doc = store.doc("tsv_watch")
    assert doc is not None
    p = doc.payload
    assert p["order"]["release"] == "34-106402"
    assert len(p["press_hits"]) == 2
    assert len(p["federal_register_hits"]) == 2
    assert "No central SEC registry" in p["caveat"]


def test_fetch_tsv_watch_degrades(tmp_path):
    async def boom(url, headers=None):
        raise ConnectionError("down")

    store = Store(tmp_path / "t.db")
    src = asyncio.run(fetch_tsv_watch(store, boom))
    assert src == "tsv-watch"  # still succeeds, records errors
    assert len(store.doc("tsv_watch").payload["errors"]) == 2


def test_scheduler_registers_tsv_jobs():
    from collector import scheduler as sch

    src = Path(sch.__file__).read_text()
    for job in ('"tsv_capex"', '"tsv_graph"', '"tsv_watch"'):
        assert job in src, job
    assert '"tokenized_securities"' in src


def test_tsv_panel_shape(tmp_path):
    from collector.panels import _tsv_panel

    store = Store(tmp_path / "t.db")
    panel = _tsv_panel(store)  # empty store degrades
    assert panel["universe_id"] == "tokenized_securities"
    assert panel["verticals"] == []
    assert panel["watch"] is None
