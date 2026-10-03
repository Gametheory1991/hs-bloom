"""CFTC COT via the Socrata public reporting API (keyless).

Dataset 6dca-aqww = legacy futures-only report; net non-commercial
positioning = long - short, weekly. $limit=5000 covers ~30y of Tuesdays.

The same dataset also carries `open_interest_all` (total futures open
interest per contract, verified live 2026-10-03) — fetch_open_interest stores
it for the Treasury codes so the terminal can chart the growing futures
short base (Bloomberg visual) against the 10Y yield.
"""
from __future__ import annotations

import json

from datetime import datetime, date

from collector.http import GetText

BASE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"


def parse_reports(text: str) -> list[tuple[date, float]]:
    out = []
    for row in json.loads(text):
        try:
            d = datetime.fromisoformat(row["report_date_as_yyyy_mm_dd"]).date()
            net = float(row["noncomm_positions_long_all"]) - float(row["noncomm_positions_short_all"])
        except (KeyError, TypeError, ValueError):
            continue  # a malformed row must not fail the series
        out.append((d, net))
    if not out:
        raise ValueError("cftc payload contained no usable reports")
    out.sort(key=lambda p: p[0])
    return out


def parse_open_interest(text: str) -> list[tuple[date, float]]:
    """Total futures open interest per report date (contracts)."""
    out = []
    for row in json.loads(text):
        try:
            d = datetime.fromisoformat(row["report_date_as_yyyy_mm_dd"]).date()
            oi = float(row["open_interest_all"])
        except (KeyError, TypeError, ValueError):
            continue  # a malformed row must not fail the series
        out.append((d, oi))
    if not out:
        raise ValueError("cftc payload contained no usable open-interest reports")
    out.sort(key=lambda p: p[0])
    return out


async def fetch_net_noncommercial(code: str, get_text: GetText) -> list[tuple[date, float]]:
    return parse_reports(await get_text(BASE, params={
        "cftc_contract_market_code": code,
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": "5000",
    }))


async def fetch_open_interest(code: str, get_text: GetText) -> list[tuple[date, float]]:
    return parse_open_interest(await get_text(BASE, params={
        "cftc_contract_market_code": code,
        "$select": "report_date_as_yyyy_mm_dd,open_interest_all",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": "5000",
    }))
