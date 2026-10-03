"""Read-only JSON API. App factory so tests inject their own store/config."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from collector.changes import apply_transform, to_bands
from collector.chat import SYSTEM_PROMPT, ask_gemini, build_context
from collector.config import Config
from collector.panels import build_dashboard
from collector.store import Store

RANGE_DAYS = {"1y": 365, "5y": 5 * 365, "10y": 10 * 365}

CHAT_MODEL_DEFAULT = "gemini-flash-latest"


def _fetcher_healthy(f: dict) -> bool:
    if not f["last_error_at"]:
        return True
    if not f["last_success"]:
        return False
    return f["last_success"] >= f["last_error_at"]


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


def create_app(store: Store, cfg: Config) -> FastAPI:
    app = FastAPI(title="os-bloom collector", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"])
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

    @app.post("/api/chat")
    def chat_endpoint(req: ChatRequest) -> dict:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="chat unavailable: GEMINI_API_KEY not set",
            )
        model = os.environ.get("CHAT_MODEL", CHAT_MODEL_DEFAULT)
        history = [
            {"role": h["role"], "content": h["content"]}
            for h in req.history[-20:]  # last 10 turns
            if h.get("role") in ("user", "assistant") and h.get("content")
        ]
        context, series_ids = build_context(store, cfg, req.message)
        messages = [
            *history,
            {"role": "user", "content": f"{context}\n\nQUESTION: {req.message}"},
        ]
        try:
            reply = ask_gemini(api_key, model, SYSTEM_PROMPT, messages)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"chat provider error: {exc}")
        return {"reply": reply, "series_used": series_ids}

    @app.get("/healthz")
    def healthz() -> dict:
        fetchers = store.statuses()
        return {"ok": all(_fetcher_healthy(f) for f in fetchers), "fetchers": fetchers}

    @app.get("/api/thirteenf")
    def thirteenf() -> dict:
        """13F change-detection payloads per watchlist filer.

        Each entry is the doc stored at thirteenf:<cik10>:changes by the
        thirteenf job: net_flow_usd, per-kind counts (n_new/n_closed/
        n_increased/n_decreased) and the top changes by |delta|. Filers with
        no diff yet (first filing seen) are omitted.
        """
        filers = []
        for w in cfg.thirteenf.watchlist:
            doc = store.doc(f"thirteenf:{w.cik.zfill(10)}:changes")
            if doc is not None:
                filers.append(doc.payload)
        return {"filers": filers}

    @app.get("/api/scorecard")
    def scorecard() -> dict:
        """Briefing-style scorecard: 1D/1M/3M/1Y moves + 1Y z-score per row.

        Rows come from the `scorecard:` config section (series id + kind).
        """
        from collector.scorecard import scorecard_row

        rows = []
        for r in cfg.scorecard:
            ccfg = cycle_by_id.get(r.series)
            if ccfg is None:
                continue
            points = store.points(f"cycle:{r.series}")
            row = scorecard_row(points, r.kind)
            row.update({"id": r.series, "name": ccfg.name,
                        "unit": ccfg.unit, "kind": r.kind})
            rows.append(row)
        return {"rows": rows}

    @app.get("/api/figi/lookup")
    def figi_lookup(idtype: str = "TICKER", idvalue: str = "") -> dict:
        """Interactive FIGI lookup: proxies OpenFIGI v3 mapping (batch 11).

        On-demand (no scheduler job). Needs OPENFIGI_API_KEY in env; without
        it returns a clean JSON error (ok: false) — never a 500 — so the UI
        can show the "set the key on Render" hint. idtype: TICKER | CUSIP |
        ISIN | SEDOL | FIGI.
        """
        from collector.fetchers.openfigi import parse_mappings

        api_key = os.environ.get("OPENFIGI_API_KEY", "").strip()
        if not api_key:
            return {"ok": False, "error": "no_key",
                    "message": "Set OPENFIGI_API_KEY on Render (free key: openfigi.com)."}
        idtype = (idtype or "").strip().upper()
        id_map = {"TICKER": "ID_TICKER", "CUSIP": "ID_CUSIP",
                  "ISIN": "ID_ISIN", "SEDOL": "ID_SEDOL", "FIGI": "ID_BB_GLOBAL"}
        if idtype not in id_map:
            return {"ok": False, "error": "bad_idtype",
                    "message": f"idtype must be one of {', '.join(sorted(id_map))}."}
        idvalue = (idvalue or "").strip()
        if not idvalue or len(idvalue) > 64:
            return {"ok": False, "error": "bad_idvalue",
                    "message": "Enter an identifier (max 64 chars)."}
        import httpx
        try:
            resp = httpx.post(
                "https://api.openfigi.com/v3/mapping",
                json=[{"idType": id_map[idtype], "idValue": idvalue}],
                headers={"X-OPENFIGI-APIKEY": api_key,
                         "Content-Type": "application/json"},
                timeout=20)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": "upstream",
                    "message": f"OpenFIGI request failed: {str(exc)[:160]}"}
        if resp.status_code != 200:
            return {"ok": False, "error": "upstream",
                    "message": f"OpenFIGI HTTP {resp.status_code}."}
        try:
            body = resp.json()
            results = body if isinstance(body, list) else []
        except ValueError:
            return {"ok": False, "error": "upstream",
                    "message": "OpenFIGI returned non-JSON."}
        mapped = parse_mappings(results)
        rows = []
        for ticker, m in mapped.items():
            rows.append({
                "name": m.get("name"), "ticker": ticker,
                "figi": m.get("figi"), "composite_figi": m.get("compositeFIGI"),
                "security_type": m.get("securityType"),
                "exchange_code": m.get("exchCode"),
                "market_sector": m.get("marketSector"),
                "share_class_figi": m.get("shareClassFIGI"),
            })
        return {"ok": True, "idtype": idtype, "idvalue": idvalue,
                "count": len(rows), "results": rows}

    return app
