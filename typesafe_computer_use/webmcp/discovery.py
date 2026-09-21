"""WebMCP Discovery Service: In-page tool detection, declarative forms, and epoch scoping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .models import ToolAnnotation, WebMCPToolDefinition

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger("typesafe.webmcp.discovery")

# Injected JavaScript to polyfill/bridge document.modelContext and extract tools
WEBMCP_DISCOVERY_JS = """
(() => {
    // 1. Ensure document.modelContext runtime exists
    if (!document.modelContext) {
        window.__webmcp_tools = window.__webmcp_tools || new Map();
        const et = new EventTarget();
        document.modelContext = {
            registerTool(def) {
                window.__webmcp_tools.set(def.name, def);
                et.dispatchEvent(new CustomEvent('toolchange', { detail: { action: 'register', name: def.name } }));
            },
            unregisterTool(name) {
                window.__webmcp_tools.delete(name);
                et.dispatchEvent(new CustomEvent('toolchange', { detail: { action: 'unregister', name: name } }));
            },
            getTools() {
                return Array.from(window.__webmcp_tools.values());
            },
            addEventListener: et.addEventListener.bind(et),
            removeEventListener: et.removeEventListener.bind(et),
            dispatchEvent: et.dispatchEvent.bind(et)
        };
    }

    const results = [];

    // 2. Collect imperative tools from document.modelContext
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
            console.error('Error fetching imperative WebMCP tools:', e);
        }
    }

    // 3. Collect declarative tools from HTML <form> elements
    const forms = document.querySelectorAll('form[tool], form[data-model-context-tool]');
    for (const form of forms) {
        const name = form.getAttribute('tool') || form.getAttribute('data-model-context-tool') || '';
        if (!name) continue;

        const description = form.getAttribute('description') || form.getAttribute('aria-label') || '';
        const isReadOnly = (form.getAttribute('method') || 'GET').toUpperCase() === 'GET';
        const isConsequential = form.hasAttribute('data-consequential') || /checkout|pay|delete/i.test(name);

        // Derive JSON schema properties from form inputs
        const properties = {};
        const required = [];
        for (const el of form.querySelectorAll('input, select, textarea')) {
            const fieldName = el.name || el.id;
            if (!fieldName) continue;
            const fieldType = (el.type || 'text').toLowerCase();
            const jsonType = fieldType === 'number' || fieldType === 'range' ? 'number' : 'string';
            properties[fieldName] = { type: jsonType, description: el.placeholder || el.title || fieldName };
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

    return results;
})()
"""


class WebMCPDiscoveryService:
    """Service to discover, validate, and maintain in-page WebMCP tools scoped to Origin and Epoch."""

    def __init__(self) -> None:
        # Map (origin, navigation_epoch) -> { tool_name: WebMCPToolDefinition }
        self._registry: dict[tuple[str, int], dict[str, WebMCPToolDefinition]] = {}

    def _extract_origin(self, url: str) -> str:
        """Extract canonical protocol + host + port origin from URL."""
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return url
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return url

    async def discover_tools(self, page: Page, navigation_epoch: int) -> dict[str, WebMCPToolDefinition]:
        """Discover tools on active page and cache them scoped to (origin, navigation_epoch)."""
        origin = self._extract_origin(page.url)
        cache_key = (origin, navigation_epoch)

        try:
            raw_tools = await page.evaluate(WEBMCP_DISCOVERY_JS)
        except Exception as e:
            logger.warning("Failed to evaluate WebMCP discovery script on %s: %s", page.url, e)
            return {}

        tools: dict[str, WebMCPToolDefinition] = {}
        for raw in raw_tools or []:
            name = raw.get("name")
            if not name:
                continue
            annotations = ToolAnnotation(
                read_only=bool(raw.get("read_only", False)),
                consequential=bool(raw.get("consequential", False)),
            )
            tool_def = WebMCPToolDefinition(
                name=name,
                description=raw.get("description", ""),
                input_schema=raw.get("input_schema", {}),
                annotations=annotations,
                origin=origin,
                navigation_epoch=navigation_epoch,
                source=raw.get("source", "imperative"),
            )
            tools[name] = tool_def

        self._registry[cache_key] = tools
        logger.debug("Discovered %d WebMCP tool(s) for origin %s epoch %d", len(tools), origin, navigation_epoch)
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
