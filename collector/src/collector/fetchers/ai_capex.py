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

AI Financials extension: three-statement set — GrossProfit,
OperatingIncomeLoss, NetIncomeLoss, ResearchAndDevelopmentExpense,
CashAndCashEquivalentsAtCarryingValue, StockholdersEquity, DebtCurrent
(+ long-term) — with derived gross/op/net/FCF margins, R&D intensity,
total_debt, net_debt, debt_to_equity, and YoY growth (rev/capex/net
income, matched by calendar month to handle non-calendar fiscals).
Segment revenue is NOT pulled: XBRL segment dimensions are not cleanly
comparable across filers, so the UI labels it unavailable.

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
import re
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
    # --- AI Financials tab: three-statement extension (batch AI-fin) ---
    # Income statement
    "gross_profit": [("us-gaap", "GrossProfit"),
                     ("ifrs-full", "GrossProfit")],
    "op_income": [("us-gaap", "OperatingIncomeLoss")],
    "net_income": [("us-gaap", "NetIncomeLoss"),
                   ("ifrs-full", "ProfitLoss")],
    "rd": [("us-gaap", "ResearchAndDevelopmentExpense"),
            ("ifrs-full", "ResearchAndDevelopmentExpense")],
    # Balance sheet
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
             ("ifrs-full", "CashAndCashEquivalents")],
    "equity": [("us-gaap", "StockholdersEquity"),
               ("us-gaap",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
               ("ifrs-full", "Equity")],
    "stdebt": [("us-gaap", "DebtCurrent"),
               ("us-gaap", "LongTermDebtCurrent")],
}

# Bank three-statement tags (used by bank_financials.py via _fetch_tag's
# tags parameter). Banks rarely tag a single NII concept; JPM/WFC/PNC all
# 404 on us-gaap:NetInterestIncome, while InterestIncomeExpenseNet works.
BANK_TAGS: dict[str, list[tuple[str, str]]] = {
    "nii": [("us-gaap", "InterestIncomeExpenseNet"),
            ("us-gaap", "NetInterestIncome")],
    "nonint_income": [("us-gaap", "NoninterestIncome")],
    "nonint_expense": [("us-gaap", "NoninterestExpense")],
    "net_income": [("us-gaap", "NetIncomeLoss"),
                   ("ifrs-full", "ProfitLoss")],
    "deposits": [("us-gaap", "Deposits")],
    "loans": [("us-gaap", "LoansAndLeasesReceivableNetOfDeferredIncome"),
              ("us-gaap", "LoansReceivableHeldForInvestmentNet"),
              ("us-gaap", "LoansAndLeasesReceivableNet")],
    "equity": [("us-gaap", "StockholdersEquity"),
               ("ifrs-full", "Equity")],
    "assets": TAGS["assets"],
    "debt": TAGS["debt"],
    "revenue": TAGS["revenue"],
}

# Income-statement (flow) metrics need quarterly derivation from YTD facts;
# balance-sheet (stock) metrics are point-in-time and used as-reported.
BANK_FLOW_METRICS = {"nii", "nonint_income", "nonint_expense", "net_income",
                     "revenue"}
BANK_STOCK_METRICS = {"assets", "deposits", "loans", "equity", "debt"}


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


# Duration (flow) concepts whose 10-Q facts must be reduced to true
# quarterly values (standalone quarter preferred; fiscal Q4 derived as
# 10-K annual minus Q3 10-Q YTD). Balance-sheet (instant) concepts are
# not in this set and keep the _parse_rows path.
FLOW_METRICS = {"revenue", "capex", "ocf", "gross_profit", "op_income",
                "net_income", "rd"}


