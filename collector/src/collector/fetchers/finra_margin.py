"""FINRA margin statistics — monthly debit/credit balances (keyless).

A single static XLSX, updated in place each month (released ~3 weeks
after month-end), with history back to 1997-01:

  https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx

Layout (verified live 2026-10-03, latest 2026-08): row 1 headers
[Year-Month, Debit Balances in Customers' Securities Margin Accounts,
Free Credit Balances in Customers' Cash Accounts, Free Credit Balances
in Customers' Securities Margin Accounts]; data rows newest-first;
values in $millions.

Stored as cycle:<id> ($M).
"""
from __future__ import annotations

import logging
from datetime import date

from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
SOURCE = "finra-margin-stats"

SERIES = {
    "finra-margin-debit": 1,          # debit balances, $M
    "finra-margin-credit-cash": 2,    # free credit, cash accounts, $M
    "finra-margin-credit-margin": 3,  # free credit, margin accounts, $M
}


def parse_workbook(data: bytes) -> dict[str, list[tuple[date, float]]]:
    rows = read_sheet(data)
    header = rows.get(1) or []
    if not header or header[0] != "Year-Month":
        raise ValueError(f"unexpected header row: {header[:2]!r}")
    out: dict[str, list[tuple[date, float]]] = {k: [] for k in SERIES}
    for r in sorted(rows):
        if r == 1:
            continue
        row = rows[r]
        ym = (row[0] if row else "").strip()
        if len(ym) != 7 or ym[4] != "-":
            continue
        try:
            d = date(int(ym[:4]), int(ym[5:7]), 1)
        except ValueError:
            continue
        for key, col in SERIES.items():
            if len(row) <= col:
                continue
            val = to_float(row[col])
            if val is not None:
                out[key].append((d, val))
    if not out["finra-margin-debit"]:
        raise ValueError("no debit-balance rows parsed")
    for pts in out.values():
        pts.sort(key=lambda p: p[0])
    return out


async def fetch_finra_margin(store: Store, get_bytes: GetBytes) -> str:
    """Monthly job: the whole history ships in one file, so every run is
    a full refresh (idempotent upsert)."""
    data = await get_bytes(URL)
    if len(data) < 5000:
        raise ValueError(f"suspiciously small payload ({len(data)}B)")
    parsed = parse_workbook(data)
    for key, pts in parsed.items():
        store.upsert_points(f"cycle:{key}", pts)
    latest = max(d for d, _ in parsed["finra-margin-debit"])
    store.put_doc("finra_margin", {
        "as_of": latest.isoformat(),
        "series": sorted(SERIES),
        "latest_debit_m": parsed["finra-margin-debit"][-1][1],
    }, source=SOURCE)
    return SOURCE
