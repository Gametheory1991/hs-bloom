"""batch 13: OFR STFM, FRB (SLOOS + CP), SEC N-CEN, N-PORT, Private Fund Statistics.

All network access is faked. SEC fixtures are fabricated minimal
versions of the real batch formats (TSV zips, XLSX workbook); only the
*formats* mirror SEC, never real fund data.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date

import pytest

from collector.config import load_config
from collector.fetchers import ofr_stfm
from collector.fetchers import frb_ddp
from collector.fetchers import sec_ncen
from collector.fetchers import sec_nport
from collector.fetchers import sec_pfs
from collector.fetchers.sec_ncen import TRUTHY
from collector.store import Store

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

UA = "hs-bloom/1.0 contact harrysugamakc@gmail.com"


def test_sec_flag_truthy_contract():
    assert TRUTHY == {"y", "yes", "true", "1"}


# ---------------------------------------------------------------- STFM ---

def _stfm_get(payload, seen):
    async def fake(url, params=None, headers=None):
        seen.append((url, (params or {}).get("mnemonic")))
        return json.dumps(payload)
    return fake


async def test_stfm_fetch_mnemonic_url_and_parse():
    seen = []
    pts = await ofr_stfm.fetch_mnemonic("FNYR-SOFR-A",
                                         _stfm_get([["2026-10-01", 3.9], [None, 1]], seen))
    assert pts == [(date(2026, 10, 1), 3.9)]  # null date skipped
    assert seen == [("https://data.financialresearch.gov/v1/series/timeseries",
                     "FNYR-SOFR-A")]


async def test_stfm_job_isolates_bad_series(tmp_path):
    cfg_series = load_config(REPO_ROOT / "config.yaml").ofr_stfm[:2]

    async def fake(url, params=None, headers=None):
        m = (params or {}).get("mnemonic")
        if m == cfg_series[0].mnemonic:
            return json.dumps([["2026-10-02", 3.88]])
        raise RuntimeError("boom")

    store = Store(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match=cfg_series[1].id):
        await ofr_stfm.fetch_ofr_stfm(cfg_series, store, fake)
    # the good series still landed
    assert store.points(f"cycle:{cfg_series[0].id}")[date(2026, 10, 2)] == 3.88


# ------------------------------------------------------------- FRB/FRED ---

def _fred_get(obs_by_series):
    async def fake(url, params=None, headers=None):
        sid = (params or {}).get("series_id")
        if sid not in obs_by_series:
            raise RuntimeError(f"unknown {sid}")
        return json.dumps({"observations": [
            {"date": d, "value": v} for d, v in obs_by_series[sid]]})
    return fake


def test_frb_sum_series_aligns_dates():
    parts = [
        [(date(2026, 1, 1), 1.0), (date(2026, 1, 8), 2.0)],
        [(date(2026, 1, 1), 10.0), (date(2026, 1, 15), 99.0)],  # stray date dropped
    ]
    assert frb_ddp.sum_series(parts) == [(date(2026, 1, 1), 11.0)]


async def test_frb_job_fred_and_fred_sum(tmp_path):
    cfgs = load_config(REPO_ROOT / "config.yaml").frb_ddp
    single = next(c for c in cfgs if c.fred == "DRTSCILM")
    total = next(c for c in cfgs if c.id == "cp-total")
    get = _fred_get({
        "DRTSCILM": [("2026-07-01", "1.8")],
        "DFINCP": [("2026-09-23", "100"), ("2026-09-30", "101")],
        "FFINCP": [("2026-09-23", "50"), ("2026-09-30", "51")],
        "ABCOMP": [("2026-09-23", "20"), ("2026-09-30", "21")],
        "NFINCP": [("2026-09-23", "30"), ("2026-09-30", "31")],
        "OTHCOMP": [("2026-09-23", "10"), ("2026-09-30", "11")],
    })
    store = Store(tmp_path / "t.db")
    out = await frb_ddp.fetch_frb_ddp([single, total], store, "k", get)
    assert out == "frb-fred"
    assert store.points("cycle:sloos-ci-large")[date(2026, 7, 1)] == 1.8
    pts = store.points("cycle:cp-total")
    assert pts[date(2026, 9, 23)] == 210.0  # 100+50+20+30+10
    assert pts[date(2026, 9, 30)] == 215.0


async def test_frb_job_isolates_bad_series(tmp_path):
    cfgs = load_config(REPO_ROOT / "config.yaml").frb_ddp
    ok, bad = cfgs[0], cfgs[1]
    get = _fred_get({})  # everything 404s
    store = Store(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match=ok.id):
        await frb_ddp.fetch_frb_ddp([ok, bad], store, "k", get)


# -------------------------------------------------------------- SEC N-CEN ---

NCEN_COLS = ["MONTHLY_AVG_NET_ASSETS", "IS_ETF", "IS_MULTI_INVERSE_INDEX",
             "IS_MONEY_MARKET", "STDV_B4_FEES_AND_EXPENSES",
             "DID_LEND_SECURITIES", "HAS_LINE_OF_CREDIT"]


def _ncen_zip(rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        tsv = "\n".join(["\t".join(NCEN_COLS)] +
                        ["\t".join(r) for r in rows])
        z.writestr("FUND_REPORTED_INFO.tsv", tsv)
        z.writestr("SECURITY_LENDING.tsv", "AVG_VALUE_SEC_LOAN\n1\n")
        # pad past the 100 KB truncated-download guard (real batch ~8.4 MB)
        z.writestr("PADDING.bin", b"0" * 110000)
    return buf.getvalue()


NCEN_ROWS = [
    ["1000", "Y", "", "", "12.0", "Y", "N"],   # ETF, lends
    ["500", "", "Y", "", "25.0", "N", "N"],    # leveraged/inverse
    ["2000", "", "", "Y", "0.1", "N", "Y"],    # money market, credit line
    ["300", "", "", "", "15.0", "N", "N"],     # plain
]


def test_ncen_parse_aggregates():
    agg = sec_ncen.parse_ncen_zip(_ncen_zip(NCEN_ROWS))
    assert agg["ncen-fund-count"] == 4
    assert agg["ncen-aum"] == 3800.0
    assert agg["ncen-etf-count"] == 1 and agg["ncen-etf-aum"] == 1000.0
    assert agg["ncen-levinv-count"] == 1 and agg["ncen-levinv-aum"] == 500.0
    assert agg["ncen-mm-count"] == 1 and agg["ncen-mm-aum"] == 2000.0
    assert agg["ncen-stdv-avg"] == pytest.approx((12.0 + 25.0 + 0.1 + 15.0) / 4)
    assert agg["ncen-seclend-pct"] == 25.0
    assert agg["ncen-loc-pct"] == 25.0


def test_ncen_parse_rejects_garbage():
    with pytest.raises(ValueError, match="not a zip"):
        sec_ncen.parse_ncen_zip(b"nope")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("WRONG.tsv", "x")
    with pytest.raises(ValueError, match="FUND_REPORTED_INFO"):
        sec_ncen.parse_ncen_zip(buf.getvalue())


def test_ncen_quarter_end_and_probe_order():
    assert sec_ncen.quarter_end(2026, 2) == date(2026, 6, 30)
    assert sec_ncen.quarter_end(2025, 4) == date(2025, 12, 31)
    batches = sec_ncen.probe_batches(date(2026, 10, 5))
    assert batches[0] == (2026, 4) and len(batches) == 4


async def test_ncen_job_skips_missing_quarters(tmp_path):
    cfg = load_config(REPO_ROOT / "config.yaml").sec_data
    assert cfg.user_agent == UA

    async def fake(url, headers=None):
        assert headers["User-Agent"] == UA
        if "2026q4_ncen.zip" in url or "2026q3_ncen.zip" in url:
            raise RuntimeError("HTTP 404")
        return _ncen_zip(NCEN_ROWS)

    store = Store(tmp_path / "t.db")
    out = await sec_ncen.fetch_sec_ncen(cfg, store, fake, today=date(2026, 10, 5))
    assert out == "sec-ncen"
    # probed 2026q4 (404), 2026q3 (404), won 2026q2
    assert store.points("cycle:ncen-fund-count")[date(2026, 6, 30)] == 4
    doc = store.doc("sec_ncen").payload
    assert doc["batch"] == "2026q2"
    assert "receipt batch" in doc["note"]


# ------------------------------------------------------------- SEC N-PORT ---

def _nport_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("FUND_REPORTED_INFO.tsv",
                   "NET_ASSETS\tTOTAL_ASSETS\tBORROWING_PAY_WITHIN_1YR\n"
                   "1000\t1200\t50\n2000\t2100\t0\n")
        z.writestr("FUT_FWD_NONFOREIGNCUR_CONTRACT.tsv",
                   "CONTRACT_ID\tNOTIONAL_AMOUNT\n1\t10\n2\t20\n")
        z.writestr("SECURITIES_LENDING.tsv",
                   "FUND_ID\tVALUE\nA\t5\n")
        # holding-level detail must be ignored, not read
        z.writestr("FUND_REPORTED_HOLDING.tsv", "HOLDING_ID\n" * 10000)


def test_nport_parse_aggregates(tmp_path):
    p = tmp_path / "2026q2_nport.zip"
    _nport_zip(str(p))
    agg = sec_nport.parse_nport_zip(str(p))
    assert agg["nport-net-assets"] == 3000.0
    assert agg["nport-total-assets"] == 3300.0
    assert agg["nport-borrow-1y"] == 50.0
    assert agg["nport-deriv-notional"] == 30.0
    assert agg["nport-deriv-schedules"] == 1
    assert agg["nport-seclend"] == 5.0
    assert agg["nport-fund-count"] == 2


def test_nport_column_detection_fallback():
    assert sec_nport._notional_columns(["NOTIONAL_AMOUNT", "X"]) == ["NOTIONAL_AMOUNT"]
    assert sec_nport._notional_columns(["value"]) == ["value"]
    assert sec_nport._notional_columns([]) == []


# ----------------------------------------------------------------- SEC PFS ---

def _pfs_xlsx() -> bytes:
    """Fabricated minimal workbook with PFS-like risk tables."""
    def row(r, cells):
        out = [f'<row r="{r}">']
        for col, (kind, val) in cells:
            if kind == "s":
                out.append(f'<c r="{col}{r}" t="inlineStr"><is><t>{val}</t></is></c>')
            else:
                out.append(f'<c r="{col}{r}"><v>{val}</v></c>')
        return "".join(out) + "</row>"

    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + row(1, [("A", ("s", "Hedge fund gross leverage (assets to NAV)")),
                  ("B", ("n", "3.1")), ("C", ("n", "3.2")), ("D", ("n", "3.4"))])
        + row(2, [("A", ("s", "Gross leverage header")), ("B", ("s", "Q1"))])  # no numerics: skipped
        + row(3, [("A", ("s", "Total borrowing by private funds")),
                  ("B", ("n", "100")), ("C", ("n", "110"))])
        + row(4, [("A", ("s", "Hedge fund borrowing ($bn)")),
                  ("B", ("n", "200")), ("C", ("n", "210"))])  # hedge row wins
        + row(5, [("A", ("s", "Gross notional exposure to NAV, hedge funds")),
                  ("B", ("n", "2.0")), ("C", ("n", "2.5"))])
        + row(6, [("A", ("s", "Liquidity: investor vs portfolio mismatch (pp)")),
                  ("B", ("n", "5")), ("C", ("n", "7"))])
        + "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Override PartName="/xl/workbook.xml" ContentType="x"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="x"/></Types>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<sheets><sheet name="HF Tables" sheetId="1"/></sheets></workbook>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        # pad past the 50 KB truncated-download guard (real xlsx ~592 KB)
        z.writestr("padding.bin", b"0" * 60000)
    return buf.getvalue()


def test_pfs_resolve_url_picks_newest():
    html = ('<a href="/files/investment/private-funds-statistics-2025-q2-supporting-data.xlsx">'
            '<a href="/files/investment/private-funds-statistics-2025-q3-supporting-data.xlsx">')
    url, year, q = sec_pfs.resolve_xlsx_url(html)
    assert (year, q) == (2025, 3)
    assert url == "https://www.sec.gov/files/investment/private-funds-statistics-2025-q3-supporting-data.xlsx"


def test_pfs_anchor_parse():
    parsed = sec_pfs.parse_pfs_workbook(_pfs_xlsx())
    assert parsed["values"]["pfs-hf-leverage"] == 3.4       # last numeric cell
    assert parsed["values"]["pfs-hf-borrowing"] == 210.0    # hedge row preferred
    assert parsed["values"]["pfs-hf-gne-nav"] == 2.5
    assert parsed["values"]["pfs-hf-liq-mismatch"] == 7.0
    assert parsed["missed"] == []
    assert "hedge fund gross leverage" in parsed["found"]["pfs-hf-leverage"].lower()


def test_pfs_missed_anchor_reported():
    with pytest.raises(ValueError, match="no PFS risk tables"):
        sec_pfs.parse_pfs_workbook(
            _pfs_xlsx(), metrics={"nope": (("zzz-no-such-table",), True)})


async def test_pfs_job_end_to_end(tmp_path):
    cfg = load_config(REPO_ROOT / "config.yaml").sec_data
    html = ('<a href="/files/investment/private-funds-statistics-2025-q3-supporting-data.xlsx">x</a>')

    async def fake(url, headers=None):
        assert headers["User-Agent"] == UA
        return html.encode() if url == sec_pfs.INDEX_URL else _pfs_xlsx()

    store = Store(tmp_path / "t.db")
    out = await sec_pfs.fetch_sec_pfs(cfg, store, fake)
    assert out == "sec-pfs"
    assert store.points("cycle:pfs-hf-leverage")[date(2025, 9, 30)] == 3.4
    doc = store.doc("sec_pfs").payload
    assert doc["report"] == "2025q3"
    assert doc["missed"] == []


# ------------------------------------------------------------------ config ---

def test_config_batch13_sections():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert len(cfg.ofr_stfm) == 7
    assert {s.id for s in cfg.ofr_stfm} == {
        "stfm-sofr", "stfm-repo-dvp", "stfm-mmf-total",
        "stfm-dealer-fail-deliver", "stfm-dealer-fail-receive",
        "stfm-dealer-repo", "stfm-dealer-rrp"}
    assert len(cfg.frb_ddp) == 7
    cp_total = next(s for s in cfg.frb_ddp if s.id == "cp-total")
    assert cp_total.fred_sum == ["DFINCP", "FFINCP", "ABCOMP", "NFINCP", "OTHCOMP"]
    assert next(s for s in cfg.frb_ddp if s.id == "sloos-ci-large").fred == "DRTSCILM"
    hf_ids = [s.id for s in cfg.cycle_series if s.id.startswith("hf-")]
    assert len(hf_ids) == 28  # 3 pre-existing + 25 new
    for want in ("hf-ust-short", "hf-nav", "hf-ird-gne", "hf-rv-lev",
                 "hf-credit-gne", "hf-eq-gne", "hf-fx-gne",
                 "hf-repo-share-top10", "hf-pb-borrow", "hf-repo-repo",
                 "hf-repo-rrepo", "hf-rv-cash", "hf-cds-stress-p5"):
        assert want in hf_ids
    sec_ids = [s.id for s in cfg.cycle_series if s.id.split("-")[0] in ("ncen", "nport", "pfs")]
    assert len(sec_ids) == 20
    for name in ("ofr_stfm", "frb_ddp", "sec_ncen", "sec_nport", "sec_pfs"):
        assert name in cfg.cadences


def test_panels_wire_new_series():
    cfg = load_config(REPO_ROOT / "config.yaml")
    tabs = {t.label: t for t in cfg.cycle_tabs}
    quant = " ".join(p.title for p in tabs["QUANT"].panels)
    assert "HF LEVERAGE BY STRATEGY & SIZE" in quant
    assert "HF ASSET-CLASS EXPOSURE" in quant
    assert "HF STRESS" in quant
    struct = " ".join(p.title for p in tabs["STRUCT"].panels)
    assert "SHORT-TERM FUNDING (OFR STFM)" in struct
    risk = " ".join(p.title for p in tabs["RISK"].panels)
    assert "BANK LENDING & WHOLESALE FUNDING (FRB)" in risk
    assert "6MO HOLDINGS LAG" in risk  # N-PORT lag note on the panel label
    credit = " ".join(r.series for p in tabs["CREDIT"].panels for r in p.rows)
    assert "sloos-ci-large" in credit and "cp-abcp" in credit
