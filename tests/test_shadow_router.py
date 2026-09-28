"""Unit and Integration tests for ShadowInteractionRouter and Rollout Modes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from typesafe_computer_use.adapters import (
    BrowserDOMAdapter,
    CapabilityReport,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    RouterMode,
    SideEffectState,
    VerificationExpectation,
    VerificationResult,
    VisualComputerUseAdapter,
    get_interaction_router_mode,
    is_interaction_router_enabled,
)
from typesafe_computer_use.router import ShadowInteractionRouter


def test_rollout_mode_detection(monkeypatch: pytest.MonkeyPatch):
    """Test environment variable precedence and backward compatibility mapping."""
    # 1. Default (no env set) -> LEGACY
    monkeypatch.delenv("INTERACTION_ROUTER_MODE", raising=False)
    monkeypatch.delenv("INTERACTION_ROUTER_ENABLED", raising=False)
    assert get_interaction_router_mode() == RouterMode.LEGACY
    assert is_interaction_router_enabled() is False

    # 2. INTERACTION_ROUTER_ENABLED backward compatibility
    monkeypatch.setenv("INTERACTION_ROUTER_ENABLED", "true")
    assert get_interaction_router_mode() == RouterMode.SHADOW
    assert is_interaction_router_enabled() is True

    monkeypatch.setenv("INTERACTION_ROUTER_ENABLED", "false")
    assert get_interaction_router_mode() == RouterMode.LEGACY
    assert is_interaction_router_enabled() is False

    # 3. INTERACTION_ROUTER_MODE takes precedence over INTERACTION_ROUTER_ENABLED
    monkeypatch.setenv("INTERACTION_ROUTER_MODE", "shadow")
    monkeypatch.setenv("INTERACTION_ROUTER_ENABLED", "false")
    assert get_interaction_router_mode() == RouterMode.SHADOW
    assert is_interaction_router_enabled() is True

    # 4. Hybrid mode is enabled in Phase 2C
    monkeypatch.setenv("INTERACTION_ROUTER_MODE", "hybrid")
    assert get_interaction_router_mode() == RouterMode.HYBRID
    assert is_interaction_router_enabled() is True


def test_legacy_mode_bypasses_dom_completely():
    """In LEGACY mode, router must execute via visual adapter and never probe/call DOM adapter."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.VISUAL_GROUNDED,
            adapter="VisualComputerUseAdapter",
            action="click",
            target="item-3",
            arguments={},
            confidence=0.92,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=120.0,
            result="Clicked item-3",
        ))

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock()
        mock_dom.execute = AsyncMock()

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.LEGACY,
        )

        req = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click",
            target="item-3",
        )
        res, decision = await router.route_and_execute(req)

        # Visual execute called once
        assert mock_visual.execute.await_count == 1
        # DOM probe and execute NEVER called
        assert mock_dom.probe.await_count == 0
        assert mock_dom.execute.await_count == 0

        # Verify decision telemetry
        assert decision.rollout_mode == RouterMode.LEGACY
        assert decision.executed_mode == InteractionMode.VISUAL_GROUNDED
        assert decision.shadow_mode is None
        assert "Legacy mode active" in decision.shadow_reason
        assert res.confidence == 0.92

    asyncio.run(_test())


def test_shadow_mode_probes_dom_and_executes_visual():
    """In SHADOW mode, router probes DOM, computes shadow telemetry, but executes visual core."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.VISUAL_GROUNDED,
            adapter="VisualComputerUseAdapter",
            action="click",
            target="[data-testid='btn-login']",
            arguments={},
            confidence=0.91,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=150.0,
            result="Clicked [data-testid='btn-login']",
        ))

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            supported_actions=["click", "fill"],
            locators=["[data-testid='btn-login']", "#username"],
        ))
        mock_dom.execute = AsyncMock()
        mock_dom.verify = AsyncMock(return_value=VerificationResult(
            verified=True,
            mode=InteractionMode.BROWSER_DOM,
            reason="Verified visible in DOM",
        ))

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.SHADOW,
        )

        req = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click",
            target="[data-testid='btn-login']",
        )
        expectation = VerificationExpectation(
            mode=InteractionMode.BROWSER_DOM,
            condition="visible",
            target="#login-status",
        )

        res, decision = await router.route_and_execute(req, expectation=expectation)

        # Both DOM probe and Visual execute were called
        assert mock_dom.probe.await_count == 1
        assert mock_visual.execute.await_count == 1
        # Physical DOM execute was NOT called (visual handles physical action)
        assert mock_dom.execute.await_count == 0
        # Post-action verify was called
        assert mock_dom.verify.await_count == 1

        # Check shadow decision telemetry
        assert res.confidence == 0.91
        assert decision.rollout_mode == RouterMode.SHADOW
        assert decision.executed_mode == InteractionMode.VISUAL_GROUNDED
        assert decision.shadow_mode == InteractionMode.BROWSER_DOM
        assert decision.shadow_target == "[data-testid='btn-login']"
        assert decision.shadow_confidence == 0.95
        assert "Exact semantic locator match in DOM" in decision.shadow_reason
        assert decision.shadow_match_result == "match"

    asyncio.run(_test())


def test_hybrid_mode_dom_stall_escalates_to_visual():
    """In HYBRID mode, repeated DOM actions or failed verification trigger dynamic escalation to Visual."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.VISUAL_GROUNDED,
            adapter="VisualComputerUseAdapter",
            action="click_item",
            target="item-7",
            arguments={},
            confidence=0.94,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=250.0,
            result="Clicked item-7",
        ))

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            locators=["button.submit"],
        ))
        mock_dom.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.BROWSER_DOM,
            adapter="BrowserDOMAdapter",
            action="click",
            target="button.submit",
            arguments={},
            confidence=0.88,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=45.0,
            result="Clicked button.submit",
        ))
        # Expectation verification fails, simulating unverified UI state / no progress
        mock_dom.verify = AsyncMock(return_value=VerificationResult(
            verified=False,
            mode=InteractionMode.BROWSER_DOM,
            reason="Element not updated",
        ))

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.HYBRID,
        )

        req = InteractionRequest(
            mode=InteractionMode.BROWSER_DOM,
            action="click_item",
            target="button.submit",
            context={"app": "Google Chrome"},
        )
        expectation = VerificationExpectation(
            mode=InteractionMode.BROWSER_DOM,
            condition="visible",
            target="#success-badge",
        )

        # Turn 1: DOM executes, verification fails (consecutive_stalls = 1, threshold not yet reached)
        _res1, dec1 = await router.route_and_execute(req, expectation=expectation)
        assert dec1.executed_mode == InteractionMode.BROWSER_DOM
        assert dec1.progress_signal == "progress"
        assert dec1.verification_result == "fail"

        # Turn 2: Repeated action with unverified state -> triggers escalation!
        _res2, dec2 = await router.route_and_execute(req, expectation=expectation)
        assert dec2.executed_mode == InteractionMode.VISUAL_GROUNDED
        assert dec2.fallback_from == InteractionMode.BROWSER_DOM
        assert dec2.fallback_to == InteractionMode.VISUAL_GROUNDED
        assert dec2.progress_signal == "loop_detected"
        assert dec2.side_effect_state == "confirmed_success"
        assert dec2.verification_result == "pass"
        assert mock_visual.execute.await_count == 1

    asyncio.run(_test())

