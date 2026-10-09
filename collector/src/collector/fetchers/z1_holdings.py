"""FRED Z.1 holdings-by-holder fetcher (FRED API, api-key authenticated).

Endpoint: https://api.stlouisfed.org/fred/series/observations?series_id=<ID>
  (api_key + file_type=json; same auth pattern as fetchers.fred)
  -> {"observations": [{"date": ..., "value": ...}]} ("." = missing;
  quarterly levels in $ millions)

Keyless fredgraph.csv is blocked from our hosts (FRED WAF), so this fetcher
uses the API-key path exactly like fetch_macro_history.

Series verified live 2026-10-05 from the Board's official DDP-FRED crosswalk
(https://www.federalreserve.gov/data/documents/DDP-FRED%20Data%20Series%20Crosswalk.csv):
  UST (12 sectors), CORP (11), AGENCY (12), MUNI (10), plus two extras:
  Z.1 hedge-fund Treasury holdings NET of shorts, and the timelier weekly
  Fed SOMA series (TREAST).

Stored as cycle:z1-<asset>-<sector> (the cycle job skips `external` rows;
these are quarterly, so a weekly or daily job is fine — the updater is cheap
and idempotent via upsert).

METHODOLOGY / double-counting traps — read before consuming these series:
  (1) The hedge-fund series (BOGZ1FL623061103Q, $123.5B @ Q2 2026) is NET of
      short sales. NEVER present it as gross long exposure alongside OFR's
      $2.36T long-UST figure (the $1.5T short book is netted out of Z.1).
  (2) OFS-2 "Mutual Funds" (Treasury Bulletin) INCLUDES money market funds —
      if OFS-2 data is ever joined here, do not add MMF separately.
  (3) Z.1 ROW (quarterly) vs TIC Table 5 (monthly, by country) vs OFS-2
      "Foreign & International" — same foreign universe, three cadences.
      Pick one per view, cross-check with the others.
  (4) Z.1 "Mutual funds" (sector 65) = mutual funds + ETFs at market value.
  (5) The state & local government Treasury series (BOGZ1FL213061103Q)
      excludes SLGS — SLGS sits in nonmarketable debt instead.
  (6) Z.1 sector IDs are NOT pattern-guessable: several sectors use
      FRED-mnemonic IDs (ROWTSEQ027S, HNOTSAQ027S, TSABSNNCB) rather than
      BOGZ1, and variant suffixes differ by sector. Always resolve via the
      DDP-FRED crosswalk, never by pattern.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import NamedTuple

from collector.fetchers import fred as _fred
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "fred-z1"
REQUEST_GAP = 0.5  # polite gap between series fetches


class Z1Series(NamedTuple):
    """One holdings series. asset in {ust, corp, agency, muni}."""

    asset: str
    sector: str  # store-key slug
    fred_id: str
    label: str


SERIES: list[Z1Series] = [
    # --- UST (Z.1 L.209-line equivalents, quarterly, $mn) ---
    Z1Series("ust", "fed", "BOGZ1FL713061103Q", "Fed (SOMA) Treasury holdings"),
    Z1Series("ust", "banks", "BOGZ1FL763061100Q", "Banks — Treasury holdings"),
    Z1Series("ust", "mutual-funds", "BOGZ1FL653061105Q", "Mutual funds — Treasury holdings"),
    Z1Series("ust", "mmf", "BOGZ1FL633061105Q", "Money market funds — Treasury holdings"),
    Z1Series("ust", "foreign", "ROWTSEQ027S", "Foreign — Treasury holdings (Z.1 quarterly)"),
    Z1Series("ust", "priv-pension", "BOGZ1FL573061105Q", "Private pension funds — Treasury holdings"),
    Z1Series("ust", "sl-retire", "BOGZ1FL223061143Q", "SL govt retirement funds — Treasury holdings"),
    Z1Series("ust", "life-ins", "BOGZ1FL543061105Q", "Life insurers — Treasury holdings"),
    Z1Series("ust", "pc-ins", "BOGZ1FL513061105Q", "P&C insurers — Treasury holdings"),
    Z1Series("ust", "households", "HNOTSAQ027S", "Households + nonprofits — Treasury holdings"),
    Z1Series("ust", "nonfin-corp", "TSABSNNCB", "Nonfinancial corporate — Treasury holdings"),
    Z1Series("ust", "sl-govt", "BOGZ1FL213061103Q", "SL governments — Treasury holdings (ex SLGS)"),
    # --- CORP (corporate & foreign bonds, quarterly, $mn) ---
    Z1Series("corp", "banks", "BOGZ1FL763063005Q", "Banks — corporate bond holdings"),
    Z1Series("corp", "mutual-funds", "BOGZ1FL653063005Q", "Mutual funds — corporate bond holdings"),
    Z1Series("corp", "mmf", "BOGZ1FL633063005Q", "Money market funds — corporate bond holdings"),
    Z1Series("corp", "priv-pension", "BOGZ1FL573063005Q", "Private pension funds — corporate bond holdings"),
    Z1Series("corp", "life-ins", "BOGZ1FL543063005Q", "Life insurers — corporate bond holdings"),
    Z1Series("corp", "pc-ins", "BOGZ1FL513063005Q", "P&C insurers — corporate bond holdings"),
    Z1Series("corp", "foreign", "ROWCBSQ027S", "Foreign — corporate bond holdings"),
    Z1Series("corp", "households", "CFBABSHNO", "Households + nonprofits — corporate bond holdings"),
    Z1Series("corp", "nonfin-corp", "BOGZ1FL103063065Q", "Nonfinancial corporate — corporate bond holdings"),
    Z1Series("corp", "sl-govt", "SLGCORQ027S", "SL governments — corporate bond holdings"),
    Z1Series("corp", "sl-retire", "BOGZ1FL223063045Q", "SL govt retirement funds — corporate bond holdings"),
    # --- AGENCY (agency- and GSE-backed securities, quarterly, $mn) ---
    Z1Series("agency", "fed", "BOGZ1FL713061705Q", "Fed (SOMA) — agency MBS holdings"),
    Z1Series("agency", "banks", "BOGZ1FL763061705Q", "Banks — agency securities holdings"),
    Z1Series("agency", "mutual-funds", "BOGZ1FL653061703Q", "Mutual funds — agency securities holdings"),
    Z1Series("agency", "mmf", "BOGZ1FL633061700Q", "Money market funds — agency securities holdings"),
    Z1Series("agency", "priv-pension", "BOGZ1FL573061705Q", "Private pension funds — agency securities holdings"),
    Z1Series("agency", "sl-retire", "BOGZ1FL223061743Q", "SL govt retirement funds — agency securities holdings"),
    Z1Series("agency", "life-ins", "BOGZ1FL543061705Q", "Life insurers — agency securities holdings"),
    Z1Series("agency", "pc-ins", "BOGZ1FL513061705Q", "P&C insurers — agency securities holdings"),
    Z1Series("agency", "foreign", "ROWGBSQ027S", "Foreign — agency securities holdings"),
    Z1Series("agency", "households", "AGSEBSABSHNO", "Households + nonprofits — agency securities holdings"),
    Z1Series("agency", "nonfin-corp", "AGSEBSABSNNCB", "Nonfinancial corporate — agency securities holdings"),
    Z1Series("agency", "sl-govt", "SLGGBSQ027S", "SL governments — agency securities holdings"),
    # --- MUNI (municipal securities, quarterly, $mn) ---
    Z1Series("muni", "banks", "BOGZ1FL763062005Q", "Banks — municipal bond holdings"),
    Z1Series("muni", "mutual-funds", "BOGZ1FL653062003Q", "Mutual funds — municipal bond holdings"),
    Z1Series("muni", "mmf", "BOGZ1FL633062000Q", "Money market funds — municipal bond holdings"),
    Z1Series("muni", "life-ins", "BOGZ1FL543062005Q", "Life insurers — municipal bond holdings"),
    Z1Series("muni", "pc-ins", "BOGZ1FL513062005Q", "P&C insurers — municipal bond holdings"),
    Z1Series("muni", "foreign", "ROWMLAQ027S", "Foreign — municipal bond holdings"),
    Z1Series("muni", "households", "MSABSHNO", "Households + nonprofits — municipal bond holdings"),
    Z1Series("muni", "nonfin-corp", "MSABSNNCB", "Nonfinancial corporate — municipal bond holdings"),
    Z1Series("muni", "sl-govt", "SLGMLOQ027S", "SL governments — municipal bond holdings"),
    # verified zero — economically correct: tax-exempt pensions don't buy
    # tax-exempt bonds. Kept so consumers see the sector exists, not a gap.
    Z1Series("muni", "sl-retire", "BOGZ1FL223062043Q", "SL govt retirement funds — municipal bond holdings (zero)"),
]

# Extras: not part of the (asset, sector) grid — they get their own keys.
EXTRA_SERIES: list[tuple[str, str, str]] = [
    # (store_key, fred_id, label)
    (
        "cycle:z1-ust-hedge-funds-net",
        "BOGZ1FL623061103Q",
        "Hedge funds — Treasury holdings NET of shorts (Z.1)",
    ),
    (
        "cycle:fed-soma-weekly",
        "TREAST",
        "Fed SOMA Treasury holdings (FRED weekly, timeliest)",
    ),
]


def store_key(cfg: Z1Series) -> str:
    return f"cycle:z1-{cfg.asset}-{cfg.sector}"


async def fetch_series(fred_id: str, api_key: str, get_text: GetText) -> list[tuple[date, float]]:
    """Fetch full series history via the FRED API — same auth path as
    fetchers.fred.fetch_series (api_key + file_type=json params)."""
    return await _fred.fetch_series(fred_id, api_key, get_text)


async def fetch_z1_holdings(store: Store, api_key: str, get_text: GetText) -> str:
    """Full-history upsert for every Z.1 holdings series.

    FRED API (keyed) — no `observation_start`, so each call pulls the full
    available history, upserted idempotently.

    Per-series isolation: one bad ID or fetch failure is recorded in the
    error list and must not starve the other series. Returns the source tag.
    """
    errors: list[str] = []
    keys: list[str] = []

    for cfg in SERIES:
        key = store_key(cfg)
        keys.append(key)
        try:
            pts = await fetch_series(cfg.fred_id, api_key, get_text)
            store.upsert_points(key, pts)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.fred_id}: {exc}")
        await asyncio.sleep(REQUEST_GAP)

    for key, fred_id, _label in EXTRA_SERIES:
        keys.append(key)
        try:
            pts = await fetch_series(fred_id, api_key, get_text)
            store.upsert_points(key, pts)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{fred_id}: {exc}")
        await asyncio.sleep(REQUEST_GAP)

    if not errors:
        latest: date | None = None
        for key in keys:
            pts = store.points(key)
            if pts:
                m = max(pts)
                latest = m if latest is None or m > latest else latest
        store.put_doc(
            "z1_holdings",
            {
                "as_of": latest.isoformat() if latest else None,
                "series": keys,
                "units": "USD millions, quarterly (fed-soma-weekly is weekly)",
            },
            source=SOURCE,
        )
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(keys)} Z.1 series failed: {'; '.join(errors)}"
        )
    return SOURCE
