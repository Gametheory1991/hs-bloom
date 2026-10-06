"""One-time backfill loader: extracts bundled backfill zips into the Store on startup.

Idempotent: uses a marker doc in the store; skips if already loaded.
Safe to run on every boot.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

MARKER_KEY = "backfill_v1_loaded"


def maybe_load_backfill(store, backfill_dir: Path) -> bool:
    """Load backfill data if not already loaded. Returns True if loaded this run."""
    try:
        # Check marker
        existing = store.doc(MARKER_KEY)
        if existing:
            return False
    except Exception:
        pass  # try loading anyway

    if not backfill_dir.is_dir():
        log.info("backfill: no backfill_data dir at %s, skipping", backfill_dir)
        return False

    zips = list(backfill_dir.glob("*.zip"))
    if not zips:
        log.info("backfill: no zips found, skipping")
        return False

    log.info("backfill: extracting %d zips from %s", len(zips), backfill_dir)
    extract_dir = backfill_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)

    for zf in zips:
        try:
            with zipfile.ZipFile(zf) as z:
                z.extractall(extract_dir)
            log.info("backfill: extracted %s", zf.name)
        except Exception as e:
            log.warning("backfill: failed to extract %s: %s", zf.name, e)

    # Now run the loader logic inline (adapted from load_backfill.py)
    # to avoid import path issues in the container
    try:
        _load_all(store, extract_dir)
        # Set marker
        try:
            store.put_doc(MARKER_KEY, {"loaded": True}, "backfill")
        except Exception:
            pass
        log.info("backfill: complete")
        return True
    except Exception as e:
        log.warning("backfill: loading failed: %s", e)
        return False


def _load_all(store, d: Path) -> None:
    """Load all extracted backfill datasets into the store."""
    import csv
    import json
    from datetime import date

    # 1. Reg SHO aggregates + top200
    agg = d / "regsho_daily_aggregates.csv"
    if agg.exists():
        per_series: dict[str, list] = {}
        with open(agg) as f:
            for row in csv.DictReader(f):
                day = date.fromisoformat(row["date"])
                mkt = row["market"]
                per_series.setdefault(f"cycle:regsho-{mkt}-shortvol", []).append(
                    (day, float(row["short_vol"])))
                per_series.setdefault(f"cycle:regsho-{mkt}-totalvol", []).append(
                    (day, float(row["total_vol"])))
        for sid, points in per_series.items():
            try:
                store.upsert_points(sid, points)
            except Exception as e:
                log.warning("backfill regsho agg %s: %s", sid, e)
        log.info("backfill: regsho aggregates loaded (%d series)", len(per_series))

    top200 = d / "regsho_daily_top200.csv"
    if top200.exists():
        per_ticker: dict[str, list] = {}
        with open(top200) as f:
            for row in csv.DictReader(f):
                day = date.fromisoformat(row["date"])
                sym = row["symbol"]
                per_ticker.setdefault(f"cycle:regsho-top-{sym}-shortvol", []).append(
                    (day, float(row["short_vol"])))
        for sid, points in per_ticker.items():
            try:
                store.upsert_points(sid, points)
            except Exception:
                pass
        log.info("backfill: regsho top200 loaded (%d tickers)", len(per_ticker))

    # 2. Prices -> ticker_stats doc
    price_dir = d / "prices"
    if not price_dir.is_dir():
        # prices may be at top level of extract
        price_files = list(d.glob("*.csv"))
        price_files = [p for p in price_files if len(p.stem) <= 6 and p.stem.isupper()]
    else:
        price_files = list(price_dir.glob("*.csv"))

    if price_files:
        try:
            existing_doc = store.doc("ticker_stats")
            doc = (existing_doc.payload.get("price_hist", {}) if existing_doc else {})
        except Exception:
            doc = {}
        for pf in price_files:
            sym = pf.stem
            closes = []
            with open(pf) as f:
                for row in csv.DictReader(f):
                    try:
                        closes.append((row.get("date", row.get("Date", "")),
                                       float(row.get("close", row.get("Close", 0)))))
                    except (ValueError, KeyError):
                        continue
            if closes:
                doc[sym] = closes[-260:]  # keep last year
        try:
            store.put_doc("ticker_stats", {"price_hist": doc}, "backfill")
            log.info("backfill: prices loaded (%d tickers)", len(doc))
        except Exception as e:
            log.warning("backfill prices: %s", e)

    # 3. CUSIP registry
    reg_json = d / "cusip_registry.json"
    if reg_json.exists():
        try:
            with open(reg_json) as f:
                registry = json.load(f)
            try:
                existing_doc = store.doc("finra_corp")
                existing = (existing_doc.payload.get("cusip_registry", {}) if existing_doc else {})
            except Exception:
                existing = {}
            existing.update(registry)
            # Merge into finra_corp doc preserving other keys
            try:
                full = store.doc("finra_corp")
                payload = dict(full.payload) if full else {}
            except Exception:
                payload = {}
            payload["cusip_registry"] = existing
            store.put_doc("finra_corp", payload, "backfill")
            log.info("backfill: cusip registry loaded (%d cusips)", len(registry))
        except Exception as e:
            log.warning("backfill cusip registry: %s", e)

    log.info("backfill: all datasets processed")
