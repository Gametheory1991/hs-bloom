from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

from collector.config import CycleSeriesCfg
from collector.fetchers.cycle import fetch_cycle
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"
FRED = (FIX / "fred_dgs10.json").read_text()
DBNOMICS = (FIX / "dbnomics_ism.json").read_text()
OECD = (FIX / "oecd_cli.csv").read_text()
CFTC = (FIX / "cftc_vix.json").read_text()
CBOE = (FIX / "cboe_daily.json").read_text()
YAHOO = (FIX / "yahoo_spx.json").read_text()
AAII = (FIX / "aaii_sentiment.xls").read_bytes()


def fake_io(urls):
    async def get_text(url, params=None, headers=None):
        urls.append(url)
        host = urlparse(url).netloc
        if host == "api.stlouisfed.org":
            return FRED
        if host == "api.db.nomics.world":
            return DBNOMICS
        if host == "sdmx.oecd.org":
            return OECD
        if host == "publicreporting.cftc.gov":
            return CFTC
        if host == "cdn.cboe.com":
            return CBOE
        if host == "query1.finance.yahoo.com":
            return YAHOO
        raise AssertionError(f"unexpected url {url}")

    async def get_bytes(url, params=None, headers=None):
        urls.append(url)
        return AAII

    return get_text, get_bytes


ALL_SOURCES = [
    CycleSeriesCfg(id="vix", name="VIX", unit="idx", fred="VIXCLS"),
    CycleSeriesCfg(id="ism", name="ISM", unit="idx", dbnomics="ISM/pmi/pm"),
    CycleSeriesCfg(id="cli", name="CLI", unit="idx", oecd="F/USA.M.LI...AA...H"),
    CycleSeriesCfg(id="cot", name="COT", unit="contracts", cftc="1170E1"),
    CycleSeriesCfg(id="pc", name="PC", unit="ratio", cboe="TOTAL PUT/CALL RATIO"),
    CycleSeriesCfg(id="aaii", name="AAII", unit="pts", aaii="bull_bear_spread"),
    CycleSeriesCfg(id="ratio", name="R", unit="ratio", yahoo_ratio=["RSP", "SPY"]),
]


async def test_fetch_cycle_dispatches_every_source(tmp_path):
    store = Store(tmp_path / "t.db")
    urls = []
    get_text, get_bytes = fake_io(urls)
    label = await fetch_cycle(ALL_SOURCES, store, "test-key", get_text, get_bytes,
                              today=date(2026, 8, 24))
    assert label == "cycle"
    for cfg in ALL_SOURCES:
        assert store.points(f"cycle:{cfg.id}") != {}, cfg.id
    assert sum(urlparse(u).netloc == "query1.finance.yahoo.com" for u in urls) == 2


