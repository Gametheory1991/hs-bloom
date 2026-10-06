"""REG WATCH fetcher tests: topic tagging, feed isolation, summary fallback."""
import os

import pytest

from collector.fetchers import regwatch
from collector.fetchers.regwatch import fetch_regwatch, tag_topics
from collector.store import Store

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>SEC Proposes Treasury Clearing Mandate for Dealers</title>
<link>https://x.example.gov/1</link>
<description>Central clearing of Treasury securities</description>
<pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>FDIC Issues CRA Examination Schedules</title>
<link>https://x.example.gov/2</link>
<description>Routine examination schedules</description>
<pubDate>Mon, 05 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""

FR_JSON = """{"count": 1, "results": [{"title": "Test Proposed Rule on Stablecoins",
"type": "Proposed Rule", "publication_date": "2026-10-01",
"comments_close_on": "2026-12-01", "html_url": "https://x.example.gov/r",
"abstract": "payment stablecoin framework"}]}"""


def test_tag_topics_keywords():
    assert "treasury-clearing" in tag_topics("Treasury Clearing Mandate Finalized", "")
    assert "genius-tokenization" in tag_topics("GENIUS Act stablecoin rules", "")
    assert "basel-iii" in tag_topics("Basel III endgame capital requirements", "")
    assert tag_topics("FDIC Issues CRA Examination Schedules", "") == []


def test_tag_topics_case_insensitive():
    assert "slr-leverage" in tag_topics("Agencies Ease Supplementary Leverage Ratio", "")


async def fake_get_text(url, params=None, headers=None):
    if "dead.example" in url:
        raise RuntimeError("connection refused")
    if "federalregister" in url:
        return FR_JSON
    return RSS


async def test_fetch_regwatch_isolates_dead_feeds(tmp_path, monkeypatch):
    monkeypatch.setattr(regwatch, "REGWATCH_FEEDS", [
        ("sec", "SEC", "https://ok.example.gov/rss"),
        ("dead", "Dead", "https://dead.example.gov/rss"),
    ])
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")
    label = await fetch_regwatch(store, fake_get_text)
    assert "1/2 feeds" in label
    doc = store.doc("regwatch").payload
    assert len(doc["items"]) == 2  # both items, newest first
    assert doc["items"][0]["agency"] == "sec"
    assert "treasury-clearing" in doc["items"][0]["topics"]
    assert doc["items"][0]["summary"]  # fallback summary present
    assert doc["feed_status"]["dead"].startswith("error")
    assert len(doc["rules"]) == 3  # one per FR agency (sec/cftc/fed), same fake payload
    assert {r["agency"] for r in doc["rules"]} == {"sec", "cftc", "fed"}
    assert doc["rules"][0]["comments_close_on"] == "2026-12-01"
    assert "genius-tokenization" in doc["rules"][0]["topics"]
    assert set(doc["topics"]) == set(regwatch.REGWATCH_TOPICS)
    assert doc["topics"]["treasury-clearing"]["count"] == 1


async def test_fetch_regwatch_all_dead_raises(tmp_path):
    async def dead(url, params=None, headers=None):
        raise RuntimeError("nope")
    store = Store(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match="all regwatch feeds failed"):
        await fetch_regwatch(store, dead)
