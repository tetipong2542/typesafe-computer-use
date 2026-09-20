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


def test_session_config_blocks_personal_profile_directories():
    """BrowserSessionConfig must reject personal user profile directories."""
    home = Path.home()
    personal_dirs = [
        home / "Library" / "Application Support" / "Google" / "Chrome",
        home / ".config" / "google-chrome",
        home / ".config" / "chromium",
    ]
    for p_dir in personal_dirs:
        with pytest.raises(BrowserSecurityError, match="Refusing to use personal Chrome profile directory"):
            BrowserSessionConfig(user_data_dir=p_dir)


def test_ownership_file_creation_and_removal(tmp_path: Path):
    """Recording ownership writes valid metadata matching schema and close() removes it."""
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

    # Verify all required schema fields
    assert data["worker_id"] == mgr.session_id
    assert data["pid"] == current_pid
    assert "process_start_time" in data
    assert "executable" in data
    assert data["profile_dir"] == str((tmp_path / "profile").resolve())
    assert data["cdp_host"] == "127.0.0.1"
    assert data["cdp_port"] == 9222
    assert data["browser_version"] == "Chrome/133.0.0.0"
    assert "created_at" in data

    # 2. Cleanup removes ownership
    asyncio.run(mgr.close())
    assert not owner_file.exists()


def test_stale_ownership_file(tmp_path: Path):
    """Dead PID in ownership file must be rejected as stale ownership file."""
    import json

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    owner_file.write_text(
        json.dumps({
            "worker_id": "worker_dead",
            "pid": 99999999,
            "process_start_time": "Sun Sep 20 12:00:00 2026",
            "executable": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "profile_dir": str(profile_dir.resolve()),
            "cdp_host": "127.0.0.1",
            "cdp_port": 9222,
            "browser_version": "Chrome/133",
            "created_at": 1000.0,
        }),
        encoding="utf-8",
    )

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    with pytest.raises(BrowserSecurityError, match="not alive \\(stale ownership file\\)"):
        mgr._verify_existing_ownership(9222)


def test_pid_reuse_mismatch(tmp_path: Path):
    """If PID is alive but process_start_time does not match OS, reject as PID reuse."""
    import json
    import os

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    current_pid = os.getpid()
    owner_file.write_text(
        json.dumps({
            "worker_id": "worker_1",
            "pid": current_pid,
            "process_start_time": "Jan 01 00:00:00 1970",  # mismatched start time
            "executable": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "profile_dir": str(profile_dir.resolve()),
            "cdp_host": "127.0.0.1",
            "cdp_port": 9222,
            "browser_version": "Chrome/133",
            "created_at": 1000.0,
        }),
        encoding="utf-8",
    )

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    with pytest.raises(BrowserSecurityError, match="PID reuse detected"):
        mgr._verify_existing_ownership(9222)


def test_wrong_executable(tmp_path: Path):
    """If process is running but executable is not Chrome/Chromium, reject connection."""
    import json
    import os

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    current_pid = os.getpid()
    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    # Get actual start time of current Python process so start_time check passes
    ps_info = mgr._get_process_info(current_pid)
    actual_start_time = ps_info["start_time"] if ps_info else ""

    owner_file.write_text(
        json.dumps({
            "worker_id": "worker_1",
            "pid": current_pid,
            "process_start_time": actual_start_time,
            "executable": "/usr/bin/python3",
            "profile_dir": str(profile_dir.resolve()),
            "cdp_host": "127.0.0.1",
            "cdp_port": 9222,
            "browser_version": "Python/3.13",
            "created_at": 1000.0,
        }),
        encoding="utf-8",
    )

    with pytest.raises(BrowserSecurityError, match="not a recognized Chrome browser"):
        mgr._verify_existing_ownership(9222)


def test_wrong_profile_directory(tmp_path: Path):
    """Mismatched profile directory in ownership record must raise BrowserSecurityError."""
    import json
    import os

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    current_pid = os.getpid()
    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    # Recorded profile points to another directory
    different_profile = tmp_path / "different_profile"
    owner_file.write_text(
        json.dumps({
            "worker_id": "worker_1",
            "pid": current_pid,
            "process_start_time": "Sun Sep 20 12:00:00 2026",
            "executable": "Google Chrome",
            "profile_dir": str(different_profile.resolve()),
            "cdp_host": "127.0.0.1",
            "cdp_port": 9222,
            "browser_version": "Chrome/133",
            "created_at": 1000.0,
        }),
        encoding="utf-8",
    )

    with pytest.raises(BrowserSecurityError, match="Ownership profile mismatch"):
        mgr._verify_existing_ownership(9222)


def test_port_reused_by_another_process(tmp_path: Path):
    """Connecting to an active port without browser_ownership.json must raise BrowserSecurityError."""
    cfg = BrowserSessionConfig(user_data_dir=tmp_path / "profile", cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    with pytest.raises(BrowserSecurityError, match=r"ownership file '.*' is missing"):
        mgr._verify_existing_ownership(9222)


def test_concurrent_worker_startup(tmp_path: Path):
    """Two BrowserSessionManagers targeting the same user_data_dir must raise BrowserSecurityError on the second."""
    import asyncio

    profile_dir = tmp_path / "shared_profile"
    cfg1 = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    cfg2 = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)

    mgr1 = BrowserSessionManager(cfg1)
    mgr2 = BrowserSessionManager(cfg2)

    # First manager acquires lock
    mgr1._acquire_profile_lock(non_blocking=True)

    # Second manager attempting to lock same directory must fail with BrowserSecurityError
    with pytest.raises(BrowserSecurityError, match="is locked by another running worker process"):
        mgr2._acquire_profile_lock(non_blocking=True)

    # Clean up mgr1 lock
    asyncio.run(mgr1.close())

    # Now mgr2 should be able to acquire
    mgr2._acquire_profile_lock(non_blocking=True)
    asyncio.run(mgr2.close())


