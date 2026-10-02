import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from collector.config import BondCfg, CbRateCfg, CycleSeriesCfg, SeriesCfg
from collector.fetchers import bonds, cycle, fred, fred_mcp
from collector.store import Store

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "fred_dgs10.json").read_text())
POINTS = [(date(2026, 7, 6), 4.15), (date(2026, 7, 8), 4.12)]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("FRED_MCP_URL", "FRED_MCP_TOKEN", "FRED_MCP_OBSERVATIONS_TOOL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mcp_server(monkeypatch):
    """Exercise the real SDK against an in-memory Streamable HTTP server."""
    monkeypatch.setenv("FRED_MCP_URL", "https://fred.example/mcp")
    state = {
        "requests": [],
        "result": {"content": [], "structuredContent": PAYLOAD, "isError": False},
        "status": 200,
    }

    def respond(request):
        state["requests"].append(request)
        if state["status"] != 200:
            return httpx.Response(
                state["status"], text="sensitive upstream error",
                headers={"Location": "/redirected?api_key=test-key"},
            )
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        if message["method"] == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-fred", "version": "1.0"},
            }
            if state.get("bad_initialize"):
                result = {"protocolVersion": {"sensitive": "test-key"}}
        elif message["method"] == "tools/list":
            result = {
                "tools": [{
                    "name": "get_series_observations",
                    "inputSchema": {"type": "object"},
                }]
            }
        else:
            assert message["method"] == "tools/call"
            state["call"] = message["params"]
            result = state["result"]
        return httpx.Response(
            200,
            headers={"Mcp-Session-Id": "test-session"},
            json={"jsonrpc": "2.0", "id": message["id"], "result": result},
        )

    real_client = httpx.AsyncClient

    def client(**kwargs):
        state["client_options"] = kwargs
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(fred_mcp.httpx, "AsyncClient", client)
    return state


async def no_direct_api(*args, **kwargs):
    raise AssertionError("MCP must not call the direct FRED API")


@pytest.mark.parametrize("structured", [True, False])
async def test_mcp_protocol_and_observations(mcp_server, monkeypatch, structured):
    monkeypatch.setenv("FRED_MCP_TOKEN", "test-token")
    monkeypatch.setenv("FRED_MCP_OBSERVATIONS_TOOL", "custom_observations")
    if not structured:
        mcp_server["result"] = {
            "content": [{"type": "text", "text": json.dumps(PAYLOAD)}],
            "isError": False,
        }
    assert await fred.fetch_series("DGS10", "test-key", no_direct_api) == POINTS
    assert mcp_server["call"] == {
        "name": "custom_observations", "arguments": {"series_id": "DGS10"}
    }
    assert fred.source_name() == "fred-mcp"
    assert mcp_server["client_options"]["follow_redirects"] is False
    for request in mcp_server["requests"]:
        assert request.headers["X-FRED-API-Key"] == "test-key"
        assert request.headers["Authorization"] == "Bearer " + "test-token"
        assert "test-key" not in str(request.url)
    assert any(request.method == "DELETE" for request in mcp_server["requests"])


async def test_server_owned_key(mcp_server):
    assert await fred.fetch_series("DGS10", "", no_direct_api) == POINTS
    for request in mcp_server["requests"]:
        assert "X-FRED-API-Key" not in request.headers
        assert "Authorization" not in request.headers


@pytest.mark.parametrize("result", [
    {"content": [{"type": "text", "text": "sensitive tool error"}], "isError": True},
    {"content": [{"type": "text", "text": "not JSON"}]},
    {"content": [], "structuredContent": {"observations": "not an array"}},
    {"content": [], "structuredContent": {"observations": [{"date": "sensitive date", "value": "1"}]}},
])
async def test_invalid_tool_responses_are_sanitized(mcp_server, result):
    mcp_server["result"] = result
    with pytest.raises(RuntimeError, match="FRED MCP request failed") as exc:
        await fred.fetch_series("DGS10", "test-key", no_direct_api)
    assert "sensitive" not in str(exc.value)
    assert "test-key" not in str(exc.value)


@pytest.mark.parametrize("status", [401, 500, 302])
async def test_transport_failure_does_not_fallback(mcp_server, status):
    mcp_server["status"] = status
    with pytest.raises(RuntimeError, match="FRED MCP request failed"):
        await fred.fetch_series("DGS10", "test-key", no_direct_api)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_sdk_redirects_rejected_before_following(mcp_server, caplog, status):
    mcp_server["status"] = status
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match="FRED MCP request failed"):
            await fred.fetch_series("DGS10", "test-key", no_direct_api)
    assert len(mcp_server["requests"]) == 1
    assert "redirected" not in caplog.text
    assert "test-key" not in caplog.text