def _quarterly_flow_rows(rows: list[dict]) -> dict[date, float]:
    """True-quarterly values for duration (flow) XBRL concepts.

    A 10-Q files each flow fact twice per quarter-end: the standalone
    quarter (frame CYyyyyQn and/or ~90-day duration) and year-to-date
    (no frame, longer duration). 10-K/20-F facts are full-year. Naive
    end-date dedup mixes YTD/annual values into the quarterly series
    (e.g. NVDA 2026-07-26: 177.8B YTD vs the true 96.2B quarter), so:

    * per quarter-end, prefer the standalone quarterly fact;
    * else derive fiscal Q4 = 10-K annual minus the Q3 10-Q YTD that
      shares its start date (requires ~9-month YTD; otherwise fall back
      to the annual value, matching legacy behavior);
    * a quarter-end with only YTD facts is skipped (honest gap).

    Dedupe within a class is latest-accession-wins, like _parse_rows.
    """
    cands: dict[date, dict[str, list]] = {}
    for r in rows:
        if r.get("form") not in ("10-Q", "10-K", "20-F", "40-F"):
            continue
        try:
            end = date.fromisoformat(str(r["end"])[:10])
            val = float(r["val"])
        except (TypeError, ValueError):
            continue
        start = None
        try:
            if r.get("start"):
                start = date.fromisoformat(str(r["start"])[:10])
        except (TypeError, ValueError):
            start = None
        dur = (end - start).days if start else None
        frame = str(r.get("frame") or "")
        form = r.get("form")
        accn = str(r.get("accn") or "")
        is_qtr = (bool(re.match(r"^CY\d{4}Q[1-4]$", frame))
                  or (form == "10-Q" and (dur is None or 70 <= dur <= 110)))
        is_ann = (form in ("10-K", "20-F", "40-F")
                  and (bool(re.match(r"^CY\d{4}$", frame))
                       or dur is None or dur > 300))
        cls = "qtr" if is_qtr else ("ann" if is_ann else "ytd")
        cands.setdefault(end, {"qtr": [], "ann": [], "ytd": []})
        cands[end][cls].append((accn, val, start, dur))

    out: dict[date, float] = {}
    for end in sorted(cands):
        grp = cands[end]
        if grp["qtr"]:
            accn, val, _, _ = max(grp["qtr"], key=lambda t: t[0])
            out[end] = val
            continue
        if grp["ann"]:
            accn, val, start, _ = max(grp["ann"], key=lambda t: t[0])
            # fiscal Q4 = annual - 9M YTD; the YTD may sit at an earlier
            # quarter-end, so the cross-end lookup below handles it —
            # here we only check YTDs filed under the same end date.
            ytds = [t for t in grp["ytd"] if t[2] == start
                    and t[3] is not None and 200 <= t[3] <= 300]
            if ytds:
                _, yv, _, _ = max(ytds, key=lambda t: t[0])
                out[end] = val - yv
            # else: deferred to the cross-end lookup below
    # cross-end YTD lookup for annuals whose Q3 YTD sits at an earlier end
    if cands:
        all_ytd = []
        for e2, grp in cands.items():
            for accn, val, start, dur in grp["ytd"]:
                if dur is not None and 200 <= dur <= 300:
                    all_ytd.append((e2, accn, val, start))
        for end in sorted(cands):
            if end in out or not cands[end]["ann"]:
                continue
            _, val, start, _ = max(cands[end]["ann"], key=lambda t: t[0])
            match = [t for t in all_ytd if t[3] == start and t[0] < end]
            if match:
                _, _, yv, _ = max(match, key=lambda t: (t[0], t[1]))
                out[end] = val - yv
            else:
                out[end] = val
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
                     get_text: GetText,
                     tags: dict[str, list[tuple[str, str]]] | None = None,
                     ) -> tuple[dict[date, float], str | None]:
    """Return (quarter-end -> value, currency). Prefers the USD unit when a
    concept reports several currencies (common for 20-F filers like TSMC).
    When a filer changed concepts over time (NVDA moved from
    PaymentsToAcquirePropertyPlantAndEquipment to
    PaymentsToAcquireProductiveAssets in 2020), the tag with the FRESHEST
    newest fact wins — so old abandoned concepts never shadow live ones.

    `tags` defaults to TAGS; bank_financials.py passes BANK_TAGS.
    Returns the raw per-tag rows too (third element) when the caller needs
    form/start metadata for quarterly derivation — see _quarterly_points.
    """
    registry = tags if tags is not None else TAGS
    headers = {"User-Agent": user_agent}
    best: tuple[date, dict[date, float], str] | None = None
    for taxonomy, tag in registry[metric]:
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
        # Flow (duration) concepts need true-quarterly reduction; balance
        # sheet (instant) concepts keep the plain end-date dedup.
        parser = _quarterly_flow_rows if metric in FLOW_METRICS else _parse_rows
        parsed = parser(units[unit])
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


async def _fetch_tag_rows(cik: str, metric: str, user_agent: str,
                          get_text: GetText,
                          tags: dict[str, list[tuple[str, str]]],
                          ) -> list[dict]:
    """Like _fetch_tag but returns the raw fact rows (with form/start/end)
    from the freshest tag — needed for quarterly YTD derivation."""
    headers = {"User-Agent": user_agent}
    best: tuple[date, list[dict]] | None = None
    for taxonomy, tag in tags[metric]:
        try:
            raw = await get_text(XBRL.format(cik, taxonomy, tag),
                                 headers=headers)
            data = json.loads(raw)
        except Exception:  # noqa: BLE001 — try the fallback tag
            continue
        units = data.get("units") or {}
        unit = "USD" if "USD" in units else next(iter(units), None)
        if not unit:
            continue
        rows = [r for r in units[unit]
                if r.get("form") in ("10-Q", "10-K", "20-F", "40-F")]
        if not rows:
            continue
        try:
            newest = max(date.fromisoformat(r["end"]) for r in rows
                         if r.get("end"))
        except (KeyError, ValueError, TypeError):
            continue
        if best is None or newest > best[0]:
            best = (newest, rows)
    return best[1] if best else []


