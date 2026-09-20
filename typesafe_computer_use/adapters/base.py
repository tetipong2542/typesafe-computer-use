"""Abstract base contract for Universal Computer Worker Interaction Adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import (
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    VerificationExpectation,
    VerificationResult,
)


class InteractionAdapter(ABC):
    """Unified interface required for all interaction adapters (WebMCP, DOM, AX, Visual, Coordinate)."""

    @property
    @abstractmethod
    def mode(self) -> InteractionMode:
        """The specific interaction mode managed by this adapter."""
        ...

    @abstractmethod
    async def probe(self, context: dict[str, Any]) -> CapabilityReport:
        """Probe the live target (web page or desktop window) for available capabilities.
        
        Must be deterministic, lightweight, and side-effect free.
        """
        ...

    @abstractmethod
    async def execute(self, request: InteractionRequest) -> InteractionResult:
        """Execute a concrete action against the target using this adapter's methodology.
        
        Must return a structured InteractionResult tracking side_effect_state.
        """
        ...

    @abstractmethod
    async def verify(self, expectation: VerificationExpectation) -> VerificationResult:
        """Independently verify post-action state without repeating the mutating action."""
        ...

    @abstractmethod
    async def cancel(self, execution_id: str) -> None:
        """Cancel an ongoing operation (e.g. via AbortSignal)."""
        ...
