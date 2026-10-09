from datetime import date
from pathlib import Path

from collector.config import SeriesCfg
from collector.fetchers.fred import fetch_macro_history, fetch_series, parse_observations
from collector.store import Store

FIXTURE = (Path(__file__).parent / "fixtures" / "fred_dgs10.json").read_text()


def test_parse_observations_skips_missing_values():
    pts = parse_observations(FIXTURE)
    assert pts == [(date(2026, 7, 6), 4.15), (date(2026, 7, 8), 4.12)]


async def test_fetch_series_passes_key_and_id():
    seen = {}

    async def fake_get(url, params=None):
        seen.update(params)
        return FIXTURE

    pts = await fetch_series("DGS10", "test-key", fake_get)
    assert seen["series_id"] == "DGS10"
    assert seen["api_key"] == "test-key"
    assert seen["file_type"] == "json"
    assert len(pts) == 2


async def test_fetch_macro_history_writes_all_series(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [
        SeriesCfg(id="us-10y", name="US 10Y", fred="DGS10", unit="%", transform="none"),
        SeriesCfg(id="us-cpi", name="US CPI", fred="CPIAUCSL", unit="%", transform="yoy"),
    ]

    async def fake_get(url, params=None):
        return FIXTURE

    label = await fetch_macro_history(series, store, "test-key", fake_get)
    assert label == "fred"
    assert store.points("macro:us-10y") != {}
    assert store.points("macro:us-cpi") != {}


async def test_fetch_macro_history_isolates_bad_series(tmp_path):
    store = Store(tmp_path / "t.db")
    series = [
        SeriesCfg(id="bad", name="Bad", fred="NOPE", unit="%", transform="none"),
        SeriesCfg(id="good", name="Good", fred="DGS10", unit="%", transform="none"),
    ]

    async def fake_get(url, params=None):
        if params["series_id"] == "NOPE":
            raise RuntimeError("HTTP 400 for https://api.stlouisfed.org/fred/series/observations")
        return FIXTURE

    try:
        await fetch_macro_history(series, store, "k", fake_get)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "bad:" in str(exc) and "1/2" in str(exc)
    assert store.points("macro:good") != {}  # good series still written


async def test_fetch_macro_history_fanout_cycle(tmp_path):
    """One FRED pull feeds both macro:<id> and the fanout cycle ids."""
    store = Store(tmp_path / "t.db")
    series = [
        SeriesCfg(id="us-2y-yield", name="US 2Y", fred="DGS2", unit="%",
                  transform="none", fanout_cycle=["us2y"]),
        SeriesCfg(id="us-cpi", name="US CPI", fred="CPIAUCSL", unit="%",
                  transform="yoy"),
    ]
    calls = []

    async def fake_get(url, params=None):
        calls.append(params["series_id"])
        return FIXTURE

    label = await fetch_macro_history(series, store, "test-key", fake_get)
    assert label == "fred"
    assert calls == ["DGS2", "CPIAUCSL"]  # exactly one pull per FRED id
    macro_pts = store.points("macro:us-2y-yield")
    assert macro_pts != {}
    assert store.points("cycle:us2y") == macro_pts  # fanned out, identical
    assert store.points("cycle:us-cpi") == {}  # no fanout configured


async def test_fetch_macro_history_fanout_isolated_on_bad_series(tmp_path):
    """A failed pull fans out nothing; the good series still fans out."""
    store = Store(tmp_path / "t.db")
    series = [
        SeriesCfg(id="bad", name="Bad", fred="NOPE", unit="%",
                  transform="none", fanout_cycle=["bad-cycle"]),
        SeriesCfg(id="good", name="Good", fred="DGS10", unit="%",
                  transform="none", fanout_cycle=["good-cycle"]),
    ]

    async def fake_get(url, params=None):
        if params["series_id"] == "NOPE":
            raise RuntimeError("HTTP 400")
        return FIXTURE

    try:
        await fetch_macro_history(series, store, "k", fake_get)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "bad:" in str(exc)
    assert store.points("cycle:bad-cycle") == {}
    assert store.points("cycle:good-cycle") != {}
