from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient

from collector.alerts import process_alerts
from collector.anomalies import (
    DigestSeries, detect_anomalies, detect_series, effective_config, series_catalog,
)
from collector.api import create_app
from collector.config import ALERT_DEFAULTS, load_config, validate_alerting_config
from collector.http import post_webhook
from collector.insights import build_digest, refresh_digest
from collector.newsletter import SmtpCfg, deliver_newsletter
from collector.scheduler import register_jobs
from collector.store import Store

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
ITEM = DigestSeries("SPX", "idx:SPX", "S&P 500", "px")


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.yaml")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "alerts.db")


def readings(values):
    return [(date(2026, 9, 1) + timedelta(days=i), float(v)) for i, v in enumerate(values)]


def seed(store):
    store.upsert_points("idx:SPX", readings([100, 101] * 15 + [110]))


def smtp(enabled=True):
    return SmtpCfg(enabled, "mail.example.test", 465, "sender", "not-a-real-secret",
                   "sender@example.test", "recipient@example.test", True, False, "")


def test_rolling_baseline_excludes_latest_and_obeys_threshold():
    points = readings([1, 2] * 10 + [4])
    settings = {**ALERT_DEFAULTS, "range_enabled": False, "reversal_enabled": False}
    events = detect_series(points, ITEM, settings)
    assert len(events) == 1
    assert events[0]["z_score"] == 5
    assert events[0]["baseline_count"] == 20
    assert events[0]["baseline_mean"] == 1.5
    assert events[0]["baseline_std"] == 0.5
    assert detect_series(points, ITEM, {**settings, "z_threshold": 5.01}) == []
    assert detect_series(points, ITEM, {**settings, "window": 10}) == []


def test_range_detection_historical_and_configured():
    points = readings([1, 2] * 10 + [4])
    settings = {**ALERT_DEFAULTS, "z_threshold": 100, "reversal_enabled": False}
    event, = detect_series(points, ITEM, settings)
    assert event["kind"] == "range"
    assert event["range_max"] == 2.5
    assert event["range_source"] == "historical"
    assert detect_series(points, ITEM, {**settings, "range_margin": 5}) == []
    event, = detect_series(readings([4]), ITEM, {**settings, "range_max": 3})
    assert event["range_source"] == "configured"
    assert event["z_score"] == 0
    assert detect_series(readings([3]), ITEM, {**settings, "range_max": 3}) == []
    down, = detect_series(readings([0]), ITEM, {**settings, "range_min": 1})
    assert down["direction"] == "down"


def test_constant_baselines_hidden_series_and_nonfinite_data(store, cfg):
    seed(store)
    store.upsert_points("cycle:usrec", readings([0] * 25 + [1, float("inf")]))
    events = detect_anomalies(store, cfg)
    assert any(e["series_id"] == "usrec" and e["kind"] == "anomaly" for e in events)
    assert any(s.series_id == "usrec" for s in series_catalog(cfg))
    for event in events:
        assert event["value"] != float("inf")


def test_meaningful_reversal_not_small_noise():
    settings = {**ALERT_DEFAULTS, "z_threshold": 100, "range_enabled": False}
    points = readings([0, 1] * 10 + [0, 3, 6, 9, 12, 15, 12, 9, 6, 3, 0])
    events = detect_series(points, ITEM, settings)
    reversal, = [e for e in events if e["kind"] == "reversal"]
    assert reversal["direction"] == "down"
    assert reversal["previous_slope"] == 3
    assert reversal["recent_slope"] == -3
    assert not detect_series(points, ITEM, {**settings, "reversal_z": 100})
    noise = readings([0, 10] * 10 + [0, .01, .02, .03, .04, .05, .04, .03, .02, .01, 0])
    assert not detect_series(noise, ITEM, settings)


@pytest.mark.parametrize("patch", [
    {"webhook_url": "https://example.test"},
    {"defaults": {"destination": "https://example.test"}},
    {"series": {"unknown": {"enabled": False}}},
    {"defaults": {"z_threshold": float("inf")}},
    {"defaults": {"z_threshold": float("nan")}},
    {"defaults": {"z_threshold": 10 ** 500}},
    {"defaults": {"z_threshold": True}},
    {"defaults": {"z_threshold": 0}},
    {"defaults": {"min_std": 1e-20}},
    {"defaults": {"range_margin": -1}},
    {"defaults": {"window": 3.1}},
    {"defaults": {"window": 5001}},
    {"defaults": {"enabled": "false"}},
    {"defaults": {"min_history": 181}},
    {"defaults": {"range_min": 10, "range_max": 10}},
    {"series": {"SPX": {"range_min": 10, "range_max": 9}}},
    {"defaults": []},
    {"series": {"SPX": []}},
])
def test_config_rejects_invalid_values(patch):
    with pytest.raises(ValueError):
        validate_alerting_config(patch, {"SPX"})


