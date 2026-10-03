"""Hyperscaler desk: debt-issuance monitor for the six AI hyperscalers.

Weekly. Two halves:

1. ISSUANCE — scans each issuer's SEC submissions feed
   (https://data.sec.gov/submissions/CIK<cik>.json, free, keyless) for debt-
   offering filings in the last 90 days: forms 424B2 / 424B3 / 424B5 / FWP /
   S-3ASR. For each 424B2 the primary prospectus is fetched and parsed
   best-effort for per-tranche coupon, maturity, and principal amount.
   Parsed fields are labeled as extracted-from-filing-text; a filing with no
   parseable tranches is still recorded (date/form/link) so nothing is
   silently dropped. The EDGAR full-text search API (efts.sec.gov) 403s
   automated access, so the submissions-API scan is the live path.

   CIKs verified live 2026-10-03 via https://www.sec.gov/files/company_tickers.json.

2. EQUITIES — reads the six names' price history already in the store
   (cycle:googl etc., fetched by the cycle job's yahoo: single-symbol
   field) and builds 90d spark tails + last/1m change. Zero extra HTTP.

Writes doc "hyper": {as_of, issuances: [...], equities: [...], note}.

Honest limits (surfaced in the UI, not faked):
- single-name bond spreads / CDS: no free feed exists; sector proxies
  (HY/IG OAS) are shown instead and labeled as proxies.
- single-name short interest: S3 / S&P Global are paid; not shown.
- holder flows: covered quarterly by the 13F watchlist; not duplicated here.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from collector.config import Config
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "hyperscaler-weekly"
DOC_KEY = "hyper"
PAUSE = 0.5  # SEC fair access: well under 10 req/sec

# (label, ticker, CIK-10, cycle series id) — CIKs verified live 2026-10-03
ISSUERS = [
    ("Alphabet", "GOOGL", "0001652044", "googl"),
    ("Amazon", "AMZN", "0001018724", "amzn"),
    ("Meta", "META", "0001326801", "meta"),
    ("Oracle", "ORCL", "0001341439", "orcl"),
    ("Microsoft", "MSFT", "0000789019", "msft"),
    ("Apple", "AAPL", "0000320193", "aapl"),
]

DEBT_FORMS = {"424B2", "424B3", "424B5", "FWP", "S-3ASR"}
LOOKBACK_DAYS = 90
SPARK_DAYS = 90

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{}/{}"

# $1,250,000,000 aggregate principal amount of our 4.500% ...
_AMT = re.compile(
    r"\$\s*([\d,]+)\s*aggregate principal amount of our\s+(\d\.\d{2,3})%",
    re.IGNORECASE,
)
# 4.500% Senior Notes due 2028 / 6.500% Notes due 2066
_TRANCHE = re.compile(
    r"(\d\.\d{2,3})%\s*(?:[Ss]enior\s+)?[Nn]otes\s+due\s+(\d{4})"
)


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(re.sub(r"\s+", " ", text))


def parse_prospectus(text: str) -> list[dict]:
    """Best-effort tranche extraction from a 424B2 prospectus.

    Returns [{coupon_pct, maturity_year, principal_usd}] — joined on coupon
    between the amount pattern and the tranche pattern. Unmatched amounts or
    coupons are kept with the fields that did parse (None for the rest).
    """
    text = _strip_html(text[:400_000])
    tranches: dict[str, dict] = {}
    for m in _TRANCHE.finditer(text):
        coupon, year = m.group(1), int(m.group(2))
        tranches.setdefault(coupon, {"coupon_pct": float(coupon),
                                    "maturity_year": year,
                                    "principal_usd": None})
    for m in _AMT.finditer(text):
        amount = int(m.group(1).replace(",", ""))
        coupon = m.group(2)
        if coupon in tranches:
            tranches[coupon]["principal_usd"] = amount
        else:
            tranches[coupon] = {"coupon_pct": float(coupon),
                               "maturity_year": None, "principal_usd": amount}
    out = sorted(tranches.values(), key=lambda t: (t["maturity_year"] or 9999,
                                                   t["coupon_pct"]))
    return out[:12]


def _filing_url(cik: str, accession: str) -> str:
    nospace = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nospace}/"


async def _one_issuer(label: str, cik: str, user_agent: str, get_text: GetText,
                      cutoff: date) -> list[dict]:
    """Scan one issuer's submissions for debt filings; parse 424B2s."""
    issuances: list[dict] = []
    headers = {"User-Agent": user_agent}
    try:
        raw = await get_text(SUBMISSIONS.format(cik), headers=headers)
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — one issuer never kills the job
        log.warning("hyperscaler: %s submissions failed: %s", label, exc)
        return []
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    await asyncio.sleep(PAUSE)
    for form, fdate, acc in zip(forms, dates, accessions):
        if form not in DEBT_FORMS:
            continue
        try:
            fdt = date.fromisoformat(fdate)
        except ValueError:
            continue
        if fdt < cutoff:
            continue
        entry = {
            "issuer": label, "cik": cik, "form": form, "filing_date": fdate,
            "url": _filing_url(cik, acc), "tranches": [],
            "parsed": "no",
        }
        if form == "424B2":
            try:
                idx_raw = await get_text(
                    ARCHIVE.format(int(cik), acc.replace("-", "")) + "index.json",
                    headers=headers)
                items = (json.loads(idx_raw).get("directory") or {}).get("item") or []
                primary = next(
                    (i["name"] for i in items
                     if i["name"].lower().endswith((".htm", ".html"))
                     and "index" not in i["name"].lower()
                     and "fee" not in i["name"].lower()),
                    None,
                )
                if primary:
                    doc = await get_text(
                        ARCHIVE.format(int(cik), acc.replace("-", "")) + primary,
                        headers=headers)
                    tranches = parse_prospectus(doc)
                    entry["tranches"] = tranches
                    entry["parsed"] = "yes" if tranches else "no-tranches-found"
                await asyncio.sleep(PAUSE)
            except Exception as exc:  # noqa: BLE001
                log.warning("hyperscaler: %s %s parse failed: %s", label, acc, exc)
                entry["parsed"] = f"error: {type(exc).__name__}"
        issuances.append(entry)
        await asyncio.sleep(PAUSE)
    return issuances


