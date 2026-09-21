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
    """Shadow/Hybrid decision and telemetry comparing DOM vs Visual potential."""
    rollout_mode: RouterMode
    executed_mode: InteractionMode
    shadow_mode: InteractionMode | None
    shadow_target: str | None
    shadow_confidence: float
    shadow_reason: str
    shadow_match_result: str
    router_reason: str
    selected_mode: InteractionMode | None = None
    fallback_from: InteractionMode | None = None
    fallback_to: InteractionMode | None = None
    fallback_reason: str | None = None
    fallback_count: int = 0
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
        self._fallback_count: int = 0

    @property
    def mode(self) -> RouterMode:
        """Active router rollout mode."""
        return self._configured_mode or get_interaction_router_mode()

    @property
    def fallback_count(self) -> int:
        """Cumulative number of automatic fallbacks in active session."""
        return self._fallback_count

    def reset_telemetry(self) -> None:
        """Reset cumulative telemetry counters."""
        self._fallback_count = 0

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
                selected_mode=InteractionMode.VISUAL_GROUNDED,
                fallback_from=None,
                fallback_to=None,
                fallback_reason=None,
                fallback_count=self._fallback_count,
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
                selected_mode=InteractionMode.VISUAL_GROUNDED,
                fallback_from=None,
                fallback_to=None,
                fallback_reason=None,
                fallback_count=self._fallback_count,
                metadata={"dom_available": dom_available},
            )
            return res, decision

        # 3. HYBRID MODE: Adaptive per-step routing with automatic fallback & side-effect invariant
        app = str(request.context.get("app") or "").strip()
        is_desktop_os = (
            bool(app and app.lower() not in ("google chrome", "chromium", "brave browser", "arc", "chrome", "browser"))
            or request.action in ("press_offscreen",)
            or str(request.target).startswith("offscreen:")
        )

        selected_mode = InteractionMode.VISUAL_GROUNDED
        router_reason = ""
        dom_available = False

        if is_desktop_os:
            selected_mode = InteractionMode.VISUAL_GROUNDED
            router_reason = f"Desktop application or OS element target ({app or 'desktop'}): routed to visual core"
        else:
            try:
                dom_report = await self.dom_adapter.probe(request.context)
                if dom_report.available:
                    dom_available = True
                    selected_mode = InteractionMode.BROWSER_DOM
                    router_reason = "Browser DOM available: selected BROWSER_DOM for precision execution"
                else:
                    selected_mode = InteractionMode.VISUAL_GROUNDED
                    router_reason = f"DOM unavailable ({dom_report.metadata.get('error', 'not reachable')}): routed to visual core"
            except Exception as e:
                selected_mode = InteractionMode.VISUAL_GROUNDED
                router_reason = f"DOM probe exception ({e}): routed to visual core"

        # Execute according to selected_mode
        if selected_mode == InteractionMode.BROWSER_DOM:
            # Map action to typed DOM action
            dom_action = request.action
            dom_args = dict(request.arguments)
            dom_target = request.target

            if dom_action == "click_item":
                dom_action = "click"
            elif dom_action == "use_browser":
                dom_action = "navigate"
                if not dom_target or dom_target == "use_browser":
                    dom_target = dom_args.get("url") or dom_args.get("site") or ""
            elif dom_action in ("type_text", "type_email"):
                dom_action = "fill"
            elif dom_action == "press_enter":
                dom_action = "press"
                dom_args.setdefault("key", "Enter")
            elif dom_action == "press_escape":
                dom_action = "press"
                dom_args.setdefault("key", "Escape")
            elif dom_action == "scroll_down":
                dom_action = "scroll"
                dom_args.setdefault("delta_y", 300)
            elif dom_action == "scroll_up":
                dom_action = "scroll"
                dom_args.setdefault("delta_y", -300)

            dom_req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action=dom_action,
                target=dom_target,
                arguments=dom_args,
                timeout_seconds=request.timeout_seconds,
                execution_id=request.execution_id,
                context=request.context,
            )

            dom_res = await self.dom_adapter.execute(dom_req)

            # Check outcome of DOM execution
            if dom_res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS:
                # Post-action verification if expectation provided
                if expectation:
                    try:
                        await self.dom_adapter.verify(expectation)
                    except Exception as e:
                        logger.debug("Post-action verification hook error: %s", e)

                decision = ShadowDecision(
                    rollout_mode=RouterMode.HYBRID,
                    executed_mode=InteractionMode.BROWSER_DOM,
                    shadow_mode=None,
                    shadow_target=dom_target,
                    shadow_confidence=dom_res.confidence,
                    shadow_reason="DOM execution confirmed success",
                    shadow_match_result="match",
                    router_reason=f"Hybrid execution: executed via {InteractionMode.BROWSER_DOM}",
                    selected_mode=InteractionMode.BROWSER_DOM,
                    fallback_from=None,
                    fallback_to=None,
                    fallback_reason=None,
                    fallback_count=self._fallback_count,
                    metadata={"dom_action": dom_action, "dom_target": dom_target},
                )
                return dom_res, decision

            elif dom_res.side_effect_state == SideEffectState.CONFIRMED_FAILURE:
                # Clean failure without persistent side-effect -> Safe to fallback to Visual Core
                fallback_reason = dom_res.error or "DOM execution failed with CONFIRMED_FAILURE"
                self._fallback_count += 1
                logger.info(
                    "DOM action %s on %s failed cleanly (%s). Automatically falling back to Visual Computer Use.",
                    dom_action,
                    dom_target,
                    fallback_reason,
                )

                vis_res = await self.visual_adapter.execute(request)

                decision = ShadowDecision(
                    rollout_mode=RouterMode.HYBRID,
                    executed_mode=InteractionMode.VISUAL_GROUNDED,
                    shadow_mode=InteractionMode.BROWSER_DOM,
                    shadow_target=dom_target,
                    shadow_confidence=vis_res.confidence,
                    shadow_reason=f"DOM failed ({fallback_reason}); visual executed.",
                    shadow_match_result="fallback_to_visual",
                    router_reason=f"Hybrid fallback: BROWSER_DOM -> VISUAL_GROUNDED due to {fallback_reason}",
                    selected_mode=InteractionMode.BROWSER_DOM,
                    fallback_from=InteractionMode.BROWSER_DOM,
                    fallback_to=InteractionMode.VISUAL_GROUNDED,
                    fallback_reason=fallback_reason,
                    fallback_count=self._fallback_count,
                    metadata={"fallback": True, "dom_error": fallback_reason},
                )
                return vis_res, decision

            else:
                # SideEffectState.UNKNOWN: Ambiguous mutation state!
                # STRICT SAFETY INVARIANT: automatic retry and fallback are FORBIDDEN.
                logger.warning(
                    "DOM action %s on %s resulted in side_effect_state=UNKNOWN (%s). "
                    "Automatic retry and fallback are strictly forbidden to prevent duplicate writes.",
                    dom_action,
                    dom_target,
                    dom_res.error,
                )
                decision = ShadowDecision(
                    rollout_mode=RouterMode.HYBRID,
                    executed_mode=InteractionMode.BROWSER_DOM,
                    shadow_mode=None,
                    shadow_target=dom_target,
                    shadow_confidence=0.0,
                    shadow_reason="DOM side-effect state unknown; fallback strictly forbidden",
                    shadow_match_result="unknown_side_effect",
                    router_reason="Hybrid execution safety stop: side_effect_state UNKNOWN; escalate to human review.",
                    selected_mode=InteractionMode.BROWSER_DOM,
                    fallback_from=None,
                    fallback_to=None,
                    fallback_reason="Fallback forbidden: ambiguous side effect state",
                    fallback_count=self._fallback_count,
                    metadata={"fallback_forbidden": True, "side_effect_state": dom_res.side_effect_state.value},
                )
                return dom_res, decision

        # Direct VISUAL_GROUNDED execution
        vis_res = await self.visual_adapter.execute(request)
        decision = ShadowDecision(
            rollout_mode=RouterMode.HYBRID,
            executed_mode=InteractionMode.VISUAL_GROUNDED,
            shadow_mode=InteractionMode.BROWSER_DOM if dom_available else None,
            shadow_target=request.target,
            shadow_confidence=vis_res.confidence,
            shadow_reason="Visual execution selected for target/context.",
            shadow_match_result="visual_executed",
            router_reason=router_reason or "Visual grounded adapter selected",
            selected_mode=InteractionMode.VISUAL_GROUNDED,
            fallback_from=None,
            fallback_to=None,
            fallback_reason=None,
            fallback_count=self._fallback_count,
            metadata={"dom_available": dom_available},
        )
        return vis_res, decision
