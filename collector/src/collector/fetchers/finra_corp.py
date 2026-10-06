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

# Treasury par-yield curve for G-spread interpolation: (years, cycle id).
# DGS3/DGS5/DGS7/DGS20 series added to config 2026-10-05 for this.
TREASURY_CURVE = (
    (1 / 12, "us-1m-bill"),
    (0.5, "us-6m-bill"),
    (1.0, "us-1y-bill"),
    (2.0, "us-2y-yield"),
    (3.0, "us-3y-yield"),
    (5.0, "us-5y-yield"),
    (7.0, "us-7y-yield"),
    (10.0, "us10y"),
    (20.0, "us-20y-yield"),
    (30.0, "us-30y-yield"),
)

HIST_CAP = 400  # max per-CUSIP history entries kept in the doc


def treasury_yield_at(store, years: float, asof: date) -> float | None:
    """Interpolated Treasury par yield (%) at `years` to maturity.

    Linear interpolation between the nearest curve tenors, using each
    series' latest point on/before `asof`. Returns None when the curve
    can't bracket the point (missing data)."""
    pts: list[tuple[float, float]] = []
    for tenor, sid in TREASURY_CURVE:
        try:
            series = store.points(f"cycle:{sid}")
        except Exception:  # noqa: BLE001 — treat as missing
            continue
        avail = [d for d in series if d <= asof and series[d] is not None]
        if avail:
            pts.append((tenor, float(series[max(avail)])))
    if len(pts) < 2:
        return None
    if years <= pts[0][0]:
        return pts[0][1]
    if years >= pts[-1][0]:
        return pts[-1][1]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if t0 <= years <= t1:
            w = (years - t0) / (t1 - t0)
            return y0 + w * (y1 - y0)
    return None


def _years_to_maturity(maturity: str, asof: date) -> float | None:
    try:
        m = date.fromisoformat(maturity[:10])
    except (ValueError, TypeError):
        return None
    days = (m - asof).days
    return days / 365.25 if days > 0 else None


def _rating_composite(moodys: str | None, sp: str | None) -> str | None:
    m = (moodys or "").strip()
    s = (sp or "").strip()
    if m and s:
        return f"{m}/{s}" if m != s else m
    return m or s or None


def derive_bond_stats(bond: dict, store, asof: date) -> dict:
    """Per-bond derived stats for the most-active table.

    chg_pct: 1-day % price change from priceChangeAmount
    ytm_yrs: years to maturity
    rating:  Moody's/S&P composite (e.g. "Baa2/BBB")
    spread:  G-spread vs interpolated Treasury curve, in bps
             (None for convertibles / missing curve data — shown as "—")
    """
    last = bond.get("last")
    chg = bond.get("change")
    chg_pct = None
    if last is not None and chg is not None:
        prev = last - chg
        if prev:
            chg_pct = chg / prev * 100.0
    ytm_yrs = _years_to_maturity(bond.get("maturity") or "", asof)
    yld = bond.get("yield")
    spread = None
    if bond.get("cat") != "conv" and yld is not None and ytm_yrs:
        tsy = treasury_yield_at(store, ytm_yrs, asof)
        if tsy is not None:
            spread = (yld - tsy) * 100.0  # bps
    return {
        "chg_pct": chg_pct,
        "ytm_yrs": ytm_yrs,
        "rating": _rating_composite(bond.get("moodys"), bond.get("sp")),
        "spread_bps": spread,
    }


def _week52_stats(hist: dict) -> dict:
    """Trailing-252-entry high/low + distance from high for a bond history."""
    prices = (hist or {}).get("p") or []
    window = prices[-252:]
    if not window:
        return {"hi52": None, "lo52": None, "d52hi_pct": None}
    hi, lo = max(window), min(window)
    last = window[-1]
    return {
        "hi52": hi,
        "lo52": lo,
        "d52hi_pct": (last - hi) / hi * 100.0 if hi else None,
    }


def merge_bond_history(prev_hist: dict, lists: dict[str, list[dict]]
                       ) -> dict:
    """Accumulate per-CUSIP price/yield history from daily bond lists.

    `lists` is {iso_date: [bond, ...]} (all dates from this fetch).
    Keeps trailing HIST_CAP entries per CUSIP, FIFO. Returns the merged
    history dict {cusip: {"d": [...], "p": [...], "y": [...]}}.
    """
    hist: dict[str, dict] = {}
    if isinstance(prev_hist, dict):
        for cusip, h in prev_hist.items():
            if isinstance(h, dict):
                hist[cusip] = {
                    "d": list(h.get("d", []))[-HIST_CAP:],
                    "p": list(h.get("p", []))[-HIST_CAP:],
                    "y": list(h.get("y", []))[-HIST_CAP:],
                }
    for day in sorted(lists):
        for b in lists[day]:
            cusip = b.get("symbol")
            if not cusip:
                continue
            h = hist.setdefault(cusip, {"d": [], "p": [], "y": []})
            if h["d"] and h["d"][-1] >= day:
                continue  # already have this day (or newer)
            h["d"].append(day)
            h["p"].append(b.get("last"))
            h["y"].append(b.get("yield"))
            for k in ("d", "p", "y"):
                if len(h[k]) > HIST_CAP:
                    h[k] = h[k][-HIST_CAP:]
    return hist


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
    all_lists: dict[str, list[dict]] = {}
    for dslug, rows in all_rows.items():
        parsed, lists = parse_most_active(rows, dslug)
        for sid, pts in parsed.items():
            series.setdefault(sid, {}).update(pts)
        for day, bonds in lists.items():
            all_lists.setdefault(day, []).extend(bonds)
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
    # Per-bond history + derived stats (bond-specific table columns).
    # `lists` covers every date fetched (backfill or trailing-10d refresh);
    # merge into the rolling per-CUSIP history from the previous doc.
    prev = store.doc("finra_corp")
    prev_payload = prev.payload if prev and isinstance(prev.payload, dict) \
        else {}
    bond_hist = merge_bond_history(prev_payload.get("bond_hist") or {},
                                   all_lists)
    asof_d = date.fromisoformat(as_of)
    for dslug, info in latest_lists.items():
        for i, b in enumerate(info["bonds"]):
            stats = derive_bond_stats(b, store, asof_d)
            b.update(stats)
            b["rank"] = i + 1  # position in FINRA most-active list = vol rank
            b.update(_week52_stats(bond_hist.get(b.get("symbol"))))
    store.put_doc("finra_corp", {
        "as_of": as_of,
        "status": "ok",
        "lists": latest_lists,
        "bond_hist": bond_hist,
        "series": sorted(series),
    }, source=SOURCE)
    log.info("finra_corp: %d series, as_of %s", stored, as_of)
    return SOURCE
