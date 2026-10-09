from datetime import date
from pathlib import Path

from collector.config import BondCfg, CbRateCfg
from collector.fetchers.bonds import fetch_bonds
from collector.store import Store

FIX = Path(__file__).parent / "fixtures"
BUNDESBANK_CSV = (FIX / "bundesbank_10y.csv").read_text()
FRED_JSON = (FIX / "fred_dgs10.json").read_text()
ECB_CSV = (FIX / "ecb_3m.csv").read_text()


async def fake_get(url, params=None, headers=None):
    if "stlouisfed" in url:
        return FRED_JSON
    if "bundesbank" in url:
        return BUNDESBANK_CSV
    if "data-api.ecb" in url:
        return ECB_CSV
    raise RuntimeError(f"unexpected url: {url}")


def make_cfg(**kw):
    base = dict(
        country="DE", tenor="10Y", fred=None,
        bundesbank="D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A",
        ecb=None,
    )
    return BondCfg(**{**base, **kw})


async def run(bonds, store, cb_rates=(), get=fake_get):
    return await fetch_bonds(list(bonds), list(cb_rates), store, get, fred_api_key="k")


async def test_fred_bond(tmp_path):
    store = Store(tmp_path / "t.db")
    cfg = make_cfg(country="US", fred="DGS10", bundesbank=None)
    label = await run([cfg], store)
    assert label == "fred"
    q = store.doc("bond_quotes").payload["US10Y"]
    assert q["yield_pct"] == 4.12 and q["source"] == "fred"
    assert q["country"] == "US" and q["tenor"] == "10Y"
    assert store.points("yield:US10Y")[date(2026, 7, 8)] == 4.12


async def test_bundesbank_bond(tmp_path):
    store = Store(tmp_path / "t.db")
    label = await run([make_cfg()], store)
    assert label == "bundesbank"
    q = store.doc("bond_quotes").payload["DE10Y"]
    assert q["yield_pct"] == 3.17 and q["source"] == "bundesbank"
    assert store.points("yield:DE10Y")[date(2026, 7, 8)] == 3.17


async def test_ecb_bond(tmp_path):
    store = Store(tmp_path / "t.db")
    cfg = make_cfg(tenor="3M", bundesbank=None, ecb="YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M")
    label = await run([cfg], store)
    assert label == "ecb"
    q = store.doc("bond_quotes").payload["DE3M"]
    assert q["yield_pct"] == 2.3337121399 and q["source"] == "ecb"
    assert store.points("yield:DE3M")[date(2026, 7, 20)] == 2.3299925919


async def test_cb_rates_written_alongside_bonds(tmp_path):
    store = Store(tmp_path / "t.db")
    cbs = [CbRateCfg(country="US", label="FED", fred="DFEDTARU")]
    label = await run([make_cfg()], store, cb_rates=cbs)
    assert label == "bundesbank+fred"
    quotes = store.doc("bond_quotes").payload
    fed = quotes["USCB"]
    assert fed["label"] == "FED" and fed["country"] == "US"
    assert fed["yield_pct"] == 4.12 and "tenor" not in fed  # fred_dgs10 fixture value
    assert store.points("cb:US")[date(2026, 7, 8)] == 4.12


async def test_failed_instrument_keeps_last_known_but_removed_dropped(tmp_path):
    store = Store(tmp_path / "t.db")
    store.put_doc("bond_quotes", {
        "DE10Y": {"country": "DE", "tenor": "10Y", "yield_pct": 2.50,
                  "ts": "2026-07-01T00:00:00Z", "source": "bundesbank"},
        "IT10Y": {"country": "IT", "tenor": "10Y", "yield_pct": 3.90,
                  "ts": "2026-07-01T00:00:00Z", "source": "yahoo"},
        "DE": {"tenor": "10Y", "yield_pct": 4.60,  # pre-matrix key: must be dropped
               "ts": "2026-07-01T00:00:00Z", "source": "bundesbank"},
    }, source="bundesbank")

    async def de_fails(url, params=None, headers=None):
        if "stlouisfed" in url:
            return FRED_JSON
        raise RuntimeError("bundesbank down")

    # config now has US (works via fred) and DE (fails); IT no longer configured
    bonds = [make_cfg(country="US", fred="DGS10", bundesbank=None), make_cfg()]
    label = await run(bonds, store, get=de_fails)
    assert label == "fred"
    quotes = store.doc("bond_quotes").payload
    assert quotes["DE10Y"]["yield_pct"] == 2.50   # carried forward, visibly stale via old ts
    assert quotes["US10Y"]["yield_pct"] == 4.12   # fresh
    assert "IT10Y" not in quotes                  # removed from config -> dropped
    assert "DE" not in quotes                     # old-shape key -> dropped


async def test_all_failed_raises(tmp_path):
    store = Store(tmp_path / "t.db")

    async def failing_get(url, params=None, headers=None):
        raise RuntimeError("down")

    try:
        await run([make_cfg()], store, get=failing_get)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "all bonds failed" in str(exc)


async def test_shared_fred_id_fetched_once_and_fanned_out(tmp_path):
    store = Store(tmp_path / "t.db")
    calls = []

    async def counting_get(url, params=None, headers=None):
        calls.append(url)
        return FRED_JSON

    # DE/FR/IT policy rates all share ECBDFR: one upstream fetch, three rows
    cbs = [CbRateCfg(country=c, label="ECB", fred="ECBDFR")
           for c in ("DE", "FR", "IT")]
    label = await run([], store, cb_rates=cbs, get=counting_get)
    assert label == "fred"
    fred_calls = [u for u in calls if "stlouisfed" in u]
    assert len(fred_calls) == 1  # not one fetch per row
    quotes = store.doc("bond_quotes").payload
    for c in ("DE", "FR", "IT"):
        assert quotes[f"{c}CB"]["yield_pct"] == 4.12
        assert store.points(f"cb:{c}")  # each row got the shared series


async def test_fresh_ids_skipped_on_second_run(tmp_path):
    store = Store(tmp_path / "t.db")
    label = await run([make_cfg(country="US", fred="DGS10", bundesbank=None)], store)
    assert label == "fred"

    async def no_network(url, params=None, headers=None):
        raise AssertionError("no HTTP expected on a fresh run")

    # second run immediately after: id already pulled, no re-fetch
    label = await run([make_cfg(country="US", fred="DGS10", bundesbank=None)],
                      store, get=no_network)
    assert label == "bonds (fresh)"
    assert store.doc("bond_quotes").payload["US10Y"]["yield_pct"] == 4.12
