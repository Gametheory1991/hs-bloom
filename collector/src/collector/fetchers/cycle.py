"""Daily market-cycle job: one series list, fifteen source kinds, per-series
isolation (a bad id or a dead file URL degrades that series only — the
summary error raises at the end so /healthz surfaces it)."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

log = logging.getLogger(__name__)

from collector.config import CycleSeriesCfg
from collector.fetchers import aaii, cboe, cftc, dbnomics, eia, fred, oecd, ofr, wei, yahoo
from collector.http import GetBytes, GetText
from collector.store import Store

# Thin-history bootstrap thresholds: a full pull runs only when the stored
# series has fewer points than this (preserves the full-history bar); once
# populated, incremental pulls keep it topped up idempotently.
CBOE_THIN_POINTS = 20   # ~one month of weekdays; 30d walk yields ~22
YAHOO_THIN_POINTS = 100  # ~5 months of daily closes


def _yahoo_range(cfg: CycleSeriesCfg, store: Store | None) -> str:
    """Incremental '5d' once the series is populated; full '10y' bootstrap
    when stored history is thin/missing (preserves the full-history bar)."""
    if store is not None:
        try:
            if len(store.points(f"cycle:{cfg.id}")) >= YAHOO_THIN_POINTS:
                return "5d"
        except Exception:  # noqa: BLE001 — degrade to bootstrap on store errors
            pass
    return "10y"


async def _cboe_points(
    cfg: CycleSeriesCfg,
    get_text: GetText,
    store: Store | None,
    cboe_daily: dict[str, tuple[date, float]] | None,
    today: date | None,
) -> list[tuple[date, float]]:
    """Derive this ratio from the once-per-run daily file; run the 30-day
    backfill walk only when stored history is thin/missing (the daily-file
    fetch may also have failed — the walk is the fallback for that)."""
    daily = (cboe_daily or {}).get(cfg.cboe)
    thin = True
    if store is not None:
        try:
            thin = len(store.points(f"cycle:{cfg.id}")) < CBOE_THIN_POINTS
        except Exception:  # noqa: BLE001 — degrade to bootstrap on store errors
            thin = True
    if thin or daily is None:
        return await cboe.fetch_ratio_history(cfg.cboe, get_text, today=today)
    return [daily]


async def _fred_pair(
    ids: list[str], fred_api_key: str, get_text: GetText, op: str
) -> list[tuple[date, float]]:
    """Combine two FRED series date-aligned: B is carried forward to each A date.

    Used for cross-frequency pairs (e.g. daily DGS10 minus monthly DE 10Y).
    """
    a = dict(await fred.fetch_series(ids[0], fred_api_key, get_text))
    b = dict(await fred.fetch_series(ids[1], fred_api_key, get_text))
    if not a or not b:
        raise ValueError(f"fred pair {ids} returned empty history")
    b_dates = sorted(b)
    out: list[tuple[date, float]] = []
    for d in sorted(a):
        prior = [bd for bd in b_dates if bd <= d]
        if not prior:
            continue
        bv = b[prior[-1]]
        if op == "spread":
            out.append((d, round(a[d] - bv, 4)))
        elif op == "ratio":
            if bv:
                out.append((d, round(a[d] / bv, 4)))
        else:
            raise ValueError(f"unknown fred pair op: {op!r}")
    if not out:
        raise ValueError(f"fred pair {ids} produced no aligned points")
    return out


async def _fetch_one(
    cfg: CycleSeriesCfg,
    fred_api_key: str,
    get_text: GetText,
    get_bytes: GetBytes,
    store: Store | None = None,
    today: date | None = None,
    cboe_daily: dict[str, tuple[date, float]] | None = None,
) -> list[tuple[date, float]]:
    if cfg.store:
        # Externally maintained series (written by another job, e.g. the
        # thirteenf job's net-flow points): re-upsert what's already stored.
        if store is None:
            raise ValueError(f"store-backed series {cfg.id} needs a Store")
        return sorted(store.points(f"cycle:{cfg.id}").items())
    if cfg.fred:
        return await fred.fetch_series(cfg.fred, fred_api_key, get_text)
    if cfg.dbnomics:
        return await dbnomics.fetch_series(cfg.dbnomics, get_text)
    if cfg.oecd:
        return await oecd.fetch_series(cfg.oecd, get_text)
    if cfg.cftc:
        return await cftc.fetch_net_noncommercial(cfg.cftc, get_text)
    if cfg.cftc_oi:
        return await cftc.fetch_open_interest(cfg.cftc_oi, get_text)
    if cfg.cboe:
        return await _cboe_points(cfg, get_text, store, cboe_daily, today)
    if cfg.aaii:
        return await aaii.fetch_spread(get_bytes)
    if cfg.ofr:
        return await ofr.fetch_mnemonic(cfg.ofr, get_text)
    if cfg.yahoo:
        return (await yahoo.fetch_chart(cfg.yahoo, get_text, range_=_yahoo_range(cfg, store))).closes
    if cfg.yahoo_ratio:
        rng = _yahoo_range(cfg, store)
        num, den = cfg.yahoo_ratio
        a = await yahoo.fetch_chart(num, get_text, range_=rng)
        b = await yahoo.fetch_chart(den, get_text, range_=rng)
        return yahoo.ratio_points(a.closes, b.closes)
    if cfg.fred_spread:
        return await _fred_pair(cfg.fred_spread, fred_api_key, get_text, "spread")
    if cfg.fred_ratio:
        return await _fred_pair(cfg.fred_ratio, fred_api_key, get_text, "ratio")
    if cfg.eia_wpsr:
        return await eia.fetch_wpsr(cfg.eia_wpsr, get_bytes)
    if cfg.wei:
        return await wei.fetch_wei(get_bytes)
    raise ValueError("no source configured")


async def fetch_cycle(
    series: list[CycleSeriesCfg],
    store: Store,
    fred_api_key: str,
    get_text: GetText,
    get_bytes: GetBytes,
    today: date | None = None,
) -> str:
    errors: list[str] = []
    last_was_yahoo = False
    # CBOE ratios all live in the SAME daily file: fetch it once per run and
    # fan out to the 6 ratio series below (the 30-day backfill walk only runs
    # for series whose stored history is thin/missing).
    cboe_daily: dict[str, tuple[date, float]] = {}
    cboe_cfgs = [c for c in series if not c.external and c.cboe]
    if cboe_cfgs:
        try:
            cboe_daily = await cboe.fetch_daily_ratios(
                sorted({c.cboe for c in cboe_cfgs}), get_text, today=today)
        except Exception as exc:  # noqa: BLE001 — per-series fallback walk covers it
            log.warning("cycle: cboe daily-file fetch failed, falling back to per-series walks: %s", exc)
    for cfg in series:
        if cfg.external:
            continue  # a dedicated job owns cycle:<id> for these
        is_yahoo = bool(cfg.yahoo or cfg.yahoo_ratio)
        # Yahoo throttles fast sequential hits (HTTP 429 storm on boot with
        # 200+ symbols). Pace consecutive Yahoo calls ~0.6s apart.
        if is_yahoo and last_was_yahoo:
            await asyncio.sleep(0.6)
        last_was_yahoo = is_yahoo
        try:
            pts = await _fetch_one(cfg, fred_api_key, get_text, get_bytes, store, today,
                                   cboe_daily=cboe_daily)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            # one retry on rate-limit after a backoff, before recording failure
            if "HTTP 429" in str(exc):
                await asyncio.sleep(15)
                try:
                    pts = await _fetch_one(cfg, fred_api_key, get_text, get_bytes, store, today,
                                           cboe_daily=cboe_daily)
                except Exception as exc2:  # noqa: BLE001
                    errors.append(f"{cfg.id}: {exc2}")
                    continue
            else:
                errors.append(f"{cfg.id}: {exc}")
                continue
        try:
            if cfg.valid_range:
                lo, hi = cfg.valid_range
                pts = [(d, v) for d, v in pts if lo <= v <= hi]
                store.prune_outside_range(f"cycle:{cfg.id}", lo, hi)
            store.upsert_points(f"cycle:{cfg.id}", pts)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if errors:
        # Partial failure: data for successful series IS in the store (upserted
        # in the loop above). Only raise if most series failed — otherwise the
        # job would be marked failed and last_success would go stale even
        # though 95% of the data is fresh. The error details are still logged
        # and surfaced via record_error.
        total = len([c for c in series if not c.external])
        log.warning(
            "cycle: %d/%d series failed: %s", len(errors), total, "; ".join(errors[:5]))
        if len(errors) > total / 2:
            raise RuntimeError(f"{len(errors)}/{total} cycle series failed: {'; '.join(errors)}")
    return f"cycle ({len(errors)} failed)" if errors else "cycle"
