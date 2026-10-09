"""Tests for the shared EDGAR 13F-HR filing XML cache (cache logic only;
get_text is stubbed)."""
from __future__ import annotations

import json

import pytest

from collector.fetchers import edgar_filing_cache as efc

GOOD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip>
  <value>100</value></infoTable>
</informationTable>"""

INDEX_JSON = json.dumps({"directory": {"item": [
    {"name": "primary_doc.xml"},
    {"name": "infotable_123.xml"},
]}})

CIK10 = "0001067983"
ACCESSION = "0001193125-26-352200"


def _stub(monkeypatch, tmp_path, xml_body=GOOD_XML, index_body=INDEX_JSON):
    monkeypatch.setenv("EDGAR_XML_CACHE_DIR", str(tmp_path / "xml-cache"))
    calls = []

    async def fake_get(url, params=None, headers=None):
        calls.append(url)
        if url.endswith("index.json"):
            return index_body
        assert url.endswith("/infotable_123.xml")
        return xml_body

    async def _noop(_):
        return None

    monkeypatch.setattr(efc.asyncio, "sleep", _noop)
    return calls, fake_get


async def test_get_filing_xml_downloads_once(tmp_path, monkeypatch):
    calls, fake_get = _stub(monkeypatch, tmp_path)
    headers = {"User-Agent": "t"}
    p1 = await efc.get_filing_xml(CIK10, ACCESSION, fake_get, headers)
    p2 = await efc.get_filing_xml(CIK10, ACCESSION, fake_get, headers)
    assert p1 == p2 == GOOD_XML
    # index.json + xml on the miss; nothing on the hit
    assert len(calls) == 2
    assert (efc.cache_dir() / "000119312526352200.xml").exists()


async def test_get_filing_xml_refuses_json_error_envelope(tmp_path, monkeypatch):
    calls, fake_get = _stub(
        monkeypatch, tmp_path, xml_body='{"error": "Request blocked", "code": 403}')
    with pytest.raises(ValueError, match="error payload"):
        await efc.get_filing_xml(CIK10, ACCESSION, fake_get, {"User-Agent": "t"})
    # error payloads are never cached
    assert list(efc.cache_dir().glob("*.xml")) == []
    assert len(calls) == 2


async def test_get_filing_xml_refuses_html_error_page(tmp_path, monkeypatch):
    calls, fake_get = _stub(
        monkeypatch, tmp_path,
        xml_body="<!DOCTYPE html><html><body>Access Denied</body></html>")
    with pytest.raises(ValueError, match="error payload"):
        await efc.get_filing_xml(CIK10, ACCESSION, fake_get, {"User-Agent": "t"})
    assert list(efc.cache_dir().glob("*.xml")) == []


async def test_get_filing_xml_redownloads_corrupt_cache(tmp_path, monkeypatch):
    calls, fake_get = _stub(monkeypatch, tmp_path)
    bad = efc.cache_dir() / "000119312526352200.xml"
    bad.write_text("this is not xml <unclosed", encoding="utf-8")
    text = await efc.get_filing_xml(CIK10, ACCESSION, fake_get, {"User-Agent": "t"})
    assert text == GOOD_XML
    assert len(calls) == 2  # corrupt entry replaced via download


async def test_get_filing_xml_no_holdings_xml_in_index(tmp_path, monkeypatch):
    index = json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}]}})
    calls, fake_get = _stub(monkeypatch, tmp_path, index_body=index)
    with pytest.raises(ValueError, match="no holdings XML"):
        await efc.get_filing_xml(CIK10, ACCESSION, fake_get, {"User-Agent": "t"})
    assert len(calls) == 1  # only index.json fetched


def test_detect_error_payload():
    assert efc.detect_error_payload("") == "empty body"
    assert efc.detect_error_payload('{"error": "x"}').startswith("JSON error envelope")
    assert efc.detect_error_payload('{"message": "rate limited"}').startswith("JSON error envelope")
    assert efc.detect_error_payload('{"unexpected": 1}').startswith("unexpected JSON")
    assert efc.detect_error_payload("<html>denied</html>") == "HTML error page"
    assert efc.detect_error_payload("<!DOCTYPE html>") == "HTML error page"
    assert efc.detect_error_payload(GOOD_XML) is None
    assert efc.detect_error_payload("  \n" + GOOD_XML) is None
