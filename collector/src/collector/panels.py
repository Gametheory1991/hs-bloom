"""Assemble the /api/dashboard payload (spec §5) from store contents."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from collector.changes import apply_transform, bp_move, pct_change, ref_close
from collector.config import CycleSeriesCfg, CycleTabCfg, IndexCfg
from collector.store import Store

log = logging.getLogger(__name__)

HORIZONS = ("1d", "1w", "ytd", "1y")


def _asof(ts_iso: str):
    # asof = quote ts; accepted: ~20min midnight-UTC window
    # can shift the 1d ref, self-corrects next tick
    return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).date()


def _equity_rows(store: Store, indexes: list[IndexCfg]) -> list[dict]:
    doc = store.doc("equity_quotes")
    if doc is None:
        return []
    rows = []
    for idx in indexes:  # config order == display order
        quote = doc.payload.get(idx.symbol)
        if quote is None:
            continue
        try:
            closes = store.points(f"idx:{idx.symbol}")
            asof = _asof(quote["ts"])
            row = {"symbol": idx.symbol, "name": idx.name, "last": quote["last"],
                   "source": quote["source"], "delayed": quote["delayed"],
                   "updated_at": doc.updated_at}
            for horizon in HORIZONS:
                row[f"chg_{horizon}"] = pct_change(quote["last"], ref_close(closes, asof, horizon))
        except (KeyError, TypeError, ValueError) as exc:
            # one malformed row must degrade that row, never 500 the dashboard
            log.warning("skipping malformed equity quote for %s: %s", idx.symbol, exc)
            continue
        rows.append(row)
    return rows


def _bond_rows(store: Store) -> list[dict]:
    """Matrix rows, one per country: CB rate + 3M + 10Y, changes on the 10Y.

    Country order = doc insertion order (= bonds config order). A malformed
    entry degrades to a null cell; a country with no usable cell is dropped.
    """
    doc = store.doc("bond_quotes")
    if doc is None:
        return []
    rows: dict[str, dict] = {}
    for key, quote in doc.payload.items():
        try:
            country = quote["country"]
            row = rows.setdefault(country, {
                "country": country, "cb_pct": None, "cb_label": None,
                "y3m_pct": None, "y10_pct": None, "chg_1d_bp": None, "chg_1w_bp": None,
                "updated_at": doc.updated_at,
            })
            if key.endswith("CB"):
                row["cb_pct"] = quote["yield_pct"]
                row["cb_label"] = quote.get("label")
            elif quote["tenor"] == "3M":
                row["y3m_pct"] = quote["yield_pct"]
            elif quote["tenor"] == "10Y":
                series = store.points(f"yield:{country}10Y")
                asof = _asof(quote["ts"])
                row["y10_pct"] = quote["yield_pct"]
                row["chg_1d_bp"] = bp_move(quote["yield_pct"], ref_close(series, asof, "1d"))
                row["chg_1w_bp"] = bp_move(quote["yield_pct"], ref_close(series, asof, "1w"))
        except (KeyError, TypeError, ValueError) as exc:
            # one malformed entry must degrade its cell, never 500 the dashboard
            log.warning("skipping malformed bond quote for %s: %s", key, exc)
            continue
    return [r for r in rows.values()
            if any(r[c] is not None for c in ("cb_pct", "y3m_pct", "y10_pct"))]


def _refs_rows(store: Store, doc) -> list[dict]:
    asof = _asof(doc.updated_at)
    rows = []
    for r in doc.payload.get("rows", []):
        try:
            series = store.points(f"ref:{r['id']}")
            row = {
                "id": r["id"], "label": r["label"], "value_pct": r["value_pct"],
                "chg_1d_bp": bp_move(r["value_pct"], ref_close(series, asof, "1d")),
                "chg_1w_bp": bp_move(r["value_pct"], ref_close(series, asof, "1w")),
                "extra": r.get("extra"),
            }
        except (KeyError, TypeError, ValueError) as exc:
            # one malformed row must degrade that row, never 500 the dashboard
            log.warning("skipping malformed ref row for %s: %s", r.get("id"), exc)
            continue
        rows.append(row)
    return rows


def _refs_panel(store: Store) -> dict:
    doc = store.doc("rate_refs")
    if doc is None:
        return {"rows": [], "updated_at": None, "source": None}
    return {"rows": _refs_rows(store, doc), "updated_at": doc.updated_at, "source": doc.source}


def _macro_panel(store: Store, now: datetime) -> dict:
    """Timeline split: 'past' = last 7 days from macro_history (FF only serves
    the current week, so history is our own accumulation); 'releases' = the
    calendar's upcoming entries. Unparseable times ("TBD") stay upcoming."""
    panel = _doc_panel(store, "macro_calendar", "releases")
    upcoming = []
    for r in panel["releases"]:
        try:
            if datetime.fromisoformat(r["time"]) < now:
                continue
        except (KeyError, TypeError, ValueError):
            pass
        upcoming.append(r)
    panel["releases"] = upcoming
    hist = store.doc("macro_history")
    cutoff = now - timedelta(days=7)
    past = []
    for r in (hist.payload.get("releases", []) if hist else []):
        try:
            t = datetime.fromisoformat(r["time"])
            if cutoff <= t < now:
                past.append((t, r))
        except (KeyError, TypeError, ValueError):
            continue
    past.sort(key=lambda p: p[0])
    panel["past"] = [r for _, r in past]
    return panel


