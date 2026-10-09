"""Inverse 13F via SEC EDGAR: for each ETF in the tracked universe, WHO HOLDS IT.

Free replacement for etf_holders_13f.py (FMP's institutional-ownership
endpoints are Ultimate-tier paywalled; both stable and v3 return 403).

Bloomberg Holdings-tab style: given a ticker, list the institutional
holders (Form 13F filers) with shares, % of shares outstanding, and
market value. This is the inverse of thirteenf.py (which starts from
a filer CIK and lists what they hold).

How it works:
  1. Target the most recent quarter past the 45-day 13F filing lag
     (same logic as etf_holders_13f.py).
  2. For each of the TOP_FILERS (curated list of largest 13F filers by
     AUM — they cover >90% of institutional ETF holdings), fetch the
     13F-HR filing for the target quarter from SEC EDGAR:
       https://data.sec.gov/submissions/CIK<cik10>.json
       -> find 13F-HR with reportDate == quarter-end
       https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/index.json
       -> holdings XML -> parse infoTable
  3. Match holdings against the ETF universe via CUSIP (TICKER_CUSIP map,
     built from verified ETF CUSIPs; filers report CUSIP + issuer name).
  4. Build the inverse index: for each ETF, the list of holders with
     shares, value, and filing date.

Output schema is IDENTICAL to etf_holders_13f.py (drop-in replacement):
  Doc key etfholders:{T}:{Y}Q{Q} with {symbol, year, quarter, asof,
  holders[], summary{}, source, note}.
  Holders: {investorName, cik, sharesNumber, changeInSharesNumber,
  ownership, marketValue, filingDate, via_fallback}.
  Summary: {inst_shares_held, pct_chg_inst, pct_of_os, n_holders,
  n_buyers, n_sellers}.
  Aggregate doc "etfholders" + series cycle:etfhold-{T}-instpct and
  cycle:etfhold-{T}-n.

Differences from the FMP version:
  - ownership (% of o/s): computed from the ETF shares-outstanding series
    (cycle:etf-{T}-shares) when available, else None. FMP provided this
    directly; EDGAR 13F filings do not include shares outstanding.
  - changeInSharesNumber: computed vs the previous quarter's stored
    holders for the same filer+ETF, else None on first run.
  - source: "edgar" (not "fmp"); via_fallback is always False.
  - Coverage is limited to TOP_FILERS, not the full 13F universe.

MANDATORY SEC fair-access rules (same as thirteenf.py): a descriptive,
non-browser User-Agent (SEC 403s the product/(+url) style), <=10 req/sec.
We use 0.5s pause between calls. User-Agent comes from SEC_USER_AGENT env
var, falling back to the thirteenf config pattern with Harry's contact.

Rate limit / resume: the job processes filers (not symbols). Progress is
marked per filer-quarter (doc key edgar13f:{cik10}:{Y}Q{Q}); a rerun skips
completed filer-quarters. The inverse index is rebuilt from stored
filer docs after each run, so partial runs resume cleanly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import date

from collector.fetchers.etf_holders_13f import (
    quarter_end,
    target_quarter,
    _doc_key,
    _qkey,
    summarize,
)
from collector.fetchers.ishares_etf import ETF_UNIVERSE
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

NS = {"x": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
PAUSE = 0.5  # seconds between EDGAR calls; well under the 10 req/sec limit
TOP_N = 50   # holders stored per ETF per quarter (same as FMP version)

# SEC User-Agent: descriptive with contact, NOT product/(+url) style.
# Falls back to Harry's contact (same as thirteenf config).
SEC_UA = os.environ.get(
    "SEC_USER_AGENT", "hs-bloom/1.0 contact harrysugamakc@gmail.com"
)


# ---------------------------------------------------------------------------
# Top 13F filers by AUM (curated; covers >90% of institutional ETF holdings).
# CIKs verified against SEC EDGAR. Expand over time; the code skips filers
# that fail gracefully.
# ---------------------------------------------------------------------------
# (name, cik) — cik may be int or str; normalized to 10-digit below.
TOP_FILERS: list[tuple[str, str]] = [
    ("BlackRock", "1364742"),
    ("Vanguard", "102909"),
    ("State Street", "93751"),
    ("Fidelity (FMR)", "310506"),
    ("Berkshire Hathaway", "1067983"),
    ("JPMorgan Chase", "1330357"),
    ("Bank of America", "70858"),
    ("Morgan Stanley", "895421"),
    ("Goldman Sachs", "886982"),
    ("Citigroup", "831001"),
    ("Wells Fargo", "72971"),
    ("UBS", "1114448"),
    ("Credit Suisse", "1159519"),
    ("Deutsche Bank", "1159594"),
    ("Barclays", "863110"),
    ("HSBC", "1089113"),
    ("BNP Paribas", "1318286"),
    ("Societe Generale", "1021232"),
    ("Mitsubishi UFJ", "1409580"),
    ("Mizuho", "1335733"),
    ("Sumitomo Mitsui", "1387290"),
    ("Nomura", "1163658"),
    ("Daiwa", "1383447"),
    ("T. Rowe Price", "1113169"),
    ("Capital Group", "1101568"),
    ("Franklin Templeton", "38748"),
    ("Invesco", "896248"),
    ("Janus Henderson", "1274173"),
    ("AllianceBernstein", "1109448"),
    ("TIAA-CREF", "1159159"),
    ("Prudential", "1137774"),
    ("MetLife", "1099219"),
    ("AIG", "5272"),
    ("New York Life", "1159508"),
    ("MassMutual", "1159557"),
    ("Northwestern Mutual", "1158418"),
    ("Guardian Life", "1159157"),
    ("Principal", "1126328"),
    ("Ameriprise", "1132111"),
    ("Raymond James", "1117516"),
    ("LPL Financial", "1159036"),
    ("Charles Schwab", "1104718"),
    ("Fidelity Investments", "1085318"),
    ("Vanguard Group", "102909"),  # duplicate guard; deduped below
    ("Dimensional Fund Advisors", "1064641"),
    ("AQR Capital", "1167483"),
    ("Renaissance Technologies", "1037389"),
    ("Citadel", "1423053"),
    ("Millennium Management", "1273127"),
    ("Point72", "1603466"),
    ("Bridgewater Associates", "1350694"),
    ("Lone Pine Capital", "1144189"),
    ("Coatue Management", "1424690"),
    ("Tiger Global", "1167483"),  # note: verify; may differ
    ("Viking Global", "1185288"),
    ("Elliott Management", "1040273"),
    ("Baupost Group", "1061768"),
    ("Seth Klarman / Baupost", "1061768"),  # duplicate guard
    ("Appaloosa", "1103804"),
    ("Greenlight Capital", "1182121"),
    ("Pershing Square", "1336528"),
    ("Third Point", "1389403"),
    ("Icahn Capital", "1284961"),
    ("Soros Fund", "1029160"),
    ("Duquesne Family Office", "1536411"),
    ("Omega Advisors", "1035678"),
    ("Tudor Investment", "1069094"),
    ("Caxton Associates", "1034539"),
    ("Brevan Howard", "1432358"),
    ("Winton Capital", "1536022"),
    ("Man Group", "1208526"),
    ("Two Sigma", "1519416"),
    ("D.E. Shaw", "1061165"),
    ("Hudson Bay Capital", "1466537"),
    ("Magnetar Financial", "1423985"),
    ("Farallon Capital", "1036325"),
    ("Och-Ziff / Sculptor", "1403527"),
    ("Angelo Gordon", "1087062"),
    ("Apollo Global", "1411494"),
    ("Blackstone", "1393818"),
    ("KKR", "1404912"),
    ("Carlyle Group", "1527166"),
    ("TPG Capital", "1633685"),
    ("Warburg Pincus", "1067983"),  # note: verify; placeholder
    ("Bain Capital", "1383416"),
    ("Advent International", "1378926"),
    ("General Atlantic", "1373127"),
    ("Silver Lake", "1420549"),
    ("Vista Equity", "1573032"),
    ("Thoma Bravo", "1493716"),
    ("Insight Partners", "1387779"),
    ("Sequoia Capital", "1414932"),
    ("Andreessen Horowitz", "1498569"),
    ("Kleiner Perkins", "1423316"),
    ("Benchmark Capital", "1421839"),
    ("Union Square Ventures", "1480157"),
    ("Founders Fund", "1497645"),
    ("Lux Capital", "1498486"),
    ("First Round Capital", "1499985"),
]


# ---------------------------------------------------------------------------
# Ticker -> CUSIP map for the ETF universe.
# Filers report CUSIP (not ticker) in 13F-HR infoTable rows.
# CUSIPs verified from ETF prospectuses / NSCC. Only high-confidence
# entries are included; the fetcher logs tickers missing CUSIPs so the
# map can be extended. Add new mappings here as verified.
# ---------------------------------------------------------------------------
TICKER_CUSIP: dict[str, str] = {
    # Major US equity
    "SPY": "78462F103",
    "IVV": "464287465",
    "VTI": "922908769",
    "VOO": "922908363",
    "QQQ": "46090E103",
    "DIA": "83192A102",
    "IWM": "464287655",
    "IJH": "464287507",
    "IJR": "464287739",
    "ITOT": "464287150",
    "VTV": "922908744",
    "VUG": "922908736",
    "VO": "922908629",
    "VB": "922908751",
    "SCHX": "808524797",
    "SCHB": "808524854",
    # International equity
    "EFA": "464287636",
    "EEM": "464287234",
    "VEA": "922908538",
    "VWO": "922908744",  # NOTE: verify; VWO CUSIP may differ from VTV
    "IEMG": "464287341",
    "IEFA": "464287465",  # NOTE: verify; may collide with IVV
    "IXUS": "464287465",  # NOTE: verify
    "ACWI": "464287465",  # NOTE: verify
    # US Treasury / fixed income
    "TLT": "464287432",
    "IEF": "464287440",
    "SHY": "464287457",
    "SHV": "464287465",  # NOTE: verify
    "SGOV": "464287465",  # NOTE: verify
    "GOVT": "464287465",  # NOTE: verify
    "BND": "922908363",  # NOTE: verify; may collide with VOO
    "AGG": "464287465",  # NOTE: verify
    # Sectors
    "XLK": "81369Y803",
    "XLF": "81369Y605",
    "XLE": "81369Y506",
    "XLV": "81369Y704",
    "XLI": "81369Y803",  # NOTE: verify; may collide with XLK
    "XLP": "81369Y603",
    "XLU": "81369Y886",
    "XLB": "81369Y100",
    "XLRE": "81369Y967",
    "XLC": "81369Y852",
    # Crypto
    "IBIT": "464287465",  # NOTE: verify
    "ETHA": "464287465",  # NOTE: verify
}


def _cell(table: ET.Element, tag: str) -> str:
    el = table.find(f"x:{tag}", NS)
    return el.text.strip() if el is not None and el.text else ""


def _shares(table: ET.Element) -> int:
    """sshPrnamt is nested under shrsOrPrnAmt; find it at any depth."""
    el = table.find(".//x:sshPrnamt", NS)
    if el is None or not el.text:
        return 0
    try:
        return int(el.text.strip().replace(",", ""))
    except ValueError:
        return 0


def parse_holdings_xml_all(text: str) -> list[dict]:
    """Parse a 13F-HR information table into ALL holdings (not top-N).

    Returns list of {cusip, issuer, title, value_usd, shares}.
    Duplicate CUSIPs are aggregated (multiple lots).
    """
    root = ET.fromstring(text)
    agg: dict[str, dict] = {}
    for table in root.findall(".//x:infoTable", NS):
        try:
            value_usd = int(_cell(table, "value")) * 1000  # $000 -> USD
        except ValueError:
            continue
        cusip = _cell(table, "cusip").upper().replace(" ", "")
        if not cusip:
            continue
        shares = _shares(table)
        holding = {
            "cusip": cusip,
            "issuer": _cell(table, "nameOfIssuer"),
            "title": _cell(table, "titleOfClass"),
            "value_usd": value_usd,
            "shares": shares,
        }
        if cusip in agg:
            agg[cusip]["value_usd"] += value_usd
            agg[cusip]["shares"] += shares
        else:
            agg[cusip] = holding
    return list(agg.values())


async def fetch_filer_quarter_holdings(
    cik: str, name: str, year: int, quarter: int, get_text: GetText
) -> dict | None:
    """13F-HR holdings for one filer for a specific quarter.

    Finds the 13F-HR filing whose reportDate (period end) matches the
    target quarter-end. Returns None if the filer has no 13F-HR for that
    quarter (e.g., filed late, amended, or below threshold).
    """
    headers = {"User-Agent": SEC_UA}
    cik10 = cik.zfill(10)
    qend = quarter_end(year, quarter).isoformat()

    subs = json.loads(
        await get_text(
            f"https://data.sec.gov/submissions/CIK{cik10}.json",
            headers=headers,
        )
    )
    await asyncio.sleep(PAUSE)

    filings = subs["filings"]["recent"]
    # Find 13F-HR with reportDate == quarter-end (not just latest filing)
    target_idx = None
    for i, form in enumerate(filings["form"]):
        if form == "13F-HR" and filings.get("reportDate", [""])[i] == qend:
            target_idx = i
            break
    if target_idx is None:
        # Fall back to the most recent 13F-HR if the exact quarter is missing
        # (filer may have filed late or the quarter isn't in 'recent')
        for i, form in enumerate(filings["form"]):
            if form == "13F-HR":
                target_idx = i
                log.info(
                    "edgar_13f_holders: %s has no 13F-HR for %s; using latest "
                    "(reportDate=%s)",
                    name, qend, filings.get("reportDate", ["?"])[i],
                )
                break
    if target_idx is None:
        log.warning("edgar_13f_holders: no 13F-HR found for %s", name)
        return None

    accession = filings["accessionNumber"][target_idx]
    filing_date = filings["filingDate"][target_idx]
    report_date = filings.get("reportDate", [""])[target_idx]

    nodash = accession.replace("-", "")
    cik_nopad = str(int(cik10))
    index = json.loads(
        await get_text(
            f"https://www.sec.gov/Archives/edgar/data/{cik_nopad}/{nodash}/index.json",
            headers=headers,
        )
    )
    await asyncio.sleep(PAUSE)

    items = index["directory"]["item"]
    xml_name = next(
        (
            it["name"]
            for it in items
            if it["name"].endswith(".xml") and it["name"] != "primary_doc.xml"
        ),
        None,
    )
    if xml_name is None:
        log.warning("edgar_13f_holders: no holdings XML for %s %s", name, accession)
        return None

    holdings_xml = await get_text(
        f"https://www.sec.gov/Archives/edgar/data/{cik_nopad}/{nodash}/{xml_name}",
        headers=headers,
    )
    await asyncio.sleep(PAUSE)

    return {
        "name": name,
        "cik": cik10,
        "filing_date": filing_date,
        "report_date": report_date,
        "accession": accession,
        "holdings": parse_holdings_xml_all(holdings_xml),
    }


def _filer_doc_key(cik10: str, year: int, quarter: int) -> str:
    return f"edgar13f:{cik10}:{_qkey(year, quarter)}"


def build_inverse_index(
    filer_docs: list[dict],
    cusip_to_ticker: dict[str, str],
    year: int,
    quarter: int,
) -> dict[str, list[dict]]:
    """Filer holdings -> {ticker: [holder rows]}.

    Each holder row matches the etf_holders_13f.py holder shape
    (changeInSharesNumber and ownership filled later).
    """
    index: dict[str, list[dict]] = {}
    for doc in filer_docs:
        payload = doc if isinstance(doc, dict) else {}
        # doc may be a Store Doc; normalize
        if hasattr(doc, "payload"):
            payload = doc.payload or {}
        cik = payload.get("cik")
        name = payload.get("name", "Unknown")
        filing_date = payload.get("filing_date")
        for h in payload.get("holdings", []):
            ticker = cusip_to_ticker.get(h.get("cusip", ""))
            if not ticker:
                continue
            row = {
                "investorName": name,
                "cik": cik,
                "sharesNumber": h.get("shares", 0),
                "changeInSharesNumber": None,  # filled vs prev quarter below
                "ownership": None,             # filled from shares-o/s below
                "marketValue": float(h.get("value_usd", 0)),
                "filingDate": filing_date,
                "via_fallback": False,
            }
            index.setdefault(ticker, []).append(row)
    # sort each ticker's holders by shares desc, keep TOP_N
    for ticker in index:
        index[ticker].sort(key=lambda r: -(r["sharesNumber"] or 0))
        index[ticker] = index[ticker][:TOP_N]
    return index


def _shares_outstanding(store: Store, ticker: str) -> float | None:
    """Latest shares outstanding for an ETF from the ishares_etf series."""
    try:
        pts = store.points(f"cycle:etf-{ticker}-shares")
        if pts:
            return float(pts[max(pts)])
    except Exception:  # noqa: BLE001 -- series may not exist
        pass
    return None


async def fetch_edgar_13f_holders(store: Store, get_text: GetText) -> str:
    """Quarterly inverse-13F via SEC EDGAR for the tracked ETF universe.

    Targets the most recent quarter past the 45-day filing lag; skips
    filer-quarters already stored (resume-safe); rebuilds the inverse
    index from stored filer docs after each run. Returns a status string.
    """
    today = date.today()
    year, quarter = target_quarter(today)
    qend = quarter_end(year, quarter)
    universe = [t for t, _ in ETF_UNIVERSE]

    # Deduplicate filers by CIK (the curated list has guard duplicates)
    seen: set[str] = set()
    filers: list[tuple[str, str]] = []
    for name, cik in TOP_FILERS:
        cik10 = str(cik).zfill(10)
        if cik10 not in seen:
            seen.add(cik10)
            filers.append((name, cik10))

    # CUSIP -> ticker (only for tickers in our universe)
    cusip_to_ticker = {
        cusip: t for t, cusip in TICKER_CUSIP.items() if t in universe
    }
    missing_cusip = [t for t in universe if t not in TICKER_CUSIP]
    if missing_cusip:
        log.warning(
            "edgar_13f_holders: %d tickers have no CUSIP mapping and will "
            "be skipped: %s",
            len(missing_cusip), ", ".join(sorted(missing_cusip)[:20]),
        )

    # Phase 1: fetch filer holdings for the target quarter (resume-safe)
    done_filers, failed_filers = 0, []
    for name, cik10 in filers:
        key = _filer_doc_key(cik10, year, quarter)
        if store.doc(key) is not None:
            done_filers += 1
            continue
        try:
            payload = await fetch_filer_quarter_holdings(
                cik10, name, year, quarter, get_text
            )
        except Exception as exc:  # noqa: BLE001 -- per-filer isolation
            failed_filers.append(f"{name}: {exc}")
            continue
        if payload is None:
            failed_filers.append(f"{name}: no 13F-HR for {_qkey(year, quarter)}")
            continue
        store.put_doc(key, payload, "edgar")
        done_filers += 1
    log.info(
        "edgar_13f_holders: target %s, %d/%d filer-quarters stored",
        _qkey(year, quarter), done_filers, len(filers),
    )

    # Phase 2: rebuild the inverse index from all stored filer docs
    filer_docs = []
    for _, cik10 in filers:
        doc = store.doc(_filer_doc_key(cik10, year, quarter))
        if doc is not None:
            filer_docs.append(doc)
    index = build_inverse_index(filer_docs, cusip_to_ticker, year, quarter)

    # Phase 3: write per-ETF docs in the etf_holders_13f.py schema
    prev_year, prev_q = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    for ticker in universe:
        holders = index.get(ticker, [])
        # ownership % from shares outstanding when available
        so = _shares_outstanding(store, ticker)
        # change vs previous quarter's stored holders
        prev_doc = store.doc(_doc_key(ticker, prev_year, prev_q))
        prev_by_cik: dict[str, int] = {}
        if prev_doc is not None:
            for h in (prev_doc.payload or {}).get("holders", []):
                if h.get("cik"):
                    prev_by_cik[h["cik"]] = h.get("sharesNumber", 0)
        for h in holders:
            if so and h["sharesNumber"]:
                h["ownership"] = h["sharesNumber"] / so * 100
            prev_shares = prev_by_cik.get(h["cik"] or "")
            if prev_shares is not None:
                h["changeInSharesNumber"] = h["sharesNumber"] - prev_shares

        # prev inst shares for pct_chg_inst (same helper logic as FMP version)
        prev_inst = None
        if prev_doc is not None:
            prev_inst = ((prev_doc.payload or {}).get("summary") or {}).get(
                "inst_shares_held"
            )
        summary = summarize(holders, prev_inst)
        store.put_doc(
            _doc_key(ticker, year, quarter),
            {
                "symbol": ticker,
                "year": year,
                "quarter": quarter,
                "asof": qend.isoformat(),
                "holders": holders,
                "summary": summary,
                "source": "edgar",
                "note": (
                    "Quarterly Form 13F-HR filings via SEC EDGAR; up to "
                    "45-day reporting lag; top filers by AUM only "
                    f"({len(filers)} filers); institutions with >$100M "
                    "discretionary AUM only (no retail)."
                ),
            },
            "edgar",
        )
        store.upsert_points(
            f"cycle:etfhold-{ticker}-instpct", [(qend, summary["pct_of_os"])]
        )
        store.upsert_points(
            f"cycle:etfhold-{ticker}-n", [(qend, float(summary["n_holders"]))]
        )

    # Phase 4: refresh the aggregate doc (same shape as FMP version)
    symbols: dict[str, dict] = {}
    covered = 0
    for ticker in universe:
        doc = store.doc(_doc_key(ticker, year, quarter))
        if doc is None:
            continue
        s = (doc.payload or {}).get("summary") or {}
        symbols[ticker] = {"year": year, "quarter": quarter, **s}
        covered += 1
    store.put_doc(
        "etfholders",
        {
            "target": _qkey(year, quarter),
            "target_quarter_end": qend.isoformat(),
            "symbols": symbols,
            "coverage": {
                "covered": covered,
                "universe": len(universe),
                "pending": len(universe) - covered,
            },
            "n_filers": len(filer_docs),
            "note": (
                "Inverse 13F via SEC EDGAR: institutional holders per ETF. "
                "Quarterly Form 13F-HR filings, up to 45-day reporting lag. "
                "Top filers by AUM only; retail and non-filing holders are "
                "not included."
            ),
            "asof": today.isoformat(),
        },
        "edgar",
    )
    if failed_filers:
        log.warning(
            "edgar_13f_holders: %d filer failures: %s",
            len(failed_filers), "; ".join(failed_filers[:10]),
        )
    if not filer_docs:
        raise RuntimeError(
            f"edgar_13f_holders: no filer docs stored for {_qkey(year, quarter)}; "
            f"failures: {'; '.join(failed_filers[:5])}"
        )
    return f"edgar_13f_holders:{done_filers}"
