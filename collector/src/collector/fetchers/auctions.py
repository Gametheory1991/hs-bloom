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

Only completed auctions (bid_to_cover_ratio present) are stored. Tenors are
normalized so reopenings land in their benchmark bucket ("29-Year 11-Month"
-> Bond-30Y). Metrics per configured bucket: bid-to-cover, high yield (high investment rate
for bills), indirect/direct/dealer shares of accepted (%), and offering
amount ($). Dated by auction_date.
Stored as auction:<Type>-<Tenor>:<metric>, e.g. auction:Note-10Y:bid_to_cover.

Two docs are also maintained: ``auction_results`` (per-auction detail records
with high yield, bid-to-cover and takedown splits, upserted by
(auction_date, cusip) so announced auctions gain their results in place) and
``upcoming_auctions`` (announced but not yet held, sorted by auction date).
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from collector.config import AuctionsCfg
from collector.http import GetText
from collector.store import Store

BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v1/accounting/od/auctions_query"
)
PAGE_SIZE = 100

_TERM_RE = re.compile(r"^(\d+)-(Week|Year)(?: (\d+)-Month)?$")
_STD_MONTHS = [24, 36, 60, 84, 120, 240, 360]  # 2Y..30Y benchmark tenors


def normalize_bucket(security_type: str, security_term: str) -> str | None:
    """'Note'/'29-Year 11-Month' -> 'Note-30Y'; 'Bill'/'13-Week' -> 'Bill-13W'."""
    m = _TERM_RE.match((security_term or "").strip())
    if not m or not security_type:
        return None
    n, unit, extra = int(m.group(1)), m.group(2), int(m.group(3) or 0)
    if unit == "Week":
        return f"{security_type}-{n}W"
    months = n * 12 + extra
    std = min(_STD_MONTHS, key=lambda s: abs(s - months))
    return f"{security_type}-{std // 12}Y"


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
        bucket = normalize_bucket(r.get("security_type"), r.get("security_term"))
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
    bucket = normalize_bucket(r.get("security_type"), r.get("security_term"))
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
    det["completed"] = det["bid_to_cover"] is not None
    return det


async def fetch_page_raw(page: int, cutoff: str, get_text: GetText) -> list[dict]:
    """Raw API records for one page (includes announced-but-not-held)."""
    text = await get_text(
        BASE,
        params={
            "filter": f"record_date:gte:{cutoff}",
            "sort": "-record_date",
            "page[size]": str(PAGE_SIZE),
            "page[number]": str(page),
        },
    )
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


RESULTS_KEEP_DAYS = 180
RESULTS_CAP = 1000


def _store_auction_docs(store: Store, details: list[dict], upcoming: list[dict]) -> None:
    """Incrementally merge per-auction records.

    Completed results upsert by (auction_date, cusip) so a re-fetched record
    (e.g. an announced auction whose results later posted) overwrites in
    place; pruned to RESULTS_KEEP_DAYS. The upcoming list is rebuilt from the
    current fetch, sorted by auction date.
    """
    today_iso = date.today().isoformat()
    existing: list[dict] = []
    doc = store.doc("auction_results")
    if doc is not None:
        existing = doc.payload.get("results", [])
    by_key = {(r.get("auction_date"), r.get("cusip")): r for r in existing}
    for d in details:
        by_key[(d["auction_date"], d["cusip"])] = d
    cutoff = (date.today() - timedelta(days=RESULTS_KEEP_DAYS)).isoformat()
    merged = [r for r in by_key.values() if (r.get("auction_date") or "") >= cutoff]
    merged.sort(key=lambda r: r.get("auction_date", ""), reverse=True)
    store.put_doc(
        "auction_results",
        {"results": merged[:RESULTS_CAP]},
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
    """Daily job: page through recent auctions, upsert per-bucket metric series.

    Also maintains two docs: ``auction_results`` (per-auction detail records,
    upserted by (auction_date, cusip) so announced auctions gain their results
    in place) and ``upcoming_auctions`` (announced but not yet held, rebuilt
    each run). Buckets not in cfg.buckets are ignored. One bad page must not
    starve the others; per-bucket isolation comes free since each bucket is
    its own series.
    """
    cutoff = (date.today() - timedelta(days=cfg.lookback_days)).isoformat()
    wanted = set(cfg.buckets)
    today_iso = date.today().isoformat()
    collected: dict[str, dict[str, list]] = {}
    details: list[dict] = []
    upcoming: list[dict] = []
    page, errors = 1, []
    while True:
        try:
            raw = await fetch_page_raw(page, cutoff, get_text)
        except Exception as exc:  # noqa: BLE001 — one bad page must not kill the run
            errors.append(f"page {page}: {exc}")
            break
        if not raw:
            break
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
    for bucket, metrics in collected.items():
        for metric, pts in metrics.items():
            pts.sort(key=lambda p: p[0])
            store.upsert_points(f"auction:{bucket}:{metric}", pts)
    _store_auction_docs(store, details, upcoming)
    if errors:
        raise RuntimeError(f"auctions paging failures: {'; '.join(errors)}")
    return "auctions"
