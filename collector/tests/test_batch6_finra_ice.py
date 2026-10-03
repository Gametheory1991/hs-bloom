"""Tests for batch 6 FINRA + ICE fetchers: TRACE Treasury aggregates,
TRACE monthly volume, ICE Vantage STAR, FINRA short interest, FINRA
margin statistics. All parsing is offline against real-file fixtures;
jobs run against fake get_bytes/get_text fakes (no network)."""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from collector.fetchers import finra_margin, finra_short, ice_star, trace_monthly, trace_treasury
from collector.fetchers.xlsx import find_row, read_sheet

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_request_gap(monkeypatch):
    """Keep the suite fast: the polite request gap is for production."""
    for mod in (finra_margin, finra_short, ice_star, trace_monthly,
                trace_treasury):
        monkeypatch.setattr(mod, "REQUEST_GAP", 0.0, raising=False)


class FakeStore:
    def __init__(self):
        self.series: dict[str, dict] = {}
        self.docs: dict[str, dict] = {}

    def upsert_points(self, key, pts):
        s = self.series.setdefault(key, {})
        for d, v in pts:
            s[d] = v

    def points(self, key, since=None):
        return self.series.get(key, {})

    def put_doc(self, key, payload, source=None):
        self.docs[key] = {"payload": payload, "source": source}

    def doc(self, key):
        d = self.docs.get(key)
        return None if d is None else type("Doc", (), d)()


# ---------- xlsx helper ----------

def test_xlsx_reads_inline_and_shared_strings():
    rows = read_sheet((FIX / "finra_margin_stats.xlsx").read_bytes())
    assert rows[1][0] == "Year-Month"
    assert "Debit Balances" in rows[1][1]
    assert find_row(rows, "2026-08")[1] == "1453832"


def test_xlsx_rejects_garbage():
    with pytest.raises(ValueError):
        read_sheet(b"not a zip at all")


# ---------- TRACE Treasury ----------

def test_trace_treasury_daily_parse():
    data = (FIX / "finra_trace_treasury_daily.xlsx").read_bytes()
    asof, vals = trace_treasury.parse_workbook(data)
    assert asof == date(2026, 10, 1)
    assert vals["trace-ust-par"] == pytest.approx(1743.8)
    assert vals["trace-ust-trades"] == pytest.approx(733730)
    assert vals["trace-ust-bills-par"] == pytest.approx(246.3)
    assert vals["trace-ust-frns-par"] == pytest.approx(1.4)
    assert vals["trace-ust-coupons-par"] == pytest.approx(1469.5)
    assert vals["trace-ust-tips-par"] == pytest.approx(26.6)
    # on-the-run / off-the-run summed across every coupon + TIPS bucket row
    assert vals["trace-ust-onrun-par"] == pytest.approx(1169.8)
    assert vals["trace-ust-offrun-par"] == pytest.approx(326.3)


def test_trace_treasury_monthly_parse():
    data = (FIX / "finra_trace_treasury_monthly.xlsx").read_bytes()
    asof, vals = trace_treasury.parse_workbook(data)
    assert asof == date(2026, 9, 30)
    assert vals["trace-ust-par"] == pytest.approx(28142.0)


def test_trace_treasury_job_probes_back_and_backfills():
    daily = (FIX / "finra_trace_treasury_daily.xlsx").read_bytes()
    monthly = (FIX / "finra_trace_treasury_monthly.xlsx").read_bytes()

    async def fake_bytes(url, params=None):
        if "daily" in url:
            if "2026-10-03" in url or "2026-10-02" in url:
                raise RuntimeError("404")  # weekend: no file
            return daily
        return monthly

    store = FakeStore()
    out = asyncio.run(trace_treasury.fetch_trace_treasury(
        store, fake_bytes, today=date(2026, 10, 3)))
    assert out == trace_treasury.SOURCE
    pts = store.points("cycle:trace-ust-par")
    assert pts[date(2026, 10, 1)] == pytest.approx(1743.8)
    assert pts[date(2026, 9, 30)] == pytest.approx(28142.0)  # monthly backfill
    assert store.points("cycle:trace-ust-frns-par")[date(2026, 10, 1)] == pytest.approx(1.4)
    assert store.points("cycle:trace-ust-onrun-par")[date(2026, 10, 1)] == pytest.approx(1169.8)
    assert store.docs["trace_treasury"]["payload"]["as_of"] == "2026-10-01"


# ---------- TRACE monthly volume ----------

