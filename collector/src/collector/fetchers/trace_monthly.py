"""FINRA TRACE Monthly Volume Report — corporate/agency/securitized (keyless).

Free monthly XLSX on the FINRA CDN, no auth, history back to Jan 2017:

  https://cdn.finra.org/trace/volume/monthly/
    TRACE_Public_Monthly_Report_YYYY-MM.xlsx

Published the 3rd business day after month-end. Layout (verified live
2026-10-03): row 1 "TRACE Report For <Month>", row 2 headers
[Trades: ATS, Interdealer, Customer, Total | Par Value ($M): ATS,
Interdealer, Customer, Total]; product rows CORP, CONV, CHRC, ELN, AGCY,
ABS, ABSX, CMO, MBS, TBA.

Stored as cycle:<id>: par in $M, trades in counts, plus the derived
customer share of corporate par (dealer-to-customer / total).
"""
from __future__ import annotations

import calendar
import logging
import asyncio
from datetime import date

from collector.fetchers.xlsx import find_row, read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

URL = ("https://cdn.finra.org/trace/volume/monthly/"
       "TRACE_Public_Monthly_Report_{}.xlsx")
SOURCE = "finra-trace-monthly"

# seconds between HTTP requests; this CDN path 403s rapid bursts — 5s gaps
# verified necessary live 2026-10-03. Backfill is ~10 min on first run.
REQUEST_GAP = 5.0

# store key -> (product label, value column: 4=total trades, 8=total par $M)
# Covers every product row in the report (incl. the small CONV/CHRC/ELN
# rows the batch-6 build skipped); trace-corp-cust-share is derived below.
SERIES = {
    "trace-corp-par": ("CORP", 8),
    "trace-corp-trades": ("CORP", 4),
    "trace-conv-par": ("CONV", 8),
    "trace-conv-trades": ("CONV", 4),
    "trace-chrc-par": ("CHRC", 8),
    "trace-chrc-trades": ("CHRC", 4),
    "trace-eln-par": ("ELN", 8),
    "trace-eln-trades": ("ELN", 4),
    "trace-agcy-par": ("AGCY", 8),
    "trace-abs-par": ("ABS", 8),
    "trace-absx-par": ("ABSX", 8),
    "trace-cmo-par": ("CMO", 8),
    "trace-mbs-par": ("MBS", 8),
    "trace-tba-par": ("TBA", 8),
}
DERIVED_SERIES = ("trace-corp-cust-share",)  # customer par / total par (CORP)


def parse_workbook(data: bytes, year: int, month: int) -> dict[str, float]:
    rows = read_sheet(data)
    title = (rows.get(1) or [""])[0]
    if "TRACE Report For" not in title:
        raise ValueError(f"unexpected title row: {title!r}")
    out: dict[str, float] = {}
    for key, (label, col) in SERIES.items():
        row = find_row(rows, label)
        if row is None or len(row) <= col:
            raise ValueError(f"product row {label!r} missing")
        val = to_float(row[col])
        if val is None:
            raise ValueError(f"non-numeric value for {label!r}: {row[col]!r}")
        out[key] = val
    # customer share of corporate par: dealer-to-customer vs total (col 7/8)
    corp = find_row(rows, "CORP")
    cust, total = to_float(corp[7]), to_float(corp[8])
    if cust is None or not total:
        raise ValueError("cannot derive trace-corp-cust-share")
    out["trace-corp-cust-share"] = cust / total
    # month-end as-of date
    last = calendar.monthrange(year, month)[1]
    out["_asof"] = date(year, month, last)
    return out


