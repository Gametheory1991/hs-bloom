"""OFR Short-Term Funding Monitor API (keyless).

Base: https://data.financialresearch.gov/v1  (verified live 2026-10-05)

Endpoints:
  GET /v1/metadata/mnemonics        -> ["FNYR-SOFR-A", ...] (442 mnemonics)
  GET /v1/metadata/query?mnemonic=  -> metadata incl. unit (USD raw, magnitude 0)
  GET /v1/series/timeseries?mnemonic=<id> -> [[date, value], ...]

Series tracked (all verified live 2026-10-05):
  FNYR-SOFR-A            daily SOFR (%, 2018-04-02 ->; 3.88 on 2026-10-02)
  REPO-DVP_AR_TOT-P      FICC DVP cleared-repo average rate, preliminary, daily (%)
  MMF-MMF_TOT-M          money-market-fund total assets, monthly (raw USD; $8.53T Aug 2026)
  NYPD-PD_AFtD_TOT-A     primary-dealer fails to deliver, weekly (raw USD; $308.2B w/e 2026-09-23)
  NYPD-PD_AFtR_TOT-A     primary-dealer fails to receive, weekly (raw USD)
  NYPD-PD_RP_TOT-A       primary-dealer repo financing, weekly (raw USD)
  NYPD-PD_RRP_TOT-A      primary-dealer reverse-repo financing, weekly (raw USD)

OFR asks clients not to poll more than daily; the series themselves are
daily/weekly/monthly — the job runs daily and upserts whatever is new.
Same [[date, value]] shape as the HF Monitor, so parsing mirrors ofr.py.
"""
from __future__ import annotations

import logging
from datetime import date

from collector.config import OfrSeriesCfg
from collector.fetchers.ofr import parse_timeseries
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

BASE = "https://data.financialresearch.gov/v1"
SOURCE = "ofr-stfm"


async def fetch_mnemonic(mnemonic: str, get_text: GetText) -> list[tuple[date, float]]:
    return parse_timeseries(
        await get_text(f"{BASE}/series/timeseries", params={"mnemonic": mnemonic})
    )


async def fetch_ofr_stfm(
    series: list[OfrSeriesCfg], store: Store, get_text: GetText
) -> str:
    """Daily job: raw history for every configured STFM mnemonic.

    Each series is fetched independently — one bad mnemonic must not starve
    the others. Stored as cycle:<id> (the cycle job skips `external` rows).
    """
    errors: list[str] = []
    for cfg in series:
        try:
            pts = await fetch_mnemonic(cfg.mnemonic, get_text)
            store.upsert_points(f"cycle:{cfg.id}", pts)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if series and not errors:
        latest = None
        for cfg in series:
            pts = store.points(f"cycle:{cfg.id}")
            if pts:
                m = max(pts)
                latest = m if latest is None or m > latest else latest
        store.put_doc("ofr_stfm", {
            "as_of": latest.isoformat() if latest else None,
            "series": [cfg.id for cfg in series],
        }, source=SOURCE)
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(series)} STFM series failed: {'; '.join(errors)}"
        )
    return SOURCE
