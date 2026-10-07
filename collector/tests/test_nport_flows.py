"""Tests for the N-PORT flow derivation (parse path is the tested unit;
the 420 MB download itself is not unit-testable)."""
import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from collector.fetchers import nport_flows
from collector.fetchers.nport_flows import (
    derive_net_flow,
    parse_nport_flows_zip,
    report_month_dates,
    reported_monthly_net_flow,
)
from collector.store import Store

SUB_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "FILE_NUM", "SUB_TYPE",
            "REPORT_ENDING_PERIOD", "REPORT_DATE", "IS_LAST_FILING"]
INFO_COLS = (["ACCESSION_NUMBER", "SERIES_NAME", "SERIES_ID", "SERIES_LEI",
              "TOTAL_ASSETS", "TOTAL_LIABILITIES", "NET_ASSETS"] +
             [f"{c}_MON{m}" for m in (1, 2, 3)
              for c in ("SALES_FLOW", "REINVESTMENT_FLOW", "REDEMPTION_FLOW")])


def _info_row(**kw):
    row = {c: "" for c in INFO_COLS}
    row.update(kw)
    return row


def _sub_row(**kw):
    row = {c: "" for c in SUB_COLS}
    row.update(kw)
    return row


def make_zip(tmp_path, subs, infos):
    path = tmp_path / "nport.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, cols, rows in (("SUBMISSION.tsv", SUB_COLS, subs),
                                 ("FUND_REPORTED_INFO.tsv", INFO_COLS, infos)):
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, delimiter="\t")
            w.writeheader()
            w.writerows(rows)
            z.writestr(name, buf.getvalue())
    return str(path)


def test_derive_net_flow_identity():
    # TNA 100 -> 110 with 5% return: 110 - 100*1.05 = 5 inflow
    assert derive_net_flow(100.0, 110.0, 0.05) == pytest.approx(5.0)
    # TNA flat at 100 with 5% return implies 5 outflow (distributions)
    assert derive_net_flow(100.0, 100.0, 0.05) == pytest.approx(-5.0)


def test_reported_monthly_net_flow_sales_plus_reinvest_minus_redemption():
    row = _info_row(SALES_FLOW_MON1="100", REINVESTMENT_FLOW_MON1="10",
                    REDEMPTION_FLOW_MON1="40",
                    SALES_FLOW_MON2="", REINVESTMENT_FLOW_MON2="",
                    REDEMPTION_FLOW_MON2="")
    out = reported_monthly_net_flow(row)
    assert out[1] == pytest.approx(70.0)
    assert out[2] is None  # no components reported at all
    assert out[3] is None


def test_report_month_dates_are_month_ends():
    assert report_month_dates(date(2026, 2, 28)) == [
        date(2025, 12, 31), date(2026, 1, 31), date(2026, 2, 28)]


def test_parse_nport_flows_per_fund_monthly_and_aggregate(tmp_path):
    subs = [_sub_row(ACCESSION_NUMBER="0001", FILING_DATE="24-APR-2026",
                     SUB_TYPE="NPORT-P", REPORT_ENDING_PERIOD="31-DEC-2026",
                     REPORT_DATE="28-FEB-2026")]
    infos = [_info_row(ACCESSION_NUMBER="0001", SERIES_NAME="Test Fund",
                       SERIES_ID="S0001", NET_ASSETS="1000",
                       SALES_FLOW_MON1="100", REINVESTMENT_FLOW_MON1="0",
                       REDEMPTION_FLOW_MON1="20",
                       SALES_FLOW_MON2="50", REINVESTMENT_FLOW_MON2="5",
                       REDEMPTION_FLOW_MON2="50",
                       SALES_FLOW_MON3="0", REINVESTMENT_FLOW_MON3="0",
                       REDEMPTION_FLOW_MON3="0")]
    parsed = parse_nport_flows_zip(make_zip(tmp_path, subs, infos))
    assert parsed["n_funds"] == 1
    fund = parsed["funds"][0]
    assert fund["series_id"] == "S0001"
    assert fund["flows"] == [("2025-12-31", 80.0), ("2026-01-31", 5.0),
                             ("2026-02-28", 0.0)]
    assert fund["tna"] == ("2026-02-28", 1000.0)
    assert dict(parsed["agg_flows"]) == {date(2025, 12, 31): 80.0,
                                         date(2026, 1, 31): 5.0,
                                         date(2026, 2, 28): 0.0}
    assert dict(parsed["agg_tna"]) == {date(2026, 2, 28): 1000.0}