def _quarterly_points(rows: list[dict]) -> dict[date, float]:
    """Derive true calendar-quarter values from YTD 10-Q / annual 10-K facts.

    Income-statement facts in 10-Qs are year-to-date, so storing them by
    quarter-end mixes YTD and quarterly values (the old bug: a Q2 YTD total
    read as a "quarter"). Derivation uses each fact's duration:
      - duration <= 100 days: a true quarterly fact (or Q1 YTD) — use as-is.
      - duration > 100 days: multi-quarter YTD — quarterly = YTD minus the
        immediately preceding quarter-end YTD (only when the previous end
        is ~one quarter earlier, 80-100 days; otherwise an honest gap).
      - 10-K annual: Q4 = annual minus Q3 YTD under the same proximity rule.
    Quarters whose inputs are missing are skipped as honest gaps. Balance
    sheet facts must NOT go through here (they are point-in-time).
    """
    ytd: dict[date, tuple[float, str, int]] = {}  # end -> (val, accn, dur)
    for r in rows:
        try:
            end = date.fromisoformat(r["end"])
            start = date.fromisoformat(r["start"])
            val = float(r["val"])
        except (KeyError, ValueError, TypeError):
            continue
        dur = (end - start).days
        accn = r.get("accn", "")
        if end not in ytd or accn > ytd[end][1]:
            ytd[end] = (val, accn, dur)
    out: dict[date, float] = {}
    prev_end: date | None = None
    prev_ytd: float | None = None
    for end in sorted(ytd):
        val, _, dur = ytd[end]
        if dur <= 100:
            out[end] = val  # quarterly fact (or Q1 YTD)
        elif prev_end is not None and 80 <= (end - prev_end).days <= 100 \
                and prev_ytd is not None:
            out[end] = val - prev_ytd
        # else: honest gap — not enough history to de-annualize
        prev_end, prev_ytd = end, val
    return out


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
        # --- AI Financials: margins, leverage, balance-sheet completeness ---
        gp, oi, ni, rd = (q.get("gross_profit"), q.get("op_income"),
                          q.get("net_income"), q.get("rd"))
        if rev:
            if gp is not None:
                q["gross_margin"] = gp / rev
            if oi is not None:
                q["op_margin"] = oi / rev
            if ni is not None:
                q["net_margin"] = ni / rev
            if rd is not None:
                q["rd_intensity"] = rd / rev
            if q.get("fcf") is not None:
                q["fcf_margin"] = q["fcf"] / rev
        stdebt = q.get("stdebt")
        if debt is not None or stdebt is not None:
            q["total_debt"] = (debt or 0.0) + (stdebt or 0.0)
        td, cash, eq = q.get("total_debt"), q.get("cash"), q.get("equity")
        if td is not None and cash is not None:
            q["net_debt"] = td - cash
        if td and eq:
            q["debt_to_equity"] = td / eq
        quarters[end] = q
    # Year-over-year growth: match same calendar month one year back
    # (handles non-calendar fiscal years: AAPL Sep, DELL Jan, CSCO Jul).
    by_ym = {(e.year, e.month): e for e in ends}
    for end in ends:
        prev = by_ym.get((end.year - 1, end.month))
        if prev is None or prev not in quarters:
            continue
        for m, ym in (("revenue", "rev_yoy"), ("capex", "capex_yoy"),
                      ("net_income", "ni_yoy")):
            v, pv = quarters[end].get(m), quarters[prev].get(m)
            if v is not None and pv:
                quarters[end][ym] = v / pv - 1.0
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
                       "funding_gap", "debt", "debt_to_assets",
                       "gross_profit", "op_income", "net_income", "rd",
                       "cash", "equity", "stdebt", "total_debt", "net_debt",
                       "gross_margin", "op_margin", "net_margin",
                       "rd_intensity", "fcf_margin", "debt_to_equity",
                       "rev_yoy", "capex_yoy", "ni_yoy"):
            pts = [(d, quarters[d][metric]) for d in sorted(quarters)
                   if metric in quarters[d]]
            if pts:
                store.upsert_points(f"{prefix}:{ticker}:{metric}", pts)
        ends = sorted(quarters)
        last = quarters[ends[-1]]
        companies[ticker] = {
            "id": co["id"], "name": co["name"], "vertical": co["vertical"],
            "latest_quarter": ends[-1].isoformat(),
            "quarters_count": len(ends),
            "currency": res.get("currencies", {}).get("revenue", "USD"),
            "revenue": last.get("revenue"), "capex": last.get("capex"),
            "ocf": last.get("ocf"), "fcf": last.get("fcf"),
            "capex_intensity": last.get("capex_intensity"),
            "funding_gap": last.get("funding_gap"),
            "debt": last.get("debt"), "debt_to_assets": last.get("debt_to_assets"),
            # AI Financials three-statement snapshot (latest reported quarter)
            "gross_profit": last.get("gross_profit"),
            "op_income": last.get("op_income"),
            "net_income": last.get("net_income"), "rd": last.get("rd"),
            "cash": last.get("cash"), "equity": last.get("equity"),
            "total_debt": last.get("total_debt"),
            "net_debt": last.get("net_debt"),
            "gross_margin": last.get("gross_margin"),
            "op_margin": last.get("op_margin"),
            "net_margin": last.get("net_margin"),
            "rd_intensity": last.get("rd_intensity"),
            "fcf_margin": last.get("fcf_margin"),
            "debt_to_equity": last.get("debt_to_equity"),
            "rev_yoy": last.get("rev_yoy"), "capex_yoy": last.get("capex_yoy"),
            "ni_yoy": last.get("ni_yoy"),
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
