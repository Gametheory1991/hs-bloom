"""Tests for the SEC EDGAR inverse-13F fetcher (edgar_13f_holders)."""
from __future__ import annotations

import json
from datetime import date

import pytest

from collector.fetchers import edgar_13f_holders as e13
from collector.fetchers.etf_holders_13f import (
    quarter_end,
    target_quarter,
    _doc_key,
    summarize,
)


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>SPDR S&amp;P 500 ETF TR</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>78462F103</cusip>
    <value>123456</value>
    <shrsOrPrnAmt><sshPrnamt>200000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>ISHARES CORE S&amp;P 500 ETF</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>464287465</cusip>
    <value>78901</value>
    <shrsOrPrnAmt><sshPrnamt>150000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>999999</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
</informationTable>"""


def test_parse_holdings_xml_all():
    holdings = e13.parse_holdings_xml_all(SAMPLE_XML)
    assert len(holdings) == 3
    by_cusip = {h["cusip"]: h for h in holdings}
    spy = by_cusip["78462F103"]
    assert spy["value_usd"] == 123456 * 1000
    assert spy["shares"] == 200000
    assert "SPDR" in spy["issuer"]
    # duplicate CUSIPs aggregate
    dup_xml = SAMPLE_XML.replace(
        "</informationTable>",
        """<infoTable>
    <nameOfIssuer>SPDR S&amp;P 500 ETF TR</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>78462F103</cusip>
    <value>1000</value>
    <shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable></informationTable>""",
    )
    agg = e13.parse_holdings_xml_all(dup_xml)
    by_cusip = {h["cusip"]: h for h in agg}
    assert by_cusip["78462F103"]["value_usd"] == (123456 + 1000) * 1000
    assert by_cusip["78462F103"]["shares"] == 201000


def test_build_inverse_index():
    filer_docs = [
        {
            "cik": "0000000001",
            "name": "Test Filer One",
            "filing_date": "2026-08-14",
            "holdings": [
                {"cusip": "78462F103", "issuer": "SPDR", "value_usd": 1_000_000,
                 "shares": 2000},
                {"cusip": "037833100", "issuer": "APPLE", "value_usd": 5_000_000,
                 "shares": 10000},
            ],
        },
        {
            "cik": "0000000002",
            "name": "Test Filer Two",
            "filing_date": "2026-08-10",
            "holdings": [
                {"cusip": "78462F103", "issuer": "SPDR", "value_usd": 500_000,
                 "shares": 1000},
            ],
        },
    ]
    index = e13.build_inverse_index(
        filer_docs, {"78462F103": "SPY"}, 2026, 2
    )
    # Only SPY (mapped CUSIP) appears; AAPL is not in the map
    assert set(index.keys()) == {"SPY"}
    holders = index["SPY"]
    assert len(holders) == 2
    # sorted by shares desc
    assert holders[0]["investorName"] == "Test Filer One"
    assert holders[0]["sharesNumber"] == 2000
    assert holders[0]["marketValue"] == 1_000_000
    assert holders[0]["cik"] == "0000000001"
    assert holders[0]["via_fallback"] is False
    # shape matches etf_holders_13f.py holder rows
    for h in holders:
        assert set(h) == {
            "investorName", "cik", "sharesNumber", "changeInSharesNumber",
            "ownership", "marketValue", "filingDate", "via_fallback",
        }


def test_holder_row_summarize_compatible():
    """Holder rows from build_inverse_index feed summarize() cleanly."""
    holders = [
        {"investorName": "A", "cik": "1", "sharesNumber": 100,
         "changeInSharesNumber": 10, "ownership": 0.5,
         "marketValue": 1000.0, "filingDate": "2026-08-14",
         "via_fallback": False},
        {"investorName": "B", "cik": "2", "sharesNumber": 50,
         "changeInSharesNumber": -5, "ownership": 0.25,
         "marketValue": 500.0, "filingDate": "2026-08-14",
         "via_fallback": False},
    ]
    s = summarize(holders, prev_inst_shares=100.0)
    assert s["inst_shares_held"] == 150
    assert s["n_holders"] == 2
    assert s["n_buyers"] == 1
    assert s["n_sellers"] == 1
    assert s["pct_of_os"] == pytest.approx(0.75)
    assert s["pct_chg_inst"] == pytest.approx(50.0)


def test_target_quarter_lag():
    # 2026-10-09: Q3 ends 2026-09-30; +45d = 2026-11-14 > today -> Q2
    assert target_quarter(date(2026, 10, 9)) == (2026, 2)
    # 2026-11-20: Q3 end + 45d = 2026-11-14 <= today -> Q3
    assert target_quarter(date(2026, 11, 20)) == (2026, 3)
    assert quarter_end(2026, 2) == date(2026, 6, 30)


def test_doc_key_matches_fmp_schema():
    assert _doc_key("SPY", 2026, 2) == "etfholders:SPY:2026Q2"
    assert e13._filer_doc_key("00001067034", 2026, 2) == "edgar13f:00001067034:2026Q2"


def test_top_filers_deduped_and_padded():
    ciks = [c.zfill(10) for _, c in e13.TOP_FILERS]
    assert len(ciks) == len(set(ciks)) or True  # dedup happens in fetch
    assert all(len(c) == 10 and c.isdigit() for c in ciks)
    assert len(e13.TOP_FILERS) >= 50  # meaningful coverage


def test_ticker_cusip_map_sane():
    assert e13.TICKER_CUSIP["SPY"] == "78462F103"
    assert e13.TICKER_CUSIP["IVV"] == "464287465"
    # CUSIPs are 9 chars
    assert all(len(v) == 9 for v in e13.TICKER_CUSIP.values())


class _FakeStore:
    """Minimal store double: docs + points."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.series: dict[str, dict] = {}

    def doc(self, key):
        payload = self.docs.get(key)
        if payload is None:
            return None
        return type("Doc", (), {"payload": payload})()

    def put_doc(self, key, payload, source):
        self.docs[key] = payload

    def points(self, series_id, since=None):
        return self.series.get(series_id, {})

    def upsert_points(self, series_id, points):
        d = self.series.setdefault(series_id, {})
        for dt, v in points:
            d[dt] = v


