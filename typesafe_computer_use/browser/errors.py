"""Browser automation and CDP errors for TypeSafe Computer Worker."""

from __future__ import annotations


class BrowserError(Exception):
    """Base exception for all browser and CDP related operations."""
    pass


class BrowserNotRunningError(BrowserError):
    """Raised when Chrome or CDP endpoint is unreachable or not running."""
    pass


class BrowserCrashError(BrowserError):
    """Raised when the browser or target page crashes or becomes unresponsive."""
    pass


class NavigationEpochError(BrowserError):
    """Raised when an operation is attempted across an invalid or obsolete navigation epoch."""
    pass


class ElementNotFoundError(BrowserError):
    """Raised when a semantic locator or selector matches zero elements on the page."""
    pass


class AmbiguousTargetError(BrowserError):
    """Raised when a semantic locator matches multiple elements and cannot be uniquely resolved."""
    pass


class BrowserSecurityError(BrowserError):
    """Raised when a security guardrail is violated (e.g. non-loopback CDP host binding)."""
    pass