async def _get_with_backoff(url: str, get_bytes: GetBytes,
                            delays: tuple[float, ...] = (60.0, 300.0, 900.0)) -> bytes:
    """GET with backoff on 403/429. The FINRA CDN 403s this path when it
    rate-limits us (and also 403s instead of 404ing for unpublished months,
    which callers handle by age). Other errors raise immediately."""
    for delay in delays:
        try:
            return await _get(url, get_bytes)
        except RuntimeError as exc:
            if "403" not in str(exc) and "429" not in str(exc):
                raise
            log.info("trace_monthly blocked (%s), retrying in %.0fs: %s",
                     exc, delay, url)
            await asyncio.sleep(delay)
    return await _get(url, get_bytes)  # final attempt; raises if still blocked


async def _get(url: str, get_bytes: GetBytes) -> bytes:
    data = await get_bytes(url)
    if len(data) < 1000:
        raise ValueError(f"suspiciously small ({len(data)}B)")
    return data


async def fetch_trace_monthly(store: Store, get_bytes: GetBytes,
                              today: date | None = None) -> str:
    """Daily job: latest published report (probe back 3 months); full
    history backfill to 2017-01 on an empty store. No-op when up to date.
    While the FINRA CDN is blocking us (flagged by the previous run), probe
    with fail-fast only — no long backoffs — so recovery is picked up
    within a day."""
    today = today or date.today()
    prev = store.doc("trace_monthly")
    blocked = bool(prev and isinstance(prev.payload, dict)
                   and prev.payload.get("blocked"))
    y, m = today.year, today.month
    got: tuple[int, int] | None = None
    errors: list[str] = []
    for _ in range(4):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        url = URL.format(f"{y}-{m:02d}")
        # Months <2mo old may simply be unpublished (the CDN 403s instead of
        # 404ing) — fail fast. Older months must exist: a 403 there means we
        # are blocked, so back off hard before concluding anything — unless
        # we already know we're blocked, in which case fail fast and just
        # poll for recovery.
        age = (today.year - y) * 12 + (today.month - m)
        try:
            data = (await _get(url, get_bytes) if age < 2 or blocked
                    else await _get_with_backoff(url, get_bytes))
            vals = parse_workbook(data, y, m)
            asof = vals.pop("_asof")
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            got = (y, m)
            break
        except Exception as exc:  # noqa: BLE001 — not yet published / blocked
            errors.append(f"{y}-{m:02d}: {exc}")
            await asyncio.sleep(REQUEST_GAP)  # polite: the CDN 403s rapid bursts
    if got is None:
        # Flag the block so the next daily run polls cheaply (fail-fast)
        # instead of burning long backoffs. Cleared automatically on success.
        payload: dict = {}
        if prev and isinstance(prev.payload, dict):
            payload = {k: v for k, v in prev.payload.items()
                       if k in ("as_of", "series")}
        payload["blocked"] = True
        payload["block_error"] = "; ".join(errors[:2])
        store.put_doc("trace_monthly", payload, source=SOURCE)
        raise RuntimeError("no TRACE monthly report in last 4 months: "
                           + "; ".join(errors[:2]))
    if len(store.points("cycle:trace-corp-par")) < 12:
        backfilled = 0
        by, bm = got
        while (by, bm) > (2017, 1):
            bm -= 1
            if bm == 0:
                bm, by = 12, by - 1
            url = URL.format(f"{by}-{bm:02d}")
            try:
                # Probe already proved we are not blocked; failures here are
                # genuine gaps — skip fast, no backoff.
                data = await _get(url, get_bytes)
                vals = parse_workbook(data, by, bm)
            except Exception as exc:  # noqa: BLE001 — tolerate gaps
                log.debug("trace_monthly %s skipped: %s", url, exc)
                await asyncio.sleep(REQUEST_GAP)
                continue
            asof = vals.pop("_asof")
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            backfilled += 1
            await asyncio.sleep(REQUEST_GAP)
        log.info("trace_monthly backfilled %d months", backfilled)
    store.put_doc("trace_monthly", {"as_of": f"{got[0]}-{got[1]:02d}",
                                      "series": sorted(SERIES) + sorted(DERIVED_SERIES)},
                  source=SOURCE)
    return SOURCE