def _cycle_row(store: Store, cfg: CycleSeriesCfg, overlay: str | None) -> dict:
    """Latest transformed value + 1M/1Y diffs; a series with no data yet
    degrades to null cells, never drops the row (the tab layout is config)."""
    row = {"id": cfg.id, "name": cfg.name, "unit": cfg.unit,
           "value": None, "chg_1m": None, "chg_1y": None, "overlay": overlay}
    points = apply_transform(store.points(f"cycle:{cfg.id}"), cfg.transform)
    if not points:
        return row
    asof = max(points)
    value = points[asof]
    row["value"] = value
    for horizon in ("1m", "1y"):
        ref = ref_close(points, asof, horizon)
        row[f"chg_{horizon}"] = None if ref is None else round(value - ref, 2)
    return row


def _cycle_panel(
    store: Store, cycle_series: list[CycleSeriesCfg], cycle_tabs: list[CycleTabCfg]
) -> dict:
    by_id = {s.id: s for s in cycle_series}
    tabs = []
    for tab in cycle_tabs:
        panels = []
        for panel in tab.panels:
            rows = []
            for r in panel.rows:
                cfg = by_id.get(r.series)
                if cfg is None:  # config drift must degrade the row, not 500
                    log.warning("cycle tab %s references unknown series %s", tab.id, r.series)
                    continue
                rows.append(_cycle_row(store, cfg, r.overlay))
            panels.append({"title": panel.title, "rows": rows})
        tabs.append({"id": tab.id, "label": tab.label, "panels": panels})
    status = store.status("cycle")
    return {"tabs": tabs, "updated_at": status["last_success"] if status else None,
            "source": "cycle"}


def _doc_panel(store: Store, key: str, list_key: str) -> dict:
    doc = store.doc(key)
    if doc is None:
        return {list_key: [], "updated_at": None, "source": None}
    return {list_key: doc.payload[list_key], "updated_at": doc.updated_at, "source": doc.source}


def _country_risk_panel(store: Store) -> dict:
    """RISK MAP tab data: per-country scores from the country_risk doc."""
    doc = store.doc("country_risk")
    if doc is None:
        return {"asof": None, "countries": [], "updated_at": None, "source": None}
    return {"asof": doc.payload.get("asof"),
            "countries": doc.payload.get("countries", []),
            "updated_at": doc.updated_at,
            "source": doc.source}


def _gse_panel(store: Store) -> dict:
    """GSE retained-portfolio balances (Fannie Mae + Freddie Mac, $M monthly).

    Latest value plus 3m/12m changes computed from the stored month-end
    history; a series with no data yet degrades to null cells."""
    doc = store.doc("gse")
    out = {"updated_at": doc.updated_at if doc else None,
           "source": doc.source if doc else None, "series": []}

    def ref_back(pts: dict, asof, days: int):
        target = asof - timedelta(days=days)
        prior = [d for d in pts if d <= target]
        return pts[max(prior)] if prior else None

    for key, label in (
        ("gse:fannie-retained", "Fannie Mae retained"),
        ("gse:freddie-retained", "Freddie Mac retained"),
        ("gse:freddie-agency", "Freddie Mac agency MBS"),
    ):
        try:
            pts = store.points(key)
        except Exception:  # noqa: BLE001
            pts = {}
        if not pts:
            out["series"].append({"id": key, "label": label, "value_m": None,
                                  "asof": None, "chg_3m_m": None, "chg_12m_m": None})
            continue
        asof = max(pts)
        value = pts[asof]
        ref_3m = ref_back(pts, asof, 90)
        ref_12m = ref_back(pts, asof, 365)
        out["series"].append({
            "id": key, "label": label, "value_m": value,
            "asof": asof.isoformat(),
            "chg_3m_m": None if ref_3m is None else round(value - ref_3m, 1),
            "chg_12m_m": None if ref_12m is None else round(value - ref_12m, 1),
        })
    return out


