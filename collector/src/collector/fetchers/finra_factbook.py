"""FINRA TRACE Fact Book — quarterly OTC bond-market tables (keyless XLSX).

Index (verified live 2026-10-05):
  https://www.finra.org/filing-reporting/trace/trace-fact-book
Quarterly workbooks go back to 2016; each quarter publishes 3 files:
  Corporate Bond Tables / Agency Debt Tables / Securitized Product Tables.
Workbook URLs are resolved from the index page (no hardcoded URL pattern —
FINRA has renamed the slugs before).

Workbook layout (verified on the Q2 2026 files):
  Corporate (21 sheets):
    'Top 50 IG' / 'Top 50 IG PV' / 'Top 50 HY' / 'Top 50 HY PV' /
    'Top 25 Conv' / 'Top 25 Conv PV': r1 title, r2 headers
      (Rank | SYMBOL | ISSUER NAME | COUPON | MATURITY | | RATING |
       TRADES/PAR VALUE | | DEALERS REPORTING), r3+ data rows.
    'P1 Trades' / 'P1 PV': "(Average Daily)" | "QN YYYY" at r2; rows hold
      "Total", sub-rows (144A, Publicly Traded, ...) and 6 trade-size buckets.
    'IG Trades' / 'IG PV' / 'HY Trades' / 'HY PV' / 'Conv IG Trades' /
    'Conv IG PV' / 'Conv HY Trades' / 'Conv HY PV': S1 (all-trades) versions
    of the same block layout.
    'IG Buy-Sell Ratio' / 'HY Buy-Sell Ratio' / 'Conv IG Buy-Sell Ratio' /
    'Conv HY Buy-Sell Ratio': r2 quarter label, r3 Gross|Net|Ratio; the
      top-level 6 size-bucket rows are the headline customer buy/sell
      split, followed by a maturity-band -> rating -> bucket drill-down.
  Agency (8 sheets): 'Top 50 Trades' / 'Top 50 Par Value',
    'P1 Trades' / 'S1 Trades' / 'P1 Par Value' / 'S1 Par Value',
    'Buy-Sell Ratio S1 Par Value' (same block conventions).
  Securitized (26 sheets): 'ABS P1 Trades' / 'ABS S1 Trades' / 'ABSX Trades' /
    'CMO Trades' / 'MBS Trades' / 'TBA Trades' (trades), OPB/RPB/PB par
    sheets, and '... Buy-Sell Ratio ...' sheets (same block conventions).

P1 = principal (dealer-customer) trades; S1 = all trades incl. interdealer.
OPB = original principal balance; RPB = remaining principal balance.
Buy-sell ratio = customer buy $ / customer sell $; net = buy - sell.
Each sheet carries ONE quarter ("QN YYYY" at r2); quarterly history
accumulates as new workbooks land (idempotent upserts at quarter-end).

Stored (quarterly, at quarter-end):
  cycle:fb-<prod>-trades / -pv                 avg-daily trades / $ par, headline total
  cycle:fb-<prod>-trades-b-<bucket> / -pv-b-<bucket>   by trade-size bucket
  cycle:fb-<prod>-bsg-b-<bucket> / -bsn-b-<bucket> / -bsr-b-<bucket>
      buy-sell gross $ / net $ / ratio, by bucket
plus a "finra_factbook" snapshot doc: top-50 lists (latest quarter only),
headline totals, latest size-bucket and buy-sell splits, and the quarters
parsed (with any missing/failed sheets recorded, so layout drift degrades
to a thinner doc, never a crash or a misread number).

Annual workbooks (yearly job, fetch_finra_factbook_annual):
  Transaction Information (Corporate / Agency / Securitized Products):
    'Graph Data' sheet -> TIME SEGMENTS blocks: 15-minute execution grids
      (42 buckets 08:00->18:30 + After Hours). Corporate/Agency blocks
      carry % trades, % par value, avg trade size; the labels ("08:15:00")
      are bucket END times (verified: 15-min buckets sum exactly to the
      coarse Table C38/C39 buckets). Securitized blocks are per sub-product
      (ABS Auto Loan, ABSX CDO, Agency CMO, MBS — the largest sub-segment
      of each product; TBA has no fine grid) with range labels
      ("08:00 AM - 08:14 AM") and % trades / % OPB / % RPB.
    Tables C38/C39 (corp), A19/A20 (agency), S54-S63 (sec): % of trades and
      par executed within 6 coarse time buckets (2-hour blocks + After
      Hours), with annual history (2023+) and quarterly detail.
    Tables C20-C29 (corp) / A13-A16 (agency) / S20-S35 (sec): full-year
      average-daily trades and par value by trade-size bucket (same bucket
      layout as the quarterly sheets) -> annual_adv_adt.
    Tables C30-C37 / A17-A18 / S36-S53: customer buy-sell gross/net/ratio
      by bucket -> annual_adv_adt buy_sell flavors.
    Graph Data quarterly columns (2021+, sec 2020+) are merged into the
      existing cycle:fb-*-trades/-pv series — dates the quarterly job has
      not set only, never clobbering. (corp OVERALL S1 skipped: fb-corp-*
      is P1; CONVERTIBLES skipped: no combined series.)
  Issue Information (Corporate / Agency / Securitized Products):
    annual Top 50 / Top 25 most-traded lists (Table C3-C8, A2-A3) — same
    column layout as the quarterly top-50 sheets, parsed by parse_top50.
    Securitized has no annual top lists.
    Tables C1-C2 / A1 / S1-S4: issues outstanding (counts, no par) by
    rating / issuer / type -> issue. Graph Data: rating/issuer/collateral
    mix snapshot (issues, avg-daily trades, par) -> issue_mix.
  Participant Information (Corporate / Agency / Securitized Products):
    Tables C9 / A4 / S5,S12,S15,S18,S19: % of S1 trades and par captured
    by the most-active 5/10/25/50 firms + firm counts -> participant.
    Primary all-eligible-firms table per product; segment splits exist in
    the source but are not parsed.
  Stored in the same "finra_factbook" doc as annual_top / interval /
  annual_adv_adt / issue / issue_mix / participant / annual_as_of
  (merged, never clobbering the quarterly keys — and the quarterly job
  carries those keys forward in turn).
  HONEST CAVEAT: all of the above comes from the ANNUAL workbooks only,
  so it refreshes yearly, not quarterly. The doc and panel both say so.

On an empty store, backfills the last 12 quarters (3Y, matching Harry's
historical-grid requirement), then each run resolves the latest quarter
from the index page.
"""
from __future__ import annotations

import asyncio
import calendar
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta

def quarter_end(year: int, q: int) -> date:
      month = q * 3
      return date(year, month, calendar.monthrange(year, month)[1])
from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes, GetText
from collector.store import Store

log = logging.getLogger(__name__)

INDEX_URL = "https://www.finra.org/filing-reporting/trace/trace-fact-book"
SOURCE = "finra-factbook"
CONTACT_UA = "os-bloom/1.0 contact harrysugamakc@gmail.com"
REQUEST_GAP = 2.0  # polite: <=1 req/2s, same posture as the SEC fetchers
BACKFILL_QUARTERS = 12

# href="...Q22026-Corporate-Bond-Tables.xlsx" (2-digit years seen pre-2020)
XLSX_RE = re.compile(r'href="([^"]*?Q([1-4])(\d{2,4})[^"]*?\.xlsx)"', re.IGNORECASE)
QUARTER_RE = re.compile(r"^Q([1-4])\s+(\d{4})$", re.IGNORECASE)

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

