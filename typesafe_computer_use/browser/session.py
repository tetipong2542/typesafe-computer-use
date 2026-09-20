"""Shared Chrome/CDP Session Manager using Playwright for Universal Computer Worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
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

    @property
    def is_connected(self) -> bool:
        """Check if CDP connection is active and responsive."""
        return self._browser is not None and self._browser.is_connected()

    @property
    def session_id(self) -> str:
        return self._session_id

    def get_navigation_epoch(self, page: Page | None = None) -> int:
        """Get current navigation epoch for a given page or the active page."""
        target_page = page or self._active_page
        if not target_page:
            return 0
        page_key = str(id(target_page))
        return self._navigation_epochs.get(page_key, 0)

    async def get_active_page(self, page_id: str | None = None) -> Page:
        """Get or resolve active page deterministically. Raises AmbiguousPageError if ambiguous."""
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

    async def _connect_internal(self, auto_launch: bool = True) -> None:
        """Connect to Chrome via CDP over loopback, optionally launching it if missing."""
        cdp_url = f"http://{self.config.cdp_host}:{self.config.cdp_port}"

        # 1. Verify CDP endpoint reachability
        is_running = await self._check_cdp_ready(cdp_url)

        # 2. If not running and auto_launch is True, spawn Chrome process
        if not is_running:
            if not auto_launch:
                raise BrowserNotRunningError(f"CDP endpoint at {cdp_url} is not responding")
            logger.info("Launching Chrome process on loopback CDP port %d", self.config.cdp_port)
            await self._launch_chrome_process()
            # Wait for readiness
            is_running = await self._wait_for_cdp(cdp_url, timeout=self.config.connect_timeout_seconds)
            if not is_running:
                raise BrowserCrashError(f"Chrome failed to start CDP listener on {cdp_url}")

        # 3. Connect via Playwright CDP
        if not self._playwright:
            self._playwright = await async_playwright().start()

        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            raise BrowserCrashError(f"Failed to connect Playwright over CDP to {cdp_url}: {e}") from e

        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = await self._browser.new_context()

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

    async def _launch_chrome_process(self) -> None:
        """Launch managed Chrome subprocess strictly on loopback address."""
        # Enforce security constraint
        if self.config.cdp_host not in ("127.0.0.1", "localhost"):
            raise BrowserSecurityError(f"Refusing to launch Chrome with non-loopback host {self.config.cdp_host}")

        # Ensure user data dir exists
        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)

        binary_path = self.config.chrome_binary_path or self._detect_chrome_binary()
        if not binary_path or not binary_path.exists():
            raise BrowserNotRunningError(
                f"Chrome binary not found at {binary_path}. Please verify Chrome installation or configure chrome_binary_path."
            )

        cmd = [
            str(binary_path),
            f"--remote-debugging-address={self.config.cdp_host}",
            f"--remote-debugging-port={self.config.cdp_port}",
            f"--user-data-dir={self.config.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-blink-features=AutomationControlled",
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

    async def close(self) -> None:
        """Gracefully disconnect and tear down Chrome session."""
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

            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=2.0)
                except Exception:
                    with contextlib.suppress(Exception):
                        self._process.kill()
                self._process = None

            self._active_page = None
            self._navigation_epochs.clear()
            logger.info("BrowserSessionManager closed cleanly")
