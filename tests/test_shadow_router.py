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

    # 4. Hybrid mode MUST fail-closed to shadow in Phase 2B
    monkeypatch.setenv("INTERACTION_ROUTER_MODE", "hybrid")
    assert get_interaction_router_mode() == RouterMode.SHADOW
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
