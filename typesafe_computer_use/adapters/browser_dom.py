"""Browser DOM Interaction Adapter using Playwright and CDP."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from playwright.async_api import Locator, Page

from typesafe_computer_use.adapters.base import InteractionAdapter
from typesafe_computer_use.adapters.models import (
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    SideEffectState,
    VerificationExpectation,
    VerificationResult,
)
from typesafe_computer_use.browser.errors import (
    AmbiguousTargetError,
    BrowserError,
    ElementNotFoundError,
)
from typesafe_computer_use.browser.session import BrowserSessionManager
from typesafe_computer_use.worker.gate import ExecutionGate

logger = logging.getLogger("typesafe.adapters.browser_dom")

# Sensitive attribute pattern for redacting passwords, tokens, credit cards, session IDs
SENSITIVE_ATTR_PATTERN = re.compile(
    r"(password|passwd|secret|token|api_?key|card|cvv|cvc|ssn|auth|bearer|session[-_]?id)",
    re.IGNORECASE,
)
CARD_PATTERN = re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b")
SECRET_PATTERN = re.compile(
    r"\b(?:sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{36}|Bearer\s+[a-zA-Z0-9_\-\.]{20,}|eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,})\b",
    re.IGNORECASE,
)



class BrowserDOMAdapter(InteractionAdapter):
    """Adapter executing interactions programmatically against live browser DOM via CDP."""

    def __init__(
        self,
        session_manager: BrowserSessionManager | None = None,
        execution_gate: ExecutionGate | None = None,
    ) -> None:
        self.session_manager = session_manager or BrowserSessionManager()
        self.execution_gate = execution_gate or ExecutionGate.get_instance()

    @property
    def mode(self) -> InteractionMode:
        return InteractionMode.BROWSER_DOM

    async def probe(self, context: dict[str, Any] | None = None) -> CapabilityReport:
        """Probe the active browser page to determine DOM accessibility and interactive controls."""
        try:
            # Check gate first
            await self.execution_gate.check_gate_or_raise()

            page = await self.session_manager.get_active_page()
            if page.is_closed():
                return CapabilityReport(
                    mode=self.mode,
                    available=False,
                    metadata={"error": "Active page is closed"},
                )

            url = page.url
            title = await page.title()
            epoch = self.session_manager.get_navigation_epoch(page)

            # Discover interactive locators (buttons, links, inputs)
            locators = await self._discover_candidate_locators(page)

            return CapabilityReport(
                mode=self.mode,
                available=True,
                supported_actions=[
                    "navigate",
                    "click",
                    "fill",
                    "select_option",
                    "press",
                    "scroll",
                    "wait_for",
                    "extract_text",
                ],
                locators=locators[:50],  # bounded list
                metadata={
                    "url": url,
                    "title": title,
                    "navigation_epoch": epoch,
                    "interactive_elements_count": len(locators),
                },
                snapshot_id=f"dom_snap_{epoch}_{int(time.time())}",
            )
        except Exception as e:
            logger.debug("BrowserDOMAdapter probe failed: %s", e)
            return CapabilityReport(
                mode=self.mode,
                available=False,
                metadata={"error": str(e)},
            )

    async def execute(self, request: InteractionRequest) -> InteractionResult:
        """Execute a typed DOM action against the target element or page."""
        start_time = time.time()
        action = request.action
        target = request.target
        args = request.arguments
        execution_id = request.execution_id or f"dom_exec_{int(time.time() * 1000)}"

        # 1. Check Global Execution Gate and register in-flight
        try:
            await self.execution_gate.check_gate_or_raise(task_id=request.context.get("task_id"))
            current_task = asyncio.current_task()
            self.execution_gate.register_in_flight(execution_id, task=current_task)
        except Exception as e:
            return InteractionResult(
                mode=self.mode,
                adapter="BrowserDOMAdapter",
                action=action,
                target=target,
                arguments=args,
                confidence=0.0,
                risk=RiskLevel.HIGH,
                side_effect_state=SideEffectState.NOT_STARTED,
                duration_ms=(time.time() - start_time) * 1000,
                result=None,
                error=f"Execution gate blocked action: {e}",
            )

        side_effect = SideEffectState.NOT_STARTED
        try:
            page = await self.session_manager.get_active_page()
            expected_epoch = args.get("navigation_epoch")
            current_epoch = self.session_manager.get_navigation_epoch(page)

            # Check navigation epoch consistency
            if expected_epoch is not None and expected_epoch != current_epoch:
                return InteractionResult(
                    mode=self.mode,
                    adapter="BrowserDOMAdapter",
                    action=action,
                    target=target,
                    arguments=args,
                    confidence=0.0,
                    risk=RiskLevel.NORMAL,
                    side_effect_state=SideEffectState.NOT_STARTED,
                    duration_ms=(time.time() - start_time) * 1000,
                    result=None,
                    error=f"Navigation epoch mismatch: expected {expected_epoch}, current is {current_epoch}",
                )

            timeout_ms = int(request.timeout_seconds * 1000)
            res_data: Any = None

            if action == "navigate":
                url = args.get("url") or target
                side_effect = SideEffectState.UNKNOWN
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                res_data = {"url": page.url, "title": await page.title()}

            elif action == "click":
                locator = await self.resolve_locator(page, target, args)
                side_effect = SideEffectState.UNKNOWN
                await locator.click(timeout=timeout_ms)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                res_data = {"clicked": target}

            elif action == "fill":
                text = args.get("text", "")
                locator = await self.resolve_locator(page, target, args)
                side_effect = SideEffectState.UNKNOWN
                await locator.fill(text, timeout=timeout_ms)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                # Redact text in returned data if sensitive target or sensitive payload
                is_sensitive = bool(
                    SENSITIVE_ATTR_PATTERN.search(target)
                    or CARD_PATTERN.search(text)
                    or SECRET_PATTERN.search(text)
                )
                res_data = {"filled": target, "value": "[REDACTED]" if is_sensitive else text}

            elif action == "select_option":
                value = args.get("value") or args.get("label") or ""
                locator = await self.resolve_locator(page, target, args)
                side_effect = SideEffectState.UNKNOWN
                await locator.select_option(value=value, timeout=timeout_ms)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                res_data = {"selected": value, "target": target}

            elif action == "press":
                key = args.get("key") or target
                side_effect = SideEffectState.UNKNOWN
                await page.keyboard.press(key)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                res_data = {"pressed": key}

            elif action == "scroll":
                delta_x = args.get("delta_x", 0)
                delta_y = args.get("delta_y", 300)
                side_effect = SideEffectState.UNKNOWN
                await page.mouse.wheel(delta_x, delta_y)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                res_data = {"scroll_x": delta_x, "scroll_y": delta_y}

            elif action == "wait_for":
                locator = await self.resolve_locator(page, target, args)
                state = args.get("state", "visible")
                await locator.wait_for(state=state, timeout=timeout_ms)
                side_effect = SideEffectState.CONFIRMED_SUCCESS
                res_data = {"wait_for": target, "state": state}

            elif action == "extract_text":
                res_data = await self.extract_sanitized_dom(page)
                side_effect = SideEffectState.CONFIRMED_SUCCESS

            else:
                raise BrowserError(f"Unsupported action: {action}")

            duration_ms = (time.time() - start_time) * 1000
            return InteractionResult(
                mode=self.mode,
                adapter="BrowserDOMAdapter",
                action=action,
                target=target,
                arguments=args,
                confidence=0.95,
                risk=RiskLevel.NORMAL,
                side_effect_state=side_effect,
                duration_ms=duration_ms,
                result=res_data,
            )

        except asyncio.CancelledError:
            logger.info("DOM action %s on %s was cancelled by Execution Gate (side_effect=%s)", action, target, side_effect.value)
            return InteractionResult(
                mode=self.mode,
                adapter="BrowserDOMAdapter",
                action=action,
                target=target,
                arguments=args,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=side_effect,
                duration_ms=(time.time() - start_time) * 1000,
                result=None,
                error="Action cancelled by Execution Gate",
            )
        except Exception as e:
            logger.warning("DOM action %s failed on %s: %s", action, target, e)
            duration_ms = (time.time() - start_time) * 1000
            return InteractionResult(
                mode=self.mode,
                adapter="BrowserDOMAdapter",
                action=action,
                target=target,
                arguments=args,
                confidence=0.0,
                risk=RiskLevel.NORMAL,
                side_effect_state=SideEffectState.CONFIRMED_FAILURE,
                duration_ms=duration_ms,
                result=None,
                error=str(e),
            )
        finally:
            self.execution_gate.unregister_in_flight(execution_id)

    async def verify(self, expectation: VerificationExpectation) -> VerificationResult:
        """Independently verify state in active DOM without modifying page state."""
        try:
            page = await self.session_manager.get_active_page()
            cond = expectation.condition
            target = expectation.target
            expected = expectation.expected_value

            if cond == "url_contains":
                is_match = expected in page.url
                return VerificationResult(
                    verified=is_match,
                    mode=self.mode,
                    reason=f"Current URL '{page.url}' {'contains' if is_match else 'does not contain'} '{expected}'",
                    evidence={"url": page.url},
                )

            elif cond == "visible":
                locator = await self.resolve_locator(page, target)
                is_vis = await locator.is_visible()
                return VerificationResult(
                    verified=is_vis,
                    mode=self.mode,
                    reason=f"Element '{target}' is {'visible' if is_vis else 'not visible'}",
                    evidence={"visible": is_vis},
                )

            elif cond == "hidden":
                locator = await self.resolve_locator(page, target)
                is_hidden = await locator.is_hidden()
                return VerificationResult(
                    verified=is_hidden,
                    mode=self.mode,
                    reason=f"Element '{target}' is {'hidden' if is_hidden else 'still visible'}",
                    evidence={"hidden": is_hidden},
                )

            elif cond == "text_contains":
                locator = await self.resolve_locator(page, target)
                text = await locator.inner_text()
                contains = expected in text
                return VerificationResult(
                    verified=contains,
                    mode=self.mode,
                    reason=f"Element text {'contains' if contains else 'does not contain'} expected value",
                    evidence={"text": text[:200]},
                )

            return VerificationResult(
                verified=False,
                mode=self.mode,
                reason=f"Unknown verification condition: {cond}",
            )
        except Exception as e:
            return VerificationResult(
                verified=False,
                mode=self.mode,
                reason=f"Verification failed with error: {e}",
            )

    async def cancel(self, execution_id: str) -> None:
        """Cancel an ongoing operation."""
        self.execution_gate.unregister_in_flight(execution_id)

    async def resolve_locator(
        self,
        page: Page,
        target: str,
        arguments: dict[str, Any] | None = None,
    ) -> Locator:
        """Resolve semantic locator hierarchy:

        1. Role + Accessible Name
        2. Accessible Label
        3. Placeholder
        4. Test ID
        5. Visible Text
        6. Stable CSS Selector
        """
        args = arguments or {}
        role = args.get("role")
        name = args.get("name")
        label = args.get("label")
        placeholder = args.get("placeholder")
        test_id = args.get("test_id")
        match_text = args.get("match_text") or args.get("element_text")
        index = args.get("index")

        locator: Locator | None = None

        # 1. Role + Name
        if role:
            locator = page.get_by_role(role, name=name)
        elif target.startswith("role="):
            match = re.match(r"^role=([a-zA-Z]+)(?:\[name=(.+)\])?$", target)
            if match:
                r_name = match.group(2)
                locator = page.get_by_role(match.group(1), name=r_name)

        # 2. Accessible Label
        if locator is None and (label or target.startswith("label=")):
            lbl_val = label or target.split("label=", 1)[1]
            locator = page.get_by_label(lbl_val)

        # 3. Placeholder
        if locator is None and (placeholder or target.startswith("placeholder=")):
            ph_val = placeholder or target.split("placeholder=", 1)[1]
            locator = page.get_by_placeholder(ph_val)

        # 4. Test ID
        if locator is None and (test_id or target.startswith("test_id=") or target.startswith("data-testid=")):
            tid_val = test_id or target.split("=", 1)[1]
            locator = page.get_by_test_id(tid_val)

        # 5. Visible Text
        if locator is None and (match_text or target.startswith("text=")):
            txt_val = match_text or target.split("text=", 1)[1]
            locator = page.get_by_text(txt_val)

        # 6. Fallback to CSS / Standard locator
        if locator is None:
            locator = page.locator(target)


        # Check match count and resolve ambiguity
        count = await locator.count()
        if count == 0:
            raise ElementNotFoundError(f"Element not found matching target: {target}")

        if count > 1:
            # If explicit index provided, select nth
            if index is not None and 0 <= index < count:
                return locator.nth(index)

            # Check if exactly one is visible
            visible_indices: list[int] = []
            for i in range(count):
                if await locator.nth(i).is_visible():
                    visible_indices.append(i)

            if len(visible_indices) == 1:
                return locator.nth(visible_indices[0])

            raise AmbiguousTargetError(
                f"Target '{target}' matched {count} elements ({len(visible_indices)} visible). "
                f"Provide more specific locator, label, role or index."
            )

        return locator

    async def extract_sanitized_dom(
        self,
        page: Page,
        max_elements: int = 150,
        max_depth: int = 6,
    ) -> str:
        """Extract a bounded, sanitized DOM representation with untrusted delimiters and redacted secrets."""
        raw_text = await page.evaluate(
            """({ maxElements, maxDepth }) => {
                const SENSITIVE_NAMES = /(password|passwd|secret|token|api_?key|auth|bearer|credit[-_]?card|card[-_]?num|cvv|cvc|ssn|session[-_]?id)/i;
                const CARD_REGEX = /\\b(?:\\d{4}[ -]?){3}\\d{1,4}\\b/g;
                const SECRET_REGEX = /\\b(?:sk-[a-zA-Z0-9_\\-]{20,}|ghp_[a-zA-Z0-9]{36}|Bearer\\s+[a-zA-Z0-9_\\-\\.]+|eyJ[a-zA-Z0-9_\\-]{10,}\\.[a-zA-Z0-9_\\-]{10,}\\.[a-zA-Z0-9_\\-]{10,})\\b/gi;
                const CVC_REGEX = /\\b(?:cvc|cvv)\\s*[:=]?\\s*\\d{3,4}\\b/gi;
                const IGNORED_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH', 'IFRAME']);

                function sanitizeText(str) {
                    if (!str || typeof str !== 'string') return str;
                    return str
                        .replace(SECRET_REGEX, '[REDACTED_SECRET]')
                        .replace(CARD_REGEX, '[REDACTED_CARD]')
                        .replace(CVC_REGEX, '[REDACTED_CVC]');
                }

                let count = 0;
                function walk(node, depth) {
                    if (!node || depth > maxDepth || count >= maxElements) return null;
                    if (node.nodeType === Node.TEXT_NODE) {
                        const txt = node.textContent.trim();
                        return txt ? sanitizeText(txt) : null;
                    }
                    if (node.nodeType !== Node.ELEMENT_NODE) return null;
                    if (IGNORED_TAGS.has(node.tagName)) return null;

                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden') return null;

                    count++;
                    const tag = node.tagName.toLowerCase();
                    const attrs = [];

                    if (node.id) attrs.push(`id="${node.id}"`);
                    if (node.getAttribute('role')) attrs.push(`role="${node.getAttribute('role')}"`);

                    const ariaLabel = node.getAttribute('aria-label');
                    if (ariaLabel) {
                        attrs.push(`aria-label="${sanitizeText(ariaLabel)}"`);
                    }

                    const placeholder = node.getAttribute('placeholder');
                    if (placeholder) {
                        if (SENSITIVE_NAMES.test(placeholder)) {
                            attrs.push('placeholder="[REDACTED]"');
                        } else {
                            attrs.push(`placeholder="${sanitizeText(placeholder)}"`);
                        }
                    }

                    if (node.getAttribute('data-testid')) attrs.push(`data-testid="${node.getAttribute('data-testid')}"`);

                    if (tag === 'input' || tag === 'textarea') {
                        const type = (node.getAttribute('type') || 'text').toLowerCase();
                        const name = node.getAttribute('name') || '';
                        attrs.push(`type="${type}"`);
                        if (name) attrs.push(`name="${name}"`);

                        const rawVal = node.value || node.getAttribute('value') || '';
                        if (type === 'password' || SENSITIVE_NAMES.test(name) || SENSITIVE_NAMES.test(node.id || '')) {
                            attrs.push('value="[REDACTED]"');
                        } else if (rawVal) {
                            const sanitizedVal = sanitizeText(rawVal);
                            attrs.push(`value="${sanitizedVal.slice(0, 50)}"`);
                        }
                    }

                    const children = [];
                    for (const child of node.childNodes) {
                        const res = walk(child, depth + 1);
                        if (res) children.push(res);
                    }

                    const attrStr = attrs.length > 0 ? ' ' + attrs.join(' ') : '';
                    if (children.length === 0) {
                        return `<${tag}${attrStr}/>`;
                    }
                    return `<${tag}${attrStr}>${children.join(' ')}</${tag}>`;
                }

                return walk(document.body, 0) || '';
            }""",
            {"maxElements": max_elements, "maxDepth": max_depth},
        )

        # Enclose in untrusted content boundary
        return f"<untrusted_dom_content>\n{raw_text}\n</untrusted_dom_content>"

    async def _discover_candidate_locators(self, page: Page) -> list[str]:
        """Discover stable semantic locators for interactive controls on current page."""
        try:
            return await page.evaluate(
                """() => {
                    const candidates = [];
                    const elements = document.querySelectorAll('button, a, input, select, textarea, [role="button"]');
                    for (const el of elements) {
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        const tid = el.getAttribute('data-testid');
                        if (tid) {
                            candidates.push(`[data-testid='${tid}']`);
                            continue;
                        }
                        const role = el.getAttribute('role') || el.tagName.toLowerCase();
                        const name = el.getAttribute('aria-label') || el.innerText?.trim() || el.getAttribute('name');
                        if (name && name.length < 40) {
                            candidates.push(`role=${role}[name=${name.replace(/[\\n\\r]+/g, ' ')}]`);
                            continue;
                        }
                        if (el.id) {
                            candidates.push(`#${el.id}`);
                        }
                    }
                    return candidates;
                }"""
            )
        except Exception:
            return []
