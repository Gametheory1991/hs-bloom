"""NY Fed Primary Dealer Statistics — weekly dealer positions & transactions.

FR2004 "Weekly Release of Primary Dealer Positions, Transactions, and
Financing" (FRBNY Markets Group). Primary dealers report weekly on
trading activity, cash positions, and financing in Treasury and other
securities. The Bank does not audit the data.

Free, keyless JSON/CSV API (verified live 2026-10-09):
  Bulk (full history 1998-01-28 -> present, ~27MB):
    GET https://markets.newyorkfed.org/api/pd/get/all/timeseries.csv
      -> "As Of Date","Time Series","Value (millions)"
  Incremental (one week, ~49KB):
    GET https://markets.newyorkfed.org/api/pd/list/asof.json
      -> {"pd": {"asofdates": [{"asof": "2026-09-30", ...}]}}
    GET https://markets.newyorkfed.org/api/pd/get/asof/{yyyy-MM-dd}.csv
  Series catalog (1539 series, keyid -> description):
    GET https://markets.newyorkfed.org/api/pd/list/timeseries.json
API spec: https://markets.newyorkfed.org/static/docs/markets-api.html

Series codes (SBN2024 vintage; SBP2001 pre-2022 used different codes —
the bulk CSV carries both, we take rows as they come):
  Positions (net long-minus-short, $M):
    PDPOSGST-TOT  Treasury (ex-TIPS)      -> cycle:pd-pos-ust
    PDPOSCS-TOT   Corporate securities    -> cycle:pd-pos-corp
    PDPOSMBS-TOT  Agency/GSE MBS          -> cycle:pd-pos-mbs
    PDPOSFGS-TOT  Agency ex-MBS           -> cycle:pd-pos-agency
    PDPOSABS-TOT  Asset-backed            -> cycle:pd-pos-abs
    PDPOSSMGO-TOT State & municipal       -> cycle:pd-pos-muni
  Transactions (weekly volume, $M):
    PDGSWOEXTTOT  Treasury ex-TIPS        -> cycle:pd-txn-ust
    PDTIPSTOT     TIPS                    -> cycle:pd-txn-tips
    PDMBSTOT      MBS                     -> cycle:pd-txn-mbs
    PDCSTOT       Corporate               -> cycle:pd-txn-corp
    PDABTOT       ABS                     -> cycle:pd-txn-abs
    PDFGSXMTOT    Agency ex-MBS           -> cycle:pd-txn-agency
    PDMGOTOT      Municipals              -> cycle:pd-txn-muni

Note: no TIPS *positions* total exists in the release (TIPS positions
are not separately broken out); TIPS *transactions* do exist.

Why this matters for hs-bloom: TRACE shows what traded; the dealer
survey shows what dealers are *positioned* (net long/short by product).
Rising net-short Treasury positions + heavy bill supply = the classic
dealer-balance-sheet-constraint setup. Complements trace_monthly /
trace_treasury (transaction-based) with a position-based view.

Storage:
  cycle:pd-pos-{ust,corp,mbs,agency,abs,muni}   weekly, $M, net
  cycle:pd-txn-{ust,tips,mbs,corp,abs,agency,muni}  weekly, $M volume
  doc nyfed_pd            latest-week snapshot {asof, positions{}, transactions{}}
  doc nyfed_pd_state      {last_asof} resume watermark (incremental mode)

Cadence: weekly (release is Thursdays). First run does the bulk pull
(full history); subsequent runs fetch only new asof weeks.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "nyfed-pd"
API = "https://markets.newyorkfed.org/api/pd"
BULK_CSV = f"{API}/get/all/timeseries.csv"
ASOF_LIST = f"{API}/list/asof.json"
WEEK_CSV = API + "/get/asof/{d}.csv"

# FRBNY series keyid -> (our series id, label)
POSITIONS = {
    "PDPOSGST-TOT": ("pd-pos-ust", "Treasury ex-TIPS net position"),
    "PDPOSCS-TOT": ("pd-pos-corp", "Corporate securities net position"),
    "PDPOSMBS-TOT": ("pd-pos-mbs", "Agency/GSE MBS net position"),
    "PDPOSFGS-TOT": ("pd-pos-agency", "Agency ex-MBS net position"),
    "PDPOSABS-TOT": ("pd-pos-abs", "Asset-backed net position"),
    "PDPOSSMGO-TOT": ("pd-pos-muni", "State & municipal net position"),
}
TRANSACTIONS = {
    "PDGSWOEXTTOT": ("pd-txn-ust", "Treasury ex-TIPS weekly transactions"),
    "PDTIPSTOT": ("pd-txn-tips", "TIPS weekly transactions"),
    "PDMBSTOT": ("pd-txn-mbs", "MBS weekly transactions"),
    "PDCSTOT": ("pd-txn-corp", "Corporate weekly transactions"),
    "PDABTOT": ("pd-txn-abs", "ABS weekly transactions"),
    "PDFGSXMTOT": ("pd-txn-agency", "Agency ex-MBS weekly transactions"),
    "PDMGOTOT": ("pd-txn-muni", "Municipal weekly transactions"),
}
WANTED = {**POSITIONS, **TRANSACTIONS}


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s in ("", ".", "*", "NA", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    try:
        return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, IndexError):
        return None


def parse_pd_csv(text: str) -> dict[str, list[tuple[date, float]]]:
    """Parse an FRBNY PD CSV ("As Of Date","Time Series","Value (millions)").

    Returns {our_series_id: [(date, value_mn), ...]} for the curated set.
    Unknown/extra series are ignored; unparseable rows are skipped.
    """
    out: dict[str, list[tuple[date, float]]] = {sid: [] for sid, _ in WANTED.values()}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        keyid = (row.get("Time Series") or "").strip()
        if keyid not in WANTED:
            continue
        d = _parse_date(row.get("As Of Date"))
        v = _num(row.get("Value (millions)"))
        if d is None or v is None:
            continue
        sid, _label = WANTED[keyid]
        out[sid].append((d, v))
    for pts in out.values():
        pts.sort(key=lambda p: p[0])
    return out


def parse_asof_list(text: str) -> list[str]:
    """Extract sorted asof date strings from /api/pd/list/asof.json."""
    payload = json.loads(text)
    recs = payload.get("pd", {}).get("asofdates", [])
    dates = sorted({r.get("asof") for r in recs if r.get("asof")})
    return dates


async def fetch_nyfed_primary_dealers(store: Store, get_text: GetText) -> str:
    """Weekly job: bulk backfill on first run, then incremental weeks."""
    state_doc = store.doc("nyfed_pd_state")
    last_asof = (state_doc.payload or {}).get("last_asof") if state_doc else None

    asof_dates = parse_asof_list(await get_text(ASOF_LIST))
    if not asof_dates:
        return "nyfed-pd: no asof dates returned"

    new_weeks = [d for d in asof_dates if not last_asof or d > last_asof]
    series: dict[str, list[tuple[date, float]]] = {sid: [] for sid, _ in WANTED.values()}

    if not last_asof:
        # First run: bulk CSV carries full history (1998 -> present).
        log.info("nyfed-pd: first run, bulk backfill")
        bulk = parse_pd_csv(await get_text(BULK_CSV))
        for sid, pts in bulk.items():
            series[sid].extend(pts)
        new_weeks = []  # bulk already covers everything
    else:
        for d in new_weeks:
            week = parse_pd_csv(await get_text(WEEK_CSV.format(d=d)))
            for sid, pts in week.items():
                series[sid].extend(pts)

    total_pts = 0
    for sid, pts in series.items():
        if pts:
            store.upsert_points(f"cycle:{sid}", pts)
            total_pts += len(pts)

    latest = asof_dates[-1]
    # Latest-week snapshot doc for dashboards.
    snap = {"asof": latest, "positions": {}, "transactions": {}}
    for keyid, (sid, label) in POSITIONS.items():
        pts = series[sid]
        if pts:
            snap["positions"][sid] = {"label": label, "value_mn": pts[-1][1]}
    for keyid, (sid, label) in TRANSACTIONS.items():
        pts = series[sid]
        if pts:
            snap["transactions"][sid] = {"label": label, "value_mn": pts[-1][1]}
    store.put_doc("nyfed_pd", snap, SOURCE)
    store.put_doc("nyfed_pd_state", {"last_asof": latest}, SOURCE)

    return (f"nyfed-pd: {total_pts} points across {sum(1 for p in series.values() if p)} "
            f"series, asof {latest}")
