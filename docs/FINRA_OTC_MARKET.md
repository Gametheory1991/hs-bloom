# FINRA OTC Market (otce.finra.org)

Over-the-counter equities data from FINRA's OTC market site, via the
**keyless** `api.finra.org` Query API (group `otcMarket`) — the same
endpoint the otce.finra.org site calls directly from the browser.
No authentication required. Verified working 2026-10-06.

## Datasets (all 8 ingested)

| Dataset | Cadence | History | Rows |
|---|---|---|---|
| `YearlyMarketStatistics` | annual | 2014 → 2025 | 114 |
| `monthlyMarketStatistics` | monthly | 2014-05 → 2026-08 | 1,360 |
| `monthlyTop100` | monthly | 2014-05 → present | ~100/mo |
| `otcDailyList` | daily | rolling 45d window, incremental | varies |
| `thresholdList` | daily | rolling 30d window, incremental | varies |
| `tradingHaltsCurrent` | snapshot | current | varies |
| `otcSecurityMaster` | snapshot | current (~10k symbols) | ~10,000 |
| `MarketParticipantList` | snapshot | current | ~hundreds |

Note: FINRA changed its market taxonomy over time — early data uses
"Other OTC", later data uses "All OTC" / "OTC Equity"; security-type
casing also varies ("Domestic" vs "DOMESTIC"). Series are stored per
raw combo (slugged), not merged, so history is never rewritten.

## Storage

Series (`cycle:otc-…`):
- `otc-yearly-{mkt}-{sec}-{metric}` — annual points (Jan 1)
- `otc-monthly-{mkt}-{sec}-{metric}` — monthly points
- `otc-monthly-total-shares / -dollarvol / -trades` — market-wide monthly
- `otc-top100-total-shares / -dollarvol` — monthly top-100 aggregates

Docs:
- `otc-top100-{YYYY-MM}` — top-100 rows for the month
- `otc-dailylist-{YYYY-MM-DD}` — categorized events
  (additions / deletions / symbol_changes / bankruptcy / dividends /
  attribute_changes / other)
- `otc-threshold-{YYYY-MM-DD}` — threshold securities snapshot
- `otc-halts-current` — current halts/resumes
- `otc-secmaster` — symbol → {name, issuer, market, type} map
- `otc-mplist` — MPID → name map
- `finra_otc` — job status

## UI

EQUITY → **OTC Market** (`#/equity/otc`):
1. Monthly totals trend chart (shares vs dollar volume, uPlot)
2. Top-100 issues table with month selector (back to 2014-05)
3. Annual statistics table
4. Daily-list corporate-action feed
5. Current trading halts
6. OTC threshold securities

API: `/api/dashboard` panel `otc`; `/api/series/otc:…` for chart series;
`/api/otc/top100?month=YYYY-MM` for historical top-100 months.

## Source

- https://otce.finra.org (menu: Daily List, Equity Short Interest,
  Market Statistics, OTC Threshold, Trading Halts, Symbol Directory,
  UPC Advisory Notices)
- Query API docs: https://developer.finra.org/docs