# trade-size bucket label -> series suffix
BUCKETS = [
    (">= 25,000,000", "ge25m"),
    (">= 10,000,000 < 25,000,000", "b10_25m"),
    (">= 5,000,000 < 10,000,000", "b5_10m"),
    (">= 1,000,000 < 5,000,000", "b1_5m"),
    (">= 100,000 < 1,000,000", "b100k_1m"),
    ("< 100,000", "lt100k"),
]
_BUCKET_KEY = {label: key for label, key in BUCKETS}

# product -> sheet names per workbook kind
PRODUCTS = {
    "corp":   {"trades": "P1 Trades", "pv": "P1 PV", "bs": None,
               "top": []},
    "ig":     {"trades": "IG Trades", "pv": "IG PV", "bs": "IG Buy-Sell Ratio",
               "top": [("Top 50 IG", "ig_trades", "trades"),
                       ("Top 50 IG PV", "ig_pv", "pv")]},
    "hy":     {"trades": "HY Trades", "pv": "HY PV", "bs": "HY Buy-Sell Ratio",
               "top": [("Top 50 HY", "hy_trades", "trades"),
                       ("Top 50 HY PV", "hy_pv", "pv")]},
    "convig": {"trades": "Conv IG Trades", "pv": "Conv IG PV",
               "bs": "Conv IG Buy-Sell Ratio",
               "top": [("Top 25 Conv", "conv_trades", "trades"),
                       ("Top 25 Conv PV", "conv_pv", "pv")]},
    "convhy": {"trades": "Conv HY Trades", "pv": "Conv HY PV",
               "bs": "Conv HY Buy-Sell Ratio", "top": []},
    "agency": {"trades": "S1 Trades", "pv": "S1 Par Value",
               "bs": "Buy-Sell Ratio S1 Par Value",
               "top": [("Top 50 Trades", "agency_trades", "trades"),
                       ("Top 50 Par Value", "agency_pv", "pv")]},
    "abs":    {"trades": "ABS S1 Trades", "pv": "ABS S1 OPB",
               "bs": "ABS Buy-Sell Ratio OPB", "top": []},
    "absx":   {"trades": "ABSX Trades", "pv": "ABSX OPB",
               "bs": "ABSX Buy-Sell Ratio OPB", "top": []},
    "cmo":    {"trades": "CMO Trades", "pv": "CMO OPB",
               "bs": "CMO Buy-Sell Ratio OPB", "top": []},
    "mbs":    {"trades": "MBS Trades", "pv": "MBS OPB",
               "bs": "MBS Buy-Sell Ratio OPB", "top": []},
    "tba":    {"trades": "TBA Trades", "pv": "TBA PB",
               "bs": "TBA Buy-Sell Ratio", "top": []},
}
KINDS = {
    "corp": ["corp", "ig", "hy", "convig", "convhy"],
    "agency": ["agency"],
    "sec": ["abs", "absx", "cmo", "mbs", "tba"],
}


def parse_quarter_label(label: str) -> tuple[int, int] | None:
    """'Q2 2026' -> (2026, 2); None when unparseable."""
    m = QUARTER_RE.match((label or "").strip())
    return (int(m.group(2)), int(m.group(1))) if m else None


def _classify(href: str) -> str | None:
    h = href.lower()
    if "corporate" in h:
        return "corp"
    if "agency" in h:
        return "agency"
    if "securitized" in h:
        return "sec"
    return None


def resolve_workbook_urls(index_html: str) -> dict[str, list[tuple[str, int, int]]]:
    """All quarterly workbook links from the index page, newest first.

    Returns {kind: [(url, year, quarter), ...]} for kinds corp/agency/sec.
    Annual-table links carry no QnYYYY stamp and are ignored.
    """
    base = "https://www.finra.org"
    found: dict[str, dict[tuple[int, int], str]] = {}
    for href, q, yr in XLSX_RE.findall(index_html or ""):
        kind = _classify(href)
        if not kind:
            continue
        year = int(yr)
        if year < 100:
            year += 2000
        url = href if href.startswith("http") else base + href
        found.setdefault(kind, {})[(year, int(q))] = url
    out: dict[str, list[tuple[str, int, int]]] = {}
    for kind, by_q in found.items():
        out[kind] = [(url, y, q) for (y, q), url in
                     sorted(by_q.items(), reverse=True)]
    return out


