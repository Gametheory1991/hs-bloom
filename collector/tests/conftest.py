"""Isolate tests from the sandbox's proxy environment.

The sandbox sets proxy env vars (including bracketed IPv6 literals in
no_proxy) that httpx 0.28 chokes on when constructing a real AsyncClient.
Production (Render) does not set these. Scrub them for every test so the
suite is hermetic regardless of the machine it runs on.
"""
import os

import pytest

_PROXY_VARS = (
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
)


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
