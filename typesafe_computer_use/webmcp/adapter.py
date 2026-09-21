"""Native WebMCP Adapter: In-page tool execution with schema validation and strict side-effect tracking."""

from __future__ import annotations

import logging
import time

from typesafe_computer_use.adapters.models import (
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    SideEffectState,
)
from typesafe_computer_use.browser.session import BrowserSessionManager
from typesafe_computer_use.worker.gate import ExecutionGate

from .discovery import WebMCPDiscoveryService
from .models import WebMCPToolDefinition
from .policy import is_consequential_tool, validate_tool_arguments, wrap_untrusted_output

logger = logging.getLogger("typesafe.webmcp.adapter")

IN_PAGE_INVOCATION_JS = """
async ({ toolName, args, source }) => {
    if (source === 'imperative') {
        const toolsMap = window.__webmcp_tools;
        let tool = toolsMap ? toolsMap.get(toolName) : null;
        if (!tool && document.modelContext && typeof document.modelContext.getTools === 'function') {
            const list = document.modelContext.getTools();
            tool = list.find(t => t.name === toolName);
        }
        if (!tool) {
            throw new Error(`WebMCP tool '${toolName}' not found in page registry`);
        }

        const fn = tool.execute || tool.handler;
        if (typeof fn !== 'function') {
            throw new Error(`WebMCP tool '${toolName}' does not provide an execute or handler function`);
        }

        return await fn(args);
    } else if (source === 'declarative_form') {
        const form = document.querySelector(`form[tool='${toolName}'], form[data-model-context-tool='${toolName}']`);
        if (!form) {
            throw new Error(`Declarative form for tool '${toolName}' not found`);
        }

        for (const [k, v] of Object.entries(args)) {
            if (k.startsWith('_')) continue;
            const el = form.querySelector(`[name='${k}'], [id='${k}']`);
            if (el) {
                el.value = v;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }

        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        return { submitted: true, tool: toolName, fields: args };
    } else {
        throw new Error(`Unknown tool source: ${source}`);
    }
}
"""


