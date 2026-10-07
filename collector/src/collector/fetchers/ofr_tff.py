"""OFR Traders in Financial Futures (TFF) via the Hedge Fund Monitor API (keyless).

Base: https://data.financialresearch.gov/hf/v1  (verified live 2026-10-06)

Endpoints:
  GET /hf/v1/metadata/mnemonics?dataset=tff  -> [{mnemonic, series_name}] (153)
  GET /hf/v1/series/timeseries?mnemonic=<id> -> [[date, value], ...], weekly

TFF = CFTC Traders in Financial Futures positioning by trader type:
  LF = leveraged funds (the hedge-fund basis-trade proxy)
  AI = asset managers / institutional
  DI = dealers / intermediaries
Contracts: TREAS (all Treasury futures), TU/FV/TY/UXY/US/WN tenors,
ED/FF/SFR funding futures, SP/ND/DJ/NX/NV equities, EC/JY/BP FX,
BITCOIN/ETHER, VIX.
Metrics: LONG/SHORT/NET_POSITION (notional $ or contracts),
LONG/SHORT/NET_DV01 ($), LONG/SHORT/NET_POS10YREQV (10Y-equiv contracts).

Headline: TFF-LF_TREAS_NET_POSITION — leveraged-funds net Treasury futures,
weekly since 2013-03-05; -$798.8B on 2026-09-15 (the basis-trade short).

AI/DI publish only LONG/SHORT legs for TREAS (no NET) — nets are derived
here (long - short) and stored as cycle:tff-ai_treas_net_position etc.

All 153 mnemonics are pulled with full history and stored as
cycle:tff-<mnemonic-lowercased-minus-TFF->. A doc 'tff' carries the curated
panel data (rolling ~300-week history per curated row) so the UI grid loads
in one fetch instead of a 150-series fan-out.

OFR asks clients not to poll more than daily; the series are weekly, so the
job runs daily and upserts whatever is new (idempotent).
"""
from __future__ import annotations

import logging
from datetime import date

from collector.fetchers.ofr import parse_timeseries
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

BASE = "https://data.financialresearch.gov/hf/v1"
SOURCE = "ofr-tff"
# rolling history kept per curated row in the doc (~5.75Y of weekly points)
DOC_ROLL = 300

# (group_id, group label, unit, [(mnemonic, row label), ...])
CURATED: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    ("treas_net", "Treasury futures — net position", "$", [
        ("TFF-LF_TREAS_NET_POSITION", "Leveraged funds"),
        ("TFF-AI_TREAS_NET_POSITION", "Asset managers"),
        ("TFF-DI_TREAS_NET_POSITION", "Dealers"),
    ]),
    ("treas_dv01", "Treasury futures — net DV01", "$", [
        ("TFF-LF_TREAS_NET_DV01", "Leveraged funds"),
        ("TFF-AI_TREAS_NET_DV01", "Asset managers"),
        ("TFF-DI_TREAS_NET_DV01", "Dealers"),
    ]),
    ("treas_10y", "Treasury futures — net 10Y-equiv", "$", [
        ("TFF-LF_TREAS_NET_POS10YREQV", "Leveraged funds"),
        ("TFF-AI_TREAS_NET_POS10YREQV", "Asset managers"),
        ("TFF-DI_TREAS_NET_POS10YREQV", "Dealers"),
    ]),
    ("tenor", "By tenor — leveraged funds net", "$", [
        ("TFF-LF_TU_NET_POSITION", "2Y (TU)"),
        ("TFF-LF_FV_NET_POSITION", "5Y (FV)"),
        ("TFF-LF_TY_NET_POSITION", "10Y (TY)"),
        ("TFF-LF_UXY_NET_POSITION", "Ultra 10Y (UXY)"),
        ("TFF-LF_US_NET_POSITION", "30Y (US)"),
        ("TFF-LF_WN_NET_POSITION", "Ultra bond (WN)"),
    ]),
    ("funding", "Funding futures — LF net DV01", "$", [
        ("TFF-LF_ED_NET_DV01", "Eurodollar (ED)"),
        ("TFF-LF_FF_NET_DV01", "Fed funds (FF)"),
        ("TFF-LF_SFR_NET_DV01", "SOFR (SFR)"),
        ("TFF-LF_SER_NET_DV01", "1M SOFR (SER)"),
    ]),
    ("equity", "Equity index — LF net", "$", [
        ("TFF-LF_SP_NET_POSITION", "S&P 500 (SP)"),
        ("TFF-LF_ND_NET_POSITION", "Nasdaq-100 (ND)"),
        ("TFF-LF_DJ_NET_POSITION", "Dow (DJ)"),
        ("TFF-LF_NX_NET_POSITION", "Nikkei 225 (NX)"),
    ]),
    ("fx", "FX futures — LF net", "$", [
        ("TFF-LF_EC_NET_POSITION", "Euro (EC)"),
        ("TFF-LF_JY_NET_POSITION", "Yen (JY)"),
        ("TFF-LF_BP_NET_POSITION", "Pound (BP)"),
        ("TFF-LF_NV_NET_POSITION", "NZ dollar (NV)"),
        ("TFF-LF_AD_NET_POSITION", "Aussie dollar (AD)"),
    ]),
    ("crypto", "Crypto futures — LF net", "$", [
        ("TFF-LF_BITCOIN_NET_POSITION", "Bitcoin"),
        ("TFF-LF_ETHER_NET_POSITION", "Ether"),
    ]),
    ("vol", "Volatility — LF net", "$", [
        ("TFF-LF_VIX_NET_POSITION", "VIX"),
    ]),
]

