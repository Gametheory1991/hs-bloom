"""Universe-driven XBRL fundamentals fetcher (generic coverage-map template).

Takes a universe_id (e.g. "ai_buildout") and reads the vendored universe
file data/<universe_id>.json — NO AI-specific hardcoding here: the same
code must later accept broker-dealer, bank, crypto and fixed-income
universes. The universe file declares verticals -> companies
[{id, name, ticker, cik, kind: public|private}]; only kind == "public"
companies with a CIK are fetched.

For each public company it pulls free companyconcept XBRL
(https://data.sec.gov/api/xbrl/companyconcept/, no auth; SEC fair-access
User-Agent required — the same contact UA the 13F fetcher uses) and stores
quarterly histories:

    Revenues (us-gaap; falls back to ifrs-full Revenue),
    PaymentsToAcquirePropertyPlantAndEquipment (capex; falls back to
      us-gaap CapitalExpenditures, then ifrs-full
      PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities),
    NetCashProvidedByUsedInOperatingActivities (OCF; falls back to ifrs-full
      CashFlowsFromUsedInOperatingActivities),
    LongTermDebt (falls back to LongTermDebtNoncurrent),
    Assets (falls back to TotalAssets).

Derived per quarter: FCF = OCF - capex; capex_intensity = capex / revenue;
funding_gap = capex - OCF; debt_to_assets = LongTermDebt / Assets.

Aggregates declared in the universe file ("aggregates": [{id, label,
members: [company ids], metric}]) are summed by as-reported quarter-end
(fiscal quarters are NOT re-aligned; the UI says so).

Series keys: <universe_id>:<TICKER>:<metric>, plus
<universe_id>:<AGG_ID>:<metric>. Doc "<universe_id>_capex":
{as_of, companies: {ticker: latest + flags}, aggregates: {...}}.

Weekly poll (data is quarterly). One bad company never fails the job.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from importlib.resources import files

from collector.config import Config
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "universe-xbrl-weekly"
PAUSE = 0.4  # SEC fair access: well under 10 req/sec

XBRL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{}/{}/{}.json"

# (us-gaap tags..., then ifrs-full fallbacks). Each entry: (taxonomy, tag).
# NB: some filers change capex concepts over time (NVDA moved from
# PaymentsToAcquirePropertyPlantAndEquipment to PaymentsToAcquireProductiveAssets
# in 2020); _fetch_tag picks the tag with the freshest newest fact.
TAGS: dict[str, list[tuple[str, str]]] = {
    "revenue": [("us-gaap", "Revenues"),
              ("us-gaap", "SalesRevenueNet"),  # ASML's top-line concept
              ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
              ("ifrs-full", "Revenue")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
              ("us-gaap", "PaymentsToAcquireProductiveAssets"),
              ("us-gaap", "CapitalExpenditures"),
              ("ifrs-full",
               "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities")],
    "ocf": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
            ("ifrs-full", "CashFlowsFromUsedInOperatingActivities")],
    "debt": [("us-gaap", "LongTermDebt"),
             ("us-gaap", "LongTermDebtNoncurrent")],
    "assets": [("us-gaap", "Assets"), ("us-gaap", "TotalAssets")],
}


def load_universe(universe_id: str) -> dict:
    """Load data/<universe_id>.json. Generic — no AI-specific assumptions."""
    raw = (files("collector") / "data" / f"{universe_id}.json").read_text(
        encoding="utf-8")
    return json.loads(raw)


def universe_companies(universe: dict) -> list[dict]:
    """All public companies across verticals, each with id/name/ticker/cik."""
    out = []
    for v in universe.get("verticals", []):
        for c in v.get("companies", []):
            if c.get("kind") == "public" and c.get("cik"):
                out.append({**c, "vertical": v["id"]})
    return out


def _parse_rows(rows: list[dict]) -> dict[date, float]:
    """Keep 10-Q/10-K (and 20-F/40-F) facts; dedupe by quarter-end, latest
    accession wins."""
    out: dict[date, tuple[str, float]] = {}
    for r in rows:
        if r.get("form") not in ("10-Q", "10-K", "20-F", "40-F"):
            continue
        try:
            end = date.fromisoformat(r["end"])
            val = float(r["val"])
        except (KeyError, ValueError, TypeError):
            continue
        accn = r.get("accn", "")
        if end not in out or accn > out[end][0]:
            out[end] = (accn, val)
    return {d: v for d, (_, v) in out.items()}


async def _fetch_tag(cik: str, metric: str, user_agent: str,
                     get_text: GetText) -> tuple[dict[date, float], str | None]:
    """Return (quarter-end -> value, currency). Prefers the USD unit when a
    concept reports several currencies (common for 20-F filers like TSMC).
    When a filer changed concepts over time (NVDA moved from
    PaymentsToAcquirePropertyPlantAndEquipment to
    PaymentsToAcquireProductiveAssets in 2020), the tag with the FRESHEST
    newest fact wins — so old abandoned concepts never shadow live ones."""
    headers = {"User-Agent": user_agent}
    best: tuple[date, dict[date, float], str] | None = None
    for taxonomy, tag in TAGS[metric]:
        try:
            raw = await get_text(XBRL.format(cik, taxonomy, tag),
                                 headers=headers)
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — try the fallback tag
            log.debug("universe_xbrl: %s %s/%s failed: %s", cik, taxonomy,
                      tag, exc)
            continue
        units = data.get("units") or {}
        unit = "USD" if "USD" in units else next(iter(units), None)
        if not unit:
            continue
        parsed = _parse_rows(units[unit])
        if not parsed:
            continue
        newest = max(parsed)
        if best is None or newest > best[0]:
            best = (newest, parsed, unit)
            log.debug("universe_xbrl: %s %s/%s newest=%s", cik, taxonomy,
                      tag, newest)
    if best is None:
        return {}, None
    return best[1], best[2]


async def _one_company(co: dict, user_agent: str,
                       get_text: GetText) -> dict:
    """Fetch all tags for one company; return {quarters: {end: {...}},
    currencies: {metric: unit}}."""
    cik = co["cik"]
    series: dict[str, dict[date, float]] = {}
    currencies: dict[str, str] = {}
    for metric in TAGS:
        parsed, unit = await _fetch_tag(cik, metric, user_agent, get_text)
        series[metric] = parsed
        if unit:
            currencies[metric] = unit
    quarters: dict[date, dict] = {}
    ends = sorted(set().union(*series.values()))
    for end in ends:
        q: dict = {}
        for metric in TAGS:
            if end in series[metric]:
                q[metric] = series[metric][end]
        rev, cx, ocf = q.get("revenue"), q.get("capex"), q.get("ocf")
        if rev and cx:
            q["fcf"] = (ocf or 0.0) - cx
            q["capex_intensity"] = cx / rev
            q["funding_gap"] = cx - (ocf or 0.0)
        debt, assets = q.get("debt"), q.get("assets")
        if debt and assets:
            q["debt_to_assets"] = debt / assets
        quarters[end] = q
    return {"quarters": quarters, "currencies": currencies}


def _flags(quarters: dict[date, dict]) -> list[str]:
    """Risk flags from the last few reported quarters."""
    flags: list[str] = []
    ends = sorted(quarters)
    if len(ends) >= 2:
        fcf2 = [quarters[e].get("fcf") for e in ends[-2:]]
        if all(f is not None and f < 0 for f in fcf2):
            flags.append("cashflow_negative")
    if ends:
        last = quarters[ends[-1]]
        rev = last.get("revenue") or 0
        ci = last.get("capex_intensity")
        # revenue floor: pre-revenue SPACs (e.g. OKLO) would otherwise show
        # 10000%+ intensity — meaningless, so skip the flag under $100M.
        if ci is not None and ci > 0.40 and rev > 1e8:
            flags.append("capex_intensity_high")
        fg = last.get("funding_gap")
        if fg is not None and fg > 5e9:
            flags.append("funding_gap_large")
    return flags


async def fetch_universe_capex(cfg: Config, store: Store, get_text: GetText,
                               universe_id: str = "ai_buildout") -> str:
    """Weekly job: pull XBRL quarterly financials for a universe's public
    companies. Generic in universe_id."""
    today = datetime.now(timezone.utc).date()
    universe = load_universe(universe_id)
    companies_cfg = universe_companies(universe)
    user_agent = cfg.thirteenf.user_agent
    prefix = universe_id
    companies: dict[str, dict] = {}
    agg_series: dict[str, dict[date, float]] = {}
    ok = 0
    for co in companies_cfg:
        ticker = co["ticker"]
        try:
            res = await _one_company(co, user_agent, get_text)
            await asyncio.sleep(PAUSE)  # SEC fair access: ~2 req/sec
        except Exception as exc:  # noqa: BLE001 — one company never kills it
            log.warning("universe_xbrl: %s failed: %s", ticker, exc)
            continue
        quarters = res["quarters"]
        if not quarters:
            log.info("universe_xbrl: %s no usable XBRL facts (IFRS filer?)",
                     ticker)
            continue
        ok += 1
        for metric in ("revenue", "capex", "ocf", "fcf", "capex_intensity",
                       "funding_gap", "debt", "debt_to_assets"):
            pts = [(d, quarters[d][metric]) for d in sorted(quarters)
                   if metric in quarters[d]]
            if pts:
                store.upsert_points(f"{prefix}:{ticker}:{metric}", pts)
        ends = sorted(quarters)
        last = quarters[ends[-1]]
        companies[ticker] = {
            "id": co["id"], "name": co["name"], "vertical": co["vertical"],
            "latest_quarter": ends[-1].isoformat(),
            "currency": res.get("currencies", {}).get("revenue", "USD"),
            "revenue": last.get("revenue"), "capex": last.get("capex"),
            "ocf": last.get("ocf"), "fcf": last.get("fcf"),
            "capex_intensity": last.get("capex_intensity"),
            "funding_gap": last.get("funding_gap"),
            "debt": last.get("debt"), "debt_to_assets": last.get("debt_to_assets"),
            "flags": _flags(quarters),
        }
    aggregates: dict[str, dict] = {}
    for agg in universe.get("aggregates", []):
        by_date: dict[date, float] = {}
        for cid in agg.get("members", []):
            tick = next((t for t, c in companies.items()
                         if c["id"] == cid), None)
            if not tick:
                continue
            pts = store.points(f"{prefix}:{tick}:{agg['metric']}")
            for d, v in pts.items():
                if v:
                    by_date[d] = by_date.get(d, 0.0) + v
        if by_date:
            store.upsert_points(f"{prefix}:{agg['id']}:{agg['metric']}",
                                sorted(by_date.items()))
            aggregates[agg["id"]] = {
                "label": agg["label"],
                "series": [[d.isoformat(), v]
                           for d, v in sorted(by_date.items())[-12:]],
            }
    store.put_doc(f"{prefix}_capex", {
        "as_of": today.isoformat(),
        "universe_id": universe_id,
        "companies": companies,
        "aggregates": aggregates,
        "note": "SEC EDGAR XBRL companyconcept, free (us-gaap with ifrs-full "
                "fallback for foreign filers). FCF = OCF - capex; "
                "funding_gap = capex - OCF. Aggregates sum as-reported "
                "quarter-ends (fiscal quarters not re-aligned).",
    }, source=SOURCE)
    return f"universe_xbrl({universe_id}): {ok}/{len(companies_cfg)} companies"
