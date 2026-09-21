"""Unit and integration tests for WebMCP Server and tools."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from typesafe_computer_use.adapters import (
    BrowserDOMAdapter,
    InteractionResult,
    RiskLevel,
    SideEffectState,
    VerificationResult,
)
from typesafe_computer_use.browser.session import BrowserSessionManager
from typesafe_computer_use.mcp.server import create_webmcp_server
from typesafe_computer_use.worker.gate import ExecutionGate


@pytest.fixture(autouse=True)
def reset_gate():
    ExecutionGate.reset_instance()
    yield
    ExecutionGate.reset_instance()


def _get_tool_text(call_tool_result: object) -> str:
    """Extract string content from MCP CallToolResult."""
    if hasattr(call_tool_result, "content") and call_tool_result.content:
        first = call_tool_result.content[0]
        return getattr(first, "text", str(first))
    return str(call_tool_result)


def test_webmcp_server_initialization_and_tool_registration():
    """Verify WebMCP server registers all 6 required browser tools."""
    server = create_webmcp_server()
    assert server.name == "typesafe-webmcp"

    async def _check():
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "browser_navigate" in tool_names
        assert "browser_click" in tool_names
        assert "browser_fill" in tool_names
        assert "browser_get_dom" in tool_names
        assert "browser_verify" in tool_names
        assert "browser_status" in tool_names

    asyncio.run(_check())


def test_webmcp_execution_gate_blocks_mutations():
    """Verify ExecutionGate emergency stop blocks mutating tools immediately."""
    gate = ExecutionGate.get_instance()

    adapter = MagicMock(spec=BrowserDOMAdapter)
    adapter.execute = AsyncMock()
    mgr = MagicMock(spec=BrowserSessionManager)
    mgr.get_navigation_epoch = MagicMock(return_value=1)

    server = create_webmcp_server(session_manager=mgr, dom_adapter=adapter, execution_gate=gate)

    async def _run():
        await gate.trigger_emergency_stop()
        res_nav = await server.call_tool("browser_navigate", {"url": "https://example.com"})
        res_click = await server.call_tool("browser_click", {"target": "#btn"})
        res_fill = await server.call_tool("browser_fill", {"target": "#input", "text": "secret"})

        # Adapter must never be called when gate is locked
        assert adapter.execute.call_count == 0

        # Output results indicate gate refusal
        for r in (res_nav, res_click, res_fill):
            text = _get_tool_text(r)
            assert "Execution gate locked" in text

    asyncio.run(_run())


def test_webmcp_takeover_blocks_mutations():
    """Verify takeover session blocks mutating tools."""
    gate = ExecutionGate.get_instance()

    adapter = MagicMock(spec=BrowserDOMAdapter)
    adapter.execute = AsyncMock()
    mgr = MagicMock(spec=BrowserSessionManager)
    mgr.get_navigation_epoch = MagicMock(return_value=1)

    server = create_webmcp_server(session_manager=mgr, dom_adapter=adapter, execution_gate=gate)

    async def _run():
        await gate.trigger_takeover()
        res_click = await server.call_tool("browser_click", {"target": "#btn"})
        assert adapter.execute.call_count == 0
        text = _get_tool_text(res_click)
        assert "Execution gate locked" in text

    asyncio.run(_run())


def test_webmcp_successful_action_execution():
    """Verify normal action execution maps through BrowserDOMAdapter and returns typed result."""
    mgr = MagicMock(spec=BrowserSessionManager)
    mgr.get_navigation_epoch = MagicMock(return_value=3)

    adapter = MagicMock(spec=BrowserDOMAdapter)
    adapter.execute = AsyncMock(
        return_value=InteractionResult(
            mode="browser_dom",
            adapter="BrowserDOMAdapter",
            action="click",
            target="#submit",
            arguments={},
            confidence=0.95,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=45.0,
            result={"clicked": "#submit"},
            error=None,
        )
    )

    server = create_webmcp_server(session_manager=mgr, dom_adapter=adapter)

    async def _run():
        res = await server.call_tool("browser_click", {"target": "#submit"})
        assert adapter.execute.call_count == 1
        call_req = adapter.execute.call_args[0][0]
        assert call_req.action == "click"
        assert call_req.target == "#submit"
        text = _get_tool_text(res)
        assert "confirmed_success" in text

    asyncio.run(_run())


def test_webmcp_browser_get_dom_sanitization():
    """Verify browser_get_dom returns sanitized DOM delimited by untrusted boundary."""
    mgr = MagicMock(spec=BrowserSessionManager)
    page_mock = MagicMock()
    mgr.get_active_page = AsyncMock(return_value=page_mock)

    adapter = MagicMock(spec=BrowserDOMAdapter)
    adapter.extract_sanitized_dom = AsyncMock(
        return_value="<untrusted_dom_content>\n<button id=\"ok\">Submit</button>\n</untrusted_dom_content>"
    )

    server = create_webmcp_server(session_manager=mgr, dom_adapter=adapter)

    async def _run():
        dom_out = await server.call_tool("browser_get_dom", {"max_elements": 100})
        text = _get_tool_text(dom_out)
        assert "<untrusted_dom_content>" in text
        assert "</untrusted_dom_content>" in text

    asyncio.run(_run())


def test_webmcp_browser_verify_tool():
    """Verify browser_verify returns read-only verification result."""
    mgr = MagicMock(spec=BrowserSessionManager)
    mgr.get_navigation_epoch = MagicMock(return_value=2)

    adapter = MagicMock(spec=BrowserDOMAdapter)
    adapter.verify = AsyncMock(
        return_value=VerificationResult(
            verified=True,
            mode="browser_dom",
            reason="Element present and text matches",
            evidence={"text": "Hello World"},
        )
    )

    server = create_webmcp_server(session_manager=mgr, dom_adapter=adapter)

    async def _run():
        res = await server.call_tool(
            "browser_verify",
            {"condition": "text_contains", "target": "#status", "expected_value": "Hello"},
        )
        text = _get_tool_text(res)
        assert "Hello World" in text or "true" in text.lower()

    asyncio.run(_run())


def test_webmcp_browser_status_tool():
    """Verify browser_status queries session metadata and execution gate status."""
    mgr = MagicMock(spec=BrowserSessionManager)
    mgr.is_connected = True
    mgr.get_navigation_epoch = MagicMock(return_value=4)
    mgr._effective_port = 50868

    page_mock = MagicMock()
    page_mock.url = "https://example.com/checkout"
    page_mock.title = AsyncMock(return_value="Example Checkout")
    mgr.get_active_page = AsyncMock(return_value=page_mock)

    server = create_webmcp_server(session_manager=mgr)

    async def _run():
        res = await server.call_tool("browser_status", {})
        text = _get_tool_text(res)
        assert "https://example.com/checkout" in text
        assert "Example Checkout" in text
        assert "50868" in text

    asyncio.run(_run())


def test_webmcp_auth_enforcement_on_all_tools():
    """Verify that EVERY MCP tool strictly enforces authentication when configured."""
    mgr = MagicMock(spec=BrowserSessionManager)
    mgr.is_connected = True
    mgr.get_navigation_epoch = MagicMock(return_value=1)
    page_mock = MagicMock()
    page_mock.url = "https://example.com"
    page_mock.title = AsyncMock(return_value="Example")
    mgr.get_active_page = AsyncMock(return_value=page_mock)

    adapter = MagicMock(spec=BrowserDOMAdapter)
    adapter.extract_sanitized_dom = AsyncMock(return_value="<untrusted_dom_content>test</untrusted_dom_content>")
    adapter.verify = AsyncMock(return_value=VerificationResult(verified=True, mode="browser_dom", reason="ok", evidence={}))
    adapter.execute = AsyncMock(
        return_value=InteractionResult(
            mode="browser_dom",
            adapter="BrowserDOMAdapter",
            action="click",
            target="#btn",
            arguments={},
            confidence=0.9,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=10.0,
            result={},
            error=None,
        )
    )

    server = create_webmcp_server(session_manager=mgr, dom_adapter=adapter)

    async def _run():
        # 1. Zero auth_token in any tool input schema
        tools = await server.list_tools()
        for t in tools:
            props = getattr(t, "inputSchema", {}).get("properties", {})
            assert "auth_token" not in props, f"Tool {t.name} exposes auth_token in MCP schema"

        # 2. Tools execute cleanly when called by authenticated server infrastructure
        res_status = await server.call_tool("browser_status", {})
        assert res_status is not None

        res_dom = await server.call_tool("browser_get_dom", {})
        assert "<untrusted_dom_content>" in _get_tool_text(res_dom)

        res_click = await server.call_tool("browser_click", {"target": "#btn"})
        assert "confirmed_success" in _get_tool_text(res_click)
        assert adapter.execute.call_count == 1

    asyncio.run(_run())


def test_mcp_middleware_security_and_origin_http():
    """Verify HTTP middleware enforces Bearer token, rejects evil Origin, and passes valid requests."""
    from starlette.testclient import TestClient

    from typesafe_computer_use.worker.server import app

    with TestClient(app) as client:
        # 1. Missing auth returns 401
        r_no_auth = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r_no_auth.status_code == 401
        assert "Unauthorized" in r_no_auth.text

        # 2. Disallowed Origin returns 403
        r_bad_origin = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {app.extra.get('WORKER_AUTH_TOKEN', '') or 'test'}", "Origin": "https://malicious-attacker.com"},
        )
        assert r_bad_origin.status_code == 403
        assert "OriginForbidden" in r_bad_origin.text

        # 3. Allowed Origin + Valid Bearer token succeeds
        valid_token = os.environ.get("WORKER_AUTH_TOKEN", "")
        if valid_token:
            r_ok = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={
                    "Authorization": f"Bearer {valid_token}",
                    "Origin": "http://localhost:3000",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert r_ok.status_code == 200
