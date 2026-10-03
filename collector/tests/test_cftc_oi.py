"""Tests for the CFTC legacy open-interest extension (batch 4).

fetchers/cftc.py is a staged replacement of the live-main file: the original
net-noncommercial path must keep working byte-for-byte, plus the new
open_interest_all path.
"""
import asyncio
import json
from datetime import date

import pytest

from collector.fetchers import cftc


def _payload(rows):
    return json.dumps([
        {"report_date_as_yyyy_mm_dd": d,
         "noncomm_positions_long_all": lo,
         "noncomm_positions_short_all": sh,
         "open_interest_all": oi}
        for d, lo, sh, oi in rows
    ])


def test_parse_reports_unchanged():
    text = _payload([("2026-09-29T00:00:00.000", "100", "60", "1000"),
                     ("2026-09-22T00:00:00.000", "90", "70", "900")])
    assert cftc.parse_reports(text) == [(date(2026, 9, 22), 20.0),
                                        (date(2026, 9, 29), 40.0)]


def test_parse_open_interest():
    text = _payload([("2026-09-29T00:00:00.000", "100", "60", "1000"),
                     ("2026-09-22T00:00:00.000", "90", "70", "900"),
                     ("2026-09-15T00:00:00.000", "80", "80", "oops")])  # bad row skipped
    assert cftc.parse_open_interest(text) == [(date(2026, 9, 22), 900.0),
                                              (date(2026, 9, 29), 1000.0)]


def test_parse_open_interest_empty_raises():
    with pytest.raises(ValueError):
        cftc.parse_open_interest("[]")


def test_fetch_open_interest_selects_oi_column():
    seen = {}

    async def fake_get_text(url, params=None):
        seen.update(params or {})
        return _payload([("2026-09-29T00:00:00.000", "1", "2", "777")])

    pts = asyncio.run(cftc.fetch_open_interest("043602", fake_get_text))
    assert pts == [(date(2026, 9, 29), 777.0)]
    assert "open_interest_all" in seen["$select"]
    assert "noncomm_positions_long_all" not in seen["$select"]
    assert seen["cftc_contract_market_code"] == "043602"


def test_cycle_dispatch_cftc_oi(monkeypatch):
    """cycle.py routes cfg.cftc_oi to cftc.fetch_open_interest."""
    import sys
    import types
    from datetime import date as _date

    from collector.config import CycleSeriesCfg

    for name in ("aaii", "cboe", "dbnomics", "fred", "oecd", "ofr", "yahoo"):
        monkeypatch.setitem(sys.modules, f"collector.fetchers.{name}",
                            types.ModuleType(f"collector.fetchers.{name}"))

    calls = []

    async def fake_oi(code, get_text):
        calls.append(code)
        return [(_date(2026, 9, 29), 123.0)]

    async def fake_net(code, get_text):
        raise AssertionError("net path must not be called")

    cftc_stub = types.ModuleType("collector.fetchers.cftc")
    cftc_stub.fetch_open_interest = fake_oi
    cftc_stub.fetch_net_noncommercial = fake_net
    monkeypatch.setitem(sys.modules, "collector.fetchers.cftc", cftc_stub)
    # `from collector.fetchers import cftc` prefers the already-set package
    # attribute over sys.modules — patch it too.
    monkeypatch.setattr(sys.modules["collector.fetchers"], "cftc", cftc_stub)
    sys.modules.pop("collector.fetchers.cycle", None)
    import collector.fetchers.cycle as cyclemod

    async def fake_get_text(url, params=None):
        raise AssertionError("should not be called")

    cfg = CycleSeriesCfg(id="oi-ust-10y", name="10Y OI", unit="contracts",
                         cftc_oi="043602")
    pts = asyncio.run(cyclemod._fetch_one(cfg, "", fake_get_text, None,
                                          today=_date(2026, 10, 3)))
    assert pts == [(_date(2026, 9, 29), 123.0)]
    assert calls == ["043602"]
