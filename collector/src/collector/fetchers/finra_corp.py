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

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from collector.fetchers.finra_dynarep import DynarepSession
from collector.store import Store

log = logging.getLogger(__name__)

# Static registry seed: CUSIPs evicted by the old REGISTRY_CAP=5000 policy.
# Restores their static attributes (issuer/coupon/maturity/ratings) so the
# registry is whole again; live fields (yield/price/spread_bps) repopulate
# when a bond reappears in the daily most-active lists.
SEED_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_cusip_registry.json"
_SEED_REGISTRY_CACHE: dict | None = None

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

def merge_bond_history(prev_hist: dict, lists: dict[str, list[dict]]
                       ) -> dict:
    """Accumulate per-CUSIP price/yield history from daily bond lists.

    `lists` is {iso_date: [bond, ...]} (all dates from this fetch).
    Keeps the FULL per-CUSIP history (no cap): every day ever seen stays
    in the doc, newest last. Returns the merged history dict
    {cusip: {"d": [...], "p": [...], "y": [...]}}.
    """
    hist: dict[str, dict] = {}
    if isinstance(prev_hist, dict):
        for cusip, h in prev_hist.items():
            if isinstance(h, dict):
                hist[cusip] = {
                    "d": list(h.get("d", [])),
                    "p": list(h.get("p", [])),
                    "y": list(h.get("y", [])),
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
    return hist


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


    return hist


# ---------------------------------------------------------------------------
# Refinancing wall: per-CUSIP registry + maturity x rating aggregation.
#
# The most-active lists are a *sample* of the bond universe (30 issues/day),
# but accumulated over time the registry grows into a useful panel of
# tracked issues. The wall is labeled as a sample everywhere it renders.
# Every CUSIP ever seen is kept (no cap).

# (label, min_years_inclusive, max_years_exclusive)
MAT_BUCKETS = (
    ("0-1Y", 0.0, 1.0),
    ("1-2Y", 1.0, 2.0),
    ("2-3Y", 2.0, 3.0),
    ("3-5Y", 3.0, 5.0),
    ("5-7Y", 5.0, 7.0),
    ("7-10Y", 7.0, 10.0),
    ("10Y+", 10.0, float("inf")),
)

# Moody's/S&P prefix -> bucket. First match wins.
_RATING_MAP = (
    (("aaa",), "AAA"),
    (("aa1", "aa2", "aa3", "aa+", "aa", "aa-"), "AA"),
    (("a1", "a2", "a3", "a+", "a", "a-"), "A"),
    (("baa1", "baa2", "baa3", "bbb+", "bbb", "bbb-"), "BBB"),
    (("ba1", "ba2", "ba3", "bb+", "bb", "bb-"), "BB"),
    (("b1", "b2", "b3", "b+", "b", "b-"), "B"),
    (("caa", "ca", "c", "ccc", "cc", "d"), "CCC"),
)


def rating_bucket(moodys: str | None, sp: str | None) -> str:
    """Map Moody's/S&P ratings to wall buckets (AAA..CCC, NR)."""
    for raw in (moodys or "", sp or ""):
        r = raw.strip().lower()
        if not r or r in ("nr", "na", "n/a", "none", "withdrawn", "wr"):
            continue
        for prefixes, bucket in _RATING_MAP:
            if any(r.startswith(p) for p in prefixes):
                return bucket
    return "NR"


def maturity_bucket_label(years: float | None) -> str | None:
    if years is None or years < 0:
        return None
    for label, lo, hi in MAT_BUCKETS:
        if lo <= years < hi:
            return label
    return None


def merge_cusip_registry(prev: dict | None,
                         lists: dict[str, list[dict]]) -> dict:
    """Accumulate per-CUSIP attributes across every fetch.

    Registry entry: issuer, coupon, maturity, moodys, sp, cat, yield,
    price, spread_bps (latest seen), last_seen. Every CUSIP ever seen is
    kept (no cap).
    """
    reg: dict[str, dict] = {}
    if isinstance(prev, dict):
        for cusip, e in prev.items():
            if isinstance(e, dict):
                reg[cusip] = dict(e)
    for day in sorted(lists):
        for b in lists[day]:
            cusip = b.get("symbol")
            if not cusip:
                continue
            e = reg.setdefault(cusip, {})
            for k in ("issuer", "coupon", "maturity", "moodys", "sp", "cat"):
                v = b.get(k)
                if v is not None and v != "":
                    e[k] = v
            if isinstance(b.get("yield"), (int, float)):
                e["yield"] = b["yield"]
            if isinstance(b.get("last"), (int, float)):
                e["price"] = b["last"]
            if isinstance(b.get("spread_bps"), (int, float)):
                e["spread_bps"] = b["spread_bps"]
            e["last_seen"] = day
    return reg


def _load_seed_registry() -> dict:
    """Seed registry (static attributes for previously-evicted CUSIPs)."""
    global _SEED_REGISTRY_CACHE
    if _SEED_REGISTRY_CACHE is None:
        try:
            _SEED_REGISTRY_CACHE = json.loads(SEED_REGISTRY_PATH.read_text())
        except OSError as exc:
            log.warning("seed registry unavailable (%s): %s",
                        SEED_REGISTRY_PATH, exc)
            _SEED_REGISTRY_CACHE = {}
    return _SEED_REGISTRY_CACHE


def restore_seed_registry(registry: dict) -> int:
    """Add seed CUSIPs missing from the registry (never overwrite).

    Restores the static attributes (issuer, coupon, maturity, moodys, sp,
    cat, last_seen) of bonds evicted by the old REGISTRY_CAP policy.
    Existing entries are left untouched — production holds fresher
    yield/price/spread_bps/last_seen for bonds still in the daily lists.
    Idempotent: after the first merge it is a no-op. Returns the number
    of entries added.
    """
    seed = _load_seed_registry()
    if not seed:
        return 0
    added = 0
    for cusip, s in seed.items():
        if cusip in registry or not isinstance(s, dict):
            continue
        registry[cusip] = {
            k: s[k] for k in ("issuer", "coupon", "maturity", "moodys",
                              "sp", "cat", "last_seen")
            if s.get(k) is not None and s.get(k) != ""
        }
        registry[cusip]["seed_restored"] = True
        added += 1
    return added


def build_refi_wall(registry: dict, asof: date) -> dict:
    """Aggregate the CUSIP registry into the refinancing-wall view.

    Returns {as_of, issues, maturities, ratings, buckets, yearly}.
    buckets[mat][rating] = {n, avg_coupon, avg_ytw, avg_spread_bps,
    refi_delta_bps}. yearly = [{year, ig, hy}] issue counts maturing
    per calendar year.
    """
    cells: dict[tuple[str, str], dict[str, list[float]]] = {}
    yearly: dict[int, dict[str, int]] = {}
    n_issues = 0
    for _cusip, e in (registry or {}).items():
        yrs = _years_to_maturity(str(e.get("maturity") or ""), asof)
        mb = maturity_bucket_label(yrs)
        if mb is None:
            continue
        rb = rating_bucket(e.get("moodys"), e.get("sp"))
        coupon = e.get("coupon")
        ytw = e.get("yield")
        if not isinstance(coupon, (int, float)) or not isinstance(ytw, (int, float)):
            continue
        if not (-10 <= ytw <= 30) or not (0 <= coupon <= 20):
            continue
        n_issues += 1
        cell = cells.setdefault((mb, rb), {"cpn": [], "ytw": [], "spr": []})
        cell["cpn"].append(coupon)
        cell["ytw"].append(ytw)
        spr = e.get("spread_bps")
        if isinstance(spr, (int, float)):
            cell["spr"].append(spr)
        try:
            my = date.fromisoformat(str(e.get("maturity"))[:10]).year
        except ValueError:
            continue
        if asof.year <= my <= asof.year + 10:
            y = yearly.setdefault(my, {"ig": 0, "hy": 0})
            y["hy" if rb in ("BB", "B", "CCC") else "ig"] += 1

    def _avg(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    buckets: dict[str, dict[str, dict]] = {}
    for (mb, rb), c in cells.items():
        ac, ay = _avg(c["cpn"]), _avg(c["ytw"])
        buckets.setdefault(mb, {})[rb] = {
            "n": len(c["cpn"]),
            "avg_coupon": round(ac, 2) if ac is not None else None,
            "avg_ytw": round(ay, 2) if ay is not None else None,
            "avg_spread_bps": round(_avg(c["spr"])) if c["spr"] else None,
            "refi_delta_bps": (round((ay - ac) * 100)
                               if ac is not None and ay is not None else None),
        }
    return {
        "as_of": asof.isoformat(),
        "issues": n_issues,
        "maturities": [m for m, _, _ in MAT_BUCKETS],
        "ratings": ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "NR"],
        "buckets": buckets,
        "yearly": [{"year": y, **yearly[y]} for y in sorted(yearly)],
    }

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
                       if k in ("as_of", "series", "bond_hist",
                                "cusip_registry", "refi_wall")}
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
    cusip_registry = merge_cusip_registry(prev_payload.get("cusip_registry"),
                                          all_lists)
    # Restore bonds evicted by the old REGISTRY_CAP policy (idempotent;
    # adds only CUSIPs missing from the registry).
    restored = restore_seed_registry(cusip_registry)
    if restored:
        log.info("finra_corp: restored %d seed registry entries", restored)
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
        "cusip_registry": cusip_registry,
        "refi_wall": build_refi_wall(cusip_registry, asof_d),
        "series": sorted(series),
    }, source=SOURCE)
    log.info("finra_corp: %d series, as_of %s", stored, as_of)
    return SOURCE
