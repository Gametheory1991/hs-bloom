"""HTTP helpers, injected into fetchers so tests never touch the network."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import asyncio
import subprocess

import httpx

# Honest, contactable User-Agent. Standard `product/version (comment)` syntax:
# some upstream WAFs (aaii.com) reject a bare product token, so the comment is
# load-bearing, not decoration. We never impersonate a browser -- upstreams can
# identify and contact us, and every source we use serves this UA fine.
USER_AGENT = "os-bloom/0.1 (+https://github.com/cleyfe/os-bloom)"

GetText = Callable[..., Awaitable[str]]
GetBytes = Callable[..., Awaitable[bytes]]
PostJson = Callable[..., Awaitable[dict]]
PostText = Callable[..., Awaitable[str]]


async def get_text(url: str, params: dict | None = None, headers: dict | None = None) -> str:
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers=headers or {"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url, params=params)
        if resp.status_code >= 400:
            # strip the query string: it can carry API keys, and this message
            # ends up in logs and the fetcher_status table
            raise RuntimeError(
                f"HTTP {resp.status_code} for {resp.url.copy_with(query=None)}"
            ) from None
        return resp.text


async def get_text_curl(url: str, params: dict | None = None, headers: dict | None = None) -> str:
    """GET via the curl binary (subprocess), for WAF-hostile hosts.

    FRED's WAF blackholes Python-TLS clients (httpx/urllib) from the Render
    host regardless of User-Agent -- requests hang to timeout -- while the
    curl binary fetches the same public CSVs in ~1s (verified 2026-10-06).
    Runs the blocking subprocess in a thread so the event loop stays free.
    """
    from urllib.parse import urlencode

    full = url + ("?" + urlencode(params) if params else "")
    ua = (headers or {}).get("User-Agent", USER_AGENT)

    def _run() -> str:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "25", "-A", ua, full],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"curl exit {r.returncode} for {url}: {r.stderr.strip()[:200]}"
            )
        return r.stdout

    return await asyncio.to_thread(_run)


async def get_bytes(url: str, params: dict | None = None, headers: dict | None = None) -> bytes:
    async with httpx.AsyncClient(
        timeout=30,  # binary sources (Excel files) are MB-sized
        follow_redirects=True,
        headers=headers or {"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url, params=params)
        if resp.status_code >= 400:
            # strip the query string: same rationale as get_text
            raise RuntimeError(
                f"HTTP {resp.status_code} for {resp.url.copy_with(query=None)}"
            ) from None
        return resp.content


async def post_json(url: str, json: Any, headers: dict | None = None) -> dict:
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers=headers or {"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.post(url, json=json)
        if resp.status_code >= 400:
            # strip the query string: same rationale as get_text
            raise RuntimeError(
                f"HTTP {resp.status_code} for {resp.url.copy_with(query=None)}"
            ) from None
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"non-dict JSON body for {resp.url.copy_with(query=None)}")
        return body


async def post_text(url: str, json: Any, headers: dict | None = None) -> str:
    """POST a JSON body, return the raw text response.

    For APIs (e.g. FINRA's Query API) that answer POSTs with CSV/pipe text
    rather than JSON.
    """
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers=headers or {"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.post(url, json=json)
        if resp.status_code >= 400:
            # strip the query string: same rationale as get_text
            raise RuntimeError(
                f"HTTP {resp.status_code} for {resp.url.copy_with(query=None)}"
            ) from None
        return resp.text
