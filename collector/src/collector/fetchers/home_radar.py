"""Market radar: multi-indicator historical-percentile snapshot for the HOME tab.

Compute-only (daily, no HTTP). Every input reads existing store history,
so this job runs after the data jobs and degrades per-indicator: one
missing series never kills the radar.

For each indicator we store the current value plus its percentile rank vs
the trailing 252 observations. Coloring is direction-aware:
  up_bad   — high percentile is stress/heat (red)
  down_bad — low percentile is stress (red), e.g. backwardated vol curve
  sym      — symmetric: extremes in either direction are fragile
  score100 — already a 0-100 stress score (risk engine outputs)
  z        — already a standardized z-score (CTA crowdedness)

Writes doc "home_radar": {as_of, regime, verdict, indicators: [...]}.
Each indicator carries a weekly-sampled 1Y history tail for sparklines.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "home-radar"
DOC_KEY = "home_radar"
WINDOW = 252       # trailing observations for percentile ranks
HIST_MIN = 20      # minimum history for a percentile
SPARK_STEP = 5     # ~weekly sampling for the 1Y sparkline tail

# (id, label, group, kind, direction)
# kind: pctile | spread_pctile | drawdown | score100 | cta_z
INDICATORS = [
    ("vol_vix", "VIX", "VOLATILITY", "pctile", "up_bad"),
    ("vol_term", "VIX3M\u2212VIX term structure", "VOLATILITY", "spread_pctile", "down_bad"),
    ("vol_rvol_spx", "SPX 21d realized vol", "VOLATILITY", "pctile", "up_bad"),
    ("vol_vvix", "VVIX (vol of VIX)", "VOLATILITY", "pctile", "up_bad"),
    ("stress_basis", "Basis-trade stress", "STRESS", "score100", "up_bad"),
    ("stress_auction", "Auction stress", "STRESS", "score100", "up_bad"),
    ("stress_rrp", "ON RRP take-up", "STRESS", "pctile", "up_bad"),
    ("stress_hy", "HY OAS spread", "STRESS", "pctile", "up_bad"),
    ("mom_spx_dd", "SPX vs 52w high", "RETURNS", "drawdown", "up_bad"),
    ("mom_tlt", "TLT 60d momentum", "RETURNS", "momentum", "sym"),
    ("mom_cta", "CTA crowdedness (max |z|)", "RETURNS", "cta_z", "up_bad"),
    ("breadth_ew", "Equal-weight vs SPX", "BREADTH", "pctile", "down_bad"),
    ("breadth_disp", "Cross-sectional dispersion (5d)", "BREADTH", "pctile", "up_bad"),
    ("infl_5y", "5Y breakeven", "INFLATION", "pctile", "up_bad"),
    ("infl_10y", "10Y breakeven", "INFLATION", "pctile", "up_bad"),
    ("infl_cpi", "CPI YoY", "INFLATION", "pctile", "up_bad"),
    ("fund_sofr_iorb", "SOFR\u2212IORB spread", "FUNDING", "spread_pctile", "up_bad"),
]

# store keys per indicator id (None = computed from multiple keys)
KEYS = {
    "vol_vix": "cycle:vix",
    "vol_rvol_spx": "rvol:spx:21d",
    "vol_vvix": "cycle:vvix",
    "stress_basis": "risk:basis_stress",
    "stress_auction": "risk:auction_stress",
    "stress_rrp": "cycle:rrp-on",
    "stress_hy": "cycle:hy-oas",
    "mom_spx_dd": "idx:SPX",
    "mom_tlt": "cycle:tlt",
    "breadth_ew": "cycle:spw-spx",
    "breadth_disp": "movers:dispersion-5d",
    "infl_5y": "cycle:us-5y-breakeven",
    "infl_10y": "cycle:breakeven-10y",
    "infl_cpi": "cycle:us-cpi-yoy",
}

CTA_Z_KEYS = ["risk:cta_z_2y", "risk:cta_z_5y", "risk:cta_z_10y", "risk:cta_z_30y"]

FMT = {
    "vol_vix": ("idx", 1), "vol_term": ("pts", 2), "vol_rvol_spx": ("%", 1),
    "vol_vvix": ("idx", 1), "stress_basis": ("/100", 0), "stress_auction": ("/100", 0),
    "stress_rrp": ("$bn", 0), "stress_hy": ("%", 2), "mom_spx_dd": ("%", 1),
    "mom_tlt": ("%", 1), "mom_cta": ("z", 2), "breadth_ew": ("ratio", 3),
    "breadth_disp": ("%", 1), "infl_5y": ("%", 2), "infl_10y": ("%", 2),
    "infl_cpi": ("%", 1), "fund_sofr_iorb": ("bp", 0),
}

NOTES = {
    "vol_term": "negative = backwardation (stress)",
    "mom_spx_dd": "drawdown from 52w high; deeper = worse",
    "mom_tlt": "extremes either way = fragile",
    "breadth_ew": "falling = mega-cap concentration",
    "stress_rrp": "high take-up = cash parked at Fed",
    "fund_sofr_iorb": "wide = funding stress",
}


def _hist(store: Store, key: str) -> dict:
    try:
        return store.points(key)
    except Exception:  # noqa: BLE001 — missing key degrades the indicator
        return {}


def _pctile(hist: dict, value: float, window: int = WINDOW) -> float | None:
    ordered = sorted(hist.items())
    if len(ordered) < HIST_MIN:
        return None
    base = [v for _, v in ordered[-window - 1:-1]]
    if not base:
        return None
    return round(100.0 * sum(1 for v in base if v <= value) / len(base), 1)


def _fmt_value(value: float, ind_id: str) -> str:
    unit, nd = FMT.get(ind_id, ("", 2))
    if unit == "%":
        return f"{value:.{nd}f}%"
    if unit == "bp":
        return f"{value:.0f}bp"
    if unit == "$bn":
        return f"${value:,.0f}B"
    if unit == "/100":
        return f"{value:.0f}/100"
    if unit == "pts":
        return f"{value:+.{nd}f}pts"
    if unit == "z":
        return f"{value:.{nd}f}\u03c3"
    if unit == "ratio":
        return f"{value:.{nd}f}"
    return f"{value:.{nd}f}"


def _color_pctile(p: float, direction: str) -> str:
    eff = 100.0 - p if direction == "down_bad" else p
    if eff >= 80:
        return "red"
    if eff >= 60:
        return "orange"
    if eff >= 40:
        return "yellow"
    return "green"


def _color_score100(v: float) -> str:
    if v >= 75:
        return "red"
    if v >= 55:
        return "orange"
    if v >= 30:
        return "yellow"
    return "green"


def _color_sym(p: float) -> str:
    d = abs(p - 50.0)
    if d >= 40:
        return "orange"
    if d >= 30:
        return "yellow"
    return "green"


def _spark(hist: dict) -> list:
    ordered = sorted(hist.items())
    tail = ordered[-WINDOW:][::SPARK_STEP]
    return [[d.isoformat(), round(v, 3)] for d, v in tail]


def _spread_hist(store: Store, key_a: str, key_b: str, scale: float = 1.0) -> dict:
    ha, hb = _hist(store, key_a), _hist(store, key_b)
    common = sorted(set(ha) & set(hb))
    return {d: round((ha[d] - hb[d]) * scale, 4) for d in common}


def _drawdown_hist(hist: dict, lookback: int = 252) -> dict:
    """Drawdown from trailing 252d high, in percent (<= 0)."""
    ordered = sorted(hist.items())
    out = {}
    for i, (d, v) in enumerate(ordered):
        window = [x for _, x in ordered[max(0, i - lookback):i + 1]]
        hi = max(window) if window else v
        out[d] = round(100.0 * (v - hi) / hi, 2) if hi else 0.0
    return out


def _momentum_hist(hist: dict, days: int = 60) -> dict:
    ordered = sorted(hist.items())
    out = {}
    for i, (d, v) in enumerate(ordered):
        if i >= days and ordered[i - days][1]:
            ref = ordered[i - days][1]
            out[d] = round(100.0 * (v - ref) / ref, 2)
    return out


def _compute_indicator(store: Store, ind_id: str, kind: str,
                       direction: str) -> dict | None:
    """Returns the indicator dict, or None when inputs are missing."""
    try:
        if kind == "pctile":
            h = _hist(store, KEYS[ind_id])
            if not h:
                return None
            asof = max(h)
            value = h[asof]
            p = _pctile(h, value)
            if ind_id == "stress_rrp":
                value = value / 1000.0  # $m -> $bn for display
                disp_hist = {d: v / 1000.0 for d, v in h.items()}
            else:
                disp_hist = h
            return _pack(ind_id, value, p, direction, disp_hist)
        if kind == "spread_pctile":
            if ind_id == "vol_term":
                h = _spread_hist(store, "cycle:vix3m", "cycle:vix")
            else:  # fund_sofr_iorb: SOFR-IORB in bp
                h = _spread_hist(store, "cycle:sofr", "cycle:iorb", scale=100.0)
            if not h:
                return None
            asof = max(h)
            value = h[asof]
            return _pack(ind_id, value, _pctile(h, value), direction, h)
        if kind == "drawdown":
            h = _hist(store, KEYS[ind_id])
            if not h:
                return None
            dd = _drawdown_hist(h)
            asof = max(dd)
            value = dd[asof]
            # percentile of drawdown depth: deeper (more negative) = higher stress
            p = _pctile({d: -v for d, v in dd.items()}, -value)
            # color on absolute depth, not percentile: a calm year pins depth
            # at 0, which would otherwise rank "100th percentile" -> red
            depth = -value
            color = ("green" if depth < 3 else "yellow" if depth < 8
                     else "orange" if depth < 15 else "red")
            return _pack(ind_id, value, p, direction, dd, color=color)
        if kind == "momentum":
            h = _hist(store, KEYS[ind_id])
            if not h:
                return None
            mom = _momentum_hist(h)
            if not mom:
                return None
            asof = max(mom)
            value = mom[asof]
            p = _pctile(mom, value)
            return _pack(ind_id, value, p, direction, mom,
                         color=_color_sym(p) if p is not None else "nodata")
        if kind == "score100":
            h = _hist(store, KEYS[ind_id])
            if not h:
                return None
            asof = max(h)
            value = h[asof]
            p = _pctile(h, value)
            return _pack(ind_id, value, p, direction, h,
                         color=_color_score100(value))
        if kind == "cta_z":
            best = None
            for k in CTA_Z_KEYS:
                h = _hist(store, k)
                if h:
                    asof = max(h)
                    z = abs(h[asof])
                    if best is None or z > best[0]:
                        best = (z, asof)
            if best is None:
                return None
            z, asof = best
            color = ("green" if z < 1 else "yellow" if z < 1.5
                     else "orange" if z < 2 else "red")
            return _pack(ind_id, z, None, direction, {}, color=color)
    except Exception as exc:  # noqa: BLE001 — per-indicator isolation
        log.warning("home_radar: indicator %s failed: %s", ind_id, exc)
    return None


def _pack(ind_id: str, value: float, pctile: float | None, direction: str,
          hist: dict, color: str | None = None) -> dict:
    if color is None:
        color = "nodata" if pctile is None else _color_pctile(pctile, direction)
    return {
        "id": ind_id,
        "label": next(l for i, l, _, _, _ in INDICATORS if i == ind_id),
        "group": next(g for i, _, g, _, _ in INDICATORS if i == ind_id),
        "value": round(value, 3),
        "value_fmt": _fmt_value(value, ind_id),
        "percentile_1y": pctile,
        "color": color,
        "direction": direction,
        "note": NOTES.get(ind_id, ""),
        "hist": _spark(hist),
    }


def build_radar(store: Store) -> dict:
    """Compute the full radar payload (pure function of the store)."""
    indicators = []
    for ind_id, _label, _group, kind, direction in INDICATORS:
        ind = _compute_indicator(store, ind_id, kind, direction)
        if ind is not None:
            indicators.append(ind)
    risk_doc = store.doc("risk_summary")
    regime = (risk_doc.payload.get("regime", "UNKNOWN") if risk_doc else "UNKNOWN")
    verdict = (risk_doc.payload.get("verdict") if risk_doc else None)
    return {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "regime": regime,
        "verdict": verdict,
        "indicators": indicators,
    }


async def refresh_home_radar(store: Store) -> str:
    """Compute-only job: rebuild the home_radar doc from stored history."""
    store.put_doc(DOC_KEY, build_radar(store), source=SOURCE)
    return SOURCE
