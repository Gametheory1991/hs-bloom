"""Per-country market risk scoring for the RISK MAP tab.

Daily compute job (no HTTP). Scores each of the 13 bond-matrix countries 0-100
from up to three inputs, each expressed as a percentile vs its own history:

  a. 10Y sovereign yield percentile vs trailing 5Y   (store: yield:<CC>10Y)
     Multi-year yield highs = stress = high score.
  b. Equity drawdown from 252-day high, percentiled vs its own drawdown
     history (store: idx:<SYM>; only countries with a terminal index).
  c. FX depreciation vs USD over 63d, percentiled vs its own history
     (store: cycle:eur-usd / cycle:usd-jpy — the only FRED DEX series in
     config; other countries skip this input).

Score = mean of available input percentiles. Buckets: <=25 green (STABLE),
<=50 yellow (IMPROVING), <=75 orange (DETERIORATING), >75 red (RISK ZONE).
Trend = point-in-time score change over 21 days, computed with no lookahead
(each evaluation only sees data <= that date): improving / deteriorating /
flat.

Every input degrades gracefully: a country with no usable input reports
score=null, bucket "nodata". One bad country never fails the job.

Writes risk:country:<CC> history (one point per run) plus the country_risk
doc consumed by the /api/dashboard riskmap panel and the /api/insights
digest (which the daily Google-Drive snapshot pulls, so the map data lands
in Harry's Drive).
"""
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone

from collector.store import Store

# code, name, equity index symbol (or None), (fx cycle id, direction) or None.
# fx direction: +1 means a rising series = local-currency depreciation
# (DEXJPUS = JPY per USD), -1 means a falling series = depreciation
# (DEXUSEU = USD per EUR).
COUNTRIES: list[tuple[str, str, str | None, tuple[str, int] | None]] = [
    ("US", "United States", "SPX", None),
    ("DE", "Germany", "DAX", ("eur-usd", -1)),
    ("FR", "France", "CAC", ("eur-usd", -1)),
    ("IT", "Italy", "FTSEMIB", ("eur-usd", -1)),
    ("ES", "Spain", "IBEX", ("eur-usd", -1)),
    ("NL", "Netherlands", "AEX", ("eur-usd", -1)),
    ("BE", "Belgium", "BFX", ("eur-usd", -1)),
    ("UK", "United Kingdom", "UKX", ("gbp-usd", -1)),
    ("JP", "Japan", "NKX", ("usd-jpy", +1)),
    ("CA", "Canada", "GSPTSE", ("cad-usd", +1)),
    ("AU", "Australia", "AXJO", ("aud-usd", -1)),
    ("CH", "Switzerland", "SMI", ("chf-usd", +1)),
    ("SE", "Sweden", "OMXS30", ("sek-usd", +1)),
    ("MX", "Mexico", "MXX", ("mxn-usd", +1)),
    ("KR", "Korea", "KS11", ("krw-usd", +1)),
]

BUCKETS = (
    (25, "green", "STABLE"),
    (50, "yellow", "IMPROVING"),
    (75, "orange", "DETERIORATING"),
    (100, "red", "RISK ZONE"),
)

YIELD_WINDOW_DAYS = 5 * 365
DD_WINDOW = 252
FX_WINDOW = 63
TREND_DAYS = 21
TREND_TOL = 3.0

MIN_YIELD_OBS = 24      # monthly series give ~60 points over 5Y
MIN_EQUITY_OBS = 60
MIN_FX_OBS = 100


def _pctile(hist: list[float], x: float) -> float | None:
    """Percentile rank of x within hist (0-100). None when hist is empty."""
    if not hist:
        return None
    below = sum(1 for v in hist if v < x)
    return 100.0 * below / len(hist)


def _asof_points(store: Store, key: str, asof: date) -> list[tuple[date, float]]:
    """History for key truncated to dates <= asof (no lookahead)."""
    try:
        pts = store.points(key)
    except Exception:
        return []
    items = pts.items() if hasattr(pts, "items") else pts
    out = []
    for d, v in items:
        try:
            if d <= asof and v is not None:
                out.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def _yield_input(store: Store, code: str, asof: date) -> float | None:
    pts = _asof_points(store, f"yield:{code}10Y", asof)
    window = [(d, v) for d, v in pts if d >= asof - timedelta(days=YIELD_WINDOW_DAYS)]
    if len(window) < MIN_YIELD_OBS:
        return None
    hist = [v for _, v in window[:-1]]
    return _pctile(hist, window[-1][1])


