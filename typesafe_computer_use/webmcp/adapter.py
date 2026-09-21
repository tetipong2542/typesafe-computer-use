"""Native WebMCP Adapter: In-page tool execution with schema validation and strict side-effect tracking.

Enforces:
1. Native document.modelContext execution via Blink C++ engine.
2. Server-side cryptographically hashed approval verification (zero argument-level bypass).
3. Explicit separation between Native WebMCP and Compatibility Bridge.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from typesafe_computer_use.adapters.models import (
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    SideEffectState,
)
from typesafe_computer_use.browser.session import BrowserSessionManager
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.gate import ExecutionGate

from .discovery import WebMCPDiscoveryService
from .models import WebMCPToolDefinition, compute_arguments_hash
from .policy import is_consequential_tool, validate_tool_arguments, wrap_untrusted_output

logger = logging.getLogger("typesafe.webmcp.adapter")

# 1. Native Chrome WebMCP Invocation via Blink C++ API
NATIVE_IN_PAGE_INVOCATION_JS = """
async ({ toolName, args }) => {
    if (typeof document.modelContext === 'undefined') {
        throw new Error("document.modelContext is undefined in page");
    }
    if (typeof document.modelContext.getTools !== 'function') {
        throw new Error("document.modelContext.getTools is not a function");
    }

    const tools = await document.modelContext.getTools();
    const tool = tools.find(t => t.name === toolName);
    if (!tool) {
        throw new Error(`WebMCP tool '${toolName}' not found in document.modelContext registry`);
    }

    // Call native document.modelContext.executeTool with serialized JSON arguments
    const serializedArgs = JSON.stringify(args || {});
    let rawResult;
    if (typeof document.modelContext.executeTool === 'function') {
        rawResult = await document.modelContext.executeTool(tool, serializedArgs);
    } else if (typeof tool.execute === 'function') {
        rawResult = await tool.execute(args);
    } else {
        throw new Error(`Neither document.modelContext.executeTool nor tool.execute is available for '${toolName}'`);
    }

    if (typeof rawResult === 'string') {
        try {
            return JSON.parse(rawResult);
        } catch (e) {
            return { raw: rawResult };
        }
    }
    return rawResult;
}
"""

# 2. Compatibility Bridge Invocation (Isolated fallback for non-native browsers)
BRIDGE_IN_PAGE_INVOCATION_JS = """
async ({ toolName, args, source }) => {
    if (source === 'imperative') {
        const toolsMap = window.__webmcp_bridge_tools || window.__webmcp_tools;
        let tool = toolsMap ? toolsMap.get(toolName) : null;
        if (!tool && document.modelContext && typeof document.modelContext.getTools === 'function') {
            const list = document.modelContext.getTools();
            tool = list.find(t => t.name === toolName);
        }
        if (!tool) {
            throw new Error(`WebMCP tool '${toolName}' not found in compatibility bridge`);
        }

        const fn = tool.execute || tool.handler;
        if (typeof fn !== 'function') {
            throw new Error(`WebMCP tool '${toolName}' does not provide an execute or handler function`);
        }

        return await fn(args);
    } else if (source === 'declarative_form') {
        const form = document.querySelector(`form[toolname='${toolName}'], form[tool='${toolName}'], form[data-model-context-tool='${toolName}']`);
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
    """Adapter executing in-page WebMCP tools declared natively by websites via Chrome Blink runtime."""

    mode = InteractionMode.WEBMCP
    implementation_name = "webmcp_native"

    def __init__(
        self,
        session_manager: BrowserSessionManager | None = None,
        execution_gate: ExecutionGate | None = None,
        discovery_service: WebMCPDiscoveryService | None = None,
        database: WorkerDatabase | None = None,
    ) -> None:
        self.session_manager = session_manager or BrowserSessionManager()
        self.execution_gate = execution_gate or ExecutionGate.get_instance()
        self.discovery = discovery_service or WebMCPDiscoveryService(implementation_mode="native")
        self.db = database or getattr(self.execution_gate, "_db", None) or WorkerDatabase()

    async def probe(self, request: InteractionRequest) -> CapabilityReport:
        """Probe whether the active webpage declares a WebMCP tool matching request."""
        if not self.session_manager.is_connected:
            return CapabilityReport(
                mode=InteractionMode.WEBMCP,
                available=False,
                metadata={"reason": "Browser session not connected", "implementation": self.implementation_name},
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
                        "implementation": self.implementation_name,
                        "navigation_epoch": epoch,
                    },
                )
        except Exception as e:
            logger.debug("WebMCP probe error: %s", e)

        return CapabilityReport(
            mode=InteractionMode.WEBMCP,
            available=False,
            metadata={
                "reason": "No matching WebMCP tool declared on active page",
                "implementation": self.implementation_name,
                "diagnostic": self.discovery.last_diagnostic,
            },
        )

    async def execute(self, request: InteractionRequest) -> InteractionResult:
        """Execute a declared WebMCP tool inside the active page with strict safety checks."""
        t0 = time.time()
        tool_name = request.arguments.get("tool_name") or request.action or request.target

        # 1. Execution Gate Check
        try:
            task_id = request.context.get("task_id") if request.context else None
            await self.execution_gate.check_gate_or_raise(task_id=task_id, db=self.db)
        except Exception as e:
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter=self.__class__.__name__,
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.NOT_STARTED,
                duration_ms=(time.time() - t0) * 1000,
                result={"implementation": self.implementation_name},
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
                adapter=self.__class__.__name__,
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=(time.time() - t0) * 1000,
                result={"implementation": self.implementation_name},
                error=f"Failed to access active page: {e}",
            )

        # 3. Discover tools on page
        tools = await self.discovery.discover_tools(page, epoch)
        tool_def: WebMCPToolDefinition | None = tools.get(tool_name)
        if not tool_def:
            logger.info("WebMCP tool '%s' not declared on origin %s (epoch %d)", tool_name, origin, epoch)
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter=self.__class__.__name__,
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=(time.time() - t0) * 1000,
                result={"implementation": self.implementation_name},
                error=f"WebMCP tool '{tool_name}' not found on origin '{origin}'",
            )

        # 4. Validate JSON Schema
        is_valid, schema_err = validate_tool_arguments(tool_def, request.arguments)
        if not is_valid:
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter=self.__class__.__name__,
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=(time.time() - t0) * 1000,
                result={"implementation": self.implementation_name, "schema_hash": tool_def.schema_hash},
                error=schema_err,
            )

        # 5. Check Consequential Policy & Server-Side Approval (Zero _user_approved bypass)
        is_consequential = is_consequential_tool(tool_def)
        approval_id: str | None = None
        args_hash = compute_arguments_hash(request.arguments)

        if is_consequential:
            task_id = request.context.get("task_id") if request.context else None
            active_approval = self.db.get_active_webmcp_approval(
                origin=origin,
                tool_name=tool_name,
                schema_hash=tool_def.schema_hash,
                arguments_hash=args_hash,
                navigation_epoch=epoch,
                task_id=task_id,
            )
            if not active_approval:
                logger.warning(
                    "Consequential WebMCP tool '%s' halted: awaiting active server-side approval "
                    "(origin=%s, epoch=%d, schema=%s, args_hash=%s)",
                    tool_name,
                    origin,
                    epoch,
                    tool_def.schema_hash,
                    args_hash,
                )
                return InteractionResult(
                    mode=InteractionMode.WEBMCP.value,
                    adapter=self.__class__.__name__,
                    action=tool_name,
                    target=request.target,
                    arguments=request.arguments,
                    confidence=0.95,
                    risk=RiskLevel.CRITICAL,
                    side_effect_state=SideEffectState.NOT_STARTED,
                    duration_ms=(time.time() - t0) * 1000,
                    result={
                        "requires_approval": True,
                        "tool": tool_name,
                        "origin": origin,
                        "schema_hash": tool_def.schema_hash,
                        "arguments_hash": args_hash,
                        "navigation_epoch": epoch,
                        "implementation": self.implementation_name,
                    },
                    error=f"Consequential WebMCP tool '{tool_name}' requires active server-side operator approval",
                )

            approval_id = str(active_approval["approval_id"])
            if active_approval.get("single_use", True):
                self.db.consume_webmcp_approval(approval_id)

        # 6. Execute in page context via CDP / Playwright evaluate
        clean_args = {k: v for k, v in request.arguments.items() if not k.startswith("_")}
        script = NATIVE_IN_PAGE_INVOCATION_JS if self.implementation_name == "webmcp_native" else BRIDGE_IN_PAGE_INVOCATION_JS

        try:
            raw_res = await page.evaluate(
                script,
                {"toolName": tool_name, "args": clean_args, "source": tool_def.source},
            )
            duration_ms = (time.time() - t0) * 1000
            untrusted = wrap_untrusted_output(raw_res)

            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter=self.__class__.__name__,
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.95,
                risk=RiskLevel.CRITICAL if is_consequential else RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
                duration_ms=duration_ms,
                result={
                    "data": raw_res,
                    "untrusted_output": untrusted,
                    "schema_hash": tool_def.schema_hash,
                    "arguments_hash": args_hash,
                    "origin": origin,
                    "navigation_epoch": epoch,
                    "approval_id": approval_id,
                    "implementation": self.implementation_name,
                },
                error=None,
            )
        except Exception as e:
            duration_ms = (time.time() - t0) * 1000
            err_msg = str(e)

            # In-flight failure where mutation outcome is uncertain -> UNKNOWN
            if "Timeout" in err_msg or "Execution context was destroyed" in err_msg:
                logger.error("WebMCP invocation outcome unknown for %s: %s", tool_name, err_msg)
                return InteractionResult(
                    mode=InteractionMode.WEBMCP.value,
                    adapter=self.__class__.__name__,
                    action=tool_name,
                    target=request.target,
                    arguments=request.arguments,
                    confidence=0.0,
                    risk=RiskLevel.CRITICAL,
                    side_effect_state=SideEffectState.UNKNOWN,
                    duration_ms=duration_ms,
                    result={
                        "schema_hash": tool_def.schema_hash,
                        "arguments_hash": args_hash,
                        "implementation": self.implementation_name,
                        "approval_id": approval_id,
                    },
                    error=f"WebMCP execution outcome unknown: {err_msg}",
                )

            # Clean failure before mutation
            return InteractionResult(
                mode=InteractionMode.WEBMCP.value,
                adapter=self.__class__.__name__,
                action=tool_name,
                target=request.target,
                arguments=request.arguments,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=duration_ms,
                result={
                    "schema_hash": tool_def.schema_hash,
                    "arguments_hash": args_hash,
                    "implementation": self.implementation_name,
                },
                error=f"WebMCP execution failed: {err_msg}",
            )


class WebMCPCompatibilityBridgeAdapter(NativeWebMCPAdapter):
    """Fallback compatibility bridge adapter for non-native environments."""

    implementation_name = "webmcp_compatibility_bridge"

    def __init__(
        self,
        session_manager: BrowserSessionManager | None = None,
        execution_gate: ExecutionGate | None = None,
        database: WorkerDatabase | None = None,
    ) -> None:
        discovery = WebMCPDiscoveryService(implementation_mode="compatibility_bridge")
        super().__init__(
            session_manager=session_manager,
            execution_gate=execution_gate,
            discovery_service=discovery,
            database=database,
        )
