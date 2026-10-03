"""Cross-asset correlation matrix + realized volatility (zero-HTTP compute job).

Reads price histories already in the store (store keys verified against the
real fetcher code on live main: `idx:<SYM>` in fetchers/equity.py,
`yield:<CC><TENOR>` in fetchers/bonds.py, `cycle:<id>` in fetchers/cycle.py)
and writes:

  doc "xcorr":
    asof, universe labels, 60d and 252d Pearson correlation matrices,
    six regime-relevant pairs (60d/252d corr, percentile of the 60d corr vs
    its own history, extreme flag at |corr| >= 0.7), realized-vol table.
  history "xcorr:<pair>"   one point per run (60d corr of the regime pair)
  history "rvol:<key>:21d" / "rvol:<key>:63d"  annualized realized vol, %

Method: per series, daily changes (log returns x100 for prices and vol
indexes, simple diffs for yields/spreads in native units). Correlations are
pairwise-complete over common dates within the window (60/252 observations);
a pair needs >=20 (>=60 for the 252d window) common observations or it is
skipped. Everything degrades gracefully: a missing series is skipped, never
zero-filled, and one bad input never fails the job.

The matrices and the regime pairs are consumed by the /api/dashboard xcorr
panel (X-CORR tab) and the /api/insights digest (extreme pairs are hashed
into digest_id so the newsletter fires when the correlation regime flips).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from statistics import correlation, pstdev

from collector.store import Store

SOURCE = "xcorr-compute"
DOC_KEY = "xcorr"

WIN_SHORT = 60
WIN_LONG = 252
MIN_OBS_SHORT = 20
MIN_OBS_LONG = 60
EXTREME_AT = 0.7  # |corr_60d| at/above this is flagged as an extreme regime

# (store key, label, change kind). Kinds: "logret" for prices/vol indexes,
# "diff" for yields and spreads (native units: pp for %, idx points for VIX).
UNIVERSE: list[tuple[str, str, str]] = [
    ("idx:SPX", "SPX", "logret"),
    ("idx:NDX", "NDX", "logret"),
    ("idx:SX5E", "SX5E", "logret"),
    ("idx:NKX", "Nikkei", "logret"),
    ("yield:US10Y", "US 10Y", "diff"),
    ("yield:DE10Y", "DE 10Y", "diff"),
    ("cycle:vix", "VIX", "logret"),
    ("cycle:vix3m", "VIX3M", "logret"),
    ("cycle:ig-oas", "IG OAS", "diff"),
    ("cycle:hy-oas", "HY OAS", "diff"),
    ("cycle:usd-broad", "USD broad", "logret"),
    ("cycle:eur-usd", "EUR/USD", "logret"),
    ("cycle:usd-jpy", "USD/JPY", "logret"),
    ("cycle:tlt-shy", "TLT/SHY", "logret"),
    ("cycle:mbb-us", "MBB", "logret"),
    ("cycle:hyg", "HYG", "logret"),
    ("cycle:gld", "GLD", "logret"),
    ("cycle:btc", "BTC", "logret"),
]

# Regime-relevant pairs: (pair id, label, key A, label A, key B, label B).
PAIRS: list[tuple[str, str, str, str, str, str]] = [
    ("spx-ust10y", "Stocks vs bonds", "idx:SPX", "SPX", "yield:US10Y", "US 10Y"),
    ("hy-spx", "Credit vs equity", "cycle:hy-oas", "HY OAS", "idx:SPX", "SPX"),
    ("usd-spx", "USD vs stocks", "cycle:usd-broad", "USD broad", "idx:SPX", "SPX"),
    ("vix-spx", "Vol vs stocks", "cycle:vix", "VIX", "idx:SPX", "SPX"),
    ("ig-hy", "IG vs HY credit", "cycle:ig-oas", "IG OAS", "cycle:hy-oas", "HY OAS"),
    ("btc-spx", "BTC vs stocks", "cycle:btc", "BTC", "idx:SPX", "SPX"),
]

# Realized vol inputs: (store key, label, slug, unit).
RVOL: list[tuple[str, str, str, str]] = [
    ("idx:SPX", "S&P 500", "spx", "%"),
    ("idx:NDX", "Nasdaq 100", "ndx", "%"),
    ("cycle:tlt-shy", "TLT/SHY", "tlt-shy", "%"),
    ("cycle:mbb-us", "MBB", "mbb", "%"),
    ("cycle:hyg", "HYG", "hyg", "%"),
    ("cycle:gld", "Gold", "gld", "%"),
    ("cycle:btc", "Bitcoin", "btc", "%"),
    ("cycle:eur-usd", "EUR/USD", "eur-usd", "%"),
    ("yield:US10Y", "US 10Y yield", "us10y", "pp"),
]

TRADING_DAYS = 252


def _changes(points: dict[date, float], kind: str) -> dict[date, float]:
    """Daily changes aligned to the later date; drops non-positive prices
    for logret and any non-finite input."""
    ordered = sorted(points.items())
    out: dict[date, float] = {}
    for (d0, v0), (d1, v1) in zip(ordered, ordered[1:]):
        try:
            if kind == "logret":
                if v0 <= 0 or v1 <= 0:
                    continue
                chg = math.log(v1 / v0) * 100.0
            else:  # diff
                chg = v1 - v0
            if math.isfinite(chg):
                out[d1] = chg
        except (TypeError, ValueError):
            continue
    return out


def _pair_corr(
    a: dict[date, float], b: dict[date, float], window: int, min_obs: int
) -> tuple[float, int] | tuple[None, int]:
    """Pearson corr over the last `window` common dates; (None, n) when too
    few common observations or zero variance."""
    common = sorted(set(a) & set(b))
    if len(common) < min_obs:
        return None, len(common)
    use = common[-window:]
    if len(use) < min_obs:
        return None, len(use)
    xs = [a[d] for d in use]
    ys = [b[d] for d in use]
    try:
        return round(correlation(xs, ys), 3), len(use)
    except Exception:  # noqa: BLE001 — zero variance etc.
        return None, len(use)


def _pctile(hist: list[float], x: float) -> float | None:
    if not hist:
        return None
    below = sum(1 for v in hist if v < x)
    return round(100.0 * below / len(hist), 1)


def _realized_vol(changes: dict[date, float], window: int) -> float | None:
    """Annualized realized vol of daily changes; unit follows the input
    (% for logret x100, pp for yield/spread diffs)."""
    ordered = sorted(changes.items())
    if len(ordered) < window:
        return None
    vals = [v for _, v in ordered[-window:]]
    try:
        sd = pstdev(vals)
    except Exception:  # noqa: BLE001
        return None
    if sd == 0:
        return 0.0
    return round(sd * math.sqrt(TRADING_DAYS), 2)


def refresh_xcorr(store: Store, today: date | None = None) -> str:
    """Daily compute job: correlation matrices, regime pairs, realized vol."""
    today = today or datetime.now(timezone.utc).date()
    changes: dict[str, dict[date, float]] = {}
    labels: dict[str, str] = {}
    for key, label, kind in UNIVERSE:
        try:
            pts = store.points(key)
        except Exception:  # noqa: BLE001 — a bad series never fails the job
            continue
        if len(pts) < MIN_OBS_SHORT + 1:
            continue
        chg = _changes(pts, kind)
        if len(chg) < MIN_OBS_SHORT:
            continue
        changes[key] = chg
        labels[key] = label

    keys = list(changes)
    matrices: dict[str, list[list[float | None]]] = {}
    n_obs: dict[str, int] = {}
    for window, min_obs, tag in (
        (WIN_SHORT, MIN_OBS_SHORT, "60d"),
        (WIN_LONG, MIN_OBS_LONG, "252d"),
    ):
        mat: list[list[float | None]] = []
        n = 0
        for ka in keys:
            row: list[float | None] = []
            for kb in keys:
                if ka == kb:
                    row.append(1.0)
                    continue
                c, used = _pair_corr(changes[ka], changes[kb], window, min_obs)
                row.append(c)
                n = max(n, used)
            mat.append(row)
        matrices[tag] = mat
        n_obs[tag] = n

    pairs = []
    for pid, plabel, ka, la, kb, kb_label in PAIRS:
        if ka not in changes or kb not in changes:
            continue
        c60, _ = _pair_corr(changes[ka], changes[kb], WIN_SHORT, MIN_OBS_SHORT)
        c252, _ = _pair_corr(changes[ka], changes[kb], WIN_LONG, MIN_OBS_LONG)
        if c60 is None:
            continue
        hist_key = f"xcorr:{pid}"
        try:
            hist = store.points(hist_key)
        except Exception:  # noqa: BLE001
            hist = {}
        pct = _pctile(sorted(hist.values()), c60)
        store.upsert_points(hist_key, [(today, c60)])
        pairs.append({
            "id": pid,
            "label": plabel,
            "a": la,
            "b": kb_label,
            "corr_60d": c60,
            "corr_252d": c252,
            "pctile_60d": pct,
            "extreme": abs(c60) >= EXTREME_AT,
        })

    rvol = []
    for key, label, slug, unit in RVOL:
        chg = changes.get(key)
        if chg is None:
            continue
        v21 = _realized_vol(chg, 21)
        v63 = _realized_vol(chg, 63)
        if v21 is not None:
            store.upsert_points(f"rvol:{slug}:21d", [(today, v21)])
        if v63 is not None:
            store.upsert_points(f"rvol:{slug}:63d", [(today, v63)])
        rvol.append({"key": slug, "label": label, "unit": unit,
                     "rv_21d": v21, "rv_63d": v63})

    doc = {
        "asof": today.isoformat(),
        "labels": [labels[k] for k in keys],
        "keys": keys,
        "matrix_60d": matrices.get("60d", []),
        "matrix_252d": matrices.get("252d", []),
        "n_obs_60d": n_obs.get("60d", 0),
        "n_obs_252d": n_obs.get("252d", 0),
        "pairs": pairs,
        "rvol": rvol,
    }
    store.put_doc(DOC_KEY, doc, source=SOURCE)
    return "xcorr"
