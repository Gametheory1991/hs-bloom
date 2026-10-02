"""Optional FRED observations over MCP Streamable HTTP."""
from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from collector.http import USER_AGENT


def endpoint() -> str:
    return os.environ.get("FRED_MCP_URL", "").strip()


def observations_json(result: CallToolResult) -> str:
    if result.isError:
        raise ValueError("FRED MCP observations tool failed")
    payload = result.structuredContent
    if payload is None:
        for block in result.content:
            if block.type != "text":
                continue
            try:
                candidate = json.loads(block.text)
            except ValueError:
                continue
            if isinstance(candidate, dict) and "observations" in candidate:
                payload = candidate
                break
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise ValueError("FRED MCP tool must return an observations array")
    return json.dumps(payload)


async def fetch_observations(fred_id: str, api_key: str) -> str:
    url = endpoint()
    parsed = urlsplit(url)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        )
    ):
        raise ValueError("FRED_MCP_URL must use HTTPS (HTTP allowed on loopback) without URL credentials")
    tool = os.environ.get("FRED_MCP_OBSERVATIONS_TOOL", "get_series_observations").strip()
    if not tool:
        raise ValueError("FRED_MCP_OBSERVATIONS_TOOL must not be empty")
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["X-FRED-API-Key"] = api_key
    token = os.environ.get("FRED_MCP_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(
                headers=headers, timeout=30, follow_redirects=False
            ) as client:
                async with streamable_http_client(url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, {"series_id": fred_id})
                        return observations_json(result)
    except Exception:
        # Upstream errors can echo credentials; healthz and scheduler logs are public.
        raise RuntimeError(
            "FRED MCP request failed; check endpoint, credentials and observations tool"
        ) from None