async def test_timeout_is_sanitized(monkeypatch):
    monkeypatch.setenv("FRED_MCP_URL", "https://fred.example/mcp")

    def fail(*args, **kwargs):
        raise TimeoutError("sensitive timeout")

    monkeypatch.setattr(fred_mcp, "streamable_http_client", fail)
    with pytest.raises(RuntimeError, match="FRED MCP request failed"):
        await fred_mcp.fetch_observations("DGS10", "test-key")


async def test_sdk_payload_logs_are_suppressed(mcp_server, caplog):
    mcp_server["bad_initialize"] = True
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match="FRED MCP request failed"):
            await fred.fetch_series("DGS10", "test-key", no_direct_api)
    assert "test-key" not in caplog.text
    assert "Raw result" not in caplog.text


def test_shared_session_logging_is_suppressed(caplog):
    record = logging.LogRecord(
        "root", logging.WARNING, "/site-packages/mcp/shared/session.py",
        383, "sensitive upstream data", (), None,
    )
    logging.getLogger().handle(record)
    logging.getLogger("collector").warning("safe collector error")
    assert "sensitive" not in caplog.text
    assert "safe collector error" in caplog.text


@pytest.mark.parametrize("url", [
    "http://fred.example/mcp",
    "https://" + "user:password@" + "fred.example/mcp",
    "https://fred.example/mcp?api_key=secret",
    "https://fred.example/mcp#fragment",
    "file:///tmp/server",
])
async def test_unsafe_endpoint_rejected(monkeypatch, url):
    monkeypatch.setenv("FRED_MCP_URL", url)
    with pytest.raises(ValueError, match="FRED_MCP_URL"):
        await fred_mcp.fetch_observations("DGS10", "test-key")


@pytest.mark.parametrize("url", [
    "http://localhost:8001/mcp", "http://127.0.0.1:8001/mcp", "http://[::1]:8001/mcp",
])
async def test_loopback_http_allowed(mcp_server, monkeypatch, url):
    monkeypatch.setenv("FRED_MCP_URL", url)
    assert await fred.fetch_series("DGS10", "", no_direct_api) == POINTS


def test_text_blocks_ignore_non_observation_content():
    result = CallToolResult(content=[
        ImageContent(type="image", data="", mimeType="image/png"),
        TextContent(type="text", text="explanation"),
        TextContent(type="text", text='{"metadata": {}}'),
        TextContent(type="text", text=json.dumps(PAYLOAD)),
    ])
    assert json.loads(fred_mcp.observations_json(result)) == PAYLOAD


async def test_blank_url_retains_direct_api(monkeypatch):
    monkeypatch.setenv("FRED_MCP_URL", "  ")

    async def get_text(url, params=None):
        assert url == fred.BASE
        assert params["api_key"] == "test-key"
        return json.dumps(PAYLOAD)

    assert await fred.fetch_series("DGS10", "test-key", get_text) == POINTS
    assert fred.source_name() == "fred"


async def test_existing_jobs_use_mcp(mcp_server, tmp_path):
    store = Store(tmp_path / "t.db")
    series = [SeriesCfg(id="us-10y", name="US 10Y", fred="DGS10", unit="%", transform="none")]
    assert await fred.fetch_macro_history(series, store, "", no_direct_api) == "fred-mcp"
    assert store.points("macro:us-10y")
    assert await bonds.fetch_bonds(
        [BondCfg(country="US", tenor="10Y", fred="DGS10")],
        [CbRateCfg(country="US", label="FED", fred="DFEDTARU")], store, no_direct_api, "",
    ) == "fred-mcp"
    assert store.doc("bond_quotes").payload["US10Y"]["source"] == "fred-mcp"
    assert store.doc("bond_quotes").payload["USCB"]["source"] == "fred-mcp"
    assert await cycle.fetch_cycle(
        [CycleSeriesCfg(id="vix", name="VIX", unit="idx", fred="VIXCLS")],
        store, "", no_direct_api, no_direct_api,
    ) == "cycle"
    assert store.points("cycle:vix")


async def test_mcp_failure_preserves_history(mcp_server, tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_points("macro:us-10y", POINTS)
    mcp_server["result"] = {"content": [], "isError": True}
    with pytest.raises(RuntimeError, match="1/1 macro series failed"):
        await fred.fetch_macro_history(
            [SeriesCfg(id="us-10y", name="US 10Y", fred="DGS10", unit="%", transform="none")],
            store, "", no_direct_api,
        )
    assert store.points("macro:us-10y") == dict(POINTS)
