"""Tests for the FINRA ATS Transparency fetcher.

Inline samples shaped like the real sources — no network. The keyless
blocksSummary path runs fully offline via injected fakes; the keyed
(OAuth2) path is tested through the token cache and graceful-skip logic
with a stubbed fetch_token.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date

import pytest

from collector.fetchers import finra_ats
from collector.fetchers.finra_ats import (
    _basic,
    _store_blocks_month,
    _store_weekly_firm,
    _store_weekly_sf,
    _store_weekly_symbols,
    fetch_finra_ats,
    fetch_token,
    norm_blocks_row,
    norm_firm_row,
    norm_sf_row,
    norm_symbol_row,
)
from collector.store import Store


BLOCKS_HEADER = (
    "MPID|marketParticipantName|summaryStartDate|totalTradeCount|"
    "totalShareQuantity|averageTradeSize|ATSSharePercent|ATSTradePercent|"
    "ATSBlockCount|ATSBlockQuantity|monthStartDate|lastUpdateDate"
)


def _blocks_text(rows: list[tuple]) -> str:
    lines = [BLOCKS_HEADER]
    for mpid, name, month, trades, shares, pct in rows:
        lines.append(
            f"{mpid}|{name}|{month}-01|{trades}|{shares}|120.5|{pct}|1.2|"
            f"10|1000|{month}-01|{month}-15")
    return "\n".join(lines) + "\n"


def _week_text(header: str, rows: list[str]) -> str:
    return header + "\n" + "\n".join(rows) + "\n"


FIRM_HEADER = ("weekStartDate|ats_mp_id|marketParticipantName|"
               "totalShareQuantity|totalTradeQuantity")
SMBL_HEADER = ("weekStartDate|tierIdentifier|issueSymbolIdentifier|"
               "issueName|totalShareQuantity|totalTradeQuantity")
SF_HEADER = (FIRM_HEADER.replace("weekStartDate|", "weekStartDate|")
             .replace("ats_mp_id", "issueSymbolIdentifier|ats_mp_id")
             .replace("marketParticipantName|totalShareQuantity",
                      "issueName|marketParticipantName|totalShareQuantity"))
SF_HEADER = ("weekStartDate|issueSymbolIdentifier|issueName|ats_mp_id|"
             "marketParticipantName|totalShareQuantity|totalTradeQuantity")


def _store(tmp_path) -> Store:
    return Store(tmp_path / "ats.db")


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------

def test_norm_firm_row(tmp_path):
    n = norm_firm_row({"weekStartDate": "2026-09-14", "ats_mp_id": "UBSA",
                       "marketParticipantName": "UBS ATS",
                       "totalShareQuantity": "123456789",
                       "totalTradeQuantity": "98765"})
    assert n == {"week": "2026-09-14", "mpid": "UBSA", "name": "UBS ATS",
                 "shares": 123456789.0, "trades": 98765.0}


def test_norm_firm_row_missing_week_or_mpid(tmp_path):
    assert norm_firm_row({"ats_mp_id": "UBSA",
                          "totalShareQuantity": "1"}) is None
    assert norm_firm_row({"weekStartDate": "2026-09-14"}) is None


def test_norm_symbol_row(tmp_path):
    n = norm_symbol_row({"weekStartDate": "2026-09-14",
                         "issueSymbolIdentifier": "aapl",
                         "issueName": "Apple Inc. Common Stock",
                         "totalShareQuantity": "5", "totalTradeQuantity": "2"})
    assert n["symbol"] == "AAPL"
    assert n["name"] == "Apple Inc. Common Stock"


def test_norm_sf_row(tmp_path):
    n = norm_sf_row({"weekStartDate": "2026-09-14",
                     "issueSymbolIdentifier": "AAPL",
                     "issueName": "Apple", "ats_mp_id": "MSPL",
                     "marketParticipantName": "MS POOL (ATS-4)",
                     "totalShareQuantity": "10", "totalTradeQuantity": "3"})
    assert (n["symbol"], n["mpid"], n["venue"]) == ("AAPL", "MSPL",
                                                  "MS POOL (ATS-4)")


def test_norm_blocks_row_keeps_raw_cells(tmp_path):
    n = norm_blocks_row(dict(zip(
        BLOCKS_HEADER.split("|"),
        ["UBSA", "UBS ATS", "2026-08-01", "12078", "134312971",
         "11120", "12.55", "1.2", "10", "1000", "2026-08-01",
         "2026-08-15"])))
    assert n["month"] == "2026-08-01"
    assert n["shares"] == 134312971.0
    assert n["sharepct"] == 12.55
    assert n["raw"]["marketParticipantName"] == "UBS ATS"


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

def test_store_weekly_firm_writes_venue_and_totals(tmp_path):
    s = _store(tmp_path)
    w = date(2026, 9, 14)
    ven = _store_weekly_firm(s, w, [
        {"weekStartDate": "2026-09-14", "ats_mp_id": "UBSA",
         "marketParticipantName": "UBS ATS",
         "totalShareQuantity": "100", "totalTradeQuantity": "10"},
        {"weekStartDate": "2026-09-14", "ats_mp_id": "BIDS",
         "marketParticipantName": "BIDS ATS",
         "totalShareQuantity": "300", "totalTradeQuantity": "30"},
    ])
    assert set(ven) == {"UBSA", "BIDS"}
    assert s.points("cycle:ats-ubsa-shares") == {w: 100.0}
    assert s.points("cycle:ats-bids-trades") == {w: 30.0}
    assert s.points("cycle:ats-total-shares") == {w: 400.0}
    assert s.points("cycle:ats-total-trades") == {w: 40.0}


def test_store_weekly_symbols_top500_bound(tmp_path):
    s = _store(tmp_path)
    w = date(2026, 9, 14)
    rows = [{"weekStartDate": "2026-09-14",
             "issueSymbolIdentifier": f"S{i:04d}",
             "issueName": "", "totalShareQuantity": str(i),
             "totalTradeQuantity": "1"} for i in range(600)]
    top = _store_weekly_symbols(s, w, rows)
    assert len(top) == 500
    assert "S0599" in top and "S0000" not in top
    assert s.points("cycle:ats-sym-S0599-shares") == {w: 599.0}


def test_store_weekly_sf_top_venues(tmp_path):
    s = _store(tmp_path)
    w = date(2026, 9, 14)
    rows = [
        {"weekStartDate": "2026-09-14", "issueSymbolIdentifier": "AAPL",
         "issueName": "Apple", "ats_mp_id": "UBSA",
         "marketParticipantName": "UBS ATS",
         "totalShareQuantity": "100", "totalTradeQuantity": "5"},
        {"weekStartDate": "2026-09-14", "issueSymbolIdentifier": "AAPL",
         "issueName": "Apple", "ats_mp_id": "BIDS",
         "marketParticipantName": "BIDS ATS",
         "totalShareQuantity": "50", "totalTradeQuantity": "2"},
    ]
    out = _store_weekly_sf(s, w, rows)
    assert out["AAPL"]["shares"] == 150.0
    assert [v["mpid"] for v in out["AAPL"]["venues"]] == ["UBSA", "BIDS"]


def test_store_blocks_month_dedupes_slices(tmp_path):
    """The API repeats each venue's monthly totals across the 6
    summaryTypeCode slices — series must take one row per venue (no 6x)."""
    s = _store(tmp_path)
    m = date(2026, 8, 1)
    rows = []
    for code in ("2K", "10K", "100K", "200K", "2K-100K", "10K-200K"):
        rows.append(dict(zip(BLOCKS_HEADER.split("|"),
                             ["UBSA", "UBS ATS", "2026-08-01", "12078",
                              "134312971", "11120", "12.55", "1.2", "10",
                              "1000", "2026-08-01", "2026-08-15"])))
        rows[-1]["summaryTypeCode"] = code
    normed = _store_blocks_month(s, m, rows)
    assert len(normed) == 6  # all slices preserved for the doc
    assert s.points("cycle:ats-m-ubsa-shares") == {m: 134312971.0}
    assert s.points("cycle:ats-m-total-shares") == {m: 134312971.0}


def test_ats_panel_monthly_mode(tmp_path):
    """_ats_panel builds a leaderboard from the keyless monthly series."""
    from collector.panels import _ats_panel
    s = _store(tmp_path)
    m1, m2 = date(2026, 7, 1), date(2026, 8, 1)
    s.upsert_points("cycle:ats-m-ubsa-shares", [(m1, 100.0), (m2, 120.0)])
    s.upsert_points("cycle:ats-m-ubsa-trades", [(m1, 10.0), (m2, 12.0)])
    s.upsert_points("cycle:ats-m-total-shares", [(m1, 1000.0), (m2, 1200.0)])
    s.upsert_points("cycle:ats-m-total-trades", [(m1, 100.0), (m2, 120.0)])
    p = _ats_panel(s, {"configured": False, "keyless_months": 2,
                       "keyless_range": ["2026-07-01", "2026-08-01"]},
                   {"UBSA": {"name": "UBS ATS"}}, None, "finra-ats")
    assert p["weekly"] is False
    assert p["as_of"] == "2026-08-01"
    assert len(p["leaderboard"]) == 1
    row = p["leaderboard"][0]
    assert row["mpid"] == "UBSA" and row["shares"] == 120.0
    assert row["share_of_ats"] == 0.1
    assert row["deltas"]["1m"]["pct"] == 0.2
    assert row["deltas"]["1m"]["nom"] == 20.0


# ---------------------------------------------------------------------------
# token cache
# ---------------------------------------------------------------------------

def test_basic_auth_header_format(tmp_path):
    h = _basic("myid", "mysecret")
    assert h.startswith("Basic ")
    import base64
    assert base64.b64decode(h[6:]).decode() == "myid:mysecret"


def test_fetch_token_caches_in_memory(monkeypatch, tmp_path):
    finra_ats._token_cache.update({"token": None, "expires_at": 0.0,
                                   "group": None})
    calls = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"access_token": "tok123", "expires_in": 3600}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls.append((a, k))
            # the secret must travel in the Authorization header, never the URL
            assert "Authorization" in k["headers"]
            assert k["headers"]["Authorization"].startswith("Basic ")
            assert "mysecret" not in str(a[0])
            return FakeResp()

    monkeypatch.setattr(finra_ats.httpx, "AsyncClient", FakeClient)
    t1 = asyncio.run(fetch_token("myid", "mysecret"))
    t2 = asyncio.run(fetch_token("myid", "mysecret"))
    assert t1 == t2 == "tok123"
    assert len(calls) == 1  # second call served from cache
    finra_ats._token_cache.update({"token": None, "expires_at": 0.0,
                                   "group": None})


# ---------------------------------------------------------------------------
# graceful skip without a key (keyless monthly still runs)
# ---------------------------------------------------------------------------

def _fake_blocks(months=("2026-07-01", "2026-08-01")):
    parts = {"availablePartitions": [{"partitions": [m]} for m in months]}

    class FakeResp:
        def __init__(self, status_code=200, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload
            self._text = text
            self.url = "https://api.finra.org/x"

        def json(self):
            return self._payload

    async def fake_get(url, headers=None):
        return FakeResp(payload=parts)

    async def fake_post(url, body, headers=None):
        month = body["compareFilters"][0]["fieldValue"]
        rows = [("UBSA", "UBS ATS", month[:7], "12078", "134312971", "12.55"),
                ("BIDS", "BIDS ATS", month[:7], "943", "9547900", "1.10")]
        return _blocks_text(rows)

    return fake_get, fake_post


def test_keyless_blocks_run_without_key(monkeypatch, caplog, tmp_path):
    s = _store(tmp_path)
    fake_get, fake_post = _fake_blocks()
    monkeypatch.setattr(finra_ats, "_api_get", fake_get)
    monkeypatch.setattr(finra_ats, "_api_post", fake_post)
    monkeypatch.delenv("FINRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("FINRA_CLIENT_SECRET", raising=False)
    with caplog.at_level("WARNING", logger="collector.fetchers.finra_ats"):
        out = asyncio.run(fetch_finra_ats(s))
    assert out == "finra-ats"
    assert "FINRA API key required" in caplog.text
    assert s.points("cycle:ats-m-ubsa-shares") == {
        date(2026, 7, 1): 134312971.0, date(2026, 8, 1): 134312971.0}
    doc = s.doc("finra_ats")
    assert doc.payload["configured"] is False
    assert doc.payload["keyless_months"] == 2
    venues = s.doc("ats_venues").payload["mpids"]
    assert venues["UBSA"]["name"] == "UBS ATS"
    latest = s.doc("ats_blocks_latest").payload
    assert latest["as_of"] == "2026-08-01"
    assert latest["venues"][0]["mpid"] == "UBSA"


def test_no_partial_crash_when_keyless_fails(monkeypatch, tmp_path):
    s = _store(tmp_path)

    async def boom(url, headers=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(finra_ats, "_api_get", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_finra_ats(s))
