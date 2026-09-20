"""Browser automation, CDP session management, and DOM interaction package."""

from __future__ import annotations

from .errors import (
    AmbiguousPageError,
    AmbiguousTargetError,
    BrowserCrashError,
    BrowserError,
    BrowserNotRunningError,
    BrowserSecurityError,
    ElementNotFoundError,
    NavigationEpochError,
)
from .models import BrowserSessionConfig, CDPEndpoint, PageSessionInfo, get_default_profile_dir
from .session import BrowserSessionManager

__all__ = [
    "AmbiguousPageError",
    "AmbiguousTargetError",
    "BrowserCrashError",
    "BrowserError",
    "BrowserNotRunningError",
    "BrowserSecurityError",
    "BrowserSessionConfig",
    "BrowserSessionManager",
    "CDPEndpoint",
    "ElementNotFoundError",
    "NavigationEpochError",
    "PageSessionInfo",
    "get_default_profile_dir",
]
