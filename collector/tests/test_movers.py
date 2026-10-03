"""Tests for the single-stock sigma movers job (batch 4).

The staging tree carries only batch files, so the test injects a stub
`collector.fetchers.yahoo` module (same pattern as test_cycle_yahoo.py) and
stubs asyncio.sleep to keep the suite fast.
"""
import asyncio
import sys
import types
from datetime import date, timedelta

import pytest

from collector.fetchers import movers
from collector.store import Store


def _closes(n_days, start=100.0, drift=0.0005, noise=0.004, jump=None, seed=7):
    """Synthetic daily closes with deterministic noise; jump=(day_index, pct)
    injects a shock."""
    import random
    rng = random.Random(seed)
    out = []
    px = start
    d0 = date(2026, 1, 5)
    for i in range(n_days):
        d = d0 + timedelta(days=i)
        px *= (1 + drift + rng.gauss(0, noise))
        if jump and i == jump[0]:
            px *= (1 + jump[1])
        out.append((d, round(px, 2)))
    return out


class _Quote:
    def __init__(self, closes):
        self.closes = closes


def _install_yahoo(monkeypatch, by_symbol):
    yahoo = types.ModuleType("collector.fetchers.yahoo")

    async def fetch_chart(symbol, get_text, range_="6mo"):
        if symbol not in by_symbol:
            raise ValueError(f"yahoo chart error: unknown symbol {symbol}")
        return _Quote(by_symbol[symbol])

    yahoo.fetch_chart = fetch_chart
    monkeypatch.setitem(sys.modules, "collector.fetchers.yahoo", yahoo)


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)


def _store():
    return Store(":memory:")


def test_zscore_moves_basic():
    closes = _closes(120, drift=0.0, noise=0.01)
    m = movers._zscore_moves(closes)
    assert m is not None
    assert set(m) == {"z5", "ret5", "z20", "ret20"}
    # pure noise, no drift -> z-scores small (a single random draw can still
    # reach ~2, so the bound is generous; the shock test proves sensitivity)
    assert abs(m["z5"]) < 2.5
    assert abs(m["z20"]) < 2.5


def test_zscore_moves_detects_shock():
    closes = _closes(120, jump=(119, 0.10))  # +10% on the last day
    m = movers._zscore_moves(closes)
    assert m is not None
    assert m["z5"] > 3.0  # a 10% daily shock is many sigma
    assert m["z20"] > 2.0


def test_zscore_moves_too_short():
    assert movers._zscore_moves(_closes(40)) is None


def test_fetch_movers_ranks_and_isolates(monkeypatch):
    by_symbol = {
        "AAA": _closes(120, jump=(119, 0.12)),   # big up shock
        "BBB": _closes(120, jump=(119, -0.12)),  # big down shock
        "CCC": _closes(120),                     # quiet
        # "DDD" missing entirely -> per-symbol isolation (skipped, not fatal)
    }
    _install_yahoo(monkeypatch, by_symbol)
    # shrink the universe to our four symbols for speed
    monkeypatch.setattr(movers, "INDEXES", [("spx", "S&P 500", "sp500.txt")])
    monkeypatch.setattr(movers, "_load_tickers",
                        lambda fn: ["AAA", "BBB", "CCC", "DDD"])

    async def fake_get_text(url, params=None):
        raise AssertionError("should not be called")

    store = _store()
    got = asyncio.run(movers.fetch_movers(store, fake_get_text,
                                          today=date(2026, 10, 3)))
    assert got == "movers"
    doc = store.doc("movers")
    assert doc is not None
    idx = doc.payload["indexes"]["spx"]
    assert idx["n_scored"] == 3  # DDD skipped
    assert idx["n_universe"] == 4
    up5 = idx["win5d"]["up"]
    dn5 = idx["win5d"]["down"]
    assert up5[0]["symbol"] == "AAA" and up5[0]["z"] > 0
    assert dn5[0]["symbol"] == "BBB" and dn5[0]["z"] < 0
    # entries carry z and return pct
    assert {"symbol", "z", "ret_pct"} <= set(up5[0])
    # top-10 cap respected
    assert len(up5) <= 10 and len(dn5) <= 10


def test_ticker_files_vendored():
    spx = movers._load_tickers("sp500.txt")
    ndx = movers._load_tickers("ndx100.txt")
    assert len(spx) >= 300, f"only {len(spx)} SPX tickers"
    assert len(ndx) >= 90, f"only {len(ndx)} NDX tickers"
    assert "AAPL" in spx and "NVDA" in ndx
