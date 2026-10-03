"""Automated market-digest generation from stored series and panel docs."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from statistics import pstdev

from collector.anomalies import (
    detect_anomalies, effective_config, ordered_points,
    series_catalog as _series_catalog, thresholds,
)
from collector.changes import ref_close
from collector.config import Config
from collector.store import Store


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


def build_digest(store: Store, cfg: Config, now: datetime | None = None, *,
                 config: dict | None = None, anomalies: list[dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    config = config if config is not None else effective_config(store, cfg)
    anomalies = anomalies if anomalies is not None else detect_anomalies(store, cfg, config)
    trends = []
    active_series = 0
    for item in _series_catalog(cfg):
        ordered = ordered_points(store, item)
        if len(ordered) < 2:
            continue
        points = dict(ordered)
        active_series += 1
        latest_date, latest_value = ordered[-1]
        settings = thresholds(config, item.series_id)
        baseline = [v for _, v in ordered[:-1][-settings["window"]:]]
        if settings["enabled"] and len(baseline) >= settings["min_history"]:
            sigma = max(pstdev(baseline), settings["min_std"])
            if sigma > 0:
                ref_1m = ref_close(points, latest_date, "1m")
                if ref_1m is not None:
                    delta = round(latest_value - ref_1m, 2)
                    trend_score = round(abs(delta) / sigma, 2)
                    if delta != 0 and trend_score >= settings["trend_z"]:
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
        },
        sort_keys=True,
    ).encode("utf-8")).hexdigest()[:16]
    return {
        "digest_id": digest_id,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "alerts": anomalies[:8],
        "trends": trends[:8],
        "newsletter": newsletter,
    }


async def refresh_digest(store: Store, cfg: Config, smtp_cfg=None) -> str:
    from collector.alerts import process_alerts

    await process_alerts(store, [], smtp_cfg=smtp_cfg, cfg=cfg)
    return "local-analysis"
