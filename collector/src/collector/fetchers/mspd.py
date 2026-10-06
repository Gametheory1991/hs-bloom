"""Treasury MSPD debt-outstanding fetchers (keyless).

Base: https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd

METHODOLOGY
-----------
Two MSPD tables are pulled once per month (month-end publication, ~5-week
lag; latest verified 2026-08-31 on 2026-10-05):

* Table III (mspd_table_3) — CUSIP-level outstanding for every marketable
  security plus non-marketable aggregate rows. CUSIP lives in
  ``security_class2_desc``; ``security_class1_desc`` is the product class;
  amounts are $ millions. "Bills Maturity Value" is discount-basis maturity
  value, not coupon par — do not mix bill "outstanding" with notes/bonds par.
  TIPS: ``outstanding_amt`` is original face; ``inflation_adj_amt`` is the
  inflation-adjusted figure — both are stored and labeled so the cube layer
  can pick one concept explicitly.
* Table 1 (mspd_table_1) — marketable vs non-marketable summary with the
  debt-held-by-public / intragovernmental split per class. Its
  "Total Public Debt Outstanding" ``total_mil_amt`` is the cube denominator;
  it is read from the live payload, never hardcoded.

Only the newest ``record_date`` rows are kept. Total/summary rows (where
``security_class2_desc`` is not a 9-char CUSIP) are skipped — they would
otherwise double-count the detail.

Docs written:
  mspd_cusips  — raw per-CUSIP audit detail for the latest month-end
  mspd_table1  — Table 1 summary rows for the latest month-end
Series written:
  cycle:mspd-total-outstanding — Total Public Debt Outstanding ($mn), monthly
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd"
)
SOURCE = "mspd"
TABLE3_URL = f"{BASE}/mspd_table_3"
TABLE1_URL = f"{BASE}/mspd_table_1"

_CUSIP_RE = re.compile(r"^[A-Z0-9]{9}$")


def _num(v) -> float:
    """Parse a Fiscal Data amount (str or number) into a float."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").strip()
    if not s or s.lower() in ("null", "none"):
        return 0.0
    return float(s)


def _is_detail_row(rec: dict) -> bool:
    """True if the row is a real CUSIP detail row, not a total/summary row."""
    class2 = str(rec.get("security_class2_desc") or "").strip()
    if not _CUSIP_RE.match(class2):
        return False
    class1 = str(rec.get("security_class1_desc") or "").lower()
    # belt-and-suspenders: never trust a row labeled as a total
    if "total" in class2.lower() or "total" in class1:
        return False
    return True


def _latest_records(records: list[dict]) -> tuple[str, list[dict]]:
    """Keep only the rows for the newest record_date."""
    dates = sorted({r.get("record_date") for r in records if r.get("record_date")})
    if not dates:
        raise RuntimeError("MSPD response contained no record_date rows")
    latest = dates[-1]
    return latest, [r for r in records if r.get("record_date") == latest]


def parse_table3(payload: dict | str) -> tuple[str, list[dict]]:
    """Parse Table III JSON -> (asof, cleaned CUSIP detail rows)."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    records = payload.get("data") or []
    asof, rows = _latest_records(records)
    out = []
    for r in rows:
        if not _is_detail_row(r):
            continue
        out.append({
            "cusip": str(r["security_class2_desc"]).strip(),
            "product_raw": (r.get("security_class1_desc") or "").strip(),
            "maturity_date": (r.get("maturity_date") or "").strip(),
            "issue_date": (r.get("issue_date") or "").strip(),
            "outstanding_mn": _num(r.get("outstanding_amt")),
            "inflation_adj_mn": _num(r.get("inflation_adj_amt")),
            "security_type": (r.get("security_type_desc") or "").strip(),
        })
    return asof, out


def parse_table1(payload: dict | str) -> tuple[str, list[dict]]:
    """Parse Table 1 JSON -> (asof, summary rows)."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    records = payload.get("data") or []
    asof, rows = _latest_records(records)
    out = []
    for r in rows:
        out.append({
            "security_type": (r.get("security_type_desc") or "").strip(),
            "security_class": (r.get("security_class_desc") or "").strip(),
            "total_mn": _num(r.get("total_mil_amt")),
            "debt_held_public_mn": _num(r.get("debt_held_public_mil_amt")),
            "intragov_hold_mn": _num(r.get("intragov_hold_mil_amt")),
        })
    return asof, out


def total_public_debt_outstanding(table1_rows: list[dict]) -> float:
    """Total Public Debt Outstanding ($mn) from Table 1 summary rows."""
    for r in table1_rows:
        if r.get("security_class", "").lower() == "total public debt outstanding":
            return r["total_mn"]
    raise RuntimeError("mspd_table_1 payload missing 'Total Public Debt Outstanding' row")


async def fetch_mspd(store: Store, get_text: GetText, today=None) -> str:
    """Monthly job: MSPD Table III CUSIP detail + Table 1 summary.

    Writes docs ``mspd_cusips`` / ``mspd_table1`` and the
    ``cycle:mspd-total-outstanding`` series.
    """
    try:
        text3 = await get_text(
            TABLE3_URL,
            params={"sort": "-record_date", "page[size]": 1200},
        )
        asof3, cusip_rows = parse_table3(text3)
        if not cusip_rows:
            raise RuntimeError("mspd_table_3 returned no CUSIP detail rows")
        store.put_doc("mspd_cusips", {"asof": asof3, "rows": cusip_rows}, source=SOURCE)
    except Exception as exc:  # noqa: BLE001 — table 3 must not starve table 1
        raise RuntimeError(f"mspd table_3 failed: {exc}") from exc

    try:
        text1 = await get_text(
            TABLE1_URL,
            params={"sort": "-record_date", "page[size]": 100},
        )
        asof1, table1_rows = parse_table1(text1)
        store.put_doc("mspd_table1", {"asof": asof1, "rows": table1_rows}, source=SOURCE)
        total_mn = total_public_debt_outstanding(table1_rows)
        store.upsert_points(
            "cycle:mspd-total-outstanding",
            [(date.fromisoformat(asof1), total_mn)],
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"mspd table_1 failed: {exc}") from exc

    log.info("mspd: %d CUSIPs @ %s, total $%.1fbn @ %s",
             len(cusip_rows), asof3, total_mn / 1000, asof1)
    return SOURCE
