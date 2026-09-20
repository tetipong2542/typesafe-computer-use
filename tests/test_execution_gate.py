"""Unit tests for Global Execution Gate and In-flight task cancellation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from typesafe_computer_use import macos
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.gate import (
    ExecutionGate,
    ExecutionGateLockedError,
    ExecutionGatePausedError,
)
from typesafe_computer_use.worker.state import TaskState


@pytest.fixture(autouse=True)
def clean_gate_state(tmp_path: Path):
    """Ensure clean gate singleton and unlocked input for each test."""
    ExecutionGate.reset_instance()
    macos.set_input_lock(False)
    yield
    macos.set_input_lock(False)
    ExecutionGate.reset_instance()


def test_gate_permits_execution_when_unlocked():
    """ExecutionGate should allow action when input is not locked."""
    async def _test():
        gate = ExecutionGate.get_instance()
        # Should not raise
        await gate.check_gate_or_raise()

    asyncio.run(_test())


def test_gate_blocks_execution_when_input_locked():
    """ExecutionGate should block action and raise ExecutionGateLockedError when input is locked."""
    async def _test():
        gate = ExecutionGate.get_instance()
        macos.set_input_lock(True)

        with pytest.raises(ExecutionGateLockedError, match="System input lock is currently active"):
            await gate.check_gate_or_raise()

    asyncio.run(_test())


def test_gate_blocks_execution_when_task_paused(tmp_path: Path):
    """ExecutionGate should raise ExecutionGatePausedError when task status is paused."""
    async def _test():
        db = WorkerDatabase(tmp_path / "test_gate.db")
        db.create_task(task_id="task_p1", goal="pause test", config={})
        db.update_task("task_p1", state=TaskState.PAUSED)

        gate = ExecutionGate.get_instance()
        with pytest.raises(ExecutionGatePausedError, match="is in 'paused' state"):
            await gate.check_gate_or_raise(task_id="task_p1", db=db)

    asyncio.run(_test())


def test_gate_cancels_in_flight_tasks_on_takeover():
    """ExecutionGate should cancel registered in-flight tasks and callbacks immediately."""
    async def _test():
        gate = ExecutionGate.get_instance()

        task_cancelled = False
        callback_called = False

        async def dummy_long_op():
            nonlocal task_cancelled
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                task_cancelled = True
                raise

        async def custom_cancel_cb():
            nonlocal callback_called
            callback_called = True

        long_task = asyncio.create_task(dummy_long_op())
        # Yield so dummy_long_op starts and reaches sleep
        await asyncio.sleep(0.01)

        gate.register_in_flight("op_1", task=long_task, cancel_cb=custom_cancel_cb)

        # Trigger takeover
        await gate.trigger_takeover()

        # Allow cancelled task to process
        with pytest.raises(asyncio.CancelledError):
            await long_task

        assert task_cancelled is True
        assert callback_called is True
        assert macos.is_input_locked() is True

    asyncio.run(_test())


def test_gate_rehydrates_lock_on_boot(tmp_path: Path):
    """ExecutionGate should restore input lock if persistent DB has active takeover."""
    db = WorkerDatabase(tmp_path / "test_rehydrate.db")
    db.create_task(task_id="task_t1", goal="takeover test", config={})
    db.update_task("task_t1", state=TaskState.TAKEOVER)

    # Input starts unlocked
    macos.set_input_lock(False)
    assert macos.is_input_locked() is False

    gate = ExecutionGate.get_instance()
    restored = gate.rehydrate_on_boot(db)

    assert restored is True
    assert macos.is_input_locked() is True
