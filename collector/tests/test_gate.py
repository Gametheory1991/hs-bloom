"""Tests for collector.gate (passphrase login gate).

Uses a throwaway FastAPI app — never imports the real collector app.
Fixture credentials are test-only values, never a real passphrase.
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from collector import gate
from collector.gate import GateMiddleware, add_login_routes, make_auth_cookie

TEST_PASSPHRASE = "test-passphrase-only"
TEST_SECRET = "test-secret-only"


@pytest.fixture()
def env_locked(monkeypatch):
    """Env with a passphrase + cookie secret configured; rate-limit cleared."""
    monkeypatch.setenv("SITE_PASSPHRASE", TEST_PASSPHRASE)
    monkeypatch.setenv("SITE_COOKIE_SECRET", TEST_SECRET)
    monkeypatch.delenv("SITE_GATE_ENABLED", raising=False)
    gate._attempts.clear()
    yield


@pytest.fixture()
def env_open(monkeypatch):
    """Env with no passphrase (gate must fail open); rate-limit cleared."""
    monkeypatch.delenv("SITE_PASSPHRASE", raising=False)
    monkeypatch.delenv("SITE_COOKIE_SECRET", raising=False)
    monkeypatch.delenv("SITE_GATE_ENABLED", raising=False)
    gate._attempts.clear()
    yield


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def index():
        return {"ok": True}

    @app.get("/api/stress/matrix")
    def matrix():
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/health")
    def api_health():
        return {"ok": True}

    app.add_middleware(GateMiddleware)
    add_login_routes(app)
    return app


def test_gate_open_when_no_passphrase(env_open):
    client = TestClient(build_app())
    resp = client.get("/api/stress/matrix", follow_redirects=False)
    assert resp.status_code == 200


def test_kill_switch_opens_gate(monkeypatch):
    monkeypatch.setenv("SITE_PASSPHRASE", TEST_PASSPHRASE)
    monkeypatch.setenv("SITE_COOKIE_SECRET", TEST_SECRET)
    monkeypatch.setenv("SITE_GATE_ENABLED", "false")
    gate._attempts.clear()
    client = TestClient(build_app())
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/api/stress/matrix", follow_redirects=False).status_code == 200


def test_locked_gate_redirects_and_401s(env_locked):
    client = TestClient(build_app())
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")
    resp = client.get("/api/stress/matrix", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "authentication required"}


def test_health_endpoints_exempt(env_locked):
    client = TestClient(build_app())
    assert client.get("/healthz", follow_redirects=False).status_code == 200
    assert client.get("/api/health", follow_redirects=False).status_code == 200
    # login page itself must be reachable (no redirect loop)
    assert client.get("/login", follow_redirects=False).status_code == 200


def test_wrong_passphrase_shows_generic_error(env_locked):
    client = TestClient(build_app())
    resp = client.post("/login", data={"passphrase": "nope"})
    assert resp.status_code == 200
    assert "Incorrect passphrase." in resp.text
    assert "set-cookie" not in {k.lower() for k in resp.headers}
    # no hints leak into the page
    assert "test-passphrase" not in resp.text


def _cookie_value_from(resp) -> str:
    raw = resp.headers["set-cookie"]
    return raw.split(";", 1)[0].split("=", 1)[1]


def test_correct_passphrase_sets_cookie_and_grants_access(env_locked):
    client = TestClient(build_app())
    resp = client.post(
        "/login",
        data={"passphrase": TEST_PASSPHRASE, "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" in set_cookie
    cookie = _cookie_value_from(resp)
    authed = client.get("/", headers={"Cookie": f"osb_auth={cookie}"})
    assert authed.status_code == 200
    api = client.get(
        "/api/stress/matrix", headers={"Cookie": f"osb_auth={cookie}"}
    )
    assert api.status_code == 200


def test_bearer_header_bypass(env_locked):
    client = TestClient(build_app())
    resp = client.get(
        "/api/stress/matrix",
        headers={"Authorization": f"Bearer {TEST_PASSPHRASE}"},
    )
    assert resp.status_code == 200
    bad = client.get(
        "/api/stress/matrix",
        headers={"Authorization": "Bearer wrong"},
        follow_redirects=False,
    )
    assert bad.status_code == 401


def test_query_token_bypass(env_locked):
    client = TestClient(build_app())
    assert client.get(f"/?token={TEST_PASSPHRASE}").status_code == 200
    bad = client.get("/?token=wrong", follow_redirects=False)
    assert bad.status_code == 302


def test_rate_limit_blocks_11th_attempt(env_locked):
    client = TestClient(build_app())
    for _ in range(10):
        resp = client.post("/login", data={"passphrase": "wrong"})
        assert resp.status_code == 200
    resp = client.post("/login", data={"passphrase": "wrong"})
    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("too many login attempts")


def test_tampered_cookie_rejected(env_locked):
    client = TestClient(build_app())
    expiry = int(time.time()) + 3600
    good = make_auth_cookie(expiry, TEST_SECRET)
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
    resp = client.get(
        "/", headers={"Cookie": f"osb_auth={tampered}"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


def test_expired_cookie_rejected(env_locked):
    client = TestClient(build_app())
    expired = make_auth_cookie(int(time.time()) - 10, TEST_SECRET)
    resp = client.get(
        "/", headers={"Cookie": f"osb_auth={expired}"}, follow_redirects=False
    )
    assert resp.status_code == 302


def test_wrong_secret_cookie_rejected(env_locked):
    client = TestClient(build_app())
    forged = make_auth_cookie(int(time.time()) + 3600, "attacker-secret")
    resp = client.get(
        "/", headers={"Cookie": f"osb_auth={forged}"}, follow_redirects=False
    )
    assert resp.status_code == 302
