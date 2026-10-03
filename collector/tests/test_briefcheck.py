"""briefcheck.py parser/comparator (mocked terminal) + the new API endpoints."""
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from collector.api import create_app
from collector.config import load_config
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_briefcheck():
    spec = importlib.util.spec_from_file_location(
        "briefcheck", REPO_ROOT / "briefcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bc = _load_briefcheck()

SAMPLE_HTML = """
<html><body>
<h3>Table 1: Benchmark Rates &amp; Yield Curve Structure</h3>
<table>
<tr><th>Series / Metric</th><th>Level (Oct 2)</th><th>1D %</th></tr>
<tr><td>UST 10Y</td><td>5.28%</td><td>+2.13%</td></tr>
<tr><td>US IG OAS</td><td>98 bps</td><td>0.00%</td></tr>
<tr><td>10Y-2Y Spread</td><td>+40 bps</td><td>-24.53%</td></tr>
</table>
<h3>Table 2: Market Plumbing, Liquidity Reserves &amp; Petroleum Inventories</h3>
<table>
<tr><th>Plumbing / Reserve Metric</th><th>Level (Oct 2)</th></tr>
<tr><td>Treasury General Account (TGA)</td><td>$824.0B</td></tr>
<tr><td>SPR Crude Stocks (WCSSTUS1)</td><td>400.0 MMBbls</td></tr>
</table>
</body></html>
"""


def test_parser_extracts_tables_and_headings():
    p = bc.BriefingParser()
    p.feed(SAMPLE_HTML)
    assert len(p.tables) == 2
    assert p.tables[0]["heading"] == "Table 1: Benchmark Rates & Yield Curve Structure"
    assert p.tables[0]["headers"][:2] == ["Series / Metric", "Level (Oct 2)"]
    assert p.tables[0]["rows"][0] == ["UST 10Y", "5.28%", "+2.13%"]
    assert len(p.tables[1]["rows"]) == 2


def test_parse_level_units():
    assert bc.parse_level("5.28%") == (5.28, "pct")
    assert bc.parse_level("98 bps") == (98.0, "bps")
    assert bc.parse_level("+40 bps") == (40.0, "bps")
    assert bc.parse_level("$824.0B") == (824000.0, "usd_m")
    assert bc.parse_level("$100.43") == (100.43, "usd")
    assert bc.parse_level("400.0 MMBbls") == (400.0, "mmbbl")
    assert bc.parse_level("16.25") == (16.25, "num")
    assert bc.parse_level("—") == (None, None)
    assert bc.parse_level("N/A") == (None, None)


def test_to_canonical_conversions():
    assert bc.to_canonical(40.0, "bps", "pct") == 0.4
    assert bc.to_canonical(5.28, "pct", "pct") == 5.28
    assert bc.to_canonical(0.98, "pct", "bps") == 98.0
    assert bc.to_canonical(100.43, "usd", "num") == 100.43
    assert bc.to_canonical(None, "pct", "pct") is None
    assert bc.to_canonical(5.0, "num", "pct") is None  # incompatible kinds


def _tvals(*pairs):
    """Fake terminal_values: {(source, key): value}."""
    base = {
        ("scorecard", "us10y"): 5.24,
        ("scorecard", "ig-oas"): 0.98,   # terminal stores OAS in %
        ("scorecard", "t10y2y"): 0.38,
        ("scorecard", "tga"): 824000.0,
        ("scorecard", "spr-stocks"): 283.767,
    }
    for key, val in pairs:
        base[key] = val
    return base


def test_check_table_flags_real_mismatch():
    p = bc.BriefingParser()
    p.feed(SAMPLE_HTML)
    checked, mismatches = bc.check_table(p.tables[1], _tvals(), None)
    assert checked == 2
    # TGA agrees (824.0B == 824000 $m); SPR 400.0 vs 283.767 is way off
    assert len(mismatches) == 1
    m = mismatches[0]
    assert m["metric"] == "SPR Crude Stocks (WCSSTUS1)"
    assert m["briefing_value"] == 400.0 and m["terminal_value"] == 283.767


def test_check_table_tolerances():
    p = bc.BriefingParser()
    p.feed(SAMPLE_HTML)
    # 10Y 5.28 vs 5.24 -> 4bp < 10bp tol; IG OAS 98 vs 98 -> ok; 10Y-2Y 40bp vs 38bp ok
    checked, mismatches = bc.check_table(p.tables[0], _tvals(), None)
    assert checked == 3 and mismatches == []
    # tighten via override: 5.28 vs 5.24 = 0.76% rel > 0.1% override... abs-mode
    # rows are unaffected by tolerance-pct; only rel-mode rows are
    checked, mismatches = bc.check_table(
        p.tables[1], _tvals((("scorecard", "tga"), 700000.0)), 0.001)
    assert any(m["metric"].startswith("Treasury General") for m in mismatches)


def test_main_exit_codes_and_report(tmp_path, monkeypatch, capsys):
    html_path = tmp_path / "brief.html"
    html_path.write_text(SAMPLE_HTML)
    monkeypatch.setattr(bc, "terminal_values", lambda base: _tvals())
    rc = bc.main(["--briefing-html", str(html_path), "--base-url", "http://x"])
    assert rc == 1  # SPR mismatch
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["checked_count"] == 5
    assert len(report["mismatches"]) == 1
    assert "checked_at" in report
    # all agreeing -> exit 0
    monkeypatch.setattr(
        bc, "terminal_values",
        lambda base: _tvals((("scorecard", "spr-stocks"), 400.0)))
    assert bc.main(["--briefing-html", str(html_path), "--base-url", "http://x"]) == 0


# --- API endpoints --------------------------------------------------------
def _client(tmp_path):
    store = Store(tmp_path / "t.db")
    cfg = load_config(REPO_ROOT / "config.yaml")
    return TestClient(create_app(store, cfg)), store


def test_alerts_config_get_put(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/alerts/config").json()
    assert {t["id"] for t in body["types"]} == {"anomaly", "trend"}
    assert all(t["threshold_mult"] == 1.0 and t["muted"] is False for t in body["types"])
    r = client.put("/api/alerts/config", json={"id": "anomaly", "muted": True})
    assert r.status_code == 200
    assert r.json()["type"]["muted"] is True
    r = client.put("/api/alerts/config",
                   json={"id": "anomaly", "threshold_mult": 2.5})
    assert r.json()["type"]["threshold_mult"] == 2.5
    # persisted
    assert client.get("/api/alerts/config").json()["types"][0]["muted"] is True
    # validation
    assert client.put("/api/alerts/config", json={"id": "nope"}).status_code == 400
    assert client.put("/api/alerts/config",
                      json={"id": "trend", "threshold_mult": 99}).status_code == 400


def test_briefcheck_store_and_get(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/briefcheck").status_code == 404
    report = {"checked_at": "2026-10-03T00:00:00Z", "checked_count": 5,
              "mismatches": [{"metric": "x"}]}
    r = client.post("/api/briefcheck", json=report)
    assert r.status_code == 200 and r.json() == {"stored": True}
    assert client.get("/api/briefcheck").json()["checked_count"] == 5
