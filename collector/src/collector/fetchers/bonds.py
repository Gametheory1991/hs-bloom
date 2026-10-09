"""Government yields (10Y + 3M) and central bank policy rates.

Chain per bond: FRED (US) -> Bundesbank (DE 10Y) -> ECB (euro-area 3M curve).
CB rates use the same FRED fetcher.

UK gilts are deliberately absent: the only keyless daily source is the BoE
IADB CSV export, whose path robots.txt disallows. Rather than ship a fetcher
that every user would be running against that directive, the UK row is out.

Writes history to 'yield:{country}{tenor}' / 'cb:{country}' and latest to the
'bond_quotes' doc keyed '{country}{tenor}' ('US10Y', 'US3M', 'USCB'):
{country, tenor|label, yield_pct, ts, source}. Instruments that fail this run
keep their last-known quote (stale beats gone) — but only instruments still
in config, because the panels layer iterates the doc's keys. Pre-matrix docs
were keyed by bare country; those keys fall out of `wanted` and are dropped.
"""
from __future__ import annotations

import logging
import time

from collector.config import BondCfg, CbRateCfg
from collector.fetchers import bundesbank, ecb, fred
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

# Per upstream-id refetch floor: the bonds job runs hourly, but every series
# here is daily-or-slower (FRED daily yields, FRED monthly OECD harmonized
# yields, Bundesbank daily, ECB daily). Don't re-pull an upstream id more
# than ~daily; the hourly cadence stays so a fresh publish is picked up
# within the hour after the floor expires. Survives restarts via the
# 'bond_fetch_state' doc.
FETCH_MIN_GAP = 20 * 3600
_STATE_DOC = "bond_fetch_state"


async def _daily_series(
    cfg: BondCfg | CbRateCfg, get_text: GetText, fred_api_key: str
) -> tuple[list, str] | None:
    """(closes, source) via the keyless-source chain, or None if unconfigured."""
    if cfg.fred:
        return await fred.fetch_series(cfg.fred, fred_api_key, get_text), "fred"
    if getattr(cfg, "bundesbank", None):
        return await bundesbank.fetch_series(cfg.bundesbank, get_text), "bundesbank"
    if getattr(cfg, "ecb", None):
        return await ecb.fetch_series(cfg.ecb, get_text), "ecb"
    return None


def _source_key(cfg: BondCfg | CbRateCfg) -> tuple[str, str] | None:
    """The distinct upstream id behind a row (FRED id, or Bundesbank/ECB key).

    Several rows share one id (e.g. ECBDFR feeds DE/FR/IT/ES/NL/BE policy
    rates): fetch each distinct id once per run and fan out to its rows.
    """
    if cfg.fred:
        return ("fred", cfg.fred)
    if getattr(cfg, "bundesbank", None):
        return ("bundesbank", cfg.bundesbank)
    if getattr(cfg, "ecb", None):
        return ("ecb", cfg.ecb)
    return None


async def fetch_bonds(
    bonds: list[BondCfg],
    cb_rates: list[CbRateCfg],
    store: Store,
    get_text: GetText,
    fred_api_key: str,
) -> str:
    instruments = [(f"{b.country}{b.tenor}", f"yield:{b.country}{b.tenor}", b) for b in bonds]
    instruments += [(f"{c.country}CB", f"cb:{c.country}", c) for c in cb_rates]
    wanted = {key for key, _, _ in instruments}
    prev = store.doc("bond_quotes")
    quotes: dict[str, dict] = (
        {k: q for k, q in prev.payload.items() if k in wanted} if prev else {}
    )

    # Group rows by distinct upstream id: fetch each id ONCE and fan out the
    # result to its country rows (ECBDFR alone feeds 6 policy-rate rows).
    groups: dict[tuple[str, str], list[tuple[str, str, BondCfg | CbRateCfg]]] = {}
    errors: list[str] = []
    for key, series_id, cfg in instruments:
        sk = _source_key(cfg)
        if sk is None:
            errors.append(f"{key}: no source configured")
            continue
        groups.setdefault(sk, []).append((key, series_id, cfg))

    state_doc = store.doc(_STATE_DOC)
    state: dict = dict(state_doc.payload) if state_doc else {}
    now = time.time()

    sources_used: set[str] = set()
    fetched = 0
    attempted = 0

    for (kind, upstream_id), rows in groups.items():
        state_key = f"{kind}:{upstream_id}"
        last = state.get(state_key) or {}
        try:
            last_ts = float(last.get("ts", 0))
        except (TypeError, ValueError):
            last_ts = 0.0
        if now - last_ts < FETCH_MIN_GAP:
            continue  # daily-or-slower id already pulled this cycle: skip
        attempted += 1
        try:
            closes, source = await _daily_series(rows[0][2], get_text, fred_api_key)
            last_d, last_v = closes[-1]
            ts = f"{last_d.isoformat()}T00:00:00Z"
        except Exception as exc:  # noqa: BLE001 — one id must not kill the run
            log.warning("bond fetch failed for %s: %s", upstream_id, exc)
            for key, _, _ in rows:
                errors.append(f"{key}: {exc}")
            continue
        for key, series_id, cfg in rows:
            is_bond = isinstance(cfg, BondCfg)
            store.upsert_points(series_id, closes)
            quote = {"country": cfg.country, "yield_pct": last_v, "ts": ts,
                     "source": source}
            if is_bond:
                quote["tenor"] = cfg.tenor
            else:
                quote["label"] = cfg.label
            quotes[key] = quote
        state[state_key] = {"ts": now, "source": source}
        sources_used.add(source)
        fetched += 1

    if attempted and not fetched:
        raise RuntimeError(f"all bonds failed: {'; '.join(errors)}")
    if not fetched:
        # Every id was fresh: nothing re-pulled, quotes unchanged.
        return "bonds (fresh)"
    store.put_doc("bond_quotes", quotes, source="+".join(sorted(sources_used)))
    store.put_doc(_STATE_DOC, state, source="bonds")
    return "+".join(sorted(sources_used))
