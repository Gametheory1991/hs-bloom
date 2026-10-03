"""JSON API with a token-protected alert threshold update endpoint."""
from __future__ import annotations

import hmac
import os
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from collector.changes import apply_transform, to_bands
from collector.anomalies import effective_config, series_catalog
from collector.config import Config, validate_alerting_config
from collector.insights import build_digest
from collector.panels import build_dashboard
from collector.store import Store

RANGE_DAYS = {"1y": 365, "5y": 5 * 365, "10y": 10 * 365}


def _fetcher_healthy(f: dict) -> bool:
    if not f["last_error_at"]:
        return True
    if not f["last_success"]:
        return False
    return f["last_success"] >= f["last_error_at"]


def create_app(store: Store, cfg: Config) -> FastAPI:
    app = FastAPI(title="os-bloom collector", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"])
    alert_series_ids = {item.series_id for item in series_catalog(cfg)}
    series_by_id = {s.id: s for s in cfg.series}
    cycle_by_id = {s.id: s for s in cfg.cycle_series}
    index_names = {i.symbol: i.name for i in cfg.indexes}
    bond_names = {f"{b.country}{b.tenor}": f"{b.country} {b.tenor} yield" for b in cfg.bonds}
    cb_names = {f"{c.country}CB": c.label for c in cfg.cb_rates}
    ref_labels = {}
    for a in cfg.refs.aave:
        ref_labels[a.supply_id] = a.supply_label
        ref_labels[a.borrow_id] = a.borrow_label
    for p in cfg.refs.pendle:
        ref_labels[p.implied_id] = p.implied_label
        ref_labels[p.underlying_id] = p.underlying_label
    for f in cfg.refs.funding:
        ref_labels[f.id] = f.label
    for chart in cfg.refs.llama_chart:
        ref_labels.setdefault(chart.series, chart.series)

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        return build_dashboard(store, cfg.indexes, now=datetime.now(timezone.utc),
                               cycle_series=cfg.cycle_series, cycle_tabs=cfg.cycle_tabs)

    @app.get("/api/series/{series_id}")
    def series(series_id: str, range: Literal["1y", "5y", "10y", "max"] = "10y") -> dict:
        scfg = series_by_id.get(series_id)
        if scfg is not None:
            points = apply_transform(store.points(f"macro:{series_id}"), scfg.transform)
            name, unit = scfg.name, scfg.unit
        elif series_id in cycle_by_id:
            ccfg = cycle_by_id[series_id]
            points = apply_transform(store.points(f"cycle:{series_id}"), ccfg.transform)
            name, unit = ccfg.name, ccfg.unit
        elif series_id in ref_labels:
            points = store.points(f"ref:{series_id}")  # already daily percent, no transform
            name, unit = ref_labels[series_id], "%"
        elif series_id in index_names:
            points = store.points(f"idx:{series_id}")
            name, unit = index_names[series_id], "px"
        elif series_id in bond_names:
            points = store.points(f"yield:{series_id}")
            name, unit = bond_names[series_id], "%"
        elif series_id in cb_names:
            points = store.points(f"cb:{series_id[:-2]}")  # USCB -> cb:US
            name, unit = cb_names[series_id], "%"
        else:
            raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
        if range != "max":
            cutoff = (datetime.now(timezone.utc) - timedelta(days=RANGE_DAYS[range])).date()
            points = {d: v for d, v in points.items() if d >= cutoff}
        return {
            "id": series_id, "name": name, "unit": unit,
            "points": [[d.isoformat(), v] for d, v in sorted(points.items())],
        }

    @app.get("/api/recessions")
    def recessions() -> dict:
        bands = to_bands(store.points("cycle:usrec"))
        return {"bands": [[a.isoformat(), b.isoformat()] for a, b in bands]}

    @app.get("/api/insights")
    def insights() -> dict:
        return build_dashboard(
            store,
            cfg.indexes,
            now=datetime.now(timezone.utc),
            cycle_series=cfg.cycle_series,
            cycle_tabs=cfg.cycle_tabs,
        )["panels"]["insights"]

    @app.get("/api/anomalies")
    def anomalies(
        series_id: str | None = Query(default=None, max_length=120),
        kind: Literal["anomaly", "range", "reversal"] | None = None,
        direction: Literal["up", "down"] | None = None,
        since: date | None = None,
        until: date | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10000),
    ) -> dict:
        if series_id is not None and series_id not in alert_series_ids:
            raise HTTPException(status_code=404, detail="unknown series")
        if since is not None and until is not None and since > until:
            raise HTTPException(status_code=422, detail="since must not exceed until")
        return store.alerts(series_id=series_id, kind=kind, direction=direction,
                            since=since.isoformat() if since else None,
                            until=until.isoformat() if until else None, limit=limit, offset=offset)

    @app.get("/api/alerts/config")
    def alert_config() -> dict:
        return effective_config(store, cfg)

    @app.post("/api/alerts/config")
    def update_alert_config(payload: dict = Body(...),
                            authorization: str | None = Header(default=None)) -> dict:
        token = os.environ.get("ALERT_CONFIG_TOKEN", "")
        if not token:
            raise HTTPException(status_code=503, detail="alert configuration updates are disabled")
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(status_code=401, detail="invalid bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        with store.alert_lock:
            try:
                config = validate_alerting_config(payload, alert_series_ids, effective_config(store, cfg))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            store.put_doc("alerting_config", config, source="api")
            store.cancel_disabled_alert_deliveries(config)
            store.put_doc("insights", build_digest(store, cfg), source="local-analysis")
        return config

    @app.get("/api/digest")
    def digest() -> dict:
        now = datetime.now(timezone.utc)
        result = build_digest(store, cfg, now=now)
        daily = store.alerts(detected_since=now.replace(hour=0, minute=0, second=0, microsecond=0)
                             .isoformat().replace("+00:00", "Z"), limit=200)
        result["daily_summary"] = {
            "date": now.date().isoformat(), "timezone": "UTC",
            "event_count": daily["total"], "events": daily["items"],
        }
        return result

    @app.get("/healthz")
    def healthz() -> dict:
        fetchers = store.statuses()
        return {"ok": all(_fetcher_healthy(f) for f in fetchers), "fetchers": fetchers}

    return app
