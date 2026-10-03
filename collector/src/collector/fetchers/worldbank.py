"""World Bank macro fundamentals for the 13 bond-matrix countries (keyless).

The world risk map scores countries off market prices alone; this job adds
the fundamentals underneath: real GDP growth, CPI inflation, and
unemployment — all from the World Bank's keyless v2 API, batched as one
request per indicator across all 13 countries (3 HTTP calls total):

  https://api.worldbank.org/v2/country/US;DE;FR;IT;ES;NL;BE;UK;JP;CA;AU;CH;SE
      /indicator/NY.GDP.MKTP.KD.ZG?format=json&per_page=200&mrv=10

Verified live 2026-10-03. Annual data, ~1-2 year lag on the latest vintage
(World Bank lastupdated 2026-07-13 at probe time).

Stored: wb:<CC>:gdp | wb:<CC>:cpi | wb:<CC>:unemp (annual points) plus a
`worldbank` doc with the latest value per country per indicator for the
risk map / UI.

Note on Econdb: the apivault listing claimed "No Auth", but probing
2026-10-03 showed /api/series/ returns
{"detail":"Authentication credentials were not provided."} — the time-series
data needs an API key. World Bank covers the same need keyless, so this job
uses World Bank instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "worldbank-v2"
BASE = "https://api.worldbank.org/v2/country/{countries}/indicator/{code}"

COUNTRIES = ("US", "DE", "FR", "IT", "ES", "NL", "BE", "UK", "JP", "CA", "AU", "CH", "SE")

# World Bank country codes differ from the bond-matrix labels in one place:
# the United Kingdom is "GB" at the World Bank. One bad code poisons an
# entire multi-country query ("Invalid value"), so map carefully.
WB_CODES = {"UK": "GB"}

# indicator code -> (store suffix, label)
INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": ("gdp", "GDP growth (annual %)"),
    "FP.CPI.TOTL.ZG": ("cpi", "CPI inflation (annual %)"),
    "SL.UEM.TOTL.NE.ZS": ("unemp", "Unemployment (% of labor force)"),
}

REQUEST_GAP = 2.0  # polite spacing between the three batched calls
HISTORY_YEARS = 10


def parse_indicator(text: str) -> dict[str, list[tuple[date, float]]]:
    """World Bank v2 JSON: [meta, [records]]. Returns {CC: [(date, value)]}."""
    body = json.loads(text)
    if not isinstance(body, list) or len(body) < 2 or not isinstance(body[1], list):
        raise ValueError("unexpected World Bank payload shape")
    out: dict[str, list[tuple[date, float]]] = {}
    for rec in body[1]:
        try:
            cc = rec["country"]["id"]
            val = rec.get("value")
            if val is None:
                continue
            d = date(int(rec["date"]), 1, 1)  # annual observations
            out.setdefault(cc, []).append((d, float(val)))
        except (KeyError, TypeError, ValueError):
            continue
    for pts in out.values():
        pts.sort()
    return out


async def fetch_worldbank(store: Store, get_text: GetText) -> str:
    wb_countries = [WB_CODES.get(cc, cc) for cc in COUNTRIES]
    rev = {v: k for k, v in WB_CODES.items()}  # GB -> UK for storage
    countries = ";".join(wb_countries)
    latest: dict[str, dict] = {cc: {} for cc in COUNTRIES}
    first = True
    for code, (suffix, _label) in INDICATORS.items():
        if not first:
            await asyncio.sleep(REQUEST_GAP)
        first = False
        url = (BASE.format(countries=countries, code=code)
               + f"?format=json&per_page={len(COUNTRIES) * HISTORY_YEARS}&mrv={HISTORY_YEARS}")
        try:
            by_country = parse_indicator(await get_text(url))
        except Exception as exc:  # noqa: BLE001 — one indicator never kills the job
            log.warning("worldbank %s failed: %s", code, exc)
            continue
        for cc, pts in by_country.items():
            cc = rev.get(cc, cc)
            if cc in COUNTRIES and pts:
                store.upsert_points(f"cycle:wb:{cc}:{suffix}", pts)
                latest[cc][suffix] = {"value": pts[-1][1], "date": pts[-1][0].isoformat()}
    store.put_doc("worldbank", {"as_of": date.today().isoformat(),
                                "indicators": {suffix: code for code, (suffix, _label)
                                               in INDICATORS.items()},
                                "countries": latest}, SOURCE)
    return SOURCE
