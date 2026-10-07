"""CBOE delayed options chains — per-symbol aggregates (GEX, put/call ratios,
ATM IV, max pain) + unusual activity. Yahoo Finance v7 as fallback.

Primary: https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json
(keyless, ~15-min delayed). Contracts carry gamma/OI/volume/IV so GEX is
real arithmetic, not synthetic.

We never store full chains (13k contracts x 14 symbols daily = bloat).
Per symbol per day we store:
  cycle:opt-{SYM}-gex      total gamma exposure, $B (calls +, puts -)
  cycle:opt-{SYM}-pc-oi    put/call open-interest ratio
  cycle:opt-{SYM}-pc-vol   put/call volume ratio
  cycle:opt-{SYM}-atm-iv   at-the-money IV, % (nearest expiry)
  cycle:opt-{SYM}-maxpain  max-pain strike, $ (nearest expiry)
plus a doc "options" with the latest snapshot: per-symbol KPIs, GEX-by-strike
for the nearest expiry (chart), and top-10 unusual-activity contracts.

Fallback: if CBOE fails for a symbol, Yahoo v7 options (no greeks — GEX is
null, OI/volume ratios still computed; flagged source=yahoo).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone

from collector.http import GetText

log = logging.getLogger(__name__)

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
YAHOO_OPTS = "https://query1.finance.yahoo.com/v7/finance/options/{sym}"

SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "USO", "XLE",
           "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META"]

OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
UNUSUAL_MIN_VOL = 500
UNUSUAL_TOP = 10


def parse_occ(sym: str) -> tuple[str, str, str, float] | None:
    """OCC symbol -> (root, expiry YYYY-MM-DD, C/P, strike)."""
    m = OCC_RE.match(sym or "")
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    return (root, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}", cp,
            int(strike) / 1000.0)


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _contract_gex(c: dict, spot: float, cp: str) -> float:
    g = (_f(c.get("gamma")) or 0.0) * (_f(c.get("open_interest")) or 0.0)
    gex = g * 100.0 * spot * spot / 1e9
    return gex if cp == "C" else -gex


def aggregate_chain(contracts: list[dict], spot: float,
                    today: str) -> dict | None:
    """Aggregate a CBOE contract list into per-symbol stats."""
    rows = []
    for c in contracts:
        p = parse_occ(c.get("option", ""))
        if not p:
            continue
        _, expiry, cp, strike = p
        rows.append((expiry, cp, strike, c))
    if not rows:
        return None

    call_oi = sum((_f(c.get("open_interest")) or 0) for _, cp, _, c in rows if cp == "C")
    put_oi = sum((_f(c.get("open_interest")) or 0) for _, cp, _, c in rows if cp == "P")
    call_vol = sum((_f(c.get("volume")) or 0) for _, cp, _, c in rows if cp == "C")
    put_vol = sum((_f(c.get("volume")) or 0) for _, cp, _, c in rows if cp == "P")
    gex = sum(_contract_gex(c, spot, cp) for _, cp, _, c in rows)

    expiries = sorted({e for e, _, _, _ in rows})
    live = [e for e in expiries if e >= today] or expiries
    near = live[0]
    near_rows = [(cp, strike, c) for e, cp, strike, c in rows if e == near]

    # ATM IV: strike nearest spot on the nearest expiry
    atm_iv = None
    if near_rows and spot:
        atm_strike = min({s for _, s, _ in near_rows}, key=lambda s: abs(s - spot))
        ivs = [(_f(c.get("iv")) or 0) * 100 for cp, s, c in near_rows
               if s == atm_strike and (_f(c.get("iv")) or 0) > 0]
        atm_iv = sum(ivs) / len(ivs) if ivs else None

    # Max pain on nearest expiry
    maxpain = None
    if near_rows:
        strikes = sorted({s for _, s, _ in near_rows})
        calls = [(s, (_f(c.get("open_interest")) or 0)) for cp, s, c in near_rows if cp == "C"]
        puts = [(s, (_f(c.get("open_interest")) or 0)) for cp, s, c in near_rows if cp == "P"]
        def pain(k: float) -> float:
            return (sum(oi * max(0.0, k - s) for s, oi in calls) +
                    sum(oi * max(0.0, s - k) for s, oi in puts))
        maxpain = min(strikes, key=pain) if strikes else None

    # GEX by strike (nearest expiry) for the chart
    strike_gex: dict[float, dict] = {}
    for cp, s, c in near_rows:
        g = _contract_gex(c, spot, cp)
        d = strike_gex.setdefault(s, {"call": 0.0, "put": 0.0})
        d["call" if cp == "C" else "put"] += g
    chart = [{"strike": s,
              "call_gex": round(v["call"], 3),
              "put_gex": round(v["put"], 3)}
             for s, v in sorted(strike_gex.items())]

    # Unusual activity: top contracts by volume/OI
    unusual = []
    for e, cp, s, c in rows:
        vol = _f(c.get("volume")) or 0
        oi = _f(c.get("open_interest")) or 0
        if vol >= UNUSUAL_MIN_VOL and oi > 0:
            unusual.append({
                "contract": c.get("option"), "expiry": e,
                "type": "Call" if cp == "C" else "Put", "strike": s,
                "volume": vol, "oi": oi, "vol_oi": round(vol / oi, 1),
                "iv": round((_f(c.get("iv")) or 0) * 100, 1),
                "bid": _f(c.get("bid")), "ask": _f(c.get("ask")),
                "spot": spot,
            })
    unusual.sort(key=lambda r: r["vol_oi"], reverse=True)
    unusual = unusual[:UNUSUAL_TOP]

    return {
        "spot": spot, "gex": round(gex, 2),
        "pc_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "pc_vol": round(put_vol / call_vol, 3) if call_vol else None,
        "atm_iv": round(atm_iv, 2) if atm_iv is not None else None,
        "maxpain": maxpain, "near_expiry": near,
        "expiries": expiries[:8], "strike_chart": chart,
        "unusual": unusual, "n_contracts": len(rows),
    }


def aggregate_yahoo(options: list[dict], expiries: list[int]) -> dict | None:
    """Fallback aggregation from Yahoo v7 options (no greeks)."""
    calls, puts = [], []
    for block in options:
        calls += block.get("calls") or []
        puts += block.get("puts") or []
    if not calls and not puts:
        return None
    call_oi = sum((_f(c.get("openInterest")) or 0) for c in calls)
    put_oi = sum((_f(c.get("openInterest")) or 0) for c in puts)
    call_vol = sum((_f(c.get("volume")) or 0) for c in calls)
    put_vol = sum((_f(c.get("volume")) or 0) for c in puts)
    unusual = []
    for c in calls + puts:
        vol = _f(c.get("volume")) or 0
        oi = _f(c.get("openInterest")) or 0
        if vol >= UNUSUAL_MIN_VOL and oi > 0:
            unusual.append({
                "contract": c.get("contractSymbol"),
                "expiry": None, "type": "Call" if c in calls else "Put",
                "strike": _f(c.get("strike")),
                "volume": vol, "oi": oi, "vol_oi": round(vol / oi, 1),
                "iv": round((_f(c.get("impliedVolatility")) or 0) * 100, 1),
                "bid": _f(c.get("bid")), "ask": _f(c.get("ask")),
                "spot": None,
            })
    unusual.sort(key=lambda r: r["vol_oi"], reverse=True)
    return {
        "spot": None, "gex": None,
        "pc_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "pc_vol": round(put_vol / call_vol, 3) if call_vol else None,
        "atm_iv": None, "maxpain": None, "near_expiry": None,
        "expiries": [], "strike_chart": [],
        "unusual": unusual[:UNUSUAL_TOP],
        "n_contracts": len(calls) + len(puts),
    }


async def fetch_symbol(sym: str, get_text: GetText, today: str) -> tuple[dict | None, str]:
    """Fetch one symbol. Returns (stats, source)."""
    try:
        raw = await get_text(CBOE_URL.format(sym=sym))
        data = json.loads(raw).get("data") or {}
        contracts = data.get("options") or []
        spot = _f(data.get("current_price"))
        if not contracts or not spot:
            raise ValueError("empty chain")
        stats = aggregate_chain(contracts, spot, today)
        return (stats, "cboe") if stats else (None, "cboe-empty")
    except Exception as exc:  # noqa: BLE001 — fall back to Yahoo
        log.warning("cboe_options: CBOE %s failed (%s), trying Yahoo", sym, exc)
    try:
        raw = await get_text(YAHOO_OPTS.format(sym=sym))
        res = (json.loads(raw).get("optionChain") or {}).get("result") or []
        if not res:
            raise ValueError("no result")
        r0 = res[0]
        stats = aggregate_yahoo(r0.get("options") or [],
                                r0.get("expirationDates") or [])
        return (stats, "yahoo") if stats else (None, "yahoo-empty")
    except Exception as exc:  # noqa: BLE001
        log.warning("cboe_options: Yahoo %s also failed: %s", sym, exc)
        return None, "failed"


async def fetch_cboe_options(store, get_text: GetText) -> str:
    """Pull CBOE delayed chains for the options universe. Returns status."""
    from collector.store import Store  # local import: keeps fetcher import-light
    assert isinstance(store, Store)
    today = date.today()
    tstr = today.isoformat()
    symbols: dict[str, dict] = {}
    ok = fell = 0
    for sym in SYMBOLS:
        stats, source = await fetch_symbol(sym, get_text, tstr)
        if stats:
            ok += 1
            symbols[sym] = {**stats, "source": source,
                            "delayed": source == "cboe"}
            store.upsert_points(f"cycle:opt-{sym}-gex",
                                [(today, stats["gex"])] if stats["gex"] is not None else [])
            if stats["pc_oi"] is not None:
                store.upsert_points(f"cycle:opt-{sym}-pc-oi", [(today, stats["pc_oi"])])
            if stats["pc_vol"] is not None:
                store.upsert_points(f"cycle:opt-{sym}-pc-vol", [(today, stats["pc_vol"])])
            if stats["atm_iv"] is not None:
                store.upsert_points(f"cycle:opt-{sym}-atm-iv", [(today, stats["atm_iv"])])
            if stats["maxpain"] is not None:
                store.upsert_points(f"cycle:opt-{sym}-maxpain", [(today, stats["maxpain"])])
        else:
            fell += 1
    store.put_doc("options",
                  {"asof": tstr, "symbols": symbols,
                   "note": "CBOE 15-min delayed chains; Yahoo fallback where flagged. "
                           "GEX = Σ gamma × OI × 100 × spot² / 1e9 (calls +, puts −)."},
                  source="cboe/yahoo")
    return f"options: {ok}/{len(SYMBOLS)} symbols, {fell} failed"
