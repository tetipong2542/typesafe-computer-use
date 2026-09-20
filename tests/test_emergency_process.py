"""Tests for subprocess execution and emergency stop lifecycle in WorkerService."""

import subprocess
import sys
from unittest.mock import MagicMock

from typesafe_computer_use import macos
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.events import EventHub
from typesafe_computer_use.worker.policy import PolicyEngine
from typesafe_computer_use.worker.service import TaskController, WorkerService
from typesafe_computer_use.worker.state import TaskState


def test_emergency_stop_subprocess_termination(tmp_path):
    """Supervisor sets input lock first, terminates subprocess, falls back to kill if needed, records exit code."""
    db = WorkerDatabase(tmp_path / "proc_test.db")
    hub = EventHub(db)
    service = WorkerService(db, hub, PolicyEngine(), base_dir=tmp_path)

    task_id = "task_proc_01"
    run_id = "run_proc_01"
    db.create_task(task_id, goal="Subprocess Test", config={}, run_id=run_id)

    controller = TaskController(task_id, run_id)
    service._controllers[task_id] = controller

    # Start a real long-running sleep subprocess
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    proc = subprocess.Popen(cmd)
    controller.process = proc

    assert proc.poll() is None  # Process is running
    assert not macos.is_input_locked()

    # Trigger emergency stop
    success = service.emergency_stop_task(task_id)
    assert success is True

    # 1. Input lock MUST be engaged
    assert macos.is_input_locked()
    assert macos.get_input_lock_path().exists()

    # 2. Process must be terminated or killed
    assert proc.poll() is not None

    # 3. Exit code must be recorded in controller and task outcome
    task = db.get_task(task_id)
    assert task is not None
    assert task.state == TaskState.STOPPED
    assert "Emergency stop executed" in (task.outcome or "")
    assert "exitcode" in (task.outcome or "")

    # Clean up lock
    macos.set_input_lock(False)


def test_emergency_stop_force_kill_on_timeout(tmp_path):
    """If process ignores SIGTERM, supervisor escalates to SIGKILL."""
    controller = TaskController("task_ignore_sig", "run_ignore_sig")

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=1.5), -9]

    controller.process = mock_proc

    exit_code = controller.request_emergency_stop()

    # Verify terminate was called first, then kill called after TimeoutExpired
    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    assert exit_code == -9
    assert macos.is_input_locked()

    macos.set_input_lock(False)
