"""Federal Reserve Board risk releases via FRED (SLOOS + Commercial Paper).

Why FRED and not the Board's Data Download Program (DDP): on 2026-07-16 the
Board announced it will remove the DDP "Build Your Package" option the week
of November 9, 2026, in preparation for retiring the DDP entirely, and
directs users to FRED (verified on the DDP Choose page 2026-10-05). The
series below are all sourced from the Board of Governors on FRED, so the
Board remains the data source; FRED is the Board-endorsed delivery path.

Series (all verified live on FRED 2026-10-05):
  SLOOS (quarterly, net % of domestic banks tightening, NSA):
    DRTSCILM  C&I loans to large and middle-market firms (Q3 2026: 0.0)
    DRTSCIS   C&I loans to small firms (2026-07-01: 1.8)
  Commercial Paper (weekly ending Wednesday, $bn, seasonally adjusted,
  Board "Commercial Paper" release):
    DFINCP   domestic financial        FFINCP  foreign financial
    ABCOMP   asset-backed              NFINCP  nonfinancial
    OTHCOMP  other
  Financial = DFINCP + FFINCP; total = sum of all five.

The job polls weekly; SLOOS is quarterly and CP weekly — upserts are
idempotent, so a weekly run keeps both current. Stored as cycle:<id>.
"""
from __future__ import annotations

import logging
from datetime import date

from collector.config import FrbDdpCfg
from collector.fetchers import fred
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)
SOURCE = "frb-fred"


def sum_series(parts: list[list[tuple[date, float]]]) -> list[tuple[date, float]]:
    """Pointwise sum of aligned FRED histories (dates present in ALL parts).

    Requiring full alignment keeps the total honest: a partial-week sum
    would read as a drop in outstanding.
    """
    if not parts:
        return []
    common = set(d for d, _ in parts[0])
    for p in parts[1:]:
        common &= {d for d, _ in p}
    maps = [{d: v for d, v in p} for p in parts]
    return sorted((d, sum(m[d] for m in maps)) for d in common)


async def fetch_frb_ddp(
    series: list[FrbDdpCfg], store: Store, fred_api_key: str, get_text: GetText
) -> str:
    """Weekly job: Board SLOOS + CP series via FRED. Per-series isolation:
    one bad series never starves the others."""
    errors: list[str] = []
    for cfg in series:
        try:
            if cfg.fred_sum:
                parts = [await fred.fetch_series(fid, fred_api_key, get_text)
                         for fid in cfg.fred_sum]
                pts = sum_series(parts)
                if not pts:
                    raise ValueError("no commonly-dated points across components")
            elif cfg.fred:
                pts = await fred.fetch_series(cfg.fred, fred_api_key, get_text)
            else:  # pragma: no cover — config guard
                raise ValueError("neither fred nor fred_sum configured")
            store.upsert_points(f"cycle:{cfg.id}", pts)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(series)} FRB series failed: {'; '.join(errors)}"
        )
    return SOURCE
