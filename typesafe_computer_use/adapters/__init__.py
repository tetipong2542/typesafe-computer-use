"""Universal Computer Worker Interaction Adapters and Router feature gating."""

from __future__ import annotations

import os

from .base import InteractionAdapter
from .models import (
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    SideEffectState,
    VerificationExpectation,
    VerificationResult,
)
from .visual import VisualComputerUseAdapter


def is_interaction_router_enabled() -> bool:
    """Check feature flag for dynamic capability routing.
    
    When False (default), all interactions strictly follow the legacy visual-only pipeline.
    """
    return os.environ.get("INTERACTION_ROUTER_ENABLED", "false").lower() in ("1", "true", "yes")


__all__ = [
    "CapabilityReport",
    "InteractionAdapter",
    "InteractionMode",
    "InteractionRequest",
    "InteractionResult",
    "RiskLevel",
    "SideEffectState",
    "VerificationExpectation",
    "VerificationResult",
    "VisualComputerUseAdapter",
    "is_interaction_router_enabled",
]
