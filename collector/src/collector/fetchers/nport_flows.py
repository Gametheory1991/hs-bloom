"""SEC Form N-PORT derived fund flows  -  per-fund MONTHLY net flows + quarterly TNA (keyless).

Batch ZIP (verified live 2026-10-05):
  https://www.sec.gov/files/dera/data/form-n-port-data-sets/YYYYqN_nport.zip
Public data = only the 3rd month of each fund's fiscal quarter, published
~60 days after quarter-end. Funds file monthly; the SEC publishes the other
two months per fund only in non-public filings.

What the public dataset gives per fund per public report (FUND_REPORTED_INFO.tsv):
  * NET_ASSETS (as of the report date  -  QUARTERLY per fund, not monthly)
  * SALES_FLOW_MON{1,2,3}, REINVESTMENT_FLOW_MON{1,2,3}, REDEMPTION_FLOW_MON{1,2,3}
     -  the fund's own reported monthly flows for the 3 months of the reporting
    period ending at REPORT_DATE (from SUBMISSION.tsv).
  * MONTHLY_TOTAL_RETURN{1,2,3} exists per share CLASS (MONTHLY_TOTAL_RETURN.tsv),
    but class-level returns cannot be aggregated to fund level without per-class
    TNA weights, which the public dataset does not carry.

Primary series (monthly per fund): the directly REPORTED monthly net flow,
  net_flow_mon = SALES_FLOW_MON + REINVESTMENT_FLOW_MON - REDEMPTION_FLOW_MON.
This is actual reported data, not an estimate.

The standard flow identity is implemented as derive_net_flow() and documented
for the quarterly case, because the public dataset does not carry monthly TNA:
  flow_t = TNA_t - TNA_{t-1} x (1 + r_t)
With quarterly TNA (NET_ASSETS) and a fund-level quarterly return r_q this
derives a quarterly cross-check flow; it cannot be applied month-to-month from
public N-PORT because TNA is reported only as of the quarter's 3rd month.

Stored series:
  cycle:nport-flow-<SERIES_ID>   monthly net flow, USD (reported)
  cycle:nport-flow-all           monthly aggregate net flow across all filers, USD
  cycle:nport-tna-<SERIES_ID>    quarterly net assets, USD (as of report date)
  cycle:nport-tna-all            quarterly aggregate net assets, USD

COVERAGE (labelled honestly): mutual funds AND ETFs that file Form N-PORT
(~13.6k funds in the 2026 Q2 batch). ~60-day filing lag. Monthly flow months
are each fund's fiscal-quarter months; the aggregate mixes fiscal calendars.
SEC fair-access rules: declared contact UA, <=1 req/2s.
"""
from __future__ import annotations

import asyncio
import calendar
import csv
import io
import logging
import zipfile
from datetime import date, datetime

from collector.config import SecDataCfg
from collector.dates import quarter_end
from collector.fetchers import nport_cache
from collector.fetchers.sec_ncen import REQUEST_GAP, probe_batches
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "sec-nport-flows"

SUBMISSION_TSV = "SUBMISSION.TSV"
INFO_TSV = "FUND_REPORTED_INFO.TSV"

FLOW_COLS = ("SALES_FLOW", "REINVESTMENT_FLOW", "REDEMPTION_FLOW")


