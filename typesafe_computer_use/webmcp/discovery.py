"""WebMCP Discovery Service: In-page tool detection, declarative forms, and epoch scoping.

Implements strict standard closure separating Chrome Native document.modelContext from
legacy compatibility bridge implementations.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .models import ToolAnnotation, WebMCPToolDefinition

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger("typesafe.webmcp.discovery")

# 1. Native WebMCP Discovery (Zero polyfills, zero monkey patching)
NATIVE_WEBMCP_DISCOVERY_JS = """
async () => {
    if (typeof document.modelContext === 'undefined') {
        return {
            available: false,
            error: 'document.modelContext is undefined in this execution context',
            originAgentCluster: window.originAgentCluster === true,
            isSecureContext: window.isSecureContext === true,
            tools: []
        };
    }
    if (typeof document.modelContext.getTools !== 'function') {
        return {
            available: false,
            error: 'document.modelContext.getTools is not a function',
            originAgentCluster: window.originAgentCluster === true,
            isSecureContext: window.isSecureContext === true,
            tools: []
        };
    }

    try {
        const rawTools = await document.modelContext.getTools();
        const results = [];
        for (const t of rawTools || []) {
            let schema = t.inputSchema || t.parameters || {};
            if (typeof schema === 'string') {
                try {
                    schema = JSON.parse(schema);
                } catch (e) {
                    schema = {};
                }
            }
            const isReadOnly = Boolean(t.readOnlyHint || (t.annotations && t.annotations.read_only));
            const isConsequential = Boolean(
                t.consequentialHint ||
                (t.annotations && t.annotations.consequential) ||
                /checkout|pay|delete/i.test(t.name || '')
            );
            results.push({
                name: String(t.name || '').trim(),
                description: String(t.description || '').slice(0, 500),
                input_schema: schema,
                read_only: isReadOnly,
                consequential: isConsequential,
                source: t.window ? 'declarative_form' : 'imperative'
            });
        }
        return {
            available: true,
            error: null,
            originAgentCluster: window.originAgentCluster === true,
            isSecureContext: window.isSecureContext === true,
            tools: results
        };
    } catch (err) {
        return {
            available: true,
            error: String(err),
            originAgentCluster: window.originAgentCluster === true,
            isSecureContext: window.isSecureContext === true,
            tools: []
        };
    }
}
"""

# 2. Compatibility Bridge Discovery (Explicitly isolated fallback for non-native environments)
COMPATIBILITY_BRIDGE_DISCOVERY_JS = """
(() => {
    // Bridge polyfill for environments without native document.modelContext support
    if (!document.modelContext) {
        window.__webmcp_bridge_tools = window.__webmcp_bridge_tools || new Map();
        const et = new EventTarget();
        document.modelContext = {
            registerTool(def) {
                window.__webmcp_bridge_tools.set(def.name, def);
                et.dispatchEvent(new CustomEvent('toolchange', { detail: { action: 'register', name: def.name } }));
            },
            unregisterTool(name) {
                window.__webmcp_bridge_tools.delete(name);
                et.dispatchEvent(new CustomEvent('toolchange', { detail: { action: 'unregister', name: name } }));
            },
            getTools() {
                return Array.from(window.__webmcp_bridge_tools.values());
            },
            addEventListener: et.addEventListener.bind(et),
            removeEventListener: et.removeEventListener.bind(et),
            dispatchEvent: et.dispatchEvent.bind(et)
        };
    }

    const results = [];

    // Collect imperative tools from bridge
    if (typeof document.modelContext.getTools === 'function') {
        try {
            const rawTools = document.modelContext.getTools();
            for (const t of rawTools) {
                results.push({
                    name: String(t.name || '').trim(),
                    description: String(t.description || '').slice(0, 500),
                    input_schema: t.inputSchema || t.parameters || {},
                    read_only: !!(t.readOnlyHint || t.annotations?.read_only),
                    consequential: !!(t.consequentialHint || t.annotations?.consequential),
                    source: 'imperative'
                });
            }
        } catch (e) {
            console.error('Error in compatibility bridge imperative tools:', e);
        }
    }

    // Collect declarative tools from HTML <form> elements supporting both official and legacy attributes
    const forms = document.querySelectorAll('form[toolname], form[tool], form[data-model-context-tool]');
    for (const form of forms) {
        const name = form.getAttribute('toolname') || form.getAttribute('tool') || form.getAttribute('data-model-context-tool') || '';
        if (!name) continue;

        const description = form.getAttribute('tooldescription') || form.getAttribute('description') || form.getAttribute('aria-label') || '';
        const isReadOnly = (form.getAttribute('method') || 'GET').toUpperCase() === 'GET';
        const isConsequential = form.hasAttribute('data-consequential') || /checkout|pay|delete/i.test(name);

        const properties = {};
        const required = [];
        for (const el of form.querySelectorAll('input, select, textarea')) {
            const fieldName = el.name || el.id;
            if (!fieldName) continue;
            const fieldType = (el.type || 'text').toLowerCase();
            const jsonType = fieldType === 'number' || fieldType === 'range' ? 'number' : 'string';
            const paramDesc = el.getAttribute('toolparamdescription') || el.placeholder || el.title || fieldName;
            properties[fieldName] = { type: jsonType, description: paramDesc };
            if (el.required) {
                required.push(fieldName);
            }
        }

        results.push({
            name: name.trim(),
            description: description.slice(0, 500),
            input_schema: {
                type: 'object',
                properties: properties,
                required: required
            },
            read_only: isReadOnly,
            consequential: isConsequential,
            source: 'declarative_form'
        });
    }

    return {
        available: true,
        error: null,
        tools: results
    };
})()
"""


class WebMCPDiscoveryService:
    """Service to discover, validate, and maintain in-page WebMCP tools scoped to Origin and Epoch."""

    def __init__(self, implementation_mode: str = "native") -> None:
        # implementation_mode: 'native' (default) or 'compatibility_bridge'
        self.implementation_mode = implementation_mode
        # Map (origin, navigation_epoch) -> { tool_name: WebMCPToolDefinition }
        self._registry: dict[tuple[str, int], dict[str, WebMCPToolDefinition]] = {}
        self._last_diagnostic: dict[str, Any] = {}

    def _extract_origin(self, url: str) -> str:
        """Extract canonical protocol + host + port origin from URL."""
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return url
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return url

    @property
    def last_diagnostic(self) -> dict[str, Any]:
        """Return diagnostic data from the most recent discovery evaluation."""
        return self._last_diagnostic

    async def discover_tools(self, page: Page, navigation_epoch: int) -> dict[str, WebMCPToolDefinition]:
        """Discover tools on active page and cache them scoped to (origin, navigation_epoch).

        In 'native' mode: executes zero polyfills. If Chrome native API is unavailable,
        returns empty dict and logs BLOCKED_BY_BROWSER_SUPPORT diagnostic.
        """
        origin = self._extract_origin(page.url)
        cache_key = (origin, navigation_epoch)

        if self.implementation_mode == "native":
            script = NATIVE_WEBMCP_DISCOVERY_JS
        else:
            script = COMPATIBILITY_BRIDGE_DISCOVERY_JS

        try:
            eval_res = await page.evaluate(script)
        except Exception as e:
            logger.warning("Failed to evaluate WebMCP discovery script on %s: %s", page.url, e)
            self._last_diagnostic = {"error": str(e), "available": False, "mode": self.implementation_mode}
            return {}

        self._last_diagnostic = eval_res if isinstance(eval_res, dict) else {"available": bool(eval_res)}
        if isinstance(eval_res, list):
            raw_tools = eval_res
        elif isinstance(eval_res, dict):
            if not eval_res.get("available"):
                err = eval_res.get("error") or "WebMCP API unavailable"
                logger.info(
                    "WebMCP native API not available on %s: %s (mode=%s)",
                    page.url,
                    err,
                    self.implementation_mode,
                )
                return {}
            raw_tools = eval_res.get("tools", [])
        else:
            return {}

        tools: dict[str, WebMCPToolDefinition] = {}
        for raw in raw_tools:
            name = raw.get("name")
            if not name:
                continue
            annotations = ToolAnnotation(
                read_only=bool(raw.get("read_only", False)),
                consequential=bool(raw.get("consequential", False)),
            )
            schema = raw.get("input_schema", {})
            if isinstance(schema, str):
                try:
                    schema = json.loads(schema)
                except Exception:
                    schema = {}

            tool_def = WebMCPToolDefinition(
                name=name,
                description=raw.get("description", ""),
                input_schema=schema,
                annotations=annotations,
                origin=origin,
                navigation_epoch=navigation_epoch,
                source=raw.get("source", "imperative"),
            )
            tools[name] = tool_def

        self._registry[cache_key] = tools
        logger.debug(
            "Discovered %d WebMCP tool(s) for origin %s epoch %d (mode=%s)",
            len(tools),
            origin,
            navigation_epoch,
            self.implementation_mode,
        )
        return tools

    def get_tool(self, origin: str, navigation_epoch: int, tool_name: str) -> WebMCPToolDefinition | None:
        """Retrieve cached tool definition strictly matching origin and navigation epoch."""
        tools = self._registry.get((origin, navigation_epoch), {})
        return tools.get(tool_name)

    def invalidate_all(self) -> None:
        """Purge all discovered tools across all origins and epochs."""
        self._registry.clear()

    def invalidate_stale_epochs(self, current_epoch: int) -> None:
        """Purge registry entries belonging to older navigation epochs."""
        stale_keys = [k for k in self._registry if k[1] < current_epoch]
        for k in stale_keys:
            del self._registry[k]
