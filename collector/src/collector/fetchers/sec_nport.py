"""SEC Form N-PORT — monthly portfolio holdings, public 3rd-month reports (quarterly batch).

Index: https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets
ZIP pattern (verified live 2026-10-05; 2026 Q2 = 440,699,889 bytes, ~420 MB):
  https://www.sec.gov/files/dera/data/form-n-port-data-sets/YYYYqN_nport.zip
ZIP of 32 TSVs + readme + metadata. Dashboard value is in AGGREGATES, so the
fetcher extracts only:
  FUND_REPORTED_INFO.tsv      (NET_ASSETS, TOTAL_ASSETS, BORROWING_PAY_WITHIN_1YR)
  derivative schedules        (SWAPTION_OPTION_WARNT_DERIV,
                               FUT_FWD_NONFOREIGNCUR_CONTRACT,
                               FWD_FOREIGNCUR_CONTRACT_SWAP,
                               NONFOREIGN_EXCHANGE_SWAP,
                               OTHER_DERIV_NOTIONAL_AMOUNT)
  SECURITIES_LENDING.tsv
The holding-level detail (FUND_REPORTED_HOLDING etc.) is never read, and the
full archive is never unpacked to disk — members are streamed from the zip.

HOLDINGS LAG: funds file monthly (30 days); only the 3rd month of each
fiscal quarter is made public, 60 days after quarter-end. As of Oct 2026 the
practical latest is the 2026 Q2 batch (public holdings mostly as of Mar 2026
— ~6-month holdings lag). The doc and panel title carry this lag note.

Memory: the zip streams to a temp file in chunks (never full RAM); rows are
streamed from the zip members (never listed). The temp file is deleted after
parsing.

SEC fair-access rules: declared contact UA (SEC 403s generic UAs), <=1 req/2s.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import tempfile
import zipfile
from datetime import date

import httpx

from collector.config import SecDataCfg
from collector.fetchers.sec_ncen import (  # shared SEC batch plumbing
    REQUEST_GAP, probe_batches, quarter_end,
)
from collector.store import Store

log = logging.getLogger(__name__)

ZIP_URL = ("https://www.sec.gov/files/dera/data/form-n-port-data-sets/"
           "{year}q{q}_nport.zip")
SOURCE = "sec-nport"

# members we actually read (upper-cased for case-insensitive matching)
INFO_TSV = "FUND_REPORTED_INFO.TSV"
DERIV_TSVS = (
    "SWAPTION_OPTION_WARNT_DERIV.TSV",
    "FUT_FWD_NONFOREIGNCUR_CONTRACT.TSV",
    "FWD_FOREIGNCUR_CONTRACT_SWAP.TSV",
    "NONFOREIGN_EXCHANGE_SWAP.TSV",
    "OTHER_DERIV_NOTIONAL_AMOUNT.TSV",
)
SECLEND_TSV = "SECURITIES_LENDING.TSV"


def _to_float(raw: str | None) -> float | None:
    try:
        return float((raw or "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _notional_columns(fieldnames: list[str] | None) -> list[str]:
    """Defensive: first columns whose name mentions notional (else value)."""
    if not fieldnames:
        return []
    notional = [c for c in fieldnames if "notional" in c.lower()]
    if notional:
        return notional
    return [c for c in fieldnames if c.lower() in ("value", "amount", "fair_value")]


async def _download(url: str, dest: str, headers: dict) -> int:
    """Stream a URL to a file in chunks; returns bytes written.

    httpx directly (not the injected get_bytes): the N-PORT zip is ~420 MB
    and must never sit fully in RAM. Not unit-testable (network); the parse
    path below is the tested unit.
    """
    size = 0
    async with httpx.AsyncClient(timeout=600, follow_redirects=True,
                                 headers=headers) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} for {url}")
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 20):  # 1 MB chunks
                    fh.write(chunk)
                    size += len(chunk)
    return size


def _stream_rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8",
                                                  errors="replace"),
                                delimiter="\t")
        for row in reader:
            yield row


def parse_nport_zip(path: str) -> dict:
    """Aggregate-first parse of an N-PORT batch zip from a local file path.

    Only the aggregate members are opened; returns the aggregate dict.
    """
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip file: {exc}") from exc
    by_upper = {n.upper(): n for n in z.namelist()}
    if INFO_TSV not in by_upper:
        raise ValueError(f"no {INFO_TSV} in zip: {list(by_upper)[:6]}")

    net_assets = total_assets = borrow_1y = 0.0
    n_funds = 0
    for row in _stream_rows(z, by_upper[INFO_TSV]):
        n_funds += 1
        v = _to_float(row.get("NET_ASSETS"))
        if v is not None:
            net_assets += v
        v = _to_float(row.get("TOTAL_ASSETS"))
        if v is not None:
            total_assets += v
        v = _to_float(row.get("BORROWING_PAY_WITHIN_1YR"))
        if v is not None:
            borrow_1y += v
    if n_funds == 0:
        raise ValueError(f"{INFO_TSV} has no data rows")

    deriv_notional = 0.0
    deriv_schedules = 0
    for tsv in DERIV_TSVS:
        if tsv not in by_upper:
            log.debug("nport: %s absent from batch", tsv)
            continue
        cols: list[str] | None = None
        sched_total = 0.0
        for row in _stream_rows(z, by_upper[tsv]):
            if cols is None:
                cols = _notional_columns(list(row.keys()))
                if not cols:
                    log.warning("nport: no notional column in %s: %s",
                                tsv, list(row.keys())[:8])
                    break
            for c in cols:
                v = _to_float(row.get(c))
                if v is not None:
                    sched_total += v
        if cols:
            deriv_schedules += 1
            deriv_notional += sched_total

    seclend = 0.0
    if SECLEND_TSV in by_upper:
        cols = None
        for row in _stream_rows(z, by_upper[SECLEND_TSV]):
            if cols is None:
                cols = _notional_columns(list(row.keys()))
                if not cols:
                    log.warning("nport: no value column in %s", SECLEND_TSV)
                    break
            for c in cols:
                v = _to_float(row.get(c))
                if v is not None:
                    seclend += v

    return {
        "nport-net-assets": net_assets,
        "nport-total-assets": total_assets,
        "nport-borrow-1y": borrow_1y,
        "nport-deriv-notional": deriv_notional,
        "nport-seclend": seclend,
        "nport-fund-count": n_funds,
        "nport-deriv-schedules": deriv_schedules,
    }


async def fetch_sec_nport(cfg: SecDataCfg, store: Store,
                          today: date | None = None) -> str:
    """Monthly poll: latest N-PORT batch, aggregate-first, streamed download."""
    headers = {"User-Agent": cfg.user_agent}
    errors: list[str] = []
    for year, q in probe_batches(today):
        url = ZIP_URL.format(year=year, q=q)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip",
                                          prefix="nport-")
        tmp.close()
        try:
            size = await _download(url, tmp.name, headers)
            if size < 50_000_000:
                raise ValueError(f"suspiciously small ({size}B)")
            agg = parse_nport_zip(tmp.name)
            asof = quarter_end(year, q)
            for key, val in agg.items():
                if key.startswith("nport-") and val is not None and not key.endswith(
                        ("count", "schedules")):
                    store.upsert_points(f"cycle:{key}", [(asof, float(val))])
            store.put_doc("sec_nport", {
                "batch": f"{year}q{q}",
                "as_of": asof.isoformat(),
                "note": ("Aggregate-first parse; holding-level detail discarded. "
                         "~6-month holdings lag: only 3rd-month reports are public, "
                         "released 60 days after quarter-end."),
                **{k: v for k, v in agg.items() if v is not None},
            }, source=SOURCE)
            return SOURCE
        except Exception as exc:  # noqa: BLE001 — probe next older quarter
            errors.append(f"{year}q{q}: {exc}")
            await asyncio.sleep(REQUEST_GAP)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    raise RuntimeError("no N-PORT batch in probe window: " + "; ".join(errors[:4]))
