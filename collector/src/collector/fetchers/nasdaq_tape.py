"""NasdaqTrader Full Volume Summary — tape/exchange volume data.

Keyless CSVs (browser UA), published daily after the close:
  30days.csv            shares, last 30 trading days
  30daystrades.csv      trades, last 30 trading days
  30daysdollarVol.csv   dollar volume, last 30 trading days

Columns: Date,Exchange,Tape A,Tape A %,Tape B,Tape B %,Tape C,Tape C %,
         Total,Total %,Tape A Moving,...,Total % Moving
("Moving" = Nasdaq's own 10-day MA; we recompute our own from history.)

Tapes: A = NYSE-listed · B = NYSE Arca/American-listed · C = Nasdaq-listed.
21 venues: lit exchanges + FINRA TRFs (off-exchange prints).

Harry's directive (2026-10-06): "Pull all historical and save it so you
could have the comparison as far as you can." The source publishes only
30 days, so every run upserts the FULL 30-day window (idempotent — a
re-run never duplicates) and the DB accumulates from there. 1D/1M deltas
work immediately; 1Q/1Y unlock as history builds.

Stored:
  cycle:tape-{shares|trades|dollar}-{slug}-{a|b|c|total}   per-venue series
  cycle:tape-{shares|trades|dollar}-all-{a|b|c|total}      market-wide series
  doc "tape": rolling daily history per venue per metric for the UI
  grid (one fetch, no 250-series fan-out) + scope/history-depth labels.

Daily cadence, after the equity close.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

SOURCE = "tape"

BASE = "https://www.nasdaqtrader.com/dynamic/MarketStats"
FILES = {
    "shares": f"{BASE}/30days.csv",
    "trades": f"{BASE}/30daystrades.csv",
    "dollar": f"{BASE}/30daysdollarVol.csv",
}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

TAPE_LABELS = {"a": "Tape A — NYSE-listed",
               "b": "Tape B — NYSE Arca/American-listed",
               "c": "Tape C — Nasdaq-listed"}

METRIC_LABELS = {"shares": "Shares", "trades": "Trades", "dollar": "Dollar volume"}


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[/\s]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    return re.sub(r"-+", "-", s).strip("-")


def _num(x) -> float | None:
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("$", "").strip() or "nan")
    except (TypeError, ValueError):
        return None


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_tape_csv(text: str) -> list[dict]:
    """Parse one NasdaqTrader volume CSV into row dicts."""
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        d = _parse_date(r.get("Date"))
        exch = (r.get("Exchange") or "").strip()
        if d is None or not exch:
            continue
        rows.append({
            "date": d, "exchange": exch, "slug": slugify(exch),
            "a": _num(r.get("Tape A")), "b": _num(r.get("Tape B")),
            "c": _num(r.get("Tape C")), "total": _num(r.get("Total")),
        })
    return rows


async def fetch_nasdaq_tape(store: Store, get_text: GetText) -> str:
    """Pull all 3 tape-volume CSVs; upsert the full 30-day window."""
    # (metric, date, slug) -> {name, a, b, c, total}
    data: dict[tuple[str, date, str], dict] = {}
    parts: list[str] = []
    for metric, url in FILES.items():
        try:
            text = await get_text(url, headers=UA)
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail
            log.warning("nasdaq_tape: %s failed: %s", metric, exc)
            parts.append(f"{metric}: fetch failed")
            continue
        rows = parse_tape_csv(text)
        n = 0
        for r in rows:
            if r["total"] is None:
                continue
            data[(metric, r["date"], r["slug"])] = {
                "name": r["exchange"], "a": r["a"] or 0.0,
                "b": r["b"] or 0.0, "c": r["c"] or 0.0,
                "total": r["total"],
            }
            n += 1
        parts.append(f"{metric}: {n} rows")

    if not data:
        return "no data; " + "; ".join(parts)

    dates = sorted({d for _, d, _ in data})
    venues: dict[str, str] = {}
    for (m, d, slug), v in data.items():
        venues.setdefault(slug, v["name"])

    # Per-venue + market-wide series.
    series: dict[str, list[tuple[date, float]]] = {}
    for (metric, d, slug), v in data.items():
        for tape in ("a", "b", "c", "total"):
            series.setdefault(f"cycle:tape-{metric}-{slug}-{tape}", []).append((d, v[tape]))
    market: dict[tuple[str, date, str], float] = {}
    for (metric, d, slug), v in data.items():
        for tape in ("a", "b", "c", "total"):
            key = (metric, d, tape)
            market[key] = market.get(key, 0.0) + v[tape]
    for (metric, d, tape), val in market.items():
        series.setdefault(f"cycle:tape-{metric}-all-{tape}", []).append((d, val))
    for sid, pts in series.items():
        store.upsert_points(sid, sorted(pts))

    # Doc for the UI grid: rolling daily history per venue per metric.
    hist: dict[str, dict] = {}
    for slug, name in venues.items():
        entry: dict = {"name": name, "slug": slug}
        for metric in FILES:
            arr = []
            for d in dates:
                v = data.get((metric, d, slug))
                arr.append([d.isoformat(), v["a"], v["b"], v["c"], v["total"]] if v
                           else [d.isoformat(), None, None, None, None])
            entry[metric] = arr
        hist[slug] = entry
    mkt_hist = {}
    for metric in FILES:
        mkt_hist[metric] = [[d.isoformat()] + [market.get((metric, d, t), 0.0)
                                               for t in ("a", "b", "c", "total")]
                            for d in dates]

    store.put_doc("tape", {
        "as_of": dates[-1].isoformat(),
        "dates": [d.isoformat() for d in dates],
        "history_days": len(dates),
        "max_source_days": 30,
        "note": ("NasdaqTrader Full Volume Summary publishes 30 trading days; "
                 "the terminal accumulates daily from first pull, so 1Q/1Y "
                 "horizons unlock as history builds."),
        "venues": hist,
        "market": mkt_hist,
        "tape_labels": TAPE_LABELS,
        "metric_labels": METRIC_LABELS,
    }, SOURCE)
    return (f"{len(venues)} venues × {len(dates)} days × 3 metrics "
            f"({len(series)} series); " + "; ".join(parts))
