"""Mutual fund net flows — quarterly Z.1 transactions via the FRED API (keyed).

Replaces the Akamai-blocked ICI scraper as the dashboard's fund-flows source.
``ici_mutual_flows.py`` is dormant as of 2026-10-09: ici.org serves HTTP 403
to our hosts on every URL pattern and header variant tested (www/no-www,
.xls workbooks 2024-2026, landing page, browser header set, staging domain
ici-dev.ici.org). Re-enable it if the block ever lifts.

Source: Federal Reserve Z.1 Financial Accounts — "Mutual Funds; Mutual Fund
Shares; Liability, Transactions". This is the official quarterly analog of
net new cash flow: net issuance of mutual fund shares, millions of USD, not
seasonally adjusted. Series IDs verified live 2026-10-09 via FRED's search
index (all Board of Governors / Z.1 release):

  BOGZ1FU653164205Q — quarterly, 1946-10-01 -> 2026-04-01, updated Sep 11 2026
  BOGZ1FU653164205A — annual, 1946 -> 2025
  BOGZ1FU654091403Q — hybrid funds, quarterly transactions, 2023-01-01 ->

Auth: FRED API key — the same keyed path as fetchers.fred / z1_holdings,
proven working from our hosts (the keyless fredgraph.csv path is WAF-blocked).

Cadence vs ICI: quarterly with ~1 quarter publication lag (not weekly), and
total + hybrid only (not the ICI workbook's 20 categories). Labelled honestly
on every series.
"""
from __future__ import annotations

import asyncio
import logging

from collector.fetchers import fred as _fred
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "fred-z1-mf-flows"
REQUEST_GAP = 0.5  # seconds between FRED calls; tiny job, stay polite

# (series key, FRED id, label)
SERIES: tuple[tuple[str, str, str], ...] = (
    (
        "cycle:mf-flow-total",
        "BOGZ1FU653164205Q",
        "Mutual fund net share issuance, quarterly, NSA ($M)",
    ),
    (
        "cycle:mf-flow-total-annual",
        "BOGZ1FU653164205A",
        "Mutual fund net share issuance, annual, NSA ($M)",
    ),
    (
        "cycle:mf-flow-hybrid",
        "BOGZ1FU654091403Q",
        "Hybrid fund net transactions, quarterly, NSA ($M)",
    ),
)


async def fetch_mf_flows(store: Store, api_key: str, get_text: GetText) -> str:
    """Full-history upsert for every mutual-fund flow series.

    FRED API (keyed) — no ``observation_start``, so each call pulls the full
    available history, upserted idempotently. Per-series isolation: one bad ID
    or fetch failure is recorded and must not starve the other series.
    """
    errors: list[str] = []
    keys = [key for key, _fred_id, _label in SERIES]

    for key, fred_id, label in SERIES:
        try:
            pts = await _fred.fetch_series(fred_id, api_key, get_text)
            store.upsert_points(key, pts)
            log.info("mf_flows: %s (%s): %d points", key, label, len(pts))
        except Exception as exc:  # noqa: BLE001 — per-series isolation
            errors.append(f"{fred_id}: {exc}")
        await asyncio.sleep(REQUEST_GAP)

    if not errors:
        latest = None
        for key in keys:
            pts = store.points(key)
            if pts:
                m = max(pts)  # dict -> max key = latest date
                latest = m if latest is None or m > latest else latest
        store.put_doc(
            "mf_flows",
            {
                "as_of": latest.isoformat() if latest else None,
                "series": [
                    {"key": key, "fred_id": fred_id, "label": label}
                    for key, fred_id, label in SERIES
                ],
                "units": "USD millions, quarterly/annual NSA, Z.1 transactions",
                "note": (
                    "Official quarterly analog of mutual-fund net new cash "
                    "flow (net share issuance). Replaces the Akamai-blocked "
                    "ICI weekly workbook; quarterly cadence, total + hybrid."
                ),
            },
            source=SOURCE,
        )
    if errors:
        raise RuntimeError(
            f"{len(errors)}/{len(keys)} mf-flow series failed: "
            + "; ".join(errors)
        )
    return SOURCE
