"""Persistent alert outbox; destinations and credentials are environment-only."""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from collector.anomalies import detect_anomalies, effective_config, thresholds
from collector.config import Config, validate_alerting_config
from collector.http import PostJson, post_webhook
from collector.newsletter import SmtpCfg, _send, load_smtp_cfg
from collector.store import Store


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _webhook_url() -> str:
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
    except ValueError:
        return ""
    return url


async def process_alerts(store: Store, events: list[dict], smtp_cfg: SmtpCfg | None = None,
                         post_json: PostJson | None = None, now: datetime | None = None,
                         config: dict | None = None, cfg: Config | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    smtp_cfg = smtp_cfg or load_smtp_cfg()
    webhook = _webhook_url()
    email_enabled = os.environ.get("ALERT_EMAIL_ENABLED", "1").strip().lower() in {"1", "true"}
    channels = (["smtp"] if smtp_cfg.enabled and email_enabled else []) + (["webhook"] if webhook else [])
    with store.alert_lock:
        if cfg is not None:
            from collector.insights import build_digest

            config = effective_config(store, cfg)
            events = detect_anomalies(store, cfg, config)
            store.put_doc("insights", build_digest(store, cfg, config=config, anomalies=events),
                          source="local-analysis")
        else:
            saved = store.doc("alerting_config")
            if saved is not None:
                raw = saved.payload
                ids = {event["series_id"] for event in events}
                if isinstance(raw, dict) and isinstance(raw.get("series"), dict):
                    ids.update(raw["series"])
                try:
                    config = validate_alerting_config(raw, ids)
                except (ValueError, TypeError):
                    pass
        for row in events:
            if config is not None:
                settings = thresholds(config, row["series_id"])
                if (not settings["enabled"]
                        or row["kind"] == "range" and not settings["range_enabled"]
                        or row["kind"] == "reversal" and not settings["reversal_enabled"]):
                    continue
            # Threshold changes, revisions and restarts do not resend the same
            # series/date/type/direction event. Only latest readings are examined.
            key = [row["store_id"], row["as_of"], row["kind"], row["direction"]]
            event = {**row, "event_id": hashlib.sha256("|".join(key).encode()).hexdigest()[:32]}
            store.record_alert(event, channels, _iso(now))

        if config is not None:
            store.cancel_disabled_alert_deliveries(config)
        deliveries = store.claim_alert_deliveries(channels, _iso(now), _iso(now + timedelta(minutes=10)))

    async def deliver(delivery: dict) -> None:
        event, channel = delivery["event"], delivery["channel"]
        with store.alert_lock:
            if not store.alert_delivery_active(event["event_id"], channel):
                return
        error = None
        try:
            if channel == "smtp":
                if not all((smtp_cfg.host, smtp_cfg.port, smtp_cfg.username, smtp_cfg.secret,
                            smtp_cfg.sender, smtp_cfg.recipient)):
                    raise ValueError("incomplete SMTP settings")
                await asyncio.to_thread(_send, smtp_cfg, {
                    "newsletter": {"headline": f"Alert: {event['name']} — {event['kind']}",
                                   "bullets": [event["summary"]]},
                    "alerts": [event], "generated_at": _iso(now),
                })
            else:
                headers = {"Idempotency-Key": event["event_id"]}
                token = os.environ.get("ALERT_WEBHOOK_TOKEN", "").strip()
                if token:
                    headers["Authorization"] = "Bearer " + token
                await (post_json or post_webhook)(webhook, json={"event": event}, headers=headers)
        except Exception as exc:
            # Exception messages from transports may contain credentials/URLs.
            error = type(exc).__name__
        retry = now + timedelta(seconds=min(3600, 60 * 2 ** min(delivery["attempts"] - 1, 6)))
        store.finish_alert_delivery(event["event_id"], channel, now=_iso(now),
                                    retry_at=_iso(retry), error=error)

    # Bounded concurrency also keeps one slow/broken channel from blocking the other.
    for start in range(0, len(deliveries), 10):
        await asyncio.gather(*(deliver(row) for row in deliveries[start:start + 10]))