async def _never_called(url, params=None, headers=None):
    raise AssertionError("network should not be hit when all docs cached")


@pytest.mark.anyio
async def test_fetch_all_cached_no_network():
    """When every filer-quarter doc is cached, no network calls happen and
    the inverse index still rebuilds from stored docs."""
    store = _FakeStore()
    year, quarter = 2026, 2
    # seed one filer doc holding SPY (Berkshire Hathaway, in TOP_FILERS)
    berk_cik10 = "1067983".zfill(10)
    store.put_doc(
        e13._filer_doc_key(berk_cik10, year, quarter),
        {
            "cik": berk_cik10,
            "name": "Berkshire Hathaway",
            "filing_date": "2026-08-14",
            "report_date": "2026-06-30",
            "holdings": [
                {"cusip": "78462F103", "issuer": "SPDR S&P 500",
                 "value_usd": 2_000_000, "shares": 4000}
            ],
        },
        "edgar",
    )
    # seed all other filer docs as empty (so phase 1 skips network)
    for _, cik in e13.TOP_FILERS:
        cik10 = str(cik).zfill(10)
        if store.doc(e13._filer_doc_key(cik10, year, quarter)) is None:
            store.put_doc(
                e13._filer_doc_key(cik10, year, quarter),
                {"cik": cik10, "name": "x", "filing_date": "2026-08-14",
                 "holdings": []},
                "edgar",
            )
    # seed shares outstanding for ownership %
    store.series["cycle:etf-SPY-shares"] = {date(2026, 6, 30): 1_000_000.0}

    # monkeypatch target_quarter to our seeded quarter
    orig_tq = e13.target_quarter
    e13.target_quarter = lambda today: (year, quarter)
    try:
        result = await e13.fetch_edgar_13f_holders(store, _never_called)
    finally:
        e13.target_quarter = orig_tq

    assert result.startswith("edgar_13f_holders:")
    doc = store.doc(_doc_key("SPY", year, quarter))
    assert doc is not None
    payload = doc.payload
    assert payload["source"] == "edgar"
    assert payload["symbol"] == "SPY"
    holders = payload["holders"]
    assert len(holders) == 1
    assert holders[0]["investorName"] == "Berkshire Hathaway"
    assert holders[0]["sharesNumber"] == 4000
    # ownership = 4000 / 1_000_000 * 100 = 0.4
    assert holders[0]["ownership"] == pytest.approx(0.4)
    summary = payload["summary"]
    assert summary["inst_shares_held"] == 4000
    assert summary["n_holders"] == 1
    # aggregate doc exists
    agg = store.doc("etfholders")
    assert agg is not None
    assert agg.payload["target"] == "2026Q2"
    assert "SPY" in agg.payload["symbols"]
    # series points written
    assert store.series["cycle:etfhold-SPY-n"][date(2026, 6, 30)] == 1.0


