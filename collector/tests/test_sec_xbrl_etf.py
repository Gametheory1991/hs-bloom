"""SEC XBRL ETF backfill tests: concept priority, dedup, AUM derivation."""
import json
from datetime import date

import pytest

from collector.fetchers.sec_xbrl_etf import (
    ETF_CIKS,
    extract_history,
    fetch_sec_xbrl_etf,
)
from collector.store import Store


def _facts(concepts):
    return {"facts": {"us-gaap": concepts}}


def _unit(pts):
    return {"units": {"shares": [{"end": e, "filed": f, "val": v, "form": "10-Q"}
                                for e, f, v in pts]}}


IBIT_PAYLOAD = _facts({
    "TemporaryEquitySharesOutstanding": _unit([
        ("2023-12-31", "2024-03-04", 4000),
        ("2023-12-31", "2024-05-08", 4000),   # dup period-end, later filing
        ("2024-03-31", "2024-05-08", 442400000),
    ]),
    "FairValueNetAssetLiability": {"units": {"USD": [
        {"end": "2023-12-31", "filed": "2024-03-04", "val": 100000, "form": "10-K"},
        {"end": "2024-03-31", "filed": "2024-05-08", "val": 17500000000, "form": "10-Q"},
    ]}},
    "NetAssetValuePerShare": {"units": {"USD/shares": [
        {"end": "2024-03-31", "filed": "2024-05-08", "val": 39.55, "form": "10-Q"},
    ]}},
})

# GBTC-style: no FairValueNetAssetLiability -> AUM derived from shares x NAV
GBTC_PAYLOAD = _facts({
    "SharesOutstanding": _unit([
        ("2024-12-31", "2025-02-25", 211920100),
        ("2025-12-31", "2026-02-25", 190000000),
    ]),
    "NetAssetValuePerShare": {"units": {"USD/shares": [
        {"end": "2024-12-31", "filed": "2025-02-25", "val": 85.0, "form": "10-K"},
        {"end": "2025-12-31", "filed": "2026-02-25", "val": 90.0, "form": "10-K"},
    ]}},
})


def test_extract_history_dedup():
    h = extract_history(IBIT_PAYLOAD)
    # dup 2023-12-31 keeps earliest-filed value (4000 either way)
    assert h["shares"] == [(date(2023, 12, 31), 4000.0),
                           (date(2024, 3, 31), 442400000.0)]
    assert h["aum"] == [(date(2023, 12, 31), 100000.0),
                        (date(2024, 3, 31), 17500000000.0)]
    assert h["nav"] == [(date(2024, 3, 31), 39.55)]


def test_extract_history_derives_aum():
    h = extract_history(GBTC_PAYLOAD)
    assert h["aum"] == [(date(2024, 12, 31), 211920100 * 85.0),
                        (date(2025, 12, 31), 190000000 * 90.0)]


def test_extract_history_empty():
    assert extract_history({"facts": {}}) == {"shares": [], "aum": [], "nav": []}


def test_cik_coverage():
    # key funds present; ETHA verified as 2000638
    assert ETF_CIKS["IBIT"] == "0001980994"
    assert ETF_CIKS["ETHA"] == "0002000638"
    assert ETF_CIKS["GBTC"] == "0001588489"
    assert ETF_CIKS["FBTC"] == "0001852317"
    assert all(len(c) == 10 and c.isdigit() for c in ETF_CIKS.values())


async def fake_get_text(url, headers=None, **kw):
    assert headers and "harrysugamakc@gmail.com" in headers["User-Agent"], \
        "SEC requires the contact UA"
    if "0001980994" in url:
        return json.dumps(IBIT_PAYLOAD)
    if "0001588489" in url:
        return json.dumps(GBTC_PAYLOAD)
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_fetch_sec_xbrl_etf(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    res = await fetch_sec_xbrl_etf(store, fake_get_text,
                                   "hs-bloom/1.0 contact harrysugamakc@gmail.com")
    assert "quarterly checkpoints" in res
    # same series the daily job writes
    assert store.points("cycle:etf-IBIT-shares")[date(2024, 3, 31)] == 442400000.0
    assert store.points("cycle:etf-IBIT-aum")[date(2023, 12, 31)] == 100000.0
    assert store.points("cycle:etf-GBTC-aum")[date(2024, 12, 31)] == pytest.approx(211920100 * 85.0)
    # failed CIKs don't fail the job
    assert "failed" in res
