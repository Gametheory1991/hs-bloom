"""Tests for the dynamic ticker master (company name + GICS sector resolver).

No network: get_text/post_json are faked; env keys are monkeypatched.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from collector import ticker_master
from collector.store import Store
from collector.ticker_master import (
    _clean_name,
    _sector_for,
    load_master,
    resolve_tickers,
)


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_clean_name_title_cases_allcaps():
    assert _clean_name("FINGERMOTION, INC.") == "Fingermotion, Inc."
    assert _clean_name("Apple Inc") == "Apple Inc"
    assert _clean_name("  spaced   out  ") == "spaced out"


def test_sector_for_etf_hints():
    assert _sector_for("ProShares Bitcoin ETF", None, None) == "ETF"
    assert _sector_for("Direxion Daily Semiconductor Bear 3X Shares",
                       None, None) == "ETF"


def test_sector_for_finnhub_industry():
    assert _sector_for("Apple Inc", "Technology", None) == "Technology"
    assert _sector_for("Nu Holdings Ltd.", "Financial Services",
                       None) == "Financials"
    assert _sector_for("Mystery Co", "Weird Industry", None) == "Other"


def test_load_master_seed_on_empty_store(tmp_path):
    master = load_master(_store(tmp_path))
    assert master["NVDA"]["name"] == "Nvidia"
    assert master["FNGR"]["name"] == "FingerMotion"  # 2026-10-05 seed
    assert master["AAL"]["sector"] == "Industrials"


def test_resolve_all_known_no_api_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    calls = []

    async def _boom(*a, **k):
        calls.append(1)
        raise AssertionError("should not be called")

    master = asyncio.run(resolve_tickers(
        _store(tmp_path), ["NVDA", "FNGR"], get_text=_boom,
        post_json=_boom))
    assert calls == []
    assert master["NVDA"]["name"] == "Nvidia"
    assert master["FNGR"]["sector"] == "Technology"


def test_resolve_via_finnhub_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    store = _store(tmp_path)
    seen = []

    async def fake_get(url, params=None, headers=None):
        seen.append(params["symbol"])
        assert params["token"] == "test-key"
        assert "finnhub.io" in url
        return json.dumps({"name": "TestCorp Inc",
                           "finnhubIndustry": "Technology"})

    master = asyncio.run(resolve_tickers(
        store, ["ZZUNKNOWN", "NVDA"], get_text=fake_get, post_json=None))
    assert seen == ["ZZUNKNOWN"]  # NVDA already seeded: no call
    assert master["ZZUNKNOWN"] == {"name": "TestCorp Inc",
                                  "sector": "Technology",
                                  "source": "finnhub",
                                  "updated": master["ZZUNKNOWN"]["updated"]}
    # persisted to the ticker_master doc (seed excluded from storage)
    doc = store.doc("ticker_master")
    assert doc is not None
    assert doc.payload["tickers"]["ZZUNKNOWN"]["name"] == "TestCorp Inc"
    assert "NVDA" not in doc.payload["tickers"]
    # second call is cache-first: no HTTP
    seen.clear()
    master2 = asyncio.run(resolve_tickers(
        store, ["ZZUNKNOWN"], get_text=fake_get, post_json=None))
    assert seen == []
    assert master2["ZZUNKNOWN"]["name"] == "TestCorp Inc"


def test_resolve_falls_back_to_openfigi(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setenv("OPENFIGI_API_KEY", "test-key")
    store = _store(tmp_path)

    async def fake_post(url, jobs, headers=None):
        assert headers == {"X-OPENFIGI-APIKEY": "test-key"}
        return [{"data": [{"ticker": jobs[0]["idValue"],
                           "name": "FAKE INDUSTRIES LTD",
                           "securityType": "Common Stock"}]}]

    master = asyncio.run(resolve_tickers(
        store, ["ZZQ"], get_text=None, post_json=fake_post))
    assert master["ZZQ"]["name"] == "Fake Industries Ltd"
    assert master["ZZQ"]["source"] == "openfigi"


def test_resolve_no_keys_degrades_to_seed(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    store = _store(tmp_path)
    master = asyncio.run(resolve_tickers(
        store, ["ZZNOPE"], get_text=None, post_json=None))
    assert "ZZNOPE" not in master
    assert store.doc("ticker_master") is None
