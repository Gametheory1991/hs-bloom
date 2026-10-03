"""OFR Hedge Fund Monitor API (keyless). dataset=fpf = SEC Form PF aggregates.

Endpoints (verified live 2026-10-03):
  GET https://data.financialresearch.gov/hf/v1/metadata/mnemonics?dataset=fpf
      -> [{"mnemonic": ..., "series_name": ...}, ...]  (329 mnemonics)
  GET https://data.financialresearch.gov/hf/v1/series/timeseries?mnemonic=<id>
      -> [[date, value], ...], quarterly, 2013 -> present

OFR's API docs (financialresearch.gov/hedge-fund-monitor/api) state no tokens
or registration; they ask only that clients not poll more than daily (the data
itself updates at most daily, and the fpf series are quarterly).
"""
from __future__ import annotations

import json
from datetime import date

from collector.config import OfrSeriesCfg
from collector.http import GetText
from collector.store import Store

BASE = "https://data.financialresearch.gov/hf/v1"


def parse_timeseries(text: str) -> list[tuple[date, float]]:
    rows = json.loads(text)
    out = []
    for row in rows:
        try:
            d = date.fromisoformat(row[0])
            v = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue  # a malformed point must not fail the series
        out.append((d, v))
    if not out:
        raise ValueError("ofr timeseries contained no usable points")
    out.sort(key=lambda p: p[0])
    return out


async def fetch_mnemonic(mnemonic: str, get_text: GetText) -> list[tuple[date, float]]:
    return parse_timeseries(
        await get_text(f"{BASE}/series/timeseries", params={"mnemonic": mnemonic})
    )


async def fetch_ofr(
    series: list[OfrSeriesCfg], store: Store, get_text: GetText
) -> str:
    """Daily-max job: raw history for every configured OFR mnemonic.

    Raw values are stored; each series is fetched independently — one bad
    mnemonic must not starve the others.
    """
    errors: list[str] = []
    for cfg in series:
        try:
            store.upsert_points(
                f"ofr:{cfg.mnemonic}", await fetch_mnemonic(cfg.mnemonic, get_text)
            )
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(series)} ofr series failed: {'; '.join(errors)}"
        )
    return "ofr"
