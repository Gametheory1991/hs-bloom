"""Universe money-flow knowledge graph (generic coverage-map template).

Takes a universe_id (e.g. "ai_buildout") and reads the vendored universe
file data/<universe_id>.json — NO AI-specific hardcoding here: the same
code must later accept broker-dealer, bank, crypto and fixed-income
universes. The universe file declares verticals -> companies
[{id, name, ticker, cik, kind}] plus an "edges" list (press-reported
deals, per-edge source + confidence) and optional "risk_notes".

Weekly, zero HTTP. Overlays the latest quarterly financials from the
<universe_id>_capex doc (written by fetch_universe_capex) onto public
nodes, by ticker:

    revenue, capex, ocf, fcf, capex_intensity, funding_gap, debt, flags

Private-kind nodes get no financials (flag "private_no_financials" only
when they have no ticker). Risk flags are computed by the capex fetcher.

Graph-level rollups: total reported deal value, per-kind totals, and the
nodes currently flagged cashflow_negative. Writes doc
"<universe_id>_graph".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from importlib.resources import files

from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "universe-graph-weekly"


def load_universe(universe_id: str) -> dict:
    """Load data/<universe_id>.json. Generic — no AI-specific assumptions."""
    raw = (files("collector") / "data" / f"{universe_id}.json").read_text(
        encoding="utf-8")
    return json.loads(raw)


def _overlay_financials(universe: dict, capex_doc: dict | None) -> dict:
    companies = (capex_doc or {}).get("companies", {})
    by_id = {c.get("id"): c for c in companies.values()}
    for v in universe.get("verticals", []):
        for node in v.get("companies", []):
            fin = by_id.get(node["id"])
            if fin:
                node["financials"] = {
                    k: fin.get(k) for k in (
                        "latest_quarter", "currency", "revenue", "capex",
                        "ocf", "fcf", "capex_intensity", "funding_gap",
                        "debt", "debt_to_assets")
                }
                node["flags"] = fin.get("flags", [])
                node["vertical"] = fin.get("vertical", v["id"])
            else:
                node["financials"] = None
                node["flags"] = (["private_no_financials"]
                                 if node.get("kind") != "public" else [])
                node.setdefault("vertical", v["id"])
    return universe


def _rollups(universe: dict) -> dict:
    edges = universe.get("edges", [])
    total = sum(e.get("amount_bn") or 0 for e in edges)
    by_kind: dict[str, float] = {}
    for e in edges:
        by_kind[e.get("kind", "?")] = (by_kind.get(e.get("kind", "?"), 0.0)
                                       + (e.get("amount_bn") or 0))
    neg_fcf = []
    for v in universe.get("verticals", []):
        for n in v.get("companies", []):
            if "cashflow_negative" in (n.get("flags") or []):
                neg_fcf.append(n["name"])
    return {
        "total_reported_bn": round(total, 1),
        "by_kind_bn": {k: round(v, 1) for k, v in by_kind.items()},
        "edge_count": len(edges),
        "node_count": sum(len(v.get("companies", []))
                          for v in universe.get("verticals", [])),
        "nodes_cashflow_negative": neg_fcf,
    }


async def fetch_universe_graph(store: Store,
                               universe_id: str = "ai_buildout") -> str:
    """Weekly job: vendored universe + live financial overlay. Zero HTTP."""
    today = datetime.now(timezone.utc).date()
    doc_key = f"{universe_id}_graph"
    try:
        universe = load_universe(universe_id)
    except Exception as exc:  # noqa: BLE001 — missing file degrades, no crash
        log.warning("universe_graph: vendored universe %s missing: %s",
                    universe_id, exc)
        store.put_doc(doc_key, {"as_of": today.isoformat(), "verticals": [],
                                "edges": [], "error": "universe file missing"},
                      source=SOURCE)
        return f"universe_graph({universe_id}): universe file missing"
    capex_doc = None
    try:
        doc = store.doc(f"{universe_id}_capex")
        capex_doc = doc.payload if doc else None
    except Exception:  # noqa: BLE001
        capex_doc = None
    universe = _overlay_financials(universe, capex_doc)
    universe["rollups"] = _rollups(universe)
    universe["as_of"] = today.isoformat()
    universe["capex_overlay"] = ("live" if capex_doc
                                 else "pending (capex job not run yet)")
    # capex tails for the stacked chart: aggregate series + per-member tails
    try:
        capex_doc_p = capex_doc or {}
        aggs = capex_doc_p.get("aggregates", {})
        stack: dict[str, list] = {}
        for agg in universe.get("aggregates", []):
            aid = agg["id"]
            try:
                pts = store.points(f"{universe_id}:{aid}:{agg['metric']}")
                stack[f"__agg__{aid}"] = [[d.isoformat(), v]
                                          for d, v in sorted(pts.items())[-8:]]
            except Exception:  # noqa: BLE001
                pass
            for cid in agg.get("members", []):
                tick = next((t for t, c in (capex_doc_p.get("companies") or {}).items()
                             if c.get("id") == cid), None)
                if not tick:
                    continue
                try:
                    pts = store.points(
                        f"{universe_id}:{tick}:{agg['metric']}")
                    stack[tick] = [[d.isoformat(), v]
                                   for d, v in sorted(pts.items())[-8:]]
                except Exception:  # noqa: BLE001
                    pass
        universe["capex_stack"] = stack
        universe["aggregates_live"] = {
            a["id"]: (aggs.get(a["id"]) or {}).get("series", [])
            for a in universe.get("aggregates", [])}
    except Exception:  # noqa: BLE001
        universe["capex_stack"] = {}
        universe["aggregates_live"] = {}
    store.put_doc(doc_key, universe, source=SOURCE)
    return (f"universe_graph({universe_id}): "
            f"{universe['rollups']['node_count']} nodes, "
            f"{universe['rollups']['edge_count']} edges, "
            f"overlay={universe['capex_overlay']}")
