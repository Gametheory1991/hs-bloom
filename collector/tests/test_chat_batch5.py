"""Tests for the batch-5 chat upgrades: analyst pack + company lookup.

The Gemini call itself is mocked; these tests assert the context pack is
built and injected into the prompt.
"""
from __future__ import annotations

from datetime import date

import pytest

import collector.chat as chat_mod
from collector.chat import (
    ask_gemini,
    build_analyst_pack,
    build_context,
    company_card,
)


class FakeStore:
    def __init__(self, series=None, docs=None):
        self.series = series or {}
        self.docs = docs or {}

    def points(self, key, since=None):
        return self.series.get(key, {})

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), {"payload": d, "source": None})()


class FakeCfg:
    series = []
    cycle_series = []
    indexes = []
    bonds = []
    cb_rates = []
    refs = type("R", (), {"aave": [], "pendle": [], "funding": []})()


def _store():
    docs = {
        "risk_summary": {
            "regime": "LATE_CYCLE",
            "verdict": "ELEVATED: basis-trade stress 61/100",
            "components": {
                "basis_stress": {"value": 61},
                "auction_stress": {"value": 22},
                "recession_prob": {"value": 18},
            },
        },
        "voldash": {
            "regime": "vol pricing mixed",
            "rows": [
                {"ticker": "EWZ", "implied": 46.0, "pctile_1y": 98.0},
                {"ticker": "SPX", "implied": 11.9, "pctile_1y": 5.0},
            ],
        },
        "movers": {
            "asof": "2026-10-03",
            "indexes": {
                "spx": {
                    "label": "S&P 500",
                    "win5d": {"up": [{"symbol": "P", "z": 1.8, "ret_pct": 5.1}],
                              "down": [{"symbol": "GEN", "z": -5.14, "ret_pct": -9.2}]},
                    "win20d": {"up": [{"symbol": "SWKS", "z": 2.61, "ret_pct": 8.0}],
                               "down": [{"symbol": "GEN", "z": -3.13, "ret_pct": -12.0}]},
                },
                "ndx": {"label": "Nasdaq 100", "win5d": {"up": [], "down": []},
                        "win20d": {"up": [], "down": []}},
            },
            "all": {"AAPL": {"idx": "spx", "z5": 1.2, "ret5": 2.1,
                             "z20": -0.4, "ret20": -1.0}},
        },
        "country_risk": {
            "countries": [
                {"code": "US", "bucket": "green"},
                {"code": "IT", "bucket": "red"},
            ]
        },
        "hyper": {
            "issuances": [{
                "issuer": "Alphabet", "form": "424B2", "filing_date": "2026-08-07",
                "tranches": [{"coupon_pct": 4.5, "maturity_year": 2028,
                              "principal_usd": 1_250_000_000}],
            }],
        },
        "macro_calendar": {
            "releases": [{"country": "USD", "event": "CPI", "time": "2026-10-14T12:30:00"}]
        },
        "news": {"items": [{"title": "Stocks hit record as yields climb"}]},
    }
    return FakeStore(docs=docs)


def test_analyst_pack_contents():
    pack = build_analyst_pack(_store())
    assert pack.startswith("MARKET SNAPSHOT:")
    assert "LATE_CYCLE" in pack
    assert "61/100" in pack
    assert "EWZ" in pack and "98th" in pack
    assert "GEN" in pack  # top mover
    assert "IT" in pack  # red-zone country
    assert "Alphabet" in pack and "424B2" in pack
    assert "CPI" in pack
    assert "Stocks hit record" in pack
    # budget: comfortably under 3k tokens (~4 chars/token)
    assert len(pack) < 9000, f"pack too long: {len(pack)} chars"


def test_analyst_pack_empty_store():
    pack = build_analyst_pack(FakeStore())
    assert "MARKET SNAPSHOT:" in pack
    assert "UNKNOWN" in pack


def test_company_card_known_ticker():
    card = company_card(_store(), "What is AAPL doing?")
    assert card is not None
    assert "AAPL" in card
    assert "+1.20" in card  # z5 formatted
    assert "SPX" in card


def test_company_card_unknown_ticker():
    assert company_card(_store(), "What is XYZQQ doing?") is None


def test_company_card_no_ticker():
    assert company_card(_store(), "How is the market?") is None


def test_company_card_earnings_disclaimer():
    card = company_card(_store(), "What is AAPL's EPS estimate?")
    assert card is not None
    assert "No earnings/estimates feed" in card


def test_build_context_injects_pack():
    text, _ = build_context(_store(), FakeCfg(), "How is the market?")
    assert text.startswith("MARKET SNAPSHOT:")
    assert "LATEST VALUES" in text


def test_build_context_appends_company_card():
    text, _ = build_context(_store(), FakeCfg(), "Tell me about AAPL")
    assert "COMPANY: AAPL" in text


class _FakeResp:
    status_code = 200

    def json(self):
        return {"candidates": [{"content": {"parts": [
            {"text": "VIX is elevated. "}, {"text": "Hedges rich."}]}}]}


def test_ask_gemini_mocked(monkeypatch):
    def _post(url, headers=None, json=None, timeout=None):
        assert "generateContent" in url
        assert json["system_instruction"]["parts"][0]["text"]
        return _FakeResp()

    monkeypatch.setattr(chat_mod.httpx, "post", _post)
    out = ask_gemini("key", "model", "system", [{"role": "user", "content": "hi"}])
    assert out == "VIX is elevated. Hedges rich."


def test_ask_gemini_http_error(monkeypatch):
    class _Bad:
        status_code = 503

        def json(self):
            return {}

        text = "overloaded"

    monkeypatch.setattr(chat_mod.httpx, "post", lambda *a, **k: _Bad())
    with pytest.raises(RuntimeError, match="gemini HTTP 503"):
        ask_gemini("key", "model", "system", [])
