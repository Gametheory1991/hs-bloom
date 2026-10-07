"""NY Fed Corporate Bond Market Distress Index (CMDI). Weekly, Friday EOW.

Direct xlsx download (no auth, verified 2026-10-06):

  https://www.newyorkfed.org/medialibrary/research/interactives/data/cmdi/cmdi_interactive_data.xlsx

Single sheet, header row:
  eow_friday | Market CMDI | IG CMDI | HY CMDI | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | Recession
Dates are Excel serials (weekly Fridays from 2005-01-07). Higher CMDI = more
distress. Stored as cycle:cmdi-market / cycle:cmdi-ig / cycle:cmdi-hy.
"""
from __future__ import annotations

import logging
from datetime import date

from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

URL = ("https://www.newyorkfed.org/medialibrary/research/interactives/data/"
       "cmdi/cmdi_interactive_data.xlsx")
SOURCE = "nyfed-cmdi"

# NY Fed's CDN serves the file to browsers; pass a browser UA explicitly.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_EPOCH = date(1899, 12, 30)  # Excel serial epoch

SERIES = ("cmdi-market", "cmdi-ig", "cmdi-hy")


def _parse(data: bytes) -> dict[str, list[tuple[date, float]]]:
    rows = read_sheet(data)
    hdr = rows.get(1, [])
    if not hdr or hdr[0].strip().lower() != "eow_friday":
        raise ValueError(f"unexpected CMDI header: {hdr[:4]}")
    out: dict[str, list[tuple[date, float]]] = {s: [] for s in SERIES}
    for r in sorted(rows):
        if r == 1:
            continue
        row = rows[r]
        serial = to_float(row[0]) if len(row) > 0 else None
        if serial is None:
            continue
        d = date.fromordinal(_EPOCH.toordinal() + int(serial))
        for sid, col in zip(SERIES, (1, 2, 3)):
            v = to_float(row[col]) if len(row) > col else None
            if v is not None:
                out[sid].append((d, v))
    if not out["cmdi-market"]:
        raise ValueError("CMDI sheet parsed but no market values found")
    return out


async def fetch_nyfed_cmdi(store: Store, get_bytes: GetBytes) -> str:
    """Weekly job: pull the CMDI workbook and upsert the three index series."""
    data = await get_bytes(URL, headers={"User-Agent": BROWSER_UA})
    series = _parse(data)
    for sid, points in series.items():
        store.upsert_points(f"cycle:{sid}", points)
    latest = series["cmdi-market"][-1]
    log.info("nyfed_cmdi: %d weekly points, latest %s = %.2f",
             len(series["cmdi-market"]), latest[0].isoformat(), latest[1])
    return SOURCE
