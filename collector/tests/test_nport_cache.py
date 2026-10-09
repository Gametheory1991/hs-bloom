"""Tests for the shared N-PORT zip download cache (cache logic only;
_stream_download itself hits the network and is stubbed)."""
import zipfile
from pathlib import Path

import pytest

from collector.fetchers import nport_cache


def _make_zip(path: Path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("FUND_REPORTED_INFO.tsv", "a\tb\n1\t2\n")


async def _stubbed(monkeypatch, tmp_path, size_ok=True):
    monkeypatch.setenv("NPORT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(nport_cache, "MIN_ZIP_BYTES", 10)
    calls = []

    async def fake_dl(url, dest, headers):
        calls.append(url)
        if size_ok:
            _make_zip(Path(dest))
            return 100
        Path(dest).write_bytes(b"tiny")
        return 4

    monkeypatch.setattr(nport_cache, "_stream_download", fake_dl)
    return calls


async def test_nport_zip_path_downloads_once(tmp_path, monkeypatch):
    calls = await _stubbed(monkeypatch, tmp_path)
    p1 = await nport_cache.nport_zip_path(2026, 2, {"User-Agent": "t"})
    p2 = await nport_cache.nport_zip_path(2026, 2, {"User-Agent": "t"})
    assert p1 == p2
    assert len(calls) == 1  # second call reused the cache
    assert Path(p1).exists()


async def test_nport_zip_path_redownloads_corrupt_cache(tmp_path, monkeypatch):
    calls = await _stubbed(monkeypatch, tmp_path)
    bad = nport_cache.cache_dir() / "2026q2_nport.zip"
    bad.write_bytes(b"not a zip at all")
    p = await nport_cache.nport_zip_path(2026, 2, {"User-Agent": "t"})
    assert len(calls) == 1  # corrupt cache entry was replaced
    with zipfile.ZipFile(p):
        pass


async def test_nport_zip_path_redownloads_undersize_cache(tmp_path, monkeypatch):
    calls = await _stubbed(monkeypatch, tmp_path)
    small = nport_cache.cache_dir() / "2026q2_nport.zip"
    _make_zip(small)
    small.write_bytes(b"x")  # valid zip overwritten by a too-small file
    await nport_cache.nport_zip_path(2026, 2, {"User-Agent": "t"})
    assert len(calls) == 1


async def test_nport_zip_path_rejects_small_download(tmp_path, monkeypatch):
    await _stubbed(monkeypatch, tmp_path, size_ok=False)
    with pytest.raises(ValueError, match="suspiciously small"):
        await nport_cache.nport_zip_path(2026, 2, {"User-Agent": "t"})
    # nothing cached on failure
    assert list((Path(nport_cache.cache_dir())).glob("*_nport.zip")) == []


async def test_nport_zip_path_prunes_stale_quarters(tmp_path, monkeypatch):
    import time
    calls = await _stubbed(monkeypatch, tmp_path)
    d = nport_cache.cache_dir()
    stale = d / "2025q4_nport.zip"
    _make_zip(stale)
    old = time.time() - 8 * 24 * 3600
    import os
    os.utime(stale, (old, old))
    await nport_cache.nport_zip_path(2026, 2, {"User-Agent": "t"})
    assert len(calls) == 1
    assert not stale.exists()  # pruned: older quarter, stale mtime
    assert (d / "2026q2_nport.zip").exists()
