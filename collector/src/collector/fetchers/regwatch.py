"""REG WATCH — regulatory news + surveillance feed.

Hourly pull of agency RSS feeds (SEC, CFTC, FINRA, Fed, OCC, FDIC, FSB, OFR),
Federal Register rulemaking tracker for SEC/CFTC/Fed, and config-driven topic
tagging. Graceful degradation: one dead feed never breaks the run.

Stored docs:
  regwatch            {items: [...], rules: [...], topics: {...}, feed_status: {...}}
  regwatch_summaries  {item_id: one-line summary}  (Gemini cache, never re-summarized)
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
from calendar import timegm
from datetime import datetime, timezone

import feedparser

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

# (agency id, label, feed url). All free, no auth. A dead feed is skipped,
# never fatal.
REGWATCH_FEEDS = [
    ("sec", "SEC", "https://www.sec.gov/news/pressreleases.rss"),
    ("cftc", "CFTC", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ("finra", "FINRA", "https://www.finra.org/rules-guidance/notices/rss"),
    ("fed", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("fed-speeches", "Fed Speeches", "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("occ", "OCC", "https://www.occ.gov/rss/occ_news.xml"),
    ("fdic", "FDIC", "https://public.govdelivery.com/topics/USFDIC_26/feed.rss"),
    ("fsb", "FSB", "https://www.fsb.org/feed/"),
    ("ofr", "OFR", "https://www.financialresearch.gov/briefs/feed.rss"),
]

# Sources checked 2026-10-06 with no usable RSS (kept for the record;
# re-check periodically): Treasury press releases (RSS discontinued), FHFA,
# MSRB, BIS, PCAOB (blocked), FSOC, NFA (HTML only), Congress.gov (API key
# required — add when Harry provides one).

# topic_id -> (label, [keywords]). Case-insensitive substring match on
# title + description. Easy for Harry to extend.
REGWATCH_TOPICS: dict[str, tuple[str, list[str]]] = {
    "private-credit": ("Private Credit",
        ["private credit", "direct lending", "bdc", "private fund", "private debt"]),
    "basel-iii": ("Basel III",
        ["basel", "capital requirements", "supplementary leverage", "slr",
         "gsib surcharge", "eslr", "tier 1 capital"]),
    "sec-lending": ("Securities Lending",
        ["securities lending", "stock loan", "securities loan"]),
    "trace-enhancement": ("TRACE Enhancement",
        ["trace", "trade reporting", "post-trade transparency"]),
    "trf": ("TRFs / Off-Exchange",
        ["trade reporting facility", "trf", "off-exchange", "dark pool",
         "internaliz"]),
    "genius-tokenization": ("GENIUS Act / Tokenization",
        ["genius", "stablecoin", "tokeniz", "blockchain", "digital asset",
         "crypto", "distributed ledger"]),
    "treasury-clearing": ("Treasury Clearing",
        ["treasury clearing", "clearing mandate", "dealer registration",
         "government securities dealer", "central clearing"]),
    "slr-leverage": ("SLR / Leverage",
        ["supplementary leverage", "slr", "leverage ratio", "gsib surcharge",
         "eslr", "leverage requirement"]),
    "cat": ("Consolidated Audit Trail",
        ["consolidated audit trail", "cat "]),
    "mmf-reform": ("Money Market Fund Reform",
        ["money market fund", "mmf reform", "mmf rule"]),
    "repo": ("Repo / Funding",
        ["repo facility", "standing repo", "on rrp", "tri-party repo",
         "repo market"]),
    "crypto-structure": ("Crypto Market Structure",
        ["clarity act", "market structure", "crypto framework",
         "digital asset framework"]),
    "gse-reform": ("GSE Reform",
        ["gse reform", "conservatorship", "fannie mae", "freddie mac",
         "fhfa", "housing finance reform"]),
    "form-pf": ("Form PF / Fund Reporting",
        ["form pf", "hedge fund reporting", "private fund reporting"]),
    "reg-sci": ("Reg SCI / Cybersecurity",
        ["reg sci", "cybersecurity", "systems compliance", "cyber incident"]),
    "ai-model-risk": ("AI / Model Risk",
        ["artificial intelligence", "model risk", "machine learning",
         "generative ai"]),
    "nbfi": ("Nonbank / NBFI",
        ["nonbank", "nbfi", "shadow banking", "non-bank"]),
}

MAX_ITEMS = 150
MAX_SUMMARIZE_PER_RUN = 12

# Federal Register API: rulemaking tracker for the three federal agencies
# (FINRA is an SRO — its rules surface via the FINRA notices feed above).
FR_API = "https://www.federalregister.gov/api/v1/documents.json"
FR_AGENCIES = {
    "sec": "securities-and-exchange-commission",
    "cftc": "commodity-futures-trading-commission",
    "fed": "federal-reserve-system",
}
FR_LOOKBACK_DAYS = 120


def _item_id(link: str) -> str:
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def _entry_time(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _clean(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def tag_topics(title: str, description: str) -> list[str]:
    hay = f"{title} {description}".lower()
    return [tid for tid, (_, kws) in REGWATCH_TOPICS.items()
            if any(kw in hay for kw in kws)]


async def _fetch_feed(agency: str, label: str, url: str, get_text: GetText) -> tuple[str, list[dict] | None, str | None]:
    """Returns (agency, items or None, error or None). Never raises."""
    try:
        parsed = feedparser.parse(await get_text(url))
    except Exception as exc:  # noqa: BLE001 — spec: drop dead feeds silently
        return agency, None, str(exc)[:120]
    items = []
    for entry in parsed.entries:
        title = _clean(entry.get("title"), 300)
        link = entry.get("link")
        if not title or not link:
            continue
        desc = _clean(entry.get("description") or entry.get("summary"), 400)
        items.append({
            "id": _item_id(link),
            "agency": agency,
            "agency_label": label,
            "title": title,
            "link": link,
            "published_at": _entry_time(entry),
            "description": desc,
            "topics": tag_topics(title, desc),
        })
    return agency, items, None


async def _fetch_rules(get_text: GetText) -> list[dict]:
    """Federal Register: proposed + final rules from SEC/CFTC/Fed, last 120 days."""
    from datetime import date, timedelta
    rules: list[dict] = []
    gte = (date.today() - timedelta(days=FR_LOOKBACK_DAYS)).isoformat()
    for agency, slug in FR_AGENCIES.items():
        params = [
            ("conditions[agencies][]", slug),
            ("conditions[type][]", "RULE"),
            ("conditions[type][]", "PRORULE"),
            ("conditions[publication_date][gte]", gte),
            ("per_page", "40"),
            ("order", "newest"),
            ("fields[]", "title"),
            ("fields[]", "type"),
            ("fields[]", "publication_date"),
            ("fields[]", "comments_close_on"),
            ("fields[]", "html_url"),
            ("fields[]", "abstract"),
        ]
        try:
            import json
            data = json.loads(await get_text(FR_API, params=params))
        except Exception as exc:  # noqa: BLE001 — FR down: tracker degrades, feed survives
            log.warning("regwatch: federal register %s failed: %s", agency, exc)
            continue
        for d in data.get("results", []):
            rules.append({
                "agency": agency,
                "agency_label": {"sec": "SEC", "cftc": "CFTC", "fed": "Federal Reserve"}[agency],
                "title": d.get("title", ""),
                "type": "proposed" if d.get("type") == "Proposed Rule" else "final",
                "published_at": d.get("publication_date"),
                "comments_close_on": d.get("comments_close_on"),
                "link": d.get("html_url"),
                "topics": tag_topics(d.get("title", ""), d.get("abstract") or ""),
            })
    # newest first
    rules.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    return rules


def _summarize_fallback(item: dict) -> str:
    desc = item.get("description") or ""
    return (desc[:140] + "…") if len(desc) > 140 else (desc or item["title"])


async def _gemini_summaries(items: list[dict], store: Store) -> dict[str, str]:
    """One-line Gemini bullet per new item; cached in regwatch_summaries."""
    cached_doc = store.doc("regwatch_summaries")
    cached: dict[str, str] = dict(cached_doc.payload) if cached_doc else {}
    todo = [it for it in items if it["id"] not in cached][:MAX_SUMMARIZE_PER_RUN]
    if not todo:
        return cached
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("regwatch: GEMINI_API_KEY missing — summaries fall back to descriptions")
        for it in todo:
            cached[it["id"]] = _summarize_fallback(it)
        store.put_doc("regwatch_summaries", cached, source="regwatch")
        return cached
    try:
        from collector.chat import ask_gemini
    except Exception as exc:  # noqa: BLE001
        log.warning("regwatch: gemini import failed: %s", exc)
        for it in todo:
            cached[it["id"]] = _summarize_fallback(it)
        store.put_doc("regwatch_summaries", cached, source="regwatch")
        return cached
    for it in todo:
        try:
            text = ask_gemini(
                api_key, "gemini-flash-latest",
                "You write one-line news summaries for a fixed-income trading desk. "
                "Rules: max 25 words, start with the key fact, no throat-clearing, "
                "plain text, no bullets or quotes.",
                [{"role": "user",
                  "content": f"Summarize in one line: {it['title']}. {it.get('description','')[:300]}"}],
                timeout=30,
            ).strip()
            cached[it["id"]] = text[:200] if text else _summarize_fallback(it)
        except Exception as exc:  # noqa: BLE001 — one bad summary never blocks the run
            log.warning("regwatch: gemini summary failed for %s: %s", it["id"], exc)
            cached[it["id"]] = _summarize_fallback(it)
    store.put_doc("regwatch_summaries", cached, source="regwatch")
    return cached


async def fetch_regwatch(store: Store, get_text: GetText) -> str:
    results = await asyncio.gather(
        *[_fetch_feed(a, label, url, get_text) for a, label, url in REGWATCH_FEEDS]
    )
    items: list[dict] = []
    feed_status: dict[str, str] = {}
    for agency, feed_items, err in results:
        if feed_items is None:
            feed_status[agency] = f"error: {err}"
            log.warning("regwatch: feed %s failed: %s", agency, err)
        else:
            feed_status[agency] = f"ok ({len(feed_items)} items)"
            items.extend(feed_items)
    if not items:
        raise RuntimeError("all regwatch feeds failed")

    # dedupe by link id, newest first
    seen: set[str] = set()
    unique: list[dict] = []
    for it in sorted(items, key=lambda i: i["published_at"], reverse=True):
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        unique.append(it)
    unique = unique[:MAX_ITEMS]

    summaries = await _gemini_summaries(unique, store)
    for it in unique:
        it["summary"] = summaries.get(it["id"], _summarize_fallback(it))

    rules = await _fetch_rules(get_text)

    # topic rollup: count + latest 5 item ids
    topics: dict[str, dict] = {}
    for tid, (label, _) in REGWATCH_TOPICS.items():
        hits = [it for it in unique if tid in it["topics"]]
        topics[tid] = {"label": label, "count": len(hits),
                       "latest": [h["id"] for h in hits[:5]]}

    store.put_doc("regwatch", {
        "items": unique,
        "rules": rules,
        "topics": topics,
        "feed_status": feed_status,
    }, source="regwatch")
    ok = sum(1 for v in feed_status.values() if v.startswith("ok"))
    return f"regwatch ({ok}/{len(REGWATCH_FEEDS)} feeds, {len(unique)} items, {len(rules)} rules)"
