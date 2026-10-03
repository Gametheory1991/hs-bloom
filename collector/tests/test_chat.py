"""Tests for the in-dashboard analyst chat (collector.chat + /api/chat)."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from collector.api import create_app
from collector.chat import ask_gemini, build_context, match_series
from collector.config import load_config
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_store(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_points("macro:us-cpi-yoy", [
        (date(2024, 1, 1), 300.0),
        (date(2025, 1, 1), 309.0),
        (date(2026, 1, 1), 318.27),
    ])
    store.upsert_points("cycle:vix", [
        (date(2025, 12, 1), 18.0),
        (date(2026, 1, 5), 22.5),
    ])
    store.put_doc("insights", {
        "digest_id": "abc123",
        "generated_at": "2026-10-03T00:00:00Z",
        "alerts": [{
            "name": "VIX",
            "summary": "22.50 is 2.50\u03c3 above trend",
            "as_of": "2026-01-05",
        }],
        "trends": [],
        "newsletter": {"headline": "1 anomaly", "bullets": ["Lead signal: VIX."]},
    }, source="local-analysis")
    return store


def test_match_series_finds_cpi_by_token():
    cfg = load_config(REPO_ROOT / "config.yaml")
    ids = [s.id for _, s in match_series(cfg, "what is cpi doing vs last year?")]
    assert "us-cpi-yoy" in ids


def test_match_series_finds_vix():
    cfg = load_config(REPO_ROOT / "config.yaml")
    ids = [s.id for _, s in match_series(cfg, "why is the vix spiking?")]
    assert "vix" in ids


def test_match_series_no_false_positive():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert match_series(cfg, "hello there") == []


def test_build_context_includes_digest_and_history(tmp_path):
    cfg = load_config(REPO_ROOT / "config.yaml")
    store = make_store(tmp_path)
    context, series_ids = build_context(store, cfg, "why is the vix spiking?")
    assert "vix" in series_ids
    assert "VIX" in context
    assert "2.50\u03c3 above trend" in context  # stored digest, not recomputed
    assert "2026-01-05" in context  # history line
    assert "LATEST VALUES" in context


def test_build_context_empty_store_no_crash(tmp_path):
    cfg = load_config(REPO_ROOT / "config.yaml")
    store = Store(tmp_path / "empty.db")
    context, series_ids = build_context(store, cfg, "what is cpi doing?")
    assert "us-cpi-yoy" in series_ids
    assert "no digest generated yet" in context
    assert "no data" in context


def test_chat_503_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")
    cfg = load_config(REPO_ROOT / "config.yaml")
    client = TestClient(create_app(store, cfg))
    resp = client.post("/api/chat", json={"message": "hi", "history": []})
    assert resp.status_code == 503
    assert "GEMINI_API_KEY" in resp.json()["detail"]


def test_chat_happy_path_mocks_provider(tmp_path, monkeypatch):
    import httpx
    from collector import chat as chatmod

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_MODEL", "test-model")

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "VIX is elevated."}]}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        assert headers["x-goog-api-key"] == "test-key"
        assert "test-model" in url
        assert json["system_instruction"]["parts"][0]["text"]  # persona present
        assert json["generationConfig"]["maxOutputTokens"] == 1024
        user_msgs = [m for m in json["contents"] if m["role"] == "user"]
        assert user_msgs and "QUESTION:" in user_msgs[-1]["parts"][0]["text"]
        assert any(m["role"] == "model" for m in json["contents"])  # history kept
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    store = make_store(tmp_path)
    cfg = load_config(REPO_ROOT / "config.yaml")
    client = TestClient(create_app(store, cfg))
    resp = client.post("/api/chat", json={
        "message": "why is the vix spiking?",
        "history": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "VIX is elevated."
    assert "vix" in body["series_used"]


def test_ask_gemini_raises_on_http_error(monkeypatch):
    import httpx
    from collector import chat as chatmod

    class FakeResp:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError, match="500"):
        chatmod.ask_gemini("key", "model", "system",
                              [{"role": "user", "content": "hi"}])


def test_ask_gemini_returns_text(monkeypatch):
    import httpx
    from collector import chat as chatmod

    seen = {}

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"candidates": [{"content": {"parts": [
                {"text": "hello"},
                {"text": "world"},
            ]}}]}

    seen_url = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["headers"] = headers
        seen["json"] = json
        seen_url["url"] = url
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = chatmod.ask_gemini("key", "the-model", "sys",
                                [{"role": "user", "content": "hi"}])
    assert out == "helloworld"
    assert "the-model" in seen_url["url"]
    assert seen["headers"]["x-goog-api-key"] == "key"
    assert seen["json"]["system_instruction"]["parts"][0]["text"] == "sys"
    assert seen["json"]["generationConfig"]["maxOutputTokens"] == 1024
