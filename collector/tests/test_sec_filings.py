"""Tests for SEC filing fetchers: Form 4, 13D/13G, 8-K parsers."""
from __future__ import annotations

import pytest

from collector.fetchers.sec_form4 import (
    detect_clusters,
    parse_form4_xml,
    parse_master_index,
)
from collector.fetchers.sec_13dg import parse_13dg_text
from collector.fetchers.sec_8k import parse_8k_header, parse_8k_items


# ---------------------------------------------------------------------------
# Sample Form 4 XML (ownershipDocument)
# ---------------------------------------------------------------------------
SAMPLE_FORM4 = """<?xml version="1.0"?>
<ownershipDocument xmlns="http://www.sec.gov/edgar/document/four">
  <issuer>
    <issuerCik>0001234567</issuerCik>
    <issuerName>ACME CORP</issuerName>
    <issuerTradingSymbol>ACME</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0007654321</rptOwnerCik>
      <rptOwnerName>SMITH JOHN</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-01</value></transactionDate>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>50.25</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-02</value></transactionDate>
      <transactionCoding>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>51.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-03</value></transactionDate>
      <transactionCoding>
        <transactionCode>A</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>5000</value></transactionShares>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

# Grant (code A) should be excluded — only P/S are open-market
SAMPLE_FORM4_NO_OM = """<?xml version="1.0"?>
<ownershipDocument xmlns="http://www.sec.gov/edgar/document/four">
  <issuer>
    <issuerCik>0009999999</issuerCik>
    <issuerName>NO TRADE INC</issuerName>
    <issuerTradingSymbol>NTRD</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001111111</rptOwnerCik>
      <rptOwnerName>DOE JANE</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>CFO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-10-01</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_form4_basic():
    parsed = parse_form4_xml(SAMPLE_FORM4)
    assert parsed["issuer"]["ticker"] == "ACME"
    assert parsed["issuer"]["name"] == "ACME CORP"
    assert len(parsed["owners"]) == 1
    assert parsed["owners"][0]["name"] == "SMITH JOHN"
    assert parsed["owners"][0]["is_director"] is True
    assert parsed["owners"][0]["is_officer"] is True
    assert parsed["owners"][0]["officer_title"] == "CEO"
    # Only P and S counted (A grant excluded)
    assert parsed["n_open_market"] == 2
    assert len(parsed["transactions"]) == 2


def test_parse_form4_transaction_detail():
    parsed = parse_form4_xml(SAMPLE_FORM4)
    buy = parsed["transactions"][0]
    assert buy["side"] == "buy"
    assert buy["code"] == "P"
    assert buy["shares"] == 10000
    assert buy["price"] == 50.25
    assert buy["value_usd"] == 10000 * 50.25
    assert buy["date"] == "2026-10-01"
    assert buy["direct_indirect"] == "D"

    sell = parsed["transactions"][1]
    assert sell["side"] == "sell"
    assert sell["code"] == "S"
    assert sell["shares"] == 2000


def test_parse_form4_excludes_non_open_market():
    parsed = parse_form4_xml(SAMPLE_FORM4_NO_OM)
    assert parsed["n_open_market"] == 0
    assert parsed["transactions"] == []


def test_parse_master_index():
    idx = """CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
0001234567|ACME CORP|4|2026-10-08|edgar/data/1234567/000123456789012345/acme-4.xml
0007654321|OTHER INC|8-K|2026-10-08|edgar/data/7654321/000765432109876543/other-8k.htm
0001111111|THIRD CO|4/A|2026-10-08|edgar/data/1111111/000111111112345678/third-4a.xml
"""
    entries = parse_master_index(idx, "4")
    assert len(entries) == 1
    assert entries[0]["cik"] == "0001234567"
    assert entries[0]["accession"] == "000123456789012345"
    assert entries[0]["form"] == "4"


