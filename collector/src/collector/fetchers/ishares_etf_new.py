"""ETF AUM / NAV / shares-outstanding / price / expense / yield -- iShares
product screener primary, FMP (Financial Modeling Prep) fallback, CoinLaw
crypto-ETF CSV fallback, Yahoo chart API for prices + TTM distributions.

Primary (keyless, daily T+1):
  https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn?...
526 funds keyed by portfolioId; ticker in `localExchangeTicker`;
NAV in `navAmount.r`, total net assets in `totalNetAssets.r`,
expense in `fees.r`, TTM yield in `twelveMonTrlYield.r`.

Fallback chain: iShares screener -> FMP /stable/etf/info (needs the free
FMP_API_KEY env var; 250 calls/day) -> CoinLaw crypto-ETF tracker CSV
(crypto only). etf.com was evaluated 2026-10-06 and is bot-walled
(HTTP 403 via Cloudflare even with full browser headers) -- not used, do
not try to circumvent. Yahoo v7 quote died 2026-10-06 (HTTP 401
entitlement wall) and was removed; the Yahoo *chart* API (v8) still works
keyless and supplies 1Y daily closes + dividend events.

Fallback chain: iShares screener -> FMP /stable/etf/info (needs the free
FMP_API_KEY env var; 250 calls/day, ~28/day used) -> CoinLaw crypto-ETF
tracker CSV (crypto only). etf.com was evaluated 2026-10-06 and is
bot-walled (HTTP 403 via Cloudflare even with full browser headers) --
not used, do not try to circumvent. Yahoo v7 quote died 2026-10-06
(HTTP 401 entitlement wall) and was removed.

CoinLaw (keyless, weekly cadence, CC BY 4.0 -- attribution in doc note):
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
  cycle:etf-{TICKER}-aum       total net assets, $
  cycle:etf-{TICKER}-nav       NAV per share, $ (iShares/FMP only)
  cycle:etf-{TICKER}-shares    shares outstanding (iShares/FMP only)
  cycle:etf-{TICKER}-price     daily closes, 1Y history (Yahoo chart, keyless)
  cycle:etf-{TICKER}-expense   net expense ratio, % (iShares fees / FMP)
  cycle:etf-{TICKER}-divyield  TTM distribution yield, %
                              (iShares twelveMonTrlYield, else Yahoo divs)
  cycle:etf-{TICKER}-flow7d    reported weekly net flow, $ (CoinLaw only)
plus a doc "etfflows" with the latest snapshot league table.
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

# FMP (Financial Modeling Prep) fallback -- free key in FMP_API_KEY env var,
# 250 calls/day. Covers the non-iShares, non-crypto gap (State Street /
# Vanguard / Invesco / Janus Henderson / PIMCO / WisdomTree / USCF funds).
FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_DELAY = 1.1  # polite pacing between FMP calls

ISHARES_URL = ("https://www.ishares.com/us/product-screener/"
               "product-screener-v3.1.jsn"
               "?dcrPath=/templatedata/config/product-screener-v3/data/en/"
               "us-ishares/ishares-product-screener-backend-config"
               "&siteEntryPassthrough=true&loc=en_us")
# etf.com is bot-walled (Cloudflare 403) -- intentionally not used.
COINLAW_CSV = ("https://coinlaw.io/crypto-etf-holdings-tracker/"
               "?desk_export=csv")
# NOTE 2026-10-06: Yahoo v7 quote (marketCap/sharesOutstanding) now returns
# HTTP 401 "Unauthorized" even with browser UA + cookies + crumb -- Yahoo put
# it behind an entitlement wall. The fallback was removed; non-iShares,
# non-CoinLaw funds are reported as pending until issuer-direct APIs land
# (State Street, Vanguard, Invesco, Janus Henderson, PIMCO, WisdomTree,
# USCF). See doc payload "pending".

# (ticker, asset class). Non-iShares names fall back to FMP automatically
# when the screener has no match.
ETF_UNIVERSE: list[tuple[str, str]] = [
    ("IVV", "equity"), ("IWM", "equity"), ("IJH", "equity"), ("IJR", "equity"),
    ("IEMG", "equity"), ("EFA", "equity"), ("EEM", "equity"),
    ("ACWI", "equity"), ("ITOT", "equity"),
    ("IXUS", "equity"), ("IEFA", "equity"), ("IVW", "equity"), ("IVE", "equity"),
    ("IWD", "equity"), ("IWF", "equity"), ("QUAL", "equity"), ("USMV", "equity"),
    ("MTUM", "equity"), ("EMXC", "equity"), ("IDEV", "equity"),
    ("SPY", "equity"), ("QQQ", "equity"), ("DIA", "equity"), ("VTI", "equity"),
    ("VOO", "equity"), ("VEA", "equity"), ("VWO", "equity"),
    ("XLE", "equity"), ("XLF", "equity"), ("XLK", "equity"),
    # US fixed income -- Treasury sub-class (Harry 2026-10-06 expansion)
    ("TLT", "fi-treasury"), ("IEF", "fi-treasury"), ("SHY", "fi-treasury"),
    ("SHV", "fi-treasury"), ("SGOV", "fi-treasury"), ("GOVT", "fi-treasury"),
    ("VGIT", "fi-treasury"), ("VGLT", "fi-treasury"), ("SCHR", "fi-treasury"),
    ("SCHQ", "fi-treasury"), ("SPTL", "fi-treasury"), ("EDV", "fi-treasury"),
    ("ZROZ", "fi-treasury"), ("TYD", "fi-treasury"), ("UST", "fi-treasury"),
    ("TBIL", "fi-treasury"), ("VGSH", "fi-treasury"),
    ("BIL", "fi-treasury"),
    # Floaters (Harry 2026-10-06): floating-rate Treasury/IG notes
    ("TFLO", "fi-floater"), ("USFR", "fi-floater"), ("FLOT", "fi-floater"),
    ("FLRN", "fi-floater"),
    # ABS / CMBS (Harry 2026-10-06)
    ("JABS", "fi-abs"), ("CMBS", "fi-cmbs"),
    # IG corporate
    ("LQD", "fi-ig"), ("VCIT", "fi-ig"), ("VCSH", "fi-ig"),
    ("SPIB", "fi-ig"), ("SPSB", "fi-ig"), ("IGSB", "fi-ig"),
    ("IGIB", "fi-ig"), ("USIG", "fi-ig"), ("LQDH", "fi-ig"),
    # High yield
    ("HYG", "fi-hy"), ("JNK", "fi-hy"), ("SJNK", "fi-hy"), ("HYLB", "fi-hy"),
    ("USHY", "fi-hy"), ("SHYG", "fi-hy"), ("ANGL", "fi-hy"),
    # MBS
    ("MBB", "fi-mbs"), ("VMBS", "fi-mbs"), ("SPMB", "fi-mbs"),
    # Munis
    ("MUB", "fi-muni"), ("VTEB", "fi-muni"), ("SUB", "fi-muni"),
    ("SHM", "fi-muni"), ("TFI", "fi-muni"), ("HYD", "fi-muni"),
    # TIPS
    ("TIP", "fi-tips"), ("SCHP", "fi-tips"), ("VTIP", "fi-tips"),
    ("STIP", "fi-tips"),
    # Bank loans / CLO
    ("BKLN", "fi-loans"), ("SRLN", "fi-loans"), ("JAAA", "fi-loans"),
    ("CLOX", "fi-loans"), ("CLOI", "fi-loans"), ("JBBB", "fi-loans"),
    ("AAA", "fi-loans"),
    # Convertibles / preferreds
    ("CWB", "fi-conv"), ("PFF", "fi-conv"), ("PGX", "fi-conv"),
    ("PFXF", "fi-conv"),
    # Aggregate
    ("AGG", "fi-agg"), ("BND", "fi-agg"), ("SCHZ", "fi-agg"),
    ("SPAB", "fi-agg"), ("IUSB", "fi-agg"),
    # International bonds
    ("BNDX", "fi-intl"), ("IAGG", "fi-intl"), ("EMB", "fi-intl"),
    ("VWOB", "fi-intl"), ("PCY", "fi-intl"),
    # private credit ETFs (Harry 2026-10-06 expansion)
    ("BINC", "privcredit"), ("BBDC", "privcredit"),
    ("VNQ", "realestate"), ("REET", "realestate"),
    ("IAU", "commodity"), ("SLV", "commodity"),
    ("GLD", "commodity"), ("USO", "commodity"), ("UNG", "commodity"),
    ("IBIT", "crypto"), ("ETHA", "crypto"),
    # non-iShares crypto ETFs: FMP first, then CoinLaw CSV (weekly reported
    # flows). Grayscale minis trade as BTC/ETH.
    ("FBTC", "crypto"), ("GBTC", "crypto"), ("BITB", "crypto"),
    ("ARKB", "crypto"), ("BTC", "crypto"), ("HODL", "crypto"),
    ("BRRR", "crypto"), ("EZBC", "crypto"), ("BTCO", "crypto"),
    ("BTCW", "crypto"), ("DEFI", "crypto"),
    ("ETHE", "crypto"), ("ETH", "crypto"), ("FETH", "crypto"),
    ("ETHW", "crypto"), ("ETHV", "crypto"), ("EZET", "crypto"),
    ("TETH", "crypto"), ("QETH", "crypto"),
    # Crypto-equity ETFs (Harry 2026-10-06): miners, exchanges, futures --
    # tagged sub="equity" to distinguish from spot-holding ETFs (sub="spot")
    ("BKCH", "crypto"), ("WGMI", "crypto"), ("BITQ", "crypto"),
    ("DAPP", "crypto"), ("BLOK", "crypto"), ("BITO", "crypto"),
    # BDCs (Harry 2026-10-06): publicly traded business development
    # companies -- equity-like, high distribution yields. Tracked here for
    # income comparison; figures are market cap, not AUM (source fmp-mcap).
    ("ARCC", "bdc"), ("MAIN", "bdc"), ("HTGC", "bdc"), ("GBDC", "bdc"),
    ("BXSL", "bdc"), ("OCSL", "bdc"), ("PSEC", "bdc"), ("NMFC", "bdc"),
    ("TCPC", "bdc"),
    # Leveraged / inverse (Harry 2026-10-06): specialized ETFs using
    # derivatives to amplify or reverse daily returns of an index, sector,
    # or single stock.
    ("TQQQ", "leveraged"), ("SQQQ", "leveraged"), ("TNA", "leveraged"),
    ("TZA", "leveraged"), ("SPXL", "leveraged"), ("SPXS", "leveraged"),
    ("UPRO", "leveraged"), ("SDS", "leveraged"), ("QLD", "leveraged"),
    ("QID", "leveraged"), ("UVXY", "leveraged"), ("SVXY", "leveraged"),
    ("TMF", "leveraged"), ("TMV", "leveraged"), ("TBT", "leveraged"),
    ("SOXL", "leveraged"), ("SOXS", "leveraged"), ("LABU", "leveraged"),
    ("LABD", "leveraged"), ("NUGT", "leveraged"), ("DUST", "leveraged"),
    ("JNUG", "leveraged"), ("JDST", "leveraged"), ("FAS", "leveraged"),
    ("FAZ", "leveraged"), ("EDC", "leveraged"), ("EDZ", "leveraged"),
    ("YINN", "leveraged"), ("YANG", "leveraged"), ("TECL", "leveraged"),
    ("TECS", "leveraged"), ("FNGU", "leveraged"), ("FNGD", "leveraged"),
    ("BULZ", "leveraged"), ("BERZ", "leveraged"), ("TSLL", "leveraged"),
    ("NVDL", "leveraged"),
    # AI-themed (Harry 2026-10-06; IRBO verified delisted 2026-10-06)
    ("BOTZ", "ai"), ("AIQ", "ai"), ("WTAI", "ai"), ("THNQ", "ai"),
    ("ROBT", "ai"), ("ROBO", "ai"), ("XAIX", "ai"),
]

CLASS_LABELS = {
    "equity": "Equities",
    "fi-treasury": "FI: Treasury", "fi-ig": "FI: Investment Grade",
    "fi-hy": "FI: High Yield", "fi-mbs": "FI: MBS", "fi-muni": "FI: Munis",
    "fi-tips": "FI: TIPS", "fi-loans": "FI: Loans/CLO",
    "fi-conv": "FI: Conv/Preferred", "fi-agg": "FI: Aggregate",
    "fi-intl": "FI: International", "fi-floater": "FI: Floaters",
    "fi-abs": "FI: ABS", "fi-cmbs": "FI: CMBS",
    "commodity": "Commodities", "crypto": "Bitcoin & Digital Assets",
    "privcredit": "Private Credit", "bdc": "BDCs",
    "realestate": "Real Estate",
    "leveraged": "Leveraged/Inverse", "ai": "AI",
}

# Crypto-equity ETFs (miners, exchanges, futures) vs spot-holding ETFs.
# Used to tag sub="equity"/"spot" in the fund dict.
CRYPTO_EQUITY = {"BKCH", "WGMI", "BITQ", "DAPP", "BLOK", "BITO"}

LEVERAGED_DESC = ("Specialized ETFs that use financial derivatives to amplify "
                  "or reverse the daily returns of an underlying index, "
                  "sector, or single stock.")

CRYPTO_DESC = ("Spot-holding ETFs (IBIT, FBTC, ETHA…) own the underlying "
               "coin; crypto-equity ETFs (miners, exchanges, futures like "
               "BITO) track crypto-adjacent stocks. Tagged SPOT / EQUITY "
               "in the table.")
BDC_DESC = ("Business Development Companies — publicly traded lenders to "
            "middle-market firms. Equity-like with high distribution yields, "
            "tracked here for income comparison. Size shown is market cap, "
            "not AUM; no creations/redemptions.")


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
    """Screener JSON -> {ticker: {name, nav, aum, expense, divyield, asof}}."""
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
            "expense": _r(rec.get("fees")),  # net expense ratio, %
            "divyield": _r(rec.get("twelveMonTrlYield")),  # TTM dist yield, %
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
    """FMP /stable/etf/info -> {name, aum, nav, shares, expense}. Response is
    a list with one dict. Field names per FMP docs: `aum` confirmed;
    NAV tried as navPrice/nav/netAssetValue (defensive -- first live
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
        "expense": _f(row.get("expenseRatio")),
        "asof": row.get("date") or row.get("updated"),
    }


