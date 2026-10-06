"""Built-in usage analytics: visits logging, event beacons, /api/usage stats."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from collector.api import create_app
from collector.config import load_config
from collector.store import Store
from collector.usage import (
    device_of,
    ip_hash,
    should_log,
    usage_stats,
)

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def make_client(tmp_path):
    store = Store(tmp_path / "t.db")
    cfg = load_config(REPO_ROOT / "config.yaml")
    return TestClient(create_app(store, cfg)), store


def _visit(client, ip, path="/", ua="Mozilla/5.0 (iPhone)", ref=""):
    h = {"X-Forwarded-For": ip, "User-Agent": ua}
    if ref:
        h["Referer"] = ref
    return client.get(path, headers=h)


def test_visits_logged_with_hashed_ip(tmp_path):
    client, store = make_client(tmp_path)
    _visit(client, "1.2.3.4")
    rows = store._execute("SELECT path, ip_hash, device, referrer FROM visits")
    assert len(rows) == 1
    assert rows[0][0] == "/"
    assert rows[0][1] != "1.2.3.4"  # hashed, never raw
    assert len(rows[0][1]) == 32
    assert rows[0][2] == "mobile"


def test_skip_rules():
    assert not should_log("/healthz", "Mozilla/5.0")
    assert not should_log("/api/event", "Mozilla/5.0")
    assert not should_log("/api/series/foo?range=1y", "Mozilla/5.0")
    assert not should_log("/ui/js/main.js", "Mozilla/5.0")
    assert not should_log("/", "Googlebot/2.1")
    assert should_log("/", "Mozilla/5.0 (iPhone)")
    assert should_log("/api/dashboard", "Mozilla/5.0")


def test_device_detection():
    assert device_of("Mozilla/5.0 (iPhone; CPU iPhone OS)") == "mobile"
    assert device_of("Mozilla/5.0 (Windows NT 10.0)") == "desktop"


def test_ip_hash_rotates_daily():
    assert ip_hash("1.2.3.4", "2026-10-06") != ip_hash("1.2.3.4", "2026-10-07")
    assert ip_hash("1.2.3.4", "2026-10-06") == ip_hash("1.2.3.4", "2026-10-06")


def _event(client, **kw):
    return client.post("/api/event", json=kw)


def test_event_endpoint_and_stats(tmp_path):
    client, store = make_client(tmp_path)
    # two visitors load the page
    _visit(client, "1.2.3.4", ref="https://x.com/")
    _visit(client, "5.6.7.8")
    # session A: 3 pageviews across hubs (not a bounce)
    for hub, sub in [("pulse", "snapshot"), ("equity", "shortvol"), ("desk", "usage")]:
        r = _event(client, type="pageview", session="sess-A", hub=hub, subtab=sub)
        assert r.status_code == 200
    _event(client, type="hub_click", session="sess-A", hub="equity")
    _event(client, type="subtab_click", session="sess-A", hub="equity", subtab="margin")
    # session B: single pageview (bounce)
    _event(client, type="pageview", session="sess-B", hub="pulse", subtab="snapshot")

    body = client.get("/api/usage?days=30").json()
    k = body["kpis"]
    assert k["unique_visitors"] == 2
    assert k["pageviews"] == 4
    assert k["sessions"] == 2
    assert k["bounce_rate"] == 0.5
    assert k["avg_session_seconds"] >= 0

    hubs = {h["hub"]: h for h in body["hub_clicks"]}
    assert hubs["equity"]["clicks"] == 1
    assert hubs["equity"]["share"] == 1.0

    routes = {r["route"]: r["pageviews"] for r in body["top_routes"]}
    assert routes["#/pulse/snapshot"] == 2
    assert routes["#/equity/shortvol"] == 1

    refs = {r["referrer"]: r["hits"] for r in body["referrers"]}
    assert refs["https://x.com/"] == 1

    devs = {d["device"]: d["visitors"] for d in body["devices"]}
    assert devs["mobile"] == 2  # both test UAs are iPhone

    assert len(body["daily_visitors"]) >= 1
    assert len(body["daily_pageviews"]) >= 1


def test_event_rejects_bad_type(tmp_path):
    client, _ = make_client(tmp_path)
    r = _event(client, type="nope", session="s1")
    assert r.status_code == 400


def test_middleware_never_breaks_response(tmp_path):
    client, _ = make_client(tmp_path)
    # even a bot hit returns the normal response
    r = client.get("/healthz", headers={"User-Agent": "Googlebot"})
    assert r.status_code == 200


def store_count_helper(store):
    return store._execute("SELECT COUNT(*) FROM visits")[0][0]


def test_bot_and_healthz_not_logged(tmp_path):
    client, store = make_client(tmp_path)
    client.get("/healthz", headers={"User-Agent": "Googlebot"})
    client.get("/", headers={"User-Agent": "Googlebot/2.1"})
    assert store_count_helper(store) == 0


def test_usage_stats_empty_store(tmp_path):
    _, store = make_client(tmp_path)
    body = usage_stats(store, 7)
    assert body["kpis"]["unique_visitors"] == 0
    assert body["kpis"]["bounce_rate"] is None
    assert body["daily_visitors"] == []
