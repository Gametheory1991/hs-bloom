"""Multi-asset activity view: five Treasury-activity legs for MARKETS -> Activity.

Legs:
  tokenized  DefiLlama RWA protocol TVL + CoinGecko tokenized-asset market caps
             (TVL and market cap are kept visually separate — never summed).
  trace      FINRA Treasury TRACE daily aggregates: par, trades, product
             split (bills/FRN/coupons/TIPS), venue (ATS vs dealer-to-customer),
             on-the-run vs off-the-run.
  etfs       Treasury ETF universe (18 fi-treasury tickers): aggregate AUM and
             estimated net flow = Δshares × NAV (labeled as estimates).
  options    CBOE/Yahoo options stats for the 14-symbol universe; TLT is the
             Treasury proxy. GEX proxy = Σ gamma×OI×100×spot²/1bn (calls +,
             puts −); n/a when greeks are unavailable.
  futures    CFTC legacy COT net non-commercial + OI (2Y/5Y/10Y/30Y),
             OFR TFF (leveraged funds / asset managers / dealers, net / DV01 /
             10Y-equiv, by tenor), OFR FPF hedge-fund long UST exposure.

Conventions (Harry's standing rules):
- Every delta shows BOTH nominal and % change; horizons that can't be
  computed are null (frontend renders n/a).
- Percentile = trailing 1Y rank of the latest level (0-100).
- z = 1Y rolling z-score of the level (min 20 observations).
- vel7 / vel30 = the largest 7-day / 30-day move in each series' stress
  direction anywhere in the trailing 1Y of history (shock intensity /
  grind), signed, in native units. Stress = up for volumes, AUM, IV;
  down for leveraged-fund net shorts, where a more negative print is the
  stress.
- Never forward-fill: each KPI/grid row carries its own asof; staleness is
  flagged per leg against its expected cadence.
- No estimates presented as canonical: derived flows are labeled "est.".

Pure function of the store — no I/O, no clock beyond build date. Never
raises: missing legs degrade to nulls.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from collector.changes import pct_change, ref_close

log = logging.getLogger(__name__)

YEAR_WINDOW = 252  # ~1 trading year of observations
MIN_HISTORY = 20
CHART_CAP = 730    # max points per chart series (payload size)
LEAD_Z_MIN = 1.5   # |z| needed for a heatmap row to become the lead

# Expected cadence per leg (stale when asof is older than this many days).
CADENCE_DAYS = {"daily": 5, "weekly": 14, "quarterly": 120}


# ---------------------------------------------------------------- helpers

def _pts(store, sid: str) -> dict:
    try:
        return store.points(sid) or {}
    except Exception:  # noqa: BLE001 — a bad series must not kill the view
        return {}


def _doc_payload(store, key: str) -> dict:
    try:
        d = store.doc(key)
    except Exception:  # noqa: BLE001
        return {}
    return (d.payload or {}) if d else {}


def _epoch_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day,
                        tzinfo=timezone.utc).timestamp() * 1000)


def _ref(points: dict, asof: date, horizon: str):
    """Latest observation on-or-before the horizon target ('1d' strictly before)."""
    try:
        return ref_close(points, asof, horizon)
    except Exception:  # noqa: BLE001
        return None


def _stats(points: dict, cadence: str = "daily",
           stress_dir: int = 1) -> dict:
    """Score one series: level, 1W/1M deltas (nominal + %), percentile, z,
    vel7/vel30 (max 7d/30d move in the stress direction within trailing 1Y,
    signed, native units), staleness flag.

    stress_dir: +1 (stress = up: volumes, IV, AUM) or -1 (stress = down:
    leveraged-funds net shorts, where a more negative print is the stress).
    """
    empty = {"last": None, "asof": None, "d1w_nom": None, "d1w_pct": None,
             "d1m_nom": None, "d1m_pct": None, "pctile": None, "z": None,
             "vel7": None, "vel30": None, "stale": True}
    if not points:
        return empty
    asof = max(points)
    last = points[asof]
    dates = sorted(points)
    out = dict(empty)
    out.update({"last": last, "asof": asof.isoformat(), "stale": False})

    r7 = _ref(points, asof, "1w")
    r30 = _ref(points, asof, "1m")
    if r7 is not None:
        out["d1w_nom"] = round(last - r7, 4)
        out["d1w_pct"] = pct_change(last, r7)
    if r30 is not None:
        out["d1m_nom"] = round(last - r30, 4)
        out["d1m_pct"] = pct_change(last, r30)

    hist_dates = dates[-YEAR_WINDOW:]
    hist = [points[d] for d in hist_dates]
    if hist:
        le = sum(1 for v in hist if v <= last)
        out["pctile"] = round(100.0 * le / len(hist), 1)
    if len(hist) >= MIN_HISTORY:
        sd = pstdev(hist)
        if sd:
            out["z"] = round((last - mean(hist)) / sd, 2)

    # Velocity: largest move in the stress direction over any trailing-N-day
    # span in the last 1Y (signed, native units).
    if len(hist_dates) >= 8:
        vals = {d: points[d] for d in hist_dates}
        for key, span in (("vel7", 7), ("vel30", 30)):
            best = None
            for d in hist_dates:
                lo = d - timedelta(days=span)
                window = [v for dd, v in vals.items() if lo <= dd <= d]
                if len(window) >= 2:
                    mag = (points[d] - min(window) if stress_dir > 0
                           else max(window) - points[d])
                    if best is None or mag > best:
                        best = mag
            out[key] = round(stress_dir * best, 4) \
                if best is not None else None

    stale_after = CADENCE_DAYS.get(cadence, 5)
    out["stale"] = (date.today() - asof).days > stale_after
    return out


def _fmt_money(v) -> str:
    if v is None:
        return "n/a"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:,.1f}K"
    return f"{sign}${a:,.2f}"


def _fmt_signed_money(v) -> str:
    if v is None:
        return "n/a"
    return ("+" if v >= 0 else "") + _fmt_money(v)


def _fmt_bn(v) -> str:
    """Values already denominated in $bn (FINRA Treasury TRACE par)."""
    if v is None:
        return "n/a"
    return f"${v:,.2f}B" if v >= 0 else f"-${abs(v):,.2f}B"


def _fmt_signed_bn(v) -> str:
    if v is None:
        return "n/a"
    return f"+{_fmt_bn(v)}" if v >= 0 else _fmt_bn(v)


def _fmt_num(v, digits: int = 0) -> str:
    if v is None:
        return "n/a"
    if digits:
        return f"{v:,.{digits}f}"
    return f"{v:,.0f}"


def _fmt_signed_num(v, digits: int = 0) -> str:
    if v is None:
        return "n/a"
    return ("+" if v >= 0 else "-") + _fmt_num(abs(v), digits)


def _fmt_pct(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%"


def _delta_cell(nom, pct, nom_fmt=None) -> str:
    if nom is None and pct is None:
        return "n/a"
    n = (nom_fmt or _fmt_signed_money)(nom) if nom is not None else "n/a"
    return f"{n} ({_fmt_pct(pct)})"


def _kpi(label: str, st: dict, fmt, chg_fmt=None, vel_fmt=None) -> dict:
    """Contract KPI row: display strings + numeric pctile/z.

    fmt: level formatter; chg_fmt: signed nominal-delta formatter
    (default _fmt_signed_money); vel_fmt: velocity formatter (default fmt).
    """
    chg_fmt = chg_fmt or _fmt_signed_money
    vfmt = vel_fmt or fmt
    return {
        "label": label,
        "value": fmt(st["last"]),
        "chg_nom": chg_fmt(st["d1w_nom"]),
        "chg_pct": _fmt_pct(st["d1w_pct"]),
        "pctile": st["pctile"],
        "z": st["z"],
        "vel7": vfmt(st["vel7"]),
        "vel30": vfmt(st["vel30"]),
        "asof": st["asof"],
    }


def _chart_series(store, sid: str, name: str) -> dict | None:
    pts = _pts(store, sid)
    if not pts:
        return None
    ordered = sorted(pts)[-CHART_CAP:]
    return {"name": name,
            "points": [[_epoch_ms(d), round(pts[d], 4)] for d in ordered]}


def _grid_row(metric: str, st: dict, fmt, delta_fmt=None) -> list:
    return [metric, fmt(st["last"]),
            _delta_cell(st["d1w_nom"], st["d1w_pct"], delta_fmt),
            _delta_cell(st["d1m_nom"], st["d1m_pct"], delta_fmt),
            f"{st['z']:.2f}" if st["z"] is not None else "n/a",
            st["asof"] or "n/a"]


# ---------------------------------------------------------------- leg 1: tokenized

_RWA_CLASSES = ["treasuries", "gold", "stocks", "credit", "realestate"]
_RWA_CLASS_FALLBACK = {
    "treasuries": "Tokenized Treasuries",
    "gold": "Tokenized Gold",
    "stocks": "Tokenized Stocks",
    "credit": "Private Credit",
    "realestate": "Real Estate",
}

_TOKENIZED_DEFS = {
    "cycle:rwa-mcap-class-treasuries":
        "CoinGecko market cap of tokenized-Treasury tokens (top-3 tokens). "
        "Rises when on-chain T-bill products (BUIDL, OUSG, USDY…) mint.",
    "cycle:rwa-tvl-total":
        "DefiLlama-tracked total value locked across RWA protocols. TVL and "
        "market cap overlap — they are shown side by side, never summed.",
}


def _leg_tokenized(store) -> tuple[dict, list]:
    """Returns (LEG dict, heatmap-candidate rows)."""
    rwa = _doc_payload(store, "rwa")
    labels = rwa.get("class_labels") or _RWA_CLASS_FALLBACK

    mcap_total = _stats(_pts(store, "cycle:rwa-mcap-total"))
    tvl_total = _stats(_pts(store, "cycle:rwa-tvl-total"))
    treas_mcap = _stats(_pts(store, "cycle:rwa-mcap-class-treasuries"))
    treas_tvl = _stats(_pts(store, "cycle:rwa-tvl-class-treasuries"))

    kpis = [
        _kpi("Tokenized Treasuries mcap", treas_mcap, _fmt_money),
        _kpi("Total tokenized mcap", mcap_total, _fmt_money),
        _kpi("RWA protocol TVL", tvl_total, _fmt_money),
        _kpi("Treasury protocols TVL", treas_tvl, _fmt_money),
    ]

    chart = {
        "title": "Tokenized assets: market cap vs protocol TVL (separate series — not summed)",
        "source": "DefiLlama + CoinGecko (full institutional dataset: rwa.xyz, API key required)",
        "asof": max([s["asof"] for s in (mcap_total, tvl_total)
                     if s["asof"]] or [None]),
        "series": [s for s in (
            _chart_series(store, "cycle:rwa-mcap-total",
                          "Tokenized mcap (all classes)"),
            _chart_series(store, "cycle:rwa-tvl-total",
                          "RWA protocol TVL"),
        ) if s],
    }

    class_stats = {c: _stats(_pts(store, f"cycle:rwa-mcap-class-{c}"))
                   for c in _RWA_CLASSES}
    denom = mcap_total["last"] or 0
    rows = []
    for c in _RWA_CLASSES:
        st = class_stats[c]
        share = (f"{100 * st['last'] / denom:.1f}%"
                 if st["last"] and denom else "n/a")
        rows.append({"label": labels.get(c, c), "value": _fmt_money(st["last"]),
                     "share": share})
    structure = {"title": "By asset class (token market cap; TVL shown separately above)",
                 "rows": rows}

    grid_rows = [_grid_row("Total tokenized mcap", mcap_total, _fmt_money),
                 _grid_row("Total RWA protocol TVL", tvl_total, _fmt_money)]
    for c in _RWA_CLASSES:
        grid_rows.append(_grid_row(f"{labels.get(c, c)} mcap",
                                   class_stats[c], _fmt_money))
    grid = {"title": "Tokenized Treasuries — levels and deltas",
            "cols": ["Metric", "Level", "1W Δ", "1M Δ", "Z", "As Of"],
            "rows": grid_rows}

    heat = [
        {"series": "cycle:rwa-mcap-class-treasuries",
         "leg": "tokenized", "label": "Tokenized Treasuries mcap",
         "stats": treas_mcap, "fmt": _fmt_money,
         "definition": _TOKENIZED_DEFS["cycle:rwa-mcap-class-treasuries"]},
        {"series": "cycle:rwa-tvl-total",
         "leg": "tokenized", "label": "RWA protocol TVL",
         "stats": tvl_total, "fmt": _fmt_money,
         "definition": _TOKENIZED_DEFS["cycle:rwa-tvl-total"]},
    ]
    return ({"title": "Tokenized Treasuries", "kpis": kpis, "chart": chart,
             "structure": structure, "grid": grid}, heat)


# ---------------------------------------------------------------- leg 2: TRACE

_TRACE_CATS = [("bills", "Bills"), ("frns", "FRNs"),
               ("coupons", "Nominal coupons"), ("tips", "TIPS")]

_TRACE_DEFS = {
    "trace_adv20":
        "20-trading-day average of FINRA Treasury TRACE daily par volume "
        "($bn/day). ADV smooths the auction-calendar lumpiness of raw dailies.",
    "cycle:trace-ust-trades":
        "Daily FINRA Treasury TRACE trade count. Spikes on auction days and "
        "macro prints when dealers intermediate customer flow.",
}


def _rolling_mean(points: dict, window: int) -> dict:
    dates = sorted(points)
    out = {}
    for i, d in enumerate(dates):
        seg = dates[max(0, i - window + 1):i + 1]
        if len(seg) == window:
            out[d] = sum(points[x] for x in seg) / window
    return out


def _leg_trace(store) -> tuple[dict, list]:
    par = _pts(store, "cycle:trace-ust-par")          # $bn/day
    trades = _pts(store, "cycle:trace-ust-trades")
    par_ats = _pts(store, "cycle:trace-ust-par-ats")
    par_d2c = _pts(store, "cycle:trace-ust-par-d2c")
    onrun = _pts(store, "cycle:trace-ust-onrun-par")
    offrun = _pts(store, "cycle:trace-ust-offrun-par")
    cat_par = {k: _pts(store, f"cycle:trace-ust-{k}-par")
               for k, _ in _TRACE_CATS}

    st_par = _stats(par)
    st_trades = _stats(trades)
    st_adv = _stats(_rolling_mean(par, 20))
    st_ats = _stats(par_ats)
    st_d2c = _stats(par_d2c)

    # Venue / product shares from the latest common date.
    def share(num_pts, den_pts):
        common = sorted(set(num_pts) & set(den_pts))
        if not common:
            return None
        d = common[-1]
        den = den_pts[d]
        return (100.0 * num_pts[d] / den) if den else None

    ats_share = share(par_ats, par)
    bills_share = share(cat_par["bills"], par)

    kpis = [
        _kpi("20-day ADV, par (est.)", st_adv, _fmt_bn, _fmt_signed_bn,
             _fmt_bn),
        _kpi("Daily par volume", st_par, _fmt_bn, _fmt_signed_bn, _fmt_bn),
        _kpi("Daily trade count", st_trades, lambda v: _fmt_num(v),
             _fmt_signed_num),
        {"label": "ATS share of par",
         "value": f"{ats_share:.1f}%" if ats_share is not None else "n/a",
         "chg_nom": "n/a", "chg_pct": "n/a", "pctile": None, "z": None,
         "vel7": "n/a", "vel30": "n/a", "asof": st_par["asof"]},
        {"label": "Bills share of par",
         "value": f"{bills_share:.1f}%" if bills_share is not None else "n/a",
         "chg_nom": "n/a", "chg_pct": "n/a", "pctile": None, "z": None,
         "vel7": "n/a", "vel30": "n/a", "asof": st_par["asof"]},
    ]

    chart = {
        "title": "Treasury TRACE: daily par ($bn) and trade count",
        "source": "FINRA Treasury TRACE aggregates (keyless CDN)",
        "asof": max([s["asof"] for s in (st_par, st_trades)
                     if s["asof"]] or [None]),
        "series": [s for s in (
            _chart_series(store, "cycle:trace-ust-par", "Par volume ($bn)"),
            _chart_series(store, "cycle:trace-ust-trades", "Trade count"),
        ) if s],
    }

    tot = st_par["last"] or 0
    rows = []
    for k, label in _TRACE_CATS:
        v = (cat_par[k].get(max(cat_par[k])) if cat_par[k] else None)
        rows.append({"label": label, "value": _fmt_money(v),
                     "share": f"{100 * v / tot:.1f}%" if v and tot else "n/a"})
    structure = {"title": "Par by product ($bn, latest day)",
                 "rows": rows}

    grid_rows = [
        _grid_row("Total par ($bn)", st_par, _fmt_bn, _fmt_signed_bn),
        _grid_row("Total trades", st_trades, lambda v: _fmt_num(v),
                  _fmt_signed_num),
        _grid_row("20-day ADV, par (est.)", st_adv, _fmt_bn,
                  _fmt_signed_bn),
    ]
    for k, label in _TRACE_CATS:
        grid_rows.append(_grid_row(f"{label} par ($bn)",
                                   _stats(cat_par[k]), _fmt_bn,
                                   _fmt_signed_bn))
    grid_rows.append(_grid_row("ATS par ($bn)", st_ats, _fmt_bn,
                               _fmt_signed_bn))
    grid_rows.append(_grid_row("Dealer-to-customer par ($bn)", st_d2c,
                               _fmt_bn, _fmt_signed_bn))
    grid_rows.append(_grid_row("On-the-run par ($bn)", _stats(onrun),
                               _fmt_bn, _fmt_signed_bn))
    grid_rows.append(_grid_row("Off-the-run par ($bn)", _stats(offrun),
                               _fmt_bn, _fmt_signed_bn))
    grid = {"title": "Treasury TRACE — levels and deltas",
            "cols": ["Metric", "Level", "1W Δ", "1M Δ", "Z", "As Of"],
            "rows": grid_rows}

    heat = [
        {"series": "trace_adv20", "leg": "trace",
         "label": "Treasury TRACE 20D ADV (par)",
         "stats": st_adv, "fmt": _fmt_bn,
         "definition": _TRACE_DEFS["trace_adv20"]},
        {"series": "cycle:trace-ust-trades", "leg": "trace",
         "label": "Treasury TRACE daily trades",
         "stats": st_trades, "fmt": lambda v: _fmt_num(v),
         "definition": _TRACE_DEFS["cycle:trace-ust-trades"]},
    ]
    return ({"title": "Treasury Cash (TRACE)", "kpis": kpis, "chart": chart,
             "structure": structure, "grid": grid}, heat)


# ---------------------------------------------------------------- leg 3: ETFs

FI_TREASURY = ["BIL", "EDV", "GOVT", "IEF", "SCHQ", "SCHR", "SGOV", "SHV",
               "SHY", "SPTL", "TBIL", "TLT", "TYD", "UST", "VGIT", "VGLT",
               "VGSH", "ZROZ"]
LEVERAGED = {"TYD", "UST"}  # 3x leveraged Treasury ETFs

_ETF_DEFS = {
    "etf_agg_aum":
        "Sum of net assets across the 18 Treasury ETFs. AUM rises on both "
        "price appreciation and creations.",
    "etf_agg_flow":
        "Estimated aggregate net flow = Σ (Δshares × NAV) per ETF across the "
        "latest snapshot pair. An estimate, not a canonical flow print.",
}


def _leg_etfs(store) -> tuple[dict, list]:
    etf_doc = _doc_payload(store, "etfflows")
    funds = etf_doc.get("funds") or {}

    names = {}
    for t in FI_TREASURY:
        f = funds.get(t) or {}
        nm = f.get("name") or t
        if t in LEVERAGED:
            nm = f"{nm} [3× leveraged]"
        names[t] = nm

    aum_pts, nav_pts, shares_pts = {}, {}, {}
    for t in FI_TREASURY:
        aum_pts[t] = _pts(store, f"cycle:etf-{t}-aum")
        nav_pts[t] = _pts(store, f"cycle:etf-{t}-nav")
        shares_pts[t] = _pts(store, f"cycle:etf-{t}-shares")

    # Aggregate AUM: date-aligned sum.
    agg_aum: dict = {}
    for t in FI_TREASURY:
        for d, v in aum_pts[t].items():
            agg_aum[d] = agg_aum.get(d, 0.0) + v
    st_agg = _stats(agg_aum)

    # Per-ETF estimated flow = (shares_t − shares_{t−1}) × nav_t.
    per_flow: dict[str, tuple] = {}  # t -> (flow, asof)
    agg_flow: dict = {}
    for t in FI_TREASURY:
        sh = shares_pts[t]
        nv = nav_pts[t]
        if len(sh) < 2:
            continue
        ds = sorted(sh)
        d1, d0 = ds[-1], ds[-2]
        nav = nv.get(d1)
        if nav is None:
            continue
        flow = (sh[d1] - sh[d0]) * nav
        per_flow[t] = (flow, d1.isoformat())
        agg_flow[d1] = agg_flow.get(d1, 0.0) + flow
    st_flow = _stats(agg_flow)
    tot_flow = sum(f for f, _ in per_flow.values())
    flow_asof = max((a for _, a in per_flow.values()), default=None)

    st_tlt = _stats(aum_pts["TLT"])
    st_sgov = _stats(aum_pts["SGOV"])

    kpis = [
        _kpi("Aggregate AUM (18 Treasury ETFs)", st_agg, _fmt_money),
        {"label": "Est. net flow, latest snapshot (Σ Δshares×NAV)",
         "value": _fmt_signed_money(tot_flow) if per_flow else "n/a",
         "chg_nom": "n/a", "chg_pct": "n/a", "pctile": None, "z": None,
         "vel7": "n/a", "vel30": "n/a", "asof": flow_asof},
        _kpi("TLT AUM", st_tlt, _fmt_money),
        _kpi("SGOV AUM", st_sgov, _fmt_money),
    ]

    chart = {
        "title": "Treasury ETFs: aggregate AUM and estimated net flow",
        "source": "iShares / FMP / Yahoo (flows = Δshares × NAV, estimates)",
        "asof": st_agg["asof"],
        "series": [],
    }
    if agg_aum:
        ordered = sorted(agg_aum)[-CHART_CAP:]
        chart["series"].append({
            "name": "Aggregate AUM ($)",
            "points": [[_epoch_ms(d), round(agg_aum[d], 2)] for d in ordered]})
    if agg_flow:
        ordered = sorted(agg_flow)[-CHART_CAP:]
        chart["series"].append({
            "name": "Est. net flow ($) — Δshares×NAV",
            "points": [[_epoch_ms(d), round(agg_flow[d], 2)]
                       for d in ordered]})

    latest_aum = {t: (aum_pts[t][max(aum_pts[t])] if aum_pts[t] else None)
                  for t in FI_TREASURY}
    denom = sum(v for v in latest_aum.values() if v) or 0
    ranked = sorted(FI_TREASURY,
                    key=lambda t: latest_aum[t] or 0, reverse=True)
    rows = []
    for t in ranked[:10]:
        v = latest_aum[t]
        rows.append({"label": f"{t} — {names[t]}", "value": _fmt_money(v),
                     "share": f"{100 * v / denom:.1f}%" if v and denom else "n/a"})
    rest = sum(v for t, v in latest_aum.items()
               if t not in ranked[:10] and v)
    if rest:
        rows.append({"label": "Other (8)", "value": _fmt_money(rest),
                     "share": f"{100 * rest / denom:.1f}%" if denom else "n/a"})
    structure = {"title": "AUM by ETF (top 10 + rest)", "rows": rows}

    grid_rows = [
        _grid_row("Aggregate AUM", st_agg, _fmt_money),
        _grid_row("Aggregate est. flow (Δshares×NAV)", st_flow, _fmt_money),
    ]
    for t in ranked[:8]:
        grid_rows.append(_grid_row(f"{t} AUM", _stats(aum_pts[t]),
                                   _fmt_money))
    grid = {"title": "Treasury ETFs — levels and deltas",
            "cols": ["Metric", "Level", "1W Δ", "1M Δ", "Z", "As Of"],
            "rows": grid_rows}

    heat = [
        {"series": "etf_agg_aum", "leg": "etfs",
         "label": "Treasury ETF aggregate AUM",
         "stats": st_agg, "fmt": _fmt_money,
         "definition": _ETF_DEFS["etf_agg_aum"]},
        {"series": "etf_agg_flow", "leg": "etfs",
         "label": "Treasury ETF est. net flow",
         "stats": st_flow, "fmt": _fmt_money,
         "definition": _ETF_DEFS["etf_agg_flow"]},
    ]
    return ({"title": "Treasury ETFs", "kpis": kpis, "chart": chart,
             "structure": structure, "grid": grid}, heat)


# ---------------------------------------------------------------- leg 4: options

_OPT_SYMS = ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "USO", "XLE",
             "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META"]

_OPT_DEFS = {
    "cycle:opt-TLT-gex":
        "TLT total gamma exposure ($B; calls +, puts −) = Σ gamma×OI×100×"
        "spot²/1bn. The Treasury proxy: dealer gamma positioning in rates "
        "options. n/a when the chain has no greeks (Yahoo fallback).",
    "cycle:opt-TLT-atm-iv":
        "TLT at-the-money implied volatility (%) on the nearest expiry — "
        "the market's priced rate-vol for the Treasury proxy.",
}


def _leg_options(store) -> tuple[dict, list]:
    opt_doc = _doc_payload(store, "options")
    symbols = opt_doc.get("symbols") or {}

    gex = {s: _stats(_pts(store, f"cycle:opt-{s}-gex")) for s in _OPT_SYMS}
    pc_oi = {s: _stats(_pts(store, f"cycle:opt-{s}-pc-oi")) for s in _OPT_SYMS}
    pc_vol = {s: _stats(_pts(store, f"cycle:opt-{s}-pc-vol")) for s in _OPT_SYMS}
    atm_iv = {s: _stats(_pts(store, f"cycle:opt-{s}-atm-iv")) for s in _OPT_SYMS}

    kpis = [
        _kpi("TLT GEX ($B) — Treasury proxy", gex["TLT"], _fmt_bn,
             _fmt_signed_bn, _fmt_bn),
        _kpi("TLT put/call OI", pc_oi["TLT"], lambda v: _fmt_num(v, 2),
             lambda v: _fmt_signed_num(v, 2)),
        _kpi("TLT put/call volume", pc_vol["TLT"], lambda v: _fmt_num(v, 2),
             lambda v: _fmt_signed_num(v, 2)),
        _kpi("TLT ATM IV (%)", atm_iv["TLT"], lambda v: _fmt_num(v, 1),
             lambda v: _fmt_signed_num(v, 1)),
    ]

    chart = {
        "title": "Gamma exposure ($B): TLT vs SPY",
        "source": "CBOE 15-min delayed chains; Yahoo fallback where flagged",
        "asof": max([s["asof"] for s in (gex["TLT"], gex["SPY"])
                     if s["asof"]] or [None]),
        "series": [s for s in (
            _chart_series(store, "cycle:opt-TLT-gex", "TLT GEX ($B)"),
            _chart_series(store, "cycle:opt-SPY-gex", "SPY GEX ($B)"),
        ) if s],
    }

    gex_total = sum(s["last"] for s in gex.values()
                    if s["last"] is not None) or 0
    rows = []
    for s in sorted(_OPT_SYMS,
                    key=lambda x: abs(gex[x]["last"] or 0), reverse=True):
        st = gex[s]
        v = st["last"]
        detail = f"${v:,.2f}B" if v is not None else "n/a"
        src = (symbols.get(s) or {}).get("source")
        if src and src != "cboe":
            detail += f" ({src})"
        rows.append({
            "label": f"{s}{' — Treasury proxy' if s == 'TLT' else ''}",
            "value": detail,
            "share": (f"{100 * v / gex_total:.1f}%"
                      if v is not None and gex_total else "n/a"),
        })
    structure = {"title": "Latest GEX by symbol (share of 14-symbol total)",
                 "rows": rows}

    grid_rows = []
    for s in _OPT_SYMS:
        grid_rows.append(_grid_row(f"{s} GEX ($B)", gex[s], _fmt_bn,
                                   _fmt_signed_bn))
    grid_rows.append(_grid_row("TLT put/call OI", pc_oi["TLT"],
                               lambda v: _fmt_num(v, 2),
                               lambda v: _fmt_signed_num(v, 2)))
    grid_rows.append(_grid_row("TLT put/call volume", pc_vol["TLT"],
                               lambda v: _fmt_num(v, 2),
                               lambda v: _fmt_signed_num(v, 2)))
    grid_rows.append(_grid_row("TLT ATM IV (%)", atm_iv["TLT"],
                               lambda v: _fmt_num(v, 1),
                               lambda v: _fmt_signed_num(v, 1)))
    grid_rows.append(_grid_row("TLT max pain ($)", _stats(_pts(store, "cycle:opt-TLT-maxpain")),
                               lambda v: _fmt_num(v, 2)))
    grid = {"title": "Options — levels and deltas",
            "cols": ["Metric", "Level", "1W Δ", "1M Δ", "Z", "As Of"],
            "rows": grid_rows}

    heat = [
        {"series": "cycle:opt-TLT-gex", "leg": "options",
         "label": "TLT GEX ($B)",
         "stats": gex["TLT"],
         "fmt": _fmt_bn,
         "definition": _OPT_DEFS["cycle:opt-TLT-gex"]},
        {"series": "cycle:opt-TLT-atm-iv", "leg": "options",
         "label": "TLT ATM IV (%)",
         "stats": atm_iv["TLT"],
         "fmt": lambda v: _fmt_num(v, 1),
         "definition": _OPT_DEFS["cycle:opt-TLT-atm-iv"]},
    ]
    return ({"title": "Options", "kpis": kpis, "chart": chart,
             "structure": structure, "grid": grid}, heat)


# ---------------------------------------------------------------- leg 5: futures

_COT = [("2y", "2Y"), ("5y", "5Y"), ("10y", "10Y"), ("30y", "30Y")]
_TFF_TENORS = [("tu", "2Y (TU)"), ("fv", "5Y (FV)"), ("ty", "10Y (TY)"),
               ("uxy", "Ultra 10Y (UXY)"), ("us", "30Y (US)"),
               ("wn", "Ultra bond (WN)")]
_TFF_CODES = {"2y": "042601", "5y": "044601", "10y": "043602", "30y": "020601"}

_FUT_DEFS = {
    "cycle:tff-lf_treas_net_position":
        "OFR TFF: leveraged-funds net Treasury futures position ($). The "
        "hedge-fund basis-trade footprint — negative = net short cash-futures "
        "basis. Weekly.",
    "cycle:cot-ust-10y":
        "CFTC legacy COT: non-commercial net 10Y T-note futures (contracts). "
        "Spec positioning in the benchmark contract. Weekly (Tuesdays).",
}


def _leg_futures(store) -> tuple[dict, list]:
    tff_lf = _stats(_pts(store, "cycle:tff-lf_treas_net_position"), "weekly",
                    -1)
    tff_ai = _stats(_pts(store, "cycle:tff-ai_treas_net_position"), "weekly")
    tff_di = _stats(_pts(store, "cycle:tff-di_treas_net_position"), "weekly")
    tff_dv01 = _stats(_pts(store, "cycle:tff-lf_treas_net_dv01"), "weekly",
                      -1)
    tff_10y = _stats(_pts(store, "cycle:tff-lf_treas_net_pos10yreqv"),
                     "weekly", -1)
    tenor = {k: _stats(_pts(store, f"cycle:tff-lf_{k}_net_position"),
                       "weekly", -1)
             for k, _ in _TFF_TENORS}
    cot = {k: _stats(_pts(store, f"cycle:cot-ust-{k}"), "weekly", -1)
           for k, _ in _COT}
    hf_long = _stats(_pts(store, "ofr:FPF-ASSETCLASS_LTREASURY_SUM"),
                     "quarterly")
    socrata = {k: _stats(_pts(store, f"cftc:tff:{code}:lev_money"),
                         "weekly", -1)
               for k, code in _TFF_CODES.items()}

    kpis = [
        _kpi("LF net Treasury futures (OFR TFF)", tff_lf, _fmt_money),
        _kpi("Asset managers net", tff_ai, _fmt_money),
        _kpi("Dealers net", tff_di, _fmt_money),
        _kpi("CFTC 10Y net non-commercial (contracts)", cot["10y"],
             lambda v: _fmt_num(v), _fmt_signed_num),
    ]

    chart = {
        "title": "Treasury futures net positioning ($): leveraged funds / asset managers / dealers",
        "source": "OFR Traders in Financial Futures (TFF), weekly",
        "asof": max([s["asof"] for s in (tff_lf, tff_ai, tff_di)
                     if s["asof"]] or [None]),
        "series": [s for s in (
            _chart_series(store, "cycle:tff-lf_treas_net_position",
                          "Leveraged funds net ($)"),
            _chart_series(store, "cycle:tff-ai_treas_net_position",
                          "Asset managers net ($)"),
            _chart_series(store, "cycle:tff-di_treas_net_position",
                          "Dealers net ($)"),
        ) if s],
    }

    denom = sum(abs(s["last"]) for s in tenor.values()
                if s["last"] is not None) or 0
    rows = []
    for k, label in _TFF_TENORS:
        v = tenor[k]["last"]
        rows.append({"label": f"LF net {label}", "value": _fmt_signed_money(v),
                     "share": (f"{100 * abs(v) / denom:.1f}%"
                               if v is not None and denom else "n/a")})
    structure = {"title": "Leveraged-funds net by tenor (share of gross tenor exposure)",
                 "rows": rows}

    grid_rows = [
        _grid_row("LF net Treasury ($)", tff_lf, _fmt_money),
        _grid_row("LF net DV01 ($)", tff_dv01, _fmt_money),
        _grid_row("LF net 10Y-equiv ($)", tff_10y, _fmt_money),
        _grid_row("AI net Treasury ($)", tff_ai, _fmt_money),
        _grid_row("DI net Treasury ($)", tff_di, _fmt_money),
        _grid_row("HF long UST exposure, OFR FPF ($)", hf_long, _fmt_money),
    ]
    for k, label in _COT:
        grid_rows.append(_grid_row(
            f"CFTC {label} net non-commercial (contracts)", cot[k],
            lambda v: _fmt_num(v), _fmt_signed_num))
        grid_rows.append(_grid_row(
            f"Socrata TFF {label} LF net (contracts)", socrata[k],
            lambda v: _fmt_num(v), _fmt_signed_num))
    grid = {"title": "Treasury futures — levels and deltas",
            "cols": ["Metric", "Level", "1W Δ", "1M Δ", "Z", "As Of"],
            "rows": grid_rows}

    heat = [
        {"series": "cycle:tff-lf_treas_net_position", "leg": "futures",
         "label": "LF net Treasury futures (TFF, $)",
         "stats": tff_lf, "fmt": _fmt_money,
         "definition": _FUT_DEFS["cycle:tff-lf_treas_net_position"]},
        {"series": "cycle:cot-ust-10y", "leg": "futures",
         "label": "CFTC 10Y net non-commercial (contracts)",
         "stats": cot["10y"], "fmt": lambda v: _fmt_num(v),
         "definition": _FUT_DEFS["cycle:cot-ust-10y"]},
    ]
    return ({"title": "Treasury Futures", "kpis": kpis, "chart": chart,
             "structure": structure, "grid": grid}, heat)


# ---------------------------------------------------------------- synthesis

_LEG_CADENCE = {"tokenized": "daily", "trace": "daily", "etfs": "daily",
                "options": "daily", "futures": "weekly"}


def _synthesis(legs: dict, heat_candidates: list) -> dict:
    heat = []
    for c in heat_candidates:
        st = c["stats"]
        if st["last"] is None:
            continue  # leg has no live data yet — stays out, shows n/a
        heat.append({
            "leg": c["leg"],
            "label": c["label"],
            "level": c["fmt"](st["last"]),
            "d1w_nom": st["d1w_nom"],
            "d1w_pct": st["d1w_pct"],
            "d1m_nom": st["d1m_nom"],
            "d1m_pct": st["d1m_pct"],
            "z": st["z"],
            "vel7": st["vel7"],
            "vel30": st["vel30"],
            "stale": st["stale"],
        })

    lead = None
    scored = [h for h in heat if h["z"] is not None]
    if scored:
        top = max(scored, key=lambda h: abs(h["z"]))
        if abs(top["z"]) >= LEAD_Z_MIN:
            cand = next(c for c in heat_candidates
                        if c["label"] == top["label"])
            lead = {"series": cand["series"], "leg": top["leg"],
                    "z": top["z"], "definition": cand["definition"],
                    "asof": cand["stats"]["asof"]}

    legs_with_data = {h["leg"] for h in heat}
    firing = sorted({h["leg"] for h in heat
                     if h["z"] is not None and abs(h["z"]) >= 2.0})
    quiet = sorted({h["leg"] for h in heat
                    if h["z"] is not None and abs(h["z"]) < 1.0})
    missing = sorted(set(_LEG_CADENCE) - legs_with_data)
    if missing and not legs_with_data:
        regime = ("No live data in any leg yet — legs render n/a until "
                  "their fetchers run.")
    else:
        bits = []
        if firing:
            bits.append(f"{len(firing)} of 5 legs elevated (|z| ≥ 2): "
                        + ", ".join(firing))
        if quiet:
            bits.append(f"near trailing-1Y norms: {', '.join(quiet)}")
        if missing:
            bits.append(f"no live data yet: {', '.join(missing)}")
        if not bits and scored:
            top = max(scored, key=lambda h: abs(h["z"]))
            bits.append(f"no leg outside normal range; largest |z| is "
                        f"{top['label']} (z={top['z']:+.2f})")
        regime = "Cross-leg state — " + "; ".join(bits) + "."

    freshness = []
    for leg in ("tokenized", "trace", "etfs", "options", "futures"):
        cands = [c for c in heat_candidates if c["leg"] == leg]
        asofs = [c["stats"]["asof"] for c in cands if c["stats"]["asof"]]
        asof = max(asofs) if asofs else None
        cadence = _LEG_CADENCE[leg]
        stale = True
        if asof:
            stale = ((date.today() -
                      date.fromisoformat(asof)).days
                     > CADENCE_DAYS[cadence])
        freshness.append({"leg": leg, "asof": asof, "stale": stale,
                          "cadence": cadence})

    return {"regime": regime, "lead": lead, "heatmap": heat,
            "freshness": freshness}


# ---------------------------------------------------------------- entry point

def build_activity(store) -> dict:
    """Build the full multi-asset activity payload from the store.

    Returns the exact /api/activity contract. Never raises: a failing leg
    degrades to nulls so one bad series can't 500 the view.
    """
    legs: dict = {}
    heat_candidates: list = []
    builders = (
        ("tokenized", _leg_tokenized),
        ("trace", _leg_trace),
        ("etfs", _leg_etfs),
        ("options", _leg_options),
        ("futures", _leg_futures),
    )
    for key, builder in builders:
        try:
            leg, heat = builder(store)
        except Exception:  # noqa: BLE001 — degrade, don't fail
            log.exception("activity: leg %s failed", key)
            leg, heat = ({"title": key, "kpis": [], "chart": {
                "title": "", "source": "", "asof": None, "series": []},
                "structure": {"title": "", "rows": []},
                "grid": {"title": "",
                         "cols": ["Metric", "Level", "1W Δ", "1M Δ",
                                  "Z", "As Of"], "rows": []}}, [])
        legs[key] = leg
        heat_candidates.extend(heat)

    return {
        "asof": date.today().isoformat(),
        "legs": legs,
        "synthesis": _synthesis(legs, heat_candidates),
    }
