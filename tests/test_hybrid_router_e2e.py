"""Comprehensive End-to-End and Integration tests for Structured Hybrid Execution (Phase 2C).

Verifies:
1. Direct DOM execution via BrowserDOMAdapter when target matches page DOM.
2. Automatic fallback from DOM -> Visual on CONFIRMED_FAILURE.
3. Strict block on fallback/retry when side_effect_state is UNKNOWN.
4. Mixed-mode workflow (browser DOM step followed by desktop OS visual step).
5. Action translation from TypeSafe decision kinds to typed DOM actions.
6. Cumulative fallback counting and telemetry persistence in SQLite.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

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
)
from typesafe_computer_use.router.shadow import ShadowInteractionRouter
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.state import EventPhase, TaskEvent, TaskState


def test_hybrid_direct_dom_execution():
    """In HYBRID mode, web actions on available DOM targets execute directly via BrowserDOMAdapter."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock()

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            supported_actions=["click", "fill", "navigate"],
            locators=["[data-testid='btn-submit']", "#username"],
        ))
        mock_dom.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.BROWSER_DOM,
            adapter="BrowserDOMAdapter",
            action="click",
            target="[data-testid='btn-submit']",
            arguments={},
            confidence=0.98,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=45.0,
            result={"clicked": "[data-testid='btn-submit']"},
        ))
        mock_dom.verify = AsyncMock(return_value=VerificationResult(
            verified=True,
            mode=InteractionMode.BROWSER_DOM,
            reason="Verified element present",
        ))

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.HYBRID,
        )

        req = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click_item",
            target="[data-testid='btn-submit']",
            context={"app": "Google Chrome", "url": "https://example.com/checkout"},
        )
        expectation = VerificationExpectation(
            condition="visible",
            target="#success-badge",
            mode=InteractionMode.BROWSER_DOM,
        )

        res, decision = await router.route_and_execute(req, expectation=expectation)

        # 1. Probe & DOM execute called
        assert mock_dom.probe.await_count == 1
        assert mock_dom.execute.await_count == 1
        assert mock_dom.verify.await_count == 1

        # 2. Visual adapter was NOT called because DOM succeeded directly
        assert mock_visual.execute.await_count == 0

        # 3. Verify decision telemetry
        assert decision.rollout_mode == RouterMode.HYBRID
        assert decision.selected_mode == InteractionMode.BROWSER_DOM
        assert decision.executed_mode == InteractionMode.BROWSER_DOM
        assert decision.fallback_from is None
        assert decision.fallback_to is None
        assert decision.fallback_count == 0
        assert res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS

        # 4. Check action translation from click_item to click
        called_req = mock_dom.execute.call_args[0][0]
        assert called_req.action == "click"

    asyncio.run(_test())


