"""Tests for the fed_meetings fetcher: FOMC decision pricing across Kalshi,
Polymarket, and Fed funds futures (CME FedWatch third-party archive).
All HTTP is faked; fixtures use real response shapes verified 2026-10-07."""
from __future__ import annotations

import json
from datetime import date

import pytest

from collector.fetchers import fed_meetings
from collector.store import Store


def _kalshi_event() -> str:
    mkts = []
    for suffix, bid, ask in (("C26", 0.00, 0.01), ("C25", 0.00, 0.01),
                             ("H0", 0.83, 0.84), ("H25", 0.15, 0.16),
                             ("H26", 0.00, 0.01)):
        mkts.append({
            "ticker": f"KXFEDDECISION-26OCT-{suffix}",
            "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "last_price_dollars": ask, "status": "active",
        })
    return json.dumps({"event": {"event_ticker": "KXFEDDECISION-26OCT",
                                 "title": "Fed decision in Oct 2026?"},
                       "markets": mkts})


def _poly_event(slug: str = "fed-decision-in-october-20260617190323537") -> str:
    mkts = []
    for title, yes in (("50+ bps increase", 0.0035), ("25 bps increase", 0.155),
                       ("No change", 0.835), ("25 bps decrease", 0.0075),
                       ("50+ bps decrease", 0.0015)):
        mkts.append({"groupItemTitle": title,
                     "outcomePrices": json.dumps([str(yes), str(1 - yes)])})
    return json.dumps({"slug": slug, "markets": mkts})


def _poly_event_stale() -> str:
    # October 2025 event: resolved, legs parked at exactly 0/1.
    mkts = []
    for title, yes in (("50+ bps decrease", 0.0), ("25 bps decrease", 1.0),
                       ("No change", 0.0), ("25+ bps increase", 0.0)):
        mkts.append({"groupItemTitle": title,
                     "outcomePrices": json.dumps([str(int(yes)), str(int(1 - yes))])})
    return json.dumps({"slug": "fed-decision-in-october", "markets": mkts})


def _fedwatch() -> str:
    return json.dumps({
        "snapshot_date": date.today().isoformat(),
        "meetings": [{"meeting_date": "2026-10-28", "contract": "ZQV6",
                      "summary": {"ease": 1.0, "no_change": 79.5,
                                  "hike": 19.5}}],
    })


def _get_text_factory(mapping: dict) -> object:
    async def fake(url: str) -> str:
        for key, val in mapping.items():
            if key in url:
                return val
        raise RuntimeError(f"unexpected url {url}")
    return fake


def _mapping() -> dict:
    return {
        "KXFEDDECISION-26OCT": _kalshi_event(),
        "fed-decision-in-october": _poly_event(),
        "cme-fedwatch-tracker": _fedwatch(),
    }


def test_next_meeting_rolls():
    assert fed_meetings._next_meeting(date(2026, 10, 7))[0] == "2026-10-28"
    assert fed_meetings._next_meeting(date(2026, 10, 29))[0] == "2026-12-09"
    assert fed_meetings._next_meeting(date(2028, 1, 1)) is None


def test_kalshi_mid_prefers_book():
    assert fed_meetings._kalshi_mid({"yes_bid_dollars": 0.83,
                                    "yes_ask_dollars": 0.84}) == pytest.approx(0.835)
    assert fed_meetings._kalshi_mid({"yes_bid_dollars": None,
                                    "yes_ask_dollars": None,
                                    "last_price_dollars": 0.5}) == pytest.approx(0.5)
    assert fed_meetings._kalshi_mid({}) is None


@pytest.mark.asyncio
async def test_fetch_fed_meetings_stores_all_venues(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    res = await fed_meetings.fetch_fed_meetings(store, _get_text_factory(_mapping()))
    assert "2026" in res and "kal" in res and "poly" in res and "ff" in res
    # Kalshi: H0 83.5 hold, H25+H26 16.0 hike, C25+C26 1.0 cut
    pts = store.points("cycle:fedmeet-kal-202610-hold")
    assert len(pts) == 1
    assert list(pts.values())[0] == pytest.approx(83.5)
    assert list(store.points("cycle:fedmeet-kal-202610-hike").values())[0] == pytest.approx(16.0)
    assert list(store.points("cycle:fedmeet-kal-202610-cut").values())[0] == pytest.approx(1.0)
    # Polymarket: increases 15.85 / no change 83.5 / decreases 0.9
    assert list(store.points("cycle:fedmeet-poly-202610-hike").values())[0] == pytest.approx(15.85)
    assert list(store.points("cycle:fedmeet-poly-202610-hold").values())[0] == pytest.approx(83.5)
    assert list(store.points("cycle:fedmeet-poly-202610-cut").values())[0] == pytest.approx(0.9)
    # FedWatch archive: ease 1.0 / no_change 79.5 / hike 19.5
    assert list(store.points("cycle:fedmeet-ff-202610-hike").values())[0] == pytest.approx(19.5)
    assert list(store.points("cycle:fedmeet-ff-202610-hold").values())[0] == pytest.approx(79.5)
    # panel doc
    doc = store.doc("fed_meetings")
    assert doc is not None
    assert doc.payload["next_meeting"]["date"] == "2026-10-28"
    assert doc.payload["fed_funds"]["manual"] is False
    assert "third-party archive" in doc.payload["fed_funds"]["source"]


@pytest.mark.asyncio
async def test_poly_skips_stale_resolved_slug(tmp_path):
    # The clean "fed-decision-in-october" slug points at the resolved Oct 2025
    # event (legs at exactly 0/1) and must be skipped for the live slug.
    async def fake(url: str) -> str:
        if "fed-decision-in-october-20260617190323537" in url:
            return _poly_event()
        if "fed-decision-in-october" in url:
            return _poly_event_stale()
        raise RuntimeError(f"unexpected url {url}")

    probs, slug = await fed_meetings._poly_probs(
        fake, ["fed-decision-in-october",
               "fed-decision-in-october-20260617190323537"])
    assert slug == "fed-decision-in-october-20260617190323537"
    assert probs["hold"] == pytest.approx(83.5)


@pytest.mark.asyncio
async def test_fetch_fed_meetings_idempotent(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    gt = _get_text_factory(_mapping())
    await fed_meetings.fetch_fed_meetings(store, gt)
    await fed_meetings.fetch_fed_meetings(store, gt)
    assert len(store.points("cycle:fedmeet-kal-202610-hold")) == 1


@pytest.mark.asyncio
async def test_fetch_fed_meetings_degrades_without_kalshi(tmp_path):
    store = Store(str(tmp_path / "t.db"))

    async def no_kalshi(url: str) -> str:
        if "kalshi" in url:
            raise RuntimeError("down")
        return await _get_text_factory(_mapping())(url)

    res = await fed_meetings.fetch_fed_meetings(store, no_kalshi)
    assert "poly" in res and "ff" in res
    assert store.doc("fed_meetings").payload["kalshi"]["probs"] is None
