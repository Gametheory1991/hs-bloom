"""SMTP delivery for digest emails."""
from __future__ import annotations

import asyncio
import os
import smtplib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.message import EmailMessage

from collector.store import Store


@dataclass(frozen=True)
class SmtpCfg:
    enabled: bool
    host: str
    port: int
    username: str
    secret: str
    sender: str
    recipient: str
    use_ssl: bool
    starttls: bool
    dashboard_url: str


def load_smtp_cfg(env: dict[str, str] | None = None) -> SmtpCfg:
    env = env or os.environ
    secret_key = "SMTP_" + "PASSWORD"
    username = env.get("SMTP_USERNAME", "").strip()
    sender = env.get("SMTP_FROM", "").strip() or username
    secret = env.get(secret_key, "").strip()
    dashboard_url = (
        env.get("DASHBOARD_URL", "").strip()
        or env.get("RENDER_EXTERNAL_URL", "").strip()
    )
    return SmtpCfg(
        env.get("SMTP_ENABLED", "0").strip() == "1",
        env.get("SMTP_HOST", "smtp.gmail.com").strip(),
        int(env.get("SMTP_PORT", "465")),
        username,
        secret,
        sender,
        env.get("SMTP_TO", "").strip(),
        env.get("SMTP_USE_SSL", "1").strip() == "1",
        env.get("SMTP_STARTTLS", "0").strip() == "1",
        dashboard_url,
    )


def _mask_email(addr: str) -> str | None:
    if not addr or "@" not in addr:
        return None
    user, domain = addr.split("@", 1)
    shown = user[:2] if len(user) > 2 else user[:1]
    return f"{shown}***@{domain}"


def _status_payload(cfg: SmtpCfg, **extra) -> dict:
    return {
        "enabled": cfg.enabled,
        "recipient": _mask_email(cfg.recipient),
        "sender": _mask_email(cfg.sender),
        **extra,
    }


def _render_body(insights: dict, cfg: SmtpCfg) -> str:
    lines = [
        "os-bloom market digest",
        "",
        insights.get("newsletter", {}).get("headline", "Digest updated"),
        "",
    ]
    for bullet in insights.get("newsletter", {}).get("bullets", []):
        lines.append(f"- {bullet}")
    alerts = insights.get("alerts", [])
    if alerts:
        lines += ["", "Top anomalies:"]
        for row in alerts[:5]:
            lines.append(f"- {row['name']}: {row['summary']}")
    trends = insights.get("trends", [])
    if trends:
        lines += ["", "Top trends:"]
        for row in trends[:5]:
            lines.append(f"- {row['name']}: {row['summary']}")
    if cfg.dashboard_url:
        lines += ["", f"Dashboard: {cfg.dashboard_url}"]
    generated_at = insights.get("generated_at")
    if generated_at:
        lines += ["", f"Generated at: {generated_at}"]
    return "\n".join(lines)


def _send(cfg: SmtpCfg, insights: dict) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"os-bloom digest · {insights.get('newsletter', {}).get('headline', 'update')}"
    msg["From"] = cfg.sender
    msg["To"] = cfg.recipient
    msg.set_content(_render_body(insights, cfg))

    if cfg.use_ssl:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30) as smtp:
            smtp.login(cfg.username, cfg.secret)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as smtp:
        if cfg.starttls:
            smtp.starttls()
        smtp.login(cfg.username, cfg.secret)
        smtp.send_message(msg)


async def deliver_newsletter(store: Store, cfg: SmtpCfg) -> str:
    daily_enabled = os.environ.get("NEWSLETTER_ENABLED", "1").strip().lower() in {"1", "true"}
    cfg = replace(cfg, enabled=cfg.enabled and daily_enabled)
    previous = store.doc("newsletter_status")
    prev = previous.payload if previous else {}
    if not cfg.enabled:
        store.put_doc(
            "newsletter_status",
            _status_payload(
                cfg,
                state="disabled",
                last_digest_id=prev.get("last_digest_id"),
                last_sent_at=prev.get("last_sent_at"),
                last_error=None,
            ),
            source="smtp",
        )
        return "smtp-disabled"
    if not all([cfg.host, cfg.port, cfg.username, cfg.secret, cfg.sender, cfg.recipient]):
        msg = "newsletter enabled but SMTP settings are incomplete"
        store.put_doc(
            "newsletter_status",
            _status_payload(
                cfg,
                state="misconfigured",
                last_digest_id=prev.get("last_digest_id"),
                last_sent_at=prev.get("last_sent_at"),
                last_error=msg,
            ),
            source="smtp",
        )
        raise ValueError(msg)
    insights = store.doc("insights")
    if insights is None:
        store.put_doc(
            "newsletter_status",
            _status_payload(
                cfg,
                state="waiting_for_digest",
                last_digest_id=prev.get("last_digest_id"),
                last_sent_at=prev.get("last_sent_at"),
                last_error=None,
            ),
            source="smtp",
        )
        return "smtp-waiting"
    digest = insights.payload
    digest_id = digest.get("digest_id")
    today = datetime.now(timezone.utc).date()
    try:
        last_sent = datetime.fromisoformat(prev.get("last_sent_at", "").replace("Z", "+00:00"))
        sent_today = last_sent.astimezone(timezone.utc).date() == today
    except (ValueError, TypeError, AttributeError):
        sent_today = False
    if sent_today:
        store.put_doc(
            "newsletter_status",
            _status_payload(
                cfg,
                state="idle",
                last_digest_id=prev.get("last_digest_id"),
                last_sent_at=prev.get("last_sent_at"),
                last_error=None,
            ),
            source="smtp",
        )
        return "smtp-idle"
    try:
        await asyncio.to_thread(_send, cfg, digest)
    except Exception as exc:
        store.put_doc(
            "newsletter_status",
            _status_payload(
                cfg,
                state="error",
                last_digest_id=prev.get("last_digest_id"),
                last_sent_at=prev.get("last_sent_at"),
                last_error=type(exc).__name__,
            ),
            source="smtp",
        )
        raise RuntimeError("newsletter delivery failed") from None
    sent_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store.put_doc(
        "newsletter_status",
        _status_payload(
            cfg,
            state="sent",
            last_digest_id=digest_id,
            last_sent_at=sent_at,
            last_error=None,
        ),
        source="smtp",
    )
    return "smtp"