def test_env_defaults_and_series_overrides(cfg, monkeypatch, store):
    monkeypatch.setenv("ALERT_Z_THRESHOLD", "8")
    monkeypatch.setenv("ALERT_RANGE_ENABLED", "false")
    configured = load_config(ROOT / "config.yaml")
    assert configured.alerting_config["defaults"]["z_threshold"] == 8
    assert configured.alerting_config["defaults"]["range_enabled"] is False
    seed(store)
    custom = validate_alerting_config(
        {"defaults": {"z_threshold": 100, "range_enabled": False, "reversal_enabled": False},
         "series": {"SPX": {"z_threshold": 2}}}, {"SPX"})
    store.put_doc("alerting_config", custom, source="test")
    assert any(row["series_id"] == "SPX" for row in build_digest(store, cfg)["alerts"])
    custom = validate_alerting_config({"series": {"SPX": {}}}, {"SPX"}, custom)
    store.put_doc("alerting_config", custom, source="test")
    assert build_digest(store, cfg)["alerts"] == []


def test_scheduler_captures_injected_smtp_config(cfg, store):
    scheduler = AsyncIOScheduler(timezone="UTC")
    injected = smtp(False)

    async def no_network(*args, **kwargs):
        raise AssertionError("no live fetching")

    register_jobs(scheduler, cfg, store, no_network, no_network, no_network, "", injected)
    jobs = {job.id: job for job in scheduler.get_jobs()}
    insights_function = jobs["insights"].func.args[2]
    newsletter_function = jobs["newsletter"].func.args[2]
    assert insights_function.keywords["smtp_cfg"] is injected
    assert newsletter_function.args[1] is injected


def test_config_api_auth_validation_persistence_and_dashboard(cfg, store, monkeypatch):
    client = TestClient(create_app(store, cfg))
    monkeypatch.delenv("ALERT_CONFIG_TOKEN", raising=False)
    assert client.post("/api/alerts/config", json={}).status_code == 503
    monkeypatch.setenv("ALERT_CONFIG_TOKEN", "unit-test-only")
    assert client.post("/api/alerts/config", json={}).status_code == 401
    assert client.post("/api/alerts/config", json={}, headers={"Authorization": "Bearer " + "wrong"}).status_code == 401
    headers = {"Authorization": "Bearer " + "unit-test-only"}
    for payload in ({"webhook_url": "https://example.test"}, {"series": {"unknown": {}}}):
        assert client.post("/api/alerts/config", json=payload, headers=headers).status_code == 422
    seed(store)
    update = {"defaults": {"z_threshold": 100, "range_enabled": False, "reversal_enabled": False}}
    response = client.post("/api/alerts/config", json=update, headers=headers)
    assert response.status_code == 200
    assert response.json()["defaults"]["z_threshold"] == 100
    assert client.get("/api/insights").json()["alerts"] == []
    reopened = Store(store.conn.execute("PRAGMA database_list").fetchone()[2])
    assert effective_config(reopened, cfg)["defaults"]["z_threshold"] == 100
    assert TestClient(create_app(reopened, cfg)).get("/api/alerts/config").json() == response.json()


