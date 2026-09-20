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


def test_ownership_file_creation_and_removal(tmp_path: Path):
    """Recording ownership writes valid metadata and close() removes it."""
    import asyncio
    import json
    import os

    cfg = BrowserSessionConfig(user_data_dir=tmp_path / "profile")
    mgr = BrowserSessionManager(cfg)

    # 1. Record ownership
    current_pid = os.getpid()
    mgr._record_ownership(current_pid, 9222, "Chrome/133.0.0.0")

    owner_file = tmp_path / "profile" / "browser_ownership.json"
    assert owner_file.exists()

    with open(owner_file, encoding="utf-8") as f:
        data = json.load(f)

    assert data["browser_pid"] == current_pid
    assert data["cdp_port"] == 9222
    assert data["browser_version"] == "Chrome/133.0.0.0"
    assert data["worker_instance_id"] == mgr.session_id

    # 2. Cleanup removes ownership
    asyncio.run(mgr.close())
    assert not owner_file.exists()


def test_ownership_verification_blocks_unowned_port(tmp_path: Path):
    """Connecting to an active port without browser_ownership.json must raise BrowserSecurityError."""
    cfg = BrowserSessionConfig(user_data_dir=tmp_path / "profile", cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    with pytest.raises(BrowserSecurityError, match=r"ownership file '.*' is missing"):
        mgr._verify_existing_ownership(9222)


def test_ownership_verification_blocks_stale_or_mismatched_ownership(tmp_path: Path):
    """Mismatched PID, dead PID, or mismatched port must raise BrowserSecurityError."""
    import json

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    # Dead PID (e.g. 99999999)
    owner_file.write_text(
        json.dumps({
            "browser_pid": 99999999,
            "profile_path": str(profile_dir.resolve()),
            "cdp_port": 9222,
            "browser_version": "Chrome/133",
            "started_at": 1000.0,
            "worker_instance_id": "sess_dead",
        }),
        encoding="utf-8",
    )

    with pytest.raises(BrowserSecurityError, match="ownership validation failed"):
        mgr._verify_existing_ownership(9222)

    # Wrong port
    import os
    owner_file.write_text(
        json.dumps({
            "browser_pid": os.getpid(),
            "profile_path": str(profile_dir.resolve()),
            "cdp_port": 9223,
            "browser_version": "Chrome/133",
            "started_at": 1000.0,
            "worker_instance_id": "sess_wrong_port",
        }),
        encoding="utf-8",
    )

    with pytest.raises(BrowserSecurityError, match="ownership validation failed"):
        mgr._verify_existing_ownership(9222)


def test_ownership_verification_accepts_valid_owner(tmp_path: Path):
    """Valid owner file with matching alive PID, port, and profile path passes verification."""
    import json
    import os

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    current_pid = os.getpid()
    owner_file.write_text(
        json.dumps({
            "browser_pid": current_pid,
            "profile_path": str(profile_dir.resolve()),
            "cdp_port": 9222,
            "browser_version": "Chrome/133",
            "started_at": 1000.0,
            "worker_instance_id": "sess_valid",
        }),
        encoding="utf-8",
    )

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)
    assert mgr._verify_existing_ownership(9222) is True


def test_dynamic_port_resolution_from_devtools_active_port(tmp_path: Path):
    """BrowserSessionManager should resolve ephemeral port from DevToolsActivePort file."""
    import asyncio

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    active_port_file = profile_dir / "DevToolsActivePort"
    active_port_file.write_text("54321\n/devtools/browser/abc-123\n", encoding="utf-8")

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=0)
    mgr = BrowserSessionManager(cfg)

    resolved_port = asyncio.run(mgr._resolve_dynamic_port(timeout=1.0))
    assert resolved_port == 54321
