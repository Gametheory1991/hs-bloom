"""ETF AUM / NAV / shares-outstanding — iShares product screener primary,
FMP (Financial Modeling Prep) fallback, CoinLaw crypto-ETF CSV fallback.

Primary (keyless, daily T+1):
  https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn?...
526 funds keyed by portfolioId; ticker in `localExchangeTicker`;
NAV in `navAmount.r`, total net assets in `totalNetAssets.r`.

Fallback chain: iShares screener -> FMP /stable/etf/info (needs the free
FMP_API_KEY env var; 250 calls/day, ~28/day used) -> CoinLaw crypto-ETF
tracker CSV (crypto only). etf.com was evaluated 2026-10-06 and is
bot-walled (HTTP 403 via Cloudflare even with full browser headers) —
not used, do not try to circumvent. Yahoo v7 quote died 2026-10-06
(HTTP 401 entitlement wall) and was removed.

CoinLaw (keyless, weekly cadence, CC BY 4.0 — attribution in doc note):
  https://coinlaw.io/crypto-etf-holdings-tracker/?desk_export=csv
22 funds / 12 issuers; columns include Ticker, Issuer, Asset,
"AUM (USD millions)", "Net flow, week (USD millions)". Flows are reported
directly (weekly), so no shares derivation is needed; we store the weekly
flow as-is. For iShares-covered crypto funds (IBIT/ETHA) CoinLaw is only a
cross-check (AUM divergence >10% is logged).

Flows are derived, not stored: shares_out = TNA / NAV;
net_flow_usd(day) = (shares_t - shares_t-1) x nav_t. The UI computes them
from consecutive daily snapshots (series below), so history accumulates
from the first run.

Stored per ticker per day:
  cycle:etf-{TICKER}-aum     total net assets, $
  cycle:etf-{TICKER}-nav     NAV per share, $ (iShares/Yahoo only)
  cycle:etf-{TICKER}-shares  shares outstanding (iShares/Yahoo only)
  cycle:etf-{TICKER}-flow7d  reported weekly net flow, $ (CoinLaw only)
plus a doc "etfflows" with the latest snapshot league table.

Yahoo fallback (v7 quote): marketCap ~= AUM, sharesOutstanding, NAV ~=
marketCap / sharesOutstanding. Flagged source=yahoo.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from datetime import date

from collector.http import GetText

log = logging.getLogger(__name__)

# FMP (Financial Modeling Prep) fallback — free key in FMP_API_KEY env var,
# 250 calls/day. Covers the non-iShares, non-crypto gap (State Street /
# Vanguard / Invesco / Janus Henderson / PIMCO / WisdomTree / USCF funds).
FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_DELAY = 1.1  # polite pacing between FMP calls

ISHARES_URL = ("https://www.ishares.com/us/product-screener/"
               "product-screener-v3.1.jsn"
               "?dcrPath=/templatedata/config/product-screener-v3/data/en/"
               "us-ishares/ishares-product-screener-backend-config"
               "&siteEntryPassthrough=true&loc=en_us")
# etf.com is bot-walled (Cloudflare 403) — intentionally not used.
COINLAW_CSV = ("https://coinlaw.io/crypto-etf-holdings-tracker/"
               "?desk_export=csv")
# NOTE 2026-10-06: Yahoo v7 quote (marketCap/sharesOutstanding) now returns
# HTTP 401 "Unauthorized" even with browser UA + cookies + crumb — Yahoo put
# it behind an entitlement wall. The fallback was removed; non-iShares,
# non-CoinLaw funds are reported as pending until issuer-direct APIs land
# (State Street, Vanguard, Invesco, Janus Henderson, PIMCO, WisdomTree,
# USCF). See doc payload "pending".

# (ticker, asset class). Non-iShares names fall back to Yahoo automatically
# when the screener has no match.
ETF_UNIVERSE: list[tuple[str, str]] = [
    ("IVV", "equity"), ("IWM", "equity"), ("IJH", "equity"), ("IJR", "equity"),
    ("IEMG", "equity"), ("EFA", "equity"), ("EEM", "equity"),
    ("ACWI", "equity"), ("ITOT", "equity"),
    ("IXUS", "equity"), ("IEFA", "equity"), ("IVW", "equity"), ("IVE", "equity"),
    ("IWD", "equity"), ("IWF", "equity"), ("QUAL", "equity"), ("USMV", "equity"),
    ("MTUM", "equity"),
    # US fixed income (Harry 2026-10-06 expansion)
    ("AGG", "fixedincome"), ("TLT", "fixedincome"), ("IEF", "fixedincome"),
    ("SHY", "fixedincome"), ("TIP", "fixedincome"), ("LQD", "fixedincome"),
    ("HYG", "fixedincome"), ("JNK", "fixedincome"), ("EMB", "fixedincome"),
    ("MBB", "fixedincome"), ("IUSB", "fixedincome"), ("SHYG", "fixedincome"),
    ("IGSB", "fixedincome"), ("SUB", "fixedincome"), ("MUB", "fixedincome"),
    ("CWB", "fixedincome"), ("PFF", "fixedincome"),
    ("SHV", "fixedincome"), ("SGOV", "fixedincome"),
    # private credit / CLO ETFs (Harry 2026-10-06 expansion)
    ("JAAA", "privcredit"), ("CLOX", "privcredit"), ("CLOI", "privcredit"),
    ("JBBB", "privcredit"), ("BINC", "privcredit"), ("BBDC", "privcredit"),
    ("VNQ", "realestate"), ("REET", "realestate"),
    ("IAU", "commodity"), ("SLV", "commodity"),
    ("IBIT", "crypto"), ("ETHA", "crypto"),
    # non-iShares crypto ETFs: Yahoo fallback first, then CoinLaw CSV
    # (weekly reported flows). Grayscale minis trade as BTC/ETH.
    ("FBTC", "crypto"), ("GBTC", "crypto"), ("BITB", "crypto"),
    ("ARKB", "crypto"), ("BTC", "crypto"), ("HODL", "crypto"),
    ("BRRR", "crypto"), ("EZBC", "crypto"), ("BTCO", "crypto"),
    ("BTCW", "crypto"), ("DEFI", "crypto"),
    ("ETHE", "crypto"), ("ETH", "crypto"), ("FETH", "crypto"),
    ("ETHW", "crypto"), ("ETHV", "crypto"), ("EZET", "crypto"),
    ("TETH", "crypto"), ("QETH", "crypto"),
    ("EMXC", "equity"), ("IDEV", "equity"),
    # non-iShares: Yahoo fallback (State Street / Vanguard / Invesco /
    # WisdomTree / Janus Henderson / PIMCO)
    ("SPY", "equity"), ("QQQ", "equity"), ("DIA", "equity"), ("VTI", "equity"),
    ("VOO", "equity"), ("VEA", "equity"), ("VWO", "equity"),
    ("BND", "fixedincome"), ("VCIT", "fixedincome"), ("VCSH", "fixedincome"),
    ("VGSH", "fixedincome"), ("BKLN", "fixedincome"), ("BIL", "fixedincome"),
    ("USFR", "fixedincome"),
    ("GLD", "commodity"), ("USO", "commodity"), ("UNG", "commodity"),
    ("XLE", "equity"), ("XLF", "equity"), ("XLK", "equity"),
]

CLASS_LABELS = {
    "equity": "Equities", "fixedincome": "Fixed Income",
    "commodity": "Commodities", "crypto": "Crypto",
    "privcredit": "Private Credit", "realestate": "Real Estate",
}


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _r(v) -> float | None:
    """iShares {d, r} wrapper -> raw float."""
    if isinstance(v, dict):
        return _f(v.get("r"))
    return _f(v)


def parse_ishares(text: str) -> dict[str, dict]:
    """Screener JSON -> {ticker: {name, nav, aum, asof}}."""
    data = json.loads(text)
    out: dict[str, dict] = {}
    for rec in data.values():
        if not isinstance(rec, dict):
            continue
        t = rec.get("localExchangeTicker")
        if not t:
            continue
        nav, aum = _r(rec.get("navAmount")), _r(rec.get("totalNetAssets"))
        if not nav or not aum:
            continue
        asof = (rec.get("navAmountAsOf") or {}).get("d") if isinstance(
            rec.get("navAmountAsOf"), dict) else None
        out[str(t).upper()] = {
            "name": rec.get("fundName"), "nav": nav, "aum": aum,
            "shares": aum / nav, "asof": asof,
        }
    return out


def parse_coinlaw(text: str) -> dict[str, dict]:
    """CoinLaw crypto-ETF CSV -> {ticker: {name, issuer, asset, aum,
    flow7d, flow_launch, asof}}. AUM/flows in USD millions -> $."""
    # file starts with a BOM; strip it before parsing
    rows = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    out: dict[str, dict] = {}
    for r in rows:
        t = (r.get("Ticker") or "").strip().upper()
        if not t:
            continue
        aum_m = _f((r.get("AUM (USD millions)") or "").replace(",", ""))
        flow_m = _f((r.get("Net flow, week (USD millions)") or "").replace(",", ""))
        launch_m = _f((r.get("Net flow since launch (USD millions)") or "").replace(",", ""))
        if aum_m is None:
            continue
        out[t] = {
            "name": (r.get("Name") or "").strip() or None,
            "issuer": (r.get("Issuer") or "").strip() or None,
            "asset": (r.get("Asset") or "").strip() or None,
            "aum": aum_m * 1e6,
            "flow7d": flow_m * 1e6 if flow_m is not None else None,
            "flow_launch": launch_m * 1e6 if launch_m is not None else None,
            "asof": (r.get("Holdings date") or "").strip() or None,
        }
    return out


def parse_fmp_info(payload) -> dict | None:
    """FMP /stable/etf/info -> {name, aum, nav, shares}. Response is a
    list with one dict. Field names per FMP docs: `aum` confirmed;
    NAV tried as navPrice/nav/netAssetValue (defensive — first live
    production run will confirm which one FMP serves)."""
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return None
    aum = _f(row.get("aum"))
    if not aum:
        return None
    nav = (_f(row.get("navPrice")) or _f(row.get("nav"))
           or _f(row.get("netAssetValue")))
    return {
        "name": row.get("name") or row.get("companyName"),
        "aum": aum, "nav": nav,
        "shares": aum / nav if nav else None,
        "asof": row.get("date") or row.get("updated"),
    }


async def _fmp_quote_price(get_text: GetText, key: str,
                           ticker: str) -> float | None:
    """Last-resort NAV proxy: FMP /stable/quote price (ETFs trade ~NAV)."""
    try:
        q = json.loads(await get_text(
            f"{FMP_BASE}/quote", params={"symbol": ticker, "apikey": key}))
        row = q[0] if isinstance(q, list) and q else q
        return _f((row or {}).get("price")) if isinstance(row, dict) else None
    except Exception:  # noqa: BLE001
        return None


async def fetch_ishares_etf(store, get_text: GetText) -> str:
    """Pull ETF AUM/NAV/shares. Returns a status string."""
    from collector.store import Store
    assert isinstance(store, Store)
    today = date.today()
    funds: dict[str, dict] = {}
    n_ishares = n_coinlaw = 0

    try:
        by_ticker = parse_ishares(await get_text(ISHARES_URL))
        log.info("ishares_etf: screener returned %d funds", len(by_ticker))
    except Exception as exc:  # noqa: BLE001 — whole screener failed
        log.warning("ishares_etf: screener failed: %s", exc)
        by_ticker = {}

    missing = [t for t, _ in ETF_UNIVERSE if t not in by_ticker]

    # CoinLaw crypto-ETF CSV: fills the non-iShares crypto gap (weekly
    # reported flows) and cross-checks iShares-covered crypto funds.
    coinlaw: dict[str, dict] = {}
    try:
        coinlaw = parse_coinlaw(await get_text(COINLAW_CSV))
        log.info("ishares_etf: coinlaw returned %d funds", len(coinlaw))
    except Exception as exc:  # noqa: BLE001
        log.warning("ishares_etf: coinlaw failed: %s", exc)

    pending: list[str] = []
    cls_by_ticker = dict(ETF_UNIVERSE)
    for ticker, cls in ETF_UNIVERSE:
        rec = by_ticker.get(ticker)
        source = "ishares"
        if rec is None and cls == "crypto":
            cl = coinlaw.get(ticker)
            if cl is not None:
                rec = {"name": cl["name"], "aum": cl["aum"],
                       "nav": None, "shares": None, "asof": cl["asof"],
                       "flow7d": cl["flow7d"], "issuer": cl["issuer"],
                       "asset": cl["asset"]}
                source = "coinlaw"
        if rec is None:
            # FMP fallback runs after this loop; anything still uncovered
            # stays honestly pending
            pending.append(ticker)
            continue
        funds[ticker] = {**rec, "ticker": ticker, "class": cls,
                         "source": source}
        if source == "ishares":
            n_ishares += 1
        else:
            n_coinlaw += 1
        store.upsert_points(f"cycle:etf-{ticker}-aum", [(today, rec["aum"])])
        if rec.get("nav") is not None:
            store.upsert_points(f"cycle:etf-{ticker}-nav",
                                [(today, rec["nav"])])
        if rec.get("shares") is not None:
            store.upsert_points(f"cycle:etf-{ticker}-shares",
                                [(today, rec["shares"])])
        if rec.get("flow7d") is not None:
            store.upsert_points(f"cycle:etf-{ticker}-flow7d",
                                [(today, rec["flow7d"])])
        # cross-check: iShares vs CoinLaw AUM for covered crypto funds
        if source == "ishares" and cls == "crypto" and ticker in coinlaw:
            cl_aum = coinlaw[ticker]["aum"]
            if cl_aum and abs(rec["aum"] - cl_aum) / cl_aum > 0.10:
                log.warning("ishares_etf: AUM cross-check %s: ishares $%.1fB vs "
                            "coinlaw $%.1fB", ticker, rec["aum"] / 1e9,
                            cl_aum / 1e9)

    # FMP fallback (Harry 2026-10-06): free FMP_API_KEY, 250 calls/day.
    # Covers whatever iShares + CoinLaw missed (non-iShares equity/bond/
    # commodity ETFs). Skipped entirely when the key is unset.
    n_fmp = 0
    fmp_key = os.environ.get("FMP_API_KEY")
    if fmp_key and pending:
        for ticker in list(pending):
            rec = None
            try:
                payload = json.loads(await get_text(
                    f"{FMP_BASE}/etf/info",
                    params={"symbol": ticker, "apikey": fmp_key}))
                rec = parse_fmp_info(payload)
                if rec and rec.get("aum") and not rec.get("nav"):
                    # etf/info had AUM but no NAV — quote price as proxy
                    await asyncio.sleep(FMP_DELAY)
                    px = await _fmp_quote_price(get_text, fmp_key, ticker)
                    if px:
                        rec["nav"] = px
                        rec["shares"] = rec["aum"] / px
            except Exception as exc:  # noqa: BLE001
                # error message has the query (key) stripped by http.get_text
                log.warning("ishares_etf: fmp %s failed: %s", ticker, exc)
            if rec and rec.get("aum"):
                pending.remove(ticker)
                funds[ticker] = {**rec, "ticker": ticker,
                                 "class": cls_by_ticker.get(ticker, "equity"),
                                 "source": "fmp"}
                store.upsert_points(f"cycle:etf-{ticker}-aum",
                                    [(today, rec["aum"])])
                if rec.get("nav") is not None:
                    store.upsert_points(f"cycle:etf-{ticker}-nav",
                                        [(today, rec["nav"])])
                if rec.get("shares") is not None:
                    store.upsert_points(f"cycle:etf-{ticker}-shares",
                                        [(today, rec["shares"])])
                n_fmp += 1
            await asyncio.sleep(FMP_DELAY)
    elif pending:
        log.warning("ishares_etf: FMP_API_KEY not set; %d funds pending",
                    len(pending))

    store.put_doc("etfflows",
                  {"asof": today.isoformat(), "funds": funds,
                   "classes": CLASS_LABELS,
                   "pending": pending,
                   "note": "iShares screener is T+1; FMP (free tier) covers "
                           "non-iShares ETFs; CoinLaw (CC BY 4.0, "
                           "https://coinlaw.io/crypto-etf-holdings-tracker/) "
                           "covers non-iShares crypto ETFs with weekly "
                           "reported flows. Flows = Δshares × NAV, "
                           "computed from consecutive snapshots. "
                           "Pending: funds with no free source yet "
                           "(Yahoo v7 quote is entitlement-walled)."},
                  source="ishares/fmp/coinlaw")
    return (f"etfflows: {len(funds)}/{len(ETF_UNIVERSE)} funds "
            f"({n_ishares} ishares, {n_fmp} fmp, {n_coinlaw} coinlaw, "
            f"{len(pending)} pending)")
