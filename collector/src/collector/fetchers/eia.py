"""EIA Weekly Petroleum Status Report (WPSR) table1.csv — crude stocks.

URL https://ir.eia.gov/wpsr/table1.csv 302-redirects to a signed CloudFront
URL (callers must follow redirects; collector.http.get_text does). The file
is encoded cp1252, NOT UTF-8 — decode bytes explicitly, never via get_text.

Layout: first data row is a header ("STUB_1","9/25/26",... week dates).
Values are millions of barrels printed directly (no scaling). We parse:
  - "Strategic Petroleum Reserve (SPR)" -> spr stocks (MMBbls)
  - "Commercial (Excluding SPR)"        -> commercial crude stocks (MMBbls)

Replaces the dead FRED series WCSSTUS1/WCESTUS1 (both 404 on FRED).
"""
from __future__ import annotations

from datetime import date, datetime

from collector.http import GetBytes

TABLE1_URL = "https://ir.eia.gov/wpsr/table1.csv"

SPR_LABEL = "Strategic Petroleum Reserve (SPR)"
COMMERCIAL_LABEL = "Commercial (Excluding SPR)"


def _parse_week(s: str) -> date:
    # "9/25/26" -> 2026-09-25
    return datetime.strptime(s.strip().strip('"'), "%m/%d/%y").date()


def parse_table1(raw: bytes) -> dict[str, list[tuple[date, float]]]:
    """Parse WPSR table1.csv bytes -> {"spr": [...], "commercial": [...]}.

    Points are (week-ending date, MMBbls), oldest first. Only the latest-week
    column is kept per row (column 2 of the CSV).
    """
    text = raw.decode("cp1252")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("eia table1.csv is empty")
    header = [c.strip().strip('"') for c in lines[0].split(",")]
    try:
        week = _parse_week(header[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"eia table1.csv header has no week date: {lines[0][:80]!r}") from exc
    out: dict[str, list[tuple[date, float]]] = {"spr": [], "commercial": []}
    for ln in lines[1:]:
        cells = [c.strip().strip('"') for c in ln.split(",")]
        if len(cells) < 2:
            continue
        try:
            value = float(cells[1].replace(",", ""))
        except ValueError:
            continue
        if cells[0] == SPR_LABEL:
            out["spr"] = [(week, value)]
        elif cells[0] == COMMERCIAL_LABEL:
            out["commercial"] = [(week, value)]
    if not out["spr"] or not out["commercial"]:
        raise ValueError("eia table1.csv missing SPR or commercial stocks row")
    return out


async def fetch_wpsr(which: str, get_bytes: GetBytes) -> list[tuple[date, float]]:
    """Fetch one stocks series: which in {"spr", "commercial"}."""
    if which not in ("spr", "commercial"):
        raise ValueError(f"unknown eia_wpsr series: {which!r}")
    data = await get_bytes(TABLE1_URL)
    return parse_table1(data)[which]
