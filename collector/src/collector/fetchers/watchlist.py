"""Shared watchlist: public-company tickers across the three coverage-map
universes (AI buildout, market structure, tokenized securities) plus the six
hyperscalers. Used by the OpenFIGI symbology job and the Finnhub
earnings/insider job so both enrich the same name set the terminal already
tracks."""
from __future__ import annotations

import json
from importlib.resources import files

UNIVERSE_IDS = ("ai_buildout", "market_structure", "tokenized_securities")

# Hyperscaler desk names (batch 5) — always in the watchlist even if a
# universe file is missing.
HYPERSCALERS = ("MSFT", "NVDA", "AAPL", "AMZN", "GOOGL", "META")


def universe_tickers(universe_id: str) -> list[str]:
    """Public-company tickers in one vendored universe file."""
    try:
        raw = (files("collector") / "data" / f"{universe_id}.json").read_text(
            encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    tickers: list[str] = []
    for vertical in json.loads(raw).get("verticals", []):
        for node in vertical.get("companies", []):
            t = (node.get("ticker") or "").strip().upper()
            # universe files also carry foreign tickers (e.g. DB1.DE);
            # Finnhub/OpenFIGI want plain US symbols — keep those only.
            if t and "." not in t and "-" not in t and t not in tickers:
                tickers.append(t)
    return tickers


def watchlist_tickers() -> list[str]:
    """Deduped watchlist: hyperscalers first, then the three universes."""
    out: list[str] = list(HYPERSCALERS)
    for uid in UNIVERSE_IDS:
        for t in universe_tickers(uid):
            if t not in out:
                out.append(t)
    return out
