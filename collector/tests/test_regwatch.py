"""REG WATCH fetcher tests: topic tagging, feed isolation, summary fallback."""
import os

import pytest

from collector.fetchers import regwatch
from collector.fetchers.regwatch import (
    _date_from_text,
    _entry_time,
    fetch_regwatch,
    tag_topics,
)
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

FR_NOTICE_JSON = """{"count": 1, "results": [{"title": "Joint Industry Plan; Notice of Filing and Immediate Effectiveness of Amendment to the Nasdaq UTP Plan To Extend SIP Operating Hours",
"type": "Notice", "publication_date": "2026-05-22",
"comments_close_on": null, "html_url": "https://x.example.gov/n",
"abstract": "extend the operating hours of the securities information processors"}]}"""


def test_tag_topics_munis_regulation():
    assert "munis-regulation" in tag_topics(
        "Self-Regulatory Organizations; Municipal Securities Rulemaking Board; "
        "Order Granting Approval of Proposed Rule Change", "")
    assert "munis-regulation" in tag_topics(
        "SEC approves MSRB amendments to muni continuing disclosure", "")
    assert "munis-regulation" in tag_topics(
        "Rule 15c2-12 muni disclosure obligations updated", "")
    assert "munis-regulation" in tag_topics(
        "EMMA filing requirements for municipal advisors", "")
    # no false positives on ordinary items
    assert "munis-regulation" not in tag_topics("FDIC Issues CRA Examination Schedules", "")
    assert "munis-regulation" not in tag_topics("SEC Proposes Treasury Clearing Mandate", "")


def test_tag_topics_extended_hours_and_trf():
    # Harry's May 2026 TRF hours notice must tag correctly
    title = "Extension of TRF Operating Hours"
    desc = ("In alignment with the proposed CTA and UTP Plan amendments to extend "
            "the operating hours of the Securities Information Processors, FINRA's "
            "Trade Reporting Facilities (TRFs) will be extending their operating "
            "hours starting December 6th, 2026.")
    topics = tag_topics(title, desc)
    assert "extended-hours" in topics
    assert "trf" in topics
    # 24X National Exchange 24/7-trading relief
    assert "extended-hours" in tag_topics(
        "Order Granting Temporary Conditional Exemptive Relief to 24X National Exchange", "")
    # CTA plan amendment
    assert "trf" in tag_topics(
        "Consolidated Tape Association; Order Approving the Fortieth Substantive Amendment", "")
    assert "extended-hours" in tag_topics(
        "Joint Industry Plan; Notice of Filing of Amendment to Extend SIP Operating Hours", "")
    # no false positives on ordinary items
    assert "extended-hours" not in tag_topics("FDIC Issues CRA Examination Schedules", "")
    assert "extended-hours" not in tag_topics("SEC Proposes Treasury Clearing Mandate", "")
    # "trf" substring must not fire on unrelated words
    assert "trf" not in tag_topics("SEC Proposes Treasury Clearing Mandate", "")


def test_tag_topics_keywords():
    assert "treasury-clearing" in tag_topics("Treasury Clearing Mandate Finalized", "")
    assert "genius-tokenization" in tag_topics("GENIUS Act stablecoin rules", "")
    assert "basel-iii" in tag_topics("Basel III endgame capital requirements", "")
    assert tag_topics("FDIC Issues CRA Examination Schedules", "") == []


def test_tag_topics_case_insensitive():
    assert "slr-leverage" in tag_topics("Agencies Ease Supplementary Leverage Ratio", "")


def test_tag_topics_binary_options_and_intl():
    assert "binary-options" in tag_topics("Nadex lists new binary option contracts", "")
    assert "binary-options" in tag_topics("ESMA extends binary options ban", "")
    assert "mifid-transparency" in tag_topics("MiFID II: ESMA makes new bond liquidity data available", "")
    assert "mifid-transparency" in tag_topics("EU consolidated tape for bonds", "")
    assert "uk-bond-transparency" in tag_topics("FCA consults on gilt transparency reform", "")
    assert "ediphy-tape" in tag_topics("Ediphy FairCT selected as EU consolidated tape provider", "")
    # no false positives on ordinary items
    assert "mifid-transparency" not in tag_topics("FDIC Issues CRA Examination Schedules", "")
    assert "binary-options" not in tag_topics("SEC Proposes Treasury Clearing Mandate", "")


def test_date_from_text_numeric():
    assert _date_from_text("Election Notice – 6/8/2026") == "2026-06-08T00:00:00Z"
    assert _date_from_text("Notice 1/15/26") == "2026-01-15T00:00:00Z"
    assert _date_from_text("no date here") is None
    assert _date_from_text(None) is None
    assert _date_from_text("13/45/2026") is None  # invalid month/day rejected


def test_date_from_text_month_name():
    assert _date_from_text("Tuesday, October 6, 2026 - 10:50") == "2026-10-06T00:00:00Z"
    assert _date_from_text("Published Jun 17, 2026") == "2026-06-17T00:00:00Z"
    assert _date_from_text("May 2026 update") is None  # month+year only: no match


class _E(dict):
    """feedparser-like entry stub."""


def test_entry_time_prefers_parsed():
    e = _E(published_parsed=(2026, 10, 5, 12, 0, 0, 0, 0, 0))
    assert _entry_time(e, "Election Notice – 6/8/2026").startswith("2026-10-05T12:00:00")


def test_entry_time_recovers_from_raw_string():
    e = _E(published="Tuesday, October 6, 2026 - 10:50")
    assert _entry_time(e, "FCA opens the gateway") == "2026-10-06T00:00:00Z"


def test_entry_time_recovers_from_title():
    e = _E()
    assert _entry_time(e, "Election Notice – 6/8/2026") == "2026-06-08T00:00:00Z"


def test_entry_time_none_when_no_date():
    # honest null — never a fake fetch-time stamp
    assert _entry_time(_E(), "ESMA sets 2027 priorities") is None


async def fake_get_text(url, params=None, headers=None):
    if "dead.example" in url:
        raise RuntimeError("connection refused")
    if "federalregister" in url:
        types = [v for k, v in (params or []) if k == "conditions[type][]"]
        if "NOTICE" in types:
            return FR_NOTICE_JSON
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
    assert len(doc["rules"]) == 4  # 3 FR RULE/PRORULE (sec/cftc/fed) + 1 SRO notice (deduped across 2 terms)
    assert {r["agency"] for r in doc["rules"]} == {"sec", "cftc", "fed"}
    assert doc["rules"][0]["comments_close_on"] == "2026-12-01"
    assert "genius-tokenization" in doc["rules"][0]["topics"]
    sro = [r for r in doc["rules"] if r["type"] == "notice"]
    assert len(sro) == 1
    assert "extended-hours" in sro[0]["topics"]
    assert "trf" in sro[0]["topics"]
    assert set(doc["topics"]) == set(regwatch.REGWATCH_TOPICS)
    assert doc["topics"]["treasury-clearing"]["count"] == 1


async def test_fetch_regwatch_all_dead_raises(tmp_path):
    async def dead(url, params=None, headers=None):
        raise RuntimeError("nope")
    store = Store(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match="all regwatch feeds failed"):
        await fetch_regwatch(store, dead)
