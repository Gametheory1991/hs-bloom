"""Shared N-PORT batch ZIP download cache.

sec_nport.py (holdings aggregates) and nport_flows.py (per-fund monthly
flows) both need the same ~420 MB quarterly ZIP from SEC EDGAR. They run
staggered ~150 s apart in the same process, so without a shared cache the
archive is downloaded twice per polling window.

nport_zip_path(year, q, headers) downloads once per quarter into a cache
dir and returns the local path; the second caller reuses it. Each caller
still does its own parse pass and keeps its own series namespace.
Filings are immutable per quarter, so a size + zip-integrity validated
cache entry is safe to reuse. Not unit-testable (network); the cache-hit
and corrupt-cache paths are tested via a stubbed _stream_download.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

ZIP_URL = ("https://www.sec.gov/files/dera/data/form-n-port-data-sets/"
           "{year}q{q}_nport.zip")

# Batches are ~420 MB; anything far smaller is a truncated/error response.
MIN_ZIP_BYTES = 50_000_000

# Prune cached quarter zips older than this (mtime); the jobs only ever
# read the latest quarter, so anything older is a leftover backfill.
PRUNE_AFTER_SECONDS = 7 * 24 * 3600

_locks: dict[tuple[int, int], asyncio.Lock] = {}


def cache_dir() -> Path:
    """Shared cache location; overridable for tests via NPORT_CACHE_DIR."""
    d = Path(os.environ.get("NPORT_CACHE_DIR",
                            Path(tempfile.gettempdir()) / "nport-zip-cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(year: int, q: int) -> Path:
    return cache_dir() / f"{year}q{q}_nport.zip"


def _valid_zip(path: Path) -> bool:
    """A cache entry is usable if it is big enough and opens as a zip."""
    try:
        if path.stat().st_size < MIN_ZIP_BYTES:
            return False
        with zipfile.ZipFile(path) as z:
            z.namelist()
        return True
    except (OSError, zipfile.BadZipFile):
        return False


async def _stream_download(url: str, dest: str, headers: dict) -> int:
    """Stream a URL to a file in chunks; returns bytes written.

    httpx directly (not the injected get_bytes): the N-PORT zip is ~420 MB
    and must never sit fully in RAM. Not unit-testable (network).
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


def _prune_old(except_path: Path) -> None:
    """Best-effort: drop cached quarter zips older than PRUNE_AFTER_SECONDS.

    Only touches files for other quarters; a file being parsed by the
    sibling job was necessarily written minutes ago, never days.
    """
    now = time.time()
    try:
        for p in cache_dir().glob("*_nport.zip"):
            if p == except_path:
                continue
            try:
                if now - p.stat().st_mtime > PRUNE_AFTER_SECONDS:
                    p.unlink()
                    log.info("nport-cache: pruned stale %s", p.name)
            except OSError:
                pass
    except OSError:
        pass


async def nport_zip_path(year: int, q: int, headers: dict) -> str:
    """Local path of the N-PORT batch ZIP for (year, q); downloads once.

    Concurrent callers for the same quarter serialize on a per-quarter
    lock so only one of them downloads. Raises on download failure (the
    caller probes the next older quarter, as before).
    """
    path = _cache_path(year, q)
    lock = _locks.setdefault((year, q), asyncio.Lock())
    async with lock:
        if path.exists():
            if _valid_zip(path):
                log.info("nport-cache: reusing %s", path.name)
                return str(path)
            log.warning("nport-cache: %s invalid (size/integrity); re-downloading",
                        path.name)
            try:
                path.unlink()
            except OSError:
                pass
        url = ZIP_URL.format(year=year, q=q)
        tmp = path.with_suffix(".zip.part")
        log.info("nport-cache: downloading %s", url)
        size = await _stream_download(url, str(tmp), headers)
        if size < MIN_ZIP_BYTES:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise ValueError(f"suspiciously small N-PORT zip ({size}B) for {url}")
        if not _valid_zip(tmp):
            try:
                tmp.unlink()
            except OSError:
                pass
            raise ValueError(f"downloaded N-PORT zip failed integrity check: {url}")
        os.replace(tmp, path)  # atomic publish; concurrent readers see it whole
        _prune_old(path)
        return str(path)
