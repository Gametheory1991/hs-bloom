"""Tests for the cycle `yahoo:` single-symbol source (batch 3).

cycle.py imports eight source submodules; the staging tree only carries the
files this batch touches, so the test injects lightweight stubs for the
modules it doesn't exercise and a fake `yahoo` carrying a chart fixture.
"""
import sys
import types
from datetime import date

import pytest

from collector.config import CycleSeriesCfg


def _install_stubs(monkeypatch):
    pkg = types.ModuleType("collector.fetchers")

    class Quote:
        def __init__(self, closes):
            self.closes = closes

    yahoo = types.ModuleType("collector.fetchers.yahoo")
    yahoo.CALLS = []

    async def fetch_chart(symbol, get_text, range_="1y"):
        yahoo.CALLS.append((symbol, range_))
        text = await get_text(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}")
        import json
        from datetime import datetime, timezone
        r = json.loads(text)["chart"]["result"][0]
        ts = r["timestamp"]
        closes = r["indicators"]["quote"][0]["close"]
        pts = [
            (datetime.fromtimestamp(t, tz=timezone.utc).date(), c)
            for t, c in zip(ts, closes) if c is not None
        ]
        return Quote(pts)

    def ratio_points(a, b):
        bd = dict(b)
        return [(d, va / bd[d]) for d, va in a if d in bd and bd[d]]

    yahoo.fetch_chart = fetch_chart
    yahoo.ratio_points = ratio_points

    for name in ("aaii", "cboe", "cftc", "dbnomics", "fred", "oecd", "ofr"):
        monkeypatch.setitem(sys.modules, f"collector.fetchers.{name}",
                            types.ModuleType(f"collector.fetchers.{name}"))
    monkeypatch.setitem(sys.modules, "collector.fetchers.yahoo", yahoo)
    # force re-import of cycle with the stubs in place
    sys.modules.pop("collector.fetchers.cycle", None)
    import collector.fetchers.cycle as cycle_mod
    return cycle_mod, yahoo


CHART_JSON = """{"chart":{"result":[{"timestamp":[1727740800,1727827200],
"indicators":{"quote":[{"close":[84.5,85.1]}]}}]}}"""


def _cfg(**kw):
    base = dict(id="mbb-us", name="MBB", unit="px")
    base.update(kw)
    return CycleSeriesCfg(**base)


def test_yahoo_single_symbol(monkeypatch):
    cycle_mod, yahoo = _install_stubs(monkeypatch)

    async def get_text(url, params=None, headers=None):
        assert "MBB" in url
        return CHART_JSON

    import asyncio
    pts = asyncio.run(cycle_mod._fetch_one(_cfg(yahoo="MBB"), "key", get_text, None, None))
    assert pts == [(date(2024, 10, 1), 84.5), (date(2024, 10, 2), 85.1)]
    assert yahoo.CALLS and yahoo.CALLS[0][0] == "MBB"


def test_yahoo_ratio_still_works(monkeypatch):
    cycle_mod, _ = _install_stubs(monkeypatch)

    async def get_text(url, params=None, headers=None):
        return CHART_JSON

    import asyncio
    pts = asyncio.run(cycle_mod._fetch_one(
        _cfg(id="mbb-shy", yahoo_ratio=["MBB", "SHY"]), "key", get_text, None, None))
    assert pts == [(date(2024, 10, 1), 1.0), (date(2024, 10, 2), 1.0)]


def test_no_source_raises(monkeypatch):
    cycle_mod, _ = _install_stubs(monkeypatch)

    async def get_text(url, params=None, headers=None):
        raise AssertionError("no HTTP expected")

    import asyncio
    with pytest.raises(ValueError, match="no source"):
        asyncio.run(cycle_mod._fetch_one(_cfg(), "key", get_text, None, None))
