"""Tests verifying completeness and precision of sensitive data redaction in DOM interactions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import async_playwright

from typesafe_computer_use.adapters.browser_dom import BrowserDOMAdapter
from typesafe_computer_use.adapters.models import InteractionMode, InteractionRequest
from typesafe_computer_use.browser.session import BrowserSessionManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_sanitization_completeness():
    """Verify all sensitive secrets (passwords, cards, cvc, tokens, session IDs) are redacted."""
    async def _run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            page = await browser.new_page()
            url = (FIXTURES_DIR / "sensitive_checkout.html").resolve().as_uri()
            await page.goto(url)

            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page.return_value = page

            adapter = BrowserDOMAdapter(session_manager=session_mgr)
            sanitized = await adapter.extract_sanitized_dom(page)

            # 1. Password must be redacted
            assert "SuperSecretP@ss!" not in sanitized

            # 2. Credit cards must be redacted (both in text and inputs and attributes)
            assert "4532 1234 5678 9010" not in sanitized
            assert "5425-2334-3211-4567" not in sanitized
            assert "4532 9999 8888 7777" not in sanitized
            assert "[REDACTED_CARD]" in sanitized

            # 3. CVC must be redacted
            assert "cvc: 789" not in sanitized
            assert "[REDACTED_CVC]" in sanitized

            # 4. API keys and tokens must be redacted
            assert "sk-live-51MzIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY" not in sanitized
            assert "ghp_abcdef1234567890abcdef1234567890abcd" not in sanitized
            assert "[REDACTED_SECRET]" in sanitized

            # 5. Session token input must be redacted
            assert "sess_prod_token_abc123" not in sanitized
            assert 'value="[REDACTED]"' in sanitized

            await browser.close()

    asyncio.run(_run())


def test_sanitization_precision():
    """Verify non-sensitive business data is preserved and NOT falsely redacted."""
    async def _run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            page = await browser.new_page()
            url = (FIXTURES_DIR / "sensitive_checkout.html").resolve().as_uri()
            await page.goto(url)

            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page.return_value = page

            adapter = BrowserDOMAdapter(session_manager=session_mgr)
            sanitized = await adapter.extract_sanitized_dom(page)

            # 1. Order ID preserved
            assert "ORD-2026-987654" in sanitized

            # 2. Product name preserved
            assert "Apple MacBook Pro 16-inch M3 Max" in sanitized

            # 3. Price preserved
            assert "$3,499.00 USD" in sanitized

            # 4. Tracking number preserved
            assert "TRACK-1Z9999999999999999" in sanitized

            # 5. Customer name & email preserved
            assert "Alice Walker" in sanitized
            assert "alice.walker@example.com" in sanitized

            # 6. Promo code in input value preserved
            assert "DISCOUNT20" in sanitized

            await browser.close()

    asyncio.run(_run())


def test_execute_fill_action_redaction():
    """Verify executing 'fill' action on sensitive target redacts returned value."""
    async def _run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            page = await browser.new_page()
            url = (FIXTURES_DIR / "sensitive_checkout.html").resolve().as_uri()
            await page.goto(url)

            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page = AsyncMock(return_value=page)
            session_mgr.get_navigation_epoch.return_value = 1

            adapter = BrowserDOMAdapter(session_manager=session_mgr)

            # 1. Fill sensitive target (card number)
            req_card = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="fill",
                target="#cc-num",
                arguments={"text": "4111 2222 3333 4444"},
            )
            res_card = await adapter.execute(req_card)
            assert res_card.result is not None
            assert res_card.result["value"] == "[REDACTED]"
            assert res_card.result["filled"] == "#cc-num"

            # 2. Fill non-sensitive target (promo code)
            req_promo = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="fill",
                target="#promo",
                arguments={"text": "SUMMER50"},
            )
            res_promo = await adapter.execute(req_promo)
            assert res_promo.result is not None
            assert res_promo.result["value"] == "SUMMER50"

            await browser.close()

    asyncio.run(_run())
