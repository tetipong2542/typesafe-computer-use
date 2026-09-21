"""WebMCP Server implementation for TypeSafe Computer Use.

Exposes verified Model Context Protocol (MCP) tools for web automation,
strictly respecting safety invariants (Execution Gate, Takeover, CDP loopback).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from mcp.server.mcpserver import MCPServer

from typesafe_computer_use.adapters import (
    BrowserDOMAdapter,
    InteractionMode,
    InteractionRequest,
    SideEffectState,
    VerificationExpectation,
)
from typesafe_computer_use.browser.session import BrowserSessionManager
from typesafe_computer_use.mcp.tools import (
    BrowserStatusResult,
    BrowserToolResult,
)
from typesafe_computer_use.worker.gate import ExecutionGate

logger = logging.getLogger("typesafe.mcp")


def create_webmcp_server(
    session_manager: BrowserSessionManager | None = None,
    dom_adapter: BrowserDOMAdapter | None = None,
    execution_gate: ExecutionGate | None = None,
    name: str = "typesafe-webmcp",
) -> MCPServer:
    """Create and configure an MCPServer instance exposing browser automation tools."""
    gate = execution_gate or ExecutionGate.get_instance()
    mgr = session_manager or BrowserSessionManager()
    adapter = dom_adapter or BrowserDOMAdapter(session_manager=mgr, execution_gate=gate)

    server = MCPServer(
        name=name,
        version="0.1.0",
        description="TypeSafe WebMCP server for verified, safe browser interaction via Playwright CDP.",
    )

    @server.tool(name="browser_navigate", description="Navigate active browser tab to specified URL.")
    async def browser_navigate(url: str) -> BrowserToolResult:
        try:
            await gate.check_gate_or_raise()
        except Exception as e:
            return BrowserToolResult(
                success=False,
                error=f"Execution gate locked: {e}",
                side_effect_state=SideEffectState.NOT_STARTED.value,
            )

        req = InteractionRequest(
            mode=InteractionMode.BROWSER_DOM,
            action="navigate",
            target=url,
            arguments={"url": url},
            execution_id=f"mcp_nav_{int(time.time() * 1000)}",
        )
        res = await adapter.execute(req)
        epoch = mgr.get_navigation_epoch()
        return BrowserToolResult(
            success=res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS,
            data=res.result,
            error=res.error,
            side_effect_state=res.side_effect_state.value,
            navigation_epoch=epoch,
        )

    @server.tool(name="browser_click", description="Click a DOM element by selector, text, test_id, or accessible role/name.")
    async def browser_click(
        target: str,
        role: str | None = None,
        name: str | None = None,
        test_id: str | None = None,
    ) -> BrowserToolResult:
        try:
            await gate.check_gate_or_raise()
        except Exception as e:
            return BrowserToolResult(
                success=False,
                error=f"Execution gate locked: {e}",
                side_effect_state=SideEffectState.NOT_STARTED.value,
            )

        args: dict[str, Any] = {}
        if role:
            args["role"] = role
        if name:
            args["name"] = name
        if test_id:
            args["test_id"] = test_id

        req = InteractionRequest(
            mode=InteractionMode.BROWSER_DOM,
            action="click",
            target=target,
            arguments=args,
            execution_id=f"mcp_click_{int(time.time() * 1000)}",
        )
        res = await adapter.execute(req)
        epoch = mgr.get_navigation_epoch()
        return BrowserToolResult(
            success=res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS,
            data=res.result,
            error=res.error,
            side_effect_state=res.side_effect_state.value,
            navigation_epoch=epoch,
        )

    @server.tool(name="browser_fill", description="Fill text into an input or textarea element.")
    async def browser_fill(target: str, text: str) -> BrowserToolResult:
        try:
            await gate.check_gate_or_raise()
        except Exception as e:
            return BrowserToolResult(
                success=False,
                error=f"Execution gate locked: {e}",
                side_effect_state=SideEffectState.NOT_STARTED.value,
            )

        req = InteractionRequest(
            mode=InteractionMode.BROWSER_DOM,
            action="fill",
            target=target,
            arguments={"text": text},
            execution_id=f"mcp_fill_{int(time.time() * 1000)}",
        )
        res = await adapter.execute(req)
        epoch = mgr.get_navigation_epoch()
        return BrowserToolResult(
            success=res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS,
            data=res.result,
            error=res.error,
            side_effect_state=res.side_effect_state.value,
            navigation_epoch=epoch,
        )

    @server.tool(name="browser_get_dom", description="Extract sanitized, bounded DOM representation wrapped in untrusted boundary.")
    async def browser_get_dom(max_elements: int = 150, max_depth: int = 6) -> str:
        try:
            page = await mgr.get_active_page()
            return await adapter.extract_sanitized_dom(page, max_elements=max_elements, max_depth=max_depth)
        except Exception as e:
            return f"<error>{e}</error>"

    @server.tool(name="browser_verify", description="Perform read-only DOM verification without triggering mutations.")
    async def browser_verify(
        condition: str,
        target: str,
        expected_value: str | None = None,
    ) -> BrowserToolResult:
        exp = VerificationExpectation(
            condition=condition,
            target=target,
            expected_value=expected_value,
        )
        try:
            v_res = await adapter.verify(exp)
            return BrowserToolResult(
                success=v_res.verified,
                data=v_res.evidence,
                error=None if v_res.verified else v_res.reason,
                side_effect_state=SideEffectState.NOT_STARTED.value,
                navigation_epoch=mgr.get_navigation_epoch(),
            )
        except Exception as e:
            return BrowserToolResult(
                success=False,
                error=str(e),
                side_effect_state=SideEffectState.NOT_STARTED.value,
            )

    @server.tool(name="browser_status", description="Query active Chrome tab metadata, CDP connection health, and lock status.")
    async def browser_status() -> BrowserStatusResult:
        is_conn = mgr.is_connected
        epoch = mgr.get_navigation_epoch()
        url = ""
        title = ""
        cdp_port = getattr(mgr, "_effective_port", None)
        if is_conn:
            try:
                page = await mgr.get_active_page()
                url = page.url
                title = await page.title()
            except Exception:
                pass

        gate_locked = False
        try:
            await gate.check_gate_or_raise()
        except Exception:
            gate_locked = True

        return BrowserStatusResult(
            url=url,
            title=title,
            is_connected=is_conn,
            navigation_epoch=epoch,
            cdp_port=cdp_port,
            execution_gate_locked=gate_locked,
        )

    return server
