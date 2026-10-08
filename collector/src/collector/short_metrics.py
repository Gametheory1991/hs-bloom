"""Computed short-selling stress metrics.

Derived from the FINRA short-interest data (finra_short fetcher) already in
the store — no new external fetches.

- cycle:short-days-to-cover : market-wide days to cover =
  SUM(short shares) / SUM(ADV) per settlement date, from the short_interest
  table. Biweekly settlements.
- cycle:short-interest-vel7 : 7-calendar-day % change in total short interest.
- cycle:short-interest-vel30: 30-calendar-day % change in total short interest.

Settlement data is biweekly, so vel7 is usually None (no two settlements
within 7 days) while vel30 captures the settlement-to-settlement move.
Both are honest about what they measure.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collector.store import Store

SOURCE = "computed:short_metrics"


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return 100.0 * (new - old) / abs(old)


def refresh_short_metrics(store: Store) -> str:
    """Compute days-to-cover and SI velocity series. Idempotent."""
    # --- days to cover: SUM(short)/SUM(adv) per settlement ---
    rows = store._execute(
        "SELECT settlement_date, SUM(short), SUM(adv) FROM short_interest "
        "GROUP BY settlement_date ORDER BY settlement_date"
    )
    dtc_points: list[tuple[date, float]] = []
    for sdate, short_sum, adv_sum in rows:
        if short_sum and adv_sum:
            d = date.fromisoformat(sdate) if isinstance(sdate, str) else sdate
            dtc_points.append((d, short_sum / adv_sum))
    if dtc_points:
        store.upsert_points("cycle:short-days-to-cover", dtc_points)

    # --- SI velocity: % change in total short interest over 7/30d ---
    total = store.points("cycle:finra-short-total")
    if total:
        dates = sorted(total.keys())
        vel7: list[tuple[date, float]] = []
        vel30: list[tuple[date, float]] = []
        by_iso = {d.isoformat() if isinstance(d, date) else d: v
                  for d, v in total.items()}
        for d in dates:
            diso = d.isoformat() if isinstance(d, date) else d
            cur = total[d]
            for n, out in ((7, vel7), (30, vel30)):
                # most recent observation in (d-n, d]
                base_d = d - timedelta(days=n) if isinstance(d, date) else None
                cands = [x for x in dates
                         if (base_d is not None and base_d < x < d)]
                if cands:
                    chg = _pct_change(cur, total[cands[-1]])
                    if chg is not None:
                        out.append((d, chg))
        if vel7:
            store.upsert_points("cycle:short-interest-vel7", vel7)
        if vel30:
            store.upsert_points("cycle:short-interest-vel30", vel30)

    return (f"short-metrics: {len(dtc_points)} dtc settlements, "
            f"{len(total)} total-SI points")
