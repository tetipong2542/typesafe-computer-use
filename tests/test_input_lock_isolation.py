"""Tests verifying strict test isolation of input lock paths and safety for production locks."""

import os
import subprocess
import sys
from pathlib import Path

from typesafe_computer_use import macos
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.state import TaskState


def test_lock_paths_are_isolated_between_tests(tmp_path):
    """Test A and Test B use completely independent lock paths via isolated fixtures."""
    current_path = macos.get_input_lock_path()
    assert str(tmp_path) in str(current_path)
    assert not current_path.exists()

    macos.set_input_lock(True)
    assert current_path.exists()
    assert macos.is_input_locked()

    macos.set_input_lock(False)
    assert not current_path.exists()


def test_subprocess_inherits_injected_lock_path(tmp_path):
    """Subprocess inherits and respects TYPESAFE_INPUT_LOCK_PATH from parent environment."""
    test_lock = tmp_path / "subproc_input_lock"
    env = os.environ.copy()
    env["TYPESAFE_INPUT_LOCK_PATH"] = str(test_lock)

    # Initially unlocked
    code_check = """
import sys
from typesafe_computer_use import macos

if str(macos.get_input_lock_path()) != sys.argv[1]:
    sys.exit(2)
sys.exit(0 if not macos.is_input_locked() else 1)
"""
    res = subprocess.run(
        [sys.executable, "-c", code_check, str(test_lock)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Subprocess check failed: {res.stderr}"

    # Lock it in parent
    test_lock.touch()

    code_locked = """
import sys
from typesafe_computer_use import macos

if str(macos.get_input_lock_path()) != sys.argv[1]:
    sys.exit(2)
sys.exit(0 if macos.is_input_locked() else 1)
"""
    res = subprocess.run(
        [sys.executable, "-c", code_locked, str(test_lock)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Subprocess lock detection failed: {res.stderr}"

    test_lock.unlink(missing_ok=True)


def test_default_production_path_used_when_no_override(monkeypatch):
    """When TYPESAFE_INPUT_LOCK_PATH is not set, system resolves to production default."""
    monkeypatch.delenv("TYPESAFE_INPUT_LOCK_PATH", raising=False)
    assert macos.get_input_lock_path() == Path("/tmp/typesafe_input_locked")
    assert Path("/tmp/typesafe_input_locked") == macos.DEFAULT_INPUT_LOCK_PATH



def test_test_suite_does_not_modify_production_lock(tmp_path, monkeypatch):
    """Operations in isolated test mode do NOT touch or delete the production lock path."""
    prod_path = Path("/tmp/typesafe_input_locked")
    test_path = tmp_path / "isolated_lock"
    monkeypatch.setenv("TYPESAFE_INPUT_LOCK_PATH", str(test_path))

    prod_initially_existed = prod_path.exists()
    try:
        # If production lock exists or we simulate one, test operations must not touch it
        if not prod_initially_existed:
            prod_path.touch()

        # Run test-isolated operations
        macos.set_input_lock(True)
        assert test_path.exists()
        assert prod_path.exists()

        macos.set_input_lock(False)
        assert not test_path.exists()
        # Production lock MUST still exist untouched!
        assert prod_path.exists()
    finally:
        # Restore production path to whatever state it was before this test
        if not prod_initially_existed and prod_path.exists():
            prod_path.unlink(missing_ok=True)


def test_emergency_stop_rehydration_with_isolated_path(tmp_path):
    """Emergency-stop lock rehydration restores lock at the configured isolated path."""
    db = WorkerDatabase(tmp_path / "rehydrate_test.db")
    isolated_lock = macos.get_input_lock_path()

    macos.set_input_lock(False)
    assert not macos.is_input_locked()
    assert not isolated_lock.exists()

    # Case: Task was emergency stopped
    db.create_task("task_rehydrate_01", goal="Goal", config={})
    db.update_task("task_rehydrate_01", state=TaskState.STOPPED, outcome="Emergency stop executed (exitcode: -9)")

    # Simulate worker restart and lock rehydration
    restored = db.check_and_restore_input_lock()
    assert restored is True
    assert macos.is_input_locked()
    assert isolated_lock.exists()

    macos.set_input_lock(False)
    assert not isolated_lock.exists()
