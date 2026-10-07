"""SEC Form N-CEN — registered investment company census (quarterly batch).

Index: https://www.sec.gov/data-research/sec-markets-data/form-n-cen-data-sets
ZIP pattern (verified live 2026-10-05; 2026 Q2 batch = 8,404,110 bytes):
  https://www.sec.gov/files/dera/data/form-n-cen-data-sets/YYYYqN_ncen.zip
ZIP of ~40 TSVs (tab-separated UTF-8) + metadata; FUND_REPORTED_INFO.tsv is
108 columns, one row per series/fund filing (verified in headers 2026-10-05).

Key risk-relevant fields (column-driven parsing — no positional assumptions):
  type flags: IS_ETF, IS_INDEX, IS_MULTI_INVERSE_INDEX (leveraged/inverse),
    IS_INTERVAL, IS_MONEY_MARKET, IS_FUND_OF_FUND, IS_NON_DIVERSIFIED
  vol: STDV_B4_FEES_AND_EXPENSES / STDV_AFTR_FEES_AND_EXPENSES (annualized)
  size: MONTHLY_AVG_NET_ASSETS, DAILY_AVG_NET_ASSETS, NAV_PER_SHARE
  sec lending: DID_LEND_SECURITIES, IS_COLLATERAL_LIQUIDATED
    (AVG_VALUE_SEC_LOAN lives in SECURITY_LENDING.tsv)
  borrowing: HAS_LINE_OF_CREDIT (LINE_OF_CREDIT_DETAIL.tsv has LINE_OF_CREDIT_SIZE)

CAVEAT: SEC publishes *receipt* batches quarterly (filings received that
quarter), not a clean per-quarter census — each fund files annually on its
own fiscal year-end. Points are dated at the batch quarter-end and the doc
carries the batch label, so the dashboard never misreads it as a timer series.

SEC fair-access rules: declared contact UA (SEC 403s generic UAs), <=1 req/2s.
One bad quarter never fails the job — the probe window just moves on.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import zipfile
from datetime import date

from collector.dates import quarter_end
from collector.config import SecDataCfg
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

ZIP_URL = ("https://www.sec.gov/files/dera/data/form-n-cen-data-sets/"
           "{year}q{q}_ncen.zip")
SOURCE = "sec-ncen"

# seconds between SEC requests; the fair-access floor is 1 req / 2s.
REQUEST_GAP = 2.0
# how many past quarters to probe for the latest published batch
PROBE_QUARTERS = 4

TRUTHY = {"y", "yes", "true", "1"}


def _is_true(raw: str | None) -> bool:
    return (raw or "").strip().lower() in TRUTHY


def _to_float(raw: str | None) -> float | None:
    try:
        return float((raw or "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None





def probe_batches(today: date | None = None) -> list[tuple[int, int]]:
    """Candidate (year, quarter) batches, newest first."""
    today = today or date.today()
    q = (today.month - 1) // 3 + 1
    out = []
    y, qq = today.year, q
    for _ in range(PROBE_QUARTERS):
        out.append((y, qq))
        qq -= 1
        if qq == 0:
            qq, y = 4, y - 1
    return out


def parse_ncen_zip(data: bytes) -> dict:
    """Aggregate one N-CEN receipt batch. Returns the aggregate dict
    (counts, AUM sums, participation rates) — quarter dating is done by
    the caller from the winning batch label."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip file: {exc}") from exc
    info_name = next(
        (n for n in z.namelist()
         if n.upper() == "FUND_REPORTED_INFO.TSV"),
        None,
    )
    if info_name is None:
        raise ValueError(f"no FUND_REPORTED_INFO.tsv in zip: {z.namelist()[:6]}")
    with z.open(info_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8",
                                                  errors="replace"),
                                delimiter="\t")
        rows = list(reader)
    if not rows:
        raise ValueError("FUND_REPORTED_INFO.tsv has no data rows")

    agg = {"fund_count": 0, "aum": 0.0, "etf_count": 0, "etf_aum": 0.0,
           "levinv_count": 0, "levinv_aum": 0.0, "mm_count": 0, "mm_aum": 0.0,
           "stdv_sum": 0.0, "stdv_n": 0, "seclend_n": 0, "loc_n": 0}
    for r in rows:
        agg["fund_count"] += 1
        aum = _to_float(r.get("MONTHLY_AVG_NET_ASSETS"))
        if aum is None:
            aum = _to_float(r.get("DAILY_AVG_NET_ASSETS"))
        aum = aum or 0.0
        agg["aum"] += aum
        if _is_true(r.get("IS_ETF")):
            agg["etf_count"] += 1
            agg["etf_aum"] += aum
        if _is_true(r.get("IS_MULTI_INVERSE_INDEX")):
            agg["levinv_count"] += 1
            agg["levinv_aum"] += aum
        if _is_true(r.get("IS_MONEY_MARKET")):
            agg["mm_count"] += 1
            agg["mm_aum"] += aum
        stdv = _to_float(r.get("STDV_B4_FEES_AND_EXPENSES"))
        if stdv is None:
            stdv = _to_float(r.get("STDV_AFTR_FEES_AND_EXPENSES"))
        if stdv is not None:
            agg["stdv_sum"] += stdv
            agg["stdv_n"] += 1
        if _is_true(r.get("DID_LEND_SECURITIES")):
            agg["seclend_n"] += 1
        if _is_true(r.get("HAS_LINE_OF_CREDIT")):
            agg["loc_n"] += 1
    n = max(agg["fund_count"], 1)
    return {
        "ncen-fund-count": agg["fund_count"],
        "ncen-aum": agg["aum"],
        "ncen-etf-count": agg["etf_count"],
        "ncen-etf-aum": agg["etf_aum"],
        "ncen-levinv-count": agg["levinv_count"],
        "ncen-levinv-aum": agg["levinv_aum"],
        "ncen-mm-count": agg["mm_count"],
        "ncen-mm-aum": agg["mm_aum"],
        "ncen-stdv-avg": agg["stdv_sum"] / agg["stdv_n"] if agg["stdv_n"] else None,
        "ncen-seclend-pct": 100.0 * agg["seclend_n"] / n,
        "ncen-loc-pct": 100.0 * agg["loc_n"] / n,
    }


