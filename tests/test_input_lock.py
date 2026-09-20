"""Tests for input locking and emergency stop safety in typesafe_computer_use.macos."""

from unittest.mock import MagicMock, patch

from typesafe_computer_use import macos


def test_input_lock_file_and_state():
    assert not macos.is_input_locked()

    macos.set_input_lock(True)
    assert macos.is_input_locked()
    assert macos.get_input_lock_path().exists()

    macos.set_input_lock(False)
    assert not macos.is_input_locked()
    assert not macos.get_input_lock_path().exists()


def test_cross_process_file_lock_detection():
    # Simulate an external supervisor process touching the lock file
    macos._INPUT_LOCKED = False
    macos.get_input_lock_path().touch()

    assert macos.is_input_locked()



def test_quartz_calls_blocked_when_locked():
    macos.set_input_lock(True)

    with patch("typesafe_computer_use.macos.Quartz.CGEventPost") as mock_post:
        macos.click_at((100, 200))
        macos.press("return")
        macos.type_text("hello")
        macos.clear_field()
        macos.scroll(-5)

        # No CGEventPost calls should have been made
        mock_post.assert_not_called()


def test_ax_calls_blocked_when_locked():
    macos.set_input_lock(True)

    dummy_element = MagicMock()
    with patch("typesafe_computer_use.macos.AS.AXUIElementPerformAction") as mock_action, \
         patch("typesafe_computer_use.macos.AS.AXUIElementSetAttributeValue") as mock_attr:

        assert macos.ax_press(dummy_element) is False
        assert macos.ax_focus(dummy_element) is False
        assert macos.ax_set_value(dummy_element, "test") is False

        mock_action.assert_not_called()
        mock_attr.assert_not_called()


def test_boot_input_lock_recovery(tmp_path):
    """If Guest reboots after an emergency stop or during takeover, lock must be restored on boot."""
    from typesafe_computer_use.worker.db import WorkerDatabase
    from typesafe_computer_use.worker.state import TaskState

    db = WorkerDatabase(tmp_path / "boot_recovery.db")

    # Ensure clean slate
    macos.set_input_lock(False)
    assert not macos.is_input_locked()

    # Case 1: No previous emergency stop -> no lock restored
    assert db.check_and_restore_input_lock() is False
    assert not macos.is_input_locked()

    # Case 2: An emergency stopped task existed before reboot
    db.create_task("task_em_01", goal="Goal", config={})
    db.update_task("task_em_01", state=TaskState.STOPPED, outcome="Emergency stop executed (exitcode: -9)")

    # Simulate reboot: input lock file was wiped by OS
    macos.set_input_lock(False)
    assert not macos.is_input_locked()

    # Worker boots up:
    restored = db.check_and_restore_input_lock()
    assert restored is True
    assert macos.is_input_locked()
    assert macos.get_input_lock_path().exists()

