"""Tests for fetchers/imf.py (IMF chart panels, prompt §15).

All sheet parsing runs on fixture data, never the live sheets. Network is
faked via get_text fixtures and a monkeypatched read_sheet_values.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from collector.fetchers import imf
from collector.store import Store

# ------------------------------------------------------------- fixtures ----

REGISTRY_ROWS = [
    ["chart_id", "title", "units", "frequency", "source_on_chart", "as_of_on_chart",
     "what_it_shows", "dashboard_panel", "dashboard_category", "free_live_source_plan",
     "live_status", "caveats", "refresh_cadence", "date_added"],
    ["IMF-01", "Benchmark yield curves", "percent", "Snapshot", "Bloomberg Finance L.P.",
     "Latest (date not stated on chart)", "US Germany Japan curves", "Global yield curves",
     "Rates", "US: FRED; Japan: MoF CSV", "buildable", "Latest date unstated", "Daily", "2026-10-07"],
    ["IMF-05", "Selected euro area spreads", "bp", "Daily", "Bloomberg",
     "early Oct 2026", "FR IT PT IE vs Bund", "Line chart", "Credit",
     "BdF FM via DBnomics", "buildable", "may differ by a few bp", "Daily", "2026-10-07"],
]

DIGITIZED_ROWS = [
    ["reading_id", "chart_id", "series", "point", "value", "unit", "tolerance",
     "as_of_note", "method", "flag"],
    ["IMF-01-02", "IMF-01", "United States latest", "10Y", "5.1", "percent", "0.1",
     "Latest (date not stated on chart)", "Read from chart image", "DIGITIZED"],
    ["IMF-01-06", "IMF-01", "United States end-2025", "30Y (line end)", "4.85", "percent", "0.1",
     "End-2025", "Read from chart image", "DIGITIZED"],
    ["IMF-02-04", "IMF-02", "Qatar LNG weekly flow", "Collapse (date approx)", "2026-03-01",
     "date", "plus or minus 2 weeks", "Weekly data", "Read from chart image", "DIGITIZED"],
    ["IMF-05-03", "IMF-05", "France minus Germany 10Y", "Latest", "140", "bp", "4",
     "About early Oct 2026", "Read from chart image", "DIGITIZED"],
]

MOF_CSV = (
    "Interest Rate (October 2026),,,,,,,,,,,,,,,(Unit : %)\n"
    "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y\n"
    "2025/12/30,0.793,0.985,1.084,1.241,1.349,1.433,1.538,1.637,1.732,1.825,2.396,2.815,3.174,3.294,3.552\n"
    "2026/10/1,1.668,1.939,2.077,2.274,2.407,2.534,2.657,2.82,2.952,3.092,3.62,3.91,4.154,4.122,4.125\n"
    "2026/10/2,1.65,1.919,2.065,2.259,2.397,2.528,2.657,2.821,2.957,3.097,3.626,3.925,4.171,4.148,4.168\n"
    ",,,,,,,,,,,,,,,\n"
    '"  If you cannot download the latest csv data, please clear the browser cache.",,,,,,,,,,,,,,,\n'
)

PORTWATCH_JSON = {
    "features": [
        {"attributes": {"date": "2026-10-02", "n_total": 4, "n_tanker": 0}},
        {"attributes": {"date": "2026-10-03", "n_total": 2, "n_tanker": 0}},
        {"attributes": {"date": "2026-10-04", "n_total": 4, "n_tanker": 0}},
        {"attributes": {"date": "not-a-date", "n_total": 9, "n_tanker": 9}},
    ]
}

FRED_JSON = {"observations": [
    {"date": "2025-12-01", "value": "2.85"},
    {"date": "2026-01-01", "value": "2.90"},
    {"date": "2026-08-01", "value": "3.18"},
    {"date": "2026-09-01", "value": "."},  # FRED missing marker
]}


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "imf_test.db")


async def _fake_get_text(url, params=None, **kw):
    if "mof.go.jp" in url:
        return MOF_CSV
    if "arcgis" in url:
        return json.dumps(PORTWATCH_JSON)
    if "fred" in url:
        return json.dumps(FRED_JSON)
    raise AssertionError(f"unexpected url {url}")


@pytest.fixture()
def fake_sheets(monkeypatch):
    async def _read(spreadsheet_id, tab_range="A1:Z2000"):
        if spreadsheet_id == imf.REGISTRY_SHEET_ID:
            return REGISTRY_ROWS
        if spreadsheet_id == imf.DIGITIZED_SHEET_ID:
            return DIGITIZED_ROWS
        raise AssertionError(spreadsheet_id)
    monkeypatch.setattr(imf, "read_sheet_values", _read)


# ------------------------------------------------------- sheet parsing ----

def test_parse_registry():
    reg = imf.parse_registry_rows(REGISTRY_ROWS)
    assert set(reg) == {"IMF-01", "IMF-05"}
    assert reg["IMF-01"]["title"] == "Benchmark yield curves"
    assert reg["IMF-01"]["as_of_on_chart"] == "Latest (date not stated on chart)"
    assert reg["IMF-05"]["free_live_source_plan"] == "BdF FM via DBnomics"


def test_parse_registry_empty():
    assert imf.parse_registry_rows([]) == {}


def test_parse_digitized():
    dig = imf.parse_digitized_rows(DIGITIZED_ROWS)
    assert set(dig) == {"IMF-01", "IMF-02", "IMF-05"}
    r = dig["IMF-01"][0]
    assert r["value_num"] == 5.1
    assert r["kind"] == "digitized"  # invariant label present on every reading
    # non-numeric value (a date) is kept as a string, never coerced
    d = dig["IMF-02"][0]
    assert d["value_num"] is None
    assert d["value"] == "2026-03-01"


# ------------------------------------------------------- live parsers -----

def test_parse_mof_csv():
    curves = imf.parse_mof_csv(MOF_CSV)
    assert curves[date(2026, 10, 2)]["10Y"] == 3.097
    assert curves[date(2026, 10, 2)]["30Y"] == 4.148
    assert curves[date(2025, 12, 30)]["2Y"] == 0.985
    # only the tenors we track are parsed
    assert set(curves[date(2026, 10, 2)]) == {"2Y", "5Y", "10Y", "30Y"}
    # footer note row and blank row are ignored
    assert len(curves) == 3


def test_parse_mof_csv_empty():
    with pytest.raises(ValueError):
        imf.parse_mof_csv("Interest Rate,,,,,(Unit : %)\nDate,1Y,2Y\n")


def test_parse_portwatch():
    rows = imf.parse_portwatch(PORTWATCH_JSON)
    assert rows == [
        (date(2026, 10, 2), 4, 0),
        (date(2026, 10, 3), 2, 0),
        (date(2026, 10, 4), 4, 0),
    ]  # malformed date row dropped


def test_last_on_or_before():
    pts = {date(2025, 12, 30): 1.8, date(2025, 12, 31): 1.9, date(2026, 1, 2): 2.0}
    d, v = imf.last_on_or_before(pts)
    assert (d, v) == (date(2025, 12, 31), 1.9)
    assert imf.last_on_or_before({}) == (None, None)


# ------------------------------------------------------- tolerance --------

def test_tolerance_within():
    r = {"reading_id": "X", "value_num": 5.1, "tolerance": "0.1",
         "value": "5.1", "unit": "percent", "as_of_note": ""}
    out = imf.tolerance_check(5.15, r)
    assert out["status"] == "within_tolerance"
    assert out["note"] is None


def test_tolerance_differs_keeps_both():
    r = {"reading_id": "X", "value_num": 5.1, "tolerance": "0.1",
         "value": "5.1", "unit": "percent", "as_of_note": ""}
    out = imf.tolerance_check(5.31, r)
    assert out["status"] == "differs_from_imf_chart"
    assert out["live"] == 5.31 and out["digitized"] == 5.1
    assert "differs from IMF chart" in out["note"]


def test_tolerance_not_comparable():
    r = {"reading_id": "X", "value_num": 1.5, "tolerance": "0.1",
         "value": "1.5", "unit": "million metric tons per week", "as_of_note": ""}
    out = imf.tolerance_check(None, r)
    assert out["status"] == "not_comparable"
    assert out["reason"]


# ------------------------------------------------- graceful degradation --

@pytest.mark.asyncio()
async def test_no_credential_degrades(store, monkeypatch):
    monkeypatch.delenv(imf.SA_ENV_VAR, raising=False)
    payload = await imf.refresh_imf(store, _fake_get_text)
    assert payload["status"] == "not-connected"
    assert "Google service account" in payload["message"]
    for panel in payload["panels"]:
        assert panel["status"] == "not-connected"
        assert panel["live"]["available"] is False
    # doc was still written so the API route has something to serve
    assert store.doc(imf.DOC_KEY) is not None


@pytest.mark.asyncio()
async def test_missing_google_auth_library_degrades(store, monkeypatch):
    # real read_sheet_values -> _service_account_token -> SheetsError
    monkeypatch.setenv(imf.SA_ENV_VAR, '{"type":"service_account"}')
    monkeypatch.setattr(imf, "_google_auth_module", lambda: None)
    payload = await imf.refresh_imf(store, _fake_get_text)
    assert payload["status"] == "not-connected"
    assert "google-auth" in payload["reason"]


def test_imf_payload_missing_doc(store):
    payload = imf.imf_payload(store)
    assert payload["status"] == "not-connected"
    assert len(payload["panels"]) == 5


# ------------------------------------------------------- full refresh -----

@pytest.mark.asyncio()
async def test_refresh_builds_panels(store, fake_sheets, monkeypatch):
    monkeypatch.setenv(imf.SA_ENV_VAR, '{"type":"service_account"}')
    # US curve legs in the store (FRED CMT, as populated by the cycle fetcher)
    store.upsert_points("cycle:us10y", [(date(2025, 12, 31), 4.20), (date(2026, 10, 5), 5.31)])
    store.upsert_points("cycle:us2y", [(date(2025, 12, 31), 3.60), (date(2026, 10, 5), 4.60)])
    store.upsert_points("cycle:ust30y", [(date(2025, 12, 31), 4.90), (date(2026, 10, 5), 5.45)])

    payload = await imf.refresh_imf(store, _fake_get_text, fred_api_key="fake-key")
    assert payload["status"] == "ok"
    by_id = {p["chart_id"]: p for p in payload["panels"]}
    assert set(by_id) == {"IMF-01", "IMF-02", "IMF-03", "IMF-04", "IMF-05"}

    # every panel tagged with source/as-of/tag
    for p in payload["panels"]:
        assert p["tag"] in ("primary", "derived", "proxy", "manual", "Q")
        assert "source" in p and "as_of" in p

    # P1: bp change table uses live values only
    p1 = by_id["IMF-01"]
    row = next(r for r in p1["live"]["bp_change_table"]
               if r["country"] == "US" and r["tenor"] == "10Y")
    assert row["bp_change"] == pytest.approx((5.31 - 4.20) * 100)
    assert row["kind"] == "live"
    # US 10Y latest 5.31 vs digitized 5.1 ± 0.1 -> differs note, both kept
    comp = next(c for c in p1["tolerance_comparisons"] if c["reading_id"] == "IMF-01-02")
    assert comp["status"] == "differs_from_imf_chart"
    assert comp["live"] == 5.31 and comp["digitized"] == 5.1

    # P2: proxy panel present, baseline computed from data, digitized grey only
    p2 = by_id["IMF-02"]
    assert p2["tag"] == "proxy"
    assert p2["live"]["kind"] == "proxy"
    assert p2["live"]["latest_total_7d"] == pytest.approx(10 / 3, abs=0.01)
    assert all(c["status"] == "not_comparable" for c in p2["tolerance_comparisons"])

    # P3/P4 digitized-only: no live values at all
    assert by_id["IMF-03"]["live"]["available"] is False
    assert by_id["IMF-04"]["live"]["available"] is False
    assert by_id["IMF-03"]["digitized_reference"]["kind"] == "digitized"

    # P5: spreads derived from monthly FRED legs
    p5 = by_id["IMF-05"]
    assert p5["tag"] == "derived"
    fr_latest = p5["live"]["latest"]["FR"]
    # FRED fixture: FR 3.18 (Aug 2026) minus DE 3.18 -> 0.0bp on the shared months
    assert fr_latest["bp"] == 0.0

    # doc written
    assert store.doc(imf.DOC_KEY).payload["status"] == "ok"


async def test_digitized_never_in_scores(store, fake_sheets, monkeypatch):
    """Invariant: no digitized value may flow into indicators/scores."""
    monkeypatch.setenv(imf.SA_ENV_VAR, '{"type":"service_account"}')
    store.upsert_points("cycle:us10y", [(date(2025, 12, 31), 4.20), (date(2026, 10, 5), 5.31)])
    store.upsert_points("cycle:us2y", [(date(2025, 12, 31), 3.60), (date(2026, 10, 5), 4.60)])
    store.upsert_points("cycle:ust30y", [(date(2025, 12, 31), 4.90), (date(2026, 10, 5), 5.45)])
    payload = await imf.refresh_imf(store, _fake_get_text, fred_api_key="fake-key")

    blob = json.dumps(payload["indicators"])
    assert '"kind": "digitized"' not in blob
    for ind in payload["indicators"]:
        assert ind["kind"] in ("live", "proxy")
        assert "tolerance" not in ind  # tolerance belongs to digitized readings only

    # digitized values appear ONLY under digitized_reference sections
    def walk(node, path=""):
        if isinstance(node, dict):
            if node.get("kind") == "digitized":
                assert "digitized_reference" in path or "readings" in path or "bubble_data" in path \
                    or "bars" in path, f"digitized value outside reference section: {path}"
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    walk(payload["panels"])


def test_weeks_since_collapse(store):
    # 80/day through the baseline window, then 2/day from 2026-03-01 (no gaps)
    pts = []
    d = date(2025, 3, 1)
    while d <= date(2026, 10, 4):
        pts.append((d, 2.0 if d >= date(2026, 3, 1) else 80.0))
        d = date.fromordinal(d.toordinal() + 1)
    store.upsert_points("imf:hormuz-total", pts)
    store.upsert_points("imf:hormuz-tanker", [(d, 0.0) for d, _ in pts])

    panel, inds = imf.build_p2(store, {}, {"IMF-02": []})
    live = panel["live"]
    assert live["baseline_trailing_year_to_2026_02_28"] == 80.0
    # 7d trailing mean first drops below 25% of 80 (=20) on 2026-03-06:
    # (80 + 6*2)/7 = 13.1
    assert live["collapse_day_7d_below_25pct_baseline"] == "2026-03-06"
    expected_weeks = (date(2026, 10, 4) - date(2026, 3, 6)).days // 7
    assert live["weeks_since_collapse"] == expected_weeks
    assert inds[0]["id"] == "hormuz_transits_7d"
    assert inds[0]["direction"] == "-"
    assert inds[0]["proxy_excluded_from_composites"] is True


def test_p2_empty_store_degrades(store):
    panel, inds = imf.build_p2(store, {}, {"IMF-02": []})
    assert panel["live"]["available"] is False
    assert inds == []
    assert any("unavailable" in n for n in panel["notes"])


def test_p5_no_german_leg_degrades(store):
    panel, inds = imf.build_p5(store, {}, {"IMF-05": []})
    assert panel["live"]["available"] is False
    assert inds == []