async def test_fetch_cycle_isolates_failures(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [
        CycleSeriesCfg(id="bad", name="Bad", unit="idx", dbnomics="NOPE/x/y"),
        CycleSeriesCfg(id="vix", name="VIX", unit="idx", fred="VIXCLS"),
    ]

    async def get_text(url, params=None, headers=None):
        if "db.nomics" in url:
            raise RuntimeError("HTTP 404")
        return FRED

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    # 1/2 failing is a minority: the job stays healthy (no raise) so
    # last_success doesn't go stale, but the failure is surfaced.
    result = await fetch_cycle(series, store, "k", get_text, get_bytes)
    assert result == "cycle (1 failed)"
    assert store.points("cycle:vix") != {}  # good series still stored
    assert store.points("cycle:bad") == {}  # failed series not stored


async def test_fetch_cycle_majority_failure_raises(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [
        CycleSeriesCfg(id="bad1", name="Bad1", unit="idx", dbnomics="NOPE/x/y"),
        CycleSeriesCfg(id="bad2", name="Bad2", unit="idx", dbnomics="NOPE/x/y"),
        CycleSeriesCfg(id="vix", name="VIX", unit="idx", fred="VIXCLS"),
    ]

    async def get_text(url, params=None, headers=None):
        if "db.nomics" in url:
            raise RuntimeError("HTTP 404")
        return FRED

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    with pytest.raises(RuntimeError, match="2/3 cycle series failed"):
        await fetch_cycle(series, store, "k", get_text, get_bytes)
    assert store.points("cycle:vix") != {}  # good series still stored


async def test_fetch_cycle_unconfigured_source_reports_error(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [CycleSeriesCfg(id="empty", name="E", unit="idx")]

    async def get_text(url, params=None, headers=None):
        raise AssertionError("unused")

    with pytest.raises(RuntimeError, match="empty"):
        await fetch_cycle(series, store, "k", get_text, get_text)


async def test_fetch_cycle_skips_external_series(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [CycleSeriesCfg(id="trace-ust-par", name="T", unit="$bn",
                             external=True)]

    async def get_text(url, params=None, headers=None):
        raise AssertionError("external series must not be fetched")

    assert await fetch_cycle(series, store, "k", get_text, get_text) == "cycle"
    assert store.points("cycle:trace-ust-par") == {}


async def test_fetch_cycle_valid_range_drops_corrupt_points(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [CycleSeriesCfg(id="ism", name="ISM", unit="idx", dbnomics="ISM/pmi/pm",
                             valid_range=[20, 80])]
    urls = []
    get_text, get_bytes = fake_io(urls)
    await fetch_cycle(series, store, "k", get_text, get_bytes)
    # dbnomics fixture holds 49.5 and 48.7 — inject nothing out of range here;
    # the range logic is exercised directly below via a synthetic fetch
    assert set(store.points("cycle:ism").values()) == {49.5, 48.7}

    corrupt = [CycleSeriesCfg(id="vixr", name="V", unit="idx", fred="VIXCLS",
                              valid_range=[4.0, 4.2])]
    await fetch_cycle(corrupt, store, "k", get_text, get_bytes)
    # fred fixture holds 4.15 and 4.12 -> only both in [4.0, 4.2]; then narrow:
    narrow = [CycleSeriesCfg(id="vixn", name="V", unit="idx", fred="VIXCLS",
                             valid_range=[4.13, 4.2])]
    await fetch_cycle(narrow, store, "k", get_text, get_bytes)
    assert list(store.points("cycle:vixn").values()) == [4.15]  # 4.12 dropped


async def test_fetch_cycle_retries_yahoo_on_429(tmp_path):
    """A 429 on a Yahoo series is retried once after a backoff, not recorded."""
    import asyncio

    store = Store(tmp_path / "t.db")
    series = [
        CycleSeriesCfg(id="spx", name="SPX", unit="idx", yahoo="^GSPC"),
    ]
    calls = {"n": 0}

    async def get_text(url, params=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP 429 for https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC")
        return YAHOO

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    sleeps = []
    orig_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)

    asyncio.sleep = fake_sleep
    try:
        assert await fetch_cycle(series, store, "k", get_text, get_bytes) == "cycle"
    finally:
        asyncio.sleep = orig_sleep
    assert calls["n"] == 2  # one retry happened
    assert 15 in sleeps  # backoff was taken
    assert store.points("cycle:spx") != {}


async def test_cboe_ratios_share_one_daily_file_when_history_populated(tmp_path):
    store = Store(tmp_path / "t.db")
    urls = []

    async def get_text(url, params=None, headers=None):
        urls.append(url)
        return CBOE

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    series = [
        CycleSeriesCfg(id="pc1", name="PC1", unit="ratio", cboe="TOTAL PUT/CALL RATIO"),
        CycleSeriesCfg(id="pc2", name="PC2", unit="ratio", cboe="EQUITY PUT/CALL RATIO"),
    ]
    # populate: > CBOE_THIN_POINTS stored points so both series take the
    # incremental (one-file) path instead of the 30-day backfill walk
    from datetime import timedelta

    base = date(2026, 6, 1)
    pts = [(base + timedelta(days=i), 0.7) for i in range(25)]
    store.upsert_points("cycle:pc1", pts)
    store.upsert_points("cycle:pc2", pts)

    label = await fetch_cycle(series, store, "k", get_text, get_bytes,
                              today=date(2026, 8, 24))
    assert label == "cycle"
    cboe_urls = [u for u in urls if "cdn.cboe.com" in u]
    assert len(cboe_urls) == 1  # one file, not one walk per ratio
    assert store.points("cycle:pc1")[date(2026, 8, 24)] == 0.72
    assert store.points("cycle:pc2")[date(2026, 8, 24)] == 0.51


async def test_cboe_thin_history_falls_back_to_backfill_walk(tmp_path):
    store = Store(tmp_path / "t.db")
    urls = []

    async def get_text(url, params=None, headers=None):
        urls.append(url)
        return CBOE

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    series = [
        CycleSeriesCfg(id="pc1", name="PC1", unit="ratio", cboe="TOTAL PUT/CALL RATIO"),
    ]
    label = await fetch_cycle(series, store, "k", get_text, get_bytes,
                              today=date(2026, 8, 24))
    assert label == "cycle"
    cboe_urls = [u for u in urls if "cdn.cboe.com" in u]
    # 1 daily-file probe + 30-day backfill walk (22 weekdays in 2026-08)
    assert len(cboe_urls) > 10
    assert len(store.points("cycle:pc1")) >= 20  # full history bar preserved


async def test_yahoo_uses_5d_incremental_once_populated(tmp_path):
    store = Store(tmp_path / "t.db")
    params_seen = []

    async def get_text(url, params=None, headers=None):
        params_seen.append(params or {})
        return YAHOO

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    from datetime import timedelta

    base = date(2025, 1, 2)
    store.upsert_points("cycle:spx", [(base + timedelta(days=i), 6000.0) for i in range(150)])
    series = [CycleSeriesCfg(id="spx", name="SPX", unit="idx", yahoo="^GSPC")]
    await fetch_cycle(series, store, "k", get_text, get_bytes)
    assert params_seen and all(p.get("range") == "5d" for p in params_seen)


async def test_yahoo_bootstrap_uses_10y_when_thin(tmp_path):
    store = Store(tmp_path / "t.db")
    params_seen = []

    async def get_text(url, params=None, headers=None):
        params_seen.append(params or {})
        return YAHOO

    async def get_bytes(url, params=None, headers=None):
        raise AssertionError("unused")

    series = [CycleSeriesCfg(id="spx", name="SPX", unit="idx", yahoo="^GSPC")]
    await fetch_cycle(series, store, "k", get_text, get_bytes)
    assert params_seen and all(p.get("range") == "10y" for p in params_seen)
    assert store.points("cycle:spx") != {}