def _xcorr_panel(store: Store) -> dict:
    """Cross-asset correlation matrices + regime pairs + realized vol."""
    doc = store.doc("xcorr")
    if doc is None:
        return {"asof": None, "labels": [], "keys": [], "matrix_60d": [],
                "matrix_252d": [], "pairs": [], "rvol": [],
                "updated_at": None, "source": None}
    p = doc.payload
    return {"asof": p.get("asof"), "labels": p.get("labels", []),
            "keys": p.get("keys", []),
            "groups": p.get("groups", []),
            "matrix_60d": p.get("matrix_60d", []),
            "matrix_252d": p.get("matrix_252d", []),
            "n_obs_60d": p.get("n_obs_60d"), "n_obs_252d": p.get("n_obs_252d"),
            "pairs": p.get("pairs", []), "pair_hist": p.get("pair_hist", {}),
            "rvol": p.get("rvol", []),
            "updated_at": doc.updated_at, "source": doc.source}


def _insights_panel(store: Store) -> dict:
    doc = store.doc("insights")
    status = store.doc("newsletter_status")
    if doc is None:
        return {
            "alerts": [],
            "trends": [],
            "newsletter": {"headline": "No automated digest yet", "bullets": [], "coverage": {}},
            "delivery": status.payload if status else {"enabled": False, "state": "disabled"},
            "digest_id": None,
            "generated_at": None,
            "updated_at": None,
            "source": None,
        }
    return {
        "digest_id": doc.payload.get("digest_id"),
        "alerts": doc.payload.get("alerts", []),
        "trends": doc.payload.get("trends", []),
        "newsletter": doc.payload.get("newsletter", {}),
        "delivery": status.payload if status else {"enabled": False, "state": "disabled"},
        "generated_at": doc.payload.get("generated_at"),
        "updated_at": doc.updated_at,
        "source": doc.source,
    }


def _movers_panel(store: Store) -> dict:
    """Single-stock sigma movers (weekly): top-10 +/- 5d and 20d z-moves."""
    doc = store.doc("movers")
    if doc is None:
        return {"asof": None, "indexes": {}, "updated_at": None, "source": None}
    return {"asof": doc.payload.get("asof"),
            "indexes": doc.payload.get("indexes", {}),
            "updated_at": doc.updated_at,
            "source": doc.source}


def _voldash_panel(store: Store) -> dict:
    """Vol dashboard: implied-vs-realized table, VIX/VVIX, spot-vol betas."""
    doc = store.doc("voldash")
    if doc is None:
        return {"asof": None, "rows": [], "vix_hist": [], "vvix_hist": [],
                "beta": {}, "beta_hist": {"vix_spx": [], "vvix_vix": []},
                "regime": None, "updated_at": None, "source": None}
    p = doc.payload
    return {"asof": p.get("asof"), "rows": p.get("rows", []),
            "vix_hist": p.get("vix_hist", []), "vvix_hist": p.get("vvix_hist", []),
            "beta": p.get("beta", {}),
            "beta_hist": p.get("beta_hist", {"vix_spx": [], "vvix_vix": []}),
            "regime": p.get("regime"),
            "updated_at": doc.updated_at, "source": doc.source}


def _radar_panel(store: Store) -> dict:
    """Market radar: multi-indicator percentile snapshot for the HOME tab."""
    doc = store.doc("home_radar")
    if doc is None:
        return {"as_of": None, "regime": "UNKNOWN", "verdict": None,
                "indicators": [], "updated_at": None, "source": None}
    p = doc.payload
    return {"as_of": p.get("as_of"), "regime": p.get("regime", "UNKNOWN"),
            "verdict": p.get("verdict"),
            "indicators": p.get("indicators", []),
            "updated_at": doc.updated_at, "source": doc.source}


