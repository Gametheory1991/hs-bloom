"""Tests for fetchers/hyperscaler.py — synthetic SEC responses, no network."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from collector.fetchers.hyperscaler import (
    _one_issuer,
    fetch_hyperscaler,
    parse_prospectus,
)


class FakeStore:
    def __init__(self, series=None):
        self.series = series or {}
        self.docs = {}

    def points(self, key, since=None):
        return self.series.get(key, {})

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        if d is None:
            return None
        return type("Doc", (), d)()


PROSPECTUS_HTML = """
<html><body>
<p>We are offering $1,250,000,000 aggregate principal amount of our 4.500%
Senior Notes due 2028 and $2,000,000,000 aggregate principal amount of our
4.625% Notes due 2029.</p>
</body></html>
"""


def test_parse_prospectus_tranches():
    out = parse_prospectus(PROSPECTUS_HTML)
    by_coupon = {t["coupon_pct"]: t for t in out}
    assert by_coupon[4.5]["maturity_year"] == 2028
    assert by_coupon[4.5]["principal_usd"] == 1_250_000_000
    assert by_coupon[4.625]["maturity_year"] == 2029
    assert by_coupon[4.625]["principal_usd"] == 2_000_000_000


def test_parse_prospectus_empty():
    assert parse_prospectus("<html><body>no tranches here</body></html>") == []


def _submissions_json(forms_dates):
    forms = [f for f, _ in forms_dates]
    return json.dumps({
        "name": "Test Inc.",
        "filings": {"recent": {
            "form": forms,
            "filingDate": [d for _, d in forms_dates],
            "accessionNumber": [f"0000000000-26-{i:06d}" for i in range(len(forms))],
        }},
    })


def _fake_get_text(mapping):
    async def _get(url, params=None, headers=None):
        for prefix, body in mapping.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"unexpected URL: {url}")
    return _get


@pytest.mark.asyncio
async def test_one_issuer_parses_424b2():
    acc = "0000000000-26-000000"  # first entry in _submissions_json
    nospace = acc.replace("-", "")
    mapping = {
        "https://data.sec.gov/submissions/CIK0000000001.json":
            _submissions_json([("424B2", "2026-08-07"), ("10-Q", "2026-07-31")]),
        f"https://www.sec.gov/Archives/edgar/data/1/{nospace}index.json":
            json.dumps({"directory": {"item": [{"name": "d123d424b2.htm"},
                                               {"name": "d123-index.html"}]}}),
        f"https://www.sec.gov/Archives/edgar/data/1/{nospace}d123d424b2.htm":
            PROSPECTUS_HTML,
    }
    out = await _one_issuer("TestCo", "0000000001", "test-ua",
                            _fake_get_text(mapping), date(2026, 10, 3) - timedelta(days=90))
    assert len(out) == 1
    e = out[0]
    assert e["form"] == "424B2" and e["parsed"] == "yes"
    assert e["tranches"][0]["coupon_pct"] == 4.5
    assert e["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/")


@pytest.mark.asyncio
async def test_one_issuer_old_filings_skipped():
    mapping = {
        "https://data.sec.gov/submissions/CIK0000000001.json":
            _submissions_json([("424B2", "2025-01-01")]),
    }
    out = await _one_issuer("TestCo", "0000000001", "test-ua",
                            _fake_get_text(mapping), date(2026, 10, 3) - timedelta(days=90))
    assert out == []


@pytest.mark.asyncio
async def test_one_issuer_submissions_failure_degrades():
    async def _boom(url, params=None, headers=None):
        raise RuntimeError("HTTP 403")
    out = await _one_issuer("TestCo", "0000000001", "test-ua", _boom,
                            date(2026, 10, 3))
    assert out == []


class FakeCfg:
    class thirteenf:
        user_agent = "test-ua"


@pytest.mark.asyncio
async def test_fetch_hyperscaler_end_to_end():
    start = date(2026, 1, 1)
    googl = {start + timedelta(days=i): 150.0 + i * 0.1 for i in range(120)}
    mapping = {
        "https://data.sec.gov/submissions/": _submissions_json([("10-Q", "2026-09-01")]),
    }
    store = FakeStore({"cycle:googl": googl})
    assert await fetch_hyperscaler(FakeCfg(), store, _fake_get_text(mapping)) == "hyperscaler-weekly"
    doc = store.doc("hyper")
    assert doc is not None
    p = doc.payload
    assert p["issuances"] == []
    googl_card = next(e for e in p["equities"] if e["ticker"] == "GOOGL")
    assert googl_card["last"] is not None and googl_card["chg_1m_pct"] is not None
    assert len(googl_card["spark"]) > 0
    assert "short interest" in p["note"]