async def fetch_sec_ncen(cfg: SecDataCfg, store: Store, get_bytes: GetBytes,
                         today: date | None = None) -> str:
    """Monthly poll: find the latest published N-CEN receipt batch, aggregate
    it, store quarterly points dated at the batch quarter-end."""
    headers = {"User-Agent": cfg.user_agent}
    errors: list[str] = []
    for year, q in probe_batches(today):
        url = ZIP_URL.format(year=year, q=q)
        try:
            data = await get_bytes(url, headers=headers)
            if len(data) < 100000:
                raise ValueError(f"suspiciously small ({len(data)}B)")
            agg = parse_ncen_zip(data)
            asof = quarter_end(year, q)
            for key, val in agg.items():
                if val is not None:
                    store.upsert_points(f"cycle:{key}", [(asof, float(val))])
            store.put_doc("sec_ncen", {
                "batch": f"{year}q{q}",
                "as_of": asof.isoformat(),
                "note": ("SEC receipt batch (filings received that quarter), not a "
                         "clean per-quarter census; each fund files annually on its "
                         "own fiscal year-end."),
                **{k: v for k, v in agg.items() if v is not None},
            }, source=SOURCE)
            return SOURCE
        except Exception as exc:  # noqa: BLE001 — probe next older quarter
            errors.append(f"{year}q{q}: {exc}")
            await asyncio.sleep(REQUEST_GAP)
    raise RuntimeError("no N-CEN batch in probe window: " + "; ".join(errors[:4]))
