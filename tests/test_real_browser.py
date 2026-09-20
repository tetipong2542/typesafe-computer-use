"""Integration tests for Option B: Managed External Chrome + Loopback CDP Session."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from typesafe_computer_use.adapters.browser_dom import BrowserDOMAdapter
from typesafe_computer_use.adapters.models import (
    InteractionMode,
    InteractionRequest,
    VerificationExpectation,
)
from typesafe_computer_use.browser.errors import BrowserSecurityError
from typesafe_computer_use.browser.models import BrowserSessionConfig
from typesafe_computer_use.browser.session import BrowserSessionManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_PORT = 9222


@pytest.mark.browser
def test_cdp_security_rejection_non_loopback():
    """Verify BrowserSessionConfig strictly rejects non-loopback CDP host bindings."""
    with pytest.raises(BrowserSecurityError) as exc_info:
        BrowserSessionConfig(cdp_host="0.0.0.0", cdp_port=TEST_PORT)
    assert "CDP host must bind to loopback address" in str(exc_info.value)


@pytest.mark.browser
def test_option_b_real_chrome_cdp_session():
    """Verify Option B: Managed Chrome process on loopback CDP, actions, verification, and clean close."""
    async def _run():
        config = BrowserSessionConfig(
            cdp_host="127.0.0.1",
            cdp_port=TEST_PORT,
            headless=True,
            user_data_dir=Path("/tmp/typesafe-test-chrome-profile"),
        )
        session_mgr = BrowserSessionManager(config=config)

        try:
            # 1. Start Chrome and attach via loopback CDP
            page = await session_mgr.start_or_attach(auto_launch=True)
            assert session_mgr.is_connected is True
            assert page is not None

            # 2. Verify CDP Network Isolation via lsof
            lsof_proc = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{TEST_PORT}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert lsof_proc.returncode == 0, "lsof failed to find CDP listener"
            output = lsof_proc.stdout
            assert "127.0.0.1" in output or "localhost" in output, f"Unexpected socket binding: {output}"
            assert "0.0.0.0" not in output, f"CDP port exposed to all interfaces! {output}"
            assert "*:" not in output, f"Wildcard binding detected! {output}"

            # 3. Navigate to login fixture
            adapter = BrowserDOMAdapter(session_manager=session_mgr)
            login_url = (FIXTURES_DIR / "login.html").resolve().as_uri()

            nav_req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="navigate",
                target=login_url,
                arguments={"url": login_url},
            )
            nav_res = await adapter.execute(nav_req)
            assert nav_res.result is not None
            assert nav_res.result["title"] == "TypeSafe Worker - Test Login"


            # 4. Fill username and password (verifying sensitive redaction)
            user_req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="fill",
                target="#username",
                arguments={"text": "test_agent_user"},
            )
            user_res = await adapter.execute(user_req)
            assert user_res.result["value"] == "test_agent_user"

            pass_req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="fill",
                target="#password",
                arguments={"text": "SuperSecretP@ssword123"},
            )
            pass_res = await adapter.execute(pass_req)
            assert pass_res.result["value"] == "[REDACTED]", "Password was not redacted in execute result!"

            # 5. Click Login button
            click_req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#submit-btn",
            )
            click_res = await adapter.execute(click_req)
            assert click_res.result is not None
            assert click_res.result["clicked"] == "#submit-btn"

            # 6. Extract sanitized DOM
            dom_text = await adapter.extract_sanitized_dom(page)
            assert "<untrusted_dom_content>" in dom_text
            assert "Account Login" in dom_text
            assert "SuperSecretP@ssword123" not in dom_text

            # 7. Verify post-conditions via adapter.verify()
            vis_check = await adapter.verify(
                VerificationExpectation(
                    mode=InteractionMode.BROWSER_DOM,
                    condition="visible",
                    target="#login-status",
                )
            )
            assert vis_check.verified is True

            txt_check = await adapter.verify(
                VerificationExpectation(
                    mode=InteractionMode.BROWSER_DOM,
                    condition="text_contains",
                    target="#login-status",
                    expected_value="Login Successful",
                )
            )
            assert txt_check.verified is True

            url_check = await adapter.verify(
                VerificationExpectation(
                    mode=InteractionMode.BROWSER_DOM,
                    condition="url_contains",
                    target="",
                    expected_value="login.html",
                )
            )
            assert url_check.verified is True

        finally:
            # 8. Clean teardown
            await session_mgr.close()
            assert session_mgr.is_connected is False

            # Verify socket is closed
            await asyncio.sleep(0.5)
            check_closed = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{TEST_PORT}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert check_closed.returncode != 0 or not check_closed.stdout.strip(), "CDP listener port was not closed!"

    asyncio.run(_run())
