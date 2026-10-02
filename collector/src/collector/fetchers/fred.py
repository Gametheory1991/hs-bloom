"""FRED observations API. Used for macro series history and the US 10Y yield."""
from __future__ import annotations

import json
from datetime import date

from collector.config import SeriesCfg
from collector.fetchers import fred_mcp
from collector.http import GetText
from collector.store import Store

BASE = "https://api.stlouisfed.org/fred/series/observations"


def source_name() -> str:
    return "fred-mcp" if fred_mcp.endpoint() else "fred"


def parse_observations(text: str) -> list[tuple[date, float]]:
    out = []
    for obs in json.loads(text)["observations"]:
        if obs["value"] == ".":  # FRED's marker for missing data
            continue
        out.append((date.fromisoformat(obs["date"]), float(obs["value"])))
    return out


async def fetch_series(fred_id: str, api_key: str, get_text: GetText) -> list[tuple[date, float]]:
    if fred_mcp.endpoint():
        try:
            return parse_observations(await fred_mcp.fetch_observations(fred_id, api_key))
        except Exception:
            raise RuntimeError(
                "FRED MCP request failed; check endpoint, credentials and observations tool"
            ) from None
    params = {"series_id": fred_id, "api_key": api_key, "file_type": "json"}
    return parse_observations(await get_text(BASE, params=params))


async def fetch_macro_history(
    series: list[SeriesCfg], store: Store, api_key: str, get_text: GetText
) -> str:
    """Daily job: raw history for every configured macro series.

    Raw values are stored; transforms are applied at read time by the API,
    so a transform change never requires a refetch. Each series is fetched
    independently — one bad FRED id must not starve the others.
    """
    errors: list[str] = []
    for cfg in series:
        try:
            store.upsert_points(f"macro:{cfg.id}", await fetch_series(cfg.fred, api_key, get_text))
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if errors:
        raise RuntimeError(f"{len(errors)}/{len(series)} macro series failed: {'; '.join(errors)}")
    return source_name()