def test_config_api_rejects_huge_integer_without_persisting(cfg, store, monkeypatch):
    monkeypatch.setenv("ALERT_CONFIG_TOKEN", "unit-test-only")
    client = TestClient(create_app(store, cfg))
    original = client.get("/api/alerts/config").json()
    response = client.post(
        "/api/alerts/config",
        headers={"Authorization": "Bearer " + "unit-test-only"},
        json={"defaults": {"z_threshold": 10 ** 400}},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "z_threshold must be finite and bounded"
    assert store.doc("alerting_config") is None
    assert client.get("/api/alerts/config").json() == original


@pytest.mark.asyncio
async def test_persistent_dedup_retry_isolation_and_no_historical_flood(cfg, store, monkeypatch):
    seed(store)
    events = detect_anomalies(store, cfg)
    sent, posted = [], []
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test/alerts")
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "unit-webhook-token")
    monkeypatch.setattr("collector.alerts._send", lambda cfg, digest: sent.append(digest))

    async def broken(url, json, headers):
        posted.append(json["event"]["event_id"])
        assert headers["Authorization"] == "Bearer " + "unit-webhook-token"
        raise RuntimeError("credentials should never be persisted")

    await process_alerts(store, events, smtp(), broken, now=NOW)
    assert store.alerts()["total"] == len(events) == 2
    assert {row["as_of"] for row in store.alerts()["items"]} == {"2026-10-01"}
    assert len(sent) == len(events)
    assert len(posted) == len(events)
    for row in store.alerts()["items"]:
        state = {d["channel"]: d for d in row["deliveries"]}
        assert state["smtp"]["state"] == "sent"
        assert state["webhook"]["last_error"] == "RuntimeError"
    await process_alerts(store, events, smtp(), broken, now=NOW + timedelta(seconds=30))
    assert len(sent) == len(events)
    assert len(posted) == len(events)
    reopened = Store(store.conn.execute("PRAGMA database_list").fetchone()[2])

    async def succeeds(url, json, headers):
        posted.append(json["event"]["event_id"])
        return {}

    await process_alerts(reopened, events, smtp(), succeeds, now=NOW + timedelta(seconds=61))
    await process_alerts(reopened, events, smtp(), succeeds, now=NOW + timedelta(days=1))
    assert len(sent) == len(events)
    assert len(posted) == 2 * len(events)
    assert all(d["state"] == "sent" for row in reopened.alerts()["items"] for d in row["deliveries"])


@pytest.mark.asyncio
@pytest.mark.parametrize("patch,cancelled_kinds", [
    ({"defaults": {"enabled": False}}, {"anomaly", "range", "reversal"}),
    ({"series": {"SPX": {"enabled": False}}}, {"anomaly", "range", "reversal"}),
    ({"defaults": {"range_enabled": False}}, {"range"}),
    ({"series": {"SPX": {"range_enabled": False}}}, {"range"}),
    ({"defaults": {"reversal_enabled": False}}, {"reversal"}),
    ({"series": {"SPX": {"reversal_enabled": False}}}, {"reversal"}),
])
async def test_disabled_series_and_kinds_cancel_retries_permanently(
    cfg, store, monkeypatch, patch, cancelled_kinds,
):
    seed(store)
    events = detect_anomalies(store, cfg)
    events.append({**events[0], "id": "reversal:SPX", "kind": "reversal"})
    initial = validate_alerting_config({}, {"SPX"})
    disabled = validate_alerting_config(patch, {"SPX"}, initial)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test")
    monkeypatch.setenv("ALERT_EMAIL_ENABLED", "1")

    def broken_email(*args):
        raise RuntimeError("transient failure")

    async def broken_webhook(*args, **kwargs):
        raise RuntimeError("transient failure")

    monkeypatch.setattr("collector.alerts._send", broken_email)
    await process_alerts(store, events, smtp(), broken_webhook, now=NOW, config=initial)
    assert store.alerts()["total"] == 3
    assert all(delivery["state"] == "error"
               for event in store.alerts()["items"] for delivery in event["deliveries"])
    email_sent, webhook_sent = [], []
    monkeypatch.setattr("collector.alerts._send", lambda cfg, digest: email_sent.append(digest))

    async def successful_webhook(url, json, headers):
        webhook_sent.append(json["event"]["kind"])
        return {}

    await process_alerts(store, events, smtp(), successful_webhook,
                         now=NOW + timedelta(seconds=61), config=disabled)
    history = store.alerts()
    assert history["total"] == 3
    for event in history["items"]:
        expected = "cancelled" if event["kind"] in cancelled_kinds else "sent"
        assert all(delivery["state"] == expected for delivery in event["deliveries"])
    expected_sends = 3 - len(cancelled_kinds)
    assert len(email_sent) == len(webhook_sent) == expected_sends
    # Re-enabling the same signals must not resurrect cancelled historical work.
    await process_alerts(store, events, smtp(), successful_webhook,
                         now=NOW + timedelta(days=1), config=initial)
    assert len(email_sent) == len(webhook_sent) == expected_sends


