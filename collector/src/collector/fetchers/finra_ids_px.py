"""FINRA-ICE Data Services Structured Pricing Tables (PXTABLES) — daily.

Companion to finra_ids_star: the same monthly ZIP
(https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-YYYYMM.zip)
also holds FINRA_IDS_PXTABLES-YYYYMMDD.xlsx, one per trading day, with
price/volume metrics per product block. Parsed here; the ZIP download is
shared with the STAR fetcher (see fetch_finra_ids_star).

PXTABLES layout (verified against the 2026-09-30 file; 7 data sheets,
1-indexed for read_sheet; sheets 8-9 are a data dictionary and an
error-correction log, not parsed):

  sheet 1 "tba"   — AGENCY PASS-THRU (TBA, STIP, $ ROLLS): sub-tables per
                    SINGLE FAMILY 15Y/30Y x settlement month (Sep-Dec);
                    blocks per issuer (UMBS/FHLMC/GNMA); coupon columns
                    (<= 3.5, 4, 4.5, 5, 5.5, 6, > 6).
  sheet 2 "mbs"   — AGENCY PASS-THRU (SPECIFIED): sub-tables 15Y / 30Y /
                    ARMS-HYBRIDS; blocks per issuer (UMBS/FNMA/FHLMC/GNMA);
                    coupon columns.
  sheet 3 "agcmo" — AGENCY CMO P&I / IO/PO - BY DEAL VINTAGE: blocks per
                    issuer (FNMA/FHLMC/GNMA); vintage columns
                    (PRE-2009, 2009-2013, 2014-2016, POST-2016).
  sheet 4 "nag"   — NON-AGENCY CMO | ABS: Investment Grade block then
                    Non-Investment Grade block (grade flips mid-table, dim
                    labels not repeated); product columns
                    (NONAGENCY CMO P&I, NONAGENCY CMO IO/PO, ABS).
                    Plus NON AGENCY CMO (P&I)/(IO/PO) - BY DEAL VINTAGE
                    sub-tables (grade blocks x vintage columns).
  sheet 5 "cmbs"  — AGENCY CMBS / CMBS CONDUIT / CMBS OTHER - BY DEAL
                    VINTAGE (two-level headers: product row + vintage row).
  sheet 6 "wcmbs" — weekly AGENCY CMBS / CMBS CONDUIT AAA LCF (P&I) /
                    CMBS CONDUIT / CMBS OTHER - BY DEAL VINTAGE. The sheet
                    covers a calendar week ("DATA AS OF: YYYY-MM-DD to
                    YYYY-MM-DD"); points are dated at the week end.
  sheet 7 "cboclo"— CBO/CDO/CLO: overall column + CBO/CDO/CLO x vintage
                    (PRE-2023, 2023-2026) + AAA / NON-AAA IG /
                    NON-INVESTMENT GRADE x vintage.

Each block carries the same 10 metric rows (labels in column B):
  AVERAGE PRICE -> avgpx | WEIGHTED AVG. PRICE -> wavgpx |
  AVG. PRICE BOTTOM 5 TRADES -> bot5 | 2ND/3RD/4TH QUARTILE PRICE -> q2/q3/q4 |
  AVG. PRICE TOP 5 TRADES -> top5 | STANDARD DEVIATION -> stdev |
  VOLUME OF TRADES (000'S) -> vol ($000s, stored x1000 like STAR) |
  NUMBER OF TRADES -> ntrades
Breakdown rows (sheets 3-7 only; sheets 1/2 have none per FINRA's note)
interleaved after each metric row are stored as their own series:
  CUSTOMER BUY -> custbuy | CUSTOMER SELL -> custsell |
  DEALER TO DEALER -> d2d | <= $1MM -> tick-le1mm | <= $10MM -> tick-le10mm |
  <= $100MM -> tick-le100mm | > $10MM -> tick-gt10mm | > $100MM -> tick-gt100mm,
keyed {suffix}-vol-{bd} (par, $000s, stored x1000) after the vol row and
{suffix}-ntrades-{bd} (counts) after the ntrades row.

Series naming: cycle:starpx-{sheet}-{sub}-{block}-{dim}-{metric}
(all lowercase, sanitized; empty parts dropped), e.g.
  cycle:starpx-tba-15y-sep-umbs-5-avgpx
  cycle:starpx-mbs-30y-gnma-le3_5-wavgpx
  cycle:starpx-agcmo-pi-fnma-post2016-wavgpx
  cycle:starpx-nag-ig-nagcmo-pi-avgpx
  cycle:starpx-cmbs-conduit-aaa-lcf-pi-pre2021-avgpx
  cycle:starpx-wcmbs-agcmbs-pi-2021-avgpx
  cycle:starpx-cboclo-aaa-2023-2026-avgpx
breakdown slices: cycle:starpx-{sheet}-{sub}-{block}-{dim}-{vol|ntrades}-{bd},
e.g.
  cycle:starpx-nag-ig-nonagency-cmo-pi-vol-custbuy
  cycle:starpx-nag-ig-nonagency-cmo-pi-ntrades-tick-le1mm
  cycle:starpx-cmbs-agcmbs-agency-cmbs-pi-vol-d2d

Zero/empty/suppressed ("*", "-", blank) cells are skipped, never stored:
a 0.0 price means "no data" in these tables, and TBA/specified volume is
unavailable by FINRA note (all zeros), so storing zeros would corrupt
charts. Tolerant of layout drift: block/dim/metric labels match
case-insensitively, unknown rows are ignored, missing sheets are skipped.

PXTABLES is a new dataset — only ~3 months of history are backfilled
(PX_BACKFILL_MONTHS), unlike STAR's 36.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from collector.fetchers.xlsx import read_sheet, to_float
from collector.store import Store

log = logging.getLogger(__name__)

PXTABLES_RE = re.compile(r"FINRA_IDS_PXTABLES-(\d{8})\.xlsx")
SOURCE = "finra-ids-star"
PX_BACKFILL_MONTHS = 3
# overall CBO/CDO/CLO average price — present on essentially every trading
# day; used as the "PX dataset populated" sentinel
SENTINEL = "cycle:starpx-cboclo-cbo-cdo-clo-avgpx"

SHEET_KEYS = {
    1: "tba", 2: "mbs", 3: "agcmo", 4: "nag",
    5: "cmbs", 6: "wcmbs", 7: "cboclo",
}

# normalized "PRICING TABLE:" title fragment -> sub-table key (longest first)
SUB_KEYS: dict[int, list[tuple[str, str]]] = {
    1: [("SINGLE FAMILY 15Y", "15y"), ("SINGLE FAMILY 30Y", "30y")],
    2: [("SINGLE FAMILY 15Y", "15y"), ("SINGLE FAMILY 30Y", "30y"),
        ("ARMS/HYBRIDS", "arms")],
    3: [("AGENCY CMO P&I", "pi"), ("AGENCY CMO IO/PO", "iopo")],
    4: [("NON AGENCY CMO (P&I)", "pi"), ("NON AGENCY CMO (IO/PO)", "iopo"),
        ("NON-AGENCY CMO | ABS", "")],
    5: [("AGENCY CMBS", "agcmbs"), ("CMBS CONDUIT", "conduit"),
        ("CMBS OTHER", "other")],
    6: [("CMBS CONDUIT AAA LCF (P&I)", "conduit-aaa-lcf"),
        ("AGENCY CMBS", "agcmbs"), ("CMBS CONDUIT", "conduit"),
        ("CMBS OTHER", "other")],
    7: [("CBO/CDO/CLO", "")],
}

METRICS = {
    "AVERAGE PRICE": "avgpx",
    "WEIGHTED AVG. PRICE": "wavgpx",
    "AVG. PRICE BOTTOM 5 TRADES": "bot5",
    "2ND QUARTILE PRICE": "q2",
    "3RD QUARTILE PRICE": "q3",
    "4TH QUARTILE PRICE": "q4",
    "AVG. PRICE TOP 5 TRADES": "top5",
    "STANDARD DEVIATION": "stdev",
    "VOLUME OF TRADES (000'S)": "vol",
    "NUMBER OF TRADES": "ntrades",
}

# col-B labels that decompose a metric row into counterparty / ticket-size
# slices. Each becomes its own series keyed
#   {sub}-{block}-{dim}-{vol|ntrades}-{suffix}
# (vol-section slices are par in $000s like the vol row — stored x1000;
# ntrades-section slices are trade counts). Verified live 2026-09-30:
# sheets 3-7 carry these rows; sheets 1/2 (TBA/specified) have none —
# FINRA notes transaction volume is unavailable there.
BREAKDOWN_SUFFIXES = {
    "CUSTOMER BUY": "custbuy",
    "CUSTOMER SELL": "custsell",
    "DEALER TO DEALER": "d2d",
    "<= $1MM": "tick-le1mm",
    "<= $10MM": "tick-le10mm",
    "<= $100MM": "tick-le100mm",
    "> $10MM": "tick-gt10mm",
    "> $100MM": "tick-gt100mm",
}
# col-B labels that are never blocks or metrics
_SKIP_PREFIXES = ("PRICING TABLE", "NOTE", "* INDICATES", "AS OF ",
                  "THESE REPORTS", "FOR ADDITIONAL", "FIELD ")
_BREAKDOWN = set(BREAKDOWN_SUFFIXES)
_HEADER_LABELS = {
    "ASSET SUB-CLASS / METRIC", "INVESTMENT GRADE / METRIC", "METRIC",
    "DEAL VINTAGE",
}
_WEEK_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec"]


def _num(raw: str) -> float:
    """Parse a PXTABLES cell: '*' (suppressed <5 trades), '-' (no data)
    and blanks -> 0.0 (callers skip zeros)."""
    if raw is None:
        return 0.0
    s = raw.strip()
    if s in ("", "*", "-"):
        return 0.0
    v = to_float(s)
    return v if v is not None else 0.0


def _slug(raw: str) -> str:
    """Lowercase URL-safe slug. '<= 3.5'->'le3_5', '> 6'->'gt6',
    'POST-2016'->'post2016', '2023-2026'->'2023-2026',
    'AGENCY CMBS (P&I)'->'agency-cmbs-pi'."""
    s = raw.strip().lower().replace("\u2264", "<=")
    s = s.replace("<=", "le").replace(">", "gt")
    s = re.sub(r"\ble\s+", "le", s)
    s = re.sub(r"\bgt\s+", "gt", s)
    s = re.sub(r"\bpre-", "pre", s)   # PRE-2009 -> pre2009
    s = re.sub(r"\bpost-", "post", s)  # POST-2016 -> post2016
    s = s.replace(".", "_")
    s = re.sub(r"[()$&†]", "", s)
    s = s.replace("/", "-")
    s = s.replace(" ", "-")
    s = re.sub(r"[^a-z0-9_-]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-_")


def _norm_label(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().upper())


def _sub_key(sheet: int, title: str) -> str:
    t = _norm_label(title).replace("PRICING TABLE:", "").strip()
    for frag, key in SUB_KEYS.get(sheet, []):
        if frag in t:
            return key
    return _slug(t)[:40]


def _settlement_abbr(label: str) -> str | None:
    """'September Settlement' -> 'sep'."""
    try:
        m = datetime.strptime(label.strip().split()[0], "%B").month
        return _MONTHS[m - 1]
    except (ValueError, IndexError):
        return None


def _looks_like_block(row: list[str], label: str) -> bool:
    """A block-header row: unrecognized col-B label + >=2 non-numeric
    dim labels in cols C+ (metric/breakdown rows hold numbers there)."""
    if not label:
        return False
    nl = _norm_label(label)
    if nl in METRICS or nl in _BREAKDOWN or nl in _HEADER_LABELS:
        return False
    if nl.startswith(_SKIP_PREFIXES) or nl.startswith("NON-INVESTMENT GRADE"):
        return False
    text = [c for c in (row[2:] if len(row) > 2 else [])
            if c.strip() and to_float(c) is None]
    return len(text) >= 2


def _single_level_dims(row: list[str]) -> list[tuple[int, str]]:
    """Dims from a block/grade header row: (col_index, slug) for each
    non-empty label in cols C+."""
    dims = []
    for i, c in enumerate(row[2:], start=2):
        if c.strip():
            dims.append((i, _slug(c)))
    return dims


def _two_level_dims(top: list[str], sub: list[str]) -> list[tuple[int, str]]:
    """Sheets 5/6/7 headers: product labels on one row, vintage labels on
    the next, with empty separator columns between product groups."""
    dims: list[tuple[int, str]] = []
    cur_top = ""
    n = max(len(top), len(sub))
    for i in range(2, n):
        t = top[i].strip() if i < len(top) else ""
        v = sub[i].strip() if i < len(sub) else ""
        if t:
            cur_top = t
        if not cur_top:
            continue
        if v:
            dims.append((i, f"{_slug(cur_top)}-{_slug(v)}"))
        elif t:
            dims.append((i, _slug(cur_top)))  # overall column, no vintage
        # empty separator columns are skipped
    return dims


def parse_pxtables(data: bytes) -> dict[str, dict[str, float]]:
    """Parse one daily PXTABLES workbook.

    Returns {sheet_key: {series_suffix: value}}; the caller stores
    cycle:starpx-{sheet_key}-{suffix}. Zero/empty/suppressed cells are
    skipped. Missing/unparseable sheets are skipped, never fatal.
    """
    out: dict[str, dict[str, float]] = {}
    for sheet, skey in SHEET_KEYS.items():
        try:
            rows = read_sheet(data, sheet=sheet)
        except ValueError:
            continue  # missing sheet in a synthetic/partial file
        series = _parse_sheet(sheet, rows)
        if series:
            out[skey] = series
    return out


def _parse_sheet(sheet: int, rows: dict[int, list[str]]) -> dict[str, float]:
    series: dict[str, float] = {}
    sub = ""
    block = ""
    dims: list[tuple[int, str]] = []
    # most recent metric row ("vol"/"ntrades") — scopes breakdown rows:
    # CUSTOMER BUY/SELL, DEALER TO DEALER and ticket-size buckets appear
    # twice per block (once after the vol row in $000s, once after the
    # ntrades row in counts). Reset whenever the block/dim context changes.
    last_metric: str | None = None

    def col_b(r: list[str]) -> str:
        return r[1].strip() if len(r) > 1 else ""

    for r in sorted(rows):
        row = rows[r]
        if not row:
            continue
        b = col_b(row)
        nl = _norm_label(b)
        if not b:
            continue
        if nl.startswith("PRICING TABLE"):
            sub = _sub_key(sheet, b)
            block, dims, last_metric = "", [], None
            continue
        if sheet == 1 and nl.endswith("SETTLEMENT"):
            # TBA sub-tables are per settlement month ("September
            # Settlement"); fold the month into the sub-table key
            abbr = _settlement_abbr(b)
            if abbr:
                sub = f"{sub}-{abbr}" if sub else abbr
            last_metric = None
            continue
        if nl.startswith("NON-INVESTMENT GRADE"):
            block = "nonig"
            d = _single_level_dims(row)
            if d:  # grade header may omit dim labels (sheet 4 reuses them)
                dims = d
            last_metric = None
            continue
        if nl == "INVESTMENT GRADE / METRIC":
            block = "ig"
            d = _single_level_dims(row)
            if d:
                dims = d
            last_metric = None
            continue
        if nl in ("ASSET SUB-CLASS / METRIC",):
            dims, block, last_metric = [], "", None  # real dims come on the block rows
            continue
        if nl == "METRIC":
            # two-level header (sheets 5/6/7): products on this row,
            # vintages on the next
            nxt = rows.get(r + 1, [])
            dims = _two_level_dims(row, nxt)
            block, last_metric = "", None
            continue
        metric = METRICS.get(nl)
        if metric is not None and dims:
            for i, dim_key in dims:
                if i >= len(row):
                    continue
                v = _num(row[i])
                if v == 0.0:
                    continue
                if metric == "vol":
                    v *= 1000.0  # VOLUME OF TRADES (000'S)
                suffix = "-".join(p for p in (sub, block, dim_key, metric)
                                  if p)
                series[suffix] = v
            last_metric = metric
            continue
        bd = BREAKDOWN_SUFFIXES.get(nl)
        if bd is not None and dims and last_metric in ("vol", "ntrades"):
            # counterparty / ticket-size slice of the preceding vol or
            # ntrades row: key {sub}-{block}-{dim}-{vol|ntrades}-{suffix}
            for i, dim_key in dims:
                if i >= len(row):
                    continue
                v = _num(row[i])
                if v == 0.0:
                    continue
                if last_metric == "vol":
                    v *= 1000.0  # slices are in $000s like the vol row
                suffix = "-".join(p for p in (sub, block, dim_key,
                                              last_metric, bd) if p)
                series[suffix] = v
            continue
        if _looks_like_block(row, b):
            block = _slug(b)
            dims = _single_level_dims(row)
            last_metric = None
            continue
        # footers, notes, unknown rows: ignored
    return series


def pxtables_asof(data: bytes, file_date: date) -> dict[str, date]:
    """Per-sheet as-of dates. Sheet 6 (weekly CMBS) covers a calendar
    week — its points are dated at the week end; everything else uses
    the file date."""
    dates = {skey: file_date for skey in SHEET_KEYS.values()}
    try:
        rows = read_sheet(data, sheet=6)
    except ValueError:
        return dates
    for r in sorted(rows):
        row = rows[r]
        if len(row) > 2 and _norm_label(row[1]) == "DATA AS OF:":
            m = _WEEK_RE.search(row[2])
            if m:
                try:
                    dates["wcmbs"] = date.fromisoformat(m.group(2))
                except ValueError:
                    pass
            break
    return dates


def iter_pxtables_files(zip_data: bytes):
    """Yield (date, xlsx_bytes) for each PXTABLES file in a monthly ZIP."""
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(zip_data))
    for name in z.namelist():
        m = PXTABLES_RE.fullmatch(name.rsplit("/", 1)[-1])
        if not m:
            continue
        ds = m.group(1)
        try:
            day = date(int(ds[0:4]), int(ds[4:6]), int(ds[6:8]))
        except ValueError:
            continue
        yield day, z.read(name)


def needs_backfill(store: Store) -> bool:
    """True when the PXTABLES series are missing/thin (new dataset)."""
    if store.doc("finra_ids_px") is None:
        return True
    pts = store.points(SENTINEL)
    return len(pts) < 20


def store_pxtables(store: Store, day: date, xlsx: bytes) -> int:
    """Parse one daily PXTABLES file and upsert its series. Returns the
    number of series points written."""
    agg = parse_pxtables(xlsx)
    if not agg:
        return 0
    dates = pxtables_asof(xlsx, day)
    n = 0
    for skey, series in agg.items():
        d = dates.get(skey, day)
        for suffix, v in series.items():
            store.upsert_points(f"cycle:starpx-{skey}-{suffix}", [(d, v)])
            n += 1
    return n
