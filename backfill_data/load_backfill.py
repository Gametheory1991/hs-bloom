"""Load backfilled FINRA datasets into the os-bloom Store.

Reads the CSVs produced by the backfill scripts (or downloaded from Google
Drive) and upserts them into the collector Store (SQLite by default,
Postgres when DATABASE_URL is set).

Usage:
    python3 load_backfill.py --dir ~/workspace/backfill [--dry-run]
    python3 load_backfill.py --dir /path/to/drive/download --only regsho

Datasets:
    regsho  - regsho_daily_aggregates.csv -> cycle:regsho-{mkt}-shortvol etc.
              regsho_daily_top200.csv     -> cycle:regsho-top-{SYM}-shortvol,
                                             cycle:regsho-top-{SYM}-totalvol
    prices  - prices/{SYM}.csv            -> merged into ticker_stats doc
                                             (daily closes for sparkline/YTD/SMA)
    cusips  - cusip_registry.json         -> finra_corp doc cusip_registry
              cusip_daily.csv             -> finra_corp doc bond_hist

Idempotent: upsert_points overwrites same-date points; docs are merged.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/home/hatch/workspace/osb-finra-shorts/collector/src")

from collector.store import Store


def load_regsho(store: Store, d: Path, dry: bool) -> dict:
    stats = {"agg_points": 0, "ticker_points": 0, "days": set()}
    agg = d / "regsho_daily_aggregates.csv"
    if agg.exists():
        per_series: dict[str, list] = {}
        with open(agg) as f:
            for row in csv.DictReader(f):
                day = date.fromisoformat(row["date"])
                mkt = row["market"]
                stats["days"].add(day)
                per_series.setdefault(f"cycle:regsho-{mkt}-shortvol",
                                      []).append((day, float(row["short_vol"])))
                per_series.setdefault(f"cycle:regsho-{mkt}-shortexempt",
                                      []).append((day, float(row["exempt_vol"])))
                per_series.setdefault(
                    f"cycle:regsho-{mkt}-shortratio",
                    []).append((day, float(row["short_ratio"])))
        if not dry:
            for sid, pts in per_series.items():
                store.upsert_points(sid, sorted(pts))
        stats["agg_points"] = sum(len(v) for v in per_series.values())
    top = d / "regsho_daily_top200.csv"
    if top.exists():
        sv: dict[str, list] = {}
        tv: dict[str, list] = {}
        with open(top) as f:
            for row in csv.DictReader(f):
                day = date.fromisoformat(row["date"])
                stats["days"].add(day)
                safe = re.sub(r"[^A-Z0-9]", "", row["symbol"].upper())
                if not safe:
                    continue
                sv.setdefault(f"cycle:regsho-top-{safe}-shortvol",
                              []).append((day, float(row["short_vol"])))
                tv.setdefault(f"cycle:regsho-top-{safe}-totalvol",
                              []).append((day, float(row["total_vol"])))
        if not dry:
            for sid, pts in {**sv, **tv}.items():
                store.upsert_points(sid, sorted(pts))
        stats["ticker_points"] = sum(len(v) for v in sv.values())
    stats["days"] = len(stats["days"])
    return stats


def load_prices(store: Store, d: Path, dry: bool) -> dict:
    """Merge Yahoo daily closes into the ticker_stats doc as price history."""
    stats = {"tickers": 0, "points": 0}
    price_dir = d / "prices"
    if not price_dir.exists():
        return stats
    doc = store.doc("ticker_stats")
    payload = dict(doc.payload) if doc and isinstance(doc.payload, dict) \
        else {"tickers": {}}
    tickers = payload.setdefault("tickers", {})
    for csvf in sorted(price_dir.glob("*.csv")):
        if csvf.name == "manifest.csv":
            continue
        sym = csvf.stem.upper()
        closes = []
        with open(csvf) as f:
            for row in csv.DictReader(f):
                try:
                    closes.append((date.fromisoformat(row["date"]),
                                   float(row["adjclose"] or row["close"])))
                except (ValueError, TypeError):
                    continue
        if not closes:
            continue
        closes.sort()
        t = tickers.setdefault(sym, {})
        hist = t.setdefault("price_hist",
                            {})  # {iso_date: close}
        for day, c in closes:
            hist[day.isoformat()] = c
        stats["tickers"] += 1
        stats["points"] += len(closes)
    if not dry and stats["tickers"]:
        payload["price_hist_as_of"] = date.today().isoformat()
        payload["price_hist_source"] = "yahoo-backfill"
        store.put_doc("ticker_stats", payload, source="backfill")
    return stats


def load_cusips(store: Store, d: Path, dry: bool) -> dict:
    """Merge CUSIP registry + daily observations into the finra_corp doc."""
    stats = {"cusips": 0, "daily_rows": 0}
    reg_f = d / "cusip_registry.json"
    daily_f = d / "cusip_daily.csv"
    doc = store.doc("finra_corp")
    payload = dict(doc.payload) if doc and isinstance(doc.payload, dict) else {}
    if reg_f.exists():
        new_reg = json.loads(reg_f.read_text())
        reg = payload.setdefault("cusip_registry", {})
        for cusip, info in new_reg.items():
            cur = reg.setdefault(cusip, info)
            # keep the earliest first_seen and latest last_seen
            if info.get("first_seen", "9") < cur.get("first_seen", "9"):
                cur["first_seen"] = info["first_seen"]
            if info.get("last_seen", "") > cur.get("last_seen", ""):
                cur["last_seen"] = info["last_seen"]
                for k in ("issuer", "coupon", "maturity", "moodys",
                          "sp", "cat"):
                    if info.get(k):
                        cur[k] = info[k]
            cur["n_obs"] = cur.get("n_obs", 0) + info.get("n_obs", 0)
        stats["cusips"] = len(new_reg)
    if daily_f.exists():
        bond_hist = payload.setdefault("bond_hist", {})
        with open(daily_f) as f:
            for row in csv.DictReader(f):
                cusip = (row.get("cusip") or "").strip()
                day = (row.get("date") or "").strip()
                if not cusip or not day:
                    continue
                hist = bond_hist.setdefault(cusip, [])
                if not any(h.get("date") == day for h in hist):
                    try:
                        hist.append({"date": day,
                                     "price": float(row["price"])
                                     if row.get("price") else None,
                                     "yield": float(row["yield"])
                                     if row.get("yield") else None})
                        stats["daily_rows"] += 1
                    except ValueError:
                        pass
    if not dry and (stats["cusips"] or stats["daily_rows"]):
        store.put_doc("finra_corp", payload, source="backfill")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--only", choices=["regsho", "prices", "cusips"],
                    default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=None,
                    help="SQLite path (default: $BLOOM_DB or ./bloom.db)")
    args = ap.parse_args()
    base = Path(args.only and args.dir or args.dir)
    db_path = args.db or os.environ.get("BLOOM_DB", "./bloom.db")
    store = Store(db_path)
    results = {}
    if not args.only or args.only == "regsho":
        results["regsho"] = load_regsho(store, base / "regsho", args.dry_run)
    if not args.only or args.only == "prices":
        results["prices"] = load_prices(store, base, args.dry_run)
    if not args.only or args.only == "cusips":
        results["cusips"] = load_cusips(store, base / "cusips", args.dry_run)
    print(json.dumps(results, indent=2, default=str))
    if args.dry_run:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