@pytest.mark.asyncio
async def test_config_api_cancels_queued_work_without_removing_audit_history(cfg, store, monkeypatch):
    seed(store)
    monkeypatch.setenv("ALERT_CONFIG_TOKEN", "unit-test-only")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test")

    async def failure(*args, **kwargs):
        raise RuntimeError("transient failure")

    await process_alerts(store, detect_anomalies(store, cfg), smtp(False), failure, now=NOW)
    client = TestClient(create_app(store, cfg))
    headers = {"Authorization": "Bearer " + "unit-test-only"}
    response = client.post("/api/alerts/config", headers=headers,
                           json={"series": {"SPX": {"enabled": False}}})
    assert response.status_code == 200
    history = client.get("/api/anomalies?series_id=SPX").json()
    assert history["total"] == 2
    assert all(delivery["state"] == "cancelled"
               for event in history["items"] for delivery in event["deliveries"])
    assert client.get("/api/insights").json()["alerts"] == []
    # A late completion cannot change a terminal cancellation back to retryable work.
    event_id = history["items"][0]["event_id"]
    store.finish_alert_delivery(event_id, "webhook", now=NOW.isoformat(),
                                retry_at=NOW.isoformat(), error="RuntimeError")
    assert all(delivery["state"] == "cancelled"
               for event in store.alerts()["items"] for delivery in event["deliveries"])


@pytest.mark.asyncio
async def test_scheduled_refresh_applies_persisted_config_to_pending_retries(cfg, store, monkeypatch):
    seed(store)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test")

    async def failure(*args, **kwargs):
        raise RuntimeError("transient failure")

    await process_alerts(store, detect_anomalies(store, cfg), smtp(False), failure, now=NOW)
    disabled = validate_alerting_config({"series": {"SPX": {"enabled": False}}}, {"SPX"})
    store.put_doc("alerting_config", disabled, source="test")
    await refresh_digest(store, cfg, smtp_cfg=smtp(False))
    assert store.alerts()["total"] == 2
    assert all(delivery["state"] == "cancelled"
               for event in store.alerts()["items"] for delivery in event["deliveries"])


