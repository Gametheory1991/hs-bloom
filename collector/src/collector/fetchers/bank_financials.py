"""Bank quarterly three-statement financials via SEC XBRL companyconcept.

Covers the bank_fixed_income universe's public companies (money-center,
regionals, custody, GSEs, Jefferies). Unlike fetch_universe_capex (which
stores as-reported quarter-ends), income-statement items are de-annualized
to true calendar quarters via _quarterly_points, because 10-Q income facts
are year-to-date.

Series: bank_fixed_income:{TICKER}:{metric}
Doc:    bank_fixed_income_financials
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from collector.config import Config
from collector.fetchers.ai_capex import (
    BANK_FLOW_METRICS,
    BANK_STOCK_METRICS,
    BANK_TAGS,
    PAUSE,
    _fetch_tag,
    _fetch_tag_rows,
    _quarterly_points,
    load_universe,
    universe_companies,
)
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "bank-financials-weekly"

# Metrics written as per-bank series (order matters for the UI table).
SERIES_METRICS = (
    "assets", "deposits", "loans", "equity", "debt",
    "nii", "nonint_income", "nonint_expense", "net_income", "revenue",
    "nim_proxy", "loan_to_deposit", "debt_to_equity",
    "roa_ttm", "roe_ttm", "yoy_nii", "yoy_net_income",
)


def _ttm(vals: list[float | None]) -> float | None:
    """Trailing-twelve-month sum of the last 4 quarterly values."""
    last4 = vals[-4:]
    if len(last4) < 4 or any(v is None for v in last4):
        return None
    return sum(last4)  # type: ignore[arg-type]


async def _one_bank(co: dict, user_agent: str,
                    get_text: GetText) -> dict:
    """Fetch all bank tags for one company; return {quarters, currencies}."""
    cik = co["cik"]
    flow: dict[str, dict[date, float]] = {}
    stock: dict[str, dict[date, float]] = {}
    currencies: dict[str, str] = {}
    for metric in BANK_TAGS:
        if metric in BANK_FLOW_METRICS:
            rows = await _fetch_tag_rows(cik, metric, user_agent, get_text,
                                        BANK_TAGS)
            flow[metric] = _quarterly_points(rows)
        else:
            parsed, unit = await _fetch_tag(cik, metric, user_agent,
                                            get_text, BANK_TAGS)
            stock[metric] = parsed
            if unit:
                currencies[metric] = unit
    series: dict[str, dict[date, float]] = {**flow, **stock}
    quarters: dict[date, dict] = {}
    ends = sorted(set().union(*series.values())) if series else []
    for end in ends:
        q: dict = {m: series[m][end] for m in series if end in series[m]}
        quarters[end] = q
    # Derived ratios per quarter (need history, so compute on sorted ends).
    for i, end in enumerate(ends):
        q = quarters[end]
        nii, assets = q.get("nii"), q.get("assets")
        loans, deposits = q.get("loans"), q.get("deposits")
        debt, equity = q.get("debt"), q.get("equity")
        netinc = q.get("net_income")
        if loans and deposits:
            q["loan_to_deposit"] = loans / deposits
        if debt and equity:
            q["debt_to_equity"] = debt / equity
        # TTM-based: need the last 4 quarterly ends.
        win = ends[max(0, i - 3): i + 1]
        nii_w = [quarters[e].get("nii") for e in win]
        ni_w = [quarters[e].get("net_income") for e in win]
        a_w = [quarters[e].get("assets") for e in win]
        e_w = [quarters[e].get("equity") for e in win]
        nii_ttm, ni_ttm = _ttm(nii_w), _ttm(ni_w)
        avg_a = sum(a_w) / len(a_w) if a_w and all(a_w) else None
        avg_e = sum(e_w) / len(e_w) if e_w and all(e_w) else None
        if nii_ttm and avg_a:
            # NIM proxy: banks rarely tag average earning assets; label it.
            q["nim_proxy"] = nii_ttm / avg_a
        if ni_ttm and avg_a:
            q["roa_ttm"] = ni_ttm / avg_a
        if ni_ttm and avg_e:
            q["roe_ttm"] = ni_ttm / avg_e
        # YoY vs the same quarter last year (4 quarters back).
        if i >= 4:
            prev = quarters[ends[i - 4]]
            if nii and prev.get("nii"):
                q["yoy_nii"] = (nii - prev["nii"]) / abs(prev["nii"])
            if netinc and prev.get("net_income"):
                q["yoy_net_income"] = ((netinc - prev["net_income"])
                                       / abs(prev["net_income"]))
    return {"quarters": quarters, "currencies": currencies}


async def fetch_bank_financials(cfg: Config, store: Store,
                                get_text: GetText) -> str:
    """Weekly job: SEC XBRL quarterly three-statement for each bank."""
    today = datetime.now(timezone.utc).date()
    universe = load_universe("bank_fixed_income")
    companies_cfg = universe_companies(universe)
    user_agent = cfg.thirteenf.user_agent
    prefix = "bank_fixed_income"
    companies: dict[str, dict] = {}
    ok = 0
    for co in companies_cfg:
        ticker = co["ticker"]
        try:
            res = await _one_bank(co, user_agent, get_text)
            await asyncio.sleep(PAUSE)  # SEC fair access
        except Exception as exc:  # noqa: BLE001 — one bank never kills it
            log.warning("bank_financials: %s failed: %s", ticker, exc)
            continue
        quarters = res["quarters"]
        if not quarters:
            log.info("bank_financials: %s no usable XBRL facts", ticker)
            continue
        ok += 1
        for metric in SERIES_METRICS:
            pts = [(d, quarters[d][metric]) for d in sorted(quarters)
                   if metric in quarters[d]]
            if pts:
                store.upsert_points(f"{prefix}:{ticker}:{metric}", pts)
        ends = sorted(quarters)
        last = quarters[ends[-1]]
        companies[ticker] = {
            "id": co["id"], "name": co["name"],
            "vertical": co["vertical"],
            "latest_quarter": ends[-1].isoformat(),
            "quarters_reported": len(ends),
            "currency": res.get("currencies", {}).get("net_income", "USD"),
            **{m: last.get(m) for m in SERIES_METRICS},
        }
    store.put_doc("bank_fixed_income_financials", {
        "as_of": today.isoformat(),
        "universe_id": "bank_fixed_income",
        "companies": companies,
        "metrics": list(SERIES_METRICS),
        "note": "SEC EDGAR XBRL companyconcept, free. Income-statement "
                "items are de-annualized to true calendar quarters "
                "(10-Q facts are YTD; Q4 = 10-K annual minus Q3 YTD). "
                "nim_proxy = TTM net interest income / average total assets "
                "(banks rarely tag average earning assets; labeled proxy). "
                "Quarters with missing inputs are honest gaps.",
    }, source=SOURCE)
    return f"bank_financials: {ok}/{len(companies_cfg)} banks"
