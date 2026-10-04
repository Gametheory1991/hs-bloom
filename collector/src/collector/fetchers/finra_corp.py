"""FINRA most-active corporate bonds (keyless public dynarep API).

Harry asked for: https://www.finra.org/finra-data/fixed-income/market-corp
("Most Active Corporate Bonds" — the ten most active investment-grade,
high-yield and convertible corporate bonds, published each market day).

Same public dynarep API as the breadth/sentiment pages; session handling
lives in finra_dynarep.py (no auth, XSRF-TOKEN cookie + browser UA):

  MostActiveCorporateSecurities       (regular corporate bonds)
  MostActiveCorporate144ASecurities   (144A corporate bonds)

Dataset/template IDs were read from the page's app-dynamic-reporting.js
bundle (verified 2026-10-04). History starts 2023-02-15 (verified live);
published daily. Each market day carries 30 rows: 10 IG + 10 HY + 10
convertibles (securityTypeCode inv/hy/conv).

Stored as per-category daily averages —
  cycle:finra-corp-{ig,hy,conv}-{avgyield,avgprice,avgchg}
  cycle:finra-corp144a-{ig,hy,conv}-{avgyield,avgprice,avgchg}
— plus a "finra_corp" snapshot doc holding the latest day's full bond
lists (issuer, coupon, maturity, ratings, price, yield) for the dashboard.

Sanity filters on the averages: convertible prints occasionally carry
nonsense yields/prices (e.g. -84% yield, $1050 price), so yields are
averaged over [-10, 30] and prices over [5, 500]; rows failing the price
filter are excluded from all three metrics.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from collector.fetchers.finra_dynarep import DynarepSession
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "finra-corp-most-active"
HISTORY_START = date(2023, 2, 15)  # earliest date with data (verified live)

DATASETS = {
    "corp": "MostActiveCorporateSecurities",
    "corp144a": "MostActiveCorporate144ASecurities",
}
# (slug, securityTypeCode)
CATEGORIES = (("ig", "inv"), ("hy", "hy"), ("conv", "conv"))
FIELDS = ["issuerName", "issueSymbolIdentifier", "bondType", "couponRate",
          "maturityDate", "moodysRating", "standardAndPoorsRating",
          "highPrice", "lowPrice", "lastPrice", "priceChangeAmount",
          "yieldPercent", "reportDate", "securityTypeCode"]

METRICS = ("avgyield", "avgprice", "avgchg")


def corp_series_ids() -> list[str]:
    ids: list[str] = []
    for dslug in DATASETS:
        for cslug, _ in CATEGORIES:
            for m in METRICS:
                ids.append(f"finra-{dslug}-{cslug}-{m}")
    return ids


def _fnum(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f


def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def parse_most_active(rows: list[dict], dslug: str
                      ) -> tuple[dict[str, dict[date, float]],
                                 dict[str, list[dict]]]:
    """Pivot most-active rows -> ({series_id: {date: value}}, bond lists).

    Returns per-category daily mean yield / mean price / mean price-change
    plus the raw bond lists keyed by date (for the snapshot doc).
    """
    by_cat: dict[tuple[str, date], dict[str, list[float]]] = {}
    lists: dict[str, list[dict]] = {}
    for r in rows:
        cat = next((c for c, code in CATEGORIES
                    if code == r.get("securityTypeCode")), None)
        if cat is None:
            continue
        try:
            asof = _parse_date(str(r["reportDate"]))
        except (KeyError, ValueError):
            continue
        price = _fnum(r.get("lastPrice"))
        yld = _fnum(r.get("yieldPercent"))
        chg = _fnum(r.get("priceChangeAmount"))
        if price is None or not (5 <= price <= 500):
            continue  # bad print -> skip row entirely
        key = (cat, asof)
        bucket = by_cat.setdefault(key, {"y": [], "p": [], "c": []})
        bucket["p"].append(price)
        if chg is not None:
            bucket["c"].append(chg)
        if yld is not None and -10 <= yld <= 30:
            bucket["y"].append(yld)
        lists.setdefault(asof.isoformat(), []).append({
            "issuer": r.get("issuerName"),
            "symbol": r.get("issueSymbolIdentifier"),
            "cat": cat,
            "coupon": _fnum(r.get("couponRate")),
            "maturity": str(r.get("maturityDate") or "")[:10],
            "moodys": r.get("moodysRating"),
            "sp": r.get("standardAndPoorsRating"),
            "last": price,
            "change": chg,
            "yield": yld,
        })
    out: dict[str, dict[date, float]] = {}
    for (cat, asof), b in by_cat.items():
        if b["y"]:
            out.setdefault(f"finra-{dslug}-{cat}-avgyield", {})[asof] = \
                sum(b["y"]) / len(b["y"])
        out.setdefault(f"finra-{dslug}-{cat}-avgprice", {})[asof] = \
            sum(b["p"]) / len(b["p"])
        if b["c"]:
            out.setdefault(f"finra-{dslug}-{cat}-avgchg", {})[asof] = \
                sum(b["c"]) / len(b["c"])
    return out, lists


async def fetch_finra_corp(store: Store,
                           today: date | None = None,
                           session_factory=DynarepSession) -> str:
    """Daily job: FINRA most-active corporate bonds (corp + 144A).

    Backfills to 2023-02-15 on an empty store; otherwise refreshes the
    trailing 10 days (catches late revisions)."""
    today = today or date.today()
    prev = store.doc("finra_corp")
    have = store.points("cycle:finra-corp-ig-avgyield")
    start = HISTORY_START if not have else today - timedelta(days=10)
    try:
        async with session_factory() as sess:
            all_rows: dict[str, list[dict]] = {}
            for dslug, dataset in DATASETS.items():
                all_rows[dslug] = await sess.query(dataset, FIELDS,
                                                  start, today)
    except Exception as exc:  # noqa: BLE001 — record + retry tomorrow
        payload: dict = {}
        if prev and isinstance(prev.payload, dict):
            payload = {k: v for k, v in prev.payload.items()
                       if k in ("as_of", "series")}
        payload["status"] = "error"
        payload["error"] = str(exc)[:300]
        store.put_doc("finra_corp", payload, source=SOURCE)
        raise RuntimeError(f"finra_corp fetch failed: {exc}") from exc
    if not any(all_rows.values()):
        raise RuntimeError("finra_corp: empty response from FINRA dynarep")
    series: dict[str, dict[date, float]] = {}
    latest_lists: dict[str, dict] = {}
    latest_day = ""
    for dslug, rows in all_rows.items():
        parsed, lists = parse_most_active(rows, dslug)
        for sid, pts in parsed.items():
            series.setdefault(sid, {}).update(pts)
        for day, bonds in lists.items():
            if day > latest_day:
                latest_day = day
        # keep each dataset's latest day list under its key
        if lists:
            day = max(lists)
            latest_lists[dslug] = {"as_of": day, "bonds": lists[day]}
    stored = 0
    for sid, pts in series.items():
        if pts:
            store.upsert_points(f"cycle:{sid}", sorted(pts.items()))
            stored += 1
    as_of = max((d for pts in series.values() for d in pts),
                default=today).isoformat()
    store.put_doc("finra_corp", {
        "as_of": as_of,
        "status": "ok",
        "lists": latest_lists,
        "series": sorted(series),
    }, source=SOURCE)
    log.info("finra_corp: %d series, as_of %s", stored, as_of)
    return SOURCE
