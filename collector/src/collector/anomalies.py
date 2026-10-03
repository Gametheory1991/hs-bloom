"""Shared, latest-reading detection for dashboard, digest and delivery."""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev

from collector.changes import apply_transform
from collector.config import Config, validate_alerting_config
from collector.store import Store


@dataclass(frozen=True)
class DigestSeries:
    series_id: str
    store_id: str
    name: str
    unit: str
    transform: str = "none"


def series_catalog(cfg: Config) -> list[DigestSeries]:
    items = [
        *[DigestSeries(s.id, f"macro:{s.id}", s.name, s.unit, s.transform) for s in cfg.series],
        *[DigestSeries(s.id, f"cycle:{s.id}", s.name, s.unit, s.transform) for s in cfg.cycle_series],
        *[DigestSeries(i.symbol, f"idx:{i.symbol}", i.name, "px") for i in cfg.indexes],
        *[DigestSeries(f"{b.country}{b.tenor}", f"yield:{b.country}{b.tenor}",
                      f"{b.country} {b.tenor} yield", "%") for b in cfg.bonds],
        *[DigestSeries(f"{c.country}CB", f"cb:{c.country}", c.label, "%") for c in cfg.cb_rates],
        *[DigestSeries(a.supply_id, f"ref:{a.supply_id}", a.supply_label, "%") for a in cfg.refs.aave],
        *[DigestSeries(a.borrow_id, f"ref:{a.borrow_id}", a.borrow_label, "%") for a in cfg.refs.aave],
        *[DigestSeries(p.implied_id, f"ref:{p.implied_id}", p.implied_label, "%") for p in cfg.refs.pendle],
        *[DigestSeries(p.underlying_id, f"ref:{p.underlying_id}", p.underlying_label, "%") for p in cfg.refs.pendle],
        *[DigestSeries(f.id, f"ref:{f.id}", f.label, "%") for f in cfg.refs.funding],
        *[DigestSeries(c.series, f"ref:{c.series}", c.series, "%") for c in cfg.refs.llama_chart],
    ]
    deduped = {}
    for item in items:
        deduped.setdefault(item.store_id, item)
    return list(deduped.values())


def effective_config(store: Store, cfg: Config) -> dict:
    saved = store.doc("alerting_config")
    ids = {s.series_id for s in series_catalog(cfg)}
    if saved is not None:
        # Retired series must not prevent an otherwise valid deployment from starting.
        raw = saved.payload
        if isinstance(raw, dict) and isinstance(raw.get("series"), dict):
            raw = {**raw, "series": {k: v for k, v in raw["series"].items() if k in ids}}
        try:
            return validate_alerting_config(raw, ids)
        except (ValueError, TypeError):
            pass
    return cfg.alerting_config


def thresholds(config: dict, series_id: str) -> dict:
    return {**config["defaults"], **config["series"].get(series_id, {})}


def ordered_points(store: Store, item: DigestSeries) -> list:
    raw = {d: v for d, v in store.points(item.store_id).items()
           if math.isfinite(v)}
    return sorted((d, v) for d, v in apply_transform(raw, item.transform).items()
                  if math.isfinite(v))


def detect_series(ordered: list, item: DigestSeries, settings: dict) -> list[dict]:
    if not settings["enabled"] or not ordered:
        return []
    latest_date, value = ordered[-1]
    baseline = [v for _, v in ordered[:-1][-settings["window"]:]]
    enough = len(baseline) >= settings["min_history"]
    sigma = max(pstdev(baseline), settings["min_std"]) if enough else None
    average = mean(baseline) if enough else None
    z = (value - average) / sigma if enough else None
    events = []

    def add(kind: str, direction: str, summary: str, **details) -> None:
        events.append({
            "id": f"{kind}:{item.series_id}", "kind": kind,
            "series_id": item.series_id, "store_id": item.store_id,
            "name": item.name, "unit": item.unit, "value": round(value, 2),
            "direction": direction, "z_score": round(z, 2) if z is not None else 0.0,
            "summary": summary, "as_of": latest_date.isoformat(), **details,
        })

    if enough and abs(z) >= settings["z_threshold"]:
        add("anomaly", "up" if z > 0 else "down",
            f"{value:.2f}{'%' if item.unit == '%' else ''} is {abs(z):.2f}σ "
            f"{'above' if z > 0 else 'below'} trend",
            baseline_mean=average, baseline_std=sigma, baseline_count=len(baseline))

    if settings["range_enabled"]:
        lo, hi = settings["range_min"], settings["range_max"]
        historical = lo is None and hi is None
        if historical and enough:
            margin = settings["range_margin"] * sigma
            lo, hi = min(baseline) - margin, max(baseline) + margin
        if lo is not None and value < lo:
            add("range", "down", f"{value:.2f} is below {'historical' if historical else 'configured'} range ({lo:.2f})",
                range_min=lo, range_max=hi, range_source="historical" if historical else "configured")
        elif hi is not None and value > hi:
            add("range", "up", f"{value:.2f} is above {'historical' if historical else 'configured'} range ({hi:.2f})",
                range_min=lo, range_max=hi, range_source="historical" if historical else "configured")

    width = settings["reversal_window"]
    if settings["reversal_enabled"] and enough and len(ordered) >= 2 * width + 1:
        values = [v for _, v in ordered]
        prior = (values[-width - 1] - values[-2 * width - 1]) / width
        recent = (values[-1] - values[-width - 1]) / width
        # Compare two disjoint windows; require both legs to be substantial
        # and the newest step to agree, rather than a noisy single-day wiggle.
        score = min(abs(prior), abs(recent)) * width / sigma
        if prior * recent < 0 and recent * (values[-1] - values[-2]) > 0 and score >= settings["reversal_z"]:
            add("reversal", "up" if recent > 0 else "down",
                f"Trend reversed {'up' if recent > 0 else 'down'} ({score:.2f}σ legs)",
                previous_slope=prior, recent_slope=recent, reversal_score=round(score, 2))
    return events


def detect_anomalies(store: Store, cfg: Config, config: dict | None = None) -> list[dict]:
    config = config or effective_config(store, cfg)
    result = []
    for item in series_catalog(cfg):
        result.extend(detect_series(ordered_points(store, item), item, thresholds(config, item.series_id)))
    return sorted(result, key=lambda row: abs(row["z_score"]), reverse=True)
