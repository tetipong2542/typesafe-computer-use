"""Data models, enums, and schemas for Universal Computer Worker Interaction Adapters."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class InteractionMode(enum.StrEnum):
    """Supported interaction modes for the Universal Computer Worker."""
    WEBMCP = "webmcp"                  # W3C WebMCP structured in-page tools
    BROWSER_DOM = "browser_dom"        # CDP / Playwright semantic DOM locators
    BROWSER_SCRIPT = "browser_script"  # Constrained in-page JS automation
    MACOS_AX = "macos_ax"              # Native macOS Accessibility API
    VISUAL_GROUNDED = "visual_grounded"# TypeSafe OCR + AX grounded selection (Core Baseline)
    RAW_COORDINATE = "raw_coordinate"  # Direct coordinate input fallback


class SideEffectState(enum.StrEnum):
    """Mutation / side-effect status tracking to guarantee idempotency and prevent duplicate writes."""
    NOT_STARTED = "not_started"              # Operation aborted or failed before starting any mutation
    CONFIRMED_SUCCESS = "confirmed_success"  # Mutation completed and independently verified
    CONFIRMED_FAILURE = "confirmed_failure"  # Failed cleanly without persistent state mutation
    UNKNOWN = "unknown"                      # Timeout or connection drop during write; ambiguous state


class RiskLevel(enum.StrEnum):
    """Risk classification for actions to determine approval gating."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CapabilityReport:
    """Live capability report produced by probing a target (web page or desktop window)."""
    mode: InteractionMode
    available: bool
    supported_actions: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    locators: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    snapshot_id: str = ""


@dataclass
class InteractionRequest:
    """Request payload sent to an InteractionAdapter to perform an action."""
    mode: InteractionMode
    action: str
    target: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    execution_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class InteractionResult:
    """Unified result schema returned by all Interaction Adapters."""
    mode: InteractionMode
    adapter: str
    action: str
    target: str
    arguments: dict[str, Any]
    confidence: float
    risk: RiskLevel
    side_effect_state: SideEffectState
    duration_ms: float
    result: Any
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)


@dataclass
class VerificationExpectation:
    """Expectation for verifying post-action state independently."""
    mode: InteractionMode
    condition: str
    target: str
    expected_value: Any = None
    timeout_seconds: float = 5.0


@dataclass
class VerificationResult:
    """Outcome of an independent verification check."""
    verified: bool
    mode: InteractionMode
    reason: str
    evidence: Any = None
