"""Assemble the /api/dashboard payload (spec §5) from store contents."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

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


def econ_calendar_payload(store: Store, now: datetime) -> dict:
    """Econ calendar: upcoming releases + past 7 days with actuals.

    Timeline split: 'past' = last 7 days from macro_history (FF only serves
    the current week, so history is our own accumulation); 'releases' = the
    calendar's upcoming entries. Unparseable times ("TBD") stay upcoming.
    Shared by the dashboard macro panel and GET /api/econ-calendar.
    """
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


def _macro_panel(store: Store, now: datetime) -> dict:
    return econ_calendar_payload(store, now)


def _auctions_panel(store: Store) -> dict:
    """Treasury auction surveillance: upcoming schedule + recent results.

    Reads the ``upcoming_auctions`` / ``auction_results`` docs written by the
    auctions fetcher (Fiscal Data Treasury API). One malformed record degrades
    that row, never the panel.
    """
    upcoming, recent = [], []
    upc = store.doc("upcoming_auctions")
    if upc is not None:
        for a in upc.payload.get("auctions", [])[:8]:
            try:
                upcoming.append({
                    "auction_date": a["auction_date"],
                    "bucket": a.get("bucket"),
                    "security_term": a.get("security_term"),
                    "security_type": a.get("security_type"),
                    "offering_amt": a.get("offering_amt"),
                })
            except (KeyError, TypeError):
                continue
    res = store.doc("auction_results")
    if res is not None:
        for a in res.payload.get("results", [])[:8]:
            try:
                recent.append({
                    "auction_date": a["auction_date"],
                    "bucket": a.get("bucket"),
                    "high_yield": a.get("high_yield"),
                    "bid_to_cover": a.get("bid_to_cover"),
                    "indirect_pct": a.get("indirect_pct"),
                    "direct_pct": a.get("direct_pct"),
                    "dealer_pct": a.get("dealer_pct"),
                    "offering_amt": a.get("offering_amt"),
                })
            except (KeyError, TypeError):
                continue
    docs = [d for d in (upc, res) if d is not None]
    return {
        "upcoming": upcoming,
        "recent": recent,
        "updated_at": max((d.updated_at for d in docs), default=None),
        "source": "fiscaldata.treasury.gov",
    }


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


def _regwatch_panel(store: Store) -> dict:
    """REG WATCH tab: regulatory news items + rulemaking tracker + topic rollup."""
    doc = store.doc("regwatch")
    if doc is None:
        return {"items": [], "rules": [], "topics": {}, "feed_status": {},
                "updated_at": None, "source": None}
    p = doc.payload
    return {"items": p.get("items", []), "rules": p.get("rules", []),
            "topics": p.get("topics", {}), "feed_status": p.get("feed_status", {}),
            "updated_at": doc.updated_at, "source": doc.source}


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


def _options_panel(store: Store) -> dict:
    """CBOE delayed options aggregates + unusual activity (doc 'options')."""
    doc = store.doc("options")
    if doc is None:
        return {"asof": None, "symbols": {}, "note": None,
                "updated_at": None, "source": None}
    p = doc.payload or {}
    return {"asof": p.get("asof"), "symbols": p.get("symbols", {}),
            "note": p.get("note"), "updated_at": doc.updated_at,
            "source": doc.source}


def _etfflows_panel(store: Store) -> dict:
    """ETF AUM/NAV/shares snapshot league table (doc 'etfflows')."""
    doc = store.doc("etfflows")
    if doc is None:
        return {"asof": None, "funds": {}, "classes": {},
                "note": None, "updated_at": None, "source": None}
    p = doc.payload or {}
    return {"asof": p.get("asof"), "funds": p.get("funds", {}),
            "classes": p.get("classes", {}), "note": p.get("note"),
            "pending": p.get("pending", []),
            "updated_at": doc.updated_at, "source": doc.source}


def _tff_panel(store: Store) -> dict:
    """OFR Traders in Financial Futures (doc 'tff').

    Curated groups with rolling weekly history per row — powers the
    POSITIONING TFF view in one fetch instead of a 150-series fan-out.
    """
    doc = store.doc("tff")
    if doc is None:
        return {"asof": None, "mnemonic_count": 0, "stored": 0,
                "groups": [], "note": None,
                "updated_at": None, "source": None}
    p = doc.payload or {}
    return {"asof": p.get("asof"), "mnemonic_count": p.get("mnemonic_count", 0),
            "stored": p.get("stored", 0), "groups": p.get("groups", []),
            "note": p.get("note"), "source_url": p.get("source_url"),
            "updated_at": doc.updated_at, "source": doc.source}


def _tape_panel(store: Store) -> dict:
    """NasdaqTrader tape/exchange volume (doc 'tape').

    Rolling daily history per venue per metric (shares/trades/dollar)
    plus market-wide aggregates — powers the EQUITY Tape Volume grid
    in one fetch instead of a 250-series fan-out.
    """
    doc = store.doc("tape")
    if doc is None:
        return {"as_of": None, "dates": [], "history_days": 0,
                "venues": {}, "market": {}, "note": None,
                "tape_labels": {}, "metric_labels": {},
                "updated_at": None, "source": None}
    p = doc.payload or {}
    return {"as_of": p.get("as_of"), "dates": p.get("dates", []),
            "history_days": p.get("history_days", 0),
            "max_source_days": p.get("max_source_days", 30),
            "venues": p.get("venues", {}), "market": p.get("market", {}),
            "note": p.get("note"), "tape_labels": p.get("tape_labels", {}),
            "metric_labels": p.get("metric_labels", {}),
            "updated_at": doc.updated_at, "source": doc.source}


def _otc_panel(store: Store) -> dict:
    """FINRA OTC Market (keyless api.finra.org, group otcMarket).

    Top-100 issues by month, monthly/annual market statistics, daily-list
    corporate-action feed, threshold securities, current trading halts,
    symbol directory. Powers the EQUITY "OTC Market" sub-tab.
    Degrades gracefully when the fetcher hasn't run yet.
    """
    empty = {"as_of": None, "top100": None, "top100_months": [],
             "monthly_totals": {"shares": [], "dollarvol": [], "trades": []},
             "yearly": [], "dailylist": None, "threshold": None,
             "halts": None, "secmaster_count": 0, "mplist_count": 0,
             "updated_at": None, "source": None}
    status = store.doc("finra_otc")
    if status is None:
        return empty

    def _hist(sid: str) -> list:
        try:
            pts = store.points(f"cycle:{sid}")
        except Exception:  # noqa: BLE001
            return []
        return [{"d": d.isoformat(), "v": v} for d, v in sorted(pts.items())]

    # latest top-100 month from the totals series
    top100_months = []
    try:
        for d in store.points("cycle:otc-top100-total-shares"):
            top100_months.append(d.strftime("%Y-%m"))
    except Exception:  # noqa: BLE001
        pass
    top100_months = sorted(set(top100_months))
    top100 = None
    if top100_months:
        doc = store.doc(f"otc-top100-{top100_months[-1]}")
        if doc is not None:
            top100 = doc.payload

    # latest daily-list / threshold docs (walk back up to 10 days)
    dailylist = threshold = None
    dl_date = th_date = None
    for i in range(10):
        ds = (date.today() - timedelta(days=i)).isoformat()
        if dailylist is None:
            doc = store.doc(f"otc-dailylist-{ds}")
            if doc is not None and (doc.payload or {}).get("count"):
                dailylist, dl_date = doc.payload, ds
        if threshold is None:
            doc = store.doc(f"otc-threshold-{ds}")
            if doc is not None and (doc.payload or {}).get("rows"):
                threshold, th_date = doc.payload, ds
        if dailylist and threshold:
            break

    halts_doc = store.doc("otc-halts-current")
    sm_doc = store.doc("otc-secmaster")
    mp_doc = store.doc("otc-mplist")

    # yearly stats: All OTC / All type totals
    yearly = []
    try:
        pts = store.points("cycle:otc-yearly-all-otc-all-type-totalShareVolume")
        pts_dv = store.points("cycle:otc-yearly-all-otc-all-type-totalDollarVolume")
        pts_tr = store.points("cycle:otc-yearly-all-otc-all-type-totalTransactionCount")
        for d in sorted(pts):
            yearly.append({"y": d.year, "shares": pts[d],
                           "dollarvol": pts_dv.get(d), "trades": pts_tr.get(d)})
    except Exception:  # noqa: BLE001
        pass

    return {
        "as_of": (status.payload or {}).get("updated"),
        "top100": top100, "top100_months": top100_months,
        "monthly_totals": {
            "shares": _hist("otc-monthly-total-shares"),
            "dollarvol": _hist("otc-monthly-total-dollarvol"),
            "trades": _hist("otc-monthly-total-trades"),
        },
        "yearly": yearly,
        "dailylist": ({"date": dl_date, **dailylist} if dailylist else None),
        "threshold": ({"date": th_date, **threshold} if threshold else None),
        "halts": halts_doc.payload if halts_doc else None,
        "secmaster_count": (sm_doc.payload or {}).get("count", 0) if sm_doc else 0,
        "mplist_count": len((mp_doc.payload or {}).get("rows", [])) if mp_doc else 0,
        "updated_at": status.updated_at, "source": status.source,
    }


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
    rwa = store.doc("rwa")
    panel["rwa"] = rwa.payload if rwa else None
    panel["rwa_updated_at"] = rwa.updated_at if rwa else None
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


def _ats_panel(store: Store, ats: dict | None, venues: dict,
               ats_upd, ats_src) -> dict | None:
    """FINRA ATS Transparency panel payload.

    Venue leaderboard (weekly keyed series when present, keyless monthly
    blocksSummary otherwise): latest shares/trades, share of ATS volume,
    and nominal+% deltas across the standard horizons. Trend history for
    the chart. Degrades to None when the fetcher hasn't run yet.
    """
    if ats is None:
        return None
    weekly = bool(store.points("cycle:ats-total-shares"))
    prefix = "" if weekly else "-m"
    step_days = 7 if weekly else 31  # horizon anchor for monthly fallback

    def _hist(sid: str) -> list:
        try:
            pts = store.points(f"cycle:ats{prefix}-{sid}")
        except Exception:  # noqa: BLE001 — optional section
            return []
        return sorted(pts.items())

    def _back(pts: list, days: int):
        if len(pts) < 2:
            return None, None
        cur_d, cur_v = pts[-1]
        ref = None
        for d, v in reversed(pts[:-1]):
            if (cur_d - d).days >= days:
                ref = (d, v)
                break
        if ref is None or not ref[1]:
            return None, None
        return cur_v - ref[1], (cur_v - ref[1]) / ref[1]

    total_hist = _hist("total-shares")
    horizons = (("1w", 7), ("1m", 30), ("1q", 91), ("1y", 365)) if weekly \
        else (("1m", 31), ("3m", 93), ("1y", 365), ("3y", 1095))
    leaderboard = []
    for mpid, meta in venues.items():
        slug = "".join(c for c in mpid.lower() if c.isalnum())
        shares = _hist(f"{slug}-shares")
        trades = _hist(f"{slug}-trades")
        if not shares:
            continue
        cur = shares[-1][1]
        share_of = (cur / total_hist[-1][1]
                    if total_hist and total_hist[-1][1] else None)
        row = {"mpid": mpid, "name": (meta or {}).get("name", mpid),
               "shares": cur,
               "trades": trades[-1][1] if trades else None,
               "share_of_ats": share_of,
               "spark": [v for _, v in shares[-12:]],
               "deltas": {}}
        for tag, days in horizons:
            nom, pct = _back(shares, days)
            row["deltas"][tag] = {"nom": nom, "pct": pct}
        leaderboard.append(row)
    leaderboard.sort(key=lambda r: r["shares"], reverse=True)

    # Latest weekly snapshot (keyed): top-50 symbols w/ top venues — backs
    # the security lookup. Keyless-only runs carry latest = None.
    latest_doc = None
    latest_week = (ats or {}).get("latest_week")
    if latest_week:
        doc = store.doc(f"ats-week-{latest_week}")
        if doc:
            latest_doc = doc.payload

    trend = [{"d": d.isoformat(), "v": v} for d, v in total_hist[-260:]]
    trend_trades = [{"d": d.isoformat(), "v": v}
                    for d, v in _hist("total-trades")[-260:]]
    return {
        "configured": (ats or {}).get("configured", False),
        "weekly": weekly,
        "as_of": latest_week or ((ats or {}).get("keyless_range") or [None])[-1],
        "weeks_stored": (ats or {}).get("weeks_stored", 0),
        "backfill_pending": (ats or {}).get("backfill_pending", 0),
        "horizons": [t for t, _ in horizons],
        "leaderboard": leaderboard,
        "trend": trend,
        "trend_trades": trend_trades,
        "latest_week_detail": latest_doc,
        "updated_at": ats_upd, "source": ats_src,
    }


def _finra_panel(store: Store) -> dict:
    """Dedicated FINRA tab: every FINRA-sourced dataset in one place.

    Reg SHO daily short volume + OTC threshold list, biweekly short
    interest, bond market breadth/sentiment, most-active corporate bonds,
    capped-volume report, margin statistics, TRACE treasury daily and
    TRACE monthly (currently blocked by the FINRA CDN 403).
    Each section degrades to empty/None when its fetcher hasn't run yet.
    """
    def _d(key: str):
        doc = store.doc(key)
        if doc is None:
            return None, None, None
        return doc.payload, doc.updated_at, doc.source

    regsho, regsho_upd, regsho_src = _d("regsho_daily")
    tstats, tstats_upd, tstats_src = _d("ticker_stats")
    thresh, thresh_upd, thresh_src = _d("regsho_threshold")
    short, short_upd, short_src = _d("finra_short")
    breadth, breadth_upd, breadth_src = _d("finra_breadth")
    corp, corp_upd, corp_src = _d("finra_corp")
    capped, capped_upd, capped_src = _d("finra_capped")
    margin, margin_upd, margin_src = _d("finra_margin")
    treas, treas_upd, treas_src = _d("trace_treasury")
    monthly, monthly_upd, monthly_src = _d("trace_monthly")
    star, star_upd, star_src = _d("finra_ids_star")
    ats, ats_upd, ats_src = _d("finra_ats")
    ats_venues = (_d("ats_venues")[0] or {}).get("mpids", {})

    # Margin debit sparkline (last ~2y of monthly points)
    margin_hist = []
    try:
        pts = store.points("cycle:finra-margin-debit")
        for d in sorted(pts)[-26:]:
            margin_hist.append({"d": d.isoformat(), "v": pts[d]})
    except Exception:  # noqa: BLE001 — sparkline is optional
        margin_hist = []

    # Breadth AD-spread sparkline: corporate all-securities advancers-decliners
    breadth_hist = []
    try:
        pts = store.points("cycle:finra-breadth-corp-all-adspread")
        for d in sorted(pts)[-60:]:
            breadth_hist.append({"d": d.isoformat(), "v": pts[d]})
    except Exception:  # noqa: BLE001
        breadth_hist = []

    corp_lists = {}
    if corp:
        bond_hist = corp.get("bond_hist") or {}
        for dslug, lst in (corp.get("lists") or {}).items():
            bonds = (lst or {}).get("bonds") or []
            slim = []
            for b in bonds[:15]:
                b2 = dict(b)
                # 52 weekly-downsampled price points for the 1Y sparkline
                h = bond_hist.get(b.get("symbol")) or {}
                ps = [p for p in (h.get("p") or []) if p is not None]
                if len(ps) > 52:
                    step = len(ps) / 52
                    ps = [ps[int(i * step)] for i in range(52)]
                b2["spark"] = ps
                slim.append(b2)
            corp_lists[dslug] = {"as_of": (lst or {}).get("as_of"),
                                 "count": len(bonds), "bonds": slim}

    return {
        "regsho": ({"as_of": regsho.get("as_of"), "markets": regsho.get("markets", {}),
                    "top50": (regsho.get("top50") or [])[:25],
                    "tickers": regsho.get("tickers", {}),
                    "updated_at": regsho_upd, "source": regsho_src}
                   if regsho else None),
        "ticker_stats": ({"as_of": tstats.get("as_of"),
                          "count": tstats.get("count", 0),
                          "tickers": tstats.get("tickers", {}),
                          "updated_at": tstats_upd, "source": tstats_src}
                         if tstats else None),
        "threshold": ({"as_of": thresh.get("as_of"), "count": thresh.get("count", 0),
                       "securities": (thresh.get("securities") or [])[:50],
                       "updated_at": thresh_upd, "source": thresh_src}
                      if thresh else None),
        "short_interest": ({"as_of": short.get("as_of"),
                            "total_short_shares": short.get("total_short_shares"),
                            "updated_at": short_upd, "source": short_src}
                           if short else None),
        "breadth": ({"as_of": breadth.get("as_of"), "status": breadth.get("status"),
                     "series_count": len(breadth.get("series", []) or []),
                     "adspread_hist": breadth_hist,
                     "updated_at": breadth_upd, "source": breadth_src}
                    if breadth else None),
        "corp": ({"as_of": corp.get("as_of"), "status": corp.get("status"),
                  "lists": corp_lists,
                  "refi_wall": corp.get("refi_wall"),
                  "registry_issues": len((corp.get("cusip_registry") or {})),
                  "updated_at": corp_upd, "source": corp_src}
                 if corp else None),
        "capped": ({"as_of": capped.get("as_of"), "grades": capped.get("grades", {}),
                    "months": capped.get("months", 0),
                    "updated_at": capped_upd, "source": capped_src}
                   if capped else None),
        "margin": ({"as_of": margin.get("as_of"),
                    "latest_debit_m": margin.get("latest_debit_m"),
                    "debit_hist": margin_hist,
                    "updated_at": margin_upd, "source": margin_src}
                   if margin else None),
        "trace_treasury": ({"as_of": treas.get("as_of"),
                            "series_count": len(treas.get("series", []) or []),
                            "updated_at": treas_upd, "source": treas_src}
                           if treas else None),
        "trace_monthly": ({"as_of": monthly.get("as_of"),
                           "blocked": monthly.get("status") == "blocked",
                           "updated_at": monthly_upd, "source": monthly_src}
                          if monthly else {"as_of": None, "blocked": True,
                                           "updated_at": None, "source": None}),
        "star": ({"as_of": star.get("as_of"),
                  "latest": star.get("latest", {}),
                  "days": len(store.points("cycle:star-tba-par")),
                  "updated_at": star_upd, "source": star_src}
                 if star else None),
        "ats": _ats_panel(store, ats, ats_venues, ats_upd, ats_src),
    }


def _shortinterest_panel(store: Store) -> dict:
    """Focused POSITIONING → Short Interest view.

    Reg SHO daily short volume + OTC threshold list + FINRA biweekly
    short-interest settlement. A deeper cut than the FINRA tab summary:
    full top-50 shorted tickers, the complete threshold list, per-ticker
    settlement detail (change vs prior settlement, avg daily volume,
    days-to-cover) for the watchlist, and cycle series the UI charts.
    Every section degrades to None when its fetcher hasn't run yet.
    """
    def _d(key: str):
        doc = store.doc(key)
        if doc is None:
            return None, None, None
        return doc.payload, doc.updated_at, doc.source

    regsho, regsho_upd, regsho_src = _d("regsho_daily")
    thresh, thresh_upd, thresh_src = _d("regsho_threshold")
    short, short_upd, short_src = _d("finra_short")

    # Threshold list can be long; keep the payload bounded but tell the UI
    # the true count so it can say "showing N of M".
    secs = (thresh.get("securities") or []) if thresh else []
    latest_upd = max([u for u in (regsho_upd, thresh_upd, short_upd) if u],
                     default=None)
    # Full-universe coverage (2026-10-06 completeness): honest scope labels.
    # Defensive: some tests use a minimal FakeStore without the new tables.
    def _scope(fn, default):
        try:
            return getattr(store, fn)()
        except AttributeError:
            return default
    si_scope = _scope("short_interest_scope",
                      {"settlements": 0, "first": None, "last": None, "rows": 0})
    top_scope = _scope("regsho_top_scope",
                       {"first": None, "last": None, "days": 0, "rows": 0})
    try:
        thr_dates = store.threshold_hist_dates()
    except AttributeError:
        thr_dates = []
    return {
        "regsho": ({"as_of": regsho.get("as_of"),
                    "markets": regsho.get("markets", {}),
                    "top50": regsho.get("top50") or [],
                    "tickers": regsho.get("tickers", {}),
                    "updated_at": regsho_upd, "source": regsho_src}
                   if regsho else None),
        "regsho_top_scope": top_scope,
        "threshold": ({"as_of": thresh.get("as_of"), "count": thresh.get("count", 0),
                       "securities": secs[:200],
                       "history_snapshots": len(thr_dates),
                       "history_first": thr_dates[-1].isoformat() if thr_dates else None,
                       "history_last": thr_dates[0].isoformat() if thr_dates else None,
                       "updated_at": thresh_upd, "source": thresh_src}
                      if thresh else None),
        "short_interest": ({"as_of": short.get("as_of"),
                            "total_short_shares": short.get("total_short_shares"),
                            "tickers": short.get("tickers", {}),
                            "updated_at": short_upd, "source": short_src}
                           if short else None),
        "si_scope": si_scope,
        "updated_at": latest_upd,
        "source": "finra",
    }


def _factbook_panel(store: Store) -> dict:
    """FINRA TRACE Fact Book — quarterly + annual fixed-income fact books.

    Quarterly average-daily trades/par (ADT/ADV) by product and trade-size
    bucket, customer buy-sell ratios, and top-traded lists, plus the annual
    workbooks: time-of-day execution stats, annual top lists, annual ADV/ADT,
    issues outstanding by rating/issuer/type, and dealer concentration.
    The UI's quarterly-history chart reads ``hist`` (fb-<prod>-trades/pv
    cycle series). Every section degrades to None/{} when the fetcher
    hasn't run yet.
    """
    doc = store.doc("finra_factbook")
    payload = doc.payload if doc and isinstance(doc.payload, dict) else {}

    # ADT/ADV quarterly history from the cycle series the fetcher writes
    # (extended back to 2021 by the annual job's Graph Data merge).
    hist: dict = {}
    for prod in ("ig", "hy", "agency", "abs", "absx", "cmo", "mbs", "tba"):
        for kind in ("trades", "pv"):
            key = f"fb-{prod}-{kind}"
            try:
                pts = store.points(f"cycle:{key}")
            except Exception:  # noqa: BLE001 — history is optional
                pts = {}
            if pts:
                hist[key] = [{"d": d.isoformat(), "v": pts[d]}
                             for d in sorted(pts) if pts[d] is not None]

    return {
        "as_of": payload.get("as_of"),
        "quarter_end": payload.get("quarter_end"),
        "headlines": payload.get("headlines") or {},
        "top": payload.get("top") or {},
        "buy_sell_latest": payload.get("buy_sell_latest"),
        "buckets_latest": payload.get("buckets_latest"),
        "hist": hist,
        "annual_top": payload.get("annual_top") or {},
        "annual_as_of": payload.get("annual_as_of"),
        "interval": payload.get("interval") or {},
        "interval_note": payload.get("interval_note"),
        "annual_adv_adt": payload.get("annual_adv_adt") or {},
        "issue": payload.get("issue") or {},
        "issue_mix": payload.get("issue_mix") or {},
        "issue_note": payload.get("issue_note"),
        "participant": payload.get("participant") or {},
        "participant_note": payload.get("participant_note"),
        "updated_at": doc.updated_at if doc else None,
        "source": doc.source if doc else "finra",
    }


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
            "auctions": _auctions_panel(store),
            "equity": {"rows": _equity_rows(store, indexes),
                       "updated_at": equity_doc.updated_at if equity_doc else None},
            "bonds": {"rows": _bond_rows(store),
                      "updated_at": bonds_doc.updated_at if bonds_doc else None,
                      "source": bonds_doc.source if bonds_doc else None},
            "news": _doc_panel(store, "news", "items"),
            "regwatch": _regwatch_panel(store),
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
            "options": _options_panel(store),
            "etfflows": _etfflows_panel(store),
            "tape": _tape_panel(store),
            "tff": _tff_panel(store),
            "radar": _radar_panel(store),
            "hyper": _hyper_panel(store),
            "ai_flow": _universe_panel(store, "ai_buildout"),
            "ms_flow": _universe_panel(store, "market_structure"),
            "bank_flow": _universe_panel(store, "bank_fixed_income"),
            "tech_flow": _universe_panel(store, "technology"),
            "vendor_flow": _universe_panel(store, "vendor"),
            "etf_flow": _universe_panel(store, "etf"),
            "crypto_flow": _universe_panel(store, "crypto"),
            "tsv": _tsv_panel(store),
            "worldbank": _worldbank_panel(store),
            "usaspending": _usaspending_panel(store),
            "finnhub": _finnhub_panel(store),
            "predict": _predict_panel(store),
            "finra": _finra_panel(store),
            "otc": _otc_panel(store),
            "shortinterest": _shortinterest_panel(store),
            "factbook": _factbook_panel(store),
        },
    }
