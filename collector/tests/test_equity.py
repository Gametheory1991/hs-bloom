from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from collector.config import IndexCfg
from collector.fetchers.equity import fetch_equity
from collector.store import Store

YAHOO_SPX = (Path(__file__).parent / "fixtures" / "yahoo_spx.json").read_text()


async def fake_get(url, params=None, headers=None):
    return YAHOO_SPX


def cfg(symbol, yahoo="^GSPC"):
    return IndexCfg(symbol=symbol, name=symbol, yahoo=yahoo)


async def test_yahoo_quote_and_history_written(tmp_path):
    store = Store(tmp_path / "t.db")
    label = await fetch_equity([cfg("SPX")], store, fake_get)
    assert label == "yahoo"
    q = store.doc("equity_quotes").payload["SPX"]
    assert q["source"] == "yahoo" and q["last"] == 6240.10  # fresher-than-close quote
    assert store.points("idx:SPX")  # daily closes persisted alongside the quote


async def test_failed_symbol_keeps_last_known_quote(tmp_path):
    store = Store(tmp_path / "t.db")
    store.put_doc("equity_quotes", {"HSI": {
        "last": 24000.0, "ts": "2026-07-07T00:00:00Z", "source": "yahoo", "delayed": True,
    }}, source="yahoo")

    # HSI's only source fails this run; SPX succeeds
    async def get_spx_only(url, params=None, headers=None):
        if "%5EGSPC" in url:
            return YAHOO_SPX
        raise RuntimeError("yahoo down")

    label = await fetch_equity(
        [cfg("SPX"), cfg("HSI", yahoo="^HSI")], store, get_spx_only,
    )
    assert label == "yahoo"
    quotes = store.doc("equity_quotes").payload
    assert quotes["HSI"]["last"] == 24000.0          # carried forward, not deleted
    assert quotes["HSI"]["ts"] == "2026-07-07T00:00:00Z"  # old ts => visibly stale
    assert quotes["SPX"]["last"] == 6240.10


async def test_all_failed_raises_even_with_previous_doc(tmp_path):
    store = Store(tmp_path / "t.db")
    store.put_doc("equity_quotes", {"SPX": {
        "last": 6000.0, "ts": "2026-07-01T00:00:00Z", "source": "yahoo", "delayed": True,
    }}, source="yahoo")

    async def failing_get(url, params=None, headers=None):
        raise RuntimeError("yahoo down")

    try:
        await fetch_equity([cfg("SPX")], store, failing_get)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "all equity symbols failed" in str(exc)


async def test_removed_symbol_dropped_from_quotes(tmp_path):
    store = Store(tmp_path / "t.db")
    store.put_doc("equity_quotes", {
        "SPX": {"last": 6000.0, "ts": "2026-07-01T00:00:00Z", "source": "yahoo", "delayed": True},
        "RUT": {"last": 2200.0, "ts": "2026-07-01T00:00:00Z", "source": "yahoo", "delayed": True},
    }, source="yahoo")
    label = await fetch_equity([cfg("SPX")], store, fake_get)
    assert label == "yahoo"
    quotes = store.doc("equity_quotes").payload
    assert "RUT" not in quotes           # removed from config -> dropped
    assert quotes["SPX"]["last"] == 6240.10


async def test_yahoo_hits_the_expected_endpoint(tmp_path):
    store = Store(tmp_path / "t.db")

    async def get_yahoo(url, params=None, headers=None):
        assert urlparse(url).netloc == "query1.finance.yahoo.com"
        # no per-fetcher UA override: http.get_text applies the honest default
        assert headers is None
        return YAHOO_SPX

    label = await fetch_equity([cfg("SPX")], store, get_yahoo)
    assert label == "yahoo"
    q = store.doc("equity_quotes").payload["SPX"]
    assert q["source"] == "yahoo" and q["last"] == 6240.10


async def test_no_source_configured_symbol_reported(tmp_path):
    store = Store(tmp_path / "t.db")
    bare = IndexCfg(symbol="XXX", name="No Source", yahoo=None)
    label = await fetch_equity([cfg("SPX"), bare], store, fake_get)
    assert label == "yahoo"  # SPX still fetched; XXX simply has nowhere to go
    assert "XXX" not in store.doc("equity_quotes").payload


async def test_one_call_per_symbol_when_yahoo_succeeds(tmp_path):
    store = Store(tmp_path / "t.db")
    calls = []

    async def counting_get(url, params=None, headers=None):
        calls.append(url)
        return YAHOO_SPX

    label = await fetch_equity([cfg("SPX")], store, counting_get)
    assert label == "yahoo"
    assert len(calls) == 1


async def test_yahoo_incremental_5d_when_history_populated(tmp_path):
    store = Store(tmp_path / "t.db")
    ranges = []

    async def counting_get(url, params=None, headers=None):
        ranges.append((params or {}).get("range"))
        return YAHOO_SPX

    from datetime import timedelta

    base = date(2026, 1, 2)
    store.upsert_points("idx:SPX", [(base + timedelta(days=i), 6000.0) for i in range(50)])
    label = await fetch_equity([cfg("SPX")], store, counting_get)
    assert label == "yahoo"
    assert ranges == ["5d"]  # incremental, not the full-year pull


async def test_yahoo_full_year_bootstrap_when_history_thin(tmp_path):
    store = Store(tmp_path / "t.db")
    ranges = []

    async def counting_get(url, params=None, headers=None):
        ranges.append((params or {}).get("range"))
        return YAHOO_SPX

    label = await fetch_equity([cfg("SPX")], store, counting_get)
    assert label == "yahoo"
    assert ranges == ["1y"]  # full-history bootstrap on thin/missing history