def test_hybrid_automatic_fallback_on_confirmed_failure():
    """In HYBRID mode, clean DOM failure (CONFIRMED_FAILURE) triggers automatic fallback to visual core."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.VISUAL_GROUNDED,
            adapter="VisualComputerUseAdapter",
            action="click_item",
            target="Missing DOM Button",
            arguments={},
            confidence=0.88,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=110.0,
            result="clicked 'Missing DOM Button' via visual core",
        ))

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            supported_actions=["click"],
            locators=[],
        ))
        mock_dom.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.BROWSER_DOM,
            adapter="BrowserDOMAdapter",
            action="click",
            target="Missing DOM Button",
            arguments={},
            confidence=0.0,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_FAILURE,
            duration_ms=25.0,
            result=None,
            error="ElementNotFoundError: Element 'Missing DOM Button' not found in DOM",
        ))

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.HYBRID,
        )

        req = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click_item",
            target="Missing DOM Button",
            context={"app": "Google Chrome", "url": "https://example.com/app"},
        )

        res, decision = await router.route_and_execute(req)

        # 1. Both DOM execute and Visual execute were called
        assert mock_dom.execute.await_count == 1
        assert mock_visual.execute.await_count == 1

        # 2. Decision records fallback details
        assert decision.rollout_mode == RouterMode.HYBRID
        assert decision.selected_mode == InteractionMode.BROWSER_DOM
        assert decision.executed_mode == InteractionMode.VISUAL_GROUNDED
        assert decision.fallback_from == InteractionMode.BROWSER_DOM
        assert decision.fallback_to == InteractionMode.VISUAL_GROUNDED
        assert "ElementNotFoundError" in (decision.fallback_reason or "")
        assert decision.fallback_count == 1
        assert router.fallback_count == 1

        # 3. Overall result reflects visual success
        assert res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
        assert res.mode == InteractionMode.VISUAL_GROUNDED

    asyncio.run(_test())


def test_hybrid_strict_block_on_unknown_side_effect():
    """In HYBRID mode, side_effect_state UNKNOWN strictly forbids retry and fallback to visual."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock()

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            supported_actions=["click"],
            locators=["#pay-now-button"],
        ))
        # Ambiguous timeout during mutation
        mock_dom.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.BROWSER_DOM,
            adapter="BrowserDOMAdapter",
            action="click",
            target="#pay-now-button",
            arguments={},
            confidence=0.0,
            risk=RiskLevel.HIGH,
            side_effect_state=SideEffectState.UNKNOWN,
            duration_ms=10000.0,
            result=None,
            error="Playwright TimeoutError: Timeout 10000ms exceeded during click",
        ))

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.HYBRID,
        )

        req = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click_item",
            target="#pay-now-button",
            context={"app": "Google Chrome", "url": "https://bank.example.com/transfer"},
        )

        res, decision = await router.route_and_execute(req)

        # 1. DOM was executed
        assert mock_dom.execute.await_count == 1

        # 2. Visual adapter MUST NOT be executed (STRICT SAFETY INVARIANT)
        assert mock_visual.execute.await_count == 0

        # 3. Returned result preserves UNKNOWN side effect
        assert res.side_effect_state == SideEffectState.UNKNOWN
        assert decision.fallback_from is None
        assert decision.fallback_to is None
        assert decision.fallback_count == 0
        assert decision.metadata.get("fallback_forbidden") is True
        assert "UNKNOWN" in decision.router_reason

    asyncio.run(_test())


def test_hybrid_mixed_mode_task_routing():
    """Mixed-mode tasks: browser steps use DOM; desktop OS steps use Visual directly without probing DOM."""
    async def _test():
        mock_visual = MagicMock(spec=VisualComputerUseAdapter)
        mock_visual.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.VISUAL_GROUNDED,
            adapter="VisualComputerUseAdapter",
            action="click_item",
            target="Finder Document",
            arguments={},
            confidence=0.95,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=90.0,
            result="clicked Finder Document",
        ))

        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            supported_actions=["click"],
            locators=["[data-testid='download-btn']"],
        ))
        mock_dom.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.BROWSER_DOM,
            adapter="BrowserDOMAdapter",
            action="click",
            target="[data-testid='download-btn']",
            arguments={},
            confidence=0.97,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=30.0,
            result={"clicked": "[data-testid='download-btn']"},
        ))

        router = ShadowInteractionRouter(
            visual_adapter=mock_visual,
            dom_adapter=mock_dom,
            mode=RouterMode.HYBRID,
        )

        # Step 1: In Chrome browser -> DOM execution
        req_step1 = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click_item",
            target="[data-testid='download-btn']",
            context={"app": "Google Chrome", "step": 1},
        )
        _res1, dec1 = await router.route_and_execute(req_step1)
        assert dec1.selected_mode == InteractionMode.BROWSER_DOM
        assert dec1.executed_mode == InteractionMode.BROWSER_DOM
        assert mock_dom.execute.await_count == 1
        assert mock_visual.execute.await_count == 0

        # Step 2: In macOS Finder -> Visual execution (desktop app)
        req_step2 = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="click_item",
            target="Finder Document",
            context={"app": "Finder", "step": 2},
        )
        _res2, dec2 = await router.route_and_execute(req_step2)
        assert dec2.selected_mode == InteractionMode.VISUAL_GROUNDED
        assert dec2.executed_mode == InteractionMode.VISUAL_GROUNDED
        assert mock_visual.execute.await_count == 1
        # DOM execute was NOT called again for Finder
        assert mock_dom.execute.await_count == 1

        # Step 3: Offscreen control -> Visual execution
        req_step3 = InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="press_offscreen",
            target="offscreen:2",
            context={"app": "Google Chrome", "step": 3},
        )
        _res3, dec3 = await router.route_and_execute(req_step3)
        assert dec3.selected_mode == InteractionMode.VISUAL_GROUNDED
        assert dec3.executed_mode == InteractionMode.VISUAL_GROUNDED
        assert mock_visual.execute.await_count == 2

    asyncio.run(_test())


