"""iShares ETF fetcher tests: screener parsing, FMP/CoinLaw fallbacks, universe."""
import asyncio
import json

import pytest

from collector.fetchers.ishares_etf import (
    CLASS_LABELS,
    ETF_UNIVERSE,
    fetch_ishares_etf,
    parse_coinlaw,
    parse_fmp_info,
    parse_ishares,
)
from collector.store import Store


@pytest.fixture(autouse=True)
def _no_fmp_key(monkeypatch):
    """Existing tests must not touch the network if a key happens to be set."""
    monkeypatch.delenv("FMP_API_KEY", raising=False)

SCREENER = json.dumps({
    "1": {"localExchangeTicker": "TLT", "fundName": "iShares 20+ Year Treasury Bond ETF",
          "navAmount": {"d": "77.08", "r": 77.08},
          "totalNetAssets": {"d": "47,363,247,420", "r": 47363247419.87},
          "navAmountAsOf": {"d": "Oct 05, 2026", "r": 20261005}},
    "2": {"localExchangeTicker": "IBIT", "fundName": "iShares Bitcoin Trust ETF",
          "navAmount": {"d": "48.51", "r": 48.509148},
          "totalNetAssets": {"d": "68,987,769,571", "r": 68987769570.79},
          "navAmountAsOf": {"d": "Oct 05, 2026", "r": 20261005}},
    "3": {"localExchangeTicker": "ZZZ", "fundName": "No NAV fund"},  # skipped
})


def test_parse_ishares():
    out = parse_ishares(SCREENER)
    assert set(out) == {"TLT", "IBIT"}
    assert out["TLT"]["nav"] == 77.08
    assert out["TLT"]["aum"] == 47363247419.87
    assert out["TLT"]["shares"] == pytest.approx(47363247419.87 / 77.08)
    assert out["TLT"]["asof"] == "Oct 05, 2026"


async def fake_get_text(url, **kw):
    if "ishares.com" in url:
        return SCREENER
    if "coinlaw.io" in url:
        return COINLAW
    raise AssertionError(f"unexpected url {url}")


COINLAW = ('\ufeffRow ID,Name,Ticker,Issuer,Asset,AUM (USD millions),'
           '"Net flow, week (USD millions)",'
           'Net flow since launch (USD millions),Holdings,Holdings date\n'
           'fbtc,Fidelity Wise Origin Bitcoin Fund,FBTC,Fidelity,Bitcoin,'
           '10810,,5000,100000,2026-10-01\n'
           'bitb,Bitwise Bitcoin ETF,BITB,Bitwise,Bitcoin,3204,-51,900,'
           '30000,2026-10-01\n'
           'ibit2,iShares Bitcoin Trust ETF,IBIT,BlackRock,Bitcoin,'
           '67590,389,,803343,2026-10-01\n')


def test_parse_coinlaw():
    out = parse_coinlaw(COINLAW)
    assert set(out) == {"FBTC", "BITB", "IBIT"}
    assert out["FBTC"]["aum"] == 10810 * 1e6
    assert out["FBTC"]["flow7d"] is None  # blank cell
    assert out["BITB"]["flow7d"] == -51 * 1e6
    assert out["IBIT"]["issuer"] == "BlackRock"
    assert out["BITB"]["asof"] == "2026-10-01"