def _hyper_panel(store: Store) -> dict:
    """Hyperscaler desk: debt issuance + equity cards, with FINRA short
    interest merged onto each card by ticker (batch 6)."""
    doc = store.doc("hyper")
    if doc is None:
        return {"as_of": None, "issuances": [], "equities": [],
                "note": None, "updated_at": None, "source": None}
    p = doc.payload
    equities = p.get("equities", [])
    short_doc = store.doc("finra_short")
    short_by_ticker = ((short_doc.payload.get("tickers") or {})
                       if short_doc else {})
    for e in equities:
        e["short"] = short_by_ticker.get(e.get("ticker"))  # None until run
    return {"as_of": p.get("as_of"), "issuances": p.get("issuances", []),
            "equities": equities, "note": p.get("note"),
            "short_as_of": (short_doc.payload.get("as_of")
                            if short_doc else None),
            "updated_at": doc.updated_at, "source": doc.source}


def _universe_panel(store: Store, universe_id: str) -> dict:
    """Universe coverage-map panel (generic, batch 7/8).

    Doc key is <universe_id>_graph (generic coverage-map schema:
    verticals -> companies, edges, aggregates, risk_notes).
    batch 7: ai_flow <- ai_buildout; batch 8: ms_flow <- market_structure.
    """
    doc = store.doc(f"{universe_id}_graph")
    if doc is None:
        return {"as_of": None, "universe_id": universe_id, "verticals": [],
                "edges": [], "rollups": {}, "capex_stack": {},
                "risk_notes": [], "aggregates": {},
                "updated_at": None, "source": None}
    p = doc.payload
    return {"as_of": p.get("as_of"), "universe_id": p.get("universe_id"),
            "verticals": p.get("verticals", []), "edges": p.get("edges", []),
            "rollups": p.get("rollups", {}),
            "capex_stack": p.get("capex_stack", {}),
            "risk_notes": p.get("risk_notes", []),
            "aggregates": p.get("aggregates", {}),
            "capex_overlay": p.get("capex_overlay"),
            "_schema_note": p.get("_schema_note"),
            "updated_at": doc.updated_at, "source": doc.source}


# Back-compat alias (batch 7 callers).
def _ai_flow_panel(store: Store) -> dict:
    return _universe_panel(store, "ai_buildout")


def _tsv_panel(store: Store) -> dict:
    """Tokenized Securities Venue watch: universe graph + regulatory scan.

    Generic coverage-map graph doc (tokenized_securities_graph) plus the
    weekly tsv_watch regulatory scan under the "watch" key.
    """
    base = _universe_panel(store, "tokenized_securities")
    watch_doc = store.doc("tsv_watch")
    base["watch"] = watch_doc.payload if watch_doc else None
    base["watch_updated_at"] = watch_doc.updated_at if watch_doc else None
    graph_doc = store.doc("tokenized_securities_graph")
    base["order"] = (graph_doc.payload.get("order") if graph_doc else None) or {}
    return base


def _defi_panel(store: Store) -> dict:
    """DeFi tab: Zyfai vault rows plus CoinGecko crypto breadth (batch 11).

    The crypto table is a separate key so the vault renderer is untouched;
    an absent coingecko doc degrades to an empty list, never a 500.
    """
    panel = _doc_panel(store, "defi_pools", "rows")
    cg = store.doc("coingecko")
    panel["crypto"] = cg.payload.get("coins", []) if cg else []
    panel["crypto_as_of"] = cg.payload.get("as_of") if cg else None
    panel["btc_dominance_pct"] = cg.payload.get("btc_dominance_pct") if cg else None
    panel["crypto_updated_at"] = cg.updated_at if cg else None
    return panel


def _worldbank_panel(store: Store) -> dict:
    """Global macro fundamentals (batch 11): latest GDP/CPI/unemployment
    per bond-matrix country, from the worldbank doc."""
    doc = store.doc("worldbank")
    if doc is None:
        return {"as_of": None, "countries": {}, "updated_at": None, "source": None}
    return {"as_of": doc.payload.get("as_of"),
            "countries": doc.payload.get("countries", {}),
            "updated_at": doc.updated_at, "source": doc.source}


