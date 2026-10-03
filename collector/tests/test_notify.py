"""Tests for collector.notify (Telegram push alerts).

HTTP is never touched: the codebase convention is to inject a fake
``post_json`` callable (see collector.http: "injected into fetchers so tests
never touch the network"). One test patches the default
``collector.http.post_json`` path and verifies via a real httpx.Request that
the payload goes out as application/json.
"""
from __future__ import annotations

import os
import unittest

import httpx

from collector import http as _http
from collector import notify
from collector.store import Store

TOKEN = "TESTTOKEN123"
CHAT_ID = "999999"


class CapturingPost:
    """Fake post_json: records (url, json_payload) calls, returns Telegram's shape."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, url: str, json: dict) -> dict:
        self.calls.append((url, json))
        return {"ok": True, "result": {}}


def make_store(alerts=(), trends=()) -> Store:
    store = Store(":memory:")
    store.put_doc(
        "insights",
        {
            "alerts": list(alerts),
            "trends": list(trends),
        },
        "test",
    )
    return store


def alert(i: int, name="US 10Y spike") -> dict:
    return {
        "id": f"alert-{i}",
        "name": name,
        "summary": "10Y up 12bp on strong payrolls",
        "as_of": "2026-10-03",
    }


class EnvMixin:
    def set_env(self):
        self._saved = {k: os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        os.environ["TELEGRAM_BOT_TOKEN"] = TOKEN
        os.environ["TELEGRAM_CHAT_ID"] = CHAT_ID

    def clear_env(self):
        self._saved = {k: os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)

    def restore_env(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class NoopTest(unittest.IsolatedAsyncioTestCase, EnvMixin):
    """(a) Not configured -> silent no-op: no HTTP call, no exception."""

    def setUp(self):
        self.clear_env()

    def tearDown(self):
        self.restore_env()

    async def test_send_telegram_noop_without_env(self):
        post = CapturingPost()
        self.assertFalse(await notify.send_telegram("hello", post))
        self.assertEqual(post.calls, [])

    async def test_push_new_alerts_noop_without_env(self):
        store = make_store(alerts=[alert(1)])
        post = CapturingPost()
        self.assertEqual(await notify.push_new_alerts(store, post), 0)
        self.assertEqual(post.calls, [])


class JsonBodyTest(unittest.IsolatedAsyncioTestCase, EnvMixin):
    """(b) The sendMessage POST is application/json with chat_id + text."""

    def setUp(self):
        self.set_env()

    def tearDown(self):
        self.restore_env()

    async def test_post_uses_json_body(self):
        post = CapturingPost()
        await notify.send_telegram("hello world", post)
        self.assertEqual(len(post.calls), 1)
        url, payload = post.calls[0]
        self.assertEqual(url, f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual(payload, {"chat_id": CHAT_ID, "text": "hello world"})
        # What httpx actually puts on the wire for json= must be JSON.
        req = httpx.Request("POST", url, json=payload)
        self.assertEqual(req.headers["Content-Type"], "application/json")

    async def test_default_path_also_sends_json(self):
        """Without an injected fake, the real _http.post_json path must use json=."""
        calls = []

        async def fake(url: str, json=None, **kw):
            calls.append((url, json, kw))
            req = httpx.Request("POST", url, json=json)
            assert req.headers["Content-Type"] == "application/json", (
                "post_json must build an application/json request"
            )
            return {"ok": True}

        real = _http.post_json
        _http.post_json = fake
        try:
            self.assertTrue(await notify.send_telegram("ping"))
        finally:
            _http.post_json = real
        self.assertEqual(len(calls), 1)
        url, payload, kw = calls[0]
        self.assertNotIn("data", kw)
        self.assertEqual(payload, {"chat_id": CHAT_ID, "text": "ping"})

    async def test_terse_alert_format(self):
        store = make_store(alerts=[alert(1)])
        store.put_doc(notify.SENT_DOC, {"ids": []}, "test")  # skip first-run baseline
        post = CapturingPost()
        n = await notify.push_new_alerts(store, post)
        self.assertEqual(n, 1)
        _, payload = post.calls[0]
        self.assertEqual(
            payload["text"],
            "ALERT: US 10Y spike — 10Y up 12bp on strong payrolls (as of 2026-10-03)",
        )
        self.assertEqual(payload["chat_id"], CHAT_ID)


class DedupeTest(unittest.IsolatedAsyncioTestCase, EnvMixin):
    """(c) Baseline first run, send only new ids on second, dedupe repeats."""

    def setUp(self):
        self.set_env()

    def tearDown(self):
        self.restore_env()

    async def test_baseline_then_new_then_dedupe(self):
        store = make_store(alerts=[alert(1), alert(2)])
        post = CapturingPost()
        # First run: baselines, sends nothing.
        self.assertEqual(await notify.push_new_alerts(store, post), 0)
        self.assertEqual(post.calls, [])

        # Second run with one new alert: sends only the new one.
        doc = store.doc("insights")
        payload = dict(doc.payload)
        payload["alerts"] = payload["alerts"] + [alert(3, name="NEW copper breakdown")]
        store.put_doc("insights", payload, "test")
        self.assertEqual(await notify.push_new_alerts(store, post), 1)
        self.assertEqual(len(post.calls), 1)
        self.assertIn("NEW copper breakdown", post.calls[0][1]["text"])

        # Third run with no changes: dedupe, nothing sent.
        self.assertEqual(await notify.push_new_alerts(store, post), 0)
        self.assertEqual(len(post.calls), 1)

    async def test_no_insights_doc_is_noop(self):
        store = Store(":memory:")
        self.assertEqual(await notify.push_new_alerts(store, CapturingPost()), 0)


class SummaryCollapseTest(unittest.IsolatedAsyncioTestCase, EnvMixin):
    """(d) More than 8 new alerts collapses to a single summary message."""

    def setUp(self):
        self.set_env()

    def tearDown(self):
        self.restore_env()

    async def test_many_new_alerts_single_summary(self):
        alerts = [alert(i) for i in range(10)]
        store = make_store(alerts=alerts)
        store.put_doc(notify.SENT_DOC, {"ids": []}, "test")  # skip first-run baseline
        post = CapturingPost()
        n = await notify.push_new_alerts(store, post)
        self.assertEqual(n, 1)
        self.assertEqual(len(post.calls), 1)
        text = post.calls[0][1]["text"]
        self.assertTrue(text.startswith("10 new signals:\n"), text)
        self.assertIn("(+2 more in the terminal)", text)


if __name__ == "__main__":
    unittest.main()