# AI/DI TREAS nets are derived (long - short); legs that feed them.
_DERIVED = {
    "TFF-AI_TREAS_NET_POSITION": ("TFF-AI_TREAS_LONG_POSITION", "TFF-AI_TREAS_SHORT_POSITION"),
    "TFF-AI_TREAS_NET_DV01": ("TFF-AI_TREAS_LONG_DV01", "TFF-AI_TREAS_SHORT_DV01"),
    "TFF-AI_TREAS_NET_POS10YREQV": ("TFF-AI_TREAS_LONG_POS10YREQV", "TFF-AI_TREAS_SHORT_POS10YREQV"),
    "TFF-DI_TREAS_NET_POSITION": ("TFF-DI_TREAS_LONG_POSITION", "TFF-DI_TREAS_SHORT_POSITION"),
    "TFF-DI_TREAS_NET_DV01": ("TFF-DI_TREAS_LONG_DV01", "TFF-DI_TREAS_SHORT_DV01"),
    "TFF-DI_TREAS_NET_POS10YREQV": ("TFF-DI_TREAS_LONG_POS10YREQV", "TFF-DI_TREAS_SHORT_POS10YREQV"),
}


def slug(mnemonic: str) -> str:
    m = mnemonic.upper()
    return m[4:].lower() if m.startswith("TFF-") else m.lower()


async def _fetch_mnemonics(get_text: GetText) -> list[dict]:
    import json
    text = await get_text(f"{BASE}/metadata/mnemonics", params={"dataset": "tff"})
    items = json.loads(text)
    if not isinstance(items, list) or not items:
        raise ValueError("tff mnemonics list empty")
    return items


async def fetch_ofr_tff(store: Store, get_text: GetText) -> str:
    """Daily-max job: full history for all 153 TFF mnemonics + derived nets.

    Stored as cycle:tff-<slug>. Each mnemonic fetched independently — one
    bad mnemonic must not starve the others. Doc 'tff' carries curated panel
    data (rolling history per row) for the UI.
    """
    items = await _fetch_mnemonics(get_text)
    mnemonics = [x["mnemonic"] for x in items if x.get("mnemonic")]
    names = {x["mnemonic"]: x.get("series_name", "") for x in items}

    raw: dict[str, list[tuple[date, float]]] = {}
    errors: list[str] = []
    for m in mnemonics:
        try:
            raw[m] = parse_timeseries(
                await get_text(f"{BASE}/series/timeseries", params={"mnemonic": m})
            )
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{m}: {exc}")
    # derived AI/DI TREAS nets (long - short, date-aligned)
    for net_m, (long_m, short_m) in _DERIVED.items():
        try:
            longs = dict(raw[long_m])
            shorts = dict(raw[short_m])
            pts = [(d, longs[d] - shorts[d]) for d in sorted(set(longs) & set(shorts))]
            if pts:
                raw[net_m] = pts
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{net_m} (derived): {exc}")

    stored = 0
    for m, pts in raw.items():
        try:
            store.upsert_points(f"cycle:tff-{slug(m)}", pts)
            stored += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"store {m}: {exc}")

    # curated panel doc
    groups = []
    asof = None
    for gid, glabel, unit, rows in CURATED:
        rrows = []
        for m, label in rows:
            pts = raw.get(m, [])
            if pts:
                asof = max(asof, pts[-1][0].isoformat()) if asof else pts[-1][0].isoformat()
            rrows.append({
                "id": f"cycle:tff-{slug(m)}",
                "mnemonic": m,
                "label": label,
                "unit": unit,
                "name": names.get(m, ""),
                "derived": m in _DERIVED,
                "hist": [[d.isoformat(), v] for d, v in pts[-DOC_ROLL:]],
            })
        groups.append({"id": gid, "label": glabel, "unit": unit, "rows": rrows})

    store.put_doc("tff", {
        "asof": asof,
        "mnemonic_count": len(mnemonics),
        "stored": stored,
        "groups": groups,
        "names": names,
        "note": "OFR Traders in Financial Futures (TFF), weekly. LF = leveraged funds "
                "(hedge-fund basis-trade proxy), AI = asset managers, DI = dealers. "
                "AI/DI Treasury nets are derived (long − short); OFR publishes only legs. "
                "Negative LF Treasury net = net short (basis-trade footprint).",
        "source_url": "https://www.financialresearch.gov/hedge-fund-monitor/",
    }, SOURCE)

    if errors:
        log.warning("ofr_tff: %d issues: %s", len(errors), "; ".join(errors[:8]))
    return "ofr_tff"
