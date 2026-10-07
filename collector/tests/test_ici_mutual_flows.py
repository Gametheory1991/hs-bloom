from datetime import date
from pathlib import Path

import pytest

from collector.fetchers.ici_mutual_flows import (
    COL_SLUG,
    parse_ici_workbook,
    resolve_workbook,
)
from collector.store import Store

FIXTURE = (Path(__file__).parent / "fixtures" / "ici_flows_2026.xls").read_bytes()


def test_parse_ici_weekly_values_match_release():
    series = parse_ici_workbook(FIXTURE)
    # week ended 9/30/2026 values cross-checked against the ICI release
    weekly = {sid: dict(pts) for sid, pts in series.items()}
    assert weekly["cycle:ici-mf-total-longterm"][date(2026, 9, 30)] == -55345.0
    assert weekly["cycle:ici-mf-equity-total"][date(2026, 9, 30)] == -37130.0
    assert weekly["cycle:ici-mf-hybrid"][date(2026, 9, 30)] == -1162.0
    assert weekly["cycle:ici-mf-bond-total"][date(2026, 9, 30)] == -17053.0
    assert weekly["cycle:ici-mf-bond-municipal"][date(2026, 9, 30)] == -6674.0


def test_parse_ici_monthly_section_is_actual_history():
    series = parse_ici_workbook(FIXTURE)
    monthly = dict(series["cycle:ici-mf-total-longterm-monthly"])
    assert monthly[date(2024, 1, 31)] == -20567.0
    assert monthly[date(2026, 8, 31)] == -94199.0
    # all 20 categories present in both sections
    weekly_sids = {s for s in series if not s.endswith("-monthly")}
    monthly_sids = {s[:-len("-monthly")] for s in series if s.endswith("-monthly")}
    assert weekly_sids == monthly_sids
    assert {s.replace("cycle:ici-mf-", "") for s in weekly_sids} == set(COL_SLUG.values())


def test_parse_ici_rejects_non_xls():
    with pytest.raises(ValueError, match="magic bytes"):
        parse_ici_workbook(b"<HTML><H" + b"x" * 100)


async def test_resolve_workbook_prefers_current_year():
    seen = []

    async def fake_get_bytes(url, params=None, headers=None):
        seen.append(url)
        return FIXTURE

    url, content = await resolve_workbook(fake_get_bytes, today=date(2026, 10, 7))
    assert url == "https://www.ici.org/flows_data_2026.xls"
    assert content == FIXTURE
    assert len(seen) == 1


async def test_resolve_workbook_falls_back_to_prior_year_in_january():
    async def fake_get_bytes(url, params=None, headers=None):
        if "2027" in url:
            raise RuntimeError("404")
        return FIXTURE

    url, _ = await resolve_workbook(fake_get_bytes, today=date(2027, 1, 3))
    assert url == "https://www.ici.org/flows_data_2026.xls"


async def test_fetch_ici_mutual_flows_upserts_and_docs(tmp_path):
    from collector.fetchers.ici_mutual_flows import fetch_ici_mutual_flows

    async def fake_get_bytes(url, params=None, headers=None):
        return FIXTURE

    store = Store(tmp_path / "t.db")
    assert await fetch_ici_mutual_flows(store, fake_get_bytes, today=date(2026, 10, 7)) == "ici"
    pts = store.points("cycle:ici-mf-total-longterm")
    assert pts[date(2026, 9, 30)] == -55345.0
    mpts = store.points("cycle:ici-mf-equity-domestic-total-monthly")
    assert mpts[date(2024, 1, 31)] == -39251.0
    doc = store.doc("ici_mutual_flows")
    assert doc is not None
    assert "AGGREGATE" in doc.payload["note"]
    assert "ETFs" in doc.payload["note"]
    # idempotent: second run changes nothing
    v0 = store.data_version()
    await fetch_ici_mutual_flows(store, fake_get_bytes, today=date(2026, 10, 7))
    assert store.points("cycle:ici-mf-total-longterm")[date(2026, 9, 30)] == -55345.0
    assert store.data_version() > v0  # upserts still bump the version, values unchanged
