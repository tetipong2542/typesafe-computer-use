"""Global Execution Gate controlling all interaction adapters and in-flight cancellation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from typesafe_computer_use.macos import is_input_locked, set_input_lock
from typesafe_computer_use.worker.db import WorkerDatabase

logger = logging.getLogger("typesafe.worker.gate")


class ExecutionGateError(Exception):
    """Base exception for execution gate violations."""
    pass


class ExecutionGateLockedError(ExecutionGateError):
    """Raised when an action is blocked because input is locked or takeover is active."""
    pass


class ExecutionGatePausedError(ExecutionGateError):
    """Raised when an action is blocked because task execution is paused."""
    pass


class ExecutionGate:
    """Centralized safety gate safeguarding all adapter executions (Visual, DOM, AX, WebMCP)."""

    _instance: ExecutionGate | None = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._in_flight_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancellation_callbacks: dict[str, Callable[[], Coroutine[Any, Any, None]]] = {}

    @classmethod
    def get_instance(cls) -> ExecutionGate:
        """Singleton accessor for the execution gate."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful for test isolation)."""
        cls._instance = None

    async def check_gate_or_raise(self, task_id: str | None = None, db: WorkerDatabase | None = None) -> None:
        """Check whether execution is permitted. Raises exception if locked or paused."""
        # 1. Check physical input lock (/tmp/typesafe_input_locked and memory flag)
        if is_input_locked():
            raise ExecutionGateLockedError("Execution gate locked: System input lock is currently active.")

        # 2. Check task state in database if provided
        if task_id and db:
            task = db.get_task(task_id)
            if task:
                status = task.state.value if hasattr(task.state, "value") else str(task.state)
                if status in ("takeover", "emergency_stopped"):
                    raise ExecutionGateLockedError(
                        f"Execution gate locked: Task {task_id} is in '{status}' state."
                    )
                if status in ("paused", "pause_requested"):
                    raise ExecutionGatePausedError(
                        f"Execution gate paused: Task {task_id} is in '{status}' state."
                    )

    def register_in_flight(
        self,
        execution_id: str,
        task: asyncio.Task[Any] | None = None,
        cancel_cb: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """Register an in-flight operation so it can be aborted immediately on takeover."""
        if task:
            self._in_flight_tasks[execution_id] = task
        if cancel_cb:
            self._cancellation_callbacks[execution_id] = cancel_cb

    def unregister_in_flight(self, execution_id: str) -> None:
        """Unregister completed operation."""
        self._in_flight_tasks.pop(execution_id, None)
        self._cancellation_callbacks.pop(execution_id, None)

    async def cancel_all_in_flight(self, reason: str = "Safety gate triggered") -> int:
        """Cancel all registered in-flight operations across all adapters."""
        cancelled_count = 0

        # Execute registered custom cancellation callbacks
        callbacks = list(self._cancellation_callbacks.values())
        self._cancellation_callbacks.clear()
        for cb in callbacks:
            try:
                await cb()
                cancelled_count += 1
            except Exception as e:
                logger.warning("Error running cancellation callback: %s", e)

        # Cancel registered asyncio tasks
        tasks = list(self._in_flight_tasks.values())
        self._in_flight_tasks.clear()
        for t in tasks:
            if not t.done():
                t.cancel()
                cancelled_count += 1

        logger.info("Cancelled %d in-flight operations (reason: %s)", cancelled_count, reason)
        return cancelled_count

    async def trigger_emergency_stop(self, task_id: str | None = None, db: WorkerDatabase | None = None) -> None:
        """Enforce emergency stop: lock input immediately and cancel all active operations."""
        # 1. Lock input first
        set_input_lock(True)
        # 2. Cancel all in-flight operations
        await self.cancel_all_in_flight(reason="Emergency Stop")
        # 3. Update DB state if provided
        if task_id and db:
            from .state import TaskState
            db.update_task(task_id, state=TaskState.STOPPED, outcome="Emergency stop executed", error="Emergency stop triggered via Execution Gate")

    async def trigger_takeover(self, task_id: str | None = None, db: WorkerDatabase | None = None) -> None:
        """Enforce takeover: lock automation inputs and cancel running adapter operations."""
        set_input_lock(True)
        await self.cancel_all_in_flight(reason="Human Takeover")
        if task_id and db:
            from .state import TaskState
            db.update_task(task_id, state=TaskState.TAKEOVER)

    def rehydrate_on_boot(self, db: WorkerDatabase) -> bool:
        """Rehydrate gate and lock state from persistent database on worker startup."""
        locked = db.check_and_restore_input_lock()
        if locked:
            logger.warning("GlobalExecutionGate restored locked state from persistent DB on boot.")
        return locked
