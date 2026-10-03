"""CFTC positioning beyond legacy CoT, via the Socrata public reporting API (keyless).

Datasets (all verified live 2026-10-03 on publicreporting.cftc.gov):
  tff     = udgc-27he  Traders in Financial Futures — leveraged funds,
                       asset managers, dealers on financial futures
  cit     = j83k-qyrd  Commodity Index Traders
  disagg  = rxbv-e226  Disaggregated commitments — managed money, swap dealers,
                       producer/merchants on commodity futures

Net positioning = long - short per group, weekly (Tuesdays). $limit=5000
covers the full history. Same anonymous Socrata pattern as fetchers/cftc.py.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from collector.config import CftcPosCfg
from collector.http import GetText
from collector.store import Store

DATASETS = {
    "tff": "udgc-27he",
    "cit": "j83k-qyrd",
    "disagg": "rxbv-e226",
}
BASE = "https://publicreporting.cftc.gov/resource"

# (dataset, group) -> (long column, short column); verified against live rows.
COLUMNS = {
    ("tff", "lev_money"): ("lev_money_positions_long", "lev_money_positions_short"),
    ("tff", "asset_mgr"): ("asset_mgr_positions_long", "asset_mgr_positions_short"),
    ("tff", "dealer"): ("dealer_positions_long_all", "dealer_positions_short_all"),
    ("cit", "cit"): ("cit_positions_long_all", "cit_positions_short_all"),
    ("disagg", "m_money"): ("m_money_positions_long_all", "m_money_positions_short_all"),
    ("disagg", "swap"): ("swap_positions_long_all", "swap__positions_short_all"),
    # NOTE: Socrata's short column really has a double underscore (verified live).
    ("disagg", "prod_merc"): ("prod_merc_positions_long", "prod_merc_positions_short"),
}


def parse_net(
    text: str, long_col: str, short_col: str
) -> list[tuple[date, float]]:
    out = []
    for row in json.loads(text):
        try:
            d = datetime.fromisoformat(row["report_date_as_yyyy_mm_dd"]).date()
            net = float(row[long_col]) - float(row[short_col])
        except (KeyError, TypeError, ValueError):
            continue  # a malformed row must not fail the series
        out.append((d, net))
    if not out:
        raise ValueError("cftc_pos payload contained no usable reports")
    out.sort(key=lambda p: p[0])
    return out


async def fetch_contract_net(
    dataset_id: str,
    code: str,
    long_col: str,
    short_col: str,
    get_text: GetText,
) -> list[tuple[date, float]]:
    return parse_net(
        await get_text(
            f"{BASE}/{dataset_id}.json",
            params={
                "cftc_contract_market_code": code,
                "$select": f"report_date_as_yyyy_mm_dd,{long_col},{short_col}",
                "$order": "report_date_as_yyyy_mm_dd",
                "$limit": "5000",
            },
        ),
        long_col,
        short_col,
    )


async def fetch_cftc_positioning(
    contracts: list[CftcPosCfg], store: Store, get_text: GetText
) -> str:
    """Weekly job: net positioning per configured (dataset, contract, group).

    Each contract/group is fetched independently — one bad code or group must
    not starve the others. Stored as cftc:<dataset>:<code>:<group>.
    """
    errors: list[str] = []
    for cfg in contracts:
        dataset_id = DATASETS.get(cfg.dataset)
        if dataset_id is None:
            errors.append(f"{cfg.code}: unknown dataset {cfg.dataset!r}")
            continue
        for group in cfg.groups:
            cols = COLUMNS.get((cfg.dataset, group))
            if cols is None:
                errors.append(f"{cfg.code}: unknown group {group!r} for {cfg.dataset}")
                continue
            try:
                pts = await fetch_contract_net(dataset_id, cfg.code, cols[0], cols[1], get_text)
                store.upsert_points(f"cftc:{cfg.dataset}:{cfg.code}:{group}", pts)
            except Exception as exc:  # noqa: BLE001 — per-contract isolation
                errors.append(f"{cfg.code}/{group}: {exc}")
    if errors:
        raise RuntimeError(
            f"cftc_pos failures: {'; '.join(errors)}"
        )
    return "cftc_pos"
