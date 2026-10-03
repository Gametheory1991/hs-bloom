"""Treasury International Capital — Major Foreign Holders of Treasury Securities.

Current file (verified live 2026-10-03, current through 2026-07):
  https://ticdata.treasury.gov/Publish/slt_table5.txt
Tab-delimited, CRLF line endings, monthly, ~6-week lag, values in $bn:
  Country<TAB>2026-07<TAB>2026-06<TAB>...   (13 month columns, newest first)

NOTE: the legacy https://ticdata.treasury.gov/Publish/mfh.txt is frozen at
Jan 2023 (S-form era) — do not use it.
"""
from __future__ import annotations

from datetime import date

from collector.config import TicCfg
from collector.http import GetText
from collector.store import Store

URL = "https://ticdata.treasury.gov/Publish/slt_table5.txt"
GRAND_TOTAL = "Grand Total"


def slug(name: str) -> str:
    return name.lower().replace(",", "").replace(" ", "_").replace("__", "_")


def parse_table(
    text: str, countries: list[str]
) -> dict[str, list[tuple[date, float]]]:
    """Parse the MFH tab-delimited table.

    Returns {country_name: [(date, value), ...]} for the requested countries
    plus "Grand Total". Dates are the first of each YYYY-MM header month.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    header_idx = next(
        i for i, line in enumerate(lines) if line.startswith("Country\t")
    )
    months = lines[header_idx].split("\t")[1:]
    dates = [date(int(m[:4]), int(m[5:7]), 1) for m in months]
    wanted = set(countries) | {GRAND_TOTAL}
    out: dict[str, list[tuple[date, float]]] = {}
    for line in lines[header_idx + 1 :]:
        if not line.strip():
            continue
        if line.startswith("Notes:"):
            break  # footnotes follow the data rows
        parts = line.split("\t")
        name = parts[0].strip()
        if name not in wanted:
            continue
        pts: list[tuple[date, float]] = []
        for d, raw in zip(dates, parts[1 : 1 + len(dates)]):
            raw = raw.strip()
            if not raw or raw in ("n.a.", "--"):
                continue
            try:
                pts.append((d, float(raw)))
            except ValueError:
                continue  # a malformed cell must not fail the row
        if pts:
            out[name] = pts
    if GRAND_TOTAL not in out:
        raise ValueError("tic table contained no Grand Total row")
    return out


async def fetch_tic(cfg: TicCfg, store: Store, get_text: GetText) -> str:
    """Weekly job (monthly data): foreign holder history, stored in $bn as
    tic:<slug> (e.g. tic:japan, tic:grand_total)."""
    data = parse_table(await get_text(URL), cfg.countries)
    for name, pts in data.items():
        pts.sort(key=lambda p: p[0])
        store.upsert_points(f"tic:{slug(name)}", pts)
    return "tic"
