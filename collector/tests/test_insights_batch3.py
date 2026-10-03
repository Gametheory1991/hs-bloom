"""Tests for the batch-3 insights additions: MBS note, xcorr/gse payloads."""
from datetime import date, timedelta

from collector.config import Config
from collector.insights import _fmt_value, _mbs_note, _series_catalog


class FakeStore:
    def __init__(self, series=None):
        self._series = series or {}

    def points(self, key, since=None):
        return dict(self._series.get(key, {}))

    def doc(self, key):
        return None


def _monthly(n, start_val, drift, end=date(2026, 8, 31)):
    """n month-ends ending at `end`, values rising by drift per month."""
    import calendar
    out, d = {}, end
    vals = [start_val + drift * i for i in range(n)]
    for v in reversed(vals):
        out[d] = v
        prev = date(d.year, d.month, 1) - timedelta(days=1)
        d = date(prev.year, prev.month, calendar.monthrange(prev.year, prev.month)[1])
    return out


def _empty_cfg():
    from types import SimpleNamespace
    refs = SimpleNamespace(aave=[], llama_chart=[], pendle=[], funding=[])
    defi = SimpleNamespace(asset="USDC", strategies=[], chains=[],
                           midnight_chains=[], token_symbols={})
    return Config(
        db_path=":memory:", calendar_url="", max_news=0, cadences={},
        indexes=[], bonds=[], cb_rates=[], series=[], cycle_series=[],
        cycle_tabs=[], calendar_map=[], feeds=[], zyfai_base="",
        midnight_base="", defi=defi, refs=refs, ofr_series=[], cftc_pos=[],
        tic=None, thirteenf=None, auctions=None, dealer=[],
    )


def test_mbs_note_none_without_gse():
    assert _mbs_note(FakeStore(), None) is None


def test_mbs_note_stress_when_shrinking():
    gse_payload = {
        "fannie_retained": {"date": "2026-08-31", "value_usd_m": 172596.0},
        "freddie_retained": {"date": "2026-08-31", "value_usd_m": 138447.0},
    }
    series = {
        "gse:fannie-retained": _monthly(6, 180000.0, -1500.0),
        "gse:freddie-retained": _monthly(6, 145000.0, -1100.0),
        # MBB falling ~3% over 3m
        "cycle:mbb-us": {date(2026, 8, 31): 97.0, date(2026, 5, 29): 100.0},
    }
    note = _mbs_note(FakeStore(series), gse_payload)
    assert note is not None
    assert note["combined_retained_usd_m"] == 172596.0 + 138447.0
    assert note["combined_chg_3m_m"] < 0
    assert note["mbb_3m_pct"] < 0
    assert "stress" in note
    assert "shrinking" in note["stress"]


def test_mbs_note_no_stress_when_growing():
    gse_payload = {
        "fannie_retained": {"date": "2026-08-31", "value_usd_m": 180000.0},
        "freddie_retained": {"date": "2026-08-31", "value_usd_m": 145000.0},
    }
    series = {
        "gse:fannie-retained": _monthly(6, 170000.0, 2000.0),
        "gse:freddie-retained": _monthly(6, 140000.0, 1000.0),
    }
    note = _mbs_note(FakeStore(series), gse_payload)
    assert note is not None
    assert note["combined_chg_3m_m"] > 0
    assert "stress" not in note


def test_series_catalog_includes_gse():
    ids = {s.store_id for s in _series_catalog(_empty_cfg())}
    assert "gse:fannie-retained" in ids
    assert "gse:freddie-retained" in ids
    assert "gse:freddie-agency" in ids


def test_fmt_value_millions():
    assert _fmt_value(172596.0, "$m") == "$172.6B"


def test_market_structure_note_none_when_empty():
    from collector.insights import _market_structure_note
    assert _market_structure_note(FakeStore()) is None


def test_market_structure_note_formats_values():
    from collector.insights import _market_structure_note, _struct_bullet
    store = FakeStore({
        "cycle:finra-margin-debit": {date(2026, 8, 1): 1453832.0},
        "cycle:trace-ust-par": {date(2026, 10, 1): 1743.8},
        "cycle:trace-corp-par": {date(2026, 8, 31): 1130155.0},
        "cycle:finra-short-total": {date(2026, 9, 15): 12.5e9},
    })
    note = _market_structure_note(store)
    assert note is not None
    bullet = _struct_bullet(note)
    assert "margin debt $1.45T (2026-08)" in bullet
    assert "Treasury TRACE $1,744B/d (10-01)" in bullet
    assert "corp TRACE $1.13T/mo (2026-08)" in bullet
    assert "short interest 12.5B sh (09-15)" in bullet