def _equity_card(store: Store, label: str, ticker: str, series_id: str) -> dict:
    card = {"label": label, "ticker": ticker, "last": None, "chg_1m_pct": None,
            "spark": []}
    try:
        pts = store.points(f"cycle:{series_id}")
    except Exception:  # noqa: BLE001
        return card
    if not pts:
        return card
    ordered = sorted(pts.items())
    asof = ordered[-1][0]
    card["last"] = round(ordered[-1][1], 2)
    ref_d = asof - timedelta(days=30)
    prior = [d for d, _ in ordered if d <= ref_d]
    if prior:
        ref = pts[max(prior)]
        if ref:
            card["chg_1m_pct"] = round(100.0 * (card["last"] - ref) / ref, 2)
    tail = [(d, v) for d, v in ordered if d >= asof - timedelta(days=SPARK_DAYS)]
    card["spark"] = [[d.isoformat(), round(v, 2)] for d, v in tail]
    return card


async def fetch_hyperscaler(cfg: Config, store: Store,
                            get_text: GetText) -> str:
    """Weekly job: EDGAR debt-offering scan + equity cards from store history.

    SEC requires the configured contact User-Agent (the default product/(+url)
    style is 403'd) — same rule as the thirteenf fetcher.
    """
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS)
    user_agent = cfg.thirteenf.user_agent
    issuances: list[dict] = []
    for label, _ticker, cik, _sid in ISSUERS:
        issuances.extend(await _one_issuer(label, cik, user_agent, get_text,
                                          cutoff))
    issuances.sort(key=lambda e: e["filing_date"], reverse=True)
    equities = [_equity_card(store, label, ticker, sid)
                for label, ticker, _cik, sid in ISSUERS]
    note = (
        "Single-name bond spreads/CDS have no free feed "
        "(S3, S&P Global, Markit are paid); sector HY/IG OAS shown as proxy. "
        "short interest is FINRA's free twice-monthly consolidated file, "
        "shown per card. "
        "Holder flows covered quarterly by the 13F watchlist. "
        "Tranche fields are parsed best-effort from prospectus text."
    )
    store.put_doc(DOC_KEY, {
        "as_of": today.isoformat(),
        "issuances": issuances,
        "equities": equities,
        "note": note,
    }, source=SOURCE)
    return SOURCE
