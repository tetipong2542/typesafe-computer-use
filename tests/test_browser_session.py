"""Unit tests for BrowserSessionConfig, Security Guardrails, and Session Manager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from typesafe_computer_use.browser import (
    BrowserSecurityError,
    BrowserSessionConfig,
    BrowserSessionManager,
    get_default_profile_dir,
)


def test_session_config_allows_loopback_hosts():
    """BrowserSessionConfig must accept 127.0.0.1, localhost, and ::1."""
    cfg1 = BrowserSessionConfig(cdp_host="127.0.0.1")
    assert cfg1.cdp_host == "127.0.0.1"

    cfg2 = BrowserSessionConfig(cdp_host="localhost")
    assert cfg2.cdp_host == "localhost"

    cfg3 = BrowserSessionConfig(cdp_host="::1")
    assert cfg3.cdp_host == "::1"


def test_session_config_blocks_non_loopback_hosts():
    """BrowserSessionConfig must reject non-loopback hosts with BrowserSecurityError."""
    with pytest.raises(BrowserSecurityError, match="CDP host must bind to loopback address"):
        BrowserSessionConfig(cdp_host="0.0.0.0")

    with pytest.raises(BrowserSecurityError, match="CDP host must bind to loopback address"):
        BrowserSessionConfig(cdp_host="192.168.1.100")

    with pytest.raises(BrowserSecurityError, match="CDP host must bind to loopback address"):
        BrowserSessionConfig(cdp_host="example.com")


def test_session_config_default_profile_directory():
    """BrowserSessionConfig should default to isolated TypeSafeWorker profile directory."""
    default_dir = get_default_profile_dir()
    assert "TypeSafeWorker/ChromeProfile" in str(default_dir)
    assert default_dir == Path.home() / "Library" / "Application Support" / "TypeSafeWorker" / "ChromeProfile"

    cfg = BrowserSessionConfig()
    assert cfg.user_data_dir == default_dir


def test_navigation_epoch_tracking():
    """BrowserSessionManager should track and increment navigation epoch on main frame navigations."""
    mgr = BrowserSessionManager()

    # Mock page object
    mock_page = MagicMock()
    mock_page.main_frame = "frame_main"
    registered_callbacks = {}

    def mock_on(event_name, callback):
        registered_callbacks[event_name] = callback

    mock_page.on = mock_on
    mock_page.is_closed.return_value = False

    # Attach listeners
    mgr._attach_page_listeners(mock_page)

    # Initial epoch should be 1
    assert mgr.get_navigation_epoch(mock_page) == 1

    # Simulate sub-frame navigation (should NOT increment epoch)
    on_nav = registered_callbacks.get("framenavigated")
    assert on_nav is not None
    on_nav("frame_sub_iframe")
    assert mgr.get_navigation_epoch(mock_page) == 1

    # Simulate main frame navigation (MUST increment epoch)
    on_nav(mock_page.main_frame)
    assert mgr.get_navigation_epoch(mock_page) == 2

    on_nav(mock_page.main_frame)
    assert mgr.get_navigation_epoch(mock_page) == 3
