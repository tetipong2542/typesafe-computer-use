"""Universal Computer Worker Interaction Adapters and Router feature gating."""

from __future__ import annotations

import os

from .base import InteractionAdapter
from .browser_dom import BrowserDOMAdapter
from .models import (
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    RouterMode,
    SideEffectState,
    VerificationExpectation,
    VerificationResult,
)
from .visual import VisualComputerUseAdapter


def get_interaction_router_mode() -> RouterMode:
    """Get active rollout mode for the Interaction Router.
    
    Precedence:
    1. INTERACTION_ROUTER_MODE environment variable ('legacy', 'shadow', 'hybrid').
       Note: 'hybrid' is fail-closed to 'shadow' until Phase 2D structured execution is verified.
    2. INTERACTION_ROUTER_ENABLED backward compatibility mapping:
       - 'false' / '0' / 'no' -> RouterMode.LEGACY
       - 'true' / '1' / 'yes' -> RouterMode.SHADOW
    3. Default: RouterMode.LEGACY (100% visual-only).
    """
    raw_mode = os.environ.get("INTERACTION_ROUTER_MODE")
    if raw_mode:
        raw_mode_clean = raw_mode.strip().lower()
        if raw_mode_clean in (RouterMode.LEGACY, RouterMode.SHADOW, RouterMode.HYBRID):
            return RouterMode(raw_mode_clean)

    # Fallback to boolean flag backward compatibility
    legacy_flag = os.environ.get("INTERACTION_ROUTER_ENABLED")
    if legacy_flag is not None:
        if legacy_flag.strip().lower() in ("1", "true", "yes"):
            return RouterMode.SHADOW
        return RouterMode.LEGACY

    return RouterMode.LEGACY


def is_interaction_router_enabled() -> bool:
    """Check feature flag for dynamic capability routing.
    
    Returns True when mode is anything other than LEGACY.
    """
    return get_interaction_router_mode() != RouterMode.LEGACY


__all__ = [
    "BrowserDOMAdapter",
    "CapabilityReport",
    "InteractionAdapter",
    "InteractionMode",
    "InteractionRequest",
    "InteractionResult",
    "RiskLevel",
    "RouterMode",
    "SideEffectState",
    "VerificationExpectation",
    "VerificationResult",
    "VisualComputerUseAdapter",
    "get_interaction_router_mode",
    "is_interaction_router_enabled",
]