def _drawdowns(values: list[float], window: int) -> list[float]:
    """Trailing-window drawdown series: (window max - value) / window max."""
    dds = []
    for i, v in enumerate(values):
        w = values[max(0, i - window + 1): i + 1]
        peak = max(w)
        dds.append((peak - v) / peak if peak > 0 else 0.0)
    return dds


def _equity_input(store: Store, symbol: str, asof: date) -> float | None:
    pts = _asof_points(store, f"idx:{symbol}", asof)
    if len(pts) < MIN_EQUITY_OBS:
        return None
    values = [v for _, v in pts]
    dds = _drawdowns(values, DD_WINDOW)
    return _pctile(dds[:-1], dds[-1])


def _fx_input(store: Store, fx_id: str, direction: int, asof: date) -> float | None:
    pts = _asof_points(store, f"cycle:{fx_id}", asof)
    if len(pts) < MIN_FX_OBS:
        return None
    values = [v for _, v in pts]
    # 63d depreciation rates; direction orients so positive = local depreciation
    deps = []
    for i in range(FX_WINDOW, len(values)):
        base = values[i - FX_WINDOW]
        if base:
            deps.append(direction * (values[i] - base) / base * 100.0)
    if len(deps) < 2:
        return None
    return _pctile(deps[:-1], deps[-1])


def score_country(store: Store, code: str, name: str,
                  symbol: str | None, fx: tuple[str, int] | None,
                  asof: date) -> dict:
    """Point-in-time score for one country. Returns the country payload dict."""
    inputs: dict[str, float | None] = {}
    y = _yield_input(store, code, asof)
    if y is not None:
        inputs["yield_pct"] = round(y, 1)
    if symbol:
        e = _equity_input(store, symbol, asof)
        if e is not None:
            inputs["equity_dd_pct"] = round(e, 1)
    if fx:
        f = _fx_input(store, fx[0], fx[1], asof)
        if f is not None:
            inputs["fx_dep_pct"] = round(f, 1)
    if not inputs:
        return {"code": code, "name": name, "score": None, "bucket": "nodata",
                "bucket_label": "NO DATA", "trend": "unknown", "trend_delta": None,
                "inputs": {}, "inputs_available": 0}
    score = round(sum(inputs.values()) / len(inputs), 1)
    bucket, label = next((b, l) for lim, b, l in BUCKETS if score <= lim)
    return {"code": code, "name": name, "score": score, "bucket": bucket,
            "bucket_label": label, "trend": "flat", "trend_delta": 0.0,
            "inputs": inputs, "inputs_available": len(inputs)}


def _apply_trend(store: Store, payload: dict, asof: date) -> dict:
    """Point-in-time 21d trend: recompute the score as of asof-21d (no lookahead)."""
    if payload["score"] is None:
        return payload
    for code, name, symbol, fx in COUNTRIES:
        if code != payload["code"]:
            continue
        past = score_country(store, code, name, symbol, fx,
                             asof - timedelta(days=TREND_DAYS))
        if past["score"] is None:
            payload["trend"] = "unknown"
            payload["trend_delta"] = None
            return payload
        delta = round(payload["score"] - past["score"], 1)
        payload["trend_delta"] = delta
        payload["trend"] = ("improving" if delta <= -TREND_TOL
                            else "deteriorating" if delta >= TREND_TOL else "flat")
        return payload
    return payload


async def refresh_country_risk(store: Store, today: date | None = None) -> str:
    """Daily compute job: score every country, write history + country_risk doc."""
    asof = today or datetime.now(timezone.utc).date()
    countries = []
    for code, name, symbol, fx in COUNTRIES:
        try:
            payload = score_country(store, code, name, symbol, fx, asof)
            payload = _apply_trend(store, payload, asof)
        except Exception:
            payload = {"code": code, "name": name, "score": None, "bucket": "nodata",
                       "bucket_label": "NO DATA", "trend": "unknown",
                       "trend_delta": None, "inputs": {}, "inputs_available": 0}
        countries.append(payload)
        if payload["score"] is not None:
            store.upsert_points(f"risk:country:{code}", [(asof, payload["score"])])
    store.put_doc("country_risk",
                  {"asof": asof.isoformat(), "countries": countries},
                  source="country-risk")
    return "country-risk"
