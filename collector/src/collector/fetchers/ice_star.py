"""ICE Data Services Vantage — daily STAR aggregate for fixed income (keyless).

The Vantage aggregate page exposes a daily ZIP with no auth:

  https://vantage.interactivedata.com/aggregate/download
    ?date=YYYY-MM-DD&vdl=aggregate_download

The ZIP contains FINRA_IDS_STAR-YYYYMMDD.xlsx (Structured Trading
Activity Report: agency pass-thru/CMO by issuer + non-agency/ABS/CLO by
investment grade; trade counts and $ trades in 000s) and
FINRA_IDS_PXTABLES-YYYYMMDD.xlsx (pricing tables — all-zero on rolled
contracts; not parsed, documented as available).

STAR layout (verified live 2026-10-03 for 2023-09-18, 2024-06-12,
2025-01-15): row 7 "DATA AS OF:" + Excel serial date; section A
(agency, UMBS/FNMA/FHLMC/GNMA/...) data rows identified by a numeric or
"*" value in column B, $ columns at every 3rd column starting at D
(index 3); section B (non-agency etc.) after the "INVESTMENT GRADE"
header row, $ columns at indices 3 and 6. "*" = suppressed (<5 trades)
treated as 0.

Stored as cycle:<id>: par converted to $bn, trades as counts.
"""
from __future__ import annotations

import io
import logging
import zipfile
import asyncio
from datetime import date, timedelta

from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

URL = ("https://vantage.interactivedata.com/aggregate/download"
       "?date={}&vdl=aggregate_download")
SOURCE = "ice-vantage-star"

# seconds between HTTP requests; the CDNs 403 rapid bursts.
REQUEST_GAP = 1.0

# Excel serial epoch (1899-12-30); STAR "DATA AS OF" is a serial number.
_EPOCH = date(1899, 12, 30)

SERIES = ("ice-star-agency-par", "ice-star-agency-trades",
          "ice-star-nonagency-par", "ice-star-nonagency-trades")


def _is_data_row(row: list[str]) -> bool:
    """Section data rows have a numeric (or suppressed '*') trade count in
    column C (the sheet has a leading empty column A)."""
    if len(row) < 3:
        return False
    v = row[2].strip()
    return v == "*" or to_float(v) is not None


def parse_star(data: bytes) -> tuple[date, dict[str, float]]:
    """Parse the STAR workbook from the daily ZIP."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip file: {exc}") from exc
    names = [n for n in z.namelist()
             if "STAR" in n.upper() and n.endswith(".xlsx")]
    if not names:
        raise ValueError(f"no STAR xlsx in zip: {z.namelist()[:5]}")
    rows = read_sheet(z.read(names[0]))
    asof_val = None
    for r in sorted(rows):
        cells = [c.strip() for c in rows[r]]
        for i, c in enumerate(cells):
            if c.upper().startswith("DATA AS OF"):
                asof_val = cells[i + 1] if i + 1 < len(cells) else ""
                break
        if asof_val is not None:
            break
    serial = to_float(asof_val or "")
    if serial is None:
        raise ValueError(f"bad DATA AS OF value: {asof_val!r}")
    asof = _EPOCH + timedelta(days=int(serial))

    in_b = False
    agency_par = agency_trades = nonag_par = nonag_trades = 0.0
    for r in sorted(rows):
        row = rows[r]
        upper = " | ".join((c or "").upper() for c in row)
        if "INVESTMENT GRADE" in upper and "NON-INVESTMENT" in upper:
            in_b = True
            continue
        if not _is_data_row(row):
            continue
        if not in_b:
            # section A: $ at every 3rd col from E (idx 4); counts from C (2)
            for i in range(4, len(row), 3):
                v = to_float(row[i])
                if v is not None:
                    agency_par += v
            for i in range(2, len(row), 3):
                v = to_float(row[i])
                if v is not None:
                    agency_trades += v
        else:
            # section B: IG $ at idx 4, non-IG $ at idx 7; counts at 2 and 5
            for i in (4, 7):
                if i < len(row):
                    v = to_float(row[i])
                    if v is not None:
                        nonag_par += v
            for i in (2, 5):
                if i < len(row):
                    v = to_float(row[i])
                    if v is not None:
                        nonag_trades += v
    if agency_par == 0 and nonag_par == 0:
        raise ValueError("parsed zero par from STAR workbook")
    return asof, {
        "ice-star-agency-par": agency_par / 1e6,      # $000s -> $bn
        "ice-star-agency-trades": agency_trades,
        "ice-star-nonagency-par": nonag_par / 1e6,
        "ice-star-nonagency-trades": nonag_trades,
    }


async def fetch_ice_star(store: Store, get_bytes: GetBytes,
                         today: date | None = None) -> str:
    """Daily job: latest ZIP (probe back 7 days); bounded 30-day backfill
    on an empty store."""
    today = today or date.today()
    got = False
    errors: list[str] = []
    for back in range(7):
        day = today - timedelta(days=back)
        url = URL.format(day.isoformat())
        try:
            data = await get_bytes(url)
            if len(data) < 10000:
                raise ValueError(f"suspiciously small ({len(data)}B)")
            asof, vals = parse_star(data)
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            got = True
            break
        except Exception as exc:  # noqa: BLE001 — weekends/holidays
            errors.append(f"{day}: {exc}")
            await asyncio.sleep(REQUEST_GAP)
    if not got:
        raise RuntimeError("no ICE STAR zip in last 7 days: "
                           + "; ".join(errors[:3]))
    if len(store.points("cycle:ice-star-agency-par")) < 10:
        backfilled = 0
        for back in range(1, 31):
            day = today - timedelta(days=back)
            try:
                data = await get_bytes(URL.format(day.isoformat()))
                asof, vals = parse_star(data)
            except Exception as exc:  # noqa: BLE001 — tolerate gaps
                log.debug("ice_star %s skipped: %s", day, exc)
                await asyncio.sleep(REQUEST_GAP)
                continue
            for key, val in vals.items():
                store.upsert_points(f"cycle:{key}", [(asof, val)])
            backfilled += 1
            await asyncio.sleep(REQUEST_GAP)
        log.info("ice_star backfilled %d days", backfilled)
    store.put_doc("ice_star", {"series": list(SERIES)}, source=SOURCE)
    return SOURCE
