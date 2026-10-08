"""HTTP helpers, injected into fetchers so tests never touch the network."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import asyncio
import logging
import random
import subprocess

import httpx

# Honest, contactable User-Agent. Standard `product/version (comment)` syntax:
# some upstream WAFs (aaii.com) reject a bare product token, so the comment is
# load-bearing, not decoration. We never impersonate a browser -- upstreams can
# identify and contact us, and every source we use serves this UA fine.
USER_AGENT = "hs-bloom/0.1 (+https://github.com/cleyfe/hs-bloom)"

GetText = Callable[..., Awaitable[str]]
GetBytes = Callable[..., Awaitable[bytes]]
PostJson = Callable[..., Awaitable[dict]]
PostText = Callable[..., Awaitable[str]]

log = logging.getLogger(__name__)

# Retry policy for transient upstream failures. 429 / 5xx / timeouts /
# connection errors get exponential backoff with jitter; every other 4xx
# fails fast (permanent). Module-level constants so tests can zero the
# delays via monkeypatch.
RETRY_MAX_ATTEMPTS = 4    # initial try + 3 retries
RETRY_BASE_DELAY = 1.0     # seconds; doubles with each attempt
RETRY_MAX_DELAY = 30.0     # cap on any single backoff sleep
RETRY_AFTER_CAP = 60.0     # cap when honoring a Retry-After header
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _safe_url(url: str | httpx.URL) -> str:
    """URL with the query string stripped: it can carry API keys, and retry
    warnings end up in logs and the fetcher_status table."""
    u = url if isinstance(url, httpx.URL) else httpx.URL(url)
    return str(u.copy_with(query=None))


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    # Honor Retry-After when the upstream tells us how long to wait.
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), RETRY_AFTER_CAP)
        except (TypeError, ValueError):
            pass
    return min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY) * random.uniform(0.5, 1.5)


async def _with_retry(
    method: str, url: str, do_request: Callable[[], Awaitable[httpx.Response]]
) -> httpx.Response:
    """Run one HTTP request, retrying transient failures with backoff.

    Returns the final response; the caller still raises on status >= 400,
    preserving the existing RuntimeError contract (including the
    query-stripped URL). Non-transient 4xx return immediately, unretried.
    """
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            resp = await do_request()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == RETRY_MAX_ATTEMPTS - 1:
                raise
            delay = _retry_delay(attempt, None)
            log.warning("%s %s failed (%s); retry %d/%d in %.1fs",
                        method, _safe_url(url), exc,
                        attempt + 1, RETRY_MAX_ATTEMPTS - 1, delay)
            await asyncio.sleep(delay)
            continue
        if resp.status_code in RETRYABLE_STATUS and attempt < RETRY_MAX_ATTEMPTS - 1:
            delay = _retry_delay(attempt, resp.headers.get("retry-after"))
            log.warning("%s %s: HTTP %d; retry %d/%d in %.1fs",
                        method, _safe_url(resp.url), resp.status_code,
                        attempt + 1, RETRY_MAX_ATTEMPTS - 1, delay)
            await asyncio.sleep(delay)
            continue
        return resp
    raise AssertionError("unreachable: _with_retry loop always returns or raises")


async def get_text(url: str, params: dict | None = None, headers: dict | None = None) -> str:
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers=headers or {"User-Agent": USER_AGENT},
    ) as client:
        resp = await _with_retry("GET", url, lambda: client.get(url, params=params))
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
        resp = await _with_retry("GET", url, lambda: client.get(url, params=params))
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
        resp = await _with_retry("POST", url, lambda: client.post(url, json=json))
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
        resp = await _with_retry("POST", url, lambda: client.post(url, json=json))
        if resp.status_code >= 400:
            # strip the query string: same rationale as get_text
            raise RuntimeError(
                f"HTTP {resp.status_code} for {resp.url.copy_with(query=None)}"
            ) from None
        return resp.text