class NativeWebMCPAdapter:
    """Adapter executing in-page WebMCP tools declared natively by websites."""

    mode = InteractionMode.WEBMCP

    def __init__(
        self,
        session_manager: BrowserSessionManager | None = None,
        execution_gate: ExecutionGate | None = None,
        discovery_service: WebMCPDiscoveryService | None = None,
    ) -> None:
        self.session_manager = session_manager or BrowserSessionManager()
        self.execution_gate = execution_gate or ExecutionGate.get_instance()
        self.discovery = discovery_service or WebMCPDiscoveryService()

    async def probe(self, request: InteractionRequest) -> CapabilityReport:
        """Probe whether the active webpage declares a WebMCP tool matching request."""
        if not self.session_manager.is_connected:
            return CapabilityReport(
                mode=InteractionMode.WEBMCP,
                available=False,
                metadata={"reason": "Browser session not connected"},
            )

        try:
            page = await self.session_manager.get_active_page()
            epoch = self.session_manager.get_navigation_epoch()
            tools = await self.discovery.discover_tools(page, epoch)

            target_tool = request.arguments.get("tool_name") or request.action or request.target
            if target_tool in tools:
                tool_def = tools[target_tool]
                risk = RiskLevel.CRITICAL if is_consequential_tool(tool_def) else RiskLevel.NORMAL
                return CapabilityReport(
                    mode=InteractionMode.WEBMCP,
                    available=True,
                    supported_actions=list(tools.keys()),
                    metadata={
                        "tool_name": target_tool,
                        "schema_hash": tool_def.schema_hash,
                        "origin": tool_def.origin,
                        "risk": risk.value,
                    },
                )
        except Exception as e:
            logger.debug("WebMCP probe error: %s", e)

        return CapabilityReport(
            mode=InteractionMode.WEBMCP,
            available=False,
            metadata={"reason": "No matching WebMCP tool declared on active page"},
        )

    async def execute(self, request: InteractionRequest) -> InteractionResult:
        """Execute a declared WebMCP tool inside the active page with strict safety checks."""
        t0 = time.time()
        tool_name = request.arguments.get("tool_name") or request.action or request.target

        # 1. Execution Gate Check
        try:
            task_id = request.context.get("task_id") if request.context else None
            await self.execution_gate.check_gate_or_raise(task_id=task_id)
        except Exception as e:
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.NOT_STARTED,
                duration_ms=(time.time() - t0) * 1000,
                result={},
                error=f"Execution gate locked: {e}",
            )

        # 2. Connect & retrieve active page
        try:
            page = await self.session_manager.get_active_page()
            epoch = self.session_manager.get_navigation_epoch()
            origin = self.discovery._extract_origin(page.url)
        except Exception as e:
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=(time.time() - t0) * 1000,
                result={},
                error=f"Failed to access active page: {e}",
            )

        # 3. Discover tools on page
        tools = await self.discovery.discover_tools(page, epoch)
        tool_def: WebMCPToolDefinition | None = tools.get(tool_name)
        if not tool_def:
            logger.info("WebMCP tool '%s' not declared on origin %s (epoch %d)", tool_name, origin, epoch)
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=(time.time() - t0) * 1000,
                result={},
                error=f"WebMCP tool '{tool_name}' not found on origin '{origin}'",
            )

        # 4. Validate JSON Schema
        is_valid, schema_err = validate_tool_arguments(tool_def, request.arguments)
        if not is_valid:
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=(time.time() - t0) * 1000,
                result={},
                error=schema_err,
            )

        # 5. Check Consequential Policy & Approval
        is_consequential = is_consequential_tool(tool_def)
        if is_consequential and not request.arguments.get("_user_approved"):
            logger.warning("Consequential WebMCP tool '%s' halted awaiting explicit approval", tool_name)
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.95,
                risk=RiskLevel.CRITICAL,
                side_effect_state=SideEffectState.NOT_STARTED,
                duration_ms=(time.time() - t0) * 1000,
                result={"requires_approval": True, "tool": tool_name, "origin": origin},
                error=f"Consequential WebMCP tool '{tool_name}' requires explicit operator approval",
            )

        # 6. Execute in page context via CDP / Playwright evaluate
        clean_args = {k: v for k, v in request.arguments.items() if not k.startswith("_")}
        try:
            raw_res = await page.evaluate(
                IN_PAGE_INVOCATION_JS,
                {"toolName": tool_name, "args": clean_args, "source": tool_def.source},
            )
            duration_ms = (time.time() - t0) * 1000
            untrusted = wrap_untrusted_output(raw_res)

            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.95,
                risk=RiskLevel.CRITICAL if is_consequential else RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
                duration_ms=duration_ms,
                result={"data": raw_res, "untrusted_output": untrusted, "schema_hash": tool_def.schema_hash},
                error=None,
            )
        except Exception as e:
            duration_ms = (time.time() - t0) * 1000
            err_msg = str(e)

            # If the error happened after execution started (e.g. timeout or page unhandled error),
            # mark side-effect as UNKNOWN to prevent unsafe fallback or duplicate mutations!
            if "Timeout" in err_msg or "Execution context was destroyed" in err_msg:
                logger.error("WebMCP invocation outcome unknown for %s: %s", tool_name, err_msg)
                return InteractionResult(
                    mode=InteractionMode.WEBMCP.value,
                    adapter="NativeWebMCPAdapter",
                    action=tool_name,
                    target=request.target,
                    arguments=request.arguments,
                    confidence=0.0,
                    risk=RiskLevel.CRITICAL,
                    side_effect_state=SideEffectState.UNKNOWN,
                    duration_ms=duration_ms,
                    result={},
                    error=f"WebMCP execution outcome unknown: {err_msg}",
                )

            # Clean failure before mutation
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter="NativeWebMCPAdapter",
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=duration_ms,
                result={},
                error=f"WebMCP execution failed: {err_msg}",
            )
