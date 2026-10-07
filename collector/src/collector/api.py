"""Read-only JSON API. App factory so tests inject their own store/config."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from collector.changes import apply_transform, to_bands
from collector.chat import SYSTEM_PROMPT, ask_gemini, build_context
from collector.config import Config
from collector import debt_cube
from collector.panels import build_dashboard, econ_calendar_payload
from collector.store import Store
from collector.usage import client_ip, log_visit, record_event, usage_stats

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


class AlertConfigUpdate(BaseModel):
    id: str
    threshold_mult: float | None = None
    muted: bool | None = None


class BriefcheckReport(BaseModel):
    checked_at: str
    checked_count: int = 0
    mismatches: list[dict] = []


def create_app(store: Store, cfg: Config) -> FastAPI:
    app = FastAPI(title="os-bloom collector", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT"])
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
        elif series_id.startswith("auction:"):
            # UST auction bucket history: auction:{bucket}:{metric} (bid_to_cover,
            # high_yield, indirect_pct, direct_pct, dealer_pct, offering).
            points = store.points(series_id)
            _m = series_id.rsplit(":", 1)[-1]
            _mu = {"high_yield": "%", "bid_to_cover": "ratio",
                   "indirect_pct": "%", "direct_pct": "%", "dealer_pct": "%",
                   "offering": "$"}.get(_m, "")
            name, unit = f"{series_id[len('auction:'):]}".replace(":", " "), _mu
        elif series_id.startswith("opt:"):
            # Options aggregates: opt:{SYM}:{metric} — gex ($B), pc_oi,
            # pc_vol (ratios), atm_iv (%), maxpain ($). Written daily by the
            # cboe_options job as cycle:opt-{SYM}-{metric}.
            _om = series_id.split(":", 2)
            key = f"cycle:opt-{_om[1]}-{_om[2]}" if len(_om) == 3 else None
            if not key:
                raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
            points = store.points(key)
            _mu = {"gex": "$B", "pc_oi": "ratio", "pc_vol": "ratio",
                   "atm_iv": "%", "maxpain": "$"}.get(_om[2], "")
            name, unit = f"{_om[1]} {_om[2]}".replace("_", " "), _mu
        elif series_id.startswith("etf:"):
            # ETF snapshots: etf:{TICKER}:{metric} — aum ($), nav ($),
            # shares (sh), price ($), expense (%), divyield (%).
            # Written daily by the ishares_etf job.
            _em = series_id.split(":", 2)
            key = f"cycle:etf-{_em[1]}-{_em[2]}" if len(_em) == 3 else None
            if not key:
                raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
            points = store.points(key)
            _mu = {"aum": "$", "nav": "$", "shares": "sh",
                   "flow7d": "$", "price": "$", "expense": "%",
                   "divyield": "%"}.get(_em[2], "")
            name, unit = f"{_em[1]} {_em[2]}", _mu
        elif series_id.startswith("ats-"):
            # FINRA ATS Transparency (dynamic venue/symbol series; written by
            # the finra_ats job). Weekly: ats-total-shares, ats-{mpid}-shares,
            # ats-sym-{TICKER}-shares; monthly keyless: ats-m-*. Resolved
            # dynamically like regsho-top — not in cycle_by_id by design.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]{0,31}", series_id):
                raise HTTPException(status_code=404,
                                    detail=f"unknown series: {series_id}")
            points = store.points(f"cycle:{series_id}")
            _kind = series_id.rsplit("-", 1)[-1]
            _mu = {"shares": "sh", "trades": "trades",
                   "sharepct": "%"}.get(_kind, "")
            name = series_id[4:].replace("-", " ").upper()
            unit = _mu
        elif series_id.startswith("regsho-top-") and (
                series_id.endswith("-shortvol") or series_id.endswith("-totalvol")):
            # Dynamic per-ticker Reg SHO history (backfilled 2Y, 518 days).
            # Tickers rotate, so these are resolved dynamically instead of
            # via config. Not in cycle_by_id by design.
            points = store.points(f"cycle:{series_id}")
            sym = series_id[len("regsho-top-"):].rsplit("-", 1)[0]
            kind = "short volume" if series_id.endswith("-shortvol") else "total volume"
            name, unit = f"{sym} {kind}", "sh"
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

    @app.get("/api/auctions")
    def auctions() -> dict:
        """Treasury auction results + upcoming schedule.

        ``recent``: latest 40 completed auctions (high yield, bid-to-cover,
        takedown splits). ``buckets``: latest completed result per benchmark
        bucket. ``upcoming``: announced but not yet held, by auction date.
        Source: Fiscal Data Treasury API (keyless), refreshed daily.
        """
        upc = store.doc("upcoming_auctions")
        res = store.doc("auction_results")
        recent = (res.payload.get("results", []) if res is not None else [])[:40]
        buckets: dict[str, dict] = {}
        for r in sorted((res.payload.get("results", []) if res is not None else []),
                        key=lambda x: x.get("auction_date", ""), reverse=True):
            b = r.get("bucket")
            if b and b not in buckets and r.get("completed"):
                buckets[b] = {
                    "auction_date": r.get("auction_date"),
                    "high_yield": r.get("high_yield"),
                    "bid_to_cover": r.get("bid_to_cover"),
                    "indirect_pct": r.get("indirect_pct"),
                    "direct_pct": r.get("direct_pct"),
                    "dealer_pct": r.get("dealer_pct"),
                    "offering_amt": r.get("offering_amt"),
                }
        docs = [d for d in (upc, res) if d is not None]
        return {
            "upcoming": (upc.payload.get("auctions", []) if upc is not None else []),
            "recent": recent,
            "buckets": buckets,
            "updated_at": max((d.updated_at for d in docs), default=None),
            "source": "fiscaldata.treasury.gov",
        }

    @app.get("/api/econ-calendar")
    def econ_calendar() -> dict:
        """Econ calendar: upcoming releases + past 7 days with actuals.

        Same payload as the dashboard macro panel. Source: ForexFactory
        weekly calendar feed (USD/EUR, high impact), refreshed on a polite
        cadence (6h baseline, hourly on release days).
        """
        return econ_calendar_payload(store, datetime.now(timezone.utc))

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

    @app.get("/api/debt-cube")
    def debt_cube_api(product: str | None = None, maturity: str | None = None,
                      holder: str | None = None) -> dict:
        """Debt-outstanding slice cube: product x maturity x holder cells from
        MSPD Table III (outstanding) + NY Fed SOMA (CUSIP par), denominator =
        MSPD Table 1 Total Public Debt Outstanding. All params optional;
        returns an empty-cells payload (never a 500) when the cube doc is
        missing — the debt_cube scheduler job writes it daily."""
        doc = store.doc("debt_cube")
        if doc is None or not doc.payload:
            return {"asof": None, "denominator": None, "cells": [],
                    "updated_at": None}
        payload = doc.payload
        cells = debt_cube.query_cube(
            payload,
            product=(product or None) or None,
            maturity=(maturity or None) or None,
            holder=(holder or None) or None,
        )
        denom_mn = payload.get("denominator_mn") or 0.0
        return {
            "asof": payload.get("asof"),
            "denominator": {
                "notional_bn": round(denom_mn / 1000.0, 1),
                "desc": payload.get("denominator_desc"),
            },
            "cells": [
                {"product": c["product"], "maturity": c["maturity"],
                 "holder": c["holder"], "notional_bn": c["notional_bn"],
                 "pct_of_total": c["pct_of_total"]}
                for c in cells
            ],
            "updated_at": doc.updated_at,
        }

    @app.get("/api/equity/short-interest/settlements")
    def si_settlements() -> dict:
        """FINRA short-interest settlement coverage: every settlement date
        stored in the full-universe table (2020-01-15 onward)."""
        scope = store.short_interest_scope()
        return {
            "settlements": [{"date": d.isoformat(), "tickers": n}
                            for d, n in store.short_interest_settlements()],
            **scope,
        }

    @app.get("/api/equity/short-interest")
    def si_table(settlement: str | None = None, search: str = "",
                 sort: str = "short", direction: str = "desc",
                 page: int = 1, per_page: int = 50) -> dict:
        """Paginated full-universe short-interest ticker table for one
        settlement. per_page caps at 25,000 (explicit full-settlement CSV/XLSX
        export)."""
        scope = store.short_interest_scope()
        if settlement is None:
            settlements = store.short_interest_settlements()
            if not settlements:
                return {"settlement": None, "total": 0, "page": 1,
                        "per_page": per_page, "rows": [], "scope": scope}
            settlement = settlements[0][0].isoformat()
        try:
            from datetime import date as _d
            sdate = _d.fromisoformat(settlement)
        except ValueError:
            return {"error": "bad settlement date", "settlement": settlement,
                    "total": 0, "page": 1, "per_page": per_page, "rows": [],
                    "scope": scope}
        total, rows = store.short_interest_rows(sdate, search or None, sort,
                                                direction, page, per_page)
        return {"settlement": sdate.isoformat(), "total": total,
                "page": max(1, page), "per_page": min(max(1, per_page), 25000),
                "rows": rows, "scope": scope}

    @app.get("/api/equity/regsho-top/scope")
    def regsho_top_scope() -> dict:
        """Top-500 daily shorted-ticker table coverage (2020-01-02 onward)."""
        return store.regsho_top_scope()

    @app.get("/api/equity/regsho-top")
    def regsho_top(day: str | None = None, search: str = "",
                   page: int = 1, per_page: int = 50) -> dict:
        """Paginated top-500 tickers by daily short volume for one trading
        day (CNMS-consolidated). per_page caps at 25,000 for export."""
        scope = store.regsho_top_scope()
        if day is None:
            day = scope.get("last")
            if not day:
                return {"day": None, "total": 0, "page": 1,
                        "per_page": per_page, "rows": [], "scope": scope}
        try:
            from datetime import date as _d
            d = _d.fromisoformat(day)
        except ValueError:
            return {"error": "bad day", "day": day, "total": 0, "page": 1,
                    "per_page": per_page, "rows": [], "scope": scope}
        total, rows = store.regsho_top_rows(d, search or None, page, per_page)
        return {"day": d.isoformat(), "total": total, "page": max(1, page),
                "per_page": min(max(1, per_page), 25000), "rows": rows,
                "scope": scope}

    @app.get("/api/equity/threshold-history/dates")
    def threshold_hist_dates() -> dict:
        """Weekly threshold-list snapshot dates stored (2020-01-01 onward)."""
        dates = [d.isoformat() for d in store.threshold_hist_dates()]
        return {"dates": dates, "count": len(dates)}

    @app.get("/api/equity/threshold-history")
    def threshold_history(day: str | None = None, symbol: str | None = None,
                          search: str = "", page: int = 1,
                          per_page: int = 200) -> dict:
        """Threshold-list securities for one snapshot date, or the full
        date history for one symbol (symbol lookup)."""
        if symbol:
            return {"symbol": symbol.upper(),
                    "history": store.threshold_hist_symbol(symbol)}
        dates = store.threshold_hist_dates()
        if day is None:
            day = dates[0].isoformat() if dates else None
            if not day:
                return {"day": None, "total": 0, "page": 1,
                        "per_page": per_page, "rows": []}
        try:
            from datetime import date as _d
            d = _d.fromisoformat(day)
        except ValueError:
            return {"error": "bad day", "day": day, "total": 0, "page": 1,
                    "per_page": per_page, "rows": []}
        total, rows = store.threshold_hist_rows(d, search or None, page,
                                                per_page)
        return {"day": d.isoformat(), "total": total, "page": max(1, page),
                "per_page": min(max(1, per_page), 5000), "rows": rows,
                "snapshots": len(dates)}

    @app.get("/api/figi/lookup")
    def figi_lookup(idtype: str = "TICKER", idvalue: str = "") -> dict:
        """Interactive FIGI lookup: proxies OpenFIGI v3 mapping (batch 11).

        On-demand (no scheduler job). Needs OPENFIGI_API_KEY in env; without
        it returns a clean JSON error (ok: false) — never a 500 — so the UI
        can show the "set the key on Render" hint. idtype: TICKER | CUSIP |
        ISIN | SEDOL | FIGI.
        """

        api_key = os.environ.get("OPENFIGI_API_KEY", "").strip()
        if not api_key:
            return {"ok": False, "error": "no_key",
                    "message": "Set OPENFIGI_API_KEY on Render (free key: openfigi.com)."}
        idtype = (idtype or "").strip().upper()
        # idType values verified live 2026-10-03: TICKER must be "TICKER"
        # (ID_TICKER returns nothing); CUSIP/ISIN/SEDOL need the ID_ prefix.
        id_map = {"TICKER": "TICKER", "CUSIP": "ID_CUSIP",
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
            job = {"idType": id_map[idtype], "idValue": idvalue}
            if idtype == "TICKER":
                job["exchCode"] = "US"  # bare tickers are ambiguous w/o exchange
            resp = httpx.post(
                "https://api.openfigi.com/v3/mapping",
                json=[job],
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
        # Flatten all candidate listings; US listings first, cap at 8.
        # (parse_mappings collapses to one row per ticker — fine for the
        # batch job, but the interactive lookup should show the candidates.)
        cands = []
        for job_res in results:
            if isinstance(job_res, dict):
                cands.extend(job_res.get("data") or [])
        cands.sort(key=lambda m: 0 if m.get("exchCode") == "US" else 1)
        rows = [{
            "name": m.get("name"), "ticker": m.get("ticker"),
            "figi": m.get("figi"), "composite_figi": m.get("compositeFIGI"),
            "security_type": m.get("securityType"),
            "exchange_code": m.get("exchCode"),
            "market_sector": m.get("marketSector"),
            "share_class_figi": m.get("shareClassFIGI"),
        } for m in cands[:8]]
        return {"ok": True, "idtype": idtype, "idvalue": idvalue,
                "count": len(rows), "results": rows}

    @app.get("/api/alerts/config")
    def alerts_config() -> dict:
        """Per-alert-type tuning: [{id, label, threshold_mult, muted}]."""
        from collector.alert_config import get_config

        return {"types": list(get_config(store).values())}

    @app.put("/api/alerts/config")
    def update_alerts_config(req: AlertConfigUpdate) -> dict:
        """Update one alert type's threshold_mult and/or muted flag."""
        from collector.alert_config import set_config

        try:
            entry = set_config(store, req.id,
                               threshold_mult=req.threshold_mult, muted=req.muted)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"type": entry}

    @app.post("/api/briefcheck")
    def briefcheck_store(report: BriefcheckReport) -> dict:
        """Accept a briefcheck.py JSON report; stored as doc briefcheck_latest."""
        store.put_doc("briefcheck_latest", report.model_dump(), "briefcheck")
        return {"stored": True}

    @app.get("/api/briefcheck")
    def briefcheck_get() -> dict:
        """Latest briefcheck report, or 404 when none has been posted yet."""
        doc = store.doc("briefcheck_latest")
        if doc is None:
            raise HTTPException(status_code=404, detail="no briefcheck report yet")
        return doc.payload

    # ---- built-in usage analytics (no third-party service) ----------------
    @app.middleware("http")
    async def usage_log(request: Request, call_next):  # noqa: ANN001,ANN202
        resp = await call_next(request)
        try:
            log_visit(
                store,
                ts=datetime.now(timezone.utc),
                path=request.url.path,
                method=request.method,
                ip=client_ip(request.headers.get("x-forwarded-for"),
                             request.client.host if request.client else None),
                user_agent=request.headers.get("user-agent", ""),
                referrer=request.headers.get("referer", ""),
            )
        except Exception:  # noqa: BLE001 — analytics must never break responses
            pass
        return resp

    @app.post("/api/event")
    async def usage_event(request: Request) -> dict:
        """Client beacon: {type: pageview|hub_click|subtab_click,
        session, hub?, subtab?, detail?}. Fire-and-forget from the UI."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid JSON")
        ok = record_event(
            store,
            ts=datetime.now(timezone.utc),
            session=str(body.get("session") or "na"),
            type=str(body.get("type") or ""),
            hub=body.get("hub"),
            subtab=body.get("subtab"),
            detail=body.get("detail"),
        )
        if not ok:
            raise HTTPException(status_code=400, detail="invalid event type")
        return {"ok": True}

    @app.get("/api/usage")
    def usage(days: int = 30) -> dict:
        """Usage analytics: visitors, pageviews, bounce, sessions, top
        routes, hub clicks, referrers, device split over trailing `days`."""
        return usage_stats(store, days)

    return app