def test_trace_monthly_parse():
    data = (FIX / "finra_trace_monthly_report.xlsx").read_bytes()
    vals = trace_monthly.parse_workbook(data, 2026, 8)
    asof = vals.pop("_asof")
    assert asof == date(2026, 8, 31)
    assert vals["trace-corp-par"] == pytest.approx(1130155.0)
    assert vals["trace-corp-trades"] == pytest.approx(2773027)
    assert vals["trace-corp-cust-share"] == pytest.approx(874291.7 / 1130155.0)
    assert vals["trace-conv-par"] == pytest.approx(95617.0)
    assert vals["trace-conv-trades"] == pytest.approx(37501)
    assert vals["trace-chrc-par"] == pytest.approx(1.5)
    assert vals["trace-chrc-trades"] == pytest.approx(329)
    assert vals["trace-eln-par"] == pytest.approx(1027.9)
    assert vals["trace-eln-trades"] == pytest.approx(5126)
    assert vals["trace-tba-par"] == pytest.approx(5301694.6)
    assert vals["trace-mbs-par"] == pytest.approx(478736.8)
    assert vals["trace-agcy-par"] == pytest.approx(58756.0)


def test_trace_monthly_job_skips_unpublished():
    data = (FIX / "finra_trace_monthly_report.xlsx").read_bytes()

    async def fake_bytes(url, params=None):
        if "2026-09" in url:
            raise RuntimeError("404")  # not yet published
        return data

    store = FakeStore()
    out = asyncio.run(trace_monthly.fetch_trace_monthly(
        store, fake_bytes, today=date(2026, 10, 3)))
    assert out == trace_monthly.SOURCE
    assert store.points("cycle:trace-corp-par")
    assert store.points("cycle:trace-conv-par")
    assert store.points("cycle:trace-corp-cust-share")


# ---------- ICE STAR ----------

def test_ice_star_parse():
    data = (FIX / "ice_star_20230918.zip").read_bytes()
    asof, vals = ice_star.parse_star(data)
    assert asof == date(2023, 9, 18)
    # section A agency $000s summed -> $bn; hand-checked against the sheet
    assert vals["ice-star-agency-par"] > 200  # > $200bn agency par
    assert vals["ice-star-agency-trades"] > 5000
    assert vals["ice-star-nonagency-par"] > 1
    assert vals["ice-star-nonagency-trades"] > 100


def test_ice_star_job():
    data = (FIX / "ice_star_20230918.zip").read_bytes()

    async def fake_bytes(url, params=None):
        if "2023-09-18" not in url:
            raise RuntimeError("404")
        return data

    store = FakeStore()
    out = asyncio.run(ice_star.fetch_ice_star(
        store, fake_bytes, today=date(2023, 9, 19)))
    assert out == ice_star.SOURCE
    assert store.points("cycle:ice-star-agency-par")


# ---------- FINRA short interest ----------

def test_finra_short_parse():
    text = (FIX / "finra_short_sample.csv").read_text()
    asof, parsed = finra_short.parse_short(text)
    assert asof == date(2026, 9, 15)
    assert parsed["_total"] > 0
    for t in ("MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "META"):
        assert parsed[t]["short"] > 0
        assert parsed[t]["adv"] > 0
    assert parsed["NVDA"]["dtc"] is not None


def test_finra_short_job_probes_settlements():
    text = (FIX / "finra_short_sample.csv").read_text()

    async def fake_text(url, params=None, headers=None):
        if "20260915" not in url:
            raise RuntimeError("404")  # not yet published
        return text

    store = FakeStore()
    out = asyncio.run(finra_short.fetch_finra_short(
        store, fake_text, today=date(2026, 10, 3)))
    assert out == finra_short.SOURCE
    assert store.points("cycle:finra-short-total")
    assert store.points("cycle:short-NVDA")
    doc = store.docs["finra_short"]["payload"]
    assert doc["as_of"] == "2026-09-15"
    assert doc["tickers"]["MSFT"]["short"] > 0


# ---------- FINRA margin ----------

def test_finra_margin_parse():
    data = (FIX / "finra_margin_stats.xlsx").read_bytes()
    parsed = finra_margin.parse_workbook(data)
    pts = parsed["finra-margin-debit"]
    assert pts[-1] == (date(2026, 8, 1), pytest.approx(1453832.0))
    assert parsed["finra-margin-debit"][0][0] == date(1997, 1, 1)
    assert parsed["finra-margin-credit-cash"][-1][1] == pytest.approx(207641.0)


def test_finra_margin_job():
    data = (FIX / "finra_margin_stats.xlsx").read_bytes()

    async def fake_bytes(url, params=None):
        return data

    store = FakeStore()
    out = asyncio.run(finra_margin.fetch_finra_margin(store, fake_bytes))
    assert out == finra_margin.SOURCE
    assert store.points("cycle:finra-margin-debit")[date(2026, 8, 1)] > 1e6
    assert store.docs["finra_margin"]["payload"]["as_of"] == "2026-08-01"
