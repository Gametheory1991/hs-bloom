"""Tests for collector.fetchers.z1_holdings (FRED Z.1 holdings by holder).

The ID expectations below are hardcoded verbatim from the verified research
(docs/holdings_by_holder_research.md, verified live 2026-10-05): any typo
in the fetcher's SERIES table fails here loudly rather than silently
fetching the wrong FRED series.
"""
from __future__ import annotations

from datetime import date

from collector.fetchers import z1_holdings
from collector.fetchers.z1_holdings import (
    EXTRA_SERIES,
    SERIES,
    fetch_series,
    fetch_z1_holdings,
    parse_fred_csv,
    store_key,
)
from collector.store import Store

# (asset, sector, fred_id) — verbatim from the research doc, sections 1a-1d, 1g.
EXPECTED_IDS = {
    ("ust", "fed"): "BOGZ1FL713061103Q",
    ("ust", "banks"): "BOGZ1FL763061100Q",
    ("ust", "mutual-funds"): "BOGZ1FL653061105Q",
    ("ust", "mmf"): "BOGZ1FL633061105Q",
    ("ust", "foreign"): "ROWTSEQ027S",
    ("ust", "priv-pension"): "BOGZ1FL573061105Q",
    ("ust", "sl-retire"): "BOGZ1FL223061143Q",
    ("ust", "life-ins"): "BOGZ1FL543061105Q",
    ("ust", "pc-ins"): "BOGZ1FL513061105Q",
    ("ust", "households"): "HNOTSAQ027S",
    ("ust", "nonfin-corp"): "TSABSNNCB",
    ("ust", "sl-govt"): "BOGZ1FL213061103Q",
    ("corp", "banks"): "BOGZ1FL763063005Q",
    ("corp", "mutual-funds"): "BOGZ1FL653063005Q",
    ("corp", "mmf"): "BOGZ1FL633063005Q",
    ("corp", "priv-pension"): "BOGZ1FL573063005Q",
    ("corp", "life-ins"): "BOGZ1FL543063005Q",
    ("corp", "pc-ins"): "BOGZ1FL513063005Q",
    ("corp", "foreign"): "ROWCBSQ027S",
    ("corp", "households"): "CFBABSHNO",
    ("corp", "nonfin-corp"): "BOGZ1FL103063065Q",
    ("corp", "sl-govt"): "SLGCORQ027S",
    ("corp", "sl-retire"): "BOGZ1FL223063045Q",
    ("agency", "fed"): "BOGZ1FL713061705Q",
    ("agency", "banks"): "BOGZ1FL763061705Q",
    ("agency", "mutual-funds"): "BOGZ1FL653061703Q",
    ("agency", "mmf"): "BOGZ1FL633061700Q",
    ("agency", "priv-pension"): "BOGZ1FL573061705Q",
    ("agency", "sl-retire"): "BOGZ1FL223061743Q",
    ("agency", "life-ins"): "BOGZ1FL543061705Q",
    ("agency", "pc-ins"): "BOGZ1FL513061705Q",
    ("agency", "foreign"): "ROWGBSQ027S",
    ("agency", "households"): "AGSEBSABSHNO",
    ("agency", "nonfin-corp"): "AGSEBSABSNNCB",
    ("agency", "sl-govt"): "SLGGBSQ027S",
    ("muni", "banks"): "BOGZ1FL763062005Q",
    ("muni", "mutual-funds"): "BOGZ1FL653062003Q",
    ("muni", "mmf"): "BOGZ1FL633062000Q",
    ("muni", "life-ins"): "BOGZ1FL543062005Q",
    ("muni", "pc-ins"): "BOGZ1FL513062005Q",
    ("muni", "foreign"): "ROWMLAQ027S",
    ("muni", "households"): "MSABSHNO",
    ("muni", "nonfin-corp"): "MSABSNNCB",
    ("muni", "sl-govt"): "SLGMLOQ027S",
    ("muni", "sl-retire"): "BOGZ1FL223062043Q",
}


def test_series_table_ids_match_research_verbatim():
    assert len(SERIES) == 45, f"expected 45 series (12+11+12+10), got {len(SERIES)}"
    actual = {(c.asset, c.sector): c.fred_id for c in SERIES}
    assert actual == EXPECTED_IDS