def parse_fmp_quote(payload) -> dict | None:
    """FMP /stable/quote -> {name, price, mcap}. Used for BDCs (stocks, not
    ETFs): market cap stands in for AUM (flagged source="fmp-mcap")."""
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return None
    mcap = _f(row.get("marketCap"))
    if not mcap:
        return None
    return {
        "name": row.get("name") or row.get("companyName"),
        "price": _f(row.get("price")),
        "mcap": mcap,
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


YAHOO_DELAY = 0.25  # polite pacing for ~170 keyless chart calls


async def _yahoo_price_div(get_text: GetText,
                           ticker: str) -> tuple[list, float | None]:
    """Yahoo chart API (keyless): 1Y daily closes + TTM distribution yield.

    Returns (closes [(date, price)], ttm_yield_pct). Yield = sum of cash
    distributions in the trailing 365d / latest close."""
    import urllib.parse
    from datetime import datetime, timezone, timedelta
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(ticker))
    raw = await get_text(url, params={"range": "1y", "interval": "1d",
                                      "events": "div"})
    result = (json.loads(raw).get("chart", {}).get("result") or [None])[0]
    if not result:
        return [], None
    ts = result.get("timestamp") or []
    closes_raw = ((result.get("indicators", {}).get("quote") or [{}])[0]
                  .get("close") or [])
    closes = [(datetime.fromtimestamp(t, tz=timezone.utc).date(), float(c))
              for t, c in zip(ts, closes_raw) if c is not None]
    closes.sort(key=lambda p: p[0])
    # TTM distributions
    cutoff = date.today() - timedelta(days=365)
    ttm = 0.0
    for k, v in ((result.get("events", {}) or {}).get("dividends", {})
                 or {}).items():
        try:
            d = datetime.fromtimestamp(int(k), tz=timezone.utc).date()
        except (TypeError, ValueError):
            continue
        if d >= cutoff:
            ttm += _f((v or {}).get("amount")) or 0.0
    yld = (ttm / closes[-1][1] * 100) if closes and ttm > 0 else None
    return closes, (round(yld, 2) if yld is not None else None)


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
    except Exception as exc:  # noqa: BLE001 -- whole screener failed
        log.warning("ishares_etf: screener failed: %s", exc)
        by_ticker = {}

    # (used for logging only; the pending list is built per-ticker below)
    _missing = [t for t, _ in ETF_UNIVERSE if t not in by_ticker]

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
        fund = {**rec, "ticker": ticker, "class": cls, "source": source}
        if cls == "crypto":
            fund["sub"] = ("equity" if ticker in CRYPTO_EQUITY else "spot")
        funds[ticker] = fund
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
    n_fmp = n_fmp_mcap = 0
    fmp_key = os.environ.get("FMP_API_KEY")
    if fmp_key and pending:
        for ticker in list(pending):
            rec = None
            cls = cls_by_ticker.get(ticker, "equity")
            source = "fmp"
            try:
                if cls == "bdc":
                    # BDCs are stocks: FMP quote -> market cap as AUM proxy
                    payload = json.loads(await get_text(
                        f"{FMP_BASE}/quote",
                        params={"symbol": ticker, "apikey": fmp_key}))
                    q = parse_fmp_quote(payload)
                    if q:
                        rec = {"name": q["name"], "aum": q["mcap"],
                               "nav": None, "shares": None,
                               "asof": today.isoformat()}
                        source = "fmp-mcap"
                else:
                    payload = json.loads(await get_text(
                        f"{FMP_BASE}/etf/info",
                        params={"symbol": ticker, "apikey": fmp_key}))
                    rec = parse_fmp_info(payload)
                    if rec and rec.get("aum") and not rec.get("nav"):
                        # etf/info had AUM but no NAV -- quote price as proxy
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
                fund = {**rec, "ticker": ticker, "class": cls,
                        "source": source}
                if cls == "crypto":
                    fund["sub"] = ("equity" if ticker in CRYPTO_EQUITY
                                   else "spot")
                funds[ticker] = fund
                store.upsert_points(f"cycle:etf-{ticker}-aum",
                                    [(today, rec["aum"])])
                if rec.get("nav") is not None:
                    store.upsert_points(f"cycle:etf-{ticker}-nav",
                                        [(today, rec["nav"])])
                if rec.get("shares") is not None:
                    store.upsert_points(f"cycle:etf-{ticker}-shares",
                                        [(today, rec["shares"])])
                if source == "fmp-mcap":
                    n_fmp_mcap += 1
                else:
                    n_fmp += 1
            await asyncio.sleep(FMP_DELAY)
    elif pending:
        log.warning("ishares_etf: FMP_API_KEY not set; %d funds pending",
                    len(pending))

    # Price / expense / distribution-yield pass (Harry 2026-10-06: full ETF
    # data columns). Yahoo chart API is keyless: 1Y closes give immediate
    # 1M/3M/1Y returns; events=div gives TTM distributions for yield.
    # Expense: iShares `fees` / FMP `expenseRatio` (stored, rarely changes).
    # Div yield: iShares `twelveMonTrlYield` preferred, else Yahoo TTM.
    n_px = 0
    for ticker, rec in funds.items():
        if rec.get("expense") is not None:
            store.upsert_points(f"cycle:etf-{ticker}-expense",
                                [(today, rec["expense"])])
        try:
            closes, y_yield = await _yahoo_price_div(get_text, ticker)
        except Exception as exc:  # noqa: BLE001
            log.warning("ishares_etf: yahoo %s failed: %s", ticker, exc)
            closes, y_yield = [], None
        if closes:
            store.upsert_points(f"cycle:etf-{ticker}-price", closes)
            n_px += 1
        dy = rec.get("divyield")  # iShares twelveMonTrlYield, %
        if dy is None:
            dy = y_yield
        if dy is not None:
            store.upsert_points(f"cycle:etf-{ticker}-divyield", [(today, dy)])
            rec["divyield"] = dy
        if closes:
            rec["price"] = closes[-1][1]
        await asyncio.sleep(YAHOO_DELAY)
    log.info("ishares_etf: yahoo prices for %d/%d funds",
             n_px, len(funds))

    store.put_doc("etfflows",
                  {"asof": today.isoformat(), "funds": funds,
                   "classes": CLASS_LABELS,
                   "pending": pending,
                   "note": "iShares screener is T+1; FMP (free tier) covers "
                           "non-iShares ETFs; CoinLaw (CC BY 4.0, "
                           "https://coinlaw.io/crypto-etf-holdings-tracker/) "
                           "covers non-iShares crypto ETFs with weekly "
                           "reported flows. BDCs via FMP quote (market cap "
                           "as size, no NAV/flows). Prices + TTM yields via "
                           "Yahoo chart API (keyless, 1Y history). Expense: "
                           "iShares fees / FMP expenseRatio. Flows = "
                           "Δshares × NAV, computed from consecutive "
                           "snapshots. Disc/Prem = (price − NAV)/NAV. "
                           "Pending: funds with no free source yet "
                           "(Yahoo v7 quote is entitlement-walled)."},
                  source="ishares/fmp/coinlaw/yahoo")
    return (f"etfflows: {len(funds)}/{len(ETF_UNIVERSE)} funds "
            f"({n_ishares} ishares, {n_fmp} fmp, {n_fmp_mcap} fmp-mcap, "
            f"{n_coinlaw} coinlaw, {len(pending)} pending)")
