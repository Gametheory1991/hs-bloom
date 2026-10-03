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
-> Bond-30Y). Metrics per configured bucket: bid-to-cover, indirect/direct/
dealer shares of accepted (%), and offering amount ($). Dated by auction_date.
Stored as auction:<Type>-<Tenor>:<metric>, e.g. auction:Note-10Y:bid_to_cover.
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


async def fetch_page(page: int, cutoff: str, get_text: GetText) -> list[dict]:
    text = await get_text(
        BASE,
        params={
            "filter": f"record_date:gte:{cutoff}",
            "sort": "-record_date",
            "page[size]": str(PAGE_SIZE),
            "page[number]": str(page),
        },
    )
    return parse_auctions(text)


async def fetch_auctions(cfg: AuctionsCfg, store: Store, get_text: GetText) -> str:
    """Daily job: page through recent auctions, upsert per-bucket metric series.

    Buckets not in cfg.buckets are ignored. One bad page must not starve the
    others; per-bucket isolation comes free since each bucket is its own series.
    """
    cutoff = (date.today() - timedelta(days=cfg.lookback_days)).isoformat()
    wanted = set(cfg.buckets)
    collected: dict[str, dict[str, list]] = {}
    page, errors = 1, []
    while True:
        try:
            recs = await fetch_page(page, cutoff, get_text)
        except Exception as exc:  # noqa: BLE001 — one bad page must not kill the run
            errors.append(f"page {page}: {exc}")
            break
        if not recs:
            break
        for r in recs:
            if r["bucket"] not in wanted:
                continue
            b = collected.setdefault(r["bucket"], {})
            for metric, v in r["metrics"].items():
                if v is not None:
                    b.setdefault(metric, []).append((r["date"], v))
        if len(recs) < PAGE_SIZE:
            break
        page += 1
    for bucket, metrics in collected.items():
        for metric, pts in metrics.items():
            pts.sort(key=lambda p: p[0])
            store.upsert_points(f"auction:{bucket}:{metric}", pts)
    if errors:
        raise RuntimeError(f"auctions paging failures: {'; '.join(errors)}")
    return "auctions"
