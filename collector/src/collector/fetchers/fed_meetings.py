"""FOMC decision pricing: Kalshi vs Polymarket vs Fed funds futures (keyless).

Tracks the NEXT FOMC meeting's decision probabilities across three venues,
Bloomberg-Intelligence style (grouped hike/hold/cut bars per venue):

- Kalshi: KXFEDDECISION-YYMON event — five mutually-exclusive markets
  (C26/C25/H0/H25/H26 = cut>25bp / cut 25bp / hold / hike 25bp / hike>25bp).
  Verified live 2026-10-07.
- Polymarket: "Fed decision in {Month}" event slugs (e.g.
  fed-decision-in-october); outcomes "25+ bps increase" / "No change" /
  "25 bps decrease" / "50+ bps decrease".
- Fed funds futures: CME FedWatch probabilities via the free third-party
  archive github.com/zuowood1234/cme-fedwatch-tracker (daily JSON snapshots
  scraped from CME's FedWatch page). This is NOT official CME data — the UI
  labels it "CME FedWatch via third-party archive". If the archive is stale
  (>3 days) or missing the meeting, values fall back to the manual file
  collector/data/fed_futures_manual.json (documented below), also UI-labeled.

FOMC schedule is hardcoded from the Fed's published calendar (decision dates
= second day of each meeting). The chart auto-rolls: next_meeting is the
first decision date after today.

Stored (percent 0-100, one point per day per meeting):
  cycle:fedmeet-kal-YYYYMM-hike / -hold / -cut
  cycle:fedmeet-poly-YYYYMM-hike / -hold / -cut
  cycle:fedmeet-ff-YYYYMM-hike / -hold / -cut
plus a `fed_meetings` doc with the next meeting's full snapshot for the
PREDICT tab panel (sources, staleness flags, manual-input flag).

Manual-input file format (collector/data/fed_futures_manual.json):
  {"meetings": {"2026-10-28": {"hike": 20.5, "hold": 79.5, "cut": 0.0,
                               "asof": "2026-10-07",
                               "note": "CME FedWatch, entered by hand"}}}
The file is optional; entries take precedence over the archive for that
meeting and are labeled "manual input" in the UI. Never invent values —
leave a meeting out rather than guessing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, timedelta

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "kalshi+polymarket+cmegroup-fedwatch-archive"

# (decision date, label, Kalshi event ticker, Polymarket slug candidates)
# Decision dates = second day of each FOMC meeting, from the Fed's published
# calendar (2026-2027). Kalshi pattern KXFEDDECISION-YYMON verified live
# 2026-10-07; Polymarket slugs verified for Oct 2026, searched for the rest.
MEETINGS: list[tuple[str, str, str, list[str]]] = [
    ("2026-10-28", "Oct 2026", "KXFEDDECISION-26OCT",
     ["fed-decision-in-october-20260617190323537", "fed-decision-in-october"]),
    ("2026-12-09", "Dec 2026", "KXFEDDECISION-26DEC",
     ["fed-decision-in-december"]),
    ("2027-01-27", "Jan 2027", "KXFEDDECISION-27JAN",
     ["fed-decision-in-january"]),
    ("2027-03-17", "Mar 2027", "KXFEDDECISION-27MAR",
     ["fed-decision-in-march"]),
    ("2027-04-28", "Apr 2027", "KXFEDDECISION-27APR",
     ["fed-decision-in-april"]),
    ("2027-06-09", "Jun 2027", "KXFEDDECISION-27JUN",
     ["fed-decision-in-june"]),
    ("2027-07-28", "Jul 2027", "KXFEDDECISION-27JUL",
     ["fed-decision-in-july"]),
    ("2027-09-15", "Sep 2027", "KXFEDDECISION-27SEP",
     ["fed-decision-in-september"]),
    ("2027-10-27", "Oct 2027", "KXFEDDECISION-27OCT",
     ["fed-decision-in-october-2027"]),
    ("2027-12-08", "Dec 2027", "KXFEDDECISION-27DEC",
     ["fed-decision-in-december-2027"]),
]

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA = "https://gamma-api.polymarket.com"
FEDWATCH_DAILY = ("https://raw.githubusercontent.com/zuowood1234/"
                  "cme-fedwatch-tracker/main/data/daily/{d}.json")
FEDWATCH_STALE_DAYS = 3

MANUAL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                           "data", "fed_futures_manual.json")

KALSHI_SUFFIX = (("H26", "hike"), ("H25", "hike"), ("H0", "hold"),
                 ("C25", "cut"), ("C26", "cut"))


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _kalshi_mid(m: dict) -> float | None:
    b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if b is not None and a is not None and a >= b:
        return (b + a) / 2
    return _f(m.get("last_price_dollars"))


def _next_meeting(today: date) -> tuple[str, str, str, list[str]] | None:
    for m in MEETINGS:
        if date.fromisoformat(m[0]) > today:
            return m
    return None


async def _kalshi_probs(get_text: GetText, event: str) -> dict | None:
    """{hike, hold, cut} percents from the KXFEDDECISION event, or None."""
    try:
        body = json.loads(await get_text(f"{KALSHI_BASE}/events/{event}"))
    except Exception as exc:  # noqa: BLE001
        log.warning("fed_meetings: kalshi event %s failed: %s", event, exc)
        return None
    out = {"hike": 0.0, "hold": 0.0, "cut": 0.0}
    seen = set()
    for m in (body.get("markets") or []):
        tk = str(m.get("ticker") or "")
        for suffix, bucket in KALSHI_SUFFIX:
            if tk.endswith("-" + suffix) and suffix not in seen:
                px = _kalshi_mid(m)
                if px is not None:
                    out[bucket] += px * 100.0
                    seen.add(suffix)
                break
    if not seen:
        return None
    return out


async def _poly_probs(get_text: GetText, slugs: list[str]) -> tuple[dict | None, str | None]:
    """({hike, hold, cut} percents, slug used) from the Polymarket event."""
    for slug in slugs:
        try:
            body = json.loads(await get_text(f"{POLY_GAMMA}/events/slug/{slug}"))
        except Exception:  # noqa: BLE001
            continue
        out = {"hike": 0.0, "hold": 0.0, "cut": 0.0}
        seen = False
        yes_sum = 0.0
        degenerate = True
        for m in (body.get("markets") or []):
            title = str(m.get("groupItemTitle") or m.get("question") or "").lower()
            try:
                prices = json.loads(m.get("outcomePrices") or "[]")
                yes = _f(prices[0]) if prices else None
            except (TypeError, ValueError):
                yes = None
            if yes is None:
                continue
            yes_sum += yes
            if yes not in (0.0, 1.0):
                degenerate = False
            if "increase" in title:
                out["hike"] += yes * 100.0
                seen = True
            elif "no change" in title:
                out["hold"] += yes * 100.0
                seen = True
            elif "decrease" in title:
                out["cut"] += yes * 100.0
                seen = True
        # Mutually-exclusive outcome sets must sum to ~100%. Reject stale /
        # resolved events: all legs parked at exactly 0/1, or a bad sum.
        if seen and not degenerate and 0.5 <= yes_sum <= 1.5:
            return out, slug
        log.warning("fed_meetings: polymarket slug %s looks stale/resolved "
                    "(yes-sum %.3f), trying next", slug, yes_sum)
    # Fallback: public search for the meeting's event.
    try:
        q = "fed decision " + slugs[0].replace("fed-decision-in-", "").replace("-", " ")
        body = json.loads(await get_text(
            f"{POLY_GAMMA}/public-search?q={q.replace(' ', '+')}"))
        evs = body.get("events", []) if isinstance(body, dict) else []
        for e in evs[:3]:
            slug = str(e.get("slug") or "")
            if "fed" in slug and "decision" in slug:
                return await _poly_probs(get_text, [slug])
    except Exception as exc:  # noqa: BLE001
        log.warning("fed_meetings: polymarket search failed: %s", exc)
    return None, None


def _manual_ff(meeting_date: str) -> dict | None:
    try:
        with open(os.path.normpath(MANUAL_PATH)) as fh:
            data = json.load(fh)
        m = (data.get("meetings") or {}).get(meeting_date)
        if m and all(_f(m.get(k)) is not None for k in ("hike", "hold", "cut")):
            return {"hike": float(m["hike"]), "hold": float(m["hold"]),
                    "cut": float(m["cut"]), "asof": str(m.get("asof") or ""),
                    "note": str(m.get("note") or ""), "manual": True}
    except (OSError, ValueError) as exc:
        log.warning("fed_meetings: manual file unreadable: %s", exc)
    return None


async def _fedwatch_probs(get_text: GetText, meeting_date: str) -> dict | None:
    """{hike, hold, cut} from the third-party CME FedWatch archive.

    Walks back up to 10 days for the latest snapshot; matches the meeting by
    meeting_date. Returns None if the archive is stale (>3d) or lacks the
    meeting — the caller then tries the manual file.
    """
    today = date.today()
    for back in range(10):
        d = (today - timedelta(days=back)).isoformat()
        try:
            body = json.loads(await get_text(FEDWATCH_DAILY.format(d=d)))
        except Exception:  # noqa: BLE001
            continue
        for mtg in (body.get("meetings") or []):
            if str(mtg.get("meeting_date")) != meeting_date:
                continue
            s = mtg.get("summary") or {}
            hike, hold, cut = _f(s.get("hike")), _f(s.get("no_change")), _f(s.get("ease"))
            if hike is None or hold is None or cut is None:
                return None
            age = (today - date.fromisoformat(str(body.get("snapshot_date") or d))).days
            return {"hike": hike, "hold": hold, "cut": cut,
                    "snapshot_date": str(body.get("snapshot_date") or d),
                    "stale": age > FEDWATCH_STALE_DAYS,
                    "contract": str(mtg.get("contract") or ""),
                    "manual": False}
        return None  # snapshot exists but lacks this meeting
    return None


async def fetch_fed_meetings(store: Store, get_text: GetText) -> str:
    """Daily snapshot of next-FOMC decision pricing across Kalshi,
    Polymarket, and Fed funds futures. Idempotent (same-day upserts)."""
    today = date.today()
    nxt = _next_meeting(today)
    if nxt is None:
        return "no future FOMC meeting in schedule"
    meeting_date, label, kal_event, poly_slugs = nxt
    mkey = meeting_date.replace("-", "")[:6]  # YYYYMM

    kal, (poly, poly_slug) = await asyncio.gather(
        _kalshi_probs(get_text, kal_event),
        _poly_probs(get_text, poly_slugs),
    )
    ff = await _fedwatch_probs(get_text, meeting_date)
    ff_manual = False
    if ff is None or ff.get("stale"):
        manual = _manual_ff(meeting_date)
        if manual is not None:
            ff, ff_manual = manual, True

    stored: dict[str, list[str]] = {}
    for venue, probs in (("kal", kal), ("poly", poly), ("ff", ff)):
        if not probs:
            continue
        keys = []
        for outcome in ("hike", "hold", "cut"):
            sid = f"cycle:fedmeet-{venue}-{mkey}-{outcome}"
            store.upsert_points(sid, [(today, round(float(probs[outcome]), 2))])
            keys.append(sid)
        stored[venue] = keys

    store.put_doc("fed_meetings", {
        "as_of": today.isoformat(),
        "next_meeting": {"date": meeting_date, "label": label, "key": mkey},
        "kalshi": {"event": kal_event, "probs": kal},
        "polymarket": {"slug": poly_slug, "probs": poly},
        "fed_funds": {
            "probs": {k: ff[k] for k in ("hike", "hold", "cut")} if ff else None,
            "source": ("manual input" if ff_manual
                       else "CME FedWatch via third-party archive "
                            "(github.com/zuowood1234/cme-fedwatch-tracker)"),
            "manual": ff_manual,
            "snapshot_date": (ff or {}).get("snapshot_date"),
            "stale": bool((ff or {}).get("stale")),
            "contract": (ff or {}).get("contract"),
        },
        "series": stored,
        "note": ("Probabilities are market-implied, not forecasts. Kalshi "
                 "markets are mutually exclusive; Polymarket outcomes sum the "
                 "matching legs."),
    }, source=SOURCE)
    have = [v for v in ("kal", "poly", "ff") if v in stored]
    return f"{SOURCE}: {label} meeting — venues stored: {','.join(have) or 'none'}"
