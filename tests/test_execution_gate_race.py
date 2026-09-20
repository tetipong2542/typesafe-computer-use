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
