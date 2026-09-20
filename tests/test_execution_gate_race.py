"""Race-condition and safety boundary tests for Global Execution Gate."""

from __future__ import annotations

import asyncio

import pytest

from typesafe_computer_use import macos
from typesafe_computer_use.worker.gate import (
    ExecutionGate,
    ExecutionGateLockedError,
)


@pytest.fixture(autouse=True)
def clean_gate():
    ExecutionGate.reset_instance()
    macos.set_input_lock(False)
    yield
    macos.set_input_lock(False)
    ExecutionGate.reset_instance()


def test_takeover_during_in_flight_click():
    """Triggering takeover while a click is in-flight must cancel it and await completion."""
    async def _test():
        gate = ExecutionGate.get_instance()
        click_completed = False
        click_cancelled = False

        async def simulated_dom_click():
            nonlocal click_completed, click_cancelled
            try:
                # Simulate CDP round-trip delay
                await asyncio.sleep(2.0)
                click_completed = True
            except asyncio.CancelledError:
                click_cancelled = True
                raise

        click_task = asyncio.create_task(simulated_dom_click())
        await asyncio.sleep(0.01)  # allow task to start
        gate.register_in_flight("click_1", task=click_task)

        # Trigger takeover: must await cancellation
        await gate.trigger_takeover()

        assert click_task.done() is True
        assert click_cancelled is True
        assert click_completed is False
        assert macos.is_input_locked() is True

    asyncio.run(_test())


def test_emergency_stop_during_navigation():
    """Emergency stop during page.goto must cancel navigation and close gate."""
    async def _test():
        gate = ExecutionGate.get_instance()
        nav_cancelled = False

        async def simulated_navigation():
            nonlocal nav_cancelled
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                nav_cancelled = True
                raise

        nav_task = asyncio.create_task(simulated_navigation())
        await asyncio.sleep(0.01)
        gate.register_in_flight("nav_1", task=nav_task)

        await gate.trigger_emergency_stop()

        assert nav_task.done() is True
        assert nav_cancelled is True
        assert macos.is_input_locked() is True

    asyncio.run(_test())


def test_emergency_stop_during_wait_for():
    """Emergency stop during locator.wait_for must immediately abort wait."""
    async def _test():
        gate = ExecutionGate.get_instance()
        wait_cancelled = False

        async def simulated_wait_for():
            nonlocal wait_cancelled
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                wait_cancelled = True
                raise

        wait_task = asyncio.create_task(simulated_wait_for())
        await asyncio.sleep(0.01)
        gate.register_in_flight("wait_1", task=wait_task)

        await gate.trigger_emergency_stop()

        assert wait_task.done() is True
        assert wait_cancelled is True

    asyncio.run(_test())


def test_new_action_rejected_after_gate_closed():
    """Once gate is closed by takeover/stop, new actions cannot be checked or registered."""
    async def _test():
        gate = ExecutionGate.get_instance()
        await gate.trigger_takeover()

        # check_gate_or_raise must reject
        with pytest.raises(ExecutionGateLockedError, match="Execution gate locked"):
            await gate.check_gate_or_raise()

        # register_in_flight must reject
        dummy_task = asyncio.create_task(asyncio.sleep(1.0))
        with pytest.raises(ExecutionGateLockedError, match="Cannot register in-flight action: Execution gate is closed"):
            gate.register_in_flight("queued_after_lock", task=dummy_task)

        dummy_task.cancel()

    asyncio.run(_test())


