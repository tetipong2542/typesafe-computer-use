"""Tests for input locking and emergency stop safety in typesafe_computer_use.macos."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from typesafe_computer_use import macos


@pytest.fixture(autouse=True)
def cleanup_input_lock():
    """Ensure lock file is cleared before and after each test."""
    macos.set_input_lock(False)
    lock_file = Path("/tmp/typesafe_input_locked")
    if lock_file.exists():
        lock_file.unlink()
    yield
    macos.set_input_lock(False)
    if lock_file.exists():
        lock_file.unlink()


def test_input_lock_file_and_state():
    assert not macos.is_input_locked()

    macos.set_input_lock(True)
    assert macos.is_input_locked()
    assert Path("/tmp/typesafe_input_locked").exists()

    macos.set_input_lock(False)
    assert not macos.is_input_locked()
    assert not Path("/tmp/typesafe_input_locked").exists()


def test_cross_process_file_lock_detection():
    # Simulate an external supervisor process touching the lock file
    macos._INPUT_LOCKED = False
    Path("/tmp/typesafe_input_locked").touch()

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
