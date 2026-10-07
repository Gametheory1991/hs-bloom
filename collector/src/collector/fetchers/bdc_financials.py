"""SEC EDGAR companyfacts XBRL fundamentals for listed BDCs (keyless).

BDCs file 10-K/10-Q as regulated investment companies. Tags were
discovered from live companyfacts payloads (ARCC/MAIN/BXSL verified
2026-10-07) — priority lists per metric, first hit wins; the concept
actually used is recorded per filer in the fundamentals doc.

Target metrics per BDC per quarter:
  balance sheet (instant): total assets, investments at fair value,
      debt outstanding, net assets (NAV), NAV per share, shares out
  income (quarterly): total investment income, net investment income
      (NII — the key BDC metric), distributions declared
  cash flow (quarterly): operating cash flow, financing cash flow
  exposure (derived): debt-to-equity, asset coverage ratio
      (reported InvestmentCompanySeniorSecurityIndebtednessAssetCoverageRatio
      when tagged, else estimated as assets/debt and labeled as such)

Extraction notes:
  * Instant concepts: earliest-filed value per period-end (XBRL repeats
    each period-end in later filings).
  * Flow concepts: 10-Q income statements carry both QTD (~90d) and YTD
    facts — prefer QTD; cash-flow statements and dividends are YTD-only,
    so those are quarterized by differencing (Q2 = H1 - Q1, etc.).
  * Values are as-filed USD; the accounting identity
    assets ~= liabilities + equity is checked per filer as a scale/
    sanity flag (identity_ok).
  * First-lien share is NOT XBRL-tagged by filers — not estimated.

Stored as quarterly series cycle:bdc-{T}-{metric} plus a fundamentals
doc bdc:{T}:fundamentals {tags used, latest values, asof, identity_ok}.
Weekly poll; filings land ~45 days after quarter-end.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from importlib.resources import files

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "sec-xbrl-bdc"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
PAUSE = 0.4  # SEC fair access: well under 10 req/sec

# metric -> priority-ordered us-gaap concepts (first hit wins)
INSTANT_CONCEPTS: dict[str, tuple[str, ...]] = {
    "assets": ("Assets",),
    "inv_fv": ("InvestmentOwnedAtFairValue", "InvestmentsAtFairValue",
               "InvestmentOwnedAtFairValueNet"),
    "debt": ("LongTermDebt", "LongTermDebtNoncurrent"),
    "nav": ("StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "navps": ("NetAssetValuePerShare",),
    "shares": ("CommonStockSharesOutstanding",
               "EntityCommonStockSharesOutstanding"),
    "coverage": ("InvestmentCompanySeniorSecurityIndebtednessAssetCoverageRatio",),
    "liabilities": ("Liabilities",),
}
FLOW_CONCEPTS: dict[str, tuple[str, ...]] = {
    "tii": ("GrossInvestmentIncomeOperating", "TotalInvestmentIncome",
            "InvestmentIncomeInterestAndDividend"),
    "nii": ("NetInvestmentIncome", "InvestmentIncomeNet"),
}
# YTD-only concepts -> quarterized by differencing
YTD_CONCEPTS: dict[str, tuple[str, ...]] = {
    "dist": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
             "PaymentsOfOrdinaryDividends", "DividendsCommonStockCash"),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "fcf": ("NetCashProvidedByUsedInFinancingActivities",),
}
# per-share distribution concepts -> total = per-share x shares outstanding
# (labeled derived:per-share-x-shares); used only when no total concept hits
DIST_PERSHARE_CONCEPTS = ("CommonStockDividendsPerShareDeclared",
                          "InvestmentCompanyDistributionToShareholdersPerShare")

SERIES_METRICS = ("assets", "inv_fv", "debt", "nav", "navps", "shares",
                  "tii", "nii", "dist", "ocf", "fcf", "coverage",
                  "debt_equity")


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _universe() -> list[dict]:
    """BDC nodes (ticker, cik) from the vendored private_credit universe."""
    raw = (files("collector") / "data" / "private_credit.json").read_text(
        encoding="utf-8")
    u = json.loads(raw)
    out = []
    for v in u.get("verticals", []):
        if v.get("id") != "listed_bdcs":
            continue
        for c in v.get("companies", []):
            if c.get("ticker") and c.get("cik"):
                out.append({"ticker": c["ticker"], "cik": c["cik"],
                            "name": c["name"], "id": c["id"]})
    return out


def _iter_facts(facts: dict, concept: str):
    entry = (facts.get("us-gaap") or {}).get(concept)
    if not entry:
        return
    for unit_pts in entry.get("units", {}).values():
        for p in unit_pts:
            yield p


def _end(p) -> date | None:
    try:
        return date.fromisoformat(str(p.get("end"))[:10])
    except (TypeError, ValueError):
        return None


def _dur_days(p) -> int | None:
    s = p.get("start")
    e = _end(p)
    if not s or e is None:
        return None
    try:
        return (e - date.fromisoformat(str(s)[:10])).days
    except (TypeError, ValueError):
        return None


def _instant_points(facts: dict, concepts: tuple[str, ...]
                    ) -> tuple[list[tuple[date, float]], str | None]:
    """Earliest-filed value per period-end for instant (no-start) facts."""
    for concept in concepts:
        best: dict[date, tuple[str, float]] = {}
        for p in _iter_facts(facts, concept):
            if p.get("start"):
                continue
            end = _end(p)
            val = _f(p.get("val"))
            if end is None or val is None:
                continue
            filed = str(p.get("filed") or "")
            if end not in best or filed < best[end][0]:
                best[end] = (filed, val)
        if best:
            return sorted((d, v) for d, (_, v) in best.items()), concept
    return [], None


def _qtd_points(facts: dict, concepts: tuple[str, ...]
                ) -> tuple[list[tuple[date, float]], str | None]:
    """Quarterly (~90d duration) flow facts, earliest-filed per end."""
    for concept in concepts:
        best: dict[date, tuple[str, float]] = {}
        for p in _iter_facts(facts, concept):
            dur = _dur_days(p)
            if dur is None or not 75 <= dur <= 100:
                continue
            end = _end(p)
            val = _f(p.get("val"))
            if end is None or val is None:
                continue
            filed = str(p.get("filed") or "")
            if end not in best or filed < best[end][0]:
                best[end] = (filed, val)
        if best:
            return sorted((d, v) for d, (_, v) in best.items()), concept
    return [], None


def _ytd_points(facts: dict, concepts: tuple[str, ...]
                ) -> tuple[dict[date, tuple[int, float]], str | None]:
    """YTD facts -> {end: (band, value)}; bands: 1=Q1,2=H1,3=9M,4=FY."""
    bands = ((80, 95, 1), (170, 190, 2), (265, 285, 3), (355, 375, 4))
    for concept in concepts:
        best: dict[date, tuple[str, int, float]] = {}
        for p in _iter_facts(facts, concept):
            dur = _dur_days(p)
            if dur is None:
                continue
            band = next((b for lo, hi, b in bands if lo <= dur <= hi), None)
            if band is None:
                continue
            end = _end(p)
            val = _f(p.get("val"))
            if end is None or val is None:
                continue
            filed = str(p.get("filed") or "")
            if end not in best or filed < best[end][0]:
                best[end] = (filed, band, val)
        if best:
            return {d: (b, v) for d, (_, b, v) in best.items()}, concept
    return {}, None


def _fy_month(facts: dict) -> int:
    """Fiscal year-end month from FY facts; default December."""
    for p in _iter_facts(facts, "Assets"):
        if p.get("fp") == "FY":
            end = _end(p)
            if end:
                return end.month
    return 12


def _fy_group(end: date, fy_month: int) -> int:
    return end.year if end.month <= fy_month else end.year + 1


def quarterize_ytd(ytd: dict[date, tuple[int, float]],
                   fy_month: int) -> list[tuple[date, float]]:
    """YTD {(end): (band, value)} -> quarterly points by differencing."""
    out: list[tuple[date, float]] = []
    ends = sorted(ytd)
    for e in ends:
        band, val = ytd[e]
        if band == 1:
            out.append((e, val))
            continue
        prev = None
        for e2 in ends:
            if e2 >= e:
                break
            b2, _ = ytd[e2]
            if b2 == band - 1 and _fy_group(e2, fy_month) == _fy_group(e, fy_month):
                prev = e2
        if prev is not None:
            out.append((e, val - ytd[prev][1]))
    return sorted(out)


def extract_fundamentals(payload: dict) -> dict:
    """{metric: (points, concept_used)} for one companyfacts payload."""
    facts = payload.get("facts") or {}
    fy_month = _fy_month(facts)
    res: dict[str, tuple[list[tuple[date, float]], str | None]] = {}
    for metric, concepts in INSTANT_CONCEPTS.items():
        res[metric] = _instant_points(facts, concepts)
    # scale guard: NAV/share occasionally tagged in mills (no BDC has
    # NAV/share > $1,000)
    if res["navps"][0] and max(v for _, v in res["navps"][0]) > 1000:
        res["navps"] = ([(e, v / 1000) for e, v in res["navps"][0]],
                        res["navps"][1] + ":mills")
    for metric, concepts in FLOW_CONCEPTS.items():
        pts, used = _qtd_points(facts, concepts)
        if not pts:
            ytd, used = _ytd_points(facts, concepts)
            pts = quarterize_ytd(ytd, fy_month) if ytd else []
        res[metric] = (pts, used)
    for metric, concepts in YTD_CONCEPTS.items():
        ytd, used = _ytd_points(facts, concepts)
        res[metric] = (quarterize_ytd(ytd, fy_month) if ytd else [], used)
    # distributions: per-share fallback -> total via shares outstanding
    if not res["dist"][0]:
        per_share, ps_used = _qtd_points(facts, DIST_PERSHARE_CONCEPTS)
        if not per_share:
            ytd, ps_used = _ytd_points(facts, DIST_PERSHARE_CONCEPTS)
            per_share = quarterize_ytd(ytd, fy_month) if ytd else []
        # scale guard: some filers tag per-share amounts in mills
        # (e.g. 400 = $0.40); no BDC pays >$10/share quarterly
        per_share = [(e, v / 1000 if v > 10 else v) for e, v in per_share]
        shares_pts, _ = _instant_points(facts, INSTANT_CONCEPTS["shares"])
        shares_by_q = dict(shares_pts)
        derived = [(e, v * shares_by_q[e]) for e, v in per_share
                   if e in shares_by_q]
        if derived:
            res["dist"] = (derived, f"derived:{ps_used}-x-shares")
    return res


def _latest(pts: list[tuple[date, float]]) -> tuple[date, float] | None:
    return pts[-1] if pts else None


async def fetch_bdc_financials(store: Store, get_text: GetText,
                               user_agent: str) -> str:
    """Pull quarterly XBRL fundamentals for every listed BDC."""
    universe = _universe()
    headers = {"User-Agent": user_agent}
    done = fell = 0
    pts_total = 0
    missing: dict[str, list[str]] = {}
    for b in universe:
        t = b["ticker"]
        try:
            payload = json.loads(await get_text(
                FACTS_URL.format(cik10=b["cik"]), headers=headers))
        except Exception as exc:  # noqa: BLE001 — per-BDC isolation
            log.warning("bdc_financials: %s failed: %s", t, exc)
            fell += 1
            continue
        res = extract_fundamentals(payload)
        pts = {m: r[0] for m, r in res.items()}
        tags = {m: r[1] for m, r in res.items() if r[1]}

        # derived exposure metrics
        assets, debt, nav = (dict(pts["assets"]), dict(pts["debt"]),
                            dict(pts["nav"]))
        common_ends = sorted(set(assets) & set(debt) & set(nav))
        de_pts = [(e, debt[e] / nav[e]) for e in common_ends
                  if nav[e]]
        pts["debt_equity"] = de_pts
        tags["debt_equity"] = "derived:debt/nav"
        cov_reported = pts["coverage"]
        # prefer reported coverage only when it is current (within ~1
        # quarter of the latest balance sheet); otherwise the filer
        # stopped tagging it and the derived estimate is fresher
        latest_bs = max(common_ends) if common_ends else None
        cov_current = (cov_reported and latest_bs and
                       (latest_bs - cov_reported[-1][0]).days <= 100)
        if not cov_current:
            cov_est = [(e, assets[e] / debt[e]) for e in common_ends
                       if debt[e]]
            pts["coverage"] = cov_est
            cov_estimated = True
        else:
            cov_estimated = False
        tags["coverage"] = (tags.get("coverage")
                            if cov_current else "derived:assets/debt")

        # accounting-identity sanity check on the latest quarter
        la = _latest(pts["assets"])
        liab = _latest(pts.get("liabilities", []))
        ln = _latest(pts["nav"])
        identity_ok = (la and liab and ln and la[0] == liab[0] == ln[0]
                       and abs(la[1] - (liab[1] + ln[1])) / max(abs(la[1]), 1)
                       < 0.05)

        for metric in SERIES_METRICS:
            if pts.get(metric):
                store.upsert_points(f"cycle:bdc-{t}-{metric}", pts[metric])
                pts_total += len(pts[metric])
            else:
                missing.setdefault(t, []).append(metric)

        latest_vals = {}
        for metric in SERIES_METRICS:
            lp = _latest(pts.get(metric, []))
            if lp:
                latest_vals[metric] = {"asof": lp[0].isoformat(),
                                       "value": lp[1]}
        asof = max((v["asof"] for v in latest_vals.values()), default=None)
        store.put_doc(f"bdc:{t}:fundamentals", {
            "ticker": t, "name": b["name"], "cik": b["cik"],
            "asof": asof, "tags": tags, "latest": latest_vals,
            "identity_ok": bool(identity_ok),
            "coverage_estimated": cov_estimated,
            "note": ("SEC XBRL 10-Q/10-K companyfacts; ~45-day filing lag. "
                     "First-lien share is not XBRL-tagged by filers."),
            "source": SOURCE,
        }, SOURCE)
        done += 1
        await asyncio.sleep(PAUSE)
    return (f"bdc_financials: {done}/{len(universe)} BDCs, {pts_total} "
            f"quarterly points, {fell} failed; "
            f"missing metrics: {missing or 'none'}")
