"""Daily market-cycle job: one series list, fifteen source kinds, per-series
isolation (a bad id or a dead file URL degrades that series only — the
summary error raises at the end so /healthz surfaces it)."""
from __future__ import annotations

from datetime import date

from collector.config import CycleSeriesCfg
from collector.fetchers import aaii, cboe, cftc, dbnomics, eia, fred, oecd, ofr, wei, yahoo
from collector.http import GetBytes, GetText
from collector.store import Store


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
        return await cboe.fetch_ratio_history(cfg.cboe, get_text, today=today)
    if cfg.aaii:
        return await aaii.fetch_spread(get_bytes)
    if cfg.ofr:
        return await ofr.fetch_mnemonic(cfg.ofr, get_text)
    if cfg.yahoo:
        return (await yahoo.fetch_chart(cfg.yahoo, get_text, range_="10y")).closes
    if cfg.yahoo_ratio:
        num, den = cfg.yahoo_ratio
        a = await yahoo.fetch_chart(num, get_text, range_="10y")
        b = await yahoo.fetch_chart(den, get_text, range_="10y")
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
    for cfg in series:
        try:
            pts = await _fetch_one(cfg, fred_api_key, get_text, get_bytes, store, today)
            if cfg.valid_range:
                lo, hi = cfg.valid_range
                pts = [(d, v) for d, v in pts if lo <= v <= hi]
                store.prune_outside_range(f"cycle:{cfg.id}", lo, hi)
            store.upsert_points(f"cycle:{cfg.id}", pts)
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{cfg.id}: {exc}")
    if errors:
        raise RuntimeError(f"{len(errors)}/{len(series)} cycle series failed: {'; '.join(errors)}")
    return "cycle"
