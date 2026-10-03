"""Volatility dashboard compute job (zero-HTTP): CBOE Macro Volatility Digest
replication with free data.

Implied-vol indices arrive through cycle_series `yahoo:` entries (verified
live 2026-10-03: ^VVIX, ^GVZ, ^OVX, ^VXSLV all serve Yahoo chart data;
^RVX is dead on Yahoo so RTY has no implied leg; ^MOVE is dead entirely).
Underlying prices come from the store (`idx:<SYM>` in fetchers/equity.py,
`cycle:<id>` in fetchers/cycle.py).

Writes doc "voldash":
  asof, rows[] (per ticker: 1M implied, weekly change in points, 1Y
    percentile of implied, 1M realized vol, implied-minus-realized spread,
    1Y percentile of the spread),
  vix_hist / vvix_hist (trailing 1y, for the VIX-vs-VVIX chart),
  beta (latest spot-vol betas) + beta_hist (trailing 1y series for charts),
  regime (counts of rich/cheap implied readings).

Spot-vol beta = 60d rolling OLS slope of VIX log-changes on SPX log-returns
(and VVIX log-changes on VIX log-changes) — the CBOE "spot-vol beta" gauge.

Everything degrades: a missing implied leg yields realized-only rows, a
missing underlying skips the row, one bad input never fails the job.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from statistics import mean, pstdev

from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "voldash-compute"
DOC_KEY = "voldash"
HIST_DAYS = 252
BETA_WINDOW = 60
TRADING_DAYS = 252

# (ticker label, implied store key or None, underlying store key).
ROWS: list[tuple[str, str | None, str]] = [
    ("SPX", "cycle:vix", "idx:SPX"),
    ("RTY", None, "cycle:iwm"),
    ("QQQ", "cycle:vxn", "idx:NDX"),
    ("GLD", "cycle:gvz", "cycle:gld"),
    ("USO", "cycle:ovx", "cycle:uso"),
    ("SLV", "cycle:vxslv", "cycle:slv"),
    ("TLT", None, "cycle:tlt"),
    ("LQD", None, "cycle:lqd"),
    ("HYG", None, "cycle:hyg"),
]

RICH_AT = 90.0
CHEAP_AT = 10.0


def _logrets(pts: dict[date, float]) -> dict[date, float]:
    ordered = sorted(pts.items())
    out: dict[date, float] = {}
    for (d0, v0), (d1, v1) in zip(ordered, ordered[1:]):
        if v0 > 0 and v1 > 0:
            try:
                r = math.log(v1 / v0) * 100.0
            except (ValueError, TypeError):
                continue
            if math.isfinite(r):
                out[d1] = r
    return out


def _pctile(hist: list[float], x: float) -> float | None:
    if not hist:
        return None
    return round(100.0 * sum(1 for v in hist if v < x) / len(hist), 1)


def _realized(rets: dict[date, float], window: int = 21) -> float | None:
    vals = [v for _, v in sorted(rets.items())[-window:]]
    if len(vals) < window:
        return None
    sd = pstdev(vals)
    return round(sd * math.sqrt(TRADING_DAYS), 2) if sd > 0 else 0.0


def _beta(y: dict[date, float], x: dict[date, float],
          window: int = BETA_WINDOW) -> float | None:
    """OLS slope of y on x over the last `window` common dates."""
    common = sorted(set(y) & set(x))[-window:]
    if len(common) < window:
        return None
    xs = [x[d] for d in common]
    ys = [y[d] for d in common]
    mx, my = mean(xs), mean(ys)
    denom = sum((v - mx) ** 2 for v in xs)
    if denom == 0:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / denom, 3)


def _tail(pts: dict[date, float], n: int = HIST_DAYS) -> list[list]:
    return [[d.isoformat(), v] for d, v in sorted(pts.items())[-n:]]


def refresh_voldash(store: Store, today: date | None = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    rows = []
    n_rich = n_cheap = 0
    for ticker, imp_key, und_key in ROWS:
        try:
            und = store.points(und_key)
        except Exception:  # noqa: BLE001
            und = {}
        if len(und) < 30:
            continue
        urets = _logrets(und)
        realized = _realized(urets)
        entry: dict = {"ticker": ticker, "implied": None, "wkly_chg": None,
                       "pctile_1y": None, "realized": realized,
                       "spread": None, "spread_pctile_1y": None}
        if imp_key:
            try:
                imp = store.points(imp_key)
            except Exception:  # noqa: BLE001
                imp = {}
            if imp:
                iord = sorted(imp.items())
                latest_d, latest_v = iord[-1]
                entry["implied"] = round(latest_v, 1)
                # weekly change: value 5 trading days back
                if len(iord) >= 6:
                    entry["wkly_chg"] = round(latest_v - iord[-6][1], 1)
                hist1y = [v for _, v in iord[-HIST_DAYS:-1]]
                entry["pctile_1y"] = _pctile(hist1y, latest_v)
                if entry["pctile_1y"] is not None:
                    if entry["pctile_1y"] >= RICH_AT:
                        n_rich += 1
                    elif entry["pctile_1y"] <= CHEAP_AT:
                        n_cheap += 1
                if realized is not None:
                    spread = round(latest_v - realized, 1)
                    entry["spread"] = spread
                    # spread history needs aligned implied+realized — approximate
                    # with implied history minus trailing realized at each date
                    sp_hist = []
                    closes = dict(sorted(und.items()))
                    for d, iv in iord[-HIST_DAYS:-1]:
                        # 21d realized ending at d
                        win = [(dd, c) for dd, c in closes.items() if dd <= d][-22:]
                        if len(win) < 22:
                            continue
                        lr = []
                        for (a, va), (b, vb) in zip(win, win[1:]):
                            if va > 0 and vb > 0:
                                lr.append(math.log(vb / va) * 100.0)
                        if len(lr) == 21:
                            sd = pstdev(lr)
                            sp_hist.append(iv - sd * math.sqrt(TRADING_DAYS))
                    entry["spread_pctile_1y"] = _pctile(sp_hist, spread)
        rows.append(entry)

    # VIX vs VVIX histories for the chart
    def _pts(key):
        try:
            return store.points(key)
        except Exception:  # noqa: BLE001
            return {}

    vix, vvix = _pts("cycle:vix"), _pts("cycle:vvix")
    spx = _pts("idx:SPX")
    vix_r, vvix_r, spx_r = _logrets(vix), _logrets(vvix), _logrets(spx)
    b_vix_spx = _beta(vix_r, spx_r)
    b_vvix_vix = _beta(vvix_r, vix_r)
    # trailing beta histories
    beta_hist_vix_spx, beta_hist_vvix_vix = [], []
    if vix_r and spx_r:
        common = sorted(set(vix_r) & set(spx_r))
        for i in range(BETA_WINDOW, len(common) + 1):
            seg = common[i - BETA_WINDOW:i]
            xs = [spx_r[d] for d in seg]
            ys = [vix_r[d] for d in seg]
            mx, my = mean(xs), mean(ys)
            den = sum((v - mx) ** 2 for v in xs)
            if den:
                beta_hist_vix_spx.append(
                    [seg[-1].isoformat(),
                     round(sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / den, 3)])
        beta_hist_vix_spx = beta_hist_vix_spx[-HIST_DAYS:]
    if vvix_r and vix_r:
        common = sorted(set(vvix_r) & set(vix_r))
        for i in range(BETA_WINDOW, len(common) + 1):
            seg = common[i - BETA_WINDOW:i]
            xs = [vix_r[d] for d in seg]
            ys = [vvix_r[d] for d in seg]
            mx, my = mean(xs), mean(ys)
            den = sum((v - mx) ** 2 for v in xs)
            if den:
                beta_hist_vvix_vix.append(
                    [seg[-1].isoformat(),
                     round(sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / den, 3)])
        beta_hist_vvix_vix = beta_hist_vvix_vix[-HIST_DAYS:]
    if b_vix_spx is not None:
        store.upsert_points("voldash:beta-vix-spx", [(today, b_vix_spx)])
    if b_vvix_vix is not None:
        store.upsert_points("voldash:beta-vvix-vix", [(today, b_vvix_vix)])

    if n_rich >= 3:
        regime = f"{n_rich} vol indices in the top decile — protection is rich"
    elif n_cheap >= 3:
        regime = f"{n_cheap} vol indices in the bottom decile — complacency bid"
    else:
        regime = "vol pricing mixed — no cross-asset extreme"

    doc = {
        "asof": today.isoformat(),
        "rows": rows,
        "vix_hist": _tail(vix),
        "vvix_hist": _tail(vvix),
        "beta": {"vix_spx": b_vix_spx, "vvix_vix": b_vvix_vix},
        "beta_hist": {"vix_spx": beta_hist_vix_spx,
                      "vvix_vix": beta_hist_vvix_vix},
        "regime": regime,
        "n_rich": n_rich,
        "n_cheap": n_cheap,
    }
    store.put_doc(DOC_KEY, doc, source=SOURCE)
    return "voldash"