def test_delayed_click_cancellation_yields_unknown_side_effect():
    """Cancelling an in-flight DOM click during CDP dispatch yields SideEffectState.UNKNOWN."""
    from unittest.mock import AsyncMock, MagicMock

    from typesafe_computer_use.adapters import BrowserDOMAdapter, InteractionMode, InteractionRequest, SideEffectState
    from typesafe_computer_use.browser import BrowserSessionManager

    async def _test():
        gate = ExecutionGate.get_instance()

        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "http://127.0.0.1:8080/button"

        mock_locator = MagicMock()
        mock_locator.count = AsyncMock(return_value=1)
        async def delayed_click(*args, **kwargs):
            # Simulates CDP click in-flight
            await asyncio.sleep(2.0)

        mock_locator.click = AsyncMock(side_effect=delayed_click)
        mock_page.locator.return_value = mock_locator

        mock_session_mgr = MagicMock(spec=BrowserSessionManager)
        mock_session_mgr.get_active_page = AsyncMock(return_value=mock_page)
        mock_session_mgr.get_navigation_epoch.return_value = 1

        adapter = BrowserDOMAdapter(session_manager=mock_session_mgr, execution_gate=gate)

        req = InteractionRequest(
            mode=InteractionMode.BROWSER_DOM,
            action="click",
            target="#delayed-btn",
            execution_id="delayed_exec_1",
            context={"navigation_epoch": 1},
        )

        exec_task = asyncio.create_task(adapter.execute(req))
        await asyncio.sleep(0.05)  # Wait for locator.click to be entered

        # Trigger takeover while click is in flight
        await gate.trigger_takeover()

        result = await exec_task
        assert result.error == "Action cancelled by Execution Gate"
        assert result.side_effect_state == SideEffectState.UNKNOWN

    asyncio.run(_test())


def test_pre_dispatch_cancellation_yields_not_started_side_effect():
    """Cancelling prior to dispatch (e.g. while resolving page) yields SideEffectState.NOT_STARTED."""
    from unittest.mock import AsyncMock, MagicMock

    from typesafe_computer_use.adapters import BrowserDOMAdapter, InteractionMode, InteractionRequest, SideEffectState
    from typesafe_computer_use.browser import BrowserSessionManager

    async def _test():
        gate = ExecutionGate.get_instance()

        mock_session_mgr = MagicMock(spec=BrowserSessionManager)
        async def delayed_get_page(*args, **kwargs):
            await asyncio.sleep(2.0)
            return MagicMock()

        mock_session_mgr.get_active_page = AsyncMock(side_effect=delayed_get_page)
        mock_session_mgr.get_navigation_epoch.return_value = 1

        adapter = BrowserDOMAdapter(session_manager=mock_session_mgr, execution_gate=gate)

        req = InteractionRequest(
            mode=InteractionMode.BROWSER_DOM,
            action="click",
            target="#delayed-btn",
            execution_id="delayed_exec_2",
            context={"navigation_epoch": 1},
        )

        exec_task = asyncio.create_task(adapter.execute(req))
        await asyncio.sleep(0.05)

        await gate.trigger_takeover()

        result = await exec_task
        assert result.error == "Action cancelled by Execution Gate"
        assert result.side_effect_state == SideEffectState.NOT_STARTED

    asyncio.run(_test())


def test_delayed_click_html_fixture_execution_gate():
    """Verify delayed click HTML fixture behavior and DOM mutation tracking."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from playwright.async_api import async_playwright

    from typesafe_computer_use.adapters import BrowserDOMAdapter, InteractionMode, InteractionRequest, SideEffectState
    from typesafe_computer_use.browser import BrowserSessionManager

    chrome_path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome_path.exists():
        pytest.skip("Chrome binary not found on macOS host")

    fixture_path = Path(__file__).parent / "fixtures" / "delayed_action.html"

    async def _test():
        gate = ExecutionGate.get_instance()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=str(chrome_path),
                headless=True,
            )
            page = await browser.new_page()
            await page.goto(fixture_path.resolve().as_uri())

            session_mgr = MagicMock(spec=BrowserSessionManager)
            session_mgr.get_active_page = AsyncMock(return_value=page)
            session_mgr.get_navigation_epoch.return_value = 1

            adapter = BrowserDOMAdapter(session_manager=session_mgr, execution_gate=gate)

            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#delayed-btn",
                execution_id="fixture_delayed_1",
                context={"navigation_epoch": 1},
            )

            res = await adapter.execute(req)
            assert res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS

            # Await delayed DOM mutation
            await asyncio.sleep(0.6)
            status_text = await page.locator("#status").inner_text()
            assert status_text == "action_mutated"

            await browser.close()

    asyncio.run(_test())