def test_detect_clusters():
    filings = [
        {
            "issuer": {"ticker": "ACME", "name": "ACME CORP", "cik": "123"},
            "owners": [{"name": "SMITH JOHN"}],
            "transactions": [{"date": "2026-10-01", "side": "buy"}],
            "filed": "2026-10-02",
        },
        {
            "issuer": {"ticker": "ACME", "name": "ACME CORP", "cik": "123"},
            "owners": [{"name": "DOE JANE"}],
            "transactions": [{"date": "2026-10-03", "side": "buy"}],
            "filed": "2026-10-04",
        },
        {
            "issuer": {"ticker": "OTHER", "name": "OTHER INC", "cik": "456"},
            "owners": [{"name": "SOLO BOB"}],
            "transactions": [{"date": "2026-10-01", "side": "buy"}],
            "filed": "2026-10-02",
        },
    ]
    clusters = detect_clusters(filings)
    assert len(clusters) == 1
    assert clusters[0]["issuer"] == "ACME"
    assert clusters[0]["side"] == "cluster_buy"
    assert clusters[0]["n_insiders"] == 2
    assert "SMITH JOHN" in clusters[0]["insiders"]
    assert "DOE JANE" in clusters[0]["insiders"]


def test_detect_clusters_no_cluster_single_insider():
    filings = [
        {
            "issuer": {"ticker": "SOLO", "name": "SOLO INC", "cik": "789"},
            "owners": [{"name": "ONLY ONE"}],
            "transactions": [
                {"date": "2026-10-01", "side": "buy"},
                {"date": "2026-10-02", "side": "buy"},
            ],
            "filed": "2026-10-03",
        },
    ]
    clusters = detect_clusters(filings)
    assert clusters == []


# ---------------------------------------------------------------------------
# 13D/13G parsing
# ---------------------------------------------------------------------------
SAMPLE_13D = """
SCHEDULE 13D
Name of Issuer: TARGET CORPORATION
Title of Class: Common Stock
CUSIP Number: 123456789
Name of Reporting Person: ACTIVIST FUND LP
Percent of class: 7.5%
Shares Beneficially Owned: 5,000,000
Date of Event Which Requires Filing: October 1, 2026
"""


def test_parse_13dg_basic():
    parsed = parse_13dg_text(SAMPLE_13D, "SC 13D")
    assert parsed["form"] == "SC 13D"
    assert parsed["is_activist"] is True
    assert parsed["is_amendment"] is False
    assert parsed["cusip"] == "123456789"
    assert parsed["percent_owned"] == 7.5
    assert parsed["shares_owned"] == 5000000


def test_parse_13dg_amendment_flag():
    parsed = parse_13dg_text(SAMPLE_13D, "SC 13D/A")
    assert parsed["is_amendment"] is True
    assert parsed["is_activist"] is False  # /A is not "new"


def test_parse_13dg_13g_not_activist():
    parsed = parse_13dg_text(SAMPLE_13D, "SC 13G")
    assert parsed["is_activist"] is False
    assert parsed["is_amendment"] is False


# ---------------------------------------------------------------------------
# 8-K parsing
# ---------------------------------------------------------------------------
SAMPLE_8K = """
<html><body>
<p>Item 1.01 Entry into a Material Definitive Agreement.</p>
<p>On October 1, 2026, the Company entered into a merger agreement...</p>
<p>Item 5.02 Departure of Directors or Certain Officers.</p>
<p>The Company announced the resignation of its CFO...</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
</body></html>
"""


def test_parse_8k_items():
    items = parse_8k_items(SAMPLE_8K)
    assert "1.01" in items
    assert "5.02" in items
    assert "9.01" in items
    assert len(items) == 3


def test_parse_8k_items_case_insensitive():
    text = "<p>ITEM 2.01 Completion of Acquisition.</p><p>Item 7.01 Regulation FD.</p>"
    items = parse_8k_items(text)
    assert "2.01" in items
    assert "7.01" in items


def test_parse_8k_no_items():
    items = parse_8k_items("<html><body><p>No items here.</p></body></html>")
    assert items == []


def test_parse_8k_header():
    text = """
    <html><head><title>ACME CORP (Exact name of registrant)</title></head>
    <body><p>Trading Symbol: ACME</p></body></html>
    """
    header = parse_8k_header(text)
    assert "ACME CORP" in header["issuer_name"]
    assert header["ticker"] == "ACME"
