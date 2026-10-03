"""USAspending.gov federal fiscal pulse — keyless.

Three POST calls (verified live 2026-10-03), 3s gaps:

1. /api/v2/search/spending_over_time/ — monthly obligations, trailing
   12 months, split by award type (contracts / direct / grants / IDV).
2. /api/v2/search/spending_by_category/recipient/ — top-10 recipients by
   obligated amount, trailing 12 months.
3. /api/v2/search/spending_by_category/awarding_agency/ — top-10 awarding
   agencies.

(The older {"category": "recipient"} body form 404s, as does a bare
/agency/ path — the category now lives in the URL path, and agencies need
the awarding_agency qualifier.)

Stored: cycle:usaspending:oblig-total | :oblig-contract | :oblig-grants
(monthly points, $B) plus a `usaspending` doc with the monthly table, top
recipients, and top agencies. Rationale: federal obligations are the
highest-frequency read on US fiscal impulse — the terminal's ECON tab had
TGA and bills but no spending-flow signal.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from collector.http import PostJson
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "usaspending-api"
BASE = "https://api.usaspending.gov"

REQUEST_GAP = 3.0
TOP_N = 10


def _window(months: int = 12) -> tuple[str, str]:
    end = date.today()
    start = (end - timedelta(days=30 * months)).replace(day=1)
    return start.isoformat(), end.isoformat()


def _fiscal_to_calendar(fiscal_year: int, fiscal_month: int) -> date:
    """Federal fiscal month 1 = October of the prior calendar year."""
    if fiscal_month <= 3:
        return date(fiscal_year - 1, fiscal_month + 9, 1)
    return date(fiscal_year, fiscal_month - 3, 1)


def parse_spending_over_time(body: dict) -> list[dict]:
    """Monthly obligation rows: [{date, total_b, contract_b, direct_b, grants_b}]."""
    rows = []
    for r in body.get("results", []):
        try:
            tp = r["time_period"]
            d = _fiscal_to_calendar(int(tp["fiscal_year"]), int(tp["month"]))
            rows.append({
                "date": d,
                "total_b": (r.get("aggregated_amount") or 0) / 1e9,
                "contract_b": (r.get("Contract_Obligations") or 0) / 1e9,
                "direct_b": (r.get("Direct_Obligations") or 0) / 1e9,
                "grants_b": (r.get("Grant_Obligations") or 0) / 1e9,
            })
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["date"])
    return rows


def parse_category(body: dict, limit: int = TOP_N) -> list[dict]:
    out = []
    for r in (body.get("results") or [])[:limit]:
        name = (r.get("name") or "").strip()
        if not name or name == "MULTIPLE RECIPIENTS":
            continue
        try:
            out.append({"name": name, "amount_b": round(float(r.get("amount") or 0) / 1e9, 2)})
        except (TypeError, ValueError):
            continue
    return out


async def fetch_usaspending(store: Store, post_json: PostJson) -> str:
    start, end = _window()
    filters = {"time_period": [{"start_date": start, "end_date": end}]}

    monthly: list[dict] = []
    try:
        body = await post_json(f"{BASE}/api/v2/search/spending_over_time/",
                               {"group": "month", "filters": filters})
        monthly = parse_spending_over_time(body if isinstance(body, dict) else json.loads(body))
    except Exception as exc:  # noqa: BLE001 — one call never kills the job
        log.warning("usaspending spending_over_time failed: %s", exc)

    await asyncio.sleep(REQUEST_GAP)
    recipients: list[dict] = []
    try:
        body = await post_json(f"{BASE}/api/v2/search/spending_by_category/recipient/",
                               {"filters": filters, "limit": TOP_N})
        recipients = parse_category(body if isinstance(body, dict) else json.loads(body))
    except Exception as exc:  # noqa: BLE001
        log.warning("usaspending recipients failed: %s", exc)

    await asyncio.sleep(REQUEST_GAP)
    agencies: list[dict] = []
    try:
        body = await post_json(f"{BASE}/api/v2/search/spending_by_category/awarding_agency/",
                               {"filters": filters, "limit": TOP_N})
        agencies = parse_category(body if isinstance(body, dict) else json.loads(body))
    except Exception as exc:  # noqa: BLE001
        log.warning("usaspending agencies failed: %s", exc)

    if not monthly and not recipients and not agencies:
        raise RuntimeError("all usaspending calls failed")

    for key in ("total_b", "contract_b", "grants_b"):
        pts = [(r["date"], r[key]) for r in monthly]
        if pts:
            store.upsert_points(f"cycle:usaspending:oblig-{key[:-2]}", pts)
    store.put_doc("usaspending", {
        "as_of": date.today().isoformat(),
        "window": {"start": start, "end": end},
        "monthly": [{**r, "date": r["date"].isoformat()} for r in monthly],
        "top_recipients": recipients,
        "top_agencies": agencies,
    }, SOURCE)
    return SOURCE
