"""Prediction-market edge engine — cross-venue spreads, staleness, calibration.

Compute job (plus a bounded number of resolution-check HTTP calls). Reads
the `polymarket` and `kalshi` snapshot docs written by the venue fetchers
and the per-market Yes-price histories (cycle:pm-*/cycle:kal-*), then
writes a `pred_edge` doc for the PREDICT tab.

Signals (all labeled as model estimates, never guarantees):
  * cross-venue spread: same-event markets on Polymarket vs Kalshi whose
    Yes prices diverge by more than a rough round-trip cost estimate
    (FEE_BUFFER). The classic structural edge — two venues, one event.
  * stale-price: a market whose price has not moved for >= STALE_RUNS runs
    while its matched venue's price has moved.
  * mispricing score (0-100): spread magnitude + staleness + move
    divergence, gated by liquidity (illiquid books are not tradable, so
    their "edge" scores 0).
  * calibration leaderboard: for markets we tracked that have since
    resolved, score implied probability vs outcome with the Brier score,
    bucketed by venue x category. This is how the engine keeps itself
    honest over time.

Honesty notes: fees are estimates (Kalshi taker fees vary by price;
Polymarket books have spread/slippage); cross-venue "arbitrage" usually
isn't risk-free (different settlement rules, timing, and fees apply).
Most prediction-market accounts lose money — the dashboard says so.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "pred-edge-engine"

# --- tuning constants (documented estimates, not guarantees) ---
FEE_BUFFER = 0.04        # rough round-trip cost: Kalshi taker fees + spreads/slippage
SPREAD_FLAG = 0.05       # surface divergences >= 5c even before the fee gate
MIN_POLY_LIQUIDITY = 2000.0   # USD book liquidity for a "tradable" Polymarket leg
MIN_KAL_ACTIVITY = 500.0      # USD 24h vol or open interest for a "tradable" Kalshi leg
STALE_RUNS = 3           # unchanged runs before a market counts as stale
MAX_RESOLUTION_CHECKS = 10    # bounded HTTP per run for settled-market lookups
MAX_TRACKED = 300        # cap on tracked-market memory

GAMMA = "https://gamma-api.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

DISCLAIMER = ("Model edge estimates, not guarantees. Cross-venue spreads "
              "are not risk-free (fees, settlement rules, timing). Most "
              "prediction-market accounts lose money.")

STOPWORDS = frozenset(
    "a an the will be is are was were by in on of to for and or will s".split())

CATEGORY_KEYWORDS = {
    "politics": ("election", "president", "senate", "congress", "vote", "referendum",
                 "mayor", "governor", "parliament", "nominee", "ballot"),
    "sports": ("game", "match", "nba", "nfl", "mlb", "nhl", "championship",
               "playoff", "world cup", "super bowl", " vs ", "tournament"),
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "doge",
               "xrp", "token"),
    "econ": ("fed", "rate", "cpi", "inflation", "gdp", "recession",
             "unemployment", "payrolls", "fomc", "treasury", "tariff"),
}


def categorize(text: str) -> str:
    t = (text or "").lower()
    for cat, words in CATEGORY_KEYWORDS.items():
        if any(w in t for w in words):
            return cat
    return "other"


# Curated cross-venue match rules. Each rule names substrings that identify
# the same event on both venues (or a Kalshi series prefix); the engine then
# picks the highest token-overlap pair. Month-agnostic on purpose — specific
# meetings roll, the rule keeps matching the front contract.
MATCH_RULES = [
    {"id": "fed-policy", "label": "Fed policy decision",
     "poly": ("fed",), "kalshi_series": "FEDHIKE", "kalshi": ()},
    {"id": "cpi-print", "label": "CPI inflation print",
     "poly": ("cpi",), "kalshi_series": "KXCPI", "kalshi": ()},
    {"id": "btc-price", "label": "Bitcoin price level",
     "poly": ("bitcoin",), "kalshi_series": "BTCD", "kalshi": ()},
    {"id": "eth-price", "label": "Ethereum price level",
     "poly": ("ethereum",), "kalshi_series": None, "kalshi": ("ethereum",)},
    {"id": "recession", "label": "US recession",
     "poly": ("recession",), "kalshi_series": None, "kalshi": ("recession",)},
    {"id": "election", "label": "Election outcome",
     "poly": ("election",), "kalshi_series": None, "kalshi": ("election",)},
]


def _tokens(text: str) -> set[str]:
    return {w for w in
            "".join(c.lower() if c.isalnum() else " " for c in text).split()
            if len(w) > 3 and w not in STOPWORDS}


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_pairs(poly_rows: list[dict], kal_rows: list[dict]) -> list[tuple[dict, dict, dict]]:
    """Apply MATCH_RULES; return (rule, poly_row, kal_row) for the best pair."""
    pairs = []
    for rule in MATCH_RULES:
        p_cands = [r for r in poly_rows
                   if all(s in (r.get("question") or "").lower() for s in rule["poly"])]
        k_cands = [r for r in kal_rows
                   if (rule["kalshi_series"] and
                       (r.get("ticker") or "").startswith(rule["kalshi_series"]))
                   or (rule["kalshi"] and
                       all(s in (r.get("title") or "").lower() for s in rule["kalshi"]))]
        best = None
        for pr in p_cands:
            for kr in k_cands:
                ov = _overlap(pr.get("question", ""), kr.get("title", ""))
                if best is None or ov > best[0]:
                    best = (ov, pr, kr)
        if best and best[0] > 0:
            pairs.append((rule, best[1], best[2]))
    return pairs


def implied(row: dict) -> float | None:
    """Best Yes-probability estimate from a normalized market row:
    book mid (either venue's field names), else last price, else quote."""
    for bk, ak in (("best_bid", "best_ask"), ("yes_bid", "yes_ask")):
        b, a = row.get(bk), row.get(ak)
        if b is not None and a is not None and a >= b:
            return (b + a) / 2
    for k in ("last_price", "yes_price", "last_trade", "yes_bid", "yes_ask"):
        v = row.get(k)
        if v is not None:
            return v
    return None


def staleness(points: dict) -> int:
    """Trailing runs with an (effectively) unchanged price."""
    if not points:
        return 0
    vals = [v for _, v in sorted(points.items())]
    last = vals[-1]
    n = 0
    for v in reversed(vals[:-1]):
        if abs(v - last) < 1e-9:
            n += 1
        else:
            break
    return n


def mispricing_score(spread: float, stale_runs: int, move_div: float | None,
                     liquid: bool) -> float:
    """0-100 composite. Illiquid books score 0: the edge isn't tradable."""
    if not liquid:
        return 0.0
    s = min(abs(spread) / 0.20, 1.0) * 40.0
    s += min(stale_runs / 6.0, 1.0) * 30.0
    if move_div is not None:
        s += min(abs(move_div) / 0.10, 1.0) * 30.0
    return round(s, 1)


def brier(pairs: list[tuple[float, float]]) -> float | None:
    """Mean squared error of implied probability vs binary outcome."""
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


async def _poly_resolved(get_text: GetText, slug: str) -> bool | None:
    """True/False if a Polymarket market resolved Yes/No; None if unknown."""
    try:
        body = json.loads(await get_text(f"{GAMMA}/markets?slug={slug}"))
    except Exception:  # noqa: BLE001 — network issues are "unknown", not resolved
        return None
    if not body or not isinstance(body, list):
        return None
    m = body[0]
    if not m.get("closed"):
        return None
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(prices, list) or len(prices) < 2:
        return None
    try:
        yes, no = float(prices[0]), float(prices[1])
    except (TypeError, ValueError):
        return None
    if yes == 1.0 and no == 0.0:
        return True
    if yes == 0.0 and no == 1.0:
        return False
    return None


async def _kalshi_resolved(get_text: GetText, ticker: str) -> bool | None:
    """True/False if a Kalshi market settled Yes/No; None if unknown."""
    try:
        body = json.loads(await get_text(f"{KALSHI}/markets/{ticker}"))
    except Exception:  # noqa: BLE001
        return None
    m = body.get("market", body) if isinstance(body, dict) else {}
    if m.get("status") not in ("settled", "closed"):
        return None
    r = (m.get("result") or "").lower()
    if r == "yes":
        return True
    if r == "no":
        return False
    return None


def _track_key(venue: str, row: dict) -> str:
    return f"{venue}:{row.get('key') or row.get('ticker') or row.get('slug')}"


async def fetch_pred_edge(store: Store, get_text: GetText) -> str:
    poly_doc = store.doc("polymarket")
    kal_doc = store.doc("kalshi")
    poly_rows = (poly_doc.payload.get("markets") or []) if poly_doc else []
    kal_rows = (kal_doc.payload.get("markets") or []) if kal_doc else []
    skipped = []
    if not poly_rows:
        skipped.append("polymarket snapshot missing")
    if not kal_rows:
        skipped.append("kalshi snapshot missing")

    prev = store.doc("pred_edge")
    prev_p = prev.payload if prev else {}
    tracked: dict = dict(prev_p.get("tracked") or {})
    calib: dict = json.loads(json.dumps(prev_p.get("calibration") or {}))

    # --- 1. cross-venue spreads ---
    edges = []
    for rule, pr, kr in match_pairs(poly_rows, kal_rows):
        pp, kp = implied(pr), implied(kr)
        if pp is None or kp is None:
            continue
        spread = pp - kp
        edge_est = abs(spread) - FEE_BUFFER
        liquid = ((pr.get("liquidity") or 0) >= MIN_POLY_LIQUIDITY
                  and ((kr.get("volume24h") or 0) >= MIN_KAL_ACTIVITY
                       or (kr.get("open_interest") or 0) >= MIN_KAL_ACTIVITY))
        stale_p = staleness(store.points(f"cycle:pm-{pr.get('key')}-yes"))
        stale_k = staleness(store.points(f"cycle:kal-{kr.get('key')}-yes"))
        mv_p, mv_k = pr.get("chg_1d"), None  # kalshi has no 1d-change field
        move_div = (mv_p - 0) if mv_p is not None else None
        score = mispricing_score(spread, max(stale_p, stale_k), move_div, liquid)
        if abs(spread) >= SPREAD_FLAG or score >= 20:
            edges.append({
                "rule": rule["id"], "label": rule["label"],
                "poly_q": pr.get("question"), "poly_yes": round(pp, 4),
                "poly_url": pr.get("url"),
                "kalshi_title": kr.get("title"), "kalshi_yes": round(kp, 4),
                "kalshi_url": kr.get("url"),
                "spread": round(spread, 4),
                "edge_estimate": round(edge_est, 4),
                "tradable_estimate": bool(edge_est > 0.01 and liquid),
                "stale_runs_poly": stale_p, "stale_runs_kalshi": stale_k,
                "mispricing_score": score,
                "note": "Model edge estimate, not a guarantee.",
            })
    edges.sort(key=lambda e: e["mispricing_score"], reverse=True)

    # --- 2. unusual movers per venue ---
    movers = []
    for row in poly_rows:
        c = row.get("chg_1d")
        if c is not None and abs(c) >= 0.05:
            movers.append({"venue": "polymarket", "label": row.get("question"),
                           "yes": implied(row), "chg_1d": round(c, 4),
                           "volume24h": row.get("volume24h"), "url": row.get("url")})
    movers.sort(key=lambda m: abs(m["chg_1d"]), reverse=True)
    movers = movers[:15]

    # --- 3. calibration: resolve tracked markets that left the snapshots ---
    live_keys = {_track_key("pm", r) for r in poly_rows} | {_track_key("kal", r) for r in kal_rows}
    for r in poly_rows:
        tkey = _track_key("pm", r)
        if tkey not in tracked:
            tracked[tkey] = {"venue": "polymarket", "ref": r.get("slug"),
                             "label": (r.get("question") or "")[:120],
                             "implied": implied(r),
                             "category": categorize(r.get("question")),
                             "first_seen": date.today().isoformat()}
    for r in kal_rows:
        tkey = _track_key("kal", r)
        if tkey not in tracked:
            tracked[tkey] = {"venue": "kalshi", "ref": r.get("ticker"),
                             "label": (r.get("title") or "")[:120],
                             "implied": implied(r),
                             "category": categorize(r.get("title")),
                             "first_seen": date.today().isoformat()}
    # prune oldest if over cap
    if len(tracked) > MAX_TRACKED:
        for tkey in sorted(tracked, key=lambda k: tracked[k]["first_seen"])[:len(tracked) - MAX_TRACKED]:
            del tracked[tkey]

    resolved_this_run = 0
    checks = 0
    for tkey in list(tracked):
        if tkey in live_keys or checks >= MAX_RESOLUTION_CHECKS:
            continue
        t = tracked[tkey]
        outcome = (await _poly_resolved(get_text, t["ref"]) if t["venue"] == "polymarket"
                   else await _kalshi_resolved(get_text, t["ref"]))
        checks += 1
        if outcome is None:
            continue  # still pending or lookup failed; keep tracking
        bucket = calib.setdefault(t["venue"], {}).setdefault(
            t["category"], {"n": 0, "brier_sum": 0.0, "wins": 0, "implied_sum": 0.0})
        p = t["implied"] if t["implied"] is not None else 0.5
        bucket["n"] += 1
        bucket["brier_sum"] += (p - float(outcome)) ** 2
        bucket["wins"] += int(outcome)
        bucket["implied_sum"] += p
        del tracked[tkey]
        resolved_this_run += 1

    leaderboard = []
    for venue, cats in calib.items():
        for cat, b in cats.items():
            if not b["n"]:
                continue
            leaderboard.append({
                "venue": venue, "category": cat, "n": b["n"],
                "brier": round(b["brier_sum"] / b["n"], 4),
                "win_rate": round(b["wins"] / b["n"], 3),
                "avg_implied": round(b["implied_sum"] / b["n"], 3),
            })
    leaderboard.sort(key=lambda r: r["n"], reverse=True)

    store.put_doc("pred_edge", {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "edges": edges[:25],
        "movers": movers,
        "calibration": leaderboard,
        "tracked": tracked,
        "tracked_count": len(tracked),
        "resolved_this_run": resolved_this_run,
        "coverage": {"polymarket": len(poly_rows), "kalshi": len(kal_rows)},
        "skipped": skipped,
        "disclaimer": DISCLAIMER,
    }, source=SOURCE)
    log.info("pred_edge: %d edges, %d movers, %d calibrated buckets, %d tracked",
             len(edges), len(movers), len(leaderboard), len(tracked))
    return SOURCE
