# FINRA ATS Transparency (dark pools) — ingestion notes

**Date:** 2026-10-06
**Directive:** Harry — "ingest all and build all biggest gaps including the
dark pool feed but call it FINRA ATS transparency."
**Fetcher:** `collector/src/collector/fetchers/finra_ats.py`
**UI:** EQUITY → ATS Transparency (`#/equity/ats`)

## What was verified LIVE (no key) on 2026-10-06

- `GET https://api.finra.org/partitions/group/otcMarket/name/blocksSummary`
  → 200, **123 monthly partitions, 2016-06-01 .. 2026-08-01**.
- `POST https://api.finra.org/data/group/otcMarket/name/blocksSummary`
  → 200, 30 columns (MPID, marketParticipantName, monthStartDate,
  totalShareQuantity, totalTradeCount, ATSSharePercent, ATSTradePercent,
  ATSBlockCount/Quantity, averageTradeSize, summaryTypeCode,
  summaryTypeDescription, …).
- Full keyless ingestion ran end-to-end: 123 monthly points, 58 venue MPIDs,
  `cycle:ats-m-total-shares` / `-trades`, per-venue
  `cycle:ats-m-{mpid}-{shares,trades,sharepct}`.
- `ATS_W_FIRM`, `ATS_W_SMBL`, `ATS_W_SMBL_FIRM` (and their `*Mock` variants)
  → **HTTP 401 without auth** (dataset names confirmed to exist — 401, not 404).
- `otcBlockSummary` → **404, dataset does not exist** (despite appearing in
  a third-party catalog) — not ingested, not referenced.

## What needs the FINRA API key (not yet verified)

- OAuth2 client-credentials at
  `POST https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token`
  (`Authorization: Basic base64(client_id:client_secret)`,
  `grant_type=client_credentials` form field). Token flow verified from
  FINRA's own developer docs + finra-py source; **no live token has been
  issued yet** because no key exists.
- Weekly datasets `ATS_W_FIRM` / `ATS_W_SMBL` / `ATS_W_SMBL_FIRM` (+
  `<name>HISTORIC` for pre-12-month history): field names are mapped
  **defensively** — the fetcher requests ALL fields and parses by header
  aliases, so a renamed column degrades to a skipped row, never a 400.
  Column names still need one authenticated smoke test to confirm
  (`weekStartDate`, `ats_mp_id`, `issueSymbolIdentifier`,
  `totalShareQuantity`, `totalTradeQuantity` are FINRA-documented candidates).
- The historic-variant spelling (`ATS_W_FIRMHISTORIC` vs
  `ATS_W_FIRM_HISTORIC`) is probed at runtime; whichever 404s is skipped.

## Free key

https://gateway.finra.org/app/api-console → set `FINRA_CLIENT_ID` /
`FINRA_CLIENT_SECRET` as Render env vars. Without them the job logs
"FINRA API key required…" and skips the weekly pull cleanly; the keyless
monthly data still lands.

## Data model

| Series | Source | Cadence |
|---|---|---|
| `cycle:ats-m-{mpid}-shares` / `-trades` / `-sharepct` | blocksSummary (keyless) | monthly |
| `cycle:ats-m-total-shares` / `-trades` | blocksSummary, summed | monthly |
| `cycle:ats-{mpid}-shares` / `-trades` | ATS_W_FIRM (keyed) | weekly |
| `cycle:ats-total-shares` / `-trades` | ATS_W_FIRM, summed (keyed) | weekly |
| `cycle:ats-sym-{TICKER}-shares` / `-trades` | ATS_W_SMBL, top-500/wk (keyed) | weekly |

Docs: `ats_venues` (MPID → ATS name), `finra_ats` (status: configured,
weeks_stored, backfill_pending, keyless range), `ats_blocks_latest`
(latest monthly venue rows, all slices), `ats-week-{YYYY-MM-DD}` (keyed
weekly snapshot: top venues + top-50 symbols with top-3 venues).

## Traps (hit during build)

1. **6x slice duplication.** `blocksSummary` repeats each venue's monthly
   totals across 6 `summaryTypeCode` slices (2K / 10K / 2K-100K / 10K-200K /
   100K / 200K block-size × dollar buckets). Summing naively 6x-counts.
   Series take ONE row per venue; per-slice block detail is preserved in the
   `ats_blocks_latest` doc.
2. **Weekly data is 2–4 weeks delayed** (Tier 1 NMS: 2 wk, others: 4 wk) —
   labeled in the UI.
3. **Production weekly datasets cover the rolling 12 months only**; full
   history to May 2014 needs the HISTORIC variants (runtime-probed).
4. **`ATS_W_SMBL_FIRM` (venue × security) is ~100x the firm/symbol volume** —
   backfill bounded to the 52 latest weeks; cursor in the `finra_ats` doc.
5. The sandbox's `no_proxy` contains bracketed IPv6 literals that httpx 0.28
   chokes on at client construction — sanitize `no_proxy`/`NO_PROXY` (keep
   the proxy itself) when running the fetcher from this sandbox.
