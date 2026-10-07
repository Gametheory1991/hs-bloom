"""Private Credit universe graph builder (daily).

Loads the vendored data/private_credit.json (BDCs + managers + ETFs +
curated bonds + manages/issued edges) and enriches every node:
  * Yahoo Finance chart API (keyless): last price, 1Y price history
    (also backfilled to cycle:bdc-{T}-price), TTM dividends -> yield.
  * SEC XBRL fundamentals docs (bdc:{T}:fundamentals, written by the
    weekly bdc_financials job): NAV/share -> premium/discount, latest
    NII / coverage / debt / NAV, shares outstanding -> market cap.

Writes the universe-schema doc private_credit_graph:
  verticals -> companies (enriched), edges, aggregates, capex_stack
  (quarterly NII for the top-8 BDCs by market cap, feeds the shared
  stacked chart), risk_notes.

Honest labels: prices are live/daily; fundamentals carry ~45-day XBRL
filing lag; non-traded BDCs have no free data source.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "bdc-universe-daily"
YH_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
          "?interval=1d&range=2y&events=div")
PAUSE = 0.6  # Yahoo pacing

FIN_METRICS = ("assets", "inv_fv", "debt", "nav", "navps", "tii", "nii",
               "dist", "ocf", "fcf", "coverage", "debt_equity")


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def parse_yahoo(text: str, today: date) -> dict:
    """{price, closes, ttm_div} from a chart+div payload."""
    chart = json.loads(text)["chart"]
    results = chart.get("result")
    if not results:
        raise ValueError("yahoo: no result")
    r = results[0]
    ts = r.get("timestamp") or []
    closes_raw = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    closes = []
    for t, c in zip(ts, closes_raw):
        if c is None:
            continue
        closes.append((datetime.fromtimestamp(t, tz=timezone.utc).date(),
                       float(c)))
    closes.sort()
    price = _f(r.get("meta", {}).get("regularMarketPrice")) or (
        closes[-1][1] if closes else None)
    cutoff = today - timedelta(days=365)
    ttm = 0.0
    for tss, dv in (r.get("events", {}).get("dividends") or {}).items():
        d = datetime.fromtimestamp(int(tss), tz=timezone.utc).date()
        if d >= cutoff:
            ttm += _f(dv.get("amount")) or 0.0
    return {"price": price, "closes": closes, "ttm_div": ttm}


def _load_universe() -> dict:
    raw = (files("collector") / "data" / "private_credit.json").read_text(
        encoding="utf-8")
    return json.loads(raw)


def _last_n(pts: dict, n: int):
    items = pts.items() if isinstance(pts, dict) else pts
    return [(d.isoformat() if hasattr(d, "isoformat") else d, v)
            for d, v in sorted(items, key=lambda p: p[0])[-n:]]


async def fetch_bdc_universe(store: Store, get_text: GetText) -> str:
    """Enrich the private-credit universe and write private_credit_graph."""
    from collector.fetchers.yahoo import _pace_yahoo  # reuse rate pacing
    u = _load_universe()
    today = datetime.now(timezone.utc).date()
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

    by_id: dict[str, dict] = {}
    for v in u.get("verticals", []):
        for c in v.get("companies", []):
            by_id[c["id"]] = c

    bdc_ids = [c["id"] for v in u["verticals"] if v["id"] == "listed_bdcs"
               for c in v["companies"]]
    etf_ids = [c["id"] for v in u["verticals"] if v["id"] == "pc_etfs"
               for c in v["companies"]]

    enriched = failed = 0
    for cid in bdc_ids + etf_ids:
        node = by_id[cid]
        t = node.get("ticker")
        if not t:
            continue
        try:
            await _pace_yahoo()
            q = parse_yahoo(await get_text(YH_URL.format(sym=t),
                                          headers=headers), today)
        except Exception as exc:  # noqa: BLE001 — per-ticker isolation
            log.warning("bdc_universe: Yahoo %s failed: %s", t, exc)
            failed += 1
            continue
        price = q["price"]
        closes = q["closes"]
        # backfill 2Y price history (idempotent upserts)
        if closes and cid in bdc_ids:
            store.upsert_points(f"cycle:bdc-{t}-price", closes)
        ret_1y = None
        if closes and len(closes) > 5:
            base = closes[0][1]
            ret_1y = (price / base - 1) if base and price else None

        fin = {"bdc": True, "price": price, "ret_1y": ret_1y,
               "ttm_div": q["ttm_div"] or None}
        if price and q["ttm_div"]:
            fin["div_yield"] = q["ttm_div"] / price

        if cid in bdc_ids:
            doc = store.doc(f"bdc:{t}:fundamentals")
            p = (doc.payload if doc else None) or {}
            latest = p.get("latest") or {}
            shares = (latest.get("shares") or {}).get("value")
            navps = (latest.get("navps") or {}).get("value")
            if price and shares:
                fin["mktcap"] = price * shares
            if price and navps:
                fin["navps"] = navps
                fin["prem_disc"] = price / navps - 1
                fin["nav_asof"] = (latest.get("navps") or {}).get("asof")
            for m in ("nii", "coverage", "debt", "nav", "debt_equity"):
                v = (latest.get(m) or {}).get("value")
                if v is not None:
                    fin[m] = v
            # last-8-quarter history for the detail view
            hist = {}
            for m in FIN_METRICS:
                pts = store.points(f"cycle:bdc-{t}-{m}")
                if pts:
                    hist[m] = _last_n(pts, 8)
            if hist:
                fin["fin_hist"] = hist
                qs = sorted({d for m in hist.values() for d, _ in m})
                fin["fin_quarters"] = qs[-8:]
            fin["coverage_estimated"] = bool(p.get("coverage_estimated"))
            fin["xbrl_asof"] = p.get("asof")
        node["financials"] = fin
        node["flags"] = []
        enriched += 1

    # aggregates
    bdc_nodes = [by_id[i] for i in bdc_ids]
    mcaps = [(n["financials"].get("mktcap") or 0) for n in bdc_nodes
             if n.get("financials")]
    yields = [(n["financials"].get("div_yield"), n["financials"].get("mktcap") or 0)
              for n in bdc_nodes if n.get("financials", {}).get("div_yield")]
    pds = [n["financials"].get("prem_disc") for n in bdc_nodes
           if n.get("financials", {}).get("prem_disc") is not None]
    covs = [n["financials"].get("coverage") for n in bdc_nodes
            if n.get("financials", {}).get("coverage") is not None]
    tot_y = sum(y * w for y, w in yields)
    tot_w = sum(w for _, w in yields)
    aggregates = {
        "n_bdcs": len(bdc_ids),
        "n_etfs": len(etf_ids),
        "n_bonds": sum(1 for v in u["verticals"] if v["id"] == "bdc_bonds"
                       for _ in v["companies"]),
        "total_mktcap": round(sum(mcaps)),
        "wavg_div_yield": round(tot_y / tot_w, 4) if tot_w else None,
        "median_prem_disc": round(sorted(pds)[len(pds) // 2], 4) if pds else None,
        "median_coverage": round(sorted(covs)[len(covs) // 2], 2) if covs else None,
    }

    # stacked NII chart: top-8 BDCs by market cap
    ranked = sorted(
        [n for n in bdc_nodes
         if n.get("financials", {}).get("mktcap")],
        key=lambda n: n["financials"]["mktcap"], reverse=True)[:8]
    capex_stack = {}
    for n in ranked:
        t = n["ticker"]
        pts = store.points(f"cycle:bdc-{t}-nii")
        if pts:
            capex_stack[t] = _last_n(pts, 8)

    store.put_doc("private_credit_graph", {
        "universe_id": "private_credit",
        "title": u.get("title"),
        "as_of": today.isoformat(),
        "verticals": u["verticals"],
        "edges": u.get("edges", []),
        "rollups": {},
        "capex_stack": capex_stack,
        "risk_notes": u.get("risk_notes", []),
        "aggregates": aggregates,
        "_schema_note": u.get("_schema_note"),
        "source": SOURCE,
    }, SOURCE)
    return (f"bdc_universe: {enriched} nodes enriched, {failed} Yahoo "
            f"failures; total BDC mktcap ${aggregates['total_mktcap']/1e9:.1f}B")