def test_parse_nport_flows_amendment_deduped_latest_filing_wins(tmp_path):
    subs = [
        _sub_row(ACCESSION_NUMBER="0001", FILING_DATE="24-APR-2026",
                 SUB_TYPE="NPORT-P", REPORT_DATE="28-FEB-2026"),
        _sub_row(ACCESSION_NUMBER="0002", FILING_DATE="10-MAY-2026",
                 SUB_TYPE="NPORT-P/A", REPORT_DATE="28-FEB-2026"),
    ]
    infos = [
        _info_row(ACCESSION_NUMBER="0001", SERIES_ID="S0001", NET_ASSETS="1000",
                  SALES_FLOW_MON3="100", REDEMPTION_FLOW_MON3="0"),
        _info_row(ACCESSION_NUMBER="0002", SERIES_ID="S0001", NET_ASSETS="1000",
                  SALES_FLOW_MON3="60", REDEMPTION_FLOW_MON3="0"),
    ]
    parsed = parse_nport_flows_zip(make_zip(tmp_path, subs, infos))
    assert parsed["n_funds"] == 1  # one (series, report_date), not two
    fund = parsed["funds"][0]
    assert ("2026-02-28", 60.0) in fund["flows"]
    assert ("2026-02-28", 100.0) not in fund["flows"]


def test_parse_nport_flows_skips_unmatched_accessions(tmp_path):
    subs = []
    infos = [_info_row(ACCESSION_NUMBER="9999", SERIES_ID="S0009",
                       NET_ASSETS="10")]
    with pytest.raises(ValueError, match="no fund rows"):
        parse_nport_flows_zip(make_zip(tmp_path, subs, infos))


async def test_fetch_nport_flows_stores_per_fund_and_aggregate(tmp_path, monkeypatch):
    subs = [_sub_row(ACCESSION_NUMBER="0001", FILING_DATE="24-APR-2026",
                     SUB_TYPE="NPORT-P", REPORT_DATE="28-FEB-2026")]
    infos = [_info_row(ACCESSION_NUMBER="0001", SERIES_NAME="Test Fund",
                       SERIES_ID="S0001", NET_ASSETS="1000",
                       SALES_FLOW_MON3="200", REDEMPTION_FLOW_MON3="50")]
    zip_path = make_zip(tmp_path, subs, infos)

    async def fake_download(url, dest, headers):
        assert "2026q2_nport.zip" in url  # probe newest-first from April 2026
        Path(dest).write_bytes(Path(zip_path).read_bytes())
        return 60_000_000

    monkeypatch.setattr(nport_flows, "_download", fake_download)

    class Cfg:
        user_agent = "test-agent"

    store = Store(tmp_path / "t.db")
    assert await nport_flows.fetch_nport_flows(
        Cfg(), store, today=date(2026, 4, 15)) == "sec-nport-flows"
    pts = store.points("cycle:nport-flow-S0001")
    assert pts[date(2026, 2, 28)] == pytest.approx(150.0)
    agg = store.points("cycle:nport-flow-all")
    assert agg[date(2026, 2, 28)] == pytest.approx(150.0)
    tna = store.points("cycle:nport-tna-S0001")
    assert tna[date(2026, 2, 28)] == pytest.approx(1000.0)
    doc = store.doc("sec_nport_flows")
    assert doc is not None
    assert "mutual funds AND ETFs" in doc.payload["note"]
    assert "60 days" in doc.payload["note"]
