"""Tests for the four new data fetchers: OFR, CFTC positioning, TIC, 13F."""
from datetime import date
from pathlib import Path

from collector.fetchers.cftc_pos import COLUMNS, DATASETS, fetch_contract_net, parse_net
from collector.fetchers.ofr import fetch_mnemonic, parse_timeseries
from collector.fetchers.thirteenf import fetch_filer, parse_holdings_xml
from collector.fetchers.tic import parse_table, slug

FIX = Path(__file__).parent / "fixtures"


def test_ofr_parse_timeseries_skips_bad_row():
    pts = parse_timeseries((FIX / "ofr_timeseries.json").read_text())
    assert pts == [
        (date(2024, 3, 31), 9490000000000.0),
        (date(2024, 6, 30), 9620000000000.0),
        (date(2024, 9, 30), 9710000000000.0),
    ]


async def test_ofr_fetch_mnemonic_hits_timeseries_endpoint():
    seen = {}

    async def fake_get(url, params=None, headers=None):
        seen["url"] = url
        seen["params"] = params
        return (FIX / "ofr_timeseries.json").read_text()

    pts = await fetch_mnemonic("FPF-ALLQHF_GAV_SUM", fake_get)
    assert seen["url"] == "https://data.financialresearch.gov/hf/v1/series/timeseries"
    assert seen["params"] == {"mnemonic": "FPF-ALLQHF_GAV_SUM"}
    assert len(pts) == 3


def test_cftc_pos_column_map_covers_configured_groups():
    for dataset, groups in [("tff", ["lev_money", "asset_mgr", "dealer"]),
                            ("cit", ["cit"]),
                            ("disagg", ["m_money", "swap", "prod_merc"])]:
        assert dataset in DATASETS
        for g in groups:
            assert (dataset, g) in COLUMNS


def test_cftc_pos_parse_net_and_bad_row_skip():
    text = (FIX / "cftc_pos_tff.json").read_text()
    pts = parse_net(text, "lev_money_positions_long", "lev_money_positions_short")
    assert pts == [
        (date(2026, 9, 22), 38476.0 - 3693.0),
        (date(2026, 9, 29), 39000.0 - 4000.0),
        # third row lacks position columns -> skipped
    ]


async def test_cftc_pos_fetch_contract_net_filters_by_code():
    seen = {}

    async def fake_get(url, params=None, headers=None):
        seen["url"] = url
        seen["params"] = params
        return (FIX / "cftc_pos_tff.json").read_text()

    pts = await fetch_contract_net(
        "udgc-27he", "13874A",
        "lev_money_positions_long", "lev_money_positions_short", fake_get,
    )
    assert seen["url"] == "https://publicreporting.cftc.gov/resource/udgc-27he.json"
    assert seen["params"]["cftc_contract_market_code"] == "13874A"
    assert seen["params"]["$limit"] == "5000"
    assert len(pts) == 2


def test_tic_parse_table_countries_and_grand_total():
    text = (FIX / "tic_table5.txt").read_text()
    data = parse_table(text, ["Japan", "China, Mainland"])
    assert set(data) == {"Japan", "China, Mainland", "Grand Total"}
    assert data["Japan"] == [
        (date(2026, 7, 1), 1103.9),
        (date(2026, 6, 1), 1116.7),
        (date(2026, 5, 1), 1143.1),
    ]
    # "n.a." cell skipped
    assert data["China, Mainland"] == [
        (date(2026, 7, 1), 618.0),
        (date(2026, 6, 1), 633.4),
    ]
    assert data["Grand Total"][0] == (date(2026, 7, 1), 9248.1)
    # "All Other" not requested -> excluded; "Of Which" rows excluded
    assert "All Other" not in data
    assert slug("China, Mainland") == "china_mainland"
    assert slug("Grand Total") == "grand_total"


def test_thirteenf_parse_holdings_aggregates_issuer_and_skips_bad_row():
    holdings = parse_holdings_xml((FIX / "thirteenf_infotable.xml").read_text())
    assert len(holdings) == 2  # bad row skipped, Apple aggregated
    amex, apple = holdings
    assert amex["issuer"] == "AMERICAN EXPRESS CO"
    assert amex["value_usd"] == 50419898 * 1000
    assert apple["issuer"] == "APPLE INC"
    assert apple["value_usd"] == (23341172 + 17808079) * 1000
    assert apple["cusip"] == "037833100"  # CUSIP of the largest row kept


async def test_thirteenf_fetch_filer_flow(monkeypatch):
    subs = (FIX / "thirteenf_submissions.json").read_text()
    index = (FIX / "thirteenf_index.json").read_text()
    xml = (FIX / "thirteenf_infotable.xml").read_text()
    calls = []

    async def fake_get(url, params=None, headers=None):
        calls.append(url)
        assert headers and "User-Agent" in headers
        if "data.sec.gov" in url:
            assert "CIK0001067983" in url  # 10-digit padding
            return subs
        if "index.json" in url:
            assert "1067983/000119312526352200" in url  # unpadded CIK, dashes stripped
            return index
        assert url.endswith("/56757.xml")  # holdings doc, not primary_doc.xml
        return xml

    import collector.fetchers.thirteenf as mod

    async def _noop(_):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _noop)

    payload = await fetch_filer("0001067983", "Berkshire Hathaway", "test-ua/1.0 contact x@y.z", fake_get)
    assert payload["name"] == "Berkshire Hathaway"
    assert payload["cik"] == "0001067983"
    assert payload["filing_date"] == "2026-08-14"  # latest 13F-HR, not the 10-Q
    assert len(payload["holdings"]) == 2
    assert len(calls) == 3