def test_extra_series_ids_verbatim():
    keys = {key: (fred_id, label) for key, fred_id, label in EXTRA_SERIES}
    assert keys["cycle:z1-ust-hedge-funds-net"][0] == "BOGZ1FL623061103Q"
    assert keys["cycle:fed-soma-weekly"][0] == "TREAST"


def test_parse_fred_csv_skips_dot_missing():
    text = "DATE,VALUE\n2026-03-31,4082700.0\n2026-06-30,.\n2026-09-30,4100000.0\n"
    pts = parse_fred_csv(text)
    assert pts == [(date(2026, 3, 31), 4082700.0), (date(2026, 9, 30), 4100000.0)]


def test_parse_fred_csv_sorts_and_raises_on_empty():
    text = "DATE,VALUE\n2026-06-30,2.0\n2026-03-31,1.0\n"
    assert parse_fred_csv(text) == [
        (date(2026, 3, 31), 1.0),
        (date(2026, 6, 30), 2.0),
    ]
    try:
        parse_fred_csv("DATE,VALUE\n2026-06-30,.\n")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError on all-missing CSV")


def test_store_key_format():
    cfg = next(c for c in SERIES if (c.asset, c.sector) == ("ust", "fed"))
    assert store_key(cfg) == "cycle:z1-ust-fed"
    cfg = next(c for c in SERIES if (c.asset, c.sector) == ("muni", "sl-retire"))
    assert store_key(cfg) == "cycle:z1-muni-sl-retire"


def test_hedge_fund_label_contains_net():
    _, _, label = next(
        row for row in EXTRA_SERIES if row[0] == "cycle:z1-ust-hedge-funds-net"
    )
    assert label == "Hedge funds — Treasury holdings NET of shorts (Z.1)"
    assert "NET" in label


async def test_fetch_series_passes_id_and_parses():
    seen = {}

    async def fake_get(url, params=None, headers=None):
        seen.update(params or {})
        return "DATE,VALUE\n2026-06-30,4082700.0\n"

    pts = await fetch_series("BOGZ1FL713061103Q", fake_get)
    assert seen["id"] == "BOGZ1FL713061103Q"
    assert pts == [(date(2026, 6, 30), 4082700.0)]


async def test_fetch_z1_holdings_upserts_two_series_no_network(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")

    async def fake_get(url, params=None, headers=None):
        fred_id = (params or {}).get("id")
        if fred_id == "BOGZ1FL713061103Q":
            return "DATE,VALUE\n2026-03-31,4082700.0\n2026-06-30,.\n"
        if fred_id == "TREAST":
            return "DATE,VALUE\n2026-09-23,4564161.0\n"
        raise RuntimeError("unexpected series")

    # Restrict to two series so the fake covers everything, and kill the
    # polite sleeps so the test stays fast.
    monkeypatch.setattr(
        z1_holdings,
        "SERIES",
        [
            next(c for c in SERIES if (c.asset, c.sector) == ("ust", "fed")),
        ],
    )
    monkeypatch.setattr(
        z1_holdings,
        "EXTRA_SERIES",
        [row for row in EXTRA_SERIES if row[0] == "cycle:fed-soma-weekly"],
    )
    monkeypatch.setattr(z1_holdings.asyncio, "sleep", lambda *a, **k: _noop())
    result = await fetch_z1_holdings(store, fake_get)

    assert result == "fred-z1"
    assert store.points("cycle:z1-ust-fed") == {date(2026, 3, 31): 4082700.0}
    assert store.points("cycle:fed-soma-weekly") == {date(2026, 9, 23): 4564161.0}
    doc = store.doc("z1_holdings")
    assert doc.source == "fred-z1"
    assert doc.payload["as_of"] == "2026-09-23"


async def test_fetch_z1_holdings_raises_on_bad_series(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")

    async def fake_get(url, params=None, headers=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        z1_holdings,
        "SERIES",
        [next(c for c in SERIES if (c.asset, c.sector) == ("ust", "fed"))],
    )
    monkeypatch.setattr(z1_holdings, "EXTRA_SERIES", [])
    monkeypatch.setattr(z1_holdings.asyncio, "sleep", lambda *a, **k: _noop())
    try:
        await fetch_z1_holdings(store, fake_get)
    except RuntimeError as exc:
        assert "BOGZ1FL713061103Q" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError when every series fails")


async def _noop():
    return None
