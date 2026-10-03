"""SEC EDGAR 13F-HR holdings for a configurable watchlist (keyless).

Flow per CIK (verified live 2026-10-03):
  1. https://data.sec.gov/submissions/CIK<cik-10-digit>.json
     -> latest 13F-HR filing: accessionNumber + filingDate
  2. https://www.sec.gov/Archives/edgar/data/<cik-no-pad>/<accession-no-dashes>/index.json
     -> holdings XML (the .xml that is not primary_doc.xml)
  3. parse infoTable (namespace
     http://www.sec.gov/edgar/document/thirteenf/informationtable)
     -> top 15 holdings by value (values in $000 -> USD), stored as a JSON doc

MANDATORY fair-access rules: a descriptive, non-browser User-Agent (SEC 403s
the product/(+url) style — verified), <=10 req/sec. Set `user_agent` in
config to a real contact; we sleep 0.5s between calls. Filings are quarterly,
filed up to 45 days after quarter-end.
"""
from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET

from collector.config import ThirteenFCfg
from collector.http import GetText
from collector.store import Store

NS = {"x": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
TOP_N = 15
PAUSE = 0.5  # seconds between EDGAR calls; well under the 10 req/sec limit


def _cell(table: ET.Element, tag: str) -> str:
    el = table.find(f"x:{tag}", NS)
    return el.text.strip() if el is not None and el.text else ""


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


async def fetch_filer(
    cik: str, name: str, user_agent: str, get_text: GetText
) -> dict:
    """Full 13F-HR pull for one filer; returns the doc payload."""
    headers = {"User-Agent": user_agent}
    cik10 = cik.zfill(10)
    subs = json.loads(
        await get_text(f"https://data.sec.gov/submissions/CIK{cik10}.json", headers=headers)
    )
    filings = subs["filings"]["recent"]
    idx = next(i for i, form in enumerate(filings["form"]) if form == "13F-HR")
    accession = filings["accessionNumber"][idx]
    filing_date = filings["filingDate"][idx]
    await asyncio.sleep(PAUSE)
    nodash = accession.replace("-", "")
    cik_nopad = str(int(cik10))  # EDGAR archive path uses the unpadded CIK
    index = json.loads(
        await get_text(
            f"https://www.sec.gov/Archives/edgar/data/{cik_nopad}/{nodash}/index.json",
            headers=headers,
        )
    )
    items = index["directory"]["item"]
    xml_name = next(
        it["name"]
        for it in items
        if it["name"].endswith(".xml") and it["name"] != "primary_doc.xml"
    )
    await asyncio.sleep(PAUSE)
    holdings_xml = await get_text(
        f"https://www.sec.gov/Archives/edgar/data/{cik_nopad}/{nodash}/{xml_name}",
        headers=headers,
    )
    await asyncio.sleep(PAUSE)
    return {
        "name": name,
        "cik": cik10,
        "filing_date": filing_date,
        "accession": accession,
        "holdings": parse_holdings_xml(holdings_xml),
    }


async def fetch_thirteenf(cfg: ThirteenFCfg, store: Store, get_text: GetText) -> str:
    """Weekly job (quarterly filings): top-15 holdings per watchlist filer,
    stored as JSON docs at thirteenf:<cik10>:top15."""
    errors: list[str] = []
    for w in cfg.watchlist:
        try:
            payload = await fetch_filer(w.cik, w.name, cfg.user_agent, get_text)
            store.put_doc(f"thirteenf:{w.cik.zfill(10)}:top15", payload, "edgar")
        except Exception as exc:  # noqa: BLE001 — per-filer isolation
            errors.append(f"{w.name}: {exc}")
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(cfg.watchlist)} 13F filers failed: {'; '.join(errors)}"
        )
    return "thirteenf"