def _usaspending_panel(store: Store) -> dict:
    """US fiscal pulse (batch 11): monthly obligations, top recipients and
    awarding agencies from the usaspending doc."""
    doc = store.doc("usaspending")
    if doc is None:
        return {"as_of": None, "monthly": [], "top_recipients": [],
                "top_agencies": [], "updated_at": None, "source": None}
    p = doc.payload
    return {"as_of": p.get("as_of"), "monthly": p.get("monthly", []),
            "top_recipients": p.get("top_recipients", []),
            "top_agencies": p.get("top_agencies", []),
            "updated_at": doc.updated_at, "source": doc.source}


def _finnhub_panel(store: Store) -> dict:
    """Finnhub watchlist intel (batch 11): upcoming earnings for universe
    tickers + insider sentiment for earnings names. Empty until a
    FINNHUB_API_KEY is configured."""
    doc = store.doc("finnhub")
    if doc is None:
        return {"as_of": None, "earnings": [], "insider": [],
                "key_configured": False, "updated_at": None, "source": None}
    p = doc.payload
    return {"as_of": p.get("as_of"), "window": p.get("window"),
            "earnings": p.get("earnings", []), "insider": p.get("insider", []),
            "key_configured": True,
            "updated_at": doc.updated_at, "source": doc.source}


def _predict_panel(store: Store) -> dict:
    """Prediction markets (batch 12): venue snapshots, cross-venue edge
    estimates, unusual movers, and the calibration leaderboard. Empty
    until the polymarket/kalshi fetchers and the edge engine have run."""
    edge = store.doc("pred_edge")
    poly = store.doc("polymarket")
    kal = store.doc("kalshi")
    if edge is None:
        return {"as_of": None, "edges": [], "movers": [], "calibration": [],
                "polymarket": [], "kalshi": [], "disclaimer": None,
                "updated_at": None, "source": None}
    p = edge.payload
    return {
        "as_of": p.get("as_of"),
        "edges": p.get("edges", []),
        "movers": p.get("movers", []),
        "calibration": p.get("calibration", []),
        "polymarket": (poly.payload.get("markets", []) if poly else [])[:15],
        "kalshi": (kal.payload.get("markets", []) if kal else [])[:15],
        "tracked_count": p.get("tracked_count", 0),
        "resolved_this_run": p.get("resolved_this_run", 0),
        "coverage": p.get("coverage", {}),
        "skipped": p.get("skipped", []),
        "disclaimer": p.get("disclaimer"),
        "updated_at": edge.updated_at, "source": edge.source,
    }


def build_dashboard(
    store: Store,
    indexes: list[IndexCfg],
    now: datetime,
    cycle_series: list[CycleSeriesCfg] = (),
    cycle_tabs: list[CycleTabCfg] = (),
) -> dict:
    equity_doc = store.doc("equity_quotes")
    bonds_doc = store.doc("bond_quotes")
    return {
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "panels": {
            "macro": _macro_panel(store, now),
            "equity": {"rows": _equity_rows(store, indexes),
                       "updated_at": equity_doc.updated_at if equity_doc else None},
            "bonds": {"rows": _bond_rows(store),
                      "updated_at": bonds_doc.updated_at if bonds_doc else None,
                      "source": bonds_doc.source if bonds_doc else None},
            "news": _doc_panel(store, "news", "items"),
            "defi": _defi_panel(store),
            "midnight": _doc_panel(store, "midnight_curve", "rows"),
            "morpho": _doc_panel(store, "morpho_markets", "rows"),
            "refs": _refs_panel(store),
            "cycle": _cycle_panel(store, list(cycle_series), list(cycle_tabs)),
            "insights": _insights_panel(store),
            "riskmap": _country_risk_panel(store),
            "gse": _gse_panel(store),
            "xcorr": _xcorr_panel(store),
            "movers": _movers_panel(store),
            "voldash": _voldash_panel(store),
            "radar": _radar_panel(store),
            "hyper": _hyper_panel(store),
            "ai_flow": _universe_panel(store, "ai_buildout"),
            "ms_flow": _universe_panel(store, "market_structure"),
            "tsv": _tsv_panel(store),
            "worldbank": _worldbank_panel(store),
            "usaspending": _usaspending_panel(store),
            "finnhub": _finnhub_panel(store),
            "predict": _predict_panel(store),
        },
    }
