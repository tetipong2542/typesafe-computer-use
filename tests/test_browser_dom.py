"""Integration and Unit tests for BrowserDOMAdapter using local HTML fixtures."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.async_api import async_playwright

from typesafe_computer_use.adapters.browser_dom import BrowserDOMAdapter
from typesafe_computer_use.adapters.models import (
    InteractionMode,
    InteractionRequest,
    SideEffectState,
    VerificationExpectation,
)
from typesafe_computer_use.browser.errors import (
    AmbiguousTargetError,
    ElementNotFoundError,
)
from typesafe_computer_use.browser.session import BrowserSessionManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


async def launch_test_browser(pw):
    return await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)


@pytest.fixture(scope="module")
def fixtures_path() -> Path:
    return FIXTURES_DIR


def test_dom_adapter_semantic_locator_resolution():
    """BrowserDOMAdapter should resolve elements across the semantic ladder."""
    async def _test():
        async with async_playwright() as pw:
            browser = await launch_test_browser(pw)
            page = await browser.new_page()
            login_file = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(login_file)

            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page.return_value = page
            session_mgr.get_navigation_epoch.return_value = 1

            adapter = BrowserDOMAdapter(session_manager=session_mgr)

            # 1. Role + Name
            loc_role = await adapter.resolve_locator(page, "role=button[name=Sign In]")
            assert await loc_role.count() == 1
            assert await loc_role.inner_text() == "Sign In"

            # 2. Label
            loc_label = await adapter.resolve_locator(page, "label=Username or Email")
            assert await loc_label.count() == 1
            assert await loc_label.get_attribute("name") == "username"

            # 3. Placeholder
            loc_ph = await adapter.resolve_locator(page, "placeholder=user@example.com")
            assert await loc_ph.count() == 1
            assert await loc_ph.get_attribute("id") == "username"

            # 4. Test ID
            loc_tid = await adapter.resolve_locator(page, "data-testid=input-password")
            assert await loc_tid.count() == 1
            assert await loc_tid.get_attribute("type") == "password"

            # 5. Visible Text
            loc_txt = await adapter.resolve_locator(page, "text=Account Login")
            assert await loc_txt.count() == 1

            # 6. CSS Selector
            loc_css = await adapter.resolve_locator(page, "#submit-btn")
            assert await loc_css.count() == 1

            await browser.close()

    asyncio.run(_test())


def test_dom_adapter_element_not_found():
    """BrowserDOMAdapter should raise ElementNotFoundError when element does not exist."""
    async def _test():
        async with async_playwright() as pw:
            browser = await launch_test_browser(pw)
            page = await browser.new_page()
            login_file = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(login_file)

            adapter = BrowserDOMAdapter()
            with pytest.raises(ElementNotFoundError, match="Element not found matching target: #non-existent-btn"):
                await adapter.resolve_locator(page, "#non-existent-btn")

            await browser.close()

    asyncio.run(_test())


def test_dom_adapter_ambiguity_handling():
    """BrowserDOMAdapter should raise AmbiguousTargetError on duplicates, but allow index resolution."""
    async def _test():
        async with async_playwright() as pw:
            browser = await launch_test_browser(pw)
            page = await browser.new_page()
            ambig_file = (FIXTURES_DIR / "ambiguous.html").resolve().as_uri()
            await page.goto(ambig_file)

            adapter = BrowserDOMAdapter()

            # Ambiguous query matches multiple visible buttons
            with pytest.raises(AmbiguousTargetError, match="matched 3 elements"):
                await adapter.resolve_locator(page, ".action-btn")

            # Resolving with explicit index 0 should succeed
            loc_first = await adapter.resolve_locator(page, ".action-btn", {"index": 0})
            assert await loc_first.count() == 1  # nth(0) targets a single resolved element

            await browser.close()

    asyncio.run(_test())


def test_dom_adapter_sanitized_extraction_and_redaction():
    """BrowserDOMAdapter must redact sensitive passwords/cards and enclose in untrusted content tags."""
    async def _test():
        async with async_playwright() as pw:
            browser = await launch_test_browser(pw)
            page = await browser.new_page()
            login_file = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(login_file)

            # Fill secret values
            await page.locator("#password").fill("SuperSecretP@ssword123")
            await page.locator("#credit-card").fill("4532-9999-8888-1111")
            await page.locator("#username").fill("alice_worker")

            adapter = BrowserDOMAdapter()
            sanitized = await adapter.extract_sanitized_dom(page)

            # Verify untrusted content boundary
            assert sanitized.startswith("<untrusted_dom_content>")
            assert sanitized.strip().endswith("</untrusted_dom_content>")

            # Verify sensitive fields are REDACTED
            assert "SuperSecretP@ssword123" not in sanitized
            assert "4532-9999-8888-1111" not in sanitized
            assert '[REDACTED]' in sanitized

            # Verify non-sensitive text is preserved
            assert "alice_worker" in sanitized
            assert "Account Login" in sanitized

            await browser.close()

    asyncio.run(_test())


def test_dom_adapter_execute_click_and_verify():
    """BrowserDOMAdapter execute action and post-action verification."""
    async def _test():
        async with async_playwright() as pw:
            browser = await launch_test_browser(pw)
            page = await browser.new_page()
            login_file = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(login_file)

            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page.return_value = page
            session_mgr.get_navigation_epoch.return_value = 1

            adapter = BrowserDOMAdapter(session_manager=session_mgr)

            # Verify status is initially hidden
            v_before = await adapter.verify(VerificationExpectation(
                mode=InteractionMode.BROWSER_DOM,
                condition="hidden",
                target="#login-status",
            ))
            assert v_before.verified is True

            # Execute click on sign in button
            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#submit-btn",
            )
            res = await adapter.execute(req)
            assert res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS

            # Verify status is now visible
            v_after = await adapter.verify(VerificationExpectation(
                mode=InteractionMode.BROWSER_DOM,
                condition="visible",
                target="#login-status",
            ))
            assert v_after.verified is True

            # Verify text content
            v_text = await adapter.verify(VerificationExpectation(
                mode=InteractionMode.BROWSER_DOM,
                condition="text_contains",
                target="#login-status",
                expected_value="Login Successful",
            ))
            assert v_text.verified is True

            await browser.close()

    asyncio.run(_test())
