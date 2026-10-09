"""FINRA margin concentration — firm-level breakdown + leverage risk metrics.

HONEST AVAILABILITY NOTE (verified 2026-10-09):
  FINRA Rule 4521(d) requires each member firm carrying margin accounts to
  report debit/free-credit balances TO FINRA monthly — but FINRA publishes
  only the AGGREGATE. There is no public firm-level margin dataset:
    * the monthly XLSX (same source as finra_margin.py) is aggregate-only
    * api.finra.org has no keyless margin dataset (probed 2026-10-09)
    * the FINRA Industry Snapshot shows aggregate charts only
  So "top 5 firms' share of margin debt" cannot be computed from public
  FINRA data today. If FINRA ever publishes firm-level margin (or Harry
  obtains it via another channel), parse_firm_table() below is the ready
  stub — same XLSX layout, one row per firm.

WHAT THIS MODULE DOES NOW (no new data source, no duplication):
  Reuses the aggregate margin XLSX to compute concentration-*relevant*
  derived series that finra_margin.py does not store (distinct series IDs,
  no overlap):
    cycle:finra-margin-levratio    debit / total free credit (leverage ratio)
    cycle:finra-margin-netdebit    debit - total free credit ($M net leverage)
    cycle:finra-margin-debit-yoy   YoY % change in debit balances
    cycle:finra-margin-debit-mom   MoM % change in debit balances
  plus doc finra_margin_risk {asof, levratio, netdebit_m, yoy, mom, note}.

Why: the Dukascopy analysis of this exact file (Aug 2023-Jan 2026) showed
debit balances doubling to $1.28T while free credit lagged — the
debit-per-dollar-of-cash ratio rose from 3.90 to 6.28. That ratio IS the
concentration/fragility signal available from public data, and it now
lands as a first-class series.

Source (same as finra_margin.py):
  https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx
Layout: row 1 headers [Year-Month, Debit..., Free Credit Cash...,
Free Credit Margin...]; data newest-first; $millions.
"""
from __future__ import annotations

import logging
from datetime import date

from collector.fetchers.xlsx import read_sheet, to_float
from collector.http import GetBytes
from collector.store import Store

log = logging.getLogger(__name__)

URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
SOURCE = "finra-margin-risk"

# column indices in the XLSX (0-based): keep in sync with finra_margin.py
COL_DEBIT = 1
COL_CREDIT_CASH = 2
COL_CREDIT_MARGIN = 3


def parse_margin_rows(data: bytes) -> list[dict]:
    """Parse the aggregate XLSX -> [{date, debit_m, credit_cash_m,
    credit_margin_m}]. Shared shape with finra_margin.parse_workbook but
    returns rows (not per-series lists) for derived math."""
    rows = read_sheet(data)
    header = rows.get(1) or []
    if not header or header[0] != "Year-Month":
        raise ValueError(f"unexpected header row: {header[:2]!r}")
    out = []
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
        debit = to_float(row[COL_DEBIT]) if len(row) > COL_DEBIT else None
        cc = to_float(row[COL_CREDIT_CASH]) if len(row) > COL_CREDIT_CASH else None
        cm = to_float(row[COL_CREDIT_MARGIN]) if len(row) > COL_CREDIT_MARGIN else None
        if debit is None:
            continue
        out.append({"date": d, "debit_m": debit,
                    "credit_cash_m": cc or 0.0, "credit_margin_m": cm or 0.0})
    if not out:
        raise ValueError("no margin rows parsed")
    return out


def derive_risk(rows: list[dict]) -> dict[str, list[tuple[date, float]]]:
    """Derived concentration-risk series from aggregate rows."""
    lev, net, yoy, mom = [], [], [], []
    by_date = {r["date"]: r for r in rows}
    for r in rows:
        d = r["date"]
        total_credit = r["credit_cash_m"] + r["credit_margin_m"]
        if total_credit > 0:
            lev.append((d, r["debit_m"] / total_credit))
        net.append((d, r["debit_m"] - total_credit))
        prev_y = by_date.get(date(d.year - 1, d.month, 1))
        if prev_y and prev_y["debit_m"]:
            yoy.append((d, 100.0 * (r["debit_m"] - prev_y["debit_m"]) / prev_y["debit_m"]))
        pm = d.month - 1 or 12
        py = d.year if d.month > 1 else d.year - 1
        prev_m = by_date.get(date(py, pm, 1))
        if prev_m and prev_m["debit_m"]:
            mom.append((d, 100.0 * (r["debit_m"] - prev_m["debit_m"]) / prev_m["debit_m"]))
    return {
        "finra-margin-levratio": lev,
        "finra-margin-netdebit": net,
        "finra-margin-debit-yoy": yoy,
        "finra-margin-debit-mom": mom,
    }


def parse_firm_table(data: bytes) -> list[dict]:
    """STUB — firm-level margin parser.

    FINRA does not publish this today (see module docstring). If a
    firm-level file becomes available with columns like
    [Firm, Debit Balances ($M), Free Credit ($M)], implement here and
    return [{firm, debit_m, credit_m}]; the concentration math below
    (top-5 share) is already written against that shape.
    """
    raise NotImplementedError(
        "FINRA publishes only aggregate margin data publicly; "
        "no firm-level source exists as of 2026-10-09."
    )


def firm_concentration(firms: list[dict]) -> dict:
    """Top-5 share of debit balances from firm rows (for future use)."""
    rows = sorted(firms, key=lambda f: f.get("debit_m") or 0, reverse=True)
    total = sum(f.get("debit_m") or 0 for f in rows)
    top5 = sum(f.get("debit_m") or 0 for f in rows[:5])
    return {
        "n_firms": len(rows),
        "total_debit_m": total,
        "top5_debit_m": top5,
        "top5_share": (top5 / total) if total else None,
        "top5_firms": [f.get("firm") for f in rows[:5]],
    }


async def fetch_finra_margin_firms(store: Store, get_bytes: GetBytes) -> str:
    """Monthly job: derived leverage/concentration-risk series."""
    data = await get_bytes(URL)
    if len(data) < 5000:
        raise ValueError(f"suspiciously small payload ({len(data)}B)")
    rows = parse_margin_rows(data)
    derived = derive_risk(rows)
    for key, pts in derived.items():
        if pts:
            store.upsert_points(f"cycle:{key}", pts)
    latest = rows[-1]
    total_credit = latest["credit_cash_m"] + latest["credit_margin_m"]
    lev = latest["debit_m"] / total_credit if total_credit else None
    store.put_doc("finra_margin_risk", {
        "as_of": latest["date"].isoformat(),
        "debit_m": latest["debit_m"],
        "total_free_credit_m": total_credit,
        "leverage_ratio": lev,
        "firm_level": "not_published",
        "note": ("FINRA publishes only aggregate margin balances; "
                 "firm-level breakdown is not public (Rule 4521(d) reports "
                 "are firm-to-FINRA)."),
    }, source=SOURCE)
    return (f"{SOURCE}: {sum(len(p) for p in derived.values())} derived points, "
            f"asof {latest['date'].isoformat()}")
