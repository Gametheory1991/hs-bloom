"""SEC EDGAR 13F-HR holdings for a configurable watchlist (keyless).

Flow per CIK (verified live 2026-10-03):
  1. https://data.sec.gov/submissions/CIK<cik-10-digit>.json
     -> latest 13F-HR filings: accessionNumber + filingDate
  2. https://www.sec.gov/Archives/edgar/data/<cik-no-pad>/<accession-no-dashes>/index.json
     -> holdings XML (the .xml that is not primary_doc.xml)
  3. parse infoTable (namespace
     http://www.sec.gov/edgar/document/thirteenf/informationtable)
     -> top 15 holdings by value (values in $000 -> USD), stored as a JSON doc

Change detection: the TWO most recent 13F-HR filings are diffed (top-15
holdings). Net flow per filer is estimated as the sum of position deltas
(new = +value, closed = -value) and upserted as quarterly points at
cycle:fl-<slug>; the watchlist aggregate lands at cycle:fl-all. The full
diff is stored as a JSON doc at thirteenf:<cik10>:changes.

CAVEAT: the diff runs on top-15 lists, so a position drifting out of the
top 15 reads as "closed" (and back in as "new") even if still held.

MANDATORY fair-access rules: a descriptive, non-browser User-Agent (SEC 403s
the product/(+url) style — verified), <=10 req/sec. Set `user_agent` in
config to a real contact; we sleep 0.5s between calls. Filings are quarterly,
filed up to 45 days after quarter-end.
"""
from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from datetime import date

from collector.config import ThirteenFCfg
from collector.fetchers.edgar_filing_cache import get_filing_xml
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

