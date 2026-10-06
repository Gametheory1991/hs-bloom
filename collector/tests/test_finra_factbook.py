"""Tests for the FINRA TRACE Fact Book fetcher.

All parsing is offline against synthetic workbooks built in-test with the
stdlib (zipfile + inline strings) — no openpyxl dependency, no /tmp files,
no network. The fetch job runs against fake get_text/get_bytes.
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date
from xml.sax.saxutils import escape

import pytest

from collector.fetchers import finra_factbook as fb


@pytest.fixture(autouse=True)
def _no_request_gap(monkeypatch):
    """Keep the suite fast: the polite request gap is for production."""
    monkeypatch.setattr(fb, "REQUEST_GAP", 0.0)


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


# ---------- synthetic xlsx builder (stdlib only) ----------

def _col_letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(sheets: list[tuple[str, list[list]]]) -> bytes:
    """sheets: [(name, rows)]; cell values str | int | float | None."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = "\n".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.'
            f'spreadsheetml.worksheet+xml"/>'
            for i in range(len(sheets)))
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns='
                   '"http://schemas.openxmlformats.org/package/2006/'
                   'content-types">\n<Default Extension="rels" ContentType='
                   '"application/vnd.openxmlformats-package.relationships'
                   '+xml"/>\n<Default Extension="xml" ContentType='
                   '"application/xml"/>\n<Override PartName="/xl/workbook.xml"'
                   ' ContentType="application/vnd.openxmlformats-'
                   'officedocument.spreadsheetml.sheet.main+xml"/>\n'
                   + overrides + "\n</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships '
                   'xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Type="http://'
                   'schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        sheet_tags = "".join(
            f'<sheet name="{escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, (name, _) in enumerate(sheets))
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<workbook xmlns='
                   '"http://schemas.openxmlformats.org/spreadsheetml/2006/'
                   'main" xmlns:r="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships"><sheets>'
                   + sheet_tags + "</sheets></workbook>")
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.'
            f'openxmlformats.org/officeDocument/2006/relationships/worksheet"'
            f' Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets)))
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships '
                   'xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships">' + rels + "</Relationships>")
        for i, (_, rows) in enumerate(sheets):
            row_xml = []
            for ri, row in enumerate(rows, start=1):
                cells = []
                for ci, v in enumerate(row):
                    if v is None:
                        continue
                    ref = f"{_col_letters(ci)}{ri}"
                    if isinstance(v, str):
                        cells.append(
                            f'<c r="{ref}" t="inlineStr"><is><t>{escape(v)}'
                            f"</t></is></c>")
                    else:
                        cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                row_xml.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
            z.writestr(
                f"xl/worksheets/sheet{i + 1}.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n<worksheet xmlns='
                '"http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>" + "".join(row_xml) + "</sheetData></worksheet>")
    return buf.getvalue()


# ---------- fixture workbooks ----------

TOP50_IG = [
    ["Top 50 Publicly Traded Investment-Grade Issues by Number of Trades"],
    ["Rank", "SYMBOL", "ISSUER NAME", "COUPON", "MATURITY", "",
     "RATING", "TRADES", "", "DEALERS REPORTING"],
    [1, "AXP6059330", "AMERICAN EXPRESS CO", 5.667, "2036-04-25 00:00:00", "",
     "A", 62158, "", 61],
    [2, "LOW5159086", "LOWES COS INC", 2.625, "2031-04-01 00:00:00", "",
     "BBB", 41649, "", 90],
]

IG_TRADES = [
    ["Investment Grade S1 Trades (excluding convertibles)"],
    ["(Average Daily)", "Q2 2026"],
    ["Total", 112117.306],
    ["144A Issues Only", 4284.71],
    ["Publicly Traded", 107832.60],
    [">= 25,000,000", 131.60],
    [">= 10,000,000 < 25,000,000", 856.66],
    [">= 5,000,000 < 10,000,000", 1443.29],
    [">= 1,000,000 < 5,000,000", 6717.18],
    [">= 100,000 < 1,000,000", 23760.11],
    ["< 100,000", 79208.47],
    ["Customer Buy", 39994.16],
]

IG_BUYSELL = [
    ["Ratio of Investment Grade Customer Buy to Customer Sell Par Value"],
    ["", "Q2 2026"],
    ["", "Gross", "Net", "Ratio"],
    [">= 25,000,000", 219496273673.74, -22927391673.74, 0.8108],
    [">= 10,000,000 < 25,000,000", 485866379500.85, -31267391824.81, 0.8791],
    [">= 5,000,000 < 10,000,000", 344770579173.49, -6674384621.15, 0.9620],
    [">= 1,000,000 < 5,000,000", 448235131252.87, -10864329318.11, 0.9527],
    [">= 100,000 < 1,000,000", 227579776579.35, 6014135622.17, 1.0543],
    ["< 100,000", 50230604139.33, 9817509335.07, 1.4859],
    ["<1 Yr. Maturity Band", 122870175772.89, -2903172640.91, 0.9538],
    ["   AAA", 3100891836, 86296000, 1.0573],
]

INDEX_HTML = """
<html><body>
<h2>2026</h2><h3>Quarterly Tables</h3>
<a href="/sites/default/files/2026-08/Q22026-Corporate-Bond-Tables.xlsx">Corporate Bond Tables</a>
<a href="/sites/default/files/2026-08/Q22026-Agency-Debt-Tables.xlsx">Agency Debt Tables</a>
<a href="/sites/default/files/2026-08/Q22026-Securitized-Product-Tables.xlsx">Securitized Product Tables</a>
<a href="/sites/default/files/2026-05/Q12026-Corporate-Bond-Tables.xlsx">Corporate Bond Tables</a>
<h2>2025</h2><h3>Annual Tables</h3>
<a href="/sites/default/files/2026-03/2025-Corporate-Bond-Tables.xlsx">Corporate Bond Tables</a>
</body></html>
"""


def _corp_workbook() -> bytes:
    return make_xlsx([
        ("Top 50 IG", TOP50_IG),
        ("Top 50 IG PV", [
            ["Top 50 Publicly Traded Investment-Grade Issues by Par Value"],
            ["Rank", "SYMBOL", "ISSUER NAME", "COUPON", "MATURITY", "",
             "RATING", "PAR VALUE", "", "DEALERS REPORTING"],
            [1, "PEMX5677787", "PETROLEOS MEXICANOS", 7.69,
             "2050-01-23 00:00:00", "", "B", 3014053000, "", 96],
        ]),
        ("IG Trades", IG_TRADES),
        ("IG PV", [
            ["Investment Grade S1 Par Value Traded"],
            ["(Average Daily)", "Q2 2026"],
            ["Total", 46900007980.40],
            [">= 25,000,000", 4951362010.87],
            [">= 10,000,000 < 25,000,000", 11232347277.92],
            [">= 5,000,000 < 10,000,000", 8791846626.79],
            [">= 1,000,000 < 5,000,000", 6717177483.55],
            [">= 100,000 < 1,000,000", 2376011290.11],
            ["< 100,000", 792084677.41],
        ]),
        ("IG Buy-Sell Ratio", IG_BUYSELL),
    ])


# ---------- unit tests ----------

def test_quarter_labels():
    assert fb.parse_quarter_label("Q2 2026") == (2026, 2)
    assert fb.parse_quarter_label("Q4 2025") == (2025, 4)
    assert fb.parse_quarter_label("q1 2020") == (2020, 1)
    assert fb.parse_quarter_label("2026") is None
    assert fb.parse_quarter_label("") is None


def test_resolve_workbook_urls_picks_latest_per_kind():
    urls = fb.resolve_workbook_urls(INDEX_HTML)
    assert set(urls) == {"corp", "agency", "sec"}
    corp = urls["corp"]
    # newest first; annual link (no QnYYYY stamp) ignored
    assert corp[0][1:] == (2026, 2)
    assert corp[1][1:] == (2026, 1)
    assert len(corp) == 2
    assert corp[0][0].startswith("https://www.finra.org/")
    assert urls["agency"][0][1:] == (2026, 2)
    assert urls["sec"][0][1:] == (2026, 2)


def test_resolve_workbook_urls_empty():
    assert fb.resolve_workbook_urls("<html></html>") == {}
    assert fb.resolve_workbook_urls("") == {}


def test_parse_top50():
    data = make_xlsx([("Top 50 IG", TOP50_IG)])
    rows = fb.read_sheet(data, sheet=1)
    out = fb.parse_top50(rows, "trades")
    assert len(out) == 2
    first = out[0]
    assert first["rank"] == 1
    assert first["symbol"] == "AXP6059330"
    assert first["issuer"] == "AMERICAN EXPRESS CO"
    assert first["coupon"] == pytest.approx(5.667)
    assert first["maturity"] == "2036-04-25"
    assert first["rating"] == "A"
    assert first["trades"] == 62158
    assert first["dealers"] == 61
    assert "pv" not in first


def test_parse_top50_pv_kind():
    data = make_xlsx([("Top 50 IG PV", [
        ["title"],
        ["Rank", "SYMBOL", "ISSUER NAME", "COUPON", "MATURITY", "",
         "RATING", "PAR VALUE", "", "DEALERS REPORTING"],
        [1, "PEMX5677787", "PETROLEOS MEXICANOS", 7.69,
         "2050-01-23 00:00:00", "", "B", 3014053000, "", 96],
    ])])
    out = fb.parse_top50(fb.read_sheet(data, sheet=1), "pv")
    assert out[0]["pv"] == 3014053000
    assert "trades" not in out[0]


def test_parse_block_headline():
    data = make_xlsx([("IG Trades", IG_TRADES)])
    b = fb.parse_block(fb.read_sheet(data, sheet=1))
    assert b["quarter"] == "Q2 2026"
    assert b["total"] == pytest.approx(112117.306)
    assert len(b["buckets"]) == 6
    assert b["buckets"][0] == ("ge25m", pytest.approx(131.60))
    assert b["buckets"][-1] == ("lt100k", pytest.approx(79208.47))
    # "Customer Buy" after the 6 buckets is not absorbed
    assert all(k in dict(fb.BUCKETS).values() for k, _ in b["buckets"])


def test_parse_block_stops_at_second_total():
    data = make_xlsx([("P1 Trades", [
        ["Corporate P1 Trades"],
        ["(Average Daily)", "Q1 2026"],
        ["Total", 15226.74],
        ["144A", 568.55],
        [">= 25,000,000", 136.89],
        [">= 10,000,000 < 25,000,000", 212.27],
        [">= 5,000,000 < 10,000,000", 223.71],
        [">= 1,000,000 < 5,000,000", 759.76],
        [">= 100,000 < 1,000,000", 2527.73],
        ["< 100,000", 11366.39],
        ["Excluding Convertibles"],
        ["Total", 15034.48],
        [">= 25,000,000", 130.71],
    ])])
    b = fb.parse_block(fb.read_sheet(data, sheet=1))
    assert b["total"] == pytest.approx(15226.74)
    assert len(b["buckets"]) == 6
    assert b["buckets"][0][1] == pytest.approx(136.89)


def test_parse_buysell_headline_only():
    data = make_xlsx([("IG Buy-Sell Ratio", IG_BUYSELL)])
    bs = fb.parse_buysell(fb.read_sheet(data, sheet=1))
    assert bs["quarter"] == "Q2 2026"
    assert len(bs["rows"]) == 6  # stops before "<1 Yr. Maturity Band"
    bkey, gross, net, ratio = bs["rows"][0]
    assert bkey == "ge25m"
    assert gross == pytest.approx(219496273673.74)
    assert net == pytest.approx(-22927391673.74)
    assert ratio == pytest.approx(0.8108)


def test_parse_buysell_skips_parent_row():
    # TBA's sheet nests the buckets under a "Good Delivery" parent row
    data = make_xlsx([("TBA Buy-Sell Ratio", [
        ["Ratio of TBA Customer Buy to Customer Sell Par Value"],
        ["", "Q3 2026"],
        ["", "Gross", "Net", "Ratio"],
        ["Good Delivery", 14354703284466, 82399749858, 1.0115],
        [">= 25,000,000", 13596467493490, 130828114158, 1.0194],
        ["< 100,000", 154111731, 15741137, 1.2275],
        ["5 Yr Maturity Band", 999, 1, 1.0],
    ])])
    bs = fb.parse_buysell(fb.read_sheet(data, sheet=1))
    assert bs["quarter"] == "Q3 2026"
    assert [b for b, _, _, _ in bs["rows"]] == ["ge25m", "lt100k"]
    assert bs["rows"][0][3] == pytest.approx(1.0194)


def test_clean_date_handles_excel_serials():
    assert fb._clean_date("2036-04-25 00:00:00") == "2036-04-25"
    assert fb._clean_date("49790") == "2036-04-25"  # Excel serial
    assert fb._clean_date("5.667") is None  # a coupon, not a date
    assert fb._clean_date("") is None
    assert fb._clean_date(None) is None


def test_parse_workbook_corp():
    p = fb.parse_workbook("corp", _corp_workbook())
    assert p["quarter"] == "Q2 2026"
    assert p["qdate"] == date(2026, 6, 30)
    s = p["series"]
    assert s["fb-ig-trades"] == pytest.approx(112117.306)
    assert s["fb-ig-pv"] == pytest.approx(46900007980.40)
    assert s["fb-ig-trades-b-ge25m"] == pytest.approx(131.60)
    assert s["fb-ig-bsr-b-ge25m"] == pytest.approx(0.8108)
    assert s["fb-ig-bsg-b-lt100k"] == pytest.approx(50230604139.33)
    assert s["fb-ig-bsn-b-lt100k"] == pytest.approx(9817509335.07)
    assert p["top"]["ig_trades"][0]["symbol"] == "AXP6059330"
    assert p["top"]["ig_pv"][0]["pv"] == 3014053000
    assert p["headlines"]["ig"]["trades"] == pytest.approx(112117.306)
    # HY / Conv sheets absent from the synthetic workbook -> recorded
    assert any(m.startswith("hy:") for m in p["missing"])


def test_sheet_match_tolerates_renames():
    data = make_xlsx([("Top 50 IG Issues", TOP50_IG)])
    sheets = fb._sheet_map(data)
    assert fb._match_sheet(sheets, "Top 50 IG") == 1
    assert fb._match_sheet(sheets, "No Such Sheet") is None


# ---------- end-to-end ----------

def _agency_workbook() -> bytes:
    return make_xlsx([
        ("Top 50 Trades", [
            ["Top 50 Agency Issues by Number of Trades"],
            ["Rank", "SYMBOL", "ISSUER NAME", "COUPON", "MATURITY", "",
             "RATING", "TRADES", "", "DEALERS REPORTING"],
            [1, "FNMA.RY", "FEDERAL NATL MTG ASSN", 6.625,
             "2030-11-15 00:00:00", "", "AA", 5121, "", 82],
        ]),
        ("S1 Trades", [
            ["Agency S1 Trades"],
            ["(Average Daily)", "Q2 2026"],
            ["Total", 3379.355],
            [">= 25,000,000", 337.94],
            [">= 10,000,000 < 25,000,000", 168.97],
            [">= 5,000,000 < 10,000,000", 135.17],
            [">= 1,000,000 < 5,000,000", 675.87],
            [">= 100,000 < 1,000,000", 1013.81],
            ["< 100,000", 1047.60],
        ]),
        ("S1 Par Value", [
            ["Agency S1 Par Value Traded"],
            ["(Average Daily)", "Q2 2026"],
            ["Total", 1212434612.90],
            [">= 25,000,000", 121243461.29],
            [">= 10,000,000 < 25,000,000", 242486922.58],
            [">= 5,000,000 < 10,000,000", 363730383.87],
            [">= 1,000,000 < 5,000,000", 242486922.58],
            [">= 100,000 < 1,000,000", 121243461.29],
            ["< 100,000", 121243461.29],
        ]),
        ("Buy-Sell Ratio S1 Par Value", [
            ["Ratio of Customer Buy to Customer Sell Par Value"],
            ["", "Q2 2026"],
            ["", "Gross", "Net", "Ratio"],
            [">= 25,000,000", 82283116798.94, 37185914000, 2.6491],
            [">= 10,000,000 < 25,000,000", 14815893077.39, 9735476906.25, 4.8326],
            [">= 5,000,000 < 10,000,000", 8665867244.52, 5892538556.56, 5.2494],
            [">= 1,000,000 < 5,000,000", 11552720006.77, 7924673709.57, 5.3686],
            [">= 100,000 < 1,000,000", 4796869744, 3076689727.64, 4.5772],
            ["< 100,000", 1789073933.73, 1135873575.75, 4.4779],
        ]),
    ])


def _sec_workbook() -> bytes:
    return make_xlsx([
        ("TBA Trades", [
            ["TBA Trades"],
            ["(Average Daily)", "Q2 2026"],
            ["Total", 8441.113],
            ["Good Delivery", 8378.935],
            ["   Customer Buy", 1849.774],
            [">= 25,000,000", 2168.016],
            [">= 10,000,000 < 25,000,000", 1082.048],
            [">= 5,000,000 < 10,000,000", 1893.710],
            [">= 1,000,000 < 5,000,000", 2372.694],
            [">= 100,000 < 1,000,000", 799.871],
            ["< 100,000", 62.597],
        ]),
        ("TBA PB", [
            ["TBA Principal Balance Traded"],
            ["(Average Daily)", "Q2 2026"],
            ["Total", 333442157096.31],
            [">= 25,000,000", 33344215709.63],
            [">= 10,000,000 < 25,000,000", 66688431419.26],
            [">= 5,000,000 < 10,000,000", 100032647128.89],
            [">= 1,000,000 < 5,000,000", 100032647128.89],
            [">= 100,000 < 1,000,000", 33344215709.63],
            ["< 100,000", 0.01],
        ]),
        ("TBA Buy-Sell Ratio", [
            ["Ratio of TBA Customer Buy to Customer Sell Par Value"],
            ["", "Q2 2026"],
            ["", "Gross", "Net", "Ratio"],
            [">= 25,000,000", 13596467493490, 130828114158, 1.0194],
            [">= 10,000,000 < 25,000,000", 417718235615, -36613559363, 0.8388],
            [">= 5,000,000 < 10,000,000", 155083784911, -3877167429, 0.9512],
            [">= 1,000,000 < 5,000,000", 168735880034, -8308658898, 0.9061],
            [">= 100,000 < 1,000,000", 16543778685, 355280253, 1.0439],
            ["< 100,000", 154111731, 15741137, 1.2275],
        ]),
    ])


def test_fetch_end_to_end():
    corp = _corp_workbook()
    agency = _agency_workbook()
    sec = _sec_workbook()

    async def fake_get_text(url, headers=None):
        assert "trace-fact-book" in url
        assert headers and "harrysugamakc@gmail.com" in headers["User-Agent"]
        return INDEX_HTML

    async def fake_get_bytes(url, headers=None):
        assert headers and "harrysugamakc@gmail.com" in headers["User-Agent"]
        if "Corporate" in url:
            return corp
        if "Agency" in url:
            return agency
        if "Securitized" in url:
            return sec
        raise AssertionError(url)

    store = FakeStore()
    src = asyncio.run(fb.fetch_finra_factbook(store, fake_get_text, fake_get_bytes))
    assert src == "finra-factbook"

    qd = date(2026, 6, 30)
    assert store.points("cycle:fb-ig-trades") == {qd: pytest.approx(112117.306)}
    assert store.points("cycle:fb-ig-bsr-b-ge25m")[qd] == pytest.approx(0.8108)
    assert store.points("cycle:fb-agency-trades")[qd] == pytest.approx(3379.355)
    assert store.points("cycle:fb-tba-pv")[qd] == pytest.approx(333442157096.31)
    assert store.points("cycle:fb-tba-trades-b-ge25m")[qd] == pytest.approx(2168.016)

    doc = store.docs["finra_factbook"]
    assert doc["source"] == "finra-factbook"
    p = doc["payload"]
    assert p["as_of"] == "Q2 2026"
    assert p["quarter_end"] == "2026-06-30"
    assert p["top"]["ig_trades"][0]["symbol"] == "AXP6059330"
    assert p["top"]["agency_trades"][0]["symbol"] == "FNMA.RY"
    assert p["headlines"]["ig"]["trades"] == pytest.approx(112117.306)
    assert p["buy_sell_latest"]["ig"][0]["bucket"] == "ge25m"
    assert p["buy_sell_latest"]["ig"][0]["ratio"] == pytest.approx(0.8108)
    assert p["buckets_latest"]["tba"]["trades"]["ge25m"] == pytest.approx(2168.016)
    # empty store -> backfill: the Q1 corp URL is fetched too (the fake serves
    # the same bytes, so its sheet label still reads Q2 2026; idempotent)
    quals = [(q["kind"], q["quarter"]) for q in p["quarters"]]
    assert len(quals) == 4
    assert {k for k, _ in quals} == {"corp", "agency", "sec"}
    assert all(q == "Q2 2026" for _, q in quals)


def test_fetch_missing_kind_still_works():
    async def fake_get_text(url, headers=None):
        return ('<a href="/sites/default/files/2026-08/'
                'Q22026-Corporate-Bond-Tables.xlsx">Corporate Bond Tables</a>')

    async def fake_get_bytes(url, headers=None):
        return _corp_workbook()

    store = FakeStore()
    src = asyncio.run(fb.fetch_finra_factbook(store, fake_get_text, fake_get_bytes))
    assert src == "finra-factbook"
    assert store.docs["finra_factbook"]["payload"]["as_of"] == "Q2 2026"


def test_fetch_raises_when_no_links():
    async def fake_get_text(url, headers=None):
        return "<html><body>no links here</body></html>"

    async def fake_get_bytes(url, headers=None):  # pragma: no cover
        raise AssertionError("should not be called")

    with pytest.raises(ValueError, match="no fact-book workbook links"):
        asyncio.run(fb.fetch_finra_factbook(FakeStore(), fake_get_text,
                                            fake_get_bytes))


# ---------- annual workbooks: fixtures ----------

ANNUAL_INDEX_HTML = """
<html><body>
<h2>Annual Tables</h2>
<a href="/sites/default/files/2026-02/2025-Transaction-Information-Corporate.xlsx">Corporate Transaction</a>
<a href="/sites/default/files/2026-02/2025-Transaction-Information-Agency.xlsx">Agency Transaction</a>
<a href="/sites/default/files/2026-02/2025-Transaction-Information-Securitized-Products.xlsx">Securitized Transaction</a>
<a href="/sites/default/files/2026-02/2025-Issue-Information-Corporate.xlsx">Corporate Issues</a>
<a href="/sites/default/files/2026-02/2025-Issue-Information-Agency.xlsx">Agency Issues</a>
<a href="/sites/default/files/2025-02/2024-Transaction-Information-Corporate.xlsx">2024 Corporate Transaction</a>
<a href="/sites/default/files/2026-08/Q22026-Corporate-Bond-Tables.xlsx">Q2 2026 Corporate</a>
</body></html>
"""

# Corporate-style Graph Data TIME SEGMENTS block. Time labels arrive as
# Excel day-fractions (0.34375 = 08:15 end -> bucket start 08:00).
GRAPH_DATA_CORP = [
    ["Corporate Transaction Information"],
    ["", "Q1 2025", "Q2 2025"],
    ["", "S1 TRADES", "", "S1 PAR VALUE", "", "AVERAGE S1 TRADE SIZE"],
    ["TIME SEGMENTS", "Percentage Executed", "Cumulative Percentage",
     "Percentage Executed", "Cumulative Percentage"],
    [0.34375, 0.0095, 0.0095, 0.0088, 0.0088, 395450.0],
    [0.3541666666666667, 0.0071, 0.0166, 0.0108, 0.0196, 643400.0],
    ["After Hours", 0.01404, 1.0, 0.03352, 1.0, 1017990.0],
    ["© 2006-26 FINRA"],
]

# Securitized-style block: range labels, OPB/RPB instead of avg size.
GRAPH_DATA_SEC = [
    ["Securitized Transaction Information"],
    ["", "Q1 2025"],
    ["ABS", "ABS Auto"],
    ["", "Trades", "", "Original Principal Balance", "",
     "Remaining Principal Balance"],
    ["TIME SEGMENTS", "Percentage Executed", "Cumulative Percentage",
     "Percentage Executed", "Cumulative Percentage",
     "Percentage Executed", "Cumulative Percentage"],
    ["08:00 AM - 08:14 AM", 0.00168, 0.00168, 0.00149, 0.00149,
     0.00143, 0.00143],
    ["After Hours", 0.0016, 1.0, 0.0008, 1.0, 0.0008, 1.0],
]

TIME_TABLE_TRADES = [
    ["Percentage of Corporate S1 Trades Within Time Segments"],
    ["", 2023, 2024, 2025, "", "Q1 2025"],
    ["8:00 AM - 9:59 AM", 0.1393, 0.1064, 0.1219, "", 0.1219],
    ["After Hours", 0.03463, 0.01405, 0.01404, "", 0.01522],
    ["© 2006-26 FINRA"],
]

TIME_TABLE_PAR = [
    ["Percentage of Corporate S1 Par Value Traded Within Time Segments"],
    ["", 2023, 2024, 2025, "", "Q1 2025"],
    ["8:00 AM - 9:59 AM", 0.15, 0.12, 0.13574, "", 0.13],
    ["After Hours", 0.04, 0.03, 0.03352, "", 0.034],
]

ANNUAL_TOP_C3 = [
    ["Top 50 Publicly Traded Corporate Investment-Grade Issues by Number of Trades"],
    ["Rank", "SYMBOL", "ISSUER NAME", "COUPON", "MATURITY", "",
     "RATING", "TRADES", "", "DEALERS REPORTING"],
    [1, "ANTM5923070", "ANTHEM INC", 5.2, "2035-02-15 00:00:00", "",
     "BBB", 62258, "", 114],
    [2, "SBUX4984105", "STARBUCKS CORP", 2.55, "2030-11-15 00:00:00", "",
     "BBB", 59660, "", 98],
]


# ---------- annual unit tests ----------

def test_resolve_annual_urls():
    urls = fb.resolve_annual_urls(ANNUAL_INDEX_HTML)
    assert set(urls) == {"transaction", "issue"}
    txn = urls["transaction"]
    # latest first per prod; 2024 corp also listed
    assert txn["corp"][0][1] == 2025
    assert txn["corp"][1][1] == 2024
    assert txn["agency"][0][1] == 2025
    assert txn["sec"][0][1] == 2025
    assert urls["issue"]["corp"][0][1] == 2025
    assert urls["issue"]["agency"][0][1] == 2025
    # quarterly link never matches the annual pattern
    assert all("Q2" not in u for u, _ in txn["corp"])
    assert fb.resolve_annual_urls("") == {}


def test_norm_fine_label():
    assert fb._norm_fine_label("After Hours") == "After Hours"
    assert fb._norm_fine_label("0.34375") == "08:00"      # 08:15 end serial
    assert fb._norm_fine_label("0.3541666666666667") == "08:15"  # 08:30 end
    assert fb._norm_fine_label("08:15:00") == "08:00"    # formatted end time
    assert fb._norm_fine_label("18:30:00") == "18:15"
    assert fb._norm_fine_label("08:00 AM - 08:14 AM") == "08:00"
    assert fb._norm_fine_label("02:00 PM - 02:14 PM") == "14:00"
    assert fb._norm_fine_label("12:00 AM - 12:14 AM") == "00:00"
    assert fb._norm_fine_label("nonsense") is None
    assert fb._norm_fine_label("") is None


def test_parse_time_table():
    data = make_xlsx([("Table C38", TIME_TABLE_TRADES)])
    out = fb.parse_time_table(fb.read_sheet(data, sheet=1))
    assert set(out) == {"8:00 AM - 9:59 AM", "After Hours"}
    ah = out["After Hours"]
    assert ah["2023"] == pytest.approx(0.03463)
    assert ah["2025"] == pytest.approx(0.01404)
    assert ah["Q1 2025"] == pytest.approx(0.01522)
    # footer row stops parsing
    assert len(out) == 2


def test_parse_graph_data_time_corp_style():
    data = make_xlsx([("Graph Data", GRAPH_DATA_CORP)])
    blocks = fb.parse_graph_data_time(fb.read_sheet(data, sheet=1))
    assert len(blocks) == 1
    b = blocks[0]
    assert not b["sec_style"]
    assert [x["t"] for x in b["fine"]] == ["08:00", "08:15", "After Hours"]
    assert b["fine"][0]["trades"] == pytest.approx(0.0095)
    assert b["fine"][0]["par"] == pytest.approx(0.0088)
    assert b["fine"][0]["avg_size"] == pytest.approx(395450.0)
    assert b["fine"][-1]["t"] == "After Hours"


def test_parse_graph_data_time_sec_style():
    data = make_xlsx([("Graph Data", GRAPH_DATA_SEC)])
    blocks = fb.parse_graph_data_time(fb.read_sheet(data, sheet=1))
    assert len(blocks) == 1
    b = blocks[0]
    assert b["sec_style"]
    assert b["sub"] == "abs auto"
    assert [x["t"] for x in b["fine"]] == ["08:00", "After Hours"]
    assert b["fine"][0]["opb"] == pytest.approx(0.00149)
    assert b["fine"][0]["rpb"] == pytest.approx(0.00143)
    assert "par" not in b["fine"][0]


def test_parse_graph_data_time_skips_garbage_header():
    # A TIME SEGMENTS row without the expected header is never misread
    data = make_xlsx([("Graph Data", [
        ["title"],
        ["TIME SEGMENTS", "something else"],
        ["08:15:00", 0.5],
    ])])
    assert fb.parse_graph_data_time(fb.read_sheet(data, sheet=1)) == []


def test_parse_annual_issue():
    data = make_xlsx([("Table C3", ANNUAL_TOP_C3)])
    t = fb.parse_annual_issue("corp", data)
    assert t["top"]["ig_trades"][0]["symbol"] == "ANTM5923070"
    assert t["top"]["ig_trades"][0]["trades"] == 62258
    assert len(t["top"]["ig_trades"]) == 2
    # other corp tables absent -> recorded as missing, never crash
    assert any(m == "corp:Table C4" for m in t["missing"])
    # securitized has no annual top lists at all
    t2 = fb.parse_annual_issue("sec", make_xlsx([("Graph Data", [["x"]])]))
    assert t2["top"] == {} and t2["missing"] == []


def test_parse_annual_transaction_corp():
    data = make_xlsx([
        ("Graph Data", GRAPH_DATA_CORP),
        ("Table C38", TIME_TABLE_TRADES),
        ("Table C39", TIME_TABLE_PAR),
    ])
    p = fb.parse_annual_transaction("corp", data)
    assert p["missing"] == []
    b = p["blocks"]["corp"]
    assert len(b["fine"]) == 3
    assert b["fine"][0]["t"] == "08:00"
    coarse = b["coarse"]["After Hours"]
    assert coarse["trades"]["2025"] == pytest.approx(0.01404)
    assert coarse["par"]["2025"] == pytest.approx(0.03352)
    assert coarse["trades"]["Q1 2025"] == pytest.approx(0.01522)


def test_parse_annual_transaction_sec_maps_subproducts():
    data = make_xlsx([
        ("Graph Data", GRAPH_DATA_SEC),
        ("Table S54", [
            ["Percentage of ABS Auto Loan S1 Trades Within Time Segments"],
            ["", 2024, 2025],
            ["After Hours", 0.002, 0.0016],
        ]),
        ("Table S59", [
            ["Percentage of ABS Auto Loan Original Principal Balance Traded Within Time Segments"],
            ["", 2024, 2025],
            ["After Hours", 0.001, 0.0008],
        ]),
    ])
    p = fb.parse_annual_transaction("sec", data)
    blk = p["blocks"]
    assert blk["abs"]["scope"] == "ABS Auto Loan"
    assert blk["abs"]["fine"][0]["t"] == "08:00"
    assert blk["abs"]["coarse"]["After Hours"]["trades"]["2025"] == pytest.approx(0.0016)
    assert blk["abs"]["coarse"]["After Hours"]["par"]["2025"] == pytest.approx(0.0008)
    # TBA: no fine grid in FINRA's file -> None, coarse still parsed if present
    assert blk["tba"]["fine"] is None
    assert any("tba:Table S58" in m for m in p["missing"])


# ---------- annual end-to-end ----------

def _annual_txn_corp() -> bytes:
    return make_xlsx([
        ("Graph Data", GRAPH_DATA_CORP),
        ("Table C38", TIME_TABLE_TRADES),
        ("Table C39", TIME_TABLE_PAR),
    ])


def _annual_issue_corp() -> bytes:
    return make_xlsx([("Table C3", ANNUAL_TOP_C3)])


def test_fetch_annual_end_to_end():
    async def fake_get_text(url, headers=None):
        assert "trace-fact-book" in url
        return ANNUAL_INDEX_HTML

    async def fake_get_bytes(url, headers=None):
        if "Transaction-Information-Corporate" in url:
            return _annual_txn_corp()
        if "Transaction-Information-Agency" in url:
            return make_xlsx([
                ("Graph Data", GRAPH_DATA_CORP),
                ("Table A19", TIME_TABLE_TRADES),
                ("Table A20", TIME_TABLE_PAR),
            ])
        if "Transaction-Information-Securitized" in url:
            return make_xlsx([("Graph Data", GRAPH_DATA_SEC)])
        if "Issue-Information-Corporate" in url:
            return _annual_issue_corp()
        if "Issue-Information-Agency" in url:
            return make_xlsx([("Table A2", [
                ["Top 50 Agency Issues"],
                ["Rank", "SYMBOL", "ISSUER NAME", "COUPON", "MATURITY", "",
                 "RATING", "TRADES", "", "DEALERS REPORTING"],
                [1, "FNMA.RY", "FEDERAL NATL MTG ASSN", 6.625,
                 "2030-11-15 00:00:00", "", "AA", 36165, "", 135],
            ])])
        raise AssertionError(url)

    store = FakeStore()
    src = asyncio.run(fb.fetch_finra_factbook_annual(
        store, fake_get_text, fake_get_bytes))
    assert src == "finra-factbook"
    p = store.docs["finra_factbook"]["payload"]
    assert p["annual_as_of"] == "2025"
    at = p["annual_top"]["2025"]
    assert at["ig_trades"][0]["symbol"] == "ANTM5923070"
    assert at["agency_trades"][0]["symbol"] == "FNMA.RY"
    iv = p["interval"]["2025"]
    assert iv["corp"]["fine"][0] == {
        "t": "08:00", "trades": pytest.approx(0.0095),
        "par": pytest.approx(0.0088), "avg_size": pytest.approx(395450.0)}
    assert iv["corp"]["coarse"]["After Hours"]["trades"]["2025"] == \
        pytest.approx(0.01404)
    assert iv["abs"]["scope"] == "ABS Auto Loan"
    assert iv["tba"]["fine"] is None
    assert "yearly refresh" in p["interval_note"]
    kinds = {(f["kind"], f["prod"]) for f in p["annual_files"]}
    assert ("transaction", "corp") in kinds
    assert ("issue", "agency") in kinds


def test_annual_and_quarterly_jobs_merge_without_clobbering():
    async def fake_get_text(url, headers=None):
        return ANNUAL_INDEX_HTML

    async def fake_get_bytes(url, headers=None):
        if "Transaction-Information" in url:
            return _annual_txn_corp()
        if "Issue-Information" in url:
            return _annual_issue_corp()
        raise AssertionError(url)

    store = FakeStore()
    # annual first, then quarterly
    asyncio.run(fb.fetch_finra_factbook_annual(
        store, fake_get_text, fake_get_bytes))

    async def fake_q_text(url, headers=None):
        return ('<a href="/sites/default/files/2026-08/'
                'Q22026-Corporate-Bond-Tables.xlsx">Corporate Bond Tables</a>')

    async def fake_q_bytes(url, headers=None):
        return _corp_workbook()

    asyncio.run(fb.fetch_finra_factbook(store, fake_q_text, fake_q_bytes))
    p = store.docs["finra_factbook"]["payload"]
    # quarterly keys present ...
    assert p["as_of"] == "Q2 2026"
    assert p["top"]["ig_trades"][0]["symbol"] == "AXP6059330"
    # ... and annual keys survived the quarterly refresh
    assert p["annual_top"]["2025"]["ig_trades"][0]["symbol"] == "ANTM5923070"
    assert p["interval"]["2025"]["corp"]["fine"][0]["t"] == "08:00"
    assert p["annual_as_of"] == "2025"


def test_fetch_annual_raises_when_no_links():
    async def fake_get_text(url, headers=None):
        return "<html><body>no links here</body></html>"

    async def fake_get_bytes(url, headers=None):  # pragma: no cover
        raise AssertionError("should not be called")

    with pytest.raises(ValueError, match="no annual fact-book links"):
        asyncio.run(fb.fetch_finra_factbook_annual(
            FakeStore(), fake_get_text, fake_get_bytes))


# ---------- annual full-table fixtures ----------

ANNUAL_C20 = [
    ["Corporate P1 Trades (excluding equity CUSIPs)"],
    ["(Average Daily)", "2024", "2025"],
    ["Total", 10785.14, 12517.59],
    ["144A", 369.67, 409.31],
    ["Publicly Traded", 10415.48, 12108.27],
    [">= 25,000,000", 89.54, 100.67],
    [">= 10,000,000 < 25,000,000", 159.15, 171.23],
    [">= 5,000,000 < 10,000,000", 178.13, 192.90],
    [">= 1,000,000 < 5,000,000", 574.14, 610.62],
    [">= 100,000 < 1,000,000", 1618.08, 1925.21],
    ["< 100,000", 8166.10, 9516.95],
    ["   EXCLUDING CONVERTIBLES"],
]

ANNUAL_C30 = [
    ["Ratio of Corporate S1 Investment-Grade Customer Buy to Customer Sell Trades"],
    ["", "2024", "", "", "2025", "", ""],
    ["", "Gross", "Net", "Ratio", "Gross", "Net", "Ratio"],
    [">= 25,000,000", 15084, -1596, 0.8086, 17225, -1631, 0.8270],
    [">= 10,000,000 < 25,000,000", 109345, -7505, 0.8715, 118679, -8000, 0.8600],
    [">= 5,000,000 < 10,000,000", 189167, -8593, 0.9131, 191513, -9000, 0.9100],
    [">= 1,000,000 < 5,000,000", 770854, 17014, 1.0451, 779343, 18000, 1.0400],
    [">= 100,000 < 1,000,000", 2750831, 474807, 1.4172, 2853581, 480000, 1.4100],
    ["< 100,000", 10901949, 3208433, 1.8341, 11348992, 3300000, 1.8200],
    ["<1 Yr Maturity Band", 769474, 53676, 1.15, 706700, 50000, 1.10],
    ["   AAA", 7993, 2635, 1.98, 6444, 2000, 1.90],
]

GRAPH_DATA_HIST = [
    ["Corporate Transaction Information Graph Data"],
    ["", "Q1 2021", "Q2 2021"],
    ["OVERALL S1 (excl. Convertibles)"],
    ["Trades"],
    ["     Customer Buy", 100.0, 110.0],
    ["     Customer Sell", 90.0, 95.0],
    ["     Interdealer", 80.0, 85.0],
    ["INVESTMENT GRADE"],
    ["Trades"],
    ["     Customer Buy", 60.0, 66.0],
    ["     Customer Sell", 50.0, 55.0],
    ["     Interdealer", 40.0, 44.0],
    ["Par Value"],
    ["     Customer Buy", 600.0, 660.0],
    ["     Customer Sell", 500.0, 550.0],
    ["     Interdealer", 400.0, 440.0],
    ["SOMETHING ELSE"],
    ["Trades"],
    ["     Customer Buy", 1.0, 1.0],
]

ANNUAL_C1 = [
    ["Corporate Issues (excluding convertible bonds and equity CUSIPs)"],
    ["", "2024", "2025", "", "Q4 2025"],
    ["Total", 233676, 339631, "", 339631],
    ["Total"],
    ["    Publicly Traded", 213259, 314281, "", 314281],
    ["    Investment Grade", 33323, 39797, "", 39797],
    ["        AAA", 616, 844, "", 844],
    ["        BBB", 13224, 13817, "", 13817],
    ["Note: As of the last day of the period"],
]

ISSUE_MIX_CORP = [
    ["Corporate Issue Information Graph Data"],
    ["", "ISSUES", "S1 TRADES", "S1 VOLUMES"],
    ["        AAA", 845, 1017.98, 403186441.56],
    ["        BBB", 13894, 60148.08, 21471683608.0],
    ["© 2006-26 Financial Industry Regulatory Authority, Inc."],
]

ANNUAL_C9 = [
    ["Percentage of Corporate S1 Activity Captured by the Most Active Firms"],
    ["", "2024", "2025"],
    ["TRACE Reporting Firms", 1595, 1539],
    ["Unique Firms Reporting", 866, 851],
    ["Average Reporting Firms per Day", 411.17, 398.98],
    ["% of S1 Trade Activity Captured by:"],
    ["      MOST ACTIVE 5 Firms", 0.2715, 0.2690],
    ["      MOST ACTIVE 10 Firms", 0.4407, 0.4531],
    ["      MOST ACTIVE 25 Firms", 0.7101, 0.7038],
    ["      MOST ACTIVE 50 Firms", 0.8705, 0.8695],
    ["% of S1 Par Value Activity Captured by:"],
    ["      MOST ACTIVE 5 Firms", 0.3382, 0.3375],
    ["      MOST ACTIVE 10 Firms", 0.5373, 0.5444],
    ["      MOST ACTIVE 25 Firms", 0.7100, 0.7000],
    ["      MOST ACTIVE 50 Firms", 0.8700, 0.8600],
    ["© 2006-26 FINRA"],
]

ANNUAL_INDEX_HTML_P = """
<html><body>
<a href="/sites/default/files/2026-02/2025-Transaction-Information-Corporate.xlsx">t</a>
<a href="/sites/default/files/2026-02/2025-Issue-Information-Corporate.xlsx">i</a>
<a href="/sites/default/files/2026-02/2025-Participant-Information-Corporate.xlsx">p</a>
<a href="/sites/default/files/2026-08/Q22026-Corporate-Bond-Tables.xlsx">q</a>
</body></html>
"""


def _annual_full_txn() -> bytes:
    return make_xlsx([
        ("Graph Data", GRAPH_DATA_HIST),
        ("Table C21", [
            ["Corporate S1 Investment-Grade Trades (excluding convertibles)"],
            ["(Average Daily)", "2024", "2025"],
            ["Total", 90000.0, 103987.30],
            [">= 25,000,000", 80.0, 100.67],
            [">= 10,000,000 < 25,000,000", 150.0, 171.23],
            [">= 5,000,000 < 10,000,000", 170.0, 192.90],
            [">= 1,000,000 < 5,000,000", 570.0, 610.62],
            [">= 100,000 < 1,000,000", 1600.0, 1925.21],
            ["< 100,000", 8100.0, 9516.95],
        ]),
        ("Table C26", [
            ["Corporate S1 Investment-Grade Par Value Traded"],
            ["(Average Daily)", "2024", "2025"],
            ["Total", 38000000000.0, 39246153319.03],
            [">= 25,000,000", 4000000000.0, 4951362010.87],
            [">= 10,000,000 < 25,000,000", 11000000000.0, 11232347277.92],
            [">= 5,000,000 < 10,000,000", 8700000000.0, 8791846626.79],
            [">= 1,000,000 < 5,000,000", 6700000000.0, 6717177483.55],
            [">= 100,000 < 1,000,000", 2300000000.0, 2376011290.11],
            ["< 100,000", 790000000.0, 792084677.41],
        ]),
        ("Table C30", ANNUAL_C30),
    ])


# ---------- annual full-table unit tests ----------

def test_parse_annual_table_picks_year_column():
    data = make_xlsx([("Table C20", ANNUAL_C20)])
    rows = fb.read_sheet(data, sheet=1)
    t25 = fb.parse_annual_table(rows, 2025)
    assert t25["total"] == pytest.approx(12517.59)
    assert len(t25["buckets"]) == 6
    assert dict(t25["buckets"])["ge25m"] == pytest.approx(100.67)
    assert dict(t25["buckets"])["lt100k"] == pytest.approx(9516.95)
    t24 = fb.parse_annual_table(rows, 2024)
    assert t24["total"] == pytest.approx(10785.14)
    # sub-rows (144A/Publicly Traded) and footer never absorbed
    assert all(k in dict(fb.BUCKETS).values() for k in dict(t25["buckets"]))


def test_parse_annual_table_missing_year_returns_none():
    data = make_xlsx([("Table C20", ANNUAL_C20)])
    assert fb.parse_annual_table(fb.read_sheet(data, sheet=1), 2022) is None


def test_parse_annual_buysell_merged_year_header():
    data = make_xlsx([("Table C30", ANNUAL_C30)])
    out = fb.parse_annual_buysell(fb.read_sheet(data, sheet=1), 2025)
    assert len(out) == 6  # stops at the maturity-band drill-down
    assert out[0][0] == "ge25m"
    assert out[0][1] == pytest.approx(17225.0)
    assert out[0][2] == pytest.approx(-1631.0)
    assert out[0][3] == pytest.approx(0.8270)
    assert out[-1][0] == "lt100k"
    assert fb.parse_annual_buysell(fb.read_sheet(data, sheet=1), 2022) is None


def test_parse_graph_data_history_sums_detail_rows():
    data = make_xlsx([("Graph Data", GRAPH_DATA_HIST)])
    h = fb.parse_graph_data_history("corp", data)
    # OVERALL S1 skipped (fb-corp-* is P1); SOMETHING ELSE unknown -> skipped
    assert "fb-corp-trades" not in h
    assert sorted(h) == ["fb-hy-pv", "fb-hy-trades", "fb-ig-pv", "fb-ig-trades"] \
        or sorted(h) == ["fb-ig-pv", "fb-ig-trades"]
    tr = dict(h["fb-ig-trades"])
    assert tr[date(2021, 3, 31)] == pytest.approx(150.0)
    assert tr[date(2021, 6, 30)] == pytest.approx(165.0)
    pv = dict(h["fb-ig-pv"])
    assert pv[date(2021, 3, 31)] == pytest.approx(1500.0)


def test_parse_graph_data_history_sec_blocks():
    data = make_xlsx([("Graph Data", [
        ["Securitized Product Transaction Information Graph Data"],
        ["", "Q1 2020", "Q2 2020"],
        ["ABS S1 Trades"],
        ["     Customer Buy", 174.0, 171.0],
        ["     Customer Sell", 214.0, 183.0],
        ["     Interdealer", 38.0, 39.0],
        ["ABS S1 Original Principal Balance"],
        ["     Customer Buy", 1002540670.0, 701603927.0],
        ["     Customer Sell", 956606057.0, 637796548.0],
        ["     Interdealer", 79120818.0, 50706275.0],
        ["TBA Trades"],
        ["     Customer Buy", 10.0, 11.0],
    ])])
    h = fb.parse_graph_data_history("sec", data)
    assert dict(h["fb-abs-trades"])[date(2020, 3, 31)] == pytest.approx(426.0)
    assert dict(h["fb-abs-pv"])[date(2020, 3, 31)] == pytest.approx(2038267545.0)
    assert dict(h["fb-tba-trades"])[date(2020, 6, 30)] == pytest.approx(11.0)
    assert "fb-absx-trades" not in h


def test_parse_graph_data_history_no_graph_data_sheet():
    data = make_xlsx([("Other", [["x"]])])
    assert fb.parse_graph_data_history("corp", data) == {}


def test_parse_annual_issue_table():
    data = make_xlsx([("Table C1", ANNUAL_C1)])
    t = fb.parse_annual_issue_table(fb.read_sheet(data, sheet=1))
    assert t["total"]["2025"] == pytest.approx(339631.0)
    assert t["total"]["Q4 2025"] == pytest.approx(339631.0)
    assert t["breakdown"]["AAA"]["2025"] == pytest.approx(844.0)
    assert t["breakdown"]["Publicly Traded"]["2024"] == pytest.approx(213259.0)
    # valueless section header + Note footer never absorbed
    assert "Total" not in t["breakdown"]
    assert t["periods"] == ["2024", "2025", "Q4 2025"]


def test_parse_annual_issue_mix():
    data = make_xlsx([("Graph Data", ISSUE_MIX_CORP)])
    m = fb.parse_annual_issue_mix("corp", data)
    aaa = m["corp"]["AAA"]
    assert aaa["issues"] == pytest.approx(845.0)
    assert aaa["trades"] == pytest.approx(1017.98)
    assert aaa["par"] == pytest.approx(403186441.56)
    assert m["corp"]["BBB"]["issues"] == pytest.approx(13894.0)


def test_parse_annual_participant():
    data = make_xlsx([("Table C9", ANNUAL_C9)])
    t = fb.parse_annual_participant(fb.read_sheet(data, sheet=1))
    assert set(t["trades"]) == {"5", "10", "25", "50"}
    assert t["trades"]["5"]["2025"] == pytest.approx(0.2690)
    assert t["trades"]["50"]["2024"] == pytest.approx(0.8705)
    assert t["par"]["5"]["2025"] == pytest.approx(0.3375)
    assert t["firms_reporting"]["2025"] == pytest.approx(1539.0)
    assert t["unique_firms"]["2025"] == pytest.approx(851.0)
    assert t["avg_firms_per_day"]["2025"] == pytest.approx(398.98)


def test_parse_annual_participant_full_tiers_sorted():
    data = make_xlsx([("Table C9", ANNUAL_C9)])
    q = fb.parse_annual_participant_full("corp", data)
    cp = q["participant"]["corp"]
    assert cp["tiers"] == ["5", "10", "25", "50"]
    assert cp["segment"] == "All eligible TRACE reporting firms"
    assert cp["trades_pct"]["10"]["2025"] == pytest.approx(0.4531)
    assert q["missing"] == []


def test_parse_annual_participant_garbage_returns_none():
    data = make_xlsx([("Table C9", [
        ["Percentage of Something"],
        ["", "2025"],
        ["TRACE Reporting Firms", 100],
        ["© footer"],
    ])])
    assert fb.parse_annual_participant(fb.read_sheet(data, sheet=1)) is None
    q = fb.parse_annual_participant_full("corp", data)
    assert q["participant"] == {}
    assert any("no tier rows" in m for m in q["missing"])


def test_resolve_annual_urls_includes_participant():
    urls = fb.resolve_annual_urls(ANNUAL_INDEX_HTML_P)
    assert set(urls) == {"transaction", "issue", "participant"}
    assert urls["participant"]["corp"][0][1] == 2025
    # quarterly link never matches
    assert all("Q2" not in u for u, _ in urls["participant"]["corp"])


def test_annual_job_full_tables_and_history_merge():
    async def fake_get_text(url, headers=None):
        return ANNUAL_INDEX_HTML_P

    async def fake_get_bytes(url, headers=None):
        if "Transaction-Information" in url:
            return _annual_full_txn()
        if "Issue-Information" in url:
            return make_xlsx([("Table C1", ANNUAL_C1),
                              ("Graph Data", ISSUE_MIX_CORP)])
        if "Participant-Information" in url:
            return make_xlsx([("Table C9", ANNUAL_C9)])
        raise AssertionError(url)

    store = FakeStore()
    # pre-seed one quarterly point: the annual merge must not clobber it
    store.upsert_points("cycle:fb-ig-trades", [(date(2021, 3, 31), 999.0)])
    asyncio.run(fb.fetch_finra_factbook_annual(
        store, fake_get_text, fake_get_bytes))
    p = store.docs["finra_factbook"]["payload"]

    adv = p["annual_adv_adt"]["2025"]["ig"]
    assert adv["trades"]["total"] == pytest.approx(103987.30)
    assert adv["trades"]["buckets"]["ge25m"] == pytest.approx(100.67)
    assert adv["par"]["total"] == pytest.approx(39246153319.03)
    assert adv["buy_sell"]["trades"]["ge25m"]["ratio"] == pytest.approx(0.8270)

    iss = p["issue"]["2025"]["corp"]
    assert iss["total"]["2025"] == pytest.approx(339631.0)
    assert iss["breakdown"]["AAA"]["2025"] == pytest.approx(844.0)
    assert p["issue_mix"]["2025"]["corp"]["AAA"]["issues"] == pytest.approx(845.0)

    part = p["participant"]["2025"]["corp"]
    assert part["tiers"] == ["5", "10", "25", "50"]
    assert part["trades_pct"]["5"]["2025"] == pytest.approx(0.2690)
    assert part["par_pct"]["5"]["2025"] == pytest.approx(0.3375)

    # history merge: existing Q1 2021 point untouched, Q2 2021 added
    pts = store.points("cycle:fb-ig-trades")
    assert pts[date(2021, 3, 31)] == 999.0
    assert pts[date(2021, 6, 30)] == pytest.approx(165.0)

    assert "yearly refresh" in p["participant_note"]
    assert "no par" in p["issue_note"]
    kinds = {(f["kind"], f["prod"]) for f in p["annual_files"]}
    assert ("participant", "corp") in kinds
