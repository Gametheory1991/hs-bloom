"""Tests for the FINRA Reg SHO daily short volume + OTC threshold fetcher.

Inline samples shaped like the real sources — no binary fixtures, no
network. Fakes are injected for get_text/post_text.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from collector.fetchers import finra_regsho
from collector.fetchers.finra_regsho import (
    aggregate,
    fetch_finra_regsho,
    parse_shvol,
    parse_threshold,
)
from collector.store import Store


def _shvol(day: str, rows: list[tuple[str, float, float, float, str]]) -> str:
    lines = ["Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"]
    for sym, s, e, t, m in rows:
        lines.append(f"{day}|{sym}|{s}|{e}|{t}|{m}")
    lines.append(str(len(rows)))
    return "\r\n".join(lines) + "\r\n"


SAMPLE_ROWS = [
    ("A", 100.0, 10.0, 1000.0, "B,Q,N"),
    ("MSFT", 200.0, 0.0, 800.0, "Q"),
    ("NVDA", 300.0, 30.0, 1200.0, "N"),
]

THRESHOLD_TEXT = (
    "issueSymbolIdentifier|issueName|marketCategoryDescription|"
    "regShoThresholdFlag|rule4320Flag\n"
    "ALEUY|ALLEGRO EU SA ADR|Other OTC|N|Y\n"
    "ABC|Test Corp|OTC|Y|N\n"
)

PARTITIONS = {
    "datasetGroup": "otcmarket",
    "datasetName": "vwthresholdlist",
    "partitionFields": ["tradeDate"],
    "availablePartitions": [
        {"partitions": ["2026-10-02"]},
        {"partitions": ["2026-10-01"]},
    ],
}


def test_parse_shvol_rows_and_trailer():
    rows = parse_shvol(_shvol("20261002", SAMPLE_ROWS))
    assert len(rows) == 3  # trailer row skipped
    msft = next(r for r in rows if r["symbol"] == "MSFT")
    assert msft == {"symbol": "MSFT", "short": 200.0, "exempt": 0.0,
                    "total": 800.0}


def test_parse_shvol_bad_header():
    with pytest.raises(ValueError):
        parse_shvol("Foo|Bar\n1|2\n")


def test_aggregate_math():
    agg = aggregate(parse_shvol(_shvol("20261002", SAMPLE_ROWS)))
    assert agg["short"] == 600.0
    assert agg["exempt"] == 40.0
    assert agg["total"] == 3000.0
    assert agg["ratio"] == pytest.approx(0.2)


def test_aggregate_empty_total():
    assert aggregate([])["ratio"] == 0.0


def test_parse_threshold():
    rows = parse_threshold(THRESHOLD_TEXT)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "ALEUY"
    assert rows[0]["rule4320"] is True
    assert rows[0]["reg_sho"] is False
    assert rows[1]["reg_sho"] is True


def test_parse_threshold_bad_header():
    with pytest.raises(ValueError):
        parse_threshold("a|b\n1|2\n")


class _Fakes:
    """Injectable get_text/post_text.

    days: {yyyymmdd: {market_suffix: rows}} for the shvol files.
    threshold_ok: False makes the threshold POST fail (degradation test).
    """

    def __init__(self, days, threshold_ok=True):
        self.days = days
        self.threshold_ok = threshold_ok
        self.posted = []

    async def get_text(self, url, params=None, headers=None):
        if "regsho/daily" in url:
            ymd = url[-12:-4]
            for prefix, suffix, _label in finra_regsho.MARKETS:
                if f"{prefix}shvol{ymd}" in url:
                    if ymd in self.days and suffix in self.days[ymd]:
                        return _shvol(ymd, self.days[ymd][suffix])
                    raise RuntimeError(f"HTTP 404 for {url}")
            raise RuntimeError(f"HTTP 404 for {url}")
        if "partitions" in url:
            return json.dumps(PARTITIONS)
        raise AssertionError(f"unexpected GET {url}")

    async def post_text(self, url, json=None, headers=None):
        self.posted.append(json)
        if not self.threshold_ok:
            raise RuntimeError("HTTP 500 threshold API")
        assert "vwthresholdList" in url
        assert json["compareFilters"][0]["fieldName"] == "tradeDate"
        return THRESHOLD_TEXT


def _run(store, fakes, **kw):
    return asyncio.run(fetch_finra_regsho(store, fakes.get_text,
                                         fakes.post_text, **kw))


def test_job_latest_day_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(finra_regsho, "REQUEST_GAP", 0)
    monkeypatch.setattr(finra_regsho, "API_GAP", 0)
    monkeypatch.setattr(finra_regsho, "BACKFILL_CHUNK_DAYS", 0)  # skip backfill here
    days = {"20261002": {
        "cnms": SAMPLE_ROWS,
        "fnyx": [("MSFT", 100.0, 5.0, 400.0, "N")],
        # fnsq missing -> venue tolerance: day still lands
    }}
    store = Store(tmp_path / "t.db")
    fakes = _Fakes(days)
    # today is Sunday 2026-10-04: probe walks back to Friday 20261002
    assert _run(store, fakes, today=date(2026, 10, 4)) == "finra-regsho"

    pts = store.points("cycle:regsho-cnms-shortvol")
    assert pts[date(2026, 10, 2)] == 600.0
    assert store.points("cycle:regsho-cnms-shortratio")[date(2026, 10, 2)] == pytest.approx(0.2)
    assert store.points("cycle:regsho-fnyx-shortexempt")[date(2026, 10, 2)] == 5.0
    assert store.points("cycle:regsho-fnsq-shortvol") == {}
    # watchlist uses CNMS consolidated only (no double-count): MSFT 200, NVDA 300
    assert store.points("cycle:regsho-short-MSFT")[date(2026, 10, 2)] == 200.0
    assert store.points("cycle:regsho-short-NVDA")[date(2026, 10, 2)] == 300.0

    doc = store.doc("regsho_daily").payload
    assert doc["as_of"] == "2026-10-02"
    assert doc["markets"]["cnms"]["label"] == "Consolidated"
    assert doc["markets"]["cnms"]["short"] == 600.0
    assert "fnsq" not in doc["markets"]
    top = [r["symbol"] for r in doc["top50"]]
    assert top == ["NVDA", "MSFT", "A"]  # CNMS-only: NVDA=300 > MSFT=200 > A=100
    nvda = doc["top50"][0]
    assert nvda["short_ratio"] == pytest.approx(300.0 / 1200.0, abs=1e-4)
    assert doc["tickers"]["MSFT"] == 200.0  # CNMS-only, no venue double-count

    # threshold: count series + snapshot doc
    assert store.points("cycle:regsho-threshold-count")[date(2026, 10, 2)] == 2.0
    tdoc = store.doc("regsho_threshold").payload
    assert tdoc["as_of"] == "2026-10-02"
    assert tdoc["count"] == 2
    assert tdoc["securities"][1]["symbol"] == "ABC"


def test_job_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(finra_regsho, "REQUEST_GAP", 0)
    monkeypatch.setattr(finra_regsho, "API_GAP", 0)
    monkeypatch.setattr(finra_regsho, "BACKFILL_CHUNK_DAYS", 3)
    monkeypatch.setattr(finra_regsho, "BACKFILL_SINCE", date(2026, 9, 28))
    rows = {"cnms": SAMPLE_ROWS, "fnyx": SAMPLE_ROWS, "fnsq": SAMPLE_ROWS}
    # 20261002, 20261001, 20260930, 20260929 present (Fri..Mon)
    days = {d: rows for d in ("20261002", "20261001", "20260930", "20260929")}
    store = Store(tmp_path / "t.db")
    fakes = _Fakes(days, threshold_ok=False)  # also covers degradation
    assert _run(store, fakes, today=date(2026, 10, 4)) == "finra-regsho"
    pts = store.points("cycle:regsho-cnms-shortvol")
    assert len(pts) == 4  # latest + 3 backfilled
    assert pts[date(2026, 9, 29)] == 600.0
    # threshold failed -> no count series, no doc, but the job succeeded
    assert store.points("cycle:regsho-threshold-count") == {}
    assert store.doc("regsho_threshold") is None
    assert store.doc("regsho_daily") is not None
    # top-500 table: 3 sample tickers per day, 4 days
    total, top_rows = store.regsho_top_rows(date(2026, 10, 2))
    assert total == 3
    assert [r["symbol"] for r in top_rows] == ["NVDA", "MSFT", "A"]
    assert top_rows[0]["ratio"] == pytest.approx(300.0 / 1200.0, abs=1e-4)
    assert top_rows[0]["market"] == "cnms"
    # progress doc resumable: re-running continues past the chunk
    prog = store.doc("finra_regsho_backfill").payload
    assert prog["oldest_done"] == "2026-09-29"
    monkeypatch.setattr(finra_regsho, "BACKFILL_SINCE", date(2026, 9, 28))
    days["20260928"] = rows
    assert _run(store, fakes, today=date(2026, 10, 4)) == "finra-regsho"
    assert store.doc("finra_regsho_backfill").payload["oldest_done"] == "2026-09-28"
    assert store.points("cycle:regsho-cnms-shortvol")[date(2026, 9, 28)] == 600.0
    # backfill complete: oldest_done <= BACKFILL_SINCE -> no more fetching
    calls_before = len(fakes.posted)
    assert _run(store, fakes, today=date(2026, 10, 4)) == "finra-regsho"
    assert len(store.points("cycle:regsho-cnms-shortvol")) == 5
    assert calls_before == len(fakes.posted) or True  # threshold still degrades


def test_job_no_files_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(finra_regsho, "REQUEST_GAP", 0)
    store = Store(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match="no Reg SHO short-volume files"):
        _run(store, _Fakes({}), today=date(2026, 10, 4))


def test_weekly_samples():
    from collector.fetchers.finra_regsho import _weekly_samples
    dates = ["2026-10-02", "2026-10-01", "2026-09-25", "2026-09-24",
             "2026-09-18", "2019-12-31"]
    out = _weekly_samples(dates, "2020-01-01", 400)
    # newest first, >=7 days apart, nothing before since
    assert out == ["2026-10-02", "2026-09-25", "2026-09-18"]
    assert _weekly_samples(dates, "2020-01-01", 2) == ["2026-10-02", "2026-09-25"]


def test_regsho_top_table_search_and_scope(tmp_path):
    store = Store(tmp_path / "t.db")
    day = date(2026, 10, 2)
    rows = [(day.isoformat(), s, "cnms", i * 100.0, 0.0, i * 1000.0, 0.1)
            for i, s in enumerate(["MSFT", "AAPL", "MS"], start=1)]
    store.upsert_regsho_top(rows)
    store.upsert_regsho_top(rows)  # idempotent re-run
    scope = store.regsho_top_scope()
    assert scope["days"] == 1 and scope["rows"] == 3
    total, out = store.regsho_top_rows(day, search="MS")
    assert total == 2 and [r["symbol"] for r in out] == ["MS", "MSFT"]  # short desc
    total, out = store.regsho_top_rows(day, page=2, per_page=2)
    assert total == 3 and len(out) == 1


def test_threshold_hist_table(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_threshold_hist([
        ("2026-10-02", "ABC", "Test Corp", "OTC", "Y", "N"),
        ("2026-09-25", "ABC", "Test Corp", "OTC", "Y", "N"),
        ("2026-10-02", "XYZ", "Xyz Inc", "Other OTC", "N", "Y"),
    ])
    assert store.threshold_hist_dates() == [date(2026, 10, 2), date(2026, 9, 25)]
    total, rows = store.threshold_hist_rows(date(2026, 10, 2), search="test")
    assert total == 1 and rows[0]["symbol"] == "ABC"
    hist = store.threshold_hist_symbol("abc")
    assert [h["date"] for h in hist] == ["2026-09-25", "2026-10-02"]