NS = {"x": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
TOP_N = 15
PAUSE = 0.5  # seconds between EDGAR calls; well under the 10 req/sec limit


def _cell(table: ET.Element, tag: str) -> str:
    el = table.find(f"x:{tag}", NS)
    return el.text.strip() if el is not None and el.text else ""


def _slug(name: str) -> str:
    """Watchlist name -> series slug, e.g. 'Berkshire Hathaway' -> 'berkshire'."""
    return name.split()[0].lower()


def parse_holdings_xml(text: str) -> list[dict]:
    """Parse a 13F-HR information table into top-N holdings by value.

    Duplicate issuers (multiple share classes / CUSIPs) are aggregated by
    issuer name, keeping the CUSIP of the largest row.
    """
    root = ET.fromstring(text)
    agg: dict[str, dict] = {}
    for table in root.findall(".//x:infoTable", NS):
        try:
            value_usd = int(_cell(table, "value")) * 1000  # $000 -> USD
        except ValueError:
            continue
        issuer = _cell(table, "nameOfIssuer")
        key = issuer or _cell(table, "cusip")
        if not key:
            continue
        holding = {
            "issuer": issuer,
            "cusip": _cell(table, "cusip"),
            "value_usd": value_usd,
            "shares": _cell(table, "sshPrnamt"),
            "share_class": _cell(table, "titleOfClass"),
        }
        if key in agg:
            agg[key]["value_usd"] += value_usd
            if value_usd > agg[key]["_row_value"]:
                agg[key]["cusip"] = holding["cusip"]
                agg[key]["shares"] = holding["shares"]
                agg[key]["_row_value"] = value_usd
        else:
            holding["_row_value"] = value_usd
            agg[key] = holding
    ranked = sorted(agg.values(), key=lambda h: -h["value_usd"])
    top = []
    for h in ranked[:TOP_N]:
        h = dict(h)
        del h["_row_value"]
        top.append(h)
    return top


def diff_holdings(prev: list[dict], curr: list[dict]) -> dict:
    """Diff two aggregated holdings lists; values in USD.

    Returns net_flow_usd (new = +value, closed = -value, else the delta),
    per-kind counts, and the top 15 changes by |delta|.
    """
    prev_m = {(h["issuer"] or h["cusip"]): h for h in prev}
    curr_m = {(h["issuer"] or h["cusip"]): h for h in curr}
    changes: list[dict] = []
    net_flow = 0
    for key, h in curr_m.items():
        if key not in prev_m:
            changes.append({
                "issuer": h["issuer"], "cusip": h["cusip"], "kind": "new",
                "delta_usd": h["value_usd"], "value_usd": h["value_usd"],
            })
            net_flow += h["value_usd"]
        else:
            delta = h["value_usd"] - prev_m[key]["value_usd"]
            if delta:
                changes.append({
                    "issuer": h["issuer"], "cusip": h["cusip"],
                    "kind": "increased" if delta > 0 else "decreased",
                    "delta_usd": delta, "value_usd": h["value_usd"],
                })
                net_flow += delta
    for key, h in prev_m.items():
        if key not in curr_m:
            changes.append({
                "issuer": h["issuer"], "cusip": h["cusip"], "kind": "closed",
                "delta_usd": -h["value_usd"], "value_usd": 0,
            })
            net_flow -= h["value_usd"]
    changes.sort(key=lambda c: -abs(c["delta_usd"]))
    kinds = [c["kind"] for c in changes]
    return {
        "net_flow_usd": net_flow,
        "n_new": kinds.count("new"),
        "n_closed": kinds.count("closed"),
        "n_increased": kinds.count("increased"),
        "n_decreased": kinds.count("decreased"),
        "changes": changes[:TOP_N],
    }


async def fetch_filer_filing(
    cik: str, name: str, user_agent: str, get_text: GetText, filing_idx: int = 0
) -> dict:
    """The filing_idx-th most recent 13F-HR filing for one filer (0 = latest)."""
    headers = {"User-Agent": user_agent}
    cik10 = cik.zfill(10)
    subs = json.loads(
        await get_text(f"https://data.sec.gov/submissions/CIK{cik10}.json", headers=headers)
    )
    filings = subs["filings"]["recent"]
    hr = [i for i, form in enumerate(filings["form"]) if form == "13F-HR"]
    if filing_idx >= len(hr):
        raise ValueError(f"only {len(hr)} 13F-HR filings found for {name}")
    idx = hr[filing_idx]
    accession = filings["accessionNumber"][idx]
    filing_date = filings["filingDate"][idx]
    await asyncio.sleep(PAUSE)
    # Shared cache with edgar_13f_holders.py: the same 13F-HR filing XML is
    # downloaded once per accession; the second fetcher reuses it.
    holdings_xml = await get_filing_xml(cik10, accession, get_text, headers)
    await asyncio.sleep(PAUSE)
    return {
        "name": name,
        "cik": cik10,
        "filing_date": filing_date,
        "accession": accession,
        "holdings": parse_holdings_xml(holdings_xml),
    }


async def fetch_filer(
    cik: str, name: str, user_agent: str, get_text: GetText
) -> dict:
    """Full 13F-HR pull for one filer (latest filing); returns the doc payload."""
    return await fetch_filer_filing(cik, name, user_agent, get_text, 0)


async def fetch_thirteenf(cfg: ThirteenFCfg, store: Store, get_text: GetText) -> str:
    """Weekly job (quarterly filings): top-15 holdings per watchlist filer,
    stored as JSON docs at thirteenf:<cik10>:top15; quarter-over-quarter
    diffs at thirteenf:<cik10>:changes; net-flow points at cycle:fl-<slug>
    plus the watchlist aggregate at cycle:fl-all."""
    errors: list[str] = []
    agg_flows: list[tuple[date, int]] = []
    for w in cfg.watchlist:
        cik10 = w.cik.zfill(10)
        try:
            payload = await fetch_filer_filing(w.cik, w.name, cfg.user_agent, get_text, 0)
            store.put_doc(f"thirteenf:{cik10}:top15", payload, "edgar")
        except Exception as exc:  # noqa: BLE001 — per-filer isolation
            errors.append(f"{w.name}: {exc}")
            continue
        try:
            prev = await fetch_filer_filing(w.cik, w.name, cfg.user_agent, get_text, 1)
            diff = diff_holdings(prev["holdings"], payload["holdings"])
            slug = _slug(w.name)
            filing_date = date.fromisoformat(payload["filing_date"])
            store.put_doc(
                f"thirteenf:{cik10}:changes",
                {
                    "name": w.name,
                    "cik": cik10,
                    "filing_date": payload["filing_date"],
                    "prev_filing_date": prev["filing_date"],
                    "note": "Diff of top-15 holdings; positions outside the top 15 are not tracked.",
                    **diff,
                },
                "edgar",
            )
            store.upsert_points(f"cycle:fl-{slug}", [(filing_date, float(diff["net_flow_usd"]))])
            agg_flows.append((filing_date, diff["net_flow_usd"]))
        except Exception as exc:  # noqa: BLE001 — a missing prior filing skips changes only
            log.warning("13F changes skipped for %s: %s", w.name, exc)
    if agg_flows:
        # Filers report on different dates; the aggregate is dated at the
        # latest filing and sums each filer's most recent quarterly net flow.
        asof = max(d for d, _ in agg_flows)
        store.upsert_points("cycle:fl-all", [(asof, float(sum(v for _, v in agg_flows)))])
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(cfg.watchlist)} 13F filers failed: {'; '.join(errors)}"
        )
    return "thirteenf"