def test_ownership_verification_accepts_valid_owner(tmp_path: Path, monkeypatch):
    """Valid owner file with matching alive PID, start time, port, Chrome executable and profile passes."""
    import json

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    fake_pid = 12345
    fake_start_time = "Sun Sep 20 14:00:00 2026"
    expected_profile = str(profile_dir.resolve())

    owner_file.write_text(
        json.dumps({
            "worker_id": "sess_valid",
            "pid": fake_pid,
            "process_start_time": fake_start_time,
            "executable": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "profile_dir": expected_profile,
            "cdp_host": "127.0.0.1",
            "cdp_port": 9222,
            "browser_version": "Chrome/133.0.0.0",
            "created_at": 1000.0,
        }),
        encoding="utf-8",
    )

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    # Mock PID alive and process info
    monkeypatch.setattr(mgr, "_is_pid_alive", lambda pid: pid == fake_pid)
    monkeypatch.setattr(
        mgr,
        "_get_process_info",
        lambda pid: {
            "start_time": fake_start_time,
            "executable": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "command_line": f"/Applications/Google Chrome.app --user-data-dir={expected_profile} --remote-debugging-port=9222",
        } if pid == fake_pid else None,
    )

    verified_data = mgr._verify_existing_ownership(9222)
    assert verified_data["pid"] == fake_pid
    assert verified_data["worker_id"] == "sess_valid"
    assert verified_data["cdp_port"] == 9222


def test_chrome_crash_then_new_instance(tmp_path: Path, monkeypatch):
    """When existing ownership file is stale (PID dead), stale file is cleaned up."""
    import asyncio
    import json

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    # Write stale ownership file with dead PID
    owner_file.write_text(
        json.dumps({
            "worker_id": "crashed_worker",
            "pid": 99999999,
            "cdp_port": 9222,
            "profile_dir": str(profile_dir.resolve()),
        }),
        encoding="utf-8",
    )
    assert owner_file.exists()

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=9222)
    mgr = BrowserSessionManager(cfg)

    # Mock CDP not ready (port closed after crash)
    monkeypatch.setattr(mgr, "_check_cdp_ready", lambda url: asyncio.sleep(0, result=False))

    from typesafe_computer_use.browser import BrowserNotRunningError

    # Calling connect with auto_launch=False will attempt check and detect dead state
    with pytest.raises(BrowserNotRunningError):
        asyncio.run(mgr._connect_internal(auto_launch=False))

    asyncio.run(mgr.close())


def test_worker_restart_reconnects_existing_instance(tmp_path: Path, monkeypatch):
    """When owned Chrome is running and responsive, new manager instance reconnects to it."""
    import asyncio
    import json

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    owner_file = profile_dir / "browser_ownership.json"

    fake_pid = 22222
    fake_start_time = "Sun Sep 20 15:00:00 2026"
    expected_profile = str(profile_dir.resolve())

    owner_file.write_text(
        json.dumps({
            "worker_id": "worker_prev",
            "pid": fake_pid,
            "process_start_time": fake_start_time,
            "executable": "Google Chrome",
            "profile_dir": expected_profile,
            "cdp_host": "127.0.0.1",
            "cdp_port": 9222,
            "browser_version": "Chrome/133.0",
            "created_at": 1000.0,
        }),
        encoding="utf-8",
    )

    cfg = BrowserSessionConfig(user_data_dir=profile_dir, cdp_port=0)  # even with cdp_port=0
    mgr = BrowserSessionManager(cfg)

    # Mock CDP ready on port 9222
    monkeypatch.setattr(mgr, "_check_cdp_ready", lambda url: asyncio.sleep(0, result="9222" in url))
    monkeypatch.setattr(mgr, "_is_pid_alive", lambda pid: pid == fake_pid)
    monkeypatch.setattr(
        mgr,
        "_get_process_info",
        lambda pid: {
            "start_time": fake_start_time,
            "executable": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "command_line": f"/Applications/Google Chrome.app --user-data-dir={expected_profile} --remote-debugging-port=9222",
        },
    )

    # Mock playwright connection
    mock_pw = MagicMock()
    mock_browser = MagicMock()
    mock_browser.contexts = [MagicMock()]
    mock_pw.chromium.connect_over_cdp = MagicMock(return_value=asyncio.sleep(0, result=mock_browser))

    async def fake_start():
        return mock_pw

    monkeypatch.setattr("typesafe_computer_use.browser.session.async_playwright", lambda: MagicMock(start=fake_start))

    asyncio.run(mgr._connect_internal(auto_launch=False))
    assert mgr.effective_port == 9222

    asyncio.run(mgr.close())


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
