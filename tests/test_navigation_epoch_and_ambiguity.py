"""Tests verifying Navigation Epoch tracking and Ambiguous Page handling."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from typesafe_computer_use.adapters.browser_dom import BrowserDOMAdapter
from typesafe_computer_use.adapters.models import InteractionMode, InteractionRequest, SideEffectState
from typesafe_computer_use.browser.errors import AmbiguousPageError
from typesafe_computer_use.browser.session import BrowserSessionManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_navigation_epoch_lifecycle():
    """Verify navigation epoch increments on goto, pushState, replaceState, and reload."""
    async def _run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            session_mgr = BrowserSessionManager()
            session_mgr._browser = browser
            session_mgr._context = context
            session_mgr._attach_page_listeners(page)

            # 1. Initial goto
            login_url = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(login_url)
            # Give events a moment to process
            await asyncio.sleep(0.1)
            epoch_1 = session_mgr.get_navigation_epoch(page)
            assert epoch_1 >= 1

            # 2. pushState (SPA client-side navigation)
            await page.evaluate("() => history.pushState({}, '', '#step2')")
            await asyncio.sleep(0.1)
            epoch_2 = session_mgr.get_navigation_epoch(page)
            assert epoch_2 > epoch_1

            # 3. replaceState
            await page.evaluate("() => history.replaceState({}, '', '#step3')")
            await asyncio.sleep(0.1)
            epoch_3 = session_mgr.get_navigation_epoch(page)
            assert epoch_3 > epoch_2

            # 4. reload
            await page.reload()
            await asyncio.sleep(0.1)
            epoch_4 = session_mgr.get_navigation_epoch(page)
            assert epoch_4 > epoch_3

            # 5. new goto
            await page.goto((FIXTURES_DIR / "adversarial.html").resolve().as_uri())
            await asyncio.sleep(0.1)
            epoch_5 = session_mgr.get_navigation_epoch(page)
            assert epoch_5 > epoch_4

            await browser.close()

    asyncio.run(_run())


def test_epoch_mismatch_rejection():
    """Verify BrowserDOMAdapter rejects action if requested epoch does not match current epoch."""
    async def _run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            url = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(url)

            session_mgr = BrowserSessionManager()
            session_mgr._browser = browser
            session_mgr._context = context
            session_mgr._active_page = page
            session_mgr._attach_page_listeners(page)

            current_epoch = session_mgr.get_navigation_epoch(page)

            adapter = BrowserDOMAdapter(session_manager=session_mgr)

            # Request with mismatched epoch (stale epoch)
            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#login-btn",
                arguments={"navigation_epoch": current_epoch + 99},
            )

            result = await adapter.execute(req)

            assert result.error is not None
            assert "Navigation epoch mismatch" in result.error
            assert result.side_effect_state == SideEffectState.NOT_STARTED

            await browser.close()

    asyncio.run(_run())


def test_ambiguous_page_rejection_and_explicit_resolution():
    """Verify AmbiguousPageError is raised when multiple user pages exist without explicit selection."""
    async def _run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            context = await browser.new_context()

            # Open two user tabs
            page1 = await context.new_page()
            await page1.goto((FIXTURES_DIR / "login.html").resolve().as_uri())

            page2 = await context.new_page()
            await page2.goto((FIXTURES_DIR / "sensitive_checkout.html").resolve().as_uri())

            session_mgr = BrowserSessionManager()
            session_mgr._browser = browser
            session_mgr._context = context
            session_mgr._active_page = None  # Force resolution

            # 1. Calling get_active_page() without page_id must raise AmbiguousPageError
            with pytest.raises(AmbiguousPageError) as exc_info:
                await session_mgr.get_active_page()

            error_msg = str(exc_info.value)
            assert "Multiple open browser pages detected (2 pages" in error_msg
            assert "login.html" in error_msg
            assert "sensitive_checkout.html" in error_msg

            # 2. Resolving explicitly by page_id succeeds
            resolved_page = await session_mgr.get_active_page(page_id=str(id(page2)))
            assert resolved_page == page2

            # 3. select_page() sets active page deterministically
            selected_page = await session_mgr.select_page(str(id(page1)))
            assert selected_page == page1
            assert session_mgr._active_page == page1

            await browser.close()

    asyncio.run(_run())
