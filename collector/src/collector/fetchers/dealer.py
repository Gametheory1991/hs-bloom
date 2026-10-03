"""NY Fed Primary Dealer Statistics via the Markets Data API (keyless).

Endpoints (verified live 2026-10-03):
  GET https://markets.newyorkfed.org/api/pd/get/{keyid}.json
  -> {"pd":{"timeseries":[{"asofdate":"2013-04-03","keyid":"...","value":"108014"}]}}
Values are $millions, Wednesday levels, weekly (released ~1 week later).
Series list: /api/pd/list/timeseries.json (1539 series, FR 2004 survey).

Wired series (all verified live):
  PDPOSGST-TOT  net outright U.S. Treasury securities (ex-TIPS) dealer position
  PDFTD-USTET   U.S. Treasury securities (ex-TIPS) fails to deliver
  PDFTR-USTET   U.S. Treasury securities (ex-TIPS) fails to receive

Each series is fetched independently — one bad keyid must not starve the
others. Stored as dealer:<id>.
"""
from __future__ import annotations

import json
from datetime import date

from collector.config import DealerSeriesCfg
from collector.http import GetText
from collector.store import Store

BASE = "https://markets.newyorkfed.org"


def parse_timeseries(text: str) -> list[tuple[date, float]]:
    try:
        rows = json.loads(text)["pd"]["timeseries"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"dealer payload not in expected shape: {exc}") from exc
    out = []
    for row in rows:
        try:
            d = date.fromisoformat(row["asofdate"])
            v = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue  # a malformed point must not fail the series
        out.append((d, v))
    if not out:
        raise ValueError("dealer timeseries contained no usable points")
    out.sort(key=lambda p: p[0])
    return out


async def fetch_series(keyid: str, get_text: GetText) -> list[tuple[date, float]]:
    return parse_timeseries(await get_text(f"{BASE}/api/pd/get/{keyid}.json"))


async def fetch_dealer(
    series: list[DealerSeriesCfg], store: Store, get_text: GetText
) -> str:
    """Weekly job: raw history for every configured NY Fed PD timeseries."""
    errors: list[str] = []
    for cfg in series:
        try:
            store.upsert_points(
                f"dealer:{cfg.id}", await fetch_series(cfg.keyid, get_text)
            )
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(series)} dealer series failed: {'; '.join(errors)}"
        )
    return "dealer"
