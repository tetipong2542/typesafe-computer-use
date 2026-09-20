"""Shadow Interaction Router evaluating DOM capabilities while delegating execution to visual core."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from typesafe_computer_use.adapters import (
    BrowserDOMAdapter,
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RouterMode,
    SideEffectState,
    VerificationExpectation,
    VisualComputerUseAdapter,
    get_interaction_router_mode,
)

logger = logging.getLogger("typesafe.router.shadow")


@dataclass
class ShadowDecision:
    """Shadow decision and telemetry comparing DOM vs Visual potential."""
    rollout_mode: RouterMode
    executed_mode: InteractionMode
    shadow_mode: InteractionMode | None
    shadow_target: str | None
    shadow_confidence: float
    shadow_reason: str
    shadow_match_result: str
    router_reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ShadowInteractionRouter:
    """Routes actions according to rollout mode. In shadow mode, probes DOM but executes via visual core."""

    def __init__(
        self,
        visual_adapter: VisualComputerUseAdapter | None = None,
        dom_adapter: BrowserDOMAdapter | None = None,
        mode: RouterMode | None = None,
    ) -> None:
        self.visual_adapter = visual_adapter or VisualComputerUseAdapter()
        self.dom_adapter = dom_adapter or BrowserDOMAdapter()
        self._configured_mode = mode

    @property
    def mode(self) -> RouterMode:
        """Active router rollout mode."""
        return self._configured_mode or get_interaction_router_mode()

    async def probe(self, context: dict[str, Any] | None = None) -> dict[InteractionMode, CapabilityReport]:
        """Probe available adapters according to active rollout mode."""
        reports: dict[InteractionMode, CapabilityReport] = {}

        # Visual is always available as baseline
        reports[InteractionMode.VISUAL_GROUNDED] = await self.visual_adapter.probe(context)

        # In legacy mode, do not probe CDP/DOM to avoid any browser side-effects or latency
        if self.mode == RouterMode.LEGACY:
            return reports

        # In shadow or hybrid mode, probe DOM capability
        try:
            reports[InteractionMode.BROWSER_DOM] = await self.dom_adapter.probe(context)
        except Exception as e:
            logger.debug("Failed to probe DOM adapter in router: %s", e)
            reports[InteractionMode.BROWSER_DOM] = CapabilityReport(
                mode=InteractionMode.BROWSER_DOM,
                available=False,
                metadata={"error": str(e)},
            )

        return reports

    async def route_and_execute(
        self,
        request: InteractionRequest,
        expectation: VerificationExpectation | None = None,
    ) -> tuple[InteractionResult, ShadowDecision]:
        """Evaluate route, execute action, and return both result and shadow telemetry."""
        current_mode = self.mode

        # 1. LEGACY MODE: Pure visual-only, zero CDP
        if current_mode == RouterMode.LEGACY:
            res = await self.visual_adapter.execute(request)
            decision = ShadowDecision(
                rollout_mode=RouterMode.LEGACY,
                executed_mode=InteractionMode.VISUAL_GROUNDED,
                shadow_mode=None,
                shadow_target=None,
                shadow_confidence=0.0,
                shadow_reason="Legacy mode active: strictly visual execution, zero CDP overhead.",
                shadow_match_result="n/a",
                router_reason="Legacy visual-only pipeline",
            )
            return res, decision

        # 2. SHADOW MODE: Probe DOM in parallel, compute shadow decision, but execute visual
        if current_mode == RouterMode.SHADOW:
            shadow_target: str | None = None
            shadow_confidence = 0.0
            shadow_reason = "DOM probe not applicable or failed"
            dom_available = False

            # Conduct shadow probe
            try:
                dom_report = await self.dom_adapter.probe(request.context)
                if dom_report.available:
                    dom_available = True
                    # Check if target matches candidate locators or is semantic
                    shadow_target = request.target
                    if shadow_target in dom_report.locators:
                        shadow_confidence = 0.95
                        shadow_reason = f"Exact semantic locator match in DOM: {shadow_target}"
                    elif dom_report.locators:
                        shadow_confidence = 0.70
                        shadow_reason = f"DOM available with {len(dom_report.locators)} interactive elements."
                    else:
                        shadow_confidence = 0.50
                        shadow_reason = "DOM available on page but no direct locator matched."
            except Exception as e:
                shadow_reason = f"Shadow probe error: {e}"

            # Execute via proven visual core
            res = await self.visual_adapter.execute(request)

            # Optional post-action read-only DOM verification
            shadow_match = "diverged"
            if expectation and dom_available:
                try:
                    v_res = await self.dom_adapter.verify(expectation)
                    shadow_match = "match" if v_res.verified else "diverged"
                except Exception:
                    shadow_match = "verification_failed"
            else:
                shadow_match = "match" if res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS else "visual_failed"

            decision = ShadowDecision(
                rollout_mode=RouterMode.SHADOW,
                executed_mode=InteractionMode.VISUAL_GROUNDED,
                shadow_mode=InteractionMode.BROWSER_DOM if dom_available else None,
                shadow_target=shadow_target,
                shadow_confidence=shadow_confidence,
                shadow_reason=shadow_reason,
                shadow_match_result=shadow_match,
                router_reason=f"Shadow mode: executed visual action. Shadow DOM evaluation: {shadow_reason}",
                metadata={"dom_available": dom_available},
            )
            return res, decision

        # 3. HYBRID MODE: Reserved for Phase 2D structured execution
        # In Phase 2B, if hybrid is selected, fallback safely to visual if DOM not ready
        res = await self.visual_adapter.execute(request)
        decision = ShadowDecision(
            rollout_mode=RouterMode.HYBRID,
            executed_mode=InteractionMode.VISUAL_GROUNDED,
            shadow_mode=InteractionMode.BROWSER_DOM,
            shadow_target=request.target,
            shadow_confidence=0.85,
            shadow_reason="Hybrid mode (Phase 2B preview): visual execution with DOM telemetry.",
            shadow_match_result="match",
            router_reason="Hybrid mode router",
        )
        return res, decision
