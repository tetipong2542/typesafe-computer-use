"""Shared Chrome/CDP Session Manager using Playwright for Universal Computer Worker."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .errors import (
    AmbiguousPageError,
    BrowserCrashError,
    BrowserNotRunningError,
    BrowserSecurityError,
)
from .models import BrowserSessionConfig, PageSessionInfo

logger = logging.getLogger("typesafe.browser.session")

DEFAULT_MAC_CHROME_PATHS = [
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


class BrowserSessionManager:
    """Manages a persistent, shared Chrome CDP session with deterministic active page tracking."""

    def __init__(self, config: BrowserSessionConfig | None = None) -> None:
        self.config = config or BrowserSessionConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._active_page: Page | None = None
        self._process: subprocess.Popen | None = None
        self._navigation_epochs: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._session_id = f"session_{int(time.time())}"
        self._effective_port: int | None = None
        self._ownership_file: Path = self.config.user_data_dir / "browser_ownership.json"
        self._lock_file: Path = self.config.user_data_dir / ".worker_profile.lock"
        self._lock_fd: int | None = None
        self._owns_process: bool = False
        self._attached_pid: int | None = None
        self._bound_loop: asyncio.AbstractEventLoop | None = None

    def _ensure_loop_resources(self) -> None:
        """Ensure asyncio primitives and Playwright handles match the currently running event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._bound_loop is None:
            self._bound_loop = current_loop
            self._lock = asyncio.Lock()
        elif self._bound_loop is not current_loop:
            self._bound_loop = current_loop
            self._lock = asyncio.Lock()
            # Invalidate Playwright handles bound to a prior event loop
            self._playwright = None
            self._browser = None
            self._context = None
            self._active_page = None

    @property
    def is_connected(self) -> bool:
        """Check if CDP connection is active and responsive."""
        try:
            current_loop = asyncio.get_running_loop()
            if self._bound_loop is not None and self._bound_loop is not current_loop:
                return False
        except RuntimeError:
            pass
        return self._browser is not None and self._browser.is_connected()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def effective_port(self) -> int:
        """Return effective CDP port (configured or dynamically allocated)."""
        return self._effective_port if self._effective_port is not None else self.config.cdp_port

    def get_navigation_epoch(self, page: Page | None = None) -> int:
        """Get current navigation epoch for a given page or the active page."""
        target_page = page or self._active_page
        if not target_page:
            return 0
        page_key = str(id(target_page))
        return self._navigation_epochs.get(page_key, 0)

    async def get_active_page(self, page_id: str | None = None) -> Page:
        """Get or resolve active page deterministically. Raises AmbiguousPageError if ambiguous."""
        self._ensure_loop_resources()
        async with self._lock:
            if not self.is_connected:
                await self._connect_internal()

            if page_id is not None:
                if not self._context:
                    raise BrowserNotRunningError("No browser context available")
                for p in self._context.pages:
                    if str(id(p)) == page_id and not p.is_closed():
                        self._active_page = p
                        self._attach_page_listeners(p)
                        return p
                raise AmbiguousPageError(f"Target page with id {page_id} not found among active pages")

            if self._active_page is not None and not self._active_page.is_closed():
                return self._active_page

            # Resolve page deterministically
            self._active_page = await self._resolve_active_page()
            return self._active_page

    async def select_page(self, page_id: str) -> Page:
        """Explicitly select a specific page as active by its ID."""
        return await self.get_active_page(page_id=page_id)

    async def _resolve_active_page(self) -> Page:
        """Deterministic selection of the primary user-facing tab.

        Raises AmbiguousPageError if multiple open user-facing tabs exist without explicit selection.
        """
        if not self._context:
            raise BrowserNotRunningError("No active browser context available")

        pages = self._context.pages
        # Filter out internal/devtools/extension pages
        user_pages = [
            p for p in pages
            if not p.is_closed()
            and not p.url.startswith(("chrome://", "devtools://", "chrome-extension://"))
        ]

        if len(user_pages) > 1:
            urls = [p.url for p in user_pages]
            raise AmbiguousPageError(
                f"Multiple open browser pages detected ({len(user_pages)} pages: {urls}). "
                "Explicit page_id or target URL required to avoid ambiguous execution."
            )
        elif len(user_pages) == 1:
            selected = user_pages[0]
        elif pages and not pages[0].is_closed():
            selected = pages[0]
        else:
            selected = await self._context.new_page()

        self._attach_page_listeners(selected)
        return selected

    def _attach_page_listeners(self, page: Page) -> None:
        """Attach listeners to track navigation epoch and lifecycle."""
        page_key = str(id(page))
        if page_key not in self._navigation_epochs:
            self._navigation_epochs[page_key] = 1

        def on_framenavigated(frame: Any) -> None:
            # Only track main frame navigations for the epoch counter
            if frame == page.main_frame:
                self._navigation_epochs[page_key] = self._navigation_epochs.get(page_key, 1) + 1
                logger.debug(
                    "Page %s navigated to %s (epoch %d)",
                    page_key,
                    page.url,
                    self._navigation_epochs[page_key],
                )

        def on_close(_: Any) -> None:
            logger.debug("Page %s closed", page_key)
            if self._active_page == page:
                self._active_page = None

        page.on("framenavigated", on_framenavigated)
        page.on("close", on_close)

    async def start_or_attach(self, auto_launch: bool = True) -> Page:
        """Ensure Chrome CDP connection is established and return active page."""
        async with self._lock:
            if self.is_connected and self._active_page and not self._active_page.is_closed():
                return self._active_page
            await self._connect_internal(auto_launch=auto_launch)
            self._active_page = await self._resolve_active_page()
            return self._active_page

    def _acquire_profile_lock(self, non_blocking: bool = True) -> None:
        """Acquire filesystem lock on worker profile directory to prevent concurrent worker races."""
        if self._lock_fd is not None:
            return
        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(str(self._lock_file), os.O_CREAT | os.O_RDWR, 0o600)
            flags = fcntl.LOCK_EX
            if non_blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._lock_fd, flags)
        except (BlockingIOError, OSError) as e:
            if self._lock_fd is not None:
                with contextlib.suppress(Exception):
                    os.close(self._lock_fd)
                self._lock_fd = None
            raise BrowserSecurityError(
                f"Profile directory '{self.config.user_data_dir}' is locked by another running worker process."
            ) from e

    def _release_profile_lock(self) -> None:
        """Release filesystem lock on worker profile directory."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except Exception:
                pass
            self._lock_fd = None

    def _is_pid_alive(self, pid: int) -> bool:
        """Check whether a given PID corresponds to a running process."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _get_process_info(self, pid: int) -> dict[str, str] | None:
        """Query process start time, executable, and command line from OS."""
        try:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart=,command="],
                capture_output=True,
                text=True,
                check=True,
                timeout=2.0,
            )
            out = res.stdout.strip()
            if len(out) < 24:
                return None
            lstart = out[:24].strip()
            cmd = out[24:].strip()
            # Resolve executable path (which on macOS often contains spaces, e.g. Google Chrome.app)
            detected = self.config.chrome_binary_path or self._detect_chrome_binary()
            if detected and cmd.startswith(str(detected)):
                executable = str(detected)
            elif " --" in cmd and Path(cmd.split(" --", 1)[0].strip()).exists():
                executable = cmd.split(" --", 1)[0].strip()
            else:
                parts = cmd.split(None, 1)
                executable = parts[0] if parts else ""
            return {
                "start_time": lstart,
                "executable": executable,
                "command_line": cmd,
            }
        except Exception:
            return None

    def _verify_existing_ownership(self, target_port: int) -> dict[str, Any]:
        """Verify that an already listening CDP port belongs to our managed profile and process.

        Strict validation checks:
        1. Ownership file exists and is valid JSON
        2. PID is alive
        3. process_start_time matches to prevent PID reuse
        4. executable is an expected Chrome/Chromium binary
        5. command_line contains --user-data-dir pointing to worker's profile
        6. CDP host is loopback only
        7. cdp_port matches target_port
        """
        if not self._ownership_file.exists():
            raise BrowserSecurityError(
                f"CDP port {target_port} is already listening on loopback, but ownership file "
                f"'{self._ownership_file}' is missing. Refusing to attach to untracked browser."
            )
        try:
            with open(self._ownership_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise BrowserSecurityError(
                f"Failed to read ownership file '{self._ownership_file}': {e}. Refusing to attach."
            ) from e

        stored_pid = data.get("pid") or data.get("browser_pid")
        stored_port = data.get("cdp_port")
        stored_profile = data.get("profile_dir") or data.get("profile_path")
        stored_start_time = data.get("process_start_time")
        stored_host = data.get("cdp_host", "127.0.0.1")

        # 1. Loopback validation
        if stored_host not in ("127.0.0.1", "localhost", "::1"):
            raise BrowserSecurityError(f"Ownership record cdp_host '{stored_host}' is not loopback.")

        # 2. Port and profile directory validation
        expected_profile = str(self.config.user_data_dir.resolve())
        if stored_port != target_port:
            raise BrowserSecurityError(
                f"Ownership port mismatch: recorded {stored_port}, target {target_port}"
            )
        if stored_profile != expected_profile:
            raise BrowserSecurityError(
                f"Ownership profile mismatch: recorded {stored_profile}, expected {expected_profile}"
            )

        # 3. PID alive validation
        if not isinstance(stored_pid, int) or not self._is_pid_alive(stored_pid):
            raise BrowserSecurityError(
                f"Ownership process {stored_pid} is not alive (stale ownership file)."
            )

        # 4. Process identity (PID reuse check via start time, executable, command line)
        ps_info = self._get_process_info(stored_pid)
        if not ps_info:
            raise BrowserSecurityError(
                f"Failed to inspect process info for PID {stored_pid}. Process may have terminated."
            )

        # PID reuse check
        if stored_start_time and ps_info["start_time"] != stored_start_time:
            raise BrowserSecurityError(
                f"PID reuse detected! Process {stored_pid} start time '{ps_info['start_time']}' "
                f"does not match recorded start time '{stored_start_time}'."
            )

        # Executable check
        cmd_line = ps_info.get("command_line", "")
        exec_path = ps_info.get("executable", "")
        is_chrome = any(
            name in exec_path.lower() or name in cmd_line.lower()
            for name in ("google chrome", "chromium", "google-chrome")
        )
        if not is_chrome:
            raise BrowserSecurityError(
                f"Process {stored_pid} executable '{exec_path}' is not a recognized Chrome browser."
            )

        # Profile directory argument check
        if expected_profile not in cmd_line:
            raise BrowserSecurityError(
                f"Process {stored_pid} command line does not use worker profile directory '{expected_profile}'."
            )

        return data

    def _record_ownership(self, pid: int, port: int, browser_version: str = "unknown") -> None:
        """Record ownership metadata atomically to browser_ownership.json."""
        ps_info = self._get_process_info(pid)
        data = {
            "worker_id": self._session_id,
            "pid": pid,
            "process_start_time": ps_info.get("start_time", "") if ps_info else "",
            "executable": ps_info.get("executable", str(self.config.chrome_binary_path or "")) if ps_info else str(self.config.chrome_binary_path or ""),
            "profile_dir": str(self.config.user_data_dir.resolve()),
            "cdp_host": self.config.cdp_host,
            "cdp_port": port,
            "browser_version": browser_version,
            "created_at": time.time(),
        }
        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = self._ownership_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, self._ownership_file)
        self._owns_process = True

    def _remove_ownership(self) -> None:
        """Clean up ownership metadata file."""
        if self._ownership_file.exists():
            with contextlib.suppress(Exception):
                self._ownership_file.unlink()
        self._owns_process = False

    async def _resolve_dynamic_port(self, timeout: float) -> int:
        """Wait for Chrome to write DevToolsActivePort and parse ephemeral port."""
        active_port_file = self.config.user_data_dir / "DevToolsActivePort"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if active_port_file.exists():
                try:
                    content = active_port_file.read_text(encoding="utf-8").splitlines()
                    if content and content[0].strip().isdigit():
                        return int(content[0].strip())
                except Exception:
                    pass
            await asyncio.sleep(0.2)
        raise BrowserCrashError(f"Chrome did not write DevToolsActivePort within {timeout}s")

    async def _fetch_version_info(self, cdp_url: str) -> dict[str, Any]:
        """Fetch version information from Chrome CDP JSON endpoint."""
        loop = asyncio.get_running_loop()
        def _fetch() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                return {}
        return await loop.run_in_executor(None, _fetch)

    async def _connect_internal(self, auto_launch: bool = True) -> None:
        """Connect to Chrome via CDP over loopback, optionally launching it if missing."""
        self._acquire_profile_lock(non_blocking=True)
        try:
            reconnect_port: int | None = None
            # Check if an existing owned Chrome instance is already running for this profile
            if self._ownership_file.exists():
                try:
                    with open(self._ownership_file, encoding="utf-8") as f:
                        cached = json.load(f)
                    cached_port = cached.get("cdp_port")
                    if isinstance(cached_port, int) and cached_port > 0:
                        check_url = f"http://{self.config.cdp_host}:{cached_port}"
                        if await self._check_cdp_ready(check_url):
                            self._verify_existing_ownership(cached_port)
                            reconnect_port = cached_port
                            logger.info("Found active owned Chrome on port %d; reconnecting...", cached_port)
                except Exception as e:
                    logger.debug("Existing Chrome ownership check failed or not running: %s", e)
                    # Stale ownership file cleanup
                    self._remove_ownership()

            if reconnect_port is not None:
                self._effective_port = reconnect_port
                cdp_url = f"http://{self.config.cdp_host}:{reconnect_port}"
                self._owns_process = True
                stored_pid = cached.get("pid")
                if isinstance(stored_pid, int):
                    self._attached_pid = stored_pid
            elif self.config.cdp_port > 0:
                target_port = self.config.cdp_port
                cdp_url = f"http://{self.config.cdp_host}:{target_port}"
                is_running = await self._check_cdp_ready(cdp_url)

                if is_running:
                    # Existing listener found: enforce ownership validation before connecting
                    owner_record = self._verify_existing_ownership(target_port)
                    self._effective_port = target_port
                    self._owns_process = True
                    self._attached_pid = owner_record.get("pid")
                    logger.info("Reusing verified existing Chrome session on port %d", target_port)
                else:
                    if not auto_launch:
                        raise BrowserNotRunningError(f"CDP endpoint at {cdp_url} is not responding")
                    logger.info("Launching Chrome process on loopback CDP port %d", target_port)
                    await self._launch_chrome_process(port=target_port)
                    self._effective_port = target_port
                    is_ready = await self._wait_for_cdp(cdp_url, timeout=self.config.connect_timeout_seconds)
                    if not is_ready:
                        raise BrowserCrashError(f"Chrome failed to start CDP listener on {cdp_url}")
                    version_info = await self._fetch_version_info(cdp_url)
                    if self._process:
                        self._record_ownership(self._process.pid, target_port, version_info.get("Browser", "unknown"))
            else:
                # cdp_port == 0: Ephemeral dynamic port
                if not auto_launch:
                    raise BrowserNotRunningError("Auto launch required when cdp_port is 0")
                logger.info("Launching Chrome process with dynamic loopback CDP port")
                await self._launch_chrome_process(port=0)
                dynamic_port = await self._resolve_dynamic_port(timeout=self.config.connect_timeout_seconds)
                self._effective_port = dynamic_port
                cdp_url = f"http://{self.config.cdp_host}:{dynamic_port}"
                is_ready = await self._wait_for_cdp(cdp_url, timeout=self.config.connect_timeout_seconds)
                if not is_ready:
                    raise BrowserCrashError(f"Chrome failed to start CDP listener on {cdp_url}")
                version_info = await self._fetch_version_info(cdp_url)
                if self._process:
                    self._record_ownership(self._process.pid, dynamic_port, version_info.get("Browser", "unknown"))

            # Connect via Playwright CDP
            if not self._playwright:
                self._playwright = await async_playwright().start()

            # Ensure at least one page target exists so connect_over_cdp does not fail
            await self._ensure_target_exists(cdp_url)

            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                raise BrowserCrashError(f"Failed to connect Playwright over CDP to {cdp_url}: {e}") from e

            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
            else:
                self._context = await self._browser.new_context()
        except Exception:
            self._release_profile_lock()
            raise

    async def _ensure_target_exists(self, cdp_url: str) -> None:
        """Ensure Chrome has at least one active page target so connect_over_cdp does not fail."""
        loop = asyncio.get_running_loop()

        def _check_and_create() -> None:
            try:
                req = urllib.request.Request(f"{cdp_url}/json/list")
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                pages = [t for t in targets if t.get("type") == "page"]
                if not pages:
                    put_req = urllib.request.Request(f"{cdp_url}/json/new", method="PUT")
                    with urllib.request.urlopen(put_req, timeout=1.0) as _:
                        pass
            except Exception as e:
                logger.debug("Failed to ensure target exists before connect_over_cdp: %s", e)

        await loop.run_in_executor(None, _check_and_create)

    async def _check_cdp_ready(self, cdp_url: str) -> bool:
        """Probe CDP JSON version endpoint synchronously via loopback."""
        loop = asyncio.get_running_loop()
        def _probe() -> bool:
            try:
                with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1.0) as resp:
                    return resp.status == 200
            except Exception:
                return False
        return await loop.run_in_executor(None, _probe)

    async def _wait_for_cdp(self, cdp_url: str, timeout: float) -> bool:
        """Poll until CDP endpoint is responsive or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._check_cdp_ready(cdp_url):
                return True
            await asyncio.sleep(0.3)
        return False

    async def _launch_chrome_process(self, port: int | None = None) -> None:
        """Launch managed Chrome subprocess strictly on loopback address."""
        if self.config.cdp_host not in ("127.0.0.1", "localhost"):
            raise BrowserSecurityError(f"Refusing to launch Chrome with non-loopback host {self.config.cdp_host}")

        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        launch_port = port if port is not None else self.config.cdp_port

        # Clear stale DevToolsActivePort if launching with dynamic port
        if launch_port == 0:
            active_port_file = self.config.user_data_dir / "DevToolsActivePort"
            if active_port_file.exists():
                with contextlib.suppress(Exception):
                    active_port_file.unlink()

        binary_path = self.config.chrome_binary_path or self._detect_chrome_binary()
        if not binary_path or not binary_path.exists():
            raise BrowserNotRunningError(
                f"Chrome binary not found at {binary_path}. Please verify Chrome installation or configure chrome_binary_path."
            )

        cmd = [
            str(binary_path),
            f"--remote-debugging-address={self.config.cdp_host}",
            f"--remote-debugging-port={launch_port}",
            f"--user-data-dir={self.config.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-blink-features=AutomationControlled",
            "about:blank",
        ]
        if self.config.headless:
            cmd.extend(["--headless=new", "--disable-gpu"])

        logger.info("Executing: %s", " ".join(cmd[:4]))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _detect_chrome_binary(self) -> Path | None:
        """Detect installed Google Chrome or Chromium binary on macOS."""
        for path in DEFAULT_MAC_CHROME_PATHS:
            if path.exists():
                return path
        return None

    async def get_page_session_info(self, page: Page | None = None) -> PageSessionInfo:
        """Get structured metadata about current page and navigation epoch."""
        target_page = page or await self.get_active_page()
        page_key = str(id(target_page))
        epoch = self._navigation_epochs.get(page_key, 1)

        try:
            url = target_page.url
            title = await target_page.title()
        except Exception:
            url = "unknown"
            title = ""

        return PageSessionInfo(
            session_id=self._session_id,
            page_id=page_key,
            url=url,
            title=title,
            navigation_epoch=epoch,
            is_active=not target_page.is_closed(),
        )

    async def disconnect(self, keep_browser_alive: bool = True) -> None:
        """Gracefully disconnect Playwright CDP client, optionally leaving Chrome process running."""
        self._ensure_loop_resources()
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.debug("Error closing browser CDP: %s", e)
                self._browser = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.debug("Error stopping playwright: %s", e)
                self._playwright = None

            if not keep_browser_alive:
                if self._process:
                    try:
                        self._process.terminate()
                        self._process.wait(timeout=2.0)
                    except Exception:
                        with contextlib.suppress(Exception):
                            self._process.kill()
                    self._process = None
                elif self._attached_pid and self._is_pid_alive(self._attached_pid):
                    try:
                        os.kill(self._attached_pid, signal.SIGTERM)
                        for _ in range(20):
                            if not self._is_pid_alive(self._attached_pid):
                                break
                            await asyncio.sleep(0.1)
                        if self._is_pid_alive(self._attached_pid):
                            os.kill(self._attached_pid, signal.SIGKILL)
                    except Exception as e:
                        logger.debug("Error terminating attached browser process: %s", e)
                    self._attached_pid = None

                if self._owns_process:
                    self._remove_ownership()
            else:
                # Leave browser process and ownership file alive for subsequent reconnect
                self._process = None
                self._attached_pid = None
                self._owns_process = False

            self._release_profile_lock()

            self._active_page = None
            self._effective_port = None
            self._navigation_epochs.clear()
            logger.info("BrowserSessionManager disconnected (keep_browser_alive=%s)", keep_browser_alive)

    async def close(self) -> None:
        """Gracefully disconnect and tear down Chrome session."""
        await self.disconnect(keep_browser_alive=False)
        logger.info("BrowserSessionManager closed cleanly")

    def __del__(self) -> None:
        self._release_profile_lock()
