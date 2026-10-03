"""Telegram push alerts for new insights-digest anomalies/trends.

Opt-in via env: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID. When either is
unset every function here is a silent no-op, so deploys never break.
Sent alert ids are tracked in the store (doc "telegram_sent"); the first
run only baselines the current digest without sending (avoids a burst).
"""
from __future__ import annotations

import logging
import os

from collector import http as _http
from collector.alert_config import is_muted
from collector.config import Config
from collector.store import Store

log = logging.getLogger(__name__)

SENT_DOC = "telegram_sent"
MAX_PER_DIGEST = 8  # more than this -> one summary message instead of a flood

# digest kind -> alert_config type id
KIND_TO_TYPE = {"ALERT": "anomaly", "TREND": "trend"}


def _creds() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return token, chat_id
    return None


async def send_telegram(text: str, post_json=None) -> bool:
    """Send one Telegram message. Returns False (no-op) when not configured.

    MUST POST application/json. Verified 2026-10-03: a form-urlencoded
    POST to api.telegram.org fails from this network (HTTP 000) while the
    equivalent JSON POST delivers. httpx sets the Content-Type itself when
    the ``json=`` kwarg is used; never pass form ``data=`` here.
    """
    creds = _creds()
    if creds is None:
        return False
    token, chat_id = creds
    post = post_json or _http.post_json
    # json= (not data=): Telegram only receives the message on this network
    # when the body is application/json. See docstring above.
    await post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
    )
    return True


def _format(kind: str, item: dict) -> str:
    return f"{kind}: {item.get('name')} — {item.get('summary')} (as of {item.get('as_of')})"


async def push_new_alerts(store: Store, post_json=None) -> int:
    """Push digest alerts/trends not yet sent. Returns messages sent."""
    doc = store.doc("insights")
    if doc is None:
        return 0
    items = [("ALERT", a) for a in doc.payload.get("alerts", []) if a.get("id")]
    items += [("TREND", t) for t in doc.payload.get("trends", []) if t.get("id")]
    sent_doc = store.doc(SENT_DOC)
    sent = set(sent_doc.payload.get("ids", [])) if sent_doc else set()
    current_ids = {a["id"] for _, a in items}
    if sent_doc is None:
        # First run: baseline the current digest, don't send a burst.
        store.put_doc(SENT_DOC, {"ids": sorted(current_ids)}, "telegram")
        return 0
    new = [(k, a) for k, a in items if a["id"] not in sent]
    # per-type tuning: muted types never send (still counted as seen so they
    # don't queue up while muted)
    new = [(k, a) for k, a in new if not is_muted(store, KIND_TO_TYPE.get(k, k))]
    if not new:
        store.put_doc(SENT_DOC, {"ids": sorted(sent | current_ids)}, "telegram")
        return 0
    if _creds() is None:
        # Not configured: advance the baseline so we don't queue forever.
        store.put_doc(SENT_DOC, {"ids": sorted(sent | current_ids)}, "telegram")
        return 0
    n = 0
    try:
        if len(new) > MAX_PER_DIGEST:
            lines = "\n".join(f"- {_format(k, a)}" for k, a in new[:MAX_PER_DIGEST])
            await send_telegram(
                f"{len(new)} new signals:\n{lines}\n(+{len(new) - MAX_PER_DIGEST} more in the terminal)",
                post_json,
            )
            n = 1
        else:
            for kind, item in new:
                await send_telegram(_format(kind, item), post_json)
                n += 1
    except Exception as exc:  # noqa: BLE001 — telegram must never break the digest job
        log.warning("telegram push failed: %s", exc)
        return n
    store.put_doc(SENT_DOC, {"ids": sorted(sent | current_ids)}, "telegram")
    return n


async def refresh_digest_and_notify(
    store: Store, cfg: Config, post_json=None
) -> str:
    """Rebuild the insights digest, then push any new alerts to Telegram."""
    from collector.insights import refresh_digest

    source = await refresh_digest(store, cfg)
    try:
        await push_new_alerts(store, post_json)
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram notify failed: %s", exc)
    return source