@pytest.mark.asyncio
async def test_fetch_ishares_etf(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    res = await fetch_ishares_etf(store, fake_get_text)
    doc = store.doc("etfflows")
    assert doc is not None
    funds = doc.payload["funds"]
    assert funds["TLT"]["source"] == "ishares"
    assert funds["TLT"]["class"] == "fixedincome"
    # SPY is non-iShares, non-crypto -> honest pending (Yahoo v7 is walled)
    assert "SPY" not in funds
    assert "SPY" in doc.payload["pending"]
    assert "2 ishares" in res and "coinlaw" in res
    # series landed
    assert store.points("cycle:etf-TLT-aum")
    assert store.points("cycle:etf-TLT-nav")
    assert store.points("cycle:etf-TLT-shares")
    # CoinLaw fallback: FBTC/BITB are crypto, missing from iShares
    assert funds["FBTC"]["source"] == "coinlaw"
    assert funds["FBTC"]["aum"] == 10810 * 1e6
    assert store.points("cycle:etf-FBTC-aum")
    assert not store.points("cycle:etf-FBTC-flow7d")  # blank cell -> none stored
    assert not store.points("cycle:etf-FBTC-nav")  # no NAV from CoinLaw
    assert funds["BITB"]["source"] == "coinlaw"
    assert store.points("cycle:etf-BITB-flow7d")  # -51M reported
    assert "coinlaw" in res
    # new fixed-income / privcredit tickers are in the universe
    uni = {t for t, _ in ETF_UNIVERSE}
    for t in ["VCIT", "BKLN", "JAAA", "CLOX", "SGOV", "USFR", "UNG", "EEM"]:
        assert t in uni
    # unknown tickers absent
    assert "ZZZ" not in funds


FMP_INFO = json.dumps([{
    "symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
    "expenseRatio": "0.0945%", "aum": 630250000000.0,
    "navPrice": 661.42}])


def test_parse_fmp_info():
    out = parse_fmp_info(json.loads(FMP_INFO))
    assert out["aum"] == 630250000000.0
    assert out["nav"] == 661.42
    assert out["shares"] == pytest.approx(630250000000.0 / 661.42)
    assert out["name"] == "SPDR S&P 500 ETF Trust"
    assert parse_fmp_info([]) is None
    assert parse_fmp_info([{"symbol": "X"}]) is None  # no aum
    # alternate NAV field names
    assert parse_fmp_info([{"aum": 10.0, "nav": 2.0}])["nav"] == 2.0
    assert parse_fmp_info([{"aum": 10.0, "netAssetValue": 2.5}])["nav"] == 2.5


async def _fake_with_fmp(url, **kw):
    if "financialmodelingprep.com/stable/etf/info" in url:
        sym = kw.get("params", {}).get("symbol")
        if sym == "SPY":
            return FMP_INFO
        return "[]"
    return await fake_get_text(url, **kw)


@pytest.mark.asyncio
async def test_fetch_fmp_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    store = Store(str(tmp_path / "t.db"))
    res = await fetch_ishares_etf(store, _fake_with_fmp)
    doc = store.doc("etfflows")
    funds = doc.payload["funds"]
    assert funds["SPY"]["source"] == "fmp"
    assert funds["SPY"]["aum"] == 630250000000.0
    assert funds["SPY"]["class"] == "equity"
    assert store.points("cycle:etf-SPY-aum")
    assert store.points("cycle:etf-SPY-nav")
    assert store.points("cycle:etf-SPY-shares")
    assert "SPY" not in doc.payload["pending"]
    assert "1 fmp" in res
    # FMP returning nothing for other pending funds -> still pending
    assert "QQQ" in doc.payload["pending"]


@pytest.mark.asyncio
async def test_fetch_fmp_no_key(tmp_path):
    """No key -> FMP skipped, no network attempt, honest pending."""
    async def fail_on_fmp(url, **kw):
        assert "financialmodelingprep" not in url
        return await fake_get_text(url, **kw)

    store = Store(str(tmp_path / "t.db"))
    await fetch_ishares_etf(store, fail_on_fmp)
    doc = store.doc("etfflows")
    assert "SPY" in doc.payload["pending"]


def test_universe_classes():
    by_t = dict(ETF_UNIVERSE)
    # Harry's 2026-10-06 expansion: fixed income + private credit
    for t in ["TLT", "IEF", "SHY", "TIP", "LQD", "HYG", "JNK", "EMB", "MBB",
              "AGG", "BND", "VCIT", "VCSH", "VGSH", "BKLN", "CWB", "PFF",
              "MUB", "SHV", "BIL", "SGOV", "USFR"]:
        assert by_t[t] == "fixedincome", t
    for t in ["JAAA", "CLOX", "CLOI", "JBBB", "BINC", "BBDC"]:
        assert by_t[t] == "privcredit", t
    for t in ["GLD", "SLV", "USO", "UNG"]:
        assert by_t[t] == "commodity", t
    for t in ["IBIT", "ETHA", "FBTC", "GBTC"]:
        assert by_t[t] == "crypto", t
    assert CLASS_LABELS["fixedincome"] == "Fixed Income"
    assert CLASS_LABELS["privcredit"] == "Private Credit"


@pytest.mark.asyncio
async def test_fetch_ishares_screener_down(tmp_path):
    async def dead(url, **kw):
        if "ishares.com" in url:
            raise RuntimeError("boom")
        return await fake_get_text(url, **kw)

    store = Store(str(tmp_path / "t.db"))
    res = await fetch_ishares_etf(store, dead)
    doc = store.doc("etfflows")
    # iShares down: TLT has no fallback -> pending, not failed
    assert "TLT" not in doc.payload["funds"]
    assert "TLT" in doc.payload["pending"]
    assert "0 ishares" in res
    # CoinLaw still fills crypto gap even when iShares is down
    assert doc.payload["funds"]["FBTC"]["source"] == "coinlaw"


@pytest.mark.asyncio
async def test_fetch_coinlaw_down(tmp_path):
    async def dead(url, **kw):
        if "coinlaw.io" in url:
            raise RuntimeError("boom")
        return await fake_get_text(url, **kw)

    store = Store(str(tmp_path / "t.db"))
    res = await fetch_ishares_etf(store, dead)
    doc = store.doc("etfflows")
    # FBTC has no iShares/Yahoo coverage -> absent, not failed
    assert "FBTC" not in doc.payload["funds"]
    assert "0 coinlaw" in res
