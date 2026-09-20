"""Strict Read-Only Verification tests for BrowserDOMAdapter.probe()."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from playwright.async_api import async_playwright

from typesafe_computer_use.adapters.browser_dom import BrowserDOMAdapter
from typesafe_computer_use.browser.session import BrowserSessionManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_shadow_probe_is_strictly_readonly():
    """BrowserDOMAdapter.probe() must not cause any DOM mutations, focus changes, scroll, or URL shifts."""
    async def _test():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME_PATH, headless=True)
            page = await browser.new_page()
            login_file = (FIXTURES_DIR / "login.html").resolve().as_uri()
            await page.goto(login_file)

            # 1. Setup in-page observer to track mutations, focus, scroll
            await page.evaluate("""() => {
                window.__dom_mutations = 0;
                const observer = new MutationObserver((mutations) => {
                    window.__dom_mutations += mutations.length;
                });
                observer.observe(document.documentElement, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                    characterData: true
                });

                // Set baseline state
                document.getElementById('username').value = 'initial_user';
                document.getElementById('username').focus();
            }""")

            # Capture baseline state
            baseline = await page.evaluate("""() => ({
                url: window.location.href,
                historyLength: window.history.length,
                scrollX: window.scrollX,
                scrollY: window.scrollY,
                activeElementId: document.activeElement ? document.activeElement.id : null,
                usernameVal: document.getElementById('username').value,
                passwordVal: document.getElementById('password').value,
                mutations: window.__dom_mutations
            })""")

            assert baseline["activeElementId"] == "username"
            assert baseline["usernameVal"] == "initial_user"
            assert baseline["mutations"] == 0

            # 2. Execute Shadow Probe
            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page.return_value = page
            session_mgr.get_navigation_epoch.return_value = 1

            adapter = BrowserDOMAdapter(session_manager=session_mgr)
            report = await adapter.probe()

            assert report.available is True
            assert len(report.locators) >= 1

            # 3. Capture post-probe state
            post_probe = await page.evaluate("""() => ({
                url: window.location.href,
                historyLength: window.history.length,
                scrollX: window.scrollX,
                scrollY: window.scrollY,
                activeElementId: document.activeElement ? document.activeElement.id : null,
                usernameVal: document.getElementById('username').value,
                passwordVal: document.getElementById('password').value,
                mutations: window.__dom_mutations
            })""")

            # Verify complete read-only invariance
            assert post_probe["url"] == baseline["url"], "URL changed during probe!"
            assert post_probe["historyLength"] == baseline["historyLength"], "History length changed!"
            assert post_probe["scrollX"] == baseline["scrollX"], "Horizontal scroll altered!"
            assert post_probe["scrollY"] == baseline["scrollY"], "Vertical scroll altered!"
            assert post_probe["activeElementId"] == baseline["activeElementId"], "activeElement focus shifted!"
            assert post_probe["usernameVal"] == baseline["usernameVal"], "Input value mutated!"
            assert post_probe["passwordVal"] == baseline["passwordVal"], "Password value mutated!"
            assert post_probe["mutations"] == 0, f"DOM mutations detected: {post_probe['mutations']}"

            await browser.close()

    asyncio.run(_test())
