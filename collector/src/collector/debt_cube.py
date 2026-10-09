"""Debt-outstanding slice cube: product x maturity x holder. COMPUTE ONLY.

No network I/O, no clock. Callers hand in rows; this module aggregates.

METHODOLOGY
-----------
- Inputs are the raw docs written by the mspd.py and soma_cusip.py fetchers.
- SOMA holdings are a SUBSET of marketable outstanding — never double-count:
  per CUSIP, public = outstanding - soma (floored at 0 for data quirks), so
  soma + public == marketable within every cell.
- TIPS: cubes on ``outstanding_amt`` (original face). The doc also stores
  ``inflation_adj_mn``; if the UI ever wants inflation-adjusted notionals it
  must label that concept explicitly.
- Bills appear as "Bills Maturity Value" (discount basis), not coupon par.
- Non-marketable rows (Government Account Series, SLGS, savings, ...) are
  fund aggregates, not CUSIPs — SOMA math never applies; they land in a
  single holder="nonmarketable" cell.
- Maturity bucket is (maturity_date - asof) in years, 365.25-day years.
- pct_of_total uses the Table 1 "Total Public Debt Outstanding" as denominator.

Doc written by refresh_debt_cube:
  debt_cube — {"asof", "denominator_mn", "denominator_desc", "cells": [...]}
"""
from __future__ import annotations

import logging
from datetime import date

from collector.store import Store
from collector.fetchers.mspd import total_public_debt_outstanding

log = logging.getLogger(__name__)

SOURCE = "debt-cube"

PRODUCTS = ("bills", "notes", "bonds", "tips", "frns")
HOLDERS = ("soma", "public", "nonmarketable")
BUCKETS = ("<1Y", "1-3Y", "3-5Y", "5-10Y", "10-20Y", "20Y+")

PRODUCT_MAP = {
    "Bills Maturity Value": "bills",
    "Notes": "notes",
    "Bonds": "bonds",
    "Inflation-Protected Securities": "tips",
    "Floating Rate Notes": "frns",
}

_MN_PER_BN = 1000.0


def maturity_bucket(maturity: date, asof: date) -> str:
    """Years-to-maturity bucket for (maturity - asof)."""
    years = (maturity - asof).days / 365.25
    if years < 1:
        return "<1Y"
    if years < 3:
        return "1-3Y"
    if years < 5:
        return "3-5Y"
    if years < 10:
        return "5-10Y"
    if years < 20:
        return "10-20Y"
    return "20Y+"


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def build_debt_cube(
    mspd_rows: list[dict],
    soma_rows: list[dict],
    total_outstanding_mn: float,
    asof,
) -> dict:
    """Aggregate CUSIP detail into product x maturity x holder cells.

    mspd_rows: doc rows from mspd_cusips ({cusip, product_raw, maturity_date,
        outstanding_mn, security_type}).
    soma_rows: doc rows from soma_cusips ({cusip, par_value_dollars, ...}).
    total_outstanding_mn: Table 1 "Total Public Debt Outstanding" ($mn),
        the pct_of_total denominator.
    asof: record date for maturity-bucket arithmetic (date or ISO string).
    """
    if total_outstanding_mn <= 0:
        raise ValueError("total_outstanding_mn must be positive")
    asof = _coerce_date(asof)
    soma_par_mn = {r["cusip"]: float(r["par_value_dollars"]) / 1e6 for r in soma_rows}

    cells: dict[tuple[str, str, str], float] = {}

    def add(product: str, maturity: str, holder: str, mn: float) -> None:
        key = (product, maturity, holder)
        cells[key] = cells.get(key, 0.0) + mn

    for row in mspd_rows:
        cusip = row.get("cusip")
        outstanding_mn = float(row.get("outstanding_mn") or 0.0)
        if not cusip or outstanding_mn <= 0:
            continue
        if row.get("security_type") != "Marketable":
            # fund aggregates, not CUSIPs: no SOMA math, single cell
            add("nonmarketable", "all", "nonmarketable", outstanding_mn)
            continue
        product_raw = row.get("product_raw") or ""
        product = PRODUCT_MAP.get(product_raw)
        if product is None:
            raise ValueError(f"unknown MSPD product class: {product_raw!r}")
        bucket = maturity_bucket(_coerce_date(row["maturity_date"]), asof)
        soma_mn = soma_par_mn.get(cusip, 0.0)
        public_mn = max(outstanding_mn - soma_mn, 0.0)
        add(product, bucket, "soma", soma_mn)
        add(product, bucket, "public", public_mn)

    ordered = sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    return {
        "asof": asof.isoformat(),
        "denominator_mn": total_outstanding_mn,
        "denominator_desc": "MSPD Table 1 Total Public Debt Outstanding",
        "cells": [
            {
                "product": product,
                "maturity": maturity,
                "holder": holder,
                "notional_bn": round(mn / _MN_PER_BN, 3),
                "pct_of_total": round(mn / total_outstanding_mn * 100.0, 4),
            }
            for (product, maturity, holder), mn in ordered
            if mn > 0
        ],
    }


def query_cube(
    cube: dict,
    product: str | None = None,
    maturity: str | None = None,
    holder: str | None = None,
) -> list[dict]:
    """Filter helper for the API layer: any-dimension slice of cube cells."""
    return [
        cell
        for cell in cube.get("cells", [])
        if (product is None or cell["product"] == product)
        and (maturity is None or cell["maturity"] == maturity)
        and (holder is None or cell["holder"] == holder)
    ]


def _table1_total(table1_rows: list[dict]) -> float:
    # Resilient lookup shared with the mspd fetcher: exact match on the
    # "Total Public Debt Outstanding" row, then fuzzy match, then the
    # Marketable + Nonmarketable sum as a last resort. Behavior is
    # identical to the old exact-match when the literal row exists.
    return total_public_debt_outstanding(table1_rows)


def refresh_debt_cube(store: Store) -> str:
    """Rebuild the slice cube from the three source docs.

    Writes doc ``debt_cube``. Returns "debt-cube-skipped" (no error) when any
    source doc is missing — the fetcher schedule heals this on the next run.
    """
    mspd_doc = store.doc("mspd_cusips")
    soma_doc = store.doc("soma_cusips")
    t1_doc = store.doc("mspd_table1")
    if not (mspd_doc and soma_doc and t1_doc):
        log.info("debt-cube: skipping, source docs not all present")
        return "debt-cube-skipped"

    mspd_payload, soma_payload = mspd_doc.payload, soma_doc.payload
    cube = build_debt_cube(
        mspd_payload.get("rows") or [],
        soma_payload.get("rows") or [],
        _table1_total(t1_doc.payload.get("rows") or []),
        mspd_payload.get("asof"),
    )
    store.put_doc("debt_cube", cube, source=SOURCE)
    log.info("debt-cube: %d cells @ %s", len(cube["cells"]), cube["asof"])
    return SOURCE
