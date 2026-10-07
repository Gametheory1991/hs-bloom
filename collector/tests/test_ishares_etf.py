"""iShares ETF fetcher tests: screener parsing, FMP/CoinLaw fallbacks, universe."""
import asyncio
import json

import pytest

from collector.fetchers.ishares_etf import (
    ACTIVE_ETFS,
    CLASS_LABELS,
    ETF_UNIVERSE,
    ISSUER_BY_TICKER,
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
          "navAmountAsOf": {"d": "Oct 05, 2026", "r": 20261005},
          "fees": {"d": "0.15", "r": 0.15},
          "twelveMonTrlYield": {"d": "5.05", "r": 5.0487}},
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
    if "query1.finance.yahoo.com" in url:
        return YAHOO_CHART
    raise AssertionError(f"unexpected url {url}")


YAHOO_CHART = ('{"chart":{"result":[{"meta":{"regularMarketPrice":77.5},'
               '"timestamp":[1728518400,1728604800],'
               '"indicators":{"quote":[{"close":[76.9,77.5]}]},'
               '"events":{"dividends":{"1728518400":{"amount":0.32},'
               '"1696118400":{"amount":0.30}}}}]}}')


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
    assert funds["TLT"]["class"] == "fi-treasury"
    assert funds["TLT"]["expense"] == 0.15
    assert funds["TLT"]["divyield"] == 5.0487
    assert funds["TLT"]["price"] == 77.5  # yahoo latest close
    # SPY is non-iShares, non-crypto -> honest pending (FMP key unset in test)
    assert "SPY" not in funds
    assert "SPY" in doc.payload["pending"]
    assert "2 ishares" in res and "coinlaw" in res
    # series landed
    assert store.points("cycle:etf-TLT-aum")
    assert store.points("cycle:etf-TLT-nav")
    assert store.points("cycle:etf-TLT-shares")
    assert store.points("cycle:etf-TLT-price")
    assert store.points("cycle:etf-TLT-expense")
    assert store.points("cycle:etf-TLT-divyield")
    # CoinLaw fallback: FBTC/BITB are crypto, missing from iShares
    assert funds["FBTC"]["source"] == "coinlaw"
    assert funds["FBTC"]["aum"] == 10810 * 1e6
    assert store.points("cycle:etf-FBTC-aum")
    assert not store.points("cycle:etf-FBTC-flow7d")  # blank cell -> none stored
    assert not store.points("cycle:etf-FBTC-nav")  # no NAV from CoinLaw
    assert funds["BITB"]["source"] == "coinlaw"
    assert store.points("cycle:etf-BITB-flow7d")  # -51M reported
    assert "coinlaw" in res
    # new fixed-income sub-classes / leveraged / AI tickers are in the universe
    uni = {t for t, _ in ETF_UNIVERSE}
    for t in ["VCIT", "BKLN", "JAAA", "CLOX", "SGOV", "USFR", "UNG", "EEM",
              "GOVT", "ANGL", "VMBS", "VTEB", "SCHP", "SRLN", "AAA", "PGX",
              "SCHZ", "BNDX", "TQQQ", "SQQQ", "TMF", "UVXY", "TSLL",
              "BOTZ", "AIQ", "WTAI", "THNQ", "ROBT"]:
        assert t in uni
    clsmap = dict(ETF_UNIVERSE)
    assert clsmap["TLT"] == "fi-treasury"
    assert clsmap["LQD"] == "fi-ig"
    assert clsmap["HYG"] == "fi-hy"
    assert clsmap["TQQQ"] == "leveraged"
    assert clsmap["BOTZ"] == "ai"
    assert "IRBO" not in clsmap  # delisted, verified 2026-10-06
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
    # Harry's 2026-10-06 expansion: fixed-income sub-classes
    assert by_t["TLT"] == "fi-treasury"
    assert by_t["IEF"] == "fi-treasury"
    assert by_t["SHY"] == "fi-treasury"
    assert by_t["LQD"] == "fi-ig"
    assert by_t["VCIT"] == "fi-ig"
    assert by_t["HYG"] == "fi-hy"
    assert by_t["JNK"] == "fi-hy"
    assert by_t["MBB"] == "fi-mbs"
    assert by_t["MUB"] == "fi-muni"
    assert by_t["TIP"] == "fi-tips"
    assert by_t["BKLN"] == "fi-loans"
    assert by_t["JAAA"] == "fi-loans"
    assert by_t["CWB"] == "fi-conv"
    assert by_t["PFF"] == "fi-conv"
    assert by_t["AGG"] == "fi-agg"
    assert by_t["BND"] == "fi-agg"
    assert by_t["EMB"] == "fi-intl"
    assert by_t["BINC"] == "privcredit"
    assert by_t["BBDC"] == "privcredit"
    for t in ["GLD", "SLV", "USO", "UNG"]:
        assert by_t[t] == "commodity", t
    for t in ["IBIT", "ETHA", "FBTC", "GBTC"]:
        assert by_t[t] == "crypto", t
    for t in ["TQQQ", "SQQQ", "TMF", "UVXY", "TSLL"]:
        assert by_t[t] == "leveraged", t
    for t in ["BOTZ", "AIQ", "WTAI", "THNQ", "ROBT"]:
        assert by_t[t] == "ai", t
    assert "fixedincome" not in CLASS_LABELS  # replaced by sub-classes
    assert CLASS_LABELS["fi-treasury"] == "FI: Treasury"
    assert CLASS_LABELS["leveraged"] == "Leveraged/Inverse"
    assert CLASS_LABELS["ai"] == "AI"
    # Harry 2026-10-06: floaters/ABS/CMBS sub-classes, BDCs, digital rename
    assert by_t["TFLO"] == "fi-floater"
    assert by_t["USFR"] == "fi-floater"
    assert by_t["FLOT"] == "fi-floater"
    assert by_t["FLRN"] == "fi-floater"
    assert by_t["JABS"] == "fi-abs"
    assert by_t["CMBS"] == "fi-cmbs"
    for t in ["ARCC", "MAIN", "HTGC", "GBDC", "BXSL", "OCSL", "PSEC",
              "NMFC", "TCPC"]:
        assert by_t[t] == "bdc", t
    assert CLASS_LABELS["crypto"] == "Bitcoin & Digital Assets"
    assert CLASS_LABELS["bdc"] == "BDCs"
    assert CLASS_LABELS["fi-floater"] == "FI: Floaters"
    assert CLASS_LABELS["fi-abs"] == "FI: ABS"
    assert CLASS_LABELS["fi-cmbs"] == "FI: CMBS"