def _sheet_map(data: bytes) -> dict[str, int]:
    """Lower-cased sheet name -> 1-based index for read_sheet."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("xl/workbook.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"xlsx workbook.xml unreadable: {exc}") from exc
    return {s.get("name", "").lower(): i + 1
            for i, s in enumerate(root.findall(".//m:sheet", _NS))}


def _match_sheet(sheets: dict[str, int], target: str) -> int | None:
    """Exact name first, then all-words- contained fallback (layout drift)."""
    tl = target.lower()
    if tl in sheets:
        return sheets[tl]
    words = tl.split()
    for name, idx in sheets.items():
        if all(w in name for w in words):
            return idx
    return None


def _quarter_of(rows: dict[int, list[str]]) -> str | None:
    """Quarter label lives at row 2 ('(Average Daily)' | 'Q2 2026')."""
    for c in rows.get(2, [])[1:]:
        if c.strip():
            return c.strip()
    return None


def _bucket_key(label: str) -> str | None:
    return _BUCKET_KEY.get((label or "").strip())


def _clean_date(raw: str) -> str | None:
    """Maturity cell -> 'YYYY-MM-DD'.

    The stdlib xlsx reader returns raw cell values: date cells arrive as
    Excel serials (days since 1899-12-30), not formatted strings.
    """
    s = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
        return s[:10]
    try:
        serial = float(s)
    except (ValueError, TypeError):
        return None
    if 20000 <= serial <= 80000:  # ~1954..2119; guards against coupons etc.
        return (date(1899, 12, 30) + timedelta(days=int(serial))).isoformat()
    return None


def parse_top50(rows: dict[int, list[str]], value_kind: str) -> list[dict]:
    """Top-50/25 sheet rows -> bond dicts. value_kind: 'trades' | 'pv'.

    Columns: A rank, B symbol, C issuer, D coupon, E maturity, G rating,
    H trades/par value, J dealers reporting. Stops at 60 rows; rows
    without a numeric rank (footnotes) are skipped.
    """
    out: list[dict] = []
    vkey = "pv" if value_kind == "pv" else "trades"
    for r in sorted(rows):
        if r < 3:
            continue
        if len(out) >= 60:
            break
        row = rows[r]
        rank = to_float(row[0]) if row else None
        if rank is None:
            continue
        symbol = row[1].strip() if len(row) > 1 else ""
        if not symbol:
            continue
        dealers = to_float(row[9]) if len(row) > 9 else None
        out.append({
            "rank": int(rank),
            "symbol": symbol,
            "issuer": row[2].strip() if len(row) > 2 else "",
            "coupon": to_float(row[3]) if len(row) > 3 else None,
            "maturity": _clean_date(row[4]) if len(row) > 4 else None,
            "rating": row[6].strip() if len(row) > 6 else "",
            vkey: to_float(row[7]) if len(row) > 7 else None,
            "dealers": int(dealers) if dealers is not None else None,
        })
    return out


def parse_block(rows: dict[int, list[str]]) -> dict:
    """Headline block of a P1/S1 Trades or PV sheet.

    Takes the first "Total" row and the 6 trade-size bucket rows that
    follow it (sub-rows like 144A / Publicly Traded are skipped; a second
    "Total" ends the block). Returns {"quarter", "total", "buckets"}.
    """
    quarter = _quarter_of(rows)
    total: float | None = None
    buckets: list[tuple[str, float]] = []
    seen_total = False
    for r in sorted(rows):
        if r < 3:
            continue
        row = rows[r]
        label = row[0].strip() if row else ""
        if not label:
            continue
        val = to_float(row[1]) if len(row) > 1 else None
        if label.lower().startswith("total"):
            if seen_total:
                break
            seen_total = True
            total = val
            continue
        if not seen_total:
            continue
        bkey = _bucket_key(label)
        if bkey is not None and val is not None:
            buckets.append((bkey, val))
            if len(buckets) == 6:
                break
        # anything else (sub-rows, section headers): skip
    return {"quarter": quarter, "total": total, "buckets": buckets}


def parse_buysell(rows: dict[int, list[str]]) -> dict:
    """Headline block of a Buy-Sell Ratio sheet: top-level size buckets.

    Row 3 holds Gross | Net | Ratio. Leading non-bucket rows (e.g. TBA's
    "Good Delivery" parent) are skipped; parsing stops at the first
    non-bucket row after the bucket run (the maturity-band drill-down).
    Returns {"quarter", "rows"} with (bucket_key, gross, net, ratio) tuples.
    """
    quarter = _quarter_of(rows)
    out: list[tuple[str, float | None, float | None, float | None]] = []
    for r in sorted(rows):
        if r < 4:
            continue
        row = rows[r]
        bkey = _bucket_key(row[0]) if row else None
        if bkey is None:
            if out:
                break
            continue
        gross = to_float(row[1]) if len(row) > 1 else None
        net = to_float(row[2]) if len(row) > 2 else None
        ratio = to_float(row[3]) if len(row) > 3 else None
        out.append((bkey, gross, net, ratio))
    return {"quarter": quarter, "rows": out}


def parse_workbook(kind: str, data: bytes) -> dict:
    """Parse one quarterly workbook -> quarterly series + top lists.

    Returns {"quarter", "qdate", "top", "series", "headlines", "missing"}.
    "series" keys are suffixes (the caller prefixes "cycle:"); "missing"
    lists sheet names that were not found (layout drift signal).
    """
    sheets = _sheet_map(data)
    out: dict = {"quarter": None, "qdate": None, "top": {}, "series": {},
                 "headlines": {}, "missing": []}
    for prod in KINDS[kind]:
        spec = PRODUCTS[prod]
        head: dict = {}
        for role in ("trades", "pv"):
            si = _match_sheet(sheets, spec[role])
            if si is None:
                out["missing"].append(f"{prod}:{spec[role]}")
                continue
            b = parse_block(read_sheet(data, sheet=si))
            out["quarter"] = out["quarter"] or b["quarter"]
            if b["total"] is not None:
                out["series"][f"fb-{prod}-{role}"] = b["total"]
                head[role] = b["total"]
            for bkey, v in b["buckets"]:
                out["series"][f"fb-{prod}-{role}-b-{bkey}"] = v
        if spec["bs"]:
            si = _match_sheet(sheets, spec["bs"])
            if si is None:
                out["missing"].append(f"{prod}:{spec['bs']}")
            else:
                bs = parse_buysell(read_sheet(data, sheet=si))
                out["quarter"] = out["quarter"] or bs["quarter"]
                for bkey, gross, net, ratio in bs["rows"]:
                    if gross is not None:
                        out["series"][f"fb-{prod}-bsg-b-{bkey}"] = gross
                    if net is not None:
                        out["series"][f"fb-{prod}-bsn-b-{bkey}"] = net
                    if ratio is not None:
                        out["series"][f"fb-{prod}-bsr-b-{bkey}"] = ratio
        for sheet_name, key, vkind in spec["top"]:
            si = _match_sheet(sheets, sheet_name)
            if si is None:
                out["missing"].append(f"{prod}:{sheet_name}")
                continue
            out["top"][key] = parse_top50(read_sheet(data, sheet=si), vkind)
        if head:
            out["headlines"][prod] = head
    if out["quarter"]:
        yq = parse_quarter_label(out["quarter"])
        if yq:
            out["qdate"] = quarter_end(*yq)
    return out


# ---- annual workbooks: top-N lists + time-of-day execution stats ----

# href="...2025-Transaction-Information-Corporate.xlsx"
ANNUAL_RE = re.compile(
    r'href="([^"]*?(\d{4})-(Transaction|Issue|Participant)-Information-'
    r"(Corporate|Agency|Securitized-Products)\.xlsx)\"",
    re.IGNORECASE,
)
_ANNUAL_PROD = {"corporate": "corp", "agency": "agency",
                "securitized-products": "sec"}

# Annual top-N tables live in the Issue Information workbooks. Same column
# layout as the quarterly top-50 sheets -> parse_top50 is reused.
# (Securitized has no annual top lists.)
ANNUAL_TOP = {
    "corp": [("Table C3", "ig_trades", "trades"),
             ("Table C4", "ig_pv", "pv"),
             ("Table C5", "hy_trades", "trades"),
             ("Table C6", "hy_pv", "pv"),
             ("Table C7", "conv_trades", "trades"),
             ("Table C8", "conv_pv", "pv")],
    "agency": [("Table A2", "agency_trades", "trades"),
               ("Table A3", "agency_pv", "pv")],
    "sec": [],
}

# Coarse time-segment tables in the annual Transaction Information
# workbooks: % of trades / % of par within 6 buckets
# ("8:00 AM - 9:59 AM" ... "After Hours"), annual + quarterly columns.
INTERVAL_TABLES = {
    "corp":   {"trades": "Table C38", "par": "Table C39"},
    "agency": {"trades": "Table A19", "par": "Table A20"},
    "abs":    {"trades": "Table S54", "par": "Table S59"},
    "absx":   {"trades": "Table S55", "par": "Table S60"},
    "cmo":    {"trades": "Table S56", "par": "Table S61"},
    "mbs":    {"trades": "Table S57", "par": "Table S62"},
    "tba":    {"trades": "Table S58", "par": "Table S63"},
}
# Securitized fine-grid sub-product -> quarterly product key. The interval
# data covers the largest sub-segment only (documented in the panel).
_SEC_FINE_SUB = {"abs auto": "abs", "absx cdo": "absx",
                 "agency cmo": "cmo", "mbs": "mbs"}
_SEC_SCOPE = {"abs": "ABS Auto Loan", "absx": "ABSX CDO",
              "cmo": "Agency CMO", "mbs": "MBS"}

_FINE_END_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_FINE_RANGE_RE = re.compile(
    r"^(\d{1,2}):(\d{2})\s*(AM|PM)\s*-\s*\d{1,2}:\d{2}\s*(AM|PM)$",
    re.IGNORECASE,
)
_COARSE_BUCKET_ORDER = ["8:00 AM - 9:59 AM", "10:00 AM - 11:59 AM",
                        "12:00 PM - 1:59 PM", "2:00 PM - 3:59 PM",
                        "4:00 PM - 6:30 PM", "After Hours"]

# doc keys owned by the annual job (the quarterly job carries them forward)
ANNUAL_DOC_KEYS = ("annual_top", "annual_as_of", "interval",
                   "interval_note", "annual_files", "annual_adv_adt",
                   "issue", "issue_mix", "issue_note",
                   "participant", "participant_note")


def resolve_annual_urls(index_html: str) -> dict[str, dict[str, list]]:
    """Annual workbook links: {kind: {prod: [(url, year), ...]}}, newest first.

    kind is 'transaction' | 'issue', prod is 'corp' | 'agency' | 'sec'.
    Quarterly links (QnYYYY) never match this pattern.
    """
    base = "https://www.finra.org"
    found: dict[str, dict[str, dict[int, str]]] = {}
    for href, yr, kind, prod in ANNUAL_RE.findall(index_html or ""):
        p = _ANNUAL_PROD.get(prod.lower())
        if not p:
            continue
        url = href if href.startswith("http") else base + href
        found.setdefault(kind.lower(), {}).setdefault(p, {})[int(yr)] = url
    out: dict[str, dict[str, list]] = {}
    for kind, by_prod in found.items():
        out[kind] = {p: [(u, y) for y, u in sorted(by_y.items(), reverse=True)]
                     for p, by_y in by_prod.items()}
    return out


def _norm_fine_label(raw: str) -> str | None:
    """15-min bucket label -> 'HH:MM' (bucket start); 'After Hours' kept.

    Corporate/Agency labels are bucket END times and arrive as Excel time
    serials (day fractions, e.g. 0.34375 = 08:15) because the stdlib reader
    returns raw cell values; formatted strings ('08:15:00') are handled too.
    Securitized labels are ranges ('08:00 AM - 08:14 AM').
    """
    s = (raw or "").strip()
    if s.lower() == "after hours":
        return "After Hours"
    try:
        frac = float(s)
        if 0 <= frac < 1:
            total_min = int(round(frac * 1440)) - 15  # labels are END times
            return f"{(total_min // 60) % 24:02d}:{(total_min % 60):02d}"
    except (ValueError, TypeError):
        pass
    m = _FINE_END_RE.match(s)
    if m:
        total_min = int(m.group(1)) * 60 + int(m.group(2)) - 15
        return f"{(total_min // 60) % 24:02d}:{(total_min % 60):02d}"
    m = _FINE_RANGE_RE.match(s)
    if m:
        h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
        if ap == "PM" and h != 12:
            h += 12
        if ap == "AM" and h == 12:
            h = 0
        return f"{h:02d}:{mi}"
    return None


def parse_time_table(rows: dict[int, list[str]]) -> dict[str, dict[str, float]]:
    """Table C38/C39-style coarse time table -> {bucket: {period: value}}.

    Row 2 holds period headers: 4-digit years ('2023') or quarters
    ('Q1 2025'); anything else is skipped. Rows 3+ hold the 6 time buckets;
    parsing stops at the footer/copyright row.
    """
    header = rows.get(2, [])
    periods: list[str | None] = []
    for c in header[1:]:
        s = (c or "").strip()
        if re.fullmatch(r"\d{4}", s) or re.fullmatch(r"Q[1-4]\s+\d{4}", s,
                                                    re.IGNORECASE):
            periods.append(s)
        else:
            periods.append(None)
    out: dict[str, dict[str, float]] = {}
    for r in sorted(rows):
        if r < 3:
            continue
        row = rows[r]
        if not row:
            continue
        bucket = row[0].strip()
        if not bucket or bucket.startswith("©"):
            break
        per: dict[str, float] = {}
        for i, p in enumerate(periods):
            if p is None or i + 1 >= len(row):
                continue
            v = to_float(row[i + 1])
            if v is not None:
                per[p] = v
        if per:
            out[bucket] = per
    return out


def parse_graph_data_time(rows: dict[int, list[str]]) -> list[dict]:
    """All TIME SEGMENTS blocks in a Graph Data sheet.

    Returns [{"sub": 'abs auto' | '' , "sec_style": bool, "fine": [...]}].
    sec_style blocks (securitized) carry % trades / % OPB / % RPB;
    corporate/agency blocks carry % trades / % par / avg trade size.
    """
    blocks: list[dict] = []
    for r in sorted(rows):
        row = rows[r]
        if not row or row[0].strip() != "TIME SEGMENTS":
            continue
        if not any("Percentage Executed" in (c or "") for c in row):
            continue  # not the header row we expect; skip, never misread
        meas = " ".join(rows.get(r - 1, [])).lower()
        sec_style = "original principal balance" in meas
        sub = ""
        if r - 2 in rows and len(rows[r - 2]) > 1:
            sub = rows[r - 2][1].strip().lower()
        fine: list[dict] = []
        for rr in range(r + 1, r + 70):
            drow = rows.get(rr)
            if not drow:
                break
            label = _norm_fine_label(drow[0] if drow else "")
            if label is None:
                if fine:
                    break  # footer reached
                continue
            def _f(i: int) -> float | None:
                return to_float(drow[i]) if len(drow) > i else None
            if sec_style:
                fine.append({"t": label, "trades": _f(1),
                             "opb": _f(3), "rpb": _f(5)})
            else:
                fine.append({"t": label, "trades": _f(1),
                             "par": _f(3), "avg_size": _f(5)})
            if label == "After Hours":
                break
        if fine:
            blocks.append({"sub": sub, "sec_style": sec_style, "fine": fine})
    return blocks


def parse_annual_transaction(prod: str, data: bytes) -> dict:
    """One annual Transaction Information workbook -> interval data.

    prod: 'corp' | 'agency' | 'sec'. Returns {"blocks", "missing"} where
    blocks maps product key -> {"fine", "coarse", "scope"}.
    """
    sheets = _sheet_map(data)
    blocks: dict[str, dict] = {}
    missing: list[str] = []

    def _coarse(pkey: str) -> dict:
        spec = INTERVAL_TABLES[pkey]
        coarse: dict[str, dict] = {}
        for role, sname in (("trades", spec["trades"]),
                            ("par", spec["par"])):
            si = _match_sheet(sheets, sname)
            if si is None:
                missing.append(f"{pkey}:{sname}")
                continue
            for bucket, per in parse_time_table(
                    read_sheet(data, sheet=si)).items():
                coarse.setdefault(bucket, {})[role] = per
        return coarse

    if prod in ("corp", "agency"):
        si = _match_sheet(sheets, "Graph Data")
        fine = None
        if si is None:
            missing.append(f"{prod}:Graph Data")
        else:
            blks = parse_graph_data_time(read_sheet(data, sheet=si))
            plain = [b for b in blks if not b["sec_style"]]
            if plain:
                fine = plain[0]["fine"]
            else:
                missing.append(f"{prod}:TIME SEGMENTS block")
        blocks[prod] = {"fine": fine, "coarse": _coarse(prod)}
    elif prod == "sec":
        si = _match_sheet(sheets, "Graph Data")
        by_sub: dict[str, list] = {}
        if si is None:
            missing.append("sec:Graph Data")
        else:
            for b in parse_graph_data_time(read_sheet(data, sheet=si)):
                pkey = _SEC_FINE_SUB.get(b["sub"])
                if pkey and pkey not in by_sub:
                    by_sub[pkey] = b["fine"]
                elif not pkey:
                    missing.append(f"sec:unmapped fine block {b['sub']!r}")
        for pkey in ("abs", "absx", "cmo", "mbs", "tba"):
            entry: dict = {"coarse": _coarse(pkey)}
            if pkey in by_sub:
                entry["fine"] = by_sub[pkey]
                entry["scope"] = _SEC_SCOPE[pkey]
            elif pkey != "tba":
                missing.append(f"{pkey}:fine grid")
            else:
                entry["fine"] = None  # TBA has no 15-min grid in FINRA's file
            blocks[pkey] = entry
    else:
        missing.append(f"unknown prod {prod!r}")
    return {"blocks": blocks, "missing": missing}


def parse_annual_issue(prod: str, data: bytes) -> dict:
    """One annual Issue Information workbook -> {"top", "missing"}."""
    sheets = _sheet_map(data)
    top: dict[str, list] = {}
    missing: list[str] = []
    for sheet_name, key, vkind in ANNUAL_TOP.get(prod, []):
        si = _match_sheet(sheets, sheet_name)
        if si is None:
            missing.append(f"{prod}:{sheet_name}")
            continue
        top[key] = parse_top50(read_sheet(data, sheet=si), vkind)
    return {"top": top, "missing": missing}


# ---- annual full tables: transaction ADV/ADT, issue outstanding, participant ----

# Annual Transaction Information tables: full-year average-daily trades and
# par value by trade-size bucket, plus buy-sell ratio tables.
# (corp, ig, hy, convig, convhy) / (agency) / (abs, absx, cmo, mbs, tba)
# follow the quarterly product keys. bs flavors: "trades"/"par" for
# corp/agency/tba; "trades_opb"/"trades_rpb"/"par_opb"/"par_rpb" for
# abs/absx/cmo/mbs (FINRA splits OPB and RPB ratio tables).
ANNUAL_TXN_TABLES = {
    "corp": {
        "trades": [("Table C20", "corp"), ("Table C21", "ig"),
                   ("Table C22", "hy"), ("Table C23", "convig"),
                   ("Table C24", "convhy")],
        "par": [("Table C25", "corp"), ("Table C26", "ig"),
                ("Table C27", "hy"), ("Table C28", "convig"),
                ("Table C29", "convhy")],
        "bs": [("Table C30", "ig", "trades"), ("Table C31", "hy", "trades"),
               ("Table C32", "convig", "trades"),
               ("Table C33", "convhy", "trades"),
               ("Table C34", "ig", "par"), ("Table C35", "hy", "par"),
               ("Table C36", "convig", "par"),
               ("Table C37", "convhy", "par")],
    },
    "agency": {
        "trades": [("Table A14", "agency")],
        "par": [("Table A16", "agency")],
        "bs": [("Table A17", "agency", "trades"),
               ("Table A18", "agency", "par")],
    },
    "sec": {
        "trades": [("Table S21", "abs"), ("Table S22", "absx"),
                   ("Table S23", "cmo"), ("Table S24", "mbs"),
                   ("Table S25", "tba")],
        "par": [("Table S27", "abs"), ("Table S29", "absx"),
                ("Table S31", "cmo"), ("Table S33", "mbs"),
                ("Table S35", "tba")],
        "bs": [("Table S36", "abs", "trades_opb"),
               ("Table S37", "abs", "trades_rpb"),
               ("Table S38", "absx", "trades_opb"),
               ("Table S39", "absx", "trades_rpb"),
               ("Table S40", "cmo", "trades_opb"),
               ("Table S41", "cmo", "trades_rpb"),
               ("Table S42", "mbs", "trades_opb"),
               ("Table S43", "mbs", "trades_rpb"),
               ("Table S44", "tba", "trades"),
               ("Table S45", "abs", "par_opb"),
               ("Table S46", "abs", "par_rpb"),
               ("Table S47", "absx", "par_opb"),
               ("Table S48", "absx", "par_rpb"),
               ("Table S49", "cmo", "par_opb"),
               ("Table S50", "cmo", "par_rpb"),
               ("Table S51", "mbs", "par_opb"),
               ("Table S52", "mbs", "par_rpb"),
               ("Table S53", "tba", "par")],
    },
}

# Annual Issue Information tables: issues outstanding (counts) by
# rating (corp), issuer (agency), or type/collateral (sec).
ANNUAL_ISSUE_TABLES = {
    "corp": [("Table C1", "corp", "Non-convertible"),
             ("Table C2", "conv", "Convertible")],
    "agency": [("Table A1", "agency", "Agency")],
    "sec": [("Table S1", "abs", "ABS"), ("Table S2", "absx", "ABSX"),
            ("Table S3", "cmo", "CMO"), ("Table S4", "mbs", "MBS")],
}

# Annual Participant Information: primary (all-eligible-firms) table per
# product. Segment splits (customer/interdealer/size/IG/HY) exist in the
# workbooks but are not parsed — the headline concentration table is the
# new information with no quarterly equivalent.
ANNUAL_PART_TABLES = {
    "corp": [("Table C9", "corp", "All eligible TRACE reporting firms")],
    "agency": [("Table A4", "agency",
               "All eligible TRACE reporting firms")],
    "sec": [("Table S5", "abs", "ABS"), ("Table S12", "absx", "ABSX"),
            ("Table S15", "cmo", "CMO"), ("Table S18", "mbs", "MBS"),
            ("Table S19", "tba", "TBA")],
}

# Graph Data quarterly-history blocks -> (product, measure).
# corp OVERALL S1 is skipped: fb-corp-* cycle series are P1, not S1.
# CONVERTIBLES is skipped: no combined convig+convhy cycle series exists.
_HIST_SECTIONS = {
    "corp": {"investment grade": "ig", "high yield": "hy"},
    "agency": {"overall": "agency"},
}
_HIST_MEASURES = {
    "corp": {"trades": "trades", "par value": "pv"},
    "agency": {"s1 trades": "trades", "s1 par value": "pv"},
}
_HIST_SEC_BLOCKS = {
    "abs s1 trades": ("abs", "trades"),
    "abs s1 original principal balance": ("abs", "pv"),
    "absx trades": ("absx", "trades"),
    "absx original principal balance": ("absx", "pv"),
    "cmo trades": ("cmo", "trades"),
    "cmo original principal balance": ("cmo", "pv"),
    "mbs trades": ("mbs", "trades"),
    "mbs original principal balance": ("mbs", "pv"),
    "tba trades": ("tba", "trades"),
    "tba par value": ("tba", "pv"),
}
_TIER_RE = re.compile(r"most active\s+(\d+)", re.IGNORECASE)


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _year_col(rows: dict[int, list[str]], year: int) -> int | None:
    """Row-2 column whose header is the 4-digit year (merged-header safe)."""
    for i, c in enumerate(rows.get(2, [])):
        if (c or "").strip() == str(year):
            return i
    return None


def _period_headers(rows: dict[int, list[str]]) -> list[str | None]:
    """Row-2 -> per-column period label (year or quarter), else None."""
    out: list[str | None] = []
    for c in rows.get(2, [])[1:]:
        s = (c or "").strip()
        if re.fullmatch(r"\d{4}", s) or parse_quarter_label(s):
            out.append(s)
        else:
            out.append(None)
    return out


def parse_annual_table(rows: dict[int, list[str]],
                       year: int) -> dict | None:
    """Annual Table C20-style: full-year average-daily Total + 6 buckets.

    Same bucket-row layout as the quarterly sheets; the value column is
    the one headed by the 4-digit year. Returns None when the year column
    is absent (never a crash).
    """
    col = _year_col(rows, year)
    if col is None:
        return None
    total: float | None = None
    buckets: list[tuple[str, float]] = []
    seen_total = False
    for r in sorted(rows):
        if r < 3:
            continue
        row = rows[r]
        label = row[0].strip() if row else ""
        if not label:
            continue
        if label.lower().startswith("total"):
            if seen_total:
                break
            seen_total = True
            total = to_float(row[col]) if len(row) > col else None
            continue
        if not seen_total:
            continue
        bkey = _bucket_key(label)
        if bkey is not None:
            v = to_float(row[col]) if len(row) > col else None
            if v is not None:
                buckets.append((bkey, v))
            if len(buckets) == 6:
                break
    return {"total": total, "buckets": buckets}


def parse_annual_buysell(rows: dict[int, list[str]],
                         year: int) -> list | None:
    """Annual Table C30-style buy-sell ratio: year header spans a
    Gross|Net|Ratio triple. Returns [(bucket, gross, net, ratio)] for the
    top-level size buckets; stops at the drill-down. None when the year
    column is absent."""
    col = _year_col(rows, year)
    if col is None:
        return None
    out: list[tuple[str, float | None, float | None, float | None]] = []
    for r in sorted(rows):
        if r < 4:
            continue
        row = rows[r]
        bkey = _bucket_key(row[0]) if row else None
        if bkey is None:
            if out:
                break
            continue

        def _f(i: int) -> float | None:
            return to_float(row[col + i]) if len(row) > col + i else None

        out.append((bkey, _f(0), _f(1), _f(2)))
    return out


def parse_annual_transaction_full(prod: str, data: bytes,
                                  year: int) -> dict:
    """Annual Transaction tables -> {"adv_adt", "missing"}.

    adv_adt: {prod: {"trades": {"total", "buckets": {bkey: v}},
                     "par": {...},
                     "buy_sell": {flavor: {bkey: {"gross","net","ratio"}}}}}
    """
    sheets = _sheet_map(data)
    adv: dict[str, dict] = {}
    missing: list[str] = []
    spec = ANNUAL_TXN_TABLES.get(prod, {})
    for role in ("trades", "par"):
        for sname, pkey in spec.get(role, []):
            si = _match_sheet(sheets, sname)
            if si is None:
                missing.append(f"{pkey}:{sname}")
                continue
            t = parse_annual_table(read_sheet(data, sheet=si), year)
            if t is None:
                missing.append(f"{pkey}:{sname}:no {year} column")
                continue
            adv.setdefault(pkey, {})[role] = {
                "total": t["total"], "buckets": dict(t["buckets"])}
    for sname, pkey, flavor in spec.get("bs", []):
        si = _match_sheet(sheets, sname)
        if si is None:
            missing.append(f"{pkey}:{sname}")
            continue
        b = parse_annual_buysell(read_sheet(data, sheet=si), year)
        if b is None:
            missing.append(f"{pkey}:{sname}:no {year} column")
            continue
        bs = adv.setdefault(pkey, {}).setdefault("buy_sell", {})
        bs[flavor] = {k: {"gross": g, "net": n, "ratio": r}
                      for k, g, n, r in b}
    return {"adv_adt": adv, "missing": missing}


def parse_graph_data_history(kind: str, data: bytes) -> dict[str, list]:
    """Graph Data quarterly blocks -> {series_suffix: [(qdate, value)]}.

    Headline totals only (Customer Buy + Customer Sell + Interdealer
    detail rows summed per quarter column). Used by the annual job to
    extend the quarterly cycle series back beyond the 12-quarter
    backfill; the job only adds dates the quarterly job has not set
    (never clobbers).
    """
    try:
        sheets = _sheet_map(data)
    except ValueError:
        return {}
    si = _match_sheet(sheets, "Graph Data")
    if si is None:
        return {}
    rows = read_sheet(data, sheet=si)
    qcols: list[tuple[int, date]] = []
    for i, c in enumerate(rows.get(2, [])):
        yq = parse_quarter_label(c)
        if yq:
            qcols.append((i, quarter_end(*yq)))
    if not qcols:
        return {}
    acc: dict[tuple[str, str], dict] = {}

    def _add(key: tuple[str, str], row: list[str]) -> None:
        for i, qd in qcols:
            v = to_float(row[i]) if len(row) > i else None
            if v is not None:
                acc.setdefault(key, {}).setdefault(qd, 0.0)
                acc[key][qd] += v

    if kind == "sec":
        cur: tuple[str, str] | None = None
        for r in sorted(rows):
            if r < 3:
                continue
            row = rows[r]
            label = _norm_label(row[0]) if row else ""
            if not label:
                cur = None
                continue
            if row[0][:1].isspace():
                if cur:
                    _add(cur, row)
                continue
            cur = _HIST_SEC_BLOCKS.get(label)  # None: not a history block
    else:
        sections = _HIST_SECTIONS.get(kind, {})
        measures = _HIST_MEASURES.get(kind, {})
        cur_prod: str | None = None
        cur_meas: str | None = None
        for r in sorted(rows):
            if r < 3:
                continue
            row = rows[r]
            label = _norm_label(row[0]) if row else ""
            if not label:
                continue
            if row[0][:1].isspace():
                if cur_prod and cur_meas:
                    _add((cur_prod, cur_meas), row)
                continue
            if label in sections:
                cur_prod, cur_meas = sections[label], None
            elif label in measures and cur_prod:
                cur_meas = measures[label]
            else:
                cur_prod, cur_meas = None, None
    return {f"fb-{p}-{m}": sorted(d.items())
            for (p, m), d in acc.items() if p}


def parse_annual_issue_table(rows: dict[int, list[str]]) -> dict:
    """Annual Issue Table C1-style -> {"total", "breakdown", "periods"}.

    Row 2 holds year + quarter period headers. The first valued "Total"
    row is the overall total; every other valued label row becomes a
    breakdown entry (rating / issuer / type hierarchy kept flat).
    Stops at the Note:/copyright footer.
    """
    periods = _period_headers(rows)
    total: dict[str, float] = {}
    breakdown: dict[str, dict[str, float]] = {}
    seen_total = False
    for r in sorted(rows):
        if r < 3:
            continue
        row = rows[r]
        if not row:
            continue
        label = row[0].strip()
        if not label:
            continue
        if label.lower().startswith(("note:", "©")):
            break
        vals: dict[str, float] = {}
        for i, p in enumerate(periods):
            if p is None or i + 1 >= len(row):
                continue
            v = to_float(row[i + 1])
            if v is not None:
                vals[p] = v
        if not vals:
            continue
        if label.lower().startswith("total") and not seen_total:
            seen_total = True
            total = vals
        else:
            breakdown[label] = vals
    return {"total": total, "breakdown": breakdown,
            "periods": [p for p in periods if p]}


def parse_annual_issue_full(prod: str, data: bytes) -> dict:
    """Annual Issue tables -> {"issue", "missing"}.

    issue: {prod: {"label", "total": {period: v},
                   "breakdown": {label: {period: v}}}}.
    Counts only — FINRA's Issue Information tables carry no par
    outstanding (documented in issue_note).
    """
    sheets = _sheet_map(data)
    issue: dict[str, dict] = {}
    missing: list[str] = []
    for sname, pkey, label in ANNUAL_ISSUE_TABLES.get(prod, []):
        si = _match_sheet(sheets, sname)
        if si is None:
            missing.append(f"{pkey}:{sname}")
            continue
        t = parse_annual_issue_table(read_sheet(data, sheet=si))
        issue[pkey] = {"label": label, "total": t["total"],
                       "breakdown": t["breakdown"],
                       "periods": t["periods"]}
    return {"issue": issue, "missing": missing}


def parse_annual_issue_mix(prod: str, data: bytes) -> dict:
    """Issue Graph Data -> {group: {label: {issues, trades, par, rpb}}}.

    Rating/issuer/collateral mix snapshot for the workbook year:
    issues outstanding plus average-daily S1 trades and par by segment.
    corp/agency group under the product key; sec groups by sub-product.
    TBA has trades only (no issues outstanding).
    """
    try:
        sheets = _sheet_map(data)
    except ValueError:
        return {}
    si = _match_sheet(sheets, "Graph Data")
    if si is None:
        return {}
    rows = read_sheet(data, sheet=si)
    mix: dict[str, dict[str, dict]] = {}
    cur_sec: str | None = None
    for r in sorted(rows):
        if r < 3:
            continue
        row = rows[r]
        if not row:
            continue
        label = row[0].strip()
        if not label or label.startswith("©"):
            if label.startswith("©"):
                break
            continue
        n_b = to_float(row[1]) if len(row) > 1 else None
        if n_b is None:
            # section header (sec workbooks) or column header row
            if (prod == "sec" and _norm_label(label) in
                    ("abs", "absx", "cmo", "mbs", "tba")):
                cur_sec = _norm_label(label)
            continue
        group = cur_sec or prod
        entry: dict[str, float] = {}
        if group == "tba":
            entry["trades"] = n_b
        else:
            entry["issues"] = n_b
        for key, idx in (("trades", 2), ("par", 3), ("rpb", 4)):
            if key in entry:
                continue
            v = to_float(row[idx]) if len(row) > idx else None
            if v is not None:
                entry[key] = v
        mix.setdefault(group, {})[label] = entry
    return mix


def parse_annual_participant(rows: dict[int, list[str]]) -> dict | None:
    """Table C9-style participant concentration.

    Firm counts (TRACE reporting / unique / avg per day) plus the share
    of S1 trades and par captured by the most-active-N-firm tiers
    (top 5/10/25/50 in FINRA's files; whatever tiers exist are kept with
    their labels). Returns None when no tier rows are found — the caller
    records it as missing instead of crashing.
    """
    periods = _period_headers(rows)
    out: dict = {"firms_reporting": {}, "unique_firms": {},
                 "avg_firms_per_day": {},
                 "trades": {}, "par": {},
                 "periods": [p for p in periods if p]}
    section: str | None = None
    for r in sorted(rows):
        if r < 3:
            continue
        row = rows[r]
        if not row:
            continue
        label = row[0].strip()
        if not label:
            continue
        if label.startswith("©"):
            break
        vals: dict[str, float] = {}
        for i, p in enumerate(periods):
            if p is None or i + 1 >= len(row):
                continue
            v = to_float(row[i + 1])
            if v is not None:
                vals[p] = v
        ll = label.lower()
        if ll.startswith("trace reporting firms"):
            out["firms_reporting"] = vals
        elif ll.startswith("unique firms reporting"):
            out["unique_firms"] = vals
        elif ll.startswith("average reporting firms"):
            out["avg_firms_per_day"] = vals
        elif "captured" in ll:
            section = "par" if "par" in ll else "trades"
        else:
            m = _TIER_RE.search(label)
            if m and section and vals:
                out[section].setdefault(m.group(1), {}).update(vals)
    if not out["trades"] and not out["par"]:
        return None
    return out


def parse_annual_participant_full(prod: str, data: bytes) -> dict:
    """Annual Participant tables -> {"participant", "missing"}.

    participant: {prod: {"segment", "tiers": ["5","10","25","50"],
        "trades_pct": {tier: {period: v}}, "par_pct": {...},
        "firms_reporting": {period: v}, "unique_firms": {...},
        "avg_firms_per_day": {...}}}. Fractions (0-1), not percents.
    """
    sheets = _sheet_map(data)
    part: dict[str, dict] = {}
    missing: list[str] = []
    for sname, pkey, segment in ANNUAL_PART_TABLES.get(prod, []):
        si = _match_sheet(sheets, sname)
        if si is None:
            missing.append(f"{pkey}:{sname}")
            continue
        t = parse_annual_participant(read_sheet(data, sheet=si))
        if t is None:
            missing.append(f"{pkey}:{sname}:no tier rows")
            continue
        tiers = sorted(set(t["trades"]) | set(t["par"]),
                       key=lambda x: int(x) if x.isdigit() else 999)
        part[pkey] = {"segment": segment, "tiers": tiers,
                      "trades_pct": t["trades"], "par_pct": t["par"],
                      "firms_reporting": t["firms_reporting"],
                      "unique_firms": t["unique_firms"],
                      "avg_firms_per_day": t["avg_firms_per_day"],
                      "periods": t["periods"]}
    return {"participant": part, "missing": missing}


async def fetch_finra_factbook_annual(store: Store, get_text: GetText,
                                      get_bytes: GetBytes,
                                      today: date | None = None) -> str:
    """Yearly job: annual Transaction + Issue Information workbooks.

    Parses annual top-N most-traded lists and time-of-day execution stats
    (15-min grids + coarse bucket history) into the "finra_factbook"
    snapshot doc as annual_top / interval / annual_as_of. Merges with the
    existing doc — the quarterly keys are never clobbered.

    HONEST CAVEAT: interval data comes from the ANNUAL workbooks only, so
    it refreshes yearly, not quarterly. Securitized interval data covers
    the largest sub-segment of each product (Auto Loan / CDO / Agency CMO),
    not the whole product.
    """
    today = today or date.today()
    headers = {"User-Agent": CONTACT_UA}

    html = await get_text(INDEX_URL, headers=headers)
    urls = resolve_annual_urls(html)
    if not urls:
        raise ValueError("no annual fact-book links on the index page")

    annual_top: dict[str, dict] = {}
    interval: dict[str, dict] = {}
    annual_adv_adt: dict[str, dict] = {}
    issue: dict[str, dict] = {}
    issue_mix: dict[str, dict] = {}
    participant: dict[str, dict] = {}
    annual_files: list[dict] = []
    latest_year: int | None = None

    for prod, lst in urls.get("transaction", {}).items():
        url, year = lst[0]
        latest_year = year if latest_year is None else max(latest_year, year)
        entry: dict = {"kind": "transaction", "prod": prod, "year": year}
        try:
            data = await get_bytes(url, headers=headers)
            p = parse_annual_transaction(prod, data)
            t = parse_annual_transaction_full(prod, data, year)
        except Exception as exc:  # noqa: BLE001 — one bad file skips
            log.warning("factbook annual transaction failed %s: %s", url, exc)
            entry["error"] = str(exc)[:120]
            annual_files.append(entry)
            await asyncio.sleep(REQUEST_GAP)
            continue
        interval.setdefault(str(year), {}).update(p["blocks"])
        annual_adv_adt.setdefault(str(year), {}).update(t["adv_adt"])
        # quarterly history merge: only dates the quarterly job has not
        # set (never clobbers). Both jobs upsert, so history merges both
        # ways; the quarterly workbook stays authoritative on overlap.
        try:
            for suffix, pts in parse_graph_data_history(prod, data).items():
                key = f"cycle:{suffix}"
                try:
                    existing = store.points(key) or {}
                except Exception:  # noqa: BLE001 — read failure: add nothing
                    existing = {}
                new = [(d, v) for d, v in pts if d not in existing]
                if new:
                    store.upsert_points(key, new)
        except Exception as exc:  # noqa: BLE001 — history merge is bonus
            log.warning("factbook annual history merge failed %s: %s",
                        url, exc)
            entry["history_merge_error"] = str(exc)[:120]
        entry["missing"] = p["missing"] + t["missing"]
        annual_files.append(entry)
        await asyncio.sleep(REQUEST_GAP)

    for prod, lst in urls.get("issue", {}).items():
        url, year = lst[0]
        entry = {"kind": "issue", "prod": prod, "year": year}
        try:
            data = await get_bytes(url, headers=headers)
            t = parse_annual_issue(prod, data)
            f = parse_annual_issue_full(prod, data)
            mix = parse_annual_issue_mix(prod, data)
        except Exception as exc:  # noqa: BLE001 — one bad file skips
            log.warning("factbook annual issue failed %s: %s", url, exc)
            entry["error"] = str(exc)[:120]
            annual_files.append(entry)
            await asyncio.sleep(REQUEST_GAP)
            continue
        annual_top.setdefault(str(year), {}).update(t["top"])
        issue.setdefault(str(year), {}).update(f["issue"])
        if mix:
            issue_mix.setdefault(str(year), {}).update(mix)
        entry["missing"] = t["missing"] + f["missing"]
        entry["lists"] = {k: len(v) for k, v in t["top"].items()}
        annual_files.append(entry)
        await asyncio.sleep(REQUEST_GAP)

    for prod, lst in urls.get("participant", {}).items():
        url, year = lst[0]
        entry = {"kind": "participant", "prod": prod, "year": year}
        try:
            data = await get_bytes(url, headers=headers)
            q = parse_annual_participant_full(prod, data)
        except Exception as exc:  # noqa: BLE001 — one bad file skips
            log.warning("factbook annual participant failed %s: %s", url, exc)
            entry["error"] = str(exc)[:120]
            annual_files.append(entry)
            await asyncio.sleep(REQUEST_GAP)
            continue
        participant.setdefault(str(year), {}).update(q["participant"])
        entry["missing"] = q["missing"]
        annual_files.append(entry)
        await asyncio.sleep(REQUEST_GAP)

    # merge into the existing doc; quarterly keys survive untouched
    try:
        prev = store.doc("finra_factbook")
        payload = dict(prev.payload) if prev and isinstance(prev.payload,
                                                            dict) else {}
    except Exception:  # noqa: BLE001 — a doc read failure must not fail the run
        payload = {}
    payload.update({
        "annual_top": annual_top,
        "annual_as_of": str(latest_year) if latest_year else None,
        "interval": interval,
        "interval_note": (
            "Time-of-day execution stats come from FINRA's ANNUAL "
            "Transaction Information workbooks: yearly refresh, not "
            "quarterly. Securitized products cover the largest sub-segment "
            "(ABS Auto Loan / ABSX CDO / Agency CMO); TBA has coarse "
            "buckets only, no 15-min grid."
        ),
        "annual_adv_adt": annual_adv_adt,
        "issue": issue,
        "issue_mix": issue_mix,
        "issue_note": (
            "Issues outstanding are COUNTS of CUSIPs as of the last day of "
            "each period — FINRA's Issue Information tables carry no par "
            "outstanding. Breakdowns: rating (corporate), issuer (agency), "
            "type/collateral (securitized). Mix snapshot is the workbook "
            "year. Annual refresh."
        ),
        "participant": participant,
        "participant_note": (
            "Dealer-concentration stats come from FINRA's ANNUAL "
            "Participant Information workbooks: yearly refresh, no "
            "quarterly equivalent. Tiers are the most-active N firms "
            "(top 5/10/25/50); values are fractions of S1 activity. "
            "Primary all-eligible-firms table per product; segment splits "
            "(customer/interdealer/size/IG/HY) exist in the source files."
        ),
        "annual_files": annual_files,
    })
    store.put_doc("finra_factbook", payload, source=SOURCE)
    log.info("finra_factbook annual: %d files, latest %s",
             len(annual_files), latest_year)
    return SOURCE


async def fetch_finra_factbook(store: Store, get_text: GetText,
                               get_bytes: GetBytes,
                               today: date | None = None,
                               backfill_quarters: int = BACKFILL_QUARTERS) -> str:
    """Quarterly job: FINRA TRACE Fact Book workbooks.

    Resolves the latest quarter's 3 workbooks from the index page; on an
    empty store, also backfills the previous `backfill_quarters` quarters
    (idempotent upserts at quarter-end). Writes quarterly cycle series and
    a "finra_factbook" snapshot doc with the latest quarter's top-50 lists.
    """
    today = today or date.today()
    headers = {"User-Agent": CONTACT_UA}

    html = await get_text(INDEX_URL, headers=headers)
    urls = resolve_workbook_urls(html)
    if not urls:
        raise ValueError("no fact-book workbook links on the index page")

    # latest quarter per kind first (snapshot doc source), then backfill
    jobs: list[tuple[str, str, int, int]] = []
    for kind, lst in urls.items():
        jobs.append((kind, lst[0][0], lst[0][1], lst[0][2]))
    backfill = len(store.points("cycle:fb-ig-trades")) < 4
    if backfill and backfill_quarters > 1:
        for kind, lst in urls.items():
            for url, y, q in lst[1:backfill_quarters]:
                jobs.append((kind, url, y, q))

    latest: dict[str, dict] = {}
    quarters: list[dict] = []
    for kind, url, y, q in jobs:
        try:
            data = await get_bytes(url, headers=headers)
        except Exception as exc:  # noqa: BLE001 — one bad file skips
            log.warning("factbook download failed %s: %s", url, exc)
            continue
        try:
            p = parse_workbook(kind, data)
        except Exception as exc:  # noqa: BLE001 — one bad file skips
            log.warning("factbook parse failed %s: %s", url, exc)
            continue
        if p["qdate"] is None:
            log.warning("factbook %s: no quarter label found", url)
            continue
        for suffix, v in p["series"].items():
            store.upsert_points(f"cycle:{suffix}", [(p["qdate"], v)])
        if kind not in latest:
            latest[kind] = p
        quarters.append({"kind": kind, "quarter": p["quarter"],
                         "series": len(p["series"]),
                         "top_lists": {k: len(v) for k, v in p["top"].items()},
                         "missing": p["missing"]})
        await asyncio.sleep(REQUEST_GAP)

    if not latest:
        raise ValueError("fact-book run parsed zero workbooks")

    # snapshot doc from the latest quarter of each kind
    top: dict[str, list] = {}
    headlines: dict[str, dict] = {}
    bs_latest: dict[str, list] = {}
    buckets_latest: dict[str, dict] = {}
    as_of = None
    qdate = None
    for kind, p in latest.items():
        as_of = as_of or p["quarter"]
        qdate = qdate or (p["qdate"].isoformat() if p["qdate"] else None)
        top.update(p["top"])
        headlines.update(p["headlines"])
        for prod in KINDS[kind]:
            bs_rows = [
                {"bucket": bkey,
                 "gross": p["series"].get(f"fb-{prod}-bsg-b-{bkey}"),
                 "net": p["series"].get(f"fb-{prod}-bsn-b-{bkey}"),
                 "ratio": p["series"].get(f"fb-{prod}-bsr-b-{bkey}")}
                for _, bkey in BUCKETS
                if f"fb-{prod}-bsr-b-{bkey}" in p["series"]
            ]
            if bs_rows:
                bs_latest[prod] = bs_rows
            tb = {bkey: p["series"].get(f"fb-{prod}-trades-b-{bkey}")
                  for _, bkey in BUCKETS}
            pb = {bkey: p["series"].get(f"fb-{prod}-pv-b-{bkey}")
                  for _, bkey in BUCKETS}
            if any(v is not None for v in list(tb.values()) + list(pb.values())):
                buckets_latest[prod] = {"trades": tb, "pv": pb}

    payload = {
        "as_of": as_of,
        "quarter_end": qdate,
        "top": top,
        "headlines": headlines,
        "buy_sell_latest": bs_latest,
        "buckets_latest": buckets_latest,
        "quarters": quarters,
    }
    # The annual job merges annual_top/interval into this doc; carry those
    # keys forward so the quarterly refresh never clobbers them.
    try:
        prev = store.doc("finra_factbook")
        prev_p = prev.payload if prev and isinstance(prev.payload, dict) else {}
    except Exception:  # noqa: BLE001 — a doc read failure must not fail the run
        prev_p = {}
    for k in ANNUAL_DOC_KEYS:
        if k in prev_p and k not in payload:
            payload[k] = prev_p[k]
    store.put_doc("finra_factbook", payload, source=SOURCE)
    log.info("finra_factbook: %d workbooks parsed, latest %s",
             len(quarters), as_of)
    return SOURCE
