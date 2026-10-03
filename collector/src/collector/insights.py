"""Automated market-digest generation from stored series and panel docs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
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


def build_digest(store: Store, cfg: Config, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    risk_doc = store.doc("risk_summary")
    risk_payload = risk_doc.payload if risk_doc else None
    cr_doc = store.doc("country_risk")
    cr_payload = cr_doc.payload if cr_doc else None
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
        ] + [
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
    }


async def refresh_digest(store: Store, cfg: Config) -> str:
    store.put_doc("insights", build_digest(store, cfg), source="local-analysis")
    return "local-analysis"
