"""Tests for finance directories (finance_dirs.py): table parsing,
primary-dealer roster, BrokerCheck CRD resolution, NBLP tagging."""
from __future__ import annotations

import json

import pytest

from collector.fetchers import finance_dirs as fd

ATS_HTML = """
<html><body><table>
<tr><th>ATS NAME</th><th>ATS ID</th><th>FIRM NAME</th><th>6732 Exemption</th></tr>
<tr><td>CITDEL ATS</td><td>CDEL</td><td>CITADEL SECURITIES LLC</td><td>Yes</td></tr>
<tr><td>VIRTU ATS</td><td>VIRT</td><td>VIRTU AMERICAS LLC</td><td></td></tr>
</table></body></html>
"""

MPID_HTML = """
<html><body><table>
<tr><th>Reserved MPID</th><th>Bank Name</th><th>Treasury (TS)</th><th>Agency Debt (CA) &amp; Agency MBS (SP)</th></tr>
<tr><td>WCHV</td><td>WELLS FARGO BANK, N.A.</td><td>X</td><td>X</td></tr>
<tr><td>JPMC</td><td>JPMORGAN CHASE BANK, N.A.</td><td>X</td><td></td></tr>
</table></body></html>
"""


def test_table_rows_parses_cells():
    rows = fd._table_rows(ATS_HTML)
    assert rows[0][0] == "ATS NAME"
    assert rows[1] == ["CITDEL ATS", "CDEL", "CITADEL SECURITIES LLC", "Yes"]


def test_primary_dealer_roster_count_and_names():
    assert len(fd.PRIMARY_DEALERS) == 26
    assert "J.P. Morgan Securities LLC" in fd.PRIMARY_DEALERS
    assert "MUFG Securities Americas Inc." in fd.PRIMARY_DEALERS
    assert "Santander US Capital Markets LLC" in fd.PRIMARY_DEALERS
    assert "SMBC Nikko Securities America, Inc." in fd.PRIMARY_DEALERS


class _Store:
    def __init__(self):
        self.docs = {}

    def put_doc(self, key, payload, source=None):
        self.docs[key] = payload

    def get_doc(self, key):
        return self.docs.get(key)


async def _get_text_ats(url, headers=None, params=None):
    if "ats-firms" in url:
        return ATS_HTML
    raise AssertionError(url)


async def _get_text_mpid(url, headers=None, params=None):
    if "depository-institutions-mpids" in url:
        return MPID_HTML
    raise AssertionError(url)


@pytest.mark.asyncio
async def test_fetch_ats_parses_and_links_volume():
    store = _Store()
    store.docs["ats_venues"] = {"venues": {"CDEL": {}}}
    n = await fd._fetch_ats(store, _get_text_ats, "2026-10-07")
    assert n == 2
    doc = store.docs["finance_dir:ats"]
    assert doc["count"] == 2
    assert "finra.org" in doc["source"]
    cdel = next(f for f in doc["firms"] if f["ats_id"] == "CDEL")
    assert cdel["volume_series"] == "cycle:ats-m-cdel-shares"
    assert cdel["has_volume"] is True
    virt = next(f for f in doc["firms"] if f["ats_id"] == "VIRT")
    assert "volume_series" not in virt
    # NBLP tag applied to Virtu
    assert "nblp" in virt["tags"]
    assert virt["tags_source"] == "curated"


@pytest.mark.asyncio
async def test_fetch_depository_parses_mpid_table():
    store = _Store()
    n = await fd._fetch_depository(store, _get_text_mpid, "2026-10-07")
    assert n == 2
    doc = store.docs["finance_dir:depository"]
    assert doc["firms"][0]["mpid"] == "WCHV"
    assert doc["firms"][0]["bank_name"] == "WELLS FARGO BANK, N.A."
    assert "finra.org" in doc["source"]


@pytest.mark.asyncio
async def test_brokercheck_crd_resolution():
    async def get_text(url, headers=None, params=None):
        assert "api.brokercheck.finra.org" in url
        return json.dumps({"hits": {"hits": [
            {"_source": {"firm_source_id": 12345,
                         "firm_name": "Test Firm LLC",
                         "firm_ia_scope": "Firm",
                         "firm_branches_count": 10}}]}})
    hit = await fd._brokercheck_crd("Test Firm", get_text)
    assert hit["crd"] == 12345
    assert hit["branches"] == 10


@pytest.mark.asyncio
async def test_brokercheck_no_hits_returns_none():
    async def get_text(url, headers=None, params=None):
        return json.dumps({"hits": {"hits": []}})
    assert await fd._brokercheck_crd("Nobody", get_text) is None


@pytest.mark.asyncio
async def test_primary_dealers_doc_labeled():
    store = _Store()
    n = await fd._fetch_primary_dealers(store, "2026-10-07")
    assert n == 26
    doc = store.docs["finance_dir:primary_dealers"]
    assert doc["as_of"] == "2026-10-07"
    assert "New York" in doc["source"]
    assert all(f["nyfed_stats"] for f in doc["firms"])