@pytest.mark.asyncio
async def test_disabled_channels_dashboard_detection_and_scheduled_refresh(cfg, store, monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    seed(store)
    await refresh_digest(store, cfg, smtp_cfg=smtp(False))
    assert store.alerts()["total"] == 2
    assert store.doc("insights").payload["alerts"]
    assert all(not row["deliveries"] for row in store.alerts()["items"])


@pytest.mark.asyncio
async def test_smtp_failure_does_not_block_webhook_and_errors_are_sanitized(cfg, store, monkeypatch):
    seed(store)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test")
    posted = []

    def broken_email(cfg, digest):
        raise RuntimeError("SMTP credentials must not appear in API status")

    async def webhook(url, json, headers):
        posted.append(json["event"]["event_id"])
        return {}

    monkeypatch.setattr("collector.alerts._send", broken_email)
    await process_alerts(store, detect_anomalies(store, cfg), smtp(), webhook, now=NOW)
    assert len(posted) == 2
    for event in store.alerts()["items"]:
        channels = {row["channel"]: row for row in event["deliveries"]}
        assert channels["smtp"]["last_error"] == "RuntimeError"
        assert channels["webhook"]["state"] == "sent"
    store.put_doc("insights", {"digest_id": "test"}, source="test")
    monkeypatch.setattr("collector.newsletter._send", broken_email)
    with pytest.raises(RuntimeError, match="^newsletter delivery failed$"):
        await deliver_newsletter(store, smtp())
    assert store.doc("newsletter_status").payload["last_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_immediate_and_daily_email_have_independent_env_controls(cfg, store, monkeypatch):
    seed(store)
    events = detect_anomalies(store, cfg)
    alerts_sent, daily_sent, posted = [], [], []
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test")
    monkeypatch.setenv("ALERT_EMAIL_ENABLED", "0")
    monkeypatch.setenv("NEWSLETTER_ENABLED", "1")
    monkeypatch.setattr("collector.alerts._send", lambda cfg, digest: alerts_sent.append(digest))
    monkeypatch.setattr("collector.newsletter._send", lambda cfg, digest: daily_sent.append(digest))

    async def webhook(url, json, headers):
        posted.append(json)
        return {}

    await process_alerts(store, events, smtp(), webhook, now=NOW)
    assert alerts_sent == []
    assert len(posted) == 2
    assert all(row["deliveries"][0]["channel"] == "webhook" for row in store.alerts()["items"])
    store.put_doc("insights", build_digest(store, cfg), source="test")
    assert await deliver_newsletter(store, smtp()) == "smtp"
    assert len(daily_sent) == 1

    monkeypatch.setenv("ALERT_EMAIL_ENABLED", "1")
    monkeypatch.setenv("NEWSLETTER_ENABLED", "0")
    await process_alerts(store, events, smtp(), webhook, now=NOW)
    assert len(alerts_sent) == 2
    assert len(posted) == 2
    assert await deliver_newsletter(store, smtp()) == "smtp-disabled"
    assert store.doc("newsletter_status").payload["enabled"] is False
    assert len(daily_sent) == 1


@pytest.mark.asyncio
async def test_delivery_lease_recovery_and_concurrent_claims(cfg, store, monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.example.test")
    seed(store)
    event = detect_anomalies(store, cfg)[0]
    event["event_id"] = "lease-test"
    iso = lambda now: now.isoformat().replace("+00:00", "Z")
    store.record_alert(event, ["webhook"], iso(NOW))
    claimed = store.claim_alert_deliveries(["webhook"], iso(NOW), iso(NOW + timedelta(minutes=1)))
    assert len(claimed) == 1
    assert store.claim_alert_deliveries(["webhook"], iso(NOW), iso(NOW)) == []
    recovered = store.claim_alert_deliveries(["webhook"], iso(NOW + timedelta(minutes=2)), iso(NOW))
    assert len(recovered) == 1
    assert recovered[0]["attempts"] == 2


@pytest.mark.asyncio
async def test_anomaly_api_bounded_filters_and_daily_digest(cfg, store, monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    seed(store)
    await process_alerts(store, detect_anomalies(store, cfg), smtp(False),
                         now=datetime.now(timezone.utc))
    client = TestClient(create_app(store, cfg))
    result = client.get("/api/anomalies?series_id=SPX&kind=anomaly&since=2026-10-01&until=2026-10-01&limit=1").json()
    assert result["total"] == 1
    assert result["items"][0]["kind"] == "anomaly"
    assert client.get("/api/anomalies?offset=2").json()["items"] == []
    for query in ("limit=0", "limit=201", "offset=-1", "offset=10001", "kind=unknown",
                  "since=2026-10-03&until=2026-10-01", "since=bad"):
        assert client.get("/api/anomalies?" + query).status_code == 422
    assert client.get("/api/anomalies?series_id=unknown").status_code == 404
    digest = client.get("/api/digest").json()
    assert digest["daily_summary"]["timezone"] == "UTC"
    assert digest["daily_summary"]["event_count"] == 2
    assert digest["newsletter"]["headline"]


@pytest.mark.asyncio
async def test_newsletter_daily_utc_dedup_even_if_content_changes(store, monkeypatch):
    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr("collector.newsletter.datetime", Clock)
    sent = []
    monkeypatch.setattr("collector.newsletter._send", lambda cfg, digest: sent.append(digest))
    store.put_doc("insights", {"digest_id": "first"}, source="test")
    assert await deliver_newsletter(store, smtp()) == "smtp"
    store.put_doc("insights", {"digest_id": "second"}, source="test")
    assert await deliver_newsletter(store, smtp()) == "smtp-idle"
    assert len(sent) == 1
    Clock.current += timedelta(days=1)
    assert await deliver_newsletter(store, smtp()) == "smtp"
    assert len(sent) == 2
    # An offset timestamp from yesterday local time can still be today in UTC.
    store.put_doc("newsletter_status", {"last_sent_at": "2026-10-03T23:30:00-02:00"}, source="test")
    assert await deliver_newsletter(store, smtp()) == "smtp-idle"


@pytest.mark.asyncio
async def test_webhook_accepts_empty_2xx_and_rejects_redirects(monkeypatch):
    real_client = httpx.AsyncClient
    status = 204

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"Location": "https://elsewhere.test"})), **kwargs)

    monkeypatch.setattr("collector.http.httpx.AsyncClient", factory)
    assert await post_webhook("https://example.test", {"event": {}}) == {}
    status = 302
    with pytest.raises(RuntimeError, match="webhook delivery failed"):
        await post_webhook("https://example.test", {"event": {}})
