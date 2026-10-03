"""Automated market-digest generation from stored series and panel docs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev

from collector.changes import apply_transform, ref_close
from collector.config import Config
from collector.store import Store

ANOMALY_Z = 2.2
TREND_Z = 1.15
HISTORY_MIN = 20
WINDOW = 180


@dataclass(frozen=True)
class DigestSeries:
    series_id: str
    store_id: str
    name: str
    unit: str
    transform: str = "none"


def _series_catalog(cfg: Config) -> list[DigestSeries]:
    items = [
        *[
            DigestSeries(s.id, f"macro:{s.id}", s.name, s.unit, s.transform)
            for s in cfg.series
        ],
        *[
            DigestSeries(s.id, f"cycle:{s.id}", s.name, s.unit, s.transform)
            for s in cfg.cycle_series
            if not s.hidden
        ],
        *[
            DigestSeries(i.symbol, f"idx:{i.symbol}", i.name, "px")
            for i in cfg.indexes
        ],
        *[
            DigestSeries(f"{b.country}{b.tenor}", f"yield:{b.country}{b.tenor}",
                         f"{b.country} {b.tenor} yield", "%")
            for b in cfg.bonds
        ],
        *[
            DigestSeries(f"{c.country}CB", f"cb:{c.country}", c.label, "%")
            for c in cfg.cb_rates
        ],
        *[
            DigestSeries(a.supply_id, f"ref:{a.supply_id}", a.supply_label, "%")
            for a in cfg.refs.aave
        ],
        *[
            DigestSeries(a.borrow_id, f"ref:{a.borrow_id}", a.borrow_label, "%")
            for a in cfg.refs.aave
        ],
        *[
            DigestSeries(p.implied_id, f"ref:{p.implied_id}", p.implied_label, "%")
            for p in cfg.refs.pendle
        ],
        *[
            DigestSeries(p.underlying_id, f"ref:{p.underlying_id}", p.underlying_label, "%")
            for p in cfg.refs.pendle
        ],
        *[
            DigestSeries(f.id, f"ref:{f.id}", f.label, "%")
            for f in cfg.refs.funding
        ],
        # GSE retained portfolios ($M, monthly): hardcoded — the gse job has
        # no config block, its store keys are fixed (see fetchers/gse.py).
        DigestSeries("gse-fannie", "gse:fannie-retained", "Fannie Mae retained portfolio", "$m"),
        DigestSeries("gse-freddie", "gse:freddie-retained", "Freddie Mac retained portfolio", "$m"),
        DigestSeries("gse-freddie-agency", "gse:freddie-agency", "Freddie Mac agency MBS in portfolio", "$m"),
    ]
    deduped: list[DigestSeries] = []
    seen: set[str] = set()
    for item in items:
        if item.store_id in seen:
            continue
        seen.add(item.store_id)
        deduped.append(item)
    return deduped


def _fmt_value(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "px":
        return f"{value:,.1f}"
    if unit == "$m":  # GSE retained portfolios, $ millions -> $ billions
        return f"${value / 1000:,.1f}B"
    if unit in {"k", "m", "idx", "pts", "ratio"}:
        return f"{value:.2f}"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def _coverage(store: Store, tracked_series: int, active_series: int) -> dict:
    macro = store.doc("macro_calendar")
    news = store.doc("news")
    defi = store.doc("defi_pools")
    midnight = store.doc("midnight_curve")
    morpho = store.doc("morpho_markets")
    refs = store.doc("rate_refs")
    return {
        "tracked_series": tracked_series,
        "active_series": active_series,
        "upcoming_macro": len((macro.payload.get("releases") if macro else []) or []),
        "headlines": len((news.payload.get("items") if news else []) or []),
        "defi_rows": len((defi.payload.get("rows") if defi else []) or []),
        "midnight_rows": len((midnight.payload.get("rows") if midnight else []) or []),
        "morpho_rows": len((morpho.payload.get("rows") if morpho else []) or []),
        "refs_rows": len((refs.payload.get("rows") if refs else []) or []),
    }


def _ref_on_or_before(hist: dict, asof: date, days_back: int) -> float | None:
    """Latest point on or before asof - days_back (ref_close has no 3m horizon)."""
    target = asof - timedelta(days=days_back)
    prior = [d for d in hist if d <= target]
    return hist[max(prior)] if prior else None


def _mbs_note(store: Store, gse_payload: dict | None) -> dict | None:
    """MBS plumbing read-out, only when the GSE data is real.

    Fannie Mae + Freddie Mac retained portfolios ($M, monthly) plus the MBB
    price 3m move. Surfaces the "who is buying mortgages" story (record MBB
    outflows, GSEs on hold) without inventing flow quantities."""
    if not gse_payload:
        return None
    try:
        pts_mbb = store.points("cycle:mbb-us")
    except Exception:  # noqa: BLE001
        pts_mbb = {}
    series = []
    total = 0.0
    chg_3m_total: float | None = 0.0
    for doc_key, store_key, label in (
        ("fannie_retained", "gse:fannie-retained", "Fannie Mae"),
        ("freddie_retained", "gse:freddie-retained", "Freddie Mac"),
    ):
        entry = (gse_payload or {}).get(doc_key) or {}
        value = entry.get("value_usd_m")
        asof = entry.get("date")
        if value is None or asof is None:
            continue
        total += value
        try:
            hist = store.points(store_key)
        except Exception:  # noqa: BLE001
            hist = {}
        chg = None
        if hist:
            asof_d = datetime.fromisoformat(asof).date()
            ref = _ref_on_or_before(hist, asof_d, 90)
            chg = None if ref is None else round(value - ref, 1)
        if chg is None:
            chg_3m_total = None
        elif chg_3m_total is not None:
            chg_3m_total += chg
        series.append({"label": label, "value_usd_m": value, "asof": asof,
                       "chg_3m_m": chg})
    if not series:
        return None
    mbb_chg = None
    if pts_mbb:
        asof_d = max(pts_mbb)
        ref = _ref_on_or_before(pts_mbb, asof_d, 90)
        if ref:
            mbb_chg = round(100.0 * (pts_mbb[asof_d] - ref) / ref, 2)
    note = {
        "asof": series[0]["asof"],
        "combined_retained_usd_m": round(total, 1),
        "combined_chg_3m_m": chg_3m_total,
        "series": series,
        "mbb_3m_pct": mbb_chg,
    }
    if chg_3m_total is not None and chg_3m_total < 0 and (mbb_chg or 0) < 0:
        note["stress"] = (
            f"GSE retained portfolios shrinking "
            f"(${chg_3m_total / 1000:+.1f}B 3m) while MBB is down {mbb_chg:.1f}% 3m — "
            f"mortgage demand is leaving, not arriving."
        )
    elif chg_3m_total is not None and chg_3m_total < 0:
        note["stress"] = (
            f"GSE retained portfolios shrinking "
            f"(${chg_3m_total / 1000:+.1f}B 3m) — no marginal GSE bid for MBS."
        )
    return note


def _vol_note(store: Store) -> dict | None:
    """Vol dashboard read-out: regime line + most extreme implied readings."""
    doc = store.doc("voldash")
    if doc is None:
        return None
    p = doc.payload
    rows = p.get("rows") or []
    priced = [r for r in rows if r.get("pctile_1y") is not None]
    if not priced:
        return None
    richest = max(priced, key=lambda r: r["pctile_1y"])
    cheapest = min(priced, key=lambda r: r["pctile_1y"])
    return {
        "asof": p.get("asof"),
        "regime": p.get("regime"),
        "n_rich": p.get("n_rich", 0),
        "n_cheap": p.get("n_cheap", 0),
        "richest": (richest["ticker"], richest["implied"], richest["pctile_1y"]),
        "cheapest": (cheapest["ticker"], cheapest["implied"], cheapest["pctile_1y"]),
        "beta_vix_spx": (p.get("beta") or {}).get("vix_spx"),
    }


def _movers_note(store: Store) -> dict | None:
    """Single-stock extremes: the single most positive/negative z-move per
    index across both windows."""
    doc = store.doc("movers")
    if doc is None:
        return None
    out = {"asof": doc.payload.get("asof"), "indexes": {}}
    for idx_id, idx in (doc.payload.get("indexes") or {}).items():
        extremes = {}
        for win in ("win5d", "win20d"):
            w = idx.get(win) or {}
            up = (w.get("up") or [None])[0]
            down = (w.get("down") or [None])[0]
            extremes[win] = {"up": up, "down": down}
        out["indexes"][idx_id] = {"label": idx.get("label"), **extremes}
    if not out["indexes"]:
        return None
    return out


def build_digest(store: Store, cfg: Config, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    risk_doc = store.doc("risk_summary")
    risk_payload = risk_doc.payload if risk_doc else None
    cr_doc = store.doc("country_risk")
    cr_payload = cr_doc.payload if cr_doc else None
    xc_doc = store.doc("xcorr")
    xc_payload = xc_doc.payload if xc_doc else None
    gse_doc = store.doc("gse")
    gse_payload = gse_doc.payload if gse_doc else None
    mbs_note = _mbs_note(store, gse_payload)
    vol_note = _vol_note(store)
    movers_note = _movers_note(store)
    anomalies = []
    trends = []
    active_series = 0
    for item in _series_catalog(cfg):
        points = apply_transform(store.points(item.store_id), item.transform)
        if len(points) < 2:
            continue
        ordered = sorted(points.items())
        active_series += 1
        latest_date, latest_value = ordered[-1]
        baseline = [v for _, v in ordered[:-1][-WINDOW:]]
        if len(baseline) >= HISTORY_MIN:
            sigma = pstdev(baseline)
            if sigma > 0:
                level_mean = mean(baseline)
                z_score = round((latest_value - level_mean) / sigma, 2)
                if abs(z_score) >= ANOMALY_Z:
                    anomalies.append({
                        "id": f"anomaly:{item.series_id}",
                        "series_id": item.series_id,
                        "name": item.name,
                        "unit": item.unit,
                        "value": round(latest_value, 2),
                        "direction": "up" if z_score > 0 else "down",
                        "z_score": z_score,
                        "summary": f"{_fmt_value(latest_value, item.unit)} is {abs(z_score):.2f}σ "
                                   f"{'above' if z_score > 0 else 'below'} trend",
                        "as_of": latest_date.isoformat(),
                    })
                ref_1m = ref_close(points, latest_date, "1m")
                if ref_1m is not None:
                    delta = round(latest_value - ref_1m, 2)
                    trend_score = round(abs(delta) / sigma, 2)
                    if delta != 0 and trend_score >= TREND_Z:
                        trends.append({
                            "id": f"trend:{item.series_id}",
                            "series_id": item.series_id,
                            "name": item.name,
                            "unit": item.unit,
                            "value": round(latest_value, 2),
                            "previous": round(ref_1m, 2),
                            "delta": delta,
                            "direction": "up" if delta > 0 else "down",
                            "trend_score": trend_score,
                            "summary": f"{delta:+.2f} vs 1m ({trend_score:.2f}σ move)",
                            "as_of": latest_date.isoformat(),
                        })
    anomalies.sort(key=lambda row: abs(row["z_score"]), reverse=True)
    trends.sort(key=lambda row: row["trend_score"], reverse=True)
    coverage = _coverage(store, tracked_series=len(_series_catalog(cfg)), active_series=active_series)
    lead = anomalies[0]["name"] if anomalies else trends[0]["name"] if trends else "No strong moves yet"
    xc_pairs = (xc_payload or {}).get("pairs", [])
    xc_extreme = [p for p in xc_pairs if p.get("extreme")]
    newsletter = {
        "headline": (
            f"{len(anomalies)} anomalies and {len(trends)} strong trends across "
            f"{coverage['active_series']} live series"
        ),
        "bullets": [
            f"Lead signal: {lead}.",
            f"Calendar: {coverage['upcoming_macro']} upcoming releases and "
            f"{coverage['headlines']} headlines monitored.",
            f"Cross-market coverage: {coverage['defi_rows']} DeFi rows, "
            f"{coverage['midnight_rows']} Midnight maturities, "
            f"{coverage['morpho_rows']} Morpho markets, {coverage['refs_rows']} rate refs.",
        ]
        + ([
            "Correlation regime: "
            + "; ".join(
                f"{p['label']} {p['corr_60d']:+.2f} (60d)"
                for p in xc_extreme[:3]
            )
            + "."
        ] if xc_extreme else [])
        + ([f"MBS plumbing — {mbs_note['stress']}"] if mbs_note and mbs_note.get("stress") else [])
        + ([f"Vol — {vol_note['regime']}. Richest: {vol_note['richest'][0]} "
             f"{vol_note['richest'][1]:.1f} ({vol_note['richest'][2]:.0f}th %ile); "
             f"cheapest: {vol_note['cheapest'][0]} "
             f"{vol_note['cheapest'][1]:.1f} ({vol_note['cheapest'][2]:.0f}th %ile)."]
           if vol_note else [])
        + [
            f"Anomaly — {row['name']}: {row['summary']}."
            for row in anomalies[:2]
        ] + [
            f"Trend — {row['name']}: {row['summary']}."
            for row in trends[:2]
        ],
        "coverage": coverage,
    }
    digest_id = hashlib.sha256(json.dumps(
        {
            "alerts": [{k: row[k] for k in ("series_id", "direction", "summary")} for row in anomalies[:8]],
            "trends": [{k: row[k] for k in ("series_id", "direction", "summary")} for row in trends[:8]],
            "newsletter": newsletter,
            # the verdict is hashed so the newsletter fires when the risk
            # engine's read of the market changes, not just on new anomalies
            "risk_verdict": (risk_payload or {}).get("verdict"),
            # country risk map buckets: the daily Drive snapshot pulls this
            # digest, so the map data lands in Harry's Drive with it
            "country_risk": [(c["code"], c["bucket"]) for c in (cr_payload or {}).get("countries", [])],
            # extreme cross-asset correlations: the newsletter fires when the
            # correlation regime flips, not just on new anomalies
            "xcorr_extreme": sorted(
                f"{p['id']}:{p['corr_60d']:+.2f}" for p in xc_extreme
            ),
            # MBS stress read-out (real GSE data only)
            "mbs_stress": (mbs_note or {}).get("stress"),
            # vol regime + single-stock extremes (hashed so the newsletter
            # fires when the vol regime or the mover board changes)
            "vol_regime": (vol_note or {}).get("regime"),
            "movers_asof": (movers_note or {}).get("asof"),
        },
        sort_keys=True,
    ).encode("utf-8")).hexdigest()[:16]
    return {
        "digest_id": digest_id,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "alerts": anomalies[:8],
        "trends": trends[:8],
        "newsletter": newsletter,
        "risk": risk_payload,
        "country_risk": cr_payload,
        "xcorr": xc_payload,
        "mbs": mbs_note,
        "vol": vol_note,
        "movers": movers_note,
    }


async def refresh_digest(store: Store, cfg: Config) -> str:
    store.put_doc("insights", build_digest(store, cfg), source="local-analysis")
    return "local-analysis"
