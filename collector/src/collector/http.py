"""HTTP helpers, injected into fetchers so tests never touch the network."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

# Honest, contactable User-Agent. Standard `product/version (comment)` syntax:
# some upstream WAFs (aaii.com) reject a bare product token, so the comment is
# load-bearing, not decoration. We never impersonate a browser -- upstreams can
# identify and contact us, and every source we use serves this UA fine.
USER_AGENT = "os-bloom/0.1 (+https://github.com/cleyfe/os-bloom)"

GetText = Callable[..., Awaitable[str]]
GetBytes = Callable[..., Awaitable[bytes]]
PostJson = Callable[..., Awaitable[dict]]


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


async def post_webhook(url: str, json: Any, headers: dict | None = None) -> dict:
    """Webhooks commonly return empty 2xx bodies; never follow credential redirects."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})}) as client:
        response = await client.post(url, json=json)
        if not 200 <= response.status_code < 300:
            raise RuntimeError("webhook delivery failed") from None
        return {}