def test_crypto_equity_set():
    from collector.fetchers.ishares_etf import CRYPTO_EQUITY
    assert CRYPTO_EQUITY == {"BKCH", "WGMI", "BITQ", "DAPP", "BLOK", "BITO"}
    by_t = dict(ETF_UNIVERSE)
    for t in CRYPTO_EQUITY:
        assert by_t[t] == "crypto", t


def test_parse_fmp_quote():
    from collector.fetchers.ishares_etf import parse_fmp_quote
    q = parse_fmp_quote([{"symbol": "ARCC", "name": "Ares Capital Corp",
                          "price": 21.5, "marketCap": 12345678900}])
    assert q["mcap"] == 12345678900
    assert q["price"] == 21.5
    assert q["name"] == "Ares Capital Corp"
    assert parse_fmp_quote([]) is None
    assert parse_fmp_quote([{"symbol": "X"}]) is None  # no marketCap


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


def test_active_etf_metadata():
    """Harry 2026-10-07: active/passive tags + issuer map are consistent."""
    uni = {t for t, _ in ETF_UNIVERSE}
    # every active ticker is in the universe
    assert ACTIVE_ETFS <= uni
    # issuer map covers the whole active cohort, issuers non-empty
    assert ACTIVE_ETFS <= set(ISSUER_BY_TICKER)
    for t, iss in ISSUER_BY_TICKER.items():
        assert iss, f"{t} has empty issuer"
        assert t in uni, f"issuer map ticker {t} not in universe"
    # leveraged / inverse funds are passive (not manager-alpha)
    by_t = dict(ETF_UNIVERSE)
    for t, cls in by_t.items():
        if cls == "leveraged":
            assert t not in ACTIVE_ETFS, f"{t} leveraged but tagged active"
    # spot checks: issuer names + classes of the new cohort
    assert ISSUER_BY_TICKER["DFUS"] == "Dimensional"
    assert ISSUER_BY_TICKER["AVUV"] == "Avantis"
    assert ISSUER_BY_TICKER["JEPI"] == "JPMorgan"
    assert ISSUER_BY_TICKER["CGDV"] == "Capital Group"
    assert ISSUER_BY_TICKER["FBCG"] == "Fidelity"
    assert ISSUER_BY_TICKER["TCHP"] == "T. Rowe Price"
    assert ISSUER_BY_TICKER["KORP"] == "American Century"
    assert len(ACTIVE_ETFS) == 50
    assert by_t["DFUS"] == "equity"
    assert by_t["JPST"] == "fi-short"
    assert by_t["FBND"] == "fi-agg"
    assert by_t["KORP"] == "fi-ig"
    assert by_t["AHYB"] == "fi-hy"
    assert by_t["AEMB"] == "fi-intl"
    assert by_t["TAXF"] == "fi-muni"
    assert CLASS_LABELS["fi-short"] == "FI: Short Duration"
    # index bellwethers stay passive
    for t in ["SPY", "VOO", "IVV", "TLT", "AGG", "QQQ"]:
        assert t not in ACTIVE_ETFS, t


@pytest.mark.asyncio
async def test_fetch_wires_active_and_issuer(tmp_path):
    """The fetch loop writes active/issuer into every fund dict."""
    screener = json.dumps({
        "1": {"localExchangeTicker": "DFUS",
              "fundName": "Dimensional US Equity ETF",
              "navAmount": {"r": 50.0},
              "totalNetAssets": {"r": 5_000_000_000.0},
              "fees": {"r": 0.09}},
        "2": {"localExchangeTicker": "TLT",
              "fundName": "iShares 20+ Year Treasury Bond ETF",
              "navAmount": {"r": 77.08},
              "totalNetAssets": {"r": 47_363_247_419.87},
              "fees": {"r": 0.15}},
    })

    async def fake(url, **kw):
        if "ishares.com" in url:
            return screener
        if "coinlaw.io" in url:
            return COINLAW
        if "query1.finance.yahoo.com" in url:
            return YAHOO_CHART
        raise AssertionError(f"unexpected url {url}")

    store = Store(str(tmp_path / "t2.db"))
    await fetch_ishares_etf(store, fake)
    funds = store.doc("etfflows").payload["funds"]
    assert funds["DFUS"]["active"] is True
    assert funds["DFUS"]["issuer"] == "Dimensional"
    assert funds["DFUS"]["class"] == "equity"
    # passive index fund: active False, issuer None
    assert funds["TLT"]["active"] is False
    assert funds["TLT"]["issuer"] is None
    # CoinLaw crypto funds keep their source-supplied issuer
    assert funds["FBTC"]["issuer"] == "Fidelity"
    assert funds["FBTC"]["active"] is False
