"""ETF catalog puller using the pyetfdb-scraper universe export."""
from __future__ import annotations

import json

from collector.http import GetText
from collector.store import Store


def _normalize_row(row: dict) -> dict | None:
    raw_symbol = row.get("symbol")
    raw_name = row.get("name")
    if not isinstance(raw_symbol, str) or not isinstance(raw_name, str):
        return None
    symbol = raw_symbol.strip().upper()
    name = raw_name.strip()
    url = str(row.get("url", "")).strip()
    if not symbol or not name:
        return None
    return {
        "symbol": symbol,
        "name": name,
        "url": url,
        "search": f"{symbol} {name}".lower(),
        "one_week_return": row.get("one_week_return"),
        "one_year_return": row.get("one_year_return"),
        "three_year_return": row.get("three_year_return"),
        "five_year_return": row.get("five_year_return"),
    }


async def fetch_etf_catalog(catalog_url: str, store: Store, get_text: GetText) -> str:
    body = json.loads(await get_text(catalog_url))
    if not isinstance(body, list):
        raise RuntimeError("ETF catalog payload is not a list")

    rows: list[dict] = []
    seen: set[str] = set()
    for raw in body:
        if not isinstance(raw, dict):
            continue
        row = _normalize_row(raw)
        if row is None or row["symbol"] in seen:
            continue
        seen.add(row["symbol"])
        rows.append(row)

    if not rows:
        raise RuntimeError("ETF catalog payload had no valid rows")
    rows.sort(key=lambda r: r["symbol"])
    by_symbol = {r["symbol"]: r for r in rows}
    store.put_doc(
        "etf_catalog",
        {"rows": rows, "by_symbol": by_symbol, "count": len(rows)},
        source="etfdb",
    )
    return "etfdb"
