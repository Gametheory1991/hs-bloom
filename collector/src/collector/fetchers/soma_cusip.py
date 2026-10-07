"""NY Fed SOMA Treasury holdings by CUSIP (keyless).

Endpoints (verified live 2026-10-05):
  GET https://markets.newyorkfed.org/api/soma/asofdates/latest.json
      -> latest weekly as-of date (Wednesdays; verified 2026-09-30)
  GET https://markets.newyorkfed.org/api/soma/tsy/get/asof/{yyyy-MM-dd}.csv
      -> 433 CUSIPs, columns: As Of Date, CUSIP, Security Type, Maturity Date,
         Coupon, Par Value ($), Current Face Value, Inflation Compensation,
         Percent Outstanding, Change From Prior Week, Change From Prior Year

METHODOLOGY
-----------
"Par Value ($)" is the par amount the Fed holds in that CUSIP — that IS the
SOMA holding, do NOT multiply by "Percent Outstanding" (which is just
SOMA-par / total-outstanding for that CUSIP, a display ratio). Weekly job.
433 rows x ~12 columns is ~48 KB, cheap enough to store raw as a doc.

Docs written:
  soma_cusips  — per-CUSIP SOMA holdings for the latest as-of date
Series written:
  cycle:soma-total-par — total SOMA Treasury par ($bn), weekly
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

BASE = "https://markets.newyorkfed.org/api/soma"
SOURCE = "soma-cusip"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").strip()
    if not s or s.lower() in ("null", "none"):
        return 0.0
    return float(s)


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def _pick(row: dict, *candidates: str) -> str:
    """Fetch a CSV column by fuzzy header match (tolerates '($)', spacing)."""
    wanted = {_norm(c) for c in candidates}
    for header, value in row.items():
        if _norm(header or "") in wanted:
            return value or ""
    return ""


def parse_latest_asof(payload: dict | list | str) -> str:
    """Extract the latest as-of date from the NY Fed asofdates response."""
    if isinstance(payload, str):
        payload = json.loads(payload)

    def _from_item(item) -> str | None:
        if isinstance(item, dict):
            for key in ("asOfDate", "asofdate", "as_of_date", "date"):
                if item.get(key):
                    return str(item[key])
            return None
        if isinstance(item, str):
            return item
        return None

    candidates: list[str] = []
    if isinstance(payload, dict):
        for key in ("asofdates", "asOfDates", "asofDates", "dates"):
            items = payload.get(key)
            if isinstance(items, list):
                candidates = [d for d in (_from_item(i) for i in items) if d]
                break
        if not candidates:
            for key in ("latest", "asOfDate"):
                if payload.get(key):
                    candidates = [str(payload[key])]
                    break
    elif isinstance(payload, list):
        candidates = [d for d in (_from_item(i) for i in payload) if d]

    if not candidates:
        raise RuntimeError(f"unrecognized soma asofdates shape: {str(payload)[:200]}")
    asof = sorted(candidates)[-1]
    if not _DATE_RE.match(asof):
        raise RuntimeError(f"bad soma as-of date: {asof!r}")
    return asof


def parse_soma_csv(text: str) -> list[dict]:
    """Parse the SOMA Treasury CUSIP CSV into cleaned rows."""
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        cusip = _pick(row, "cusip").strip().upper()
        if not cusip:
            continue
        out.append({
            "cusip": cusip,
            "security_type": _pick(row, "security type").strip(),
            "maturity_date": _pick(row, "maturity date").strip(),
            "par_value_dollars": _num(_pick(row, "par value ($)", "par value")),
            "pct_outstanding": _num(_pick(row, "percent outstanding")),
        })
    return out


async def fetch_soma_cusip(store: Store, get_text: GetText, today=None) -> str:
    """Weekly job: SOMA Treasury holdings by CUSIP for the latest as-of.

    Writes doc ``soma_cusips`` and the ``cycle:soma-total-par`` series ($bn).
    """
    latest_json = await get_text(f"{BASE}/asofdates/latest.json")
    asof = parse_latest_asof(latest_json)

    csv_text = await get_text(f"{BASE}/tsy/get/asof/{asof}.csv")
    rows = parse_soma_csv(csv_text)
    if not rows:
        raise RuntimeError(f"soma csv for {asof} returned no CUSIP rows")

    store.put_doc("soma_cusips", {"asof": asof, "rows": rows}, source=SOURCE)
    total_par_bn = sum(r["par_value_dollars"] for r in rows) / 1e9
    store.upsert_points(
        "cycle:soma-total-par",
        [(date.fromisoformat(asof), total_par_bn)],
    )
    log.info("soma_cusip: %d CUSIPs @ %s, total par $%.1fbn",
             len(rows), asof, total_par_bn)
    return SOURCE