def _to_float(raw: str | None) -> float | None:
    try:
        return float((raw or "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def derive_net_flow(tna_prev: float, tna_curr: float, total_return: float) -> float:
    """The standard flow identity: flow_t = TNA_t - TNA_{t-1} x (1 + r_t).

    Pure function, unit-tested. Applied at the frequency TNA is observed  - 
    quarterly for public N-PORT (NET_ASSETS is as of the report date only),
    never monthly, because monthly TNA is not in the public dataset.
    """
    return tna_curr - tna_prev * (1.0 + total_return)


def reported_monthly_net_flow(row: dict) -> dict[int, float | None]:
    """Per-month net flow from a FUND_REPORTED_INFO row.

    Returns {1: mon1, 2: mon2, 3: mon3} where each is
    SALES + REINVESTMENT - REDEMPTION, or None when the fund reported no
    flow components for that month.
    """
    out: dict[int, float | None] = {}
    for m in (1, 2, 3):
        parts = [_to_float(row.get(f"{c}_MON{m}")) for c in FLOW_COLS]
        if all(p is None for p in parts):
            out[m] = None
        else:
            sales, reinvest, redemp = (p or 0.0 for p in parts)
            out[m] = sales + reinvest - redemp
    return out


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_month(d: date, delta: int) -> tuple[int, int]:
    m = d.month - 1 + delta
    return d.year + m // 12, m % 12 + 1


def report_month_dates(report_date: date) -> list[date]:
    """The 3 calendar months of a reporting period ending at report_date.

    MON3 = the report month, MON1 = two months earlier; each dated at
    month-end so the points sort and join cleanly with other monthly series.
    """
    return [_month_end(*_shift_month(report_date, k)) for k in (-2, -1, 0)]


def _parse_report_date(raw: str | None) -> date | None:
    """SUBMISSION REPORT_DATE format is DD-MON-YYYY, e.g. 28-FEB-2026."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip().upper(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _stream_rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8",
                                                  errors="replace"),
                                delimiter="\t")
        for row in reader:
            yield row


def parse_nport_flows_zip(path: str) -> dict:
    """Per-fund monthly flows + quarterly TNA from an N-PORT batch zip.

    Amendments (NPORT-P/A or re-filings) are deduped: the latest FILING_DATE
    per (SERIES_ID, REPORT_DATE) wins. Returns per-fund rows and the monthly
    aggregate, ready for upsert.
    """
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip file: {exc}") from exc
    by_upper = {n.upper(): n for n in z.namelist()}
    if SUBMISSION_TSV not in by_upper:
        raise ValueError(f"no {SUBMISSION_TSV} in zip")
    if INFO_TSV not in by_upper:
        raise ValueError(f"no {INFO_TSV} in zip")

    # accession -> (report_date, filing_date, is_amendment)
    filings: dict[str, tuple[date, date, bool]] = {}
    for row in _stream_rows(z, by_upper[SUBMISSION_TSV]):
        acc = (row.get("ACCESSION_NUMBER") or "").strip()
        rdate = _parse_report_date(row.get("REPORT_DATE"))
        if not acc or rdate is None:
            continue
        try:
            fdate = datetime.strptime(
                (row.get("FILING_DATE") or "").strip().upper(), "%d-%b-%Y").date()
        except ValueError:
            fdate = date.min
        amend = "/A" in (row.get("SUB_TYPE") or "")
        prev = filings.get(acc)
        if prev is None or (fdate, amend) > (prev[1], prev[2]):
            filings[acc] = (rdate, fdate, amend)

    # (series_id, report_date) -> best row (latest filing wins)
    best: dict[tuple[str, date], tuple[date, bool, dict]] = {}
    skipped = 0
    for row in _stream_rows(z, by_upper[INFO_TSV]):
        acc = (row.get("ACCESSION_NUMBER") or "").strip()
        series = (row.get("SERIES_ID") or "").strip()
        filing = filings.get(acc)
        if not series or filing is None:
            skipped += 1
            continue
        rdate = filing[0]
        key = (series, rdate)
        cand = (filing[1], filing[2], row)
        if key not in best or (cand[0], cand[1]) > (best[key][0], best[key][1]):
            best[key] = cand

    funds: list[dict] = []
    agg: dict[date, float] = {}
    agg_tna: dict[date, float] = {}
    for (series, rdate), (_, _, row) in best.items():
        name = (row.get("SERIES_NAME") or "").strip()
        months = report_month_dates(rdate)
        flows = reported_monthly_net_flow(row)
        tna = _to_float(row.get("NET_ASSETS"))
        points: list[tuple[date, float]] = []
        for m, mdate in zip((1, 2, 3), months):
            v = flows[m]
            if v is None:
                continue
            points.append((mdate, v))
            agg[mdate] = agg.get(mdate, 0.0) + v
        if tna is not None:
            agg_tna[months[2]] = agg_tna.get(months[2], 0.0) + tna
        if points or tna is not None:
            funds.append({
                "series_id": series,
                "name": name,
                "report_date": rdate.isoformat(),
                "flows": [(d.isoformat(), v) for d, v in points],
                "tna": (months[2].isoformat(), tna) if tna is not None else None,
            })
    if not funds:
        raise ValueError("no fund rows parsed from N-PORT batch")
    return {
        "funds": funds,
        "n_funds": len(funds),
        "skipped_rows": skipped,
        "agg_flows": sorted(agg.items()),
        "agg_tna": sorted(agg_tna.items()),
    }


async def fetch_nport_flows(
    cfg: SecDataCfg, store: Store, today: date | None = None,
    max_batches: int = 1,
) -> str:
    """Monthly poll: latest N-PORT batch -> per-fund monthly flows + TNA.

    max_batches > 1 also walks older quarters (manual backfill; each batch is
    ~420 MB). Upserts are idempotent, so re-runs never duplicate history.
    """
    headers = {"User-Agent": cfg.user_agent}
    errors: list[str] = []
    done = 0
    last_batch = last_asof = None
    n_funds = 0
    for year, q in probe_batches(today):
        if done >= max_batches:
            break
        try:
            # Shared download: sec_nport.py caches this same ~420 MB zip;
            # this call reuses it instead of downloading it a second time.
            zip_path = await nport_cache.nport_zip_path(year, q, headers)
            parsed = parse_nport_flows_zip(zip_path)
            asof = quarter_end(year, q)
            items: list[tuple[str, date, float]] = []
            for f in parsed["funds"]:
                sid = f["series_id"]
                for diso, v in f["flows"]:
                    items.append((f"cycle:nport-flow-{sid}",
                                  date.fromisoformat(diso), float(v)))
                if f["tna"] is not None:
                    tiso, tna = f["tna"]
                    items.append((f"cycle:nport-tna-{sid}",
                                  date.fromisoformat(tiso), float(tna)))
            for d, v in parsed["agg_flows"]:
                items.append(("cycle:nport-flow-all", d, float(v)))
            for d, v in parsed["agg_tna"]:
                items.append(("cycle:nport-tna-all", d, float(v)))
            store.upsert_points_batch(items)
            last_batch, last_asof = f"{year}q{q}", asof
            n_funds += parsed["n_funds"]
            done += 1
            log.info("nport-flows: %s: %d funds, %d points",
                     last_batch, parsed["n_funds"], len(items))
        except Exception as exc:  # noqa: BLE001  -  probe next older quarter
            errors.append(f"{year}q{q}: {exc}")
            await asyncio.sleep(REQUEST_GAP)
    if done == 0:
        raise RuntimeError("no N-PORT batch in probe window: " + "; ".join(errors[:4]))
    store.put_doc("sec_nport_flows", {
        "batch": last_batch,
        "as_of": last_asof.isoformat() if last_asof else None,
        "funds_in_batch": n_funds,
        "note": ("Per-fund MONTHLY net flows, directly REPORTED by funds "
                 "(sales + reinvestment - redemption for each month of the "
                 "reporting period), not estimated. QUARTERLY net assets per "
                 "fund (NET_ASSETS as of the report date). The flow identity "
                 "flow_t = TNA_t - TNA_{t-1} x (1 + r_t) applies "
                 "quarter-to-quarter only: monthly TNA is not in the public "
                 "dataset, so the literal monthly derivation is not possible "
                 "from public N-PORT. "
                 "COVERAGE: mutual funds AND ETFs that file Form N-PORT. "
                 "Only the 3rd month of each fund's fiscal quarter is public, "
                 "~60 days after quarter-end. The monthly aggregate mixes "
                 "funds on different fiscal calendars."),
        "unit": "USD (flows and TNA), positive flow = net inflow",
    }, source=SOURCE)
    return SOURCE