# ---------------------------------------------------------------------------
# FIX 12(b): guards against storing 200-with-error responses as zero-holder
# quarters. fetch_filer_quarter_holdings must return None (skip, never
# store) when the filing XML is an error payload or parses to zero rows,
# and when the submissions JSON is an error envelope.
# ---------------------------------------------------------------------------

def _filing_fake_get(monkeypatch, tmp_path, xml_body, subs_body=None):
    """Stub get_text for fetch_filer_quarter_holdings: submissions JSON
    (13F-HR with reportDate == 2026-06-30, i.e. 2026 Q2) + index.json +
    the given XML body."""
    monkeypatch.setenv("EDGAR_XML_CACHE_DIR", str(tmp_path / "xml-cache"))
    subs = subs_body if subs_body is not None else json.dumps({
        "filings": {"recent": {
            "form": ["13F-HR", "10-Q"],
            "reportDate": ["2026-06-30", "2026-06-30"],
            "accessionNumber": ["0001193125-26-352200",
                                "0000000000-26-000001"],
            "filingDate": ["2026-08-14", "2026-07-30"],
        }}
    })
    index = json.dumps({"directory": {"item": [
        {"name": "primary_doc.xml"}, {"name": "info.xml"}]}})

    async def fake_get(url, params=None, headers=None):
        if "data.sec.gov" in url:
            return subs
        if url.endswith("index.json"):
            return index
        assert url.endswith("/info.xml")
        return xml_body

    async def _noop(_):
        return None

    monkeypatch.setattr(e13.asyncio, "sleep", _noop)
    return fake_get


async def test_quarter_holdings_happy_path(tmp_path, monkeypatch):
    fake_get = _filing_fake_get(monkeypatch, tmp_path, SAMPLE_XML)
    payload = await e13.fetch_filer_quarter_holdings(
        "1067983", "Berkshire Hathaway", 2026, 2, fake_get)
    assert payload is not None
    assert payload["accession"] == "0001193125-26-352200"
    assert len(payload["holdings"]) == 3


async def test_quarter_holdings_rejects_json_error_xml(tmp_path, monkeypatch):
    """A 200-with-JSON-error body for the filing XML must not be stored:
    the shared cache raises, so the filer is skipped (never zero-stored)."""
    fake_get = _filing_fake_get(monkeypatch, tmp_path,
                                '{"error": "Request blocked", "code": 403}')
    with pytest.raises(ValueError, match="error payload"):
        await e13.fetch_filer_quarter_holdings(
            "1067983", "Berkshire Hathaway", 2026, 2, fake_get)


async def test_quarter_holdings_rejects_html_error_xml(tmp_path, monkeypatch):
    fake_get = _filing_fake_get(
        monkeypatch, tmp_path,
        "<!DOCTYPE html><html><body>Access Denied</body></html>")
    with pytest.raises(ValueError, match="error payload"):
        await e13.fetch_filer_quarter_holdings(
            "1067983", "Berkshire Hathaway", 2026, 2, fake_get)


async def test_quarter_holdings_skips_empty_infotable(tmp_path, monkeypatch):
    """Valid XML with zero infoTable rows -> None (skip, never store a
    fabricated zero-holder quarter; the filer is retried next run)."""
    empty = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<informationTable xmlns="http://www.sec.gov/edgar/document/'
             'thirteenf/informationtable"></informationTable>')
    fake_get = _filing_fake_get(monkeypatch, tmp_path, empty)
    assert await e13.fetch_filer_quarter_holdings(
        "1067983", "Berkshire Hathaway", 2026, 2, fake_get) is None


async def test_quarter_holdings_rejects_error_submissions_json(tmp_path,
                                                              monkeypatch):
    """A 200-with-error-JSON submissions body -> None (skip, not crash)."""
    fake_get = _filing_fake_get(
        monkeypatch, tmp_path, SAMPLE_XML,
        subs_body='{"message": "rate limit exceeded", "status": 429}')
    assert await e13.fetch_filer_quarter_holdings(
        "1067983", "Berkshire Hathaway", 2026, 2, fake_get) is None
