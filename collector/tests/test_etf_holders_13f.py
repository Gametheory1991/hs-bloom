"""ETF inverse-13F holder fetcher: parsing, quarter targeting, resume-skip,
plan-tier fallback. All HTTP is mocked; no key is ever read from the real
environment (FMP_API_KEY is scrubbed/mocked in every test)."""
import asyncio
import json
from datetime import date

import pytest

from collector.fetchers import etf_holders_13f as eh
from collector.fetchers.etf_holders_13f import (
    fetch_etf_holders_13f,
    fetch_one,
    parse_stable,
    parse_v3,
    quarter_end,
    summarize,
    target_quarter,
)
from collector.store import Store


@pytest.fixture(autouse=True)
def _no_fmp_key(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# quarter targeting (45-day 13F lag)
# ---------------------------------------------------------------------------

def test_quarter_end():
    assert quarter_end(2026, 1) == date(2026, 3, 31)
    assert quarter_end(2026, 2) == date(2026, 6, 30)
    assert quarter_end(2026, 3) == date(2026, 9, 30)
    assert quarter_end(2026, 4) == date(2026, 12, 31)


def test_target_quarter_lag():
    # 2026-10-07: Q3 ended 2026-09-30, +45d = 2026-11-14 -> not yet filed
    assert target_quarter(date(2026, 10, 7)) == (2026, 2)
    # 2026-11-20: Q3 now past the lag window
    assert target_quarter(date(2026, 11, 20)) == (2026, 3)
    # year boundary: 2026-01-10 -> Q4 2025 ended 2025-12-31, +45d not passed
    assert target_quarter(date(2026, 1, 10)) == (2025, 3)
    assert target_quarter(date(2026, 2, 20)) == (2025, 4)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

STABLE_ROWS = [
    {"symbol": "SPY", "date": "2026-06-30", "filingDate": "2026-08-12",
     "investorName": "VANGUARD GROUP INC", "cik": "0000102909",
     "sharesNumber": 1000, "changeInSharesNumber": 50,
     "ownership": 9.1, "marketValue": 500000},
    {"symbol": "SPY", "date": "2026-06-30", "filingDate": "2026-08-14",
     "investorName": "BLACKROCK INC.", "cik": "0001364742",
     "sharesNumber": 800, "changeInSharesNumber": -20,
     "ownership": 7.3, "marketValue": 400000},
    {"symbol": "SPY", "date": "2026-06-30", "filingDate": "2026-08-10",
     "investorName": "STATE STREET CORP", "cik": "0000093751",
     "sharesNumber": 500, "changeInSharesNumber": 0,
     "ownership": 4.5, "marketValue": 250000},
    {"symbol": "SPY", "date": "2026-06-30", "filingDate": "2026-08-11",
     "investorName": "BROKEN ROW", "cik": "1",
     "sharesNumber": None, "changeInSharesNumber": None,
     "ownership": None, "marketValue": None},  # dropped
]


def test_parse_stable():
    holders = parse_stable(json.dumps(STABLE_ROWS))
    assert len(holders) == 3
    # sorted desc by shares
    assert [h["investorName"] for h in holders] == [
        "VANGUARD GROUP INC", "BLACKROCK INC.", "STATE STREET CORP"]
    assert holders[0]["cik"] == "0000102909"
    assert holders[0]["changeInSharesNumber"] == 50
    assert holders[1]["changeInSharesNumber"] == -20
    assert holders[2]["changeInSharesNumber"] == 0


def test_parse_stable_caps_at_top_n():
    rows = [{"investorName": f"F{i}", "sharesNumber": i} for i in range(120)]
    holders = parse_stable(json.dumps(rows))
    assert len(holders) == eh.TOP_N
    assert holders[0]["sharesNumber"] == 119


def test_parse_v3_flags_fallback():
    rows = [{"investorName": "VANGUARD GROUP INC", "cik": "0000102909",
             "sharesNumber": 1000, "ownership": 9.1,
             "marketValue": 500000, "filingDate": "2026-08-12"}]
    holders = parse_v3(json.dumps(rows))
    assert holders[0]["via_fallback"] is True
    assert holders[0]["changeInSharesNumber"] is None


def test_summarize_bloomberg_columns():
    holders = parse_stable(json.dumps(STABLE_ROWS))
    s = summarize(holders, prev_inst_shares=2000.0)
    assert s["inst_shares_held"] == 2300
    assert s["pct_chg_inst"] == pytest.approx(15.0)
    assert s["pct_of_os"] == pytest.approx(9.1 + 7.3 + 4.5)
    assert s["n_holders"] == 3
    assert s["n_buyers"] == 1
    assert s["n_sellers"] == 1


def test_summarize_no_prev():
    holders = parse_stable(json.dumps(STABLE_ROWS))
    assert summarize(holders, None)["pct_chg_inst"] is None


# ---------------------------------------------------------------------------
# fetching with mocked HTTP
# ---------------------------------------------------------------------------

async def _stable_get(url, **kw):
    assert "extract-analytics/holder" in url
    assert kw["params"]["apikey"] == "test-key"
    assert kw["params"]["symbol"] == "SPY"
    assert kw["params"]["year"] == 2026 and kw["params"]["quarter"] == 2
    return json.dumps(STABLE_ROWS)


async def _v3_get(url, **kw):
    return json.dumps([{"investorName": "VANGUARD GROUP INC",
                        "sharesNumber": 900, "ownership": 8.0,
                        "marketValue": 450000, "filingDate": "2026-08-12"}])


async def test_fetch_one_stable():
    holders = await fetch_one("SPY", 2026, 2, "test-key", _stable_get)
    assert len(holders) == 3
    assert holders[0]["investorName"] == "VANGUARD GROUP INC"


async def test_fetch_one_plan_blocked_falls_back_to_v3():
    calls = []

    async def blocked(url, **kw):
        calls.append(url)
        if "extract-analytics" in url:
            # get_text strips the query; plan errors surface as HTTP 402/403
            raise RuntimeError("HTTP 403 for "
                               "https://financialmodelingprep.com/stable/...")
        return await _v3_get(url, **kw)

    holders = await fetch_one("SPY", 2026, 2, "test-key", blocked)
    assert len(calls) == 2
    assert holders[0]["via_fallback"] is True


async def test_fetch_one_non_plan_error_raises():
    async def boom(url, **kw):
        raise RuntimeError("HTTP 500 for https://financialmodelingprep.com/...")
    with pytest.raises(RuntimeError):
        await fetch_one("SPY", 2026, 2, "test-key", boom)


def _store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_resume_skip_and_aggregate(monkeypatch, tmp_path):
    monkeypatch.setattr(eh, "ETF_UNIVERSE",
                        [("SPY", "equity"), ("TLT", "fi")])
    monkeypatch.setattr(eh, "DAILY_BUDGET", 50)
    monkeypatch.setattr(eh, "PAUSE", 0)
    monkeypatch.setattr(eh, "target_quarter", lambda today: (2026, 2))
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    store = _store(tmp_path)

    seen = []

    async def get(url, **kw):
        seen.append(kw["params"]["symbol"])
        rows = [dict(r, symbol=kw["params"]["symbol"]) for r in STABLE_ROWS]
        return json.dumps(rows)

    out = asyncio.run(fetch_etf_holders_13f(store, get))
    assert out == "etf_holders_13f:2"
    assert set(seen) == {"SPY", "TLT"}

    # rerun skips both symbol-quarters (resume-safe)
    seen.clear()
    out = asyncio.run(fetch_etf_holders_13f(store, get))
    assert out == "etf_holders_13f:0"
    assert seen == []

    agg = store.doc("etfholders").payload
    assert agg["coverage"]["covered"] == 2
    assert agg["coverage"]["universe"] == 2
    assert set(agg["symbols"]) == {"SPY", "TLT"}
    assert agg["symbols"]["SPY"]["n_holders"] == 3
    assert "45-day" in agg["note"]

    # quarterly points dated at quarter-end
    pts = store.points("cycle:etfhold-SPY-instpct")
    assert len(pts) == 1
    assert list(pts)[0] == date(2026, 6, 30)
    assert list(pts.values())[0] == pytest.approx(20.9)


def test_no_key_raises(monkeypatch, tmp_path):
    store = _store(tmp_path)

    async def get(url, **kw):
        raise AssertionError("must not touch the network without a key")

    with pytest.raises(RuntimeError, match="FMP_API_KEY is not set"):
        asyncio.run(fetch_etf_holders_13f(store, get))


def test_all_failed_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(eh, "ETF_UNIVERSE", [("SPY", "equity")])
    monkeypatch.setattr(eh, "PAUSE", 0)
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    store = _store(tmp_path)

    async def get(url, **kw):
        raise RuntimeError("HTTP 500 for https://financialmodelingprep.com/...")

    with pytest.raises(RuntimeError, match="all 1 attempted"):
        asyncio.run(fetch_etf_holders_13f(store, get))
