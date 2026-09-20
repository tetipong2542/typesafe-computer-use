"""Data models and configurations for Chrome CDP session and page management."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BrowserSecurityError


def get_default_profile_dir() -> Path:
    """Default persistent profile path isolated from user's personal browser."""
    return Path.home() / "Library" / "Application Support" / "TypeSafeWorker" / "ChromeProfile"


@dataclass
class BrowserSessionConfig:
    """Configuration for Chrome CDP session management."""
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 0  # Default to dynamic ephemeral port allocated by OS via --remote-debugging-port=0
    user_data_dir: Path = field(default_factory=get_default_profile_dir)
    headless: bool = False
    connect_timeout_seconds: float = 15.0
    action_timeout_seconds: float = 10.0
    chrome_binary_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate security constraints on initialization."""
        # 1. Enforce strict loopback binding to prevent remote network exposure
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if self.cdp_host.strip().lower() not in allowed_hosts:
            raise BrowserSecurityError(
                f"CDP host must bind to loopback address ({allowed_hosts}), got: {self.cdp_host}"
            )

        if self.cdp_port < 0:
            raise BrowserSecurityError(f"CDP port must be >= 0, got: {self.cdp_port}")

        if isinstance(self.user_data_dir, str):
            self.user_data_dir = Path(self.user_data_dir).expanduser()
        elif isinstance(self.user_data_dir, Path):
            self.user_data_dir = self.user_data_dir.expanduser()

        # 2. Enforce non-default user-data directory (Chrome Remote Debugging Policy & data protection)
        personal_dirs = [
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
            Path.home() / ".config" / "google-chrome",
            Path.home() / ".config" / "chromium",
        ]
        resolved = self.user_data_dir.resolve()
        for p in personal_dirs:
            if resolved == p.resolve():
                raise BrowserSecurityError(
                    f"Refusing to use personal Chrome profile directory ({resolved}) for remote debugging. "
                    "Chrome requires a dedicated non-default user-data directory for remote debugging "
                    "(https://developer.chrome.com/blog/remote-debugging-port) to protect personal browser data."
                )


@dataclass
class CDPEndpoint:
    """Resolved Chrome DevTools Protocol endpoint coordinates."""
    host: str
    port: int
    ws_url: str
    http_url: str


@dataclass
class PageSessionInfo:
    """Metadata tracking active browser page and its navigation epoch."""
    session_id: str
    page_id: str
    url: str
    title: str
    navigation_epoch: int = 0
    is_active: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
