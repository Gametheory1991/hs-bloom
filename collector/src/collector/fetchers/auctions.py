"""U.S. Treasury auction surveillance via the Fiscal Data Treasury API (keyless).

Endpoint (verified live 2026-10-03):
  GET https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query
  params: filter=record_date:gte:<YYYY-MM-DD>, sort=-record_date,
          page[size]=100, page[number]=N
  -> {"data":[{record_date, auction_date, security_type ("Bill"/"Note"/"Bond"),
               security_term ("4-Week"/"10-Year"/"29-Year 11-Month"...),
               bid_to_cover_ratio, total_accepted, indirect_bidder_accepted,
               direct_bidder_accepted, primary_dealer_accepted, offering_amt, ...}],
       "meta":{...}, "links":{...}}

Only completed auctions (results posted: high yield or bid-to-cover present)
are stored. Tenors are
normalized so reopenings land in their benchmark bucket ("29-Year 11-Month"
-> Bond-30Y). Metrics per configured bucket: bid-to-cover, high yield (high investment rate
for bills), indirect/direct/dealer shares of accepted (%), and offering
amount ($). Dated by auction_date.
Stored as auction:<Type>-<Tenor>:<metric>, e.g. auction:Note-10Y:bid_to_cover.

Two docs are also maintained: ``auction_results`` (per-auction detail records
with high yield, bid-to-cover and takedown splits, upserted by
(auction_date, cusip) so announced auctions gain their results in place; full
history is kept, never pruned) and ``upcoming_auctions`` (announced but not
yet held, sorted by auction date).

History policy: the Fiscal Data API carries auction records back to 1979, so
the fetcher keeps everything it can. The pull cutoff is incremental from the
earliest stored auction: with no stored data (or history shallower than one
revision window) there is no date filter, so the first post-deploy run
performs the full deep backfill in one shot; once a no-filter pull pages to
completion a ``history_complete`` marker is stored and later runs only pull
the recent revision window (``lookback_days``) for new auctions and late
corrections. The (auction_date, cusip) upsert is idempotent, so an
interrupted deep pull simply resumes on the next run.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from collector.config import AuctionsCfg
from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v1/accounting/od/auctions_query"
)
PAGE_SIZE = 100

_TERM_RE = re.compile(r"^(\d+)-(Week|Year)(?: (\d+)-Month)?$")
_STD_MONTHS = [24, 36, 60, 84, 120, 240, 360]  # 2Y..30Y benchmark tenors


def normalize_bucket(security_type: str, security_term: str,
                     inflation_index_security: str | None = None,
                     floating_rate: str | None = None) -> str | None:
    """'Note'/'29-Year 11-Month' -> 'Note-30Y'; 'Bill'/'13-Week' -> 'Bill-13W'.

    TIPS (inflation_index_security='Yes') and FRNs (floating_rate='Yes') get
    their own buckets (e.g. 'TIPS-10Y', 'FRN-2Y') so their yields never mix
    with nominal notes/bonds of the same tenor.
    """
    m = _TERM_RE.match((security_term or "").strip())
    if not m or not security_type:
        return None
    n, unit, extra = int(m.group(1)), m.group(2), int(m.group(3) or 0)
    if unit == "Week":
        return f"{security_type}-{n}W"
    months = n * 12 + extra
    std = min(_STD_MONTHS, key=lambda s: abs(s - months))
    tenor = f"{std // 12}Y"
    if (inflation_index_security or "").strip().lower() == "yes":
        return f"TIPS-{tenor}"
    if (floating_rate or "").strip().lower() == "yes":
        return f"FRN-{tenor}"
    return f"{security_type}-{tenor}"


def _num(v) -> float | None:
    try:
        if v is None or v == "null" or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_auctions(text: str) -> list[dict]:
    """Completed auctions only, with normalized bucket + derived metrics."""
    out = []
    for r in json.loads(text).get("data", []):
        btc = _num(r.get("bid_to_cover_ratio"))
        if btc is None:
            continue  # announced but not yet held
        bucket = normalize_bucket(r.get("security_type"), r.get("security_term"),
                               r.get("inflation_index_security"), r.get("floating_rate"))
        if bucket is None:
            continue
        try:
            d = date.fromisoformat(r["auction_date"])
        except (KeyError, TypeError, ValueError):
            continue
        total = _num(r.get("total_accepted"))
        metrics = {"bid_to_cover": btc, "offering": _num(r.get("offering_amt"))}
        if total:
            for key, field in (
                ("indirect_pct", "indirect_bidder_accepted"),
                ("direct_pct", "direct_bidder_accepted"),
                ("dealer_pct", "primary_dealer_accepted"),
            ):
                v = _num(r.get(field))
                if v is not None:
                    metrics[key] = v / total * 100.0
        out.append({"date": d, "bucket": bucket, "metrics": metrics})
    return out


def parse_detail(r: dict) -> dict | None:
    """Full per-auction record for the results/upcoming docs.

    Returns None when the record is unparseable or not in a benchmark bucket.
    ``completed`` is False for announced-but-not-held auctions
    (bid_to_cover_ratio is null / "null" until results post). For bills the
    clearing yield is the high investment rate (bond-equivalent); for
    notes/bonds it is the high yield.
    """
    try:
        auction_date = date.fromisoformat(r["auction_date"])
    except (KeyError, TypeError, ValueError):
        return None
    bucket = normalize_bucket(r.get("security_type"), r.get("security_term"),
                               r.get("inflation_index_security"), r.get("floating_rate"))
    if bucket is None:
        return None
    total = _num(r.get("total_accepted"))
    hy = _num(r.get("high_yield"))
    hir = _num(r.get("high_investment_rate"))
    det = {
        "auction_date": auction_date.isoformat(),
        "announcemt_date": r.get("announcemt_date"),
        "cusip": r.get("cusip"),
        "security_type": r.get("security_type"),
        "security_term": r.get("security_term"),
        "bucket": bucket,
        "offering_amt": _num(r.get("offering_amt")),
        "high_yield": hy if hy is not None else hir,
        "high_discnt_rate": _num(r.get("high_discnt_rate")),
        "bid_to_cover": _num(r.get("bid_to_cover_ratio")),
        "total_accepted": total,
        "total_tendered": _num(r.get("total_tendered")),
        "issue_date": r.get("issue_date"),
        "maturity_date": r.get("maturity_date"),
        "reopening": r.get("reopening"),
    }
    if total:
        for key, field in (
            ("indirect_pct", "indirect_bidder_accepted"),
            ("direct_pct", "direct_bidder_accepted"),
            ("dealer_pct", "primary_dealer_accepted"),
        ):
            v = _num(r.get(field))
            if v is not None:
                det[key] = v / total * 100.0
    # An auction is completed when its results have posted. Treasury did not
    # publish bid_to_cover_ratio before ~2000, so pre-2000 auctions (which
    # carry yields and takedown splits) count as completed via high_yield.
    det["completed"] = (det["bid_to_cover"] is not None
                        or det["high_yield"] is not None)
    return det


async def fetch_page_raw(page: int, cutoff: str | None,
                       get_text: GetText) -> list[dict]:
    """Raw API records for one page (includes announced-but-not-held).

    ``cutoff`` is a ``record_date >= YYYY-MM-DD`` filter, or None for no
    date filter (full history pull).
    """
    params: dict[str, str] = {
        "sort": "-record_date",
        "page[size]": str(PAGE_SIZE),
        "page[number]": str(page),
    }
    if cutoff is not None:
        params["filter"] = f"record_date:gte:{cutoff}"
    text = await get_text(BASE, params=params)
    return json.loads(text).get("data", [])


async def fetch_page(page: int, cutoff: str, get_text: GetText) -> list[dict]:
    """Parsed completed auctions for one page (kept for backwards compat)."""
    return parse_auctions(json.dumps({"data": await fetch_page_raw(page, cutoff, get_text)}))


def _bucket_metrics(det: dict) -> dict:
    """Per-bucket time-series metrics from a completed auction detail record."""
    metrics = {"bid_to_cover": det["bid_to_cover"], "offering": det["offering_amt"]}
    if det.get("high_yield") is not None:
        metrics["high_yield"] = det["high_yield"]
    for key in ("indirect_pct", "direct_pct", "dealer_pct"):
        if det.get(key) is not None:
            metrics[key] = det[key]
    return metrics


def _results_payload(store: Store) -> dict:
    """Payload of the stored ``auction_results`` doc ({} when absent)."""
    doc = store.doc("auction_results")
    if doc is None or not isinstance(doc.payload, dict):
        return {}
    return doc.payload


# History schema version. v1 defined `completed` as "bid_to_cover present",
# which skipped pre-2000 auctions (Treasury didn't publish bid-to-cover that
# far back). v2 counts them via high_yield. A v1-marked complete history
# triggers one full re-pull so the pre-2000 records are stored.
HISTORY_VERSION = 2


def _pull_cutoff(store: Store, lookback_days: int) -> str | None:
    """Oldest ``record_date`` to request; None means no filter (pull all).

    Incremental-from-earliest history policy:
    - history marked complete -> recent revision window only
      (``today - lookback_days``): cheap daily pull for new auctions and
      late corrections;
    - no stored data, or stored history shallower than one revision window
      (no deep pull has ever completed) -> None: the first post-deploy run
      performs the full deep backfill in one shot;
    - otherwise -> earliest stored auction date minus the revision window,
      so a partial history keeps extending backward while overlapping
      enough to catch corrections.

    The (auction_date, cusip) upsert is idempotent, so an interrupted deep
    pull simply resumes on the next run.
    """
    payload = _results_payload(store)
    if payload.get("history_complete"):
        if payload.get("history_version", 1) >= HISTORY_VERSION:
            return (date.today() - timedelta(days=lookback_days)).isoformat()
        # Complete under an older `completed` definition: re-pull everything
        # once so pre-2000 auctions are stored.
        return None
    dates = [r.get("auction_date") for r in payload.get("results", [])
             if isinstance(r, dict) and r.get("auction_date")]
    if not dates:
        return None
    earliest = min(dates)
    recent_floor = (date.today() - timedelta(days=lookback_days)).isoformat()
    if earliest >= recent_floor:
        return None
    return (date.fromisoformat(earliest) - timedelta(days=lookback_days)).isoformat()


def _store_auction_docs(store: Store, details: list[dict], upcoming: list[dict],
                        history_complete: bool) -> None:
    """Incrementally merge per-auction records.

    Completed results upsert by (auction_date, cusip) so a re-fetched record
    (e.g. an announced auction whose results later posted) overwrites in
    place. Full history is kept: nothing is pruned and there is no record
    cap. ``history_complete`` records whether a no-filter pull paged to
    completion, marking the deep backfill done. The upcoming list is rebuilt
    from the current fetch, sorted by auction date.
    """
    today_iso = date.today().isoformat()
    payload = _results_payload(store)
    existing = payload.get("results", [])
    if not isinstance(existing, list):
        existing = []
    by_key = {(r.get("auction_date"), r.get("cusip")): r for r in existing
              if isinstance(r, dict)}
    for d in details:
        by_key[(d["auction_date"], d["cusip"])] = d
    merged = sorted(by_key.values(),
                    key=lambda r: r.get("auction_date", ""), reverse=True)
    new_version = (HISTORY_VERSION if history_complete
                   else payload.get("history_version", 1))
    store.put_doc(
        "auction_results",
        {"results": merged,
         "history_complete": bool(history_complete or payload.get("history_complete")),
         "history_version": new_version},
        source="fiscaldata.treasury.gov",
    )
    upcoming_sorted = sorted(
        (u for u in upcoming if (u.get("auction_date") or "") >= today_iso),
        key=lambda r: r.get("auction_date", ""),
    )
    store.put_doc(
        "upcoming_auctions",
        {"auctions": upcoming_sorted},
        source="fiscaldata.treasury.gov",
    )


async def fetch_auctions(cfg: AuctionsCfg, store: Store, get_text: GetText) -> str:
    """Daily job: page through auctions, upsert per-bucket metric series.

    The pull cutoff is incremental from the earliest stored auction (see
    ``_pull_cutoff``): the first run with no deep history performs the full
    backfill in one shot, later runs only pull the recent revision window.
    Also maintains two docs: ``auction_results`` (per-auction detail records,
    upserted by (auction_date, cusip) so announced auctions gain their results
    in place; full history kept) and ``upcoming_auctions`` (announced but not
    yet held, rebuilt each run). Buckets not in cfg.buckets are ignored. One
    bad page must not starve the others; per-bucket isolation comes free since
    each bucket is its own series.
    """
    cutoff = _pull_cutoff(store, cfg.lookback_days)
    wanted = set(cfg.buckets)
    today_iso = date.today().isoformat()
    collected: dict[str, dict[str, list]] = {}
    details: list[dict] = []
    upcoming: list[dict] = []
    page, errors, total_raw = 1, [], 0
    log.info("auctions pull starting (cutoff=%s)", cutoff or "none/full history")
    while True:
        try:
            raw = await fetch_page_raw(page, cutoff, get_text)
        except Exception as exc:  # noqa: BLE001 — one bad page must not kill the run
            errors.append(f"page {page}: {exc}")
            break
        if not raw:
            break
        total_raw += len(raw)
        for r in raw:
            det = parse_detail(r)
            if det is None or det["bucket"] not in wanted:
                continue
            if det["completed"]:
                details.append(det)
                b = collected.setdefault(det["bucket"], {})
                for metric, v in _bucket_metrics(det).items():
                    if v is not None:
                        b.setdefault(metric, []).append(
                            (date.fromisoformat(det["auction_date"]), v))
            elif det["auction_date"] >= today_iso:
                upcoming.append(det)
        if len(raw) < PAGE_SIZE:
            break
        page += 1
        if page % 25 == 0:
            log.info("auctions pull in progress: %d pages, %d raw records",
                     page, total_raw)
    log.info("auctions pull finished: %d pages, %d raw records, %d completed",
             page, total_raw, len(details))
    batch: list[tuple[str, date, float]] = []
    for bucket, metrics in collected.items():
        for metric, pts in metrics.items():
            pts.sort(key=lambda p: p[0])
            sid = f"auction:{bucket}:{metric}"
            batch.extend((sid, d, v) for d, v in pts)
    if batch:
        store.upsert_points_batch(batch)
    # A no-filter pull that paged to completion has seen every record the
    # API holds: the deep backfill is done.
    history_complete = cutoff is None and not errors
    _store_auction_docs(store, details, upcoming, history_complete)
    if errors:
        raise RuntimeError(f"auctions paging failures: {'; '.join(errors)}")
    return "auctions"
