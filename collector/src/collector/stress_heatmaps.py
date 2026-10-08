"""Stress Monitor — 8 heatmaps (Harry's spec, 2026-10-08).

H1/H2: LEVEL today vs episode peaks (native units) — 7D acute / 30D sustained
H3/H4: VELOCITY today vs episode peaks (raw, signed) — 7D / 30D
H5/H6: VELOCITY Z-SCORE (sigmas vs trailing 1Y of n-day changes) — 7D / 30D
H7/H8: LEVEL Z-SCORE vs trailing history — 1Y / full

Columns: 8 stress episodes + NOW. No invented data: null where history is
missing. Pre-Oct-2023 OAS cells are n/a (FRED's unauthenticated OAS window
starts Oct 2023; Harry ruled estimates must not be presented as canonical).

Pure stdlib. Reads from store.points(); the daily scheduler job caches the
result via store.put_doc('stress_heatmaps', ...), served by /api/stress/heatmaps.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from collector.store import Store

DOC_KEY = "stress_heatmaps"

EPISODES = [
    ("2007 grind", "2007-06-01", "2007-12-31"),
    ("GFC", "2008-09-01", "2008-12-31"),
    ("Covid", "2020-02-20", "2020-03-23"),
    ("2022 hikes", "2022-06-01", "2022-10-31"),
    ("SVB", "2023-03-08", "2023-03-31"),
    ("Tariffs", "2025-03-01", "2025-04-09"),
    ("Lib. Day", "2025-04-02", "2025-04-09"),
    ("Iran war", "2026-03-01", "2026-03-31"),
]
COLS = [e[0] for e in EPISODES] + ["NOW"]

# name: (store series_id, unit, level_fmt, vel_mult, skip_velocity)
# vel_mult converts native -> velocity display units (bp for %-denominated)
SERIES = {
    "US 2Y": ("us-2y-yield", "%", "{:.2f}%", 100, False),
    "US 10Y": ("us10y", "%", "{:.2f}%", 100, False),
    "US 30Y": ("us-30y-yield", "%", "{:.2f}%", 100, False),
    "Real 10Y": ("__real10y__", "%", "{:.2f}%", 100, False),
    "Breakeven": ("breakeven-10y", "%", "{:.2f}%", 100, False),
    "SOFR": ("sofr", "%", "{:.2f}%", 100, False),
    "IG OAS": ("ig-oas", "bp", "{:.0f}", 100, False),
    "HY OAS": ("hy-oas", "bp", "{:.0f}", 100, False),
    "CCC OAS": ("ccc-oas", "bp", "{:.0f}", 100, False),
    "VIX": ("vix", "pts", "{:.1f}", 1, False),
    "NFCI": ("nfci", "idx", "{:.2f}", 1, False),
    "Claims": ("claims", "k", "{:.0f}k", 1 / 1000, True),  # excluded from H3/H4 (scale)
    "Sahm": ("us-sahm", "pp", "{:.2f}", 1, False),
    "CPI YoY": ("us-cpi-yoy", "%", "{:.1f}%", 1, False),
    "30Y Mortgage": ("us-mortgage-30y", "%", "{:.2f}%", 1, False),
    "Russell 2000": ("iwm", "px", "{:.0f}", 1, False),
    "S&P 500": ("SPX", "px", "{:.0f}", 1, False),
    "Unemployment": ("us-unemployment", "%", "{:.1f}%", 1, False),
    "Fed Funds Eff": ("us-fed-effective", "%", "{:.2f}%", 1, False),
    "CMDI": ("cmdi-market", "idx", "{:.2f}", 1, False),
    "A/D Spread": ("finra-breadth-corp-all-adspread", "ct", "{:.0f}", 1, False),
    "52W Lows": ("finra-breadth-corp-all-lo52", "ct", "{:.0f}", 1, False),
    "Days to Cover": ("short-days-to-cover", "days", "{:.1f}", 1, False),
    "SI Vel 30d": ("short-interest-vel30", "%", "{:+.1f}%", 1, False),
    "MOVE proxy": ("opt-TLT-atm-iv", "%", "{:.1f}", 1, False),
    "Margin Debt": ("finra-margin-debit", "$M", "{:,.0f}", 1, False),
    "Free Credit Cash": ("finra-margin-credit-cash", "$M", "{:,.0f}", 1, False),
    "Free Credit Margin": ("finra-margin-credit-margin", "$M", "{:,.0f}", 1, False),
}

MATRICES = ["lvl7", "lvl30", "vel7", "vel30", "velz7", "velz30", "lvlz1y", "lvlzfull"]


def _iso(d: date) -> str:
    return d.isoformat()


def _obs_before(d: dict[str, float], dates: list[str], t_iso: str, n: int) -> list[float]:
    """observations in (t-n, t] by calendar days"""
    t = date.fromisoformat(t_iso)
    lo = (t - timedelta(days=n)).isoformat()
    return [d[x] for x in dates if lo < x <= t_iso]


def _max_nd_rise(d: dict[str, float], dates: list[str], start: str, end: str, n: int):
    best = None
    for t in dates:
        if not (start <= t <= end):
            continue
        obs = _obs_before(d, dates, t, n)
        if not obs:
            continue
        c = d[t] - obs[0]
        if best is None or c > best:
            best = c
    return best


def _max_navg_level(d: dict[str, float], dates: list[str], start: str, end: str, n: int):
    """max n-day (calendar) average level within [start, end]"""
    best = None
    for t in dates:
        if not (start <= t <= end):
            continue
        obs = _obs_before(d, dates, t, n)
        if not obs:
            continue
        c = sum(obs) / len(obs)
        if best is None or c > best:
            best = c
    return best


def _trailing_stats(vals: list[float]):
    n = len(vals)
    if n < 2:
        return None, None
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    return m, math.sqrt(var)


def _velocity_zseries(d: dict[str, float], dates: list[str], n: int, win: int = 365):
    """per-date z of n-day change vs trailing `win` days of n-day changes"""
    ch = {}
    for t in dates:
        obs = _obs_before(d, dates, t, n)
        if obs:
            ch[t] = d[t] - obs[0]
    out = {}
    for i, t in enumerate(dates):
        past = [ch[x] for x in dates[max(0, i - win):i] if x in ch]
        if len(past) < 60:
            continue
        m, s = _trailing_stats(past)
        if s and s > 0 and t in ch:
            out[t] = ch[t] / s
    return out


def _level_zseries(d: dict[str, float], dates: list[str], win):
    """per-date z of level vs trailing `win` days (win='full' -> expanding)"""
    out = {}
    for i, t in enumerate(dates):
        past = dates[:i] if win == "full" else dates[max(0, i - win):i]
        if len(past) < 60:
            continue
        m, s = _trailing_stats([d[x] for x in past])
        if s and s > 0:
            out[t] = (d[t] - m) / s
    return out


def _load_series(store: Store) -> dict[str, dict[str, float]]:
    """{display name: {iso_date: value}}; Real 10Y synthesized from US 10Y - breakeven."""
    data: dict[str, dict[str, float]] = {}
    raw: dict[str, dict[str, float]] = {}
    for name, (sid, *_rest) in SERIES.items():
        if sid == "__real10y__":
            continue
        # Store keys are prefixed (macro:/cycle:/idx:/ref:); try each.
        pts: dict = {}
        for cand in (sid, f"macro:{sid}", f"cycle:{sid}", f"idx:{sid}", f"ref:{sid}"):
            pts = store.points(cand)
            if pts:
                break
        raw[name] = {_iso(k) if isinstance(k, date) else str(k): v for k, v in pts.items()}
    data.update(raw)
    d10 = raw.get("US 10Y", {})
    dbe = raw.get("Breakeven", {})
    data["Real 10Y"] = {k: (d10[k] - dbe[k]) for k in d10 if k in dbe}
    return data


def build_matrices(store: Store) -> dict[str, Any]:
    """Compute the 8 heatmap matrices. Returns a JSON-serializable payload
    (None instead of NaN). Never raises on missing data — missing series or
    short history produce null cells, never a crash."""
    data = _load_series(store)
    names = list(SERIES.keys())
    ncols = len(COLS)
    ni = ncols - 1  # NOW column

    M: dict[str, list[list[float | None]]] = {
        k: [[None] * ncols for _ in names] for k in MATRICES
    }
    T: dict[str, list[list[str | None]]] = {
        k: [[None] * ncols for _ in names] for k in ("lvl7", "lvl30")
    }

    for i, name in enumerate(names):
        sid, unit, lfmt, vmult, skip_vel = SERIES[name]
        d = data.get(name, {})
        dates = sorted(d)
        if not dates:
            continue
        now_t = dates[-1]  # per-series latest observation
        now_lvl = d[now_t]
        div = 100 if unit == "bp" else ((1 / 1000) if unit == "k" else 1)

        # H1/H2: level peaks per episode + NOW
        for j, (_ep, s, e) in enumerate(EPISODES):
            p7 = _max_navg_level(d, dates, s, e, 7)
            p30 = _max_navg_level(d, dates, s, e, 30)
            if p7 is not None:
                M["lvl7"][i][j] = p7
                T["lvl7"][i][j] = lfmt.format(p7 * div)
            if p30 is not None:
                M["lvl30"][i][j] = p30
                T["lvl30"][i][j] = lfmt.format(p30 * div)
        M["lvl7"][i][ni] = now_lvl
        M["lvl30"][i][ni] = now_lvl
        T["lvl7"][i][ni] = lfmt.format(now_lvl * div)
        T["lvl30"][i][ni] = lfmt.format(now_lvl * div)

        # H3/H4: velocity peaks per episode + NOW (raw, signed)
        if not skip_vel:
            for j, (_ep, s, e) in enumerate(EPISODES):
                for n, key in ((7, "vel7"), (30, "vel30")):
                    r = _max_nd_rise(d, dates, s, e, n)
                    if r is not None:
                        M[key][i][j] = r * vmult
            for n, key in ((7, "vel7"), (30, "vel30")):
                lo = (date.fromisoformat(now_t) - timedelta(days=n)).isoformat()
                r = _max_nd_rise(d, dates, lo, now_t, n)
                if r is not None:
                    M[key][i][ni] = r * vmult

        # H5/H6: velocity z-scores — max within episode + NOW
        for n, key in ((7, "velz7"), (30, "velz30")):
            zs = _velocity_zseries(d, dates, n)
            zdates = sorted(zs)
            for j, (_ep, s, e) in enumerate(EPISODES):
                vals = [zs[t] for t in zdates if s <= t <= e]
                if vals:
                    M[key][i][j] = max(vals)
            if now_t in zs:
                M[key][i][ni] = zs[now_t]

        # H7/H8: level z-scores — max within episode + NOW
        for win, key in ((365, "lvlz1y"), ("full", "lvlzfull")):
            zs = _level_zseries(d, dates, win)
            zdates = sorted(zs)
            for j, (_ep, s, e) in enumerate(EPISODES):
                vals = [zs[t] for t in zdates if s <= t <= e]
                if vals:
                    M[key][i][j] = max(vals)
            if now_t in zs:
                M[key][i][ni] = zs[now_t]

    asofs = {name: (sorted(data[name])[-1] if data.get(name) else None) for name in names}
    return {
        "asof": max((a for a in asofs.values() if a), default=None),
        "series_asof": asofs,
        "columns": COLS,
        "episodes": [{"name": e[0], "start": e[1], "end": e[2]} for e in EPISODES],
        "rows": [
            {"name": name, "unit": SERIES[name][1], "series_id": SERIES[name][0]}
            for name in names
        ],
        "matrices": M,
        "texts": {"lvl7": T["lvl7"], "lvl30": T["lvl30"]},
    }


async def refresh_stress_heatmaps(store: Store) -> str:
    """Compute-only scheduler job: rebuild the 8 matrices and cache the doc.

    Follows the refresh_risk contract: no HTTP, every input optional, never
    crashes on missing data — a premature run just yields thinner matrices.
    """
    payload = build_matrices(store)
    store.put_doc(DOC_KEY, payload, "stress_heatmaps")
    n_filled = sum(
        1 for m in payload["matrices"].values() for row in m for v in row if v is not None
    )
    return f"stress_heatmaps: {n_filled} cells filled, asof={payload['asof']}"
