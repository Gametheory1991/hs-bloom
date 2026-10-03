from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from collector.config import load_config
from collector.insights import build_digest
from collector.newsletter import SmtpCfg, deliver_newsletter, load_smtp_cfg
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


def smtp_cfg(**overrides):
    base = SmtpCfg(
        True, "smtp.gmail.com", 465, "sender@gmail.com", "app-secret",
        "sender@gmail.com", "reader@example.com", True, False, "http://192.168.1.25:8080",
    )
    return SmtpCfg(**{**base.__dict__, **overrides})


def test_load_smtp_cfg_reads_expected_env():
    cfg = load_smtp_cfg({
        "SMTP_ENABLED": "1",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "sender@gmail.com",
        "SMTP_PASSWORD": "app-secret",
        "SMTP_TO": "reader@example.com",
        "SMTP_FROM": "",
        "SMTP_USE_SSL": "1",
        "SMTP_STARTTLS": "0",
        "DASHBOARD_URL": "http://192.168.1.25:8080",
    })
    assert cfg.enabled is True
    assert cfg.sender == "sender@gmail.com"
    assert cfg.secret == "app-secret"


def test_load_smtp_cfg_falls_back_to_render_external_url():
    cfg = load_smtp_cfg({
        "SMTP_PORT": "465",
        "RENDER_EXTERNAL_URL": "https://os-bloom.onrender.com",
    })
    assert cfg.dashboard_url == "https://os-bloom.onrender.com"


@pytest.mark.asyncio
async def test_deliver_newsletter_sends_once_per_digest(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    store.upsert_points("idx:SPX", [(date(2026, 1, 1), 100.0), (date(2026, 2, 1), 130.0)])
    insights = build_digest(store, load_config(REPO_ROOT / "config.yaml"))
    store.put_doc("insights", insights, source="local-analysis")
    sent = []

    def fake_send(cfg, digest):
        sent.append((cfg.recipient, digest["digest_id"]))

    monkeypatch.setattr("collector.newsletter._send", fake_send)
    assert await deliver_newsletter(store, smtp_cfg()) == "smtp"
    assert await deliver_newsletter(store, smtp_cfg()) == "smtp-idle"
    assert sent == [("reader@example.com", insights["digest_id"])]
    status = store.doc("newsletter_status").payload
    assert status["state"] == "idle"


@pytest.mark.asyncio
async def test_deliver_newsletter_waits_for_data_then_sends_same_utc_day(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 3, 12, tzinfo=timezone.utc)

    monkeypatch.setattr("collector.newsletter.datetime", Clock)
    store = Store(tmp_path / "t.db")
    cfg = load_config(REPO_ROOT / "config.yaml")
    store.put_doc("insights", build_digest(store, cfg), source="local-analysis")
    sent = []
    monkeypatch.setattr("collector.newsletter._send", lambda cfg, digest: sent.append(digest))

    assert await deliver_newsletter(store, smtp_cfg()) == "smtp-waiting-data"
    status = store.doc("newsletter_status").payload
    assert status["state"] == "waiting_for_data"
    assert status["last_sent_at"] is None
    assert status["last_digest_id"] is None
    assert sent == []

    store.upsert_points("idx:SPX", [(date(2026, 10, 2), 100), (date(2026, 10, 3), 110)])
    populated = build_digest(store, cfg)
    assert populated["newsletter"]["coverage"]["active_series"] == 1
    store.put_doc("insights", populated, source="local-analysis")
    assert await deliver_newsletter(store, smtp_cfg()) == "smtp"
    assert len(sent) == 1
    assert store.doc("newsletter_status").payload["last_sent_at"].startswith("2026-10-03")
    assert await deliver_newsletter(store, smtp_cfg()) == "smtp-idle"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_deliver_newsletter_marks_misconfigured(tmp_path):
    store = Store(tmp_path / "t.db")
    with pytest.raises(ValueError, match="incomplete"):
        await deliver_newsletter(store, smtp_cfg(secret=""))
    status = store.doc("newsletter_status").payload
    assert status["state"] == "misconfigured"