def test_hybrid_action_translations():
    """Verify mapping of high-level TypeSafe actions to typed DOM actions."""
    async def _test():
        mock_dom = MagicMock(spec=BrowserDOMAdapter)
        mock_dom.probe = AsyncMock(return_value=CapabilityReport(
            mode=InteractionMode.BROWSER_DOM,
            available=True,
            supported_actions=["navigate", "fill", "press", "scroll"],
        ))
        mock_dom.execute = AsyncMock(return_value=InteractionResult(
            mode=InteractionMode.BROWSER_DOM,
            adapter="BrowserDOMAdapter",
            action="ok",
            target="ok",
            arguments={},
            confidence=0.99,
            risk=RiskLevel.NORMAL,
            side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
            duration_ms=10.0,
            result=None,
        ))

        router = ShadowInteractionRouter(dom_adapter=mock_dom, mode=RouterMode.HYBRID)

        # 1. use_browser -> navigate
        await router.route_and_execute(InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="use_browser",
            target="https://google.com",
            arguments={"url": "https://google.com"},
            context={"app": "Google Chrome"},
        ))
        req1 = mock_dom.execute.call_args[0][0]
        assert req1.action == "navigate"
        assert req1.target == "https://google.com"

        # 2. type_text -> fill
        await router.route_and_execute(InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="type_text",
            target="#search-input",
            arguments={"text": "quantum computing"},
            context={"app": "Google Chrome"},
        ))
        req2 = mock_dom.execute.call_args[0][0]
        assert req2.action == "fill"
        assert req2.arguments["text"] == "quantum computing"

        # 3. press_enter -> press with Enter
        await router.route_and_execute(InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="press_enter",
            target="Enter",
            context={"app": "Google Chrome"},
        ))
        req3 = mock_dom.execute.call_args[0][0]
        assert req3.action == "press"
        assert req3.arguments["key"] == "Enter"

        # 4. scroll_down -> scroll delta_y=300
        await router.route_and_execute(InteractionRequest(
            mode=InteractionMode.VISUAL_GROUNDED,
            action="scroll_down",
            target="page",
            context={"app": "Google Chrome"},
        ))
        req4 = mock_dom.execute.call_args[0][0]
        assert req4.action == "scroll"
        assert req4.arguments["delta_y"] == 300

    asyncio.run(_test())


def test_sqlite_event_telemetry_persistence(tmp_path):
    """Verify SQLite persistence for hybrid telemetry fields: selected_mode and fallback_count."""
    db_path = tmp_path / "worker_test.db"
    db = WorkerDatabase(str(db_path))

    task = db.create_task(task_id="task_hybrid_001", goal="Test hybrid persistence", config={})
    assert task.task_id == "task_hybrid_001"

    # Save event with full hybrid telemetry
    event = TaskEvent(
        event_id="evt_001",
        task_id="task_hybrid_001",
        run_id="run_001",
        step=1,
        timestamp="2026-09-21T10:00:00Z",
        state=TaskState.RUNNING,
        phase=EventPhase.ACTION_EXECUTED,
        action="click_item",
        target="Submit Order",
        confidence=0.94,
        router_mode="hybrid",
        selected_mode="browser_dom",
        executed_mode="visual_grounded",
        fallback_from="browser_dom",
        fallback_to="visual_grounded",
        fallback_reason="ElementNotFoundError: not in DOM",
        fallback_count=1,
        side_effect_state="confirmed_success",
        duration_ms=125.0,
        extra={"custom_tag": "phase2c_test"},
    )
    db.save_event(event)

    # Read back events
    events = db.get_events("task_hybrid_001")
    assert len(events) == 1
    loaded = events[0]

    assert loaded.event_id == "evt_001"
    assert loaded.router_mode == "hybrid"
    assert loaded.selected_mode == "browser_dom"
    assert loaded.executed_mode == "visual_grounded"
    assert loaded.fallback_from == "browser_dom"
    assert loaded.fallback_to == "visual_grounded"
    assert loaded.fallback_reason == "ElementNotFoundError: not in DOM"
    assert loaded.fallback_count == 1
    assert loaded.side_effect_state == "confirmed_success"
    assert loaded.extra.get("custom_tag") == "phase2c_test"
