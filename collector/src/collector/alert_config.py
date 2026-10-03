"""Per-alert-type tuning for Telegram alerts, persisted in the store.

Alert types are exactly the digest kinds notify.py emits:
  anomaly — |z| anomaly alerts (base threshold ANOMALY_Z in insights.py)
  trend   — 1-month-move trend alerts (base threshold TREND_Z)

threshold_mult scales the trigger threshold (1.0 = default; 2.0 = roughly
half as many alerts; 0.5 = roughly twice as many). muted suppresses
sending for that type entirely. Seeded from these defaults on first read;
only overrides are stored.
"""
from __future__ import annotations

from collector.store import Store

DOC_KEY = "alert_config"

DEFAULT_TYPES = (
    {"id": "anomaly", "label": "Anomaly alerts (|z| ≥ 2.2σ)",
     "threshold_mult": 1.0, "muted": False},
    {"id": "trend", "label": "Trend alerts (1m move ≥ 1.15σ)",
     "threshold_mult": 1.0, "muted": False},
)

_VALID_IDS = {t["id"] for t in DEFAULT_TYPES}
_MIN_MULT, _MAX_MULT = 0.1, 10.0


def _merged(store: Store) -> dict[str, dict]:
    """Defaults overlaid with stored overrides, keyed by type id."""
    merged = {t["id"]: dict(t) for t in DEFAULT_TYPES}
    doc = store.doc(DOC_KEY)
    if doc and isinstance(doc.payload, dict):
        for tid, over in doc.payload.get("types", {}).items():
            if tid in merged and isinstance(over, dict):
                merged[tid].update(
                    {k: over[k] for k in ("threshold_mult", "muted") if k in over}
                )
    return merged


def get_config(store: Store) -> dict[str, dict]:
    """{type_id: {id, label, threshold_mult, muted}} with sane values."""
    merged = _merged(store)
    for t in merged.values():
        try:
            m = float(t["threshold_mult"])
        except (TypeError, ValueError):
            m = 1.0
        t["threshold_mult"] = min(_MAX_MULT, max(_MIN_MULT, m))
        t["muted"] = bool(t["muted"])
    return merged


def set_config(
    store: Store,
    type_id: str,
    threshold_mult: float | None = None,
    muted: bool | None = None,
) -> dict:
    """Validate and persist one type's tuning. Returns the updated entry."""
    if type_id not in _VALID_IDS:
        raise ValueError(f"unknown alert type: {type_id!r} (known: {sorted(_VALID_IDS)})")
    if threshold_mult is not None:
        try:
            threshold_mult = float(threshold_mult)
        except (TypeError, ValueError):
            raise ValueError("threshold_mult must be a number")
        if not (_MIN_MULT <= threshold_mult <= _MAX_MULT):
            raise ValueError(f"threshold_mult must be between {_MIN_MULT} and {_MAX_MULT}")
    if muted is not None and not isinstance(muted, bool):
        raise ValueError("muted must be true or false")
    doc = store.doc(DOC_KEY)
    payload = doc.payload if doc and isinstance(doc.payload, dict) else {}
    types = payload.get("types")
    if not isinstance(types, dict):
        types = {}
        payload["types"] = types
    entry = types.get(type_id)
    if not isinstance(entry, dict):
        entry = {}
        types[type_id] = entry
    if threshold_mult is not None:
        entry["threshold_mult"] = threshold_mult
    if muted is not None:
        entry["muted"] = muted
    store.put_doc(DOC_KEY, payload, "alert-tuning")
    return get_config(store)[type_id]


def scaled_thresholds(store: Store, base_anomaly_z: float, base_trend_z: float) -> tuple[float, float]:
    """(anomaly_z, trend_z) with each type's threshold_mult applied."""
    cfg = get_config(store)
    return (
        base_anomaly_z * cfg["anomaly"]["threshold_mult"],
        base_trend_z * cfg["trend"]["threshold_mult"],
    )


def is_muted(store: Store, type_id: str) -> bool:
    return get_config(store).get(type_id, {}).get("muted", False)
