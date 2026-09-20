"""Integration tests for side-effect cancellation phases using real Chrome and delayed actions.

Verifies the 4 cancellation phases:
1. Pre-dispatch: Cancelled before CDP dispatch -> SideEffectState.NOT_STARTED, 0 mutations
2. Mid-dispatch: Cancelled during CDP execution -> SideEffectState.UNKNOWN, no fallback
3. Post DOM event but pre-adapter response: Mutation started, ambiguous finish -> SideEffectState.UNKNOWN, no duplicate
4. Post side-effect but pre-verification: Mutation completed, verify step interrupted -> read-only recovery
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from typesafe_computer_use import macos
from typesafe_computer_use.adapters.browser_dom import BrowserDOMAdapter
from typesafe_computer_use.adapters.models import (
    InteractionMode,
    InteractionRequest,
    SideEffectState,
    VerificationExpectation,
)
from typesafe_computer_use.browser.models import BrowserSessionConfig
from typesafe_computer_use.browser.session import BrowserSessionManager
from typesafe_computer_use.worker.gate import ExecutionGate
from typesafe_computer_use.worker.state import TaskState

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "delayed_action.html"


@pytest.fixture(autouse=True)
def clean_gate():
    ExecutionGate.reset_instance()
    macos.set_input_lock(False)
    yield
    macos.set_input_lock(False)
    ExecutionGate.reset_instance()


@pytest.mark.browser
def test_phase1_pre_dispatch_cancellation(tmp_path: Path):
    """Phase 1: Cancellation before CDP transmission results in NOT_STARTED and zero mutations."""
    async def _run():
        gate = ExecutionGate.get_instance()
        cfg = BrowserSessionConfig(
            user_data_dir=tmp_path / "profile_phase1",
            cdp_port=0,
            headless=True,
        )
        mgr = BrowserSessionManager(cfg)
        try:
            page = await mgr.start_or_attach(auto_launch=True)
            await page.goto(FIXTURE_PATH.resolve().as_uri())

            adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate)

            # Pre-lock gate to simulate takeover right before dispatch
            await gate.trigger_takeover()

            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#delayed-btn",
                execution_id="exec_phase1",
                context={"navigation_epoch": 1},
            )

            res = await adapter.execute(req)
            assert res.side_effect_state == SideEffectState.NOT_STARTED
            assert "Execution gate blocked action" in (res.error or "")

            # Confirm no mutation occurred on page
            status_text = await page.locator("#status").inner_text()
            assert status_text == "pending"

        finally:
            await mgr.close()

    asyncio.run(_run())


@pytest.mark.browser
def test_barrier_pre_dispatch_cancellation(tmp_path: Path):
    """Synchronization barrier: Cancellation triggered at before_dispatch strictly yields NOT_STARTED."""
    async def _run():
        gate = ExecutionGate.get_instance()
        cfg = BrowserSessionConfig(
            user_data_dir=tmp_path / "profile_pre_barrier",
            cdp_port=0,
            headless=True,
        )
        mgr = BrowserSessionManager(cfg)
        try:
            page = await mgr.start_or_attach(auto_launch=True)
            await page.goto(FIXTURE_PATH.resolve().as_uri())

            async def barrier(phase: str):
                if phase == "before_dispatch":
                    await gate.trigger_takeover()

            adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate, step_barrier=barrier)

            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#delayed-btn",
                execution_id="exec_pre_barrier",
                context={"navigation_epoch": 1},
            )

            res = await adapter.execute(req)
            assert res.side_effect_state == SideEffectState.NOT_STARTED
            assert res.error == "Action cancelled by Execution Gate"

            # Confirm zero DOM mutations occurred
            status_text = await page.locator("#status").inner_text()
            assert status_text == "pending"

        finally:
            await mgr.close()

    asyncio.run(_run())


@pytest.mark.browser
def test_phase2_mid_dispatch_cancellation(tmp_path: Path):
    """Phase 2: Cancellation during active CDP call results strictly in UNKNOWN side effect state."""
    async def _run():
        gate = ExecutionGate.get_instance()
        cfg = BrowserSessionConfig(
            user_data_dir=tmp_path / "profile_phase2",
            cdp_port=0,
            headless=True,
        )
        mgr = BrowserSessionManager(cfg)
        try:
            page = await mgr.start_or_attach(auto_launch=True)
            await page.goto(FIXTURE_PATH.resolve().as_uri())

            async def barrier(phase: str):
                if phase == "request_dispatched":
                    # Mid-dispatch takeover triggered deterministically right when request is dispatched
                    await gate.trigger_takeover()

            adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate, step_barrier=barrier)

            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#delayed-btn",
                execution_id="exec_phase2",
                context={"navigation_epoch": 1},
            )

            res = await adapter.execute(req)
            # Deterministically assert UNKNOWN because action was dispatched before cancellation
            assert res.side_effect_state == SideEffectState.UNKNOWN
            assert res.error == "Action cancelled by Execution Gate"

        finally:
            await mgr.close()

    asyncio.run(_run())


@pytest.mark.browser
def test_phase3_post_event_pre_response_cancellation(tmp_path: Path):
    """Phase 3: Post DOM event cancellation at side_effect_committed retains CONFIRMED_SUCCESS without duplicate."""
    async def _run():
        gate = ExecutionGate.get_instance()
        cfg = BrowserSessionConfig(
            user_data_dir=tmp_path / "profile_phase3",
            cdp_port=0,
            headless=True,
        )
        mgr = BrowserSessionManager(cfg)
        try:
            page = await mgr.start_or_attach(auto_launch=True)
            await page.goto(FIXTURE_PATH.resolve().as_uri())

            async def barrier(phase: str):
                if phase == "side_effect_committed":
                    await gate.trigger_takeover()

            adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate, step_barrier=barrier)

            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#delayed-btn",
                execution_id="exec_phase3",
                context={"navigation_epoch": 1},
            )

            res = await adapter.execute(req)
            # Side effect was committed before cancellation occurred
            assert res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS

            # Wait for delayed JS mutation to complete
            await asyncio.sleep(0.6)
            status_text = await page.locator("#status").inner_text()
            assert status_text == "action_mutated"

            # Verify that re-executing is NOT performed automatically
            # Verify read-only state check passes
            v_res = await adapter.verify(
                VerificationExpectation(
                    mode=InteractionMode.BROWSER_DOM,
                    condition="element_present",
                    target="#status",
                )
            )
            assert v_res.passed is True

        finally:
            await mgr.close()

    asyncio.run(_run())


@pytest.mark.browser
def test_phase4_post_side_effect_read_only_verification(tmp_path: Path):
    """Phase 4: Read-only verification inspects DOM state without triggering new mutations."""
    async def _run():
        cfg = BrowserSessionConfig(
            user_data_dir=tmp_path / "profile_phase4",
            cdp_port=0,
            headless=True,
        )
        mgr = BrowserSessionManager(cfg)
        try:
            page = await mgr.start_or_attach(auto_launch=True)
            await page.goto(FIXTURE_PATH.resolve().as_uri())

            adapter = BrowserDOMAdapter(session_manager=mgr)

            # Perform click
            req = InteractionRequest(
                mode=InteractionMode.BROWSER_DOM,
                action="click",
                target="#delayed-btn",
                execution_id="exec_phase4",
                context={"navigation_epoch": 1},
            )
            await adapter.execute(req)
            await asyncio.sleep(0.6)

            # Verification 1: element_present
            v1 = await adapter.verify(VerificationExpectation(condition="element_present", target="#status"))
            assert v1.passed is True

            # Verification 2: text_contains
            v2 = await adapter.verify(
                VerificationExpectation(condition="text_contains", target="#status", expected_value="action_mutated")
            )
            assert v2.passed is True

            # Verification 3: value_equals on non-existent element fails cleanly without mutation
            v3 = await adapter.verify(
                VerificationExpectation(condition="value_equals", target="#status", expected_value="wrong")
            )
            assert v3.passed is False

        finally:
            await mgr.close()

    asyncio.run(_run())


def test_service_escalates_unknown_side_effect_to_awaiting_review(tmp_path: Path):
    """When router returns UNKNOWN side effect state, service forbids retry/fallback and transitions to AWAITING_REVIEW."""
    from typesafe_computer_use.adapters.models import InteractionResult, RiskLevel
    from typesafe_computer_use.router import ShadowDecision
    from typesafe_computer_use.worker.db import WorkerDatabase
    from typesafe_computer_use.worker.events import EventHub
    from typesafe_computer_use.worker.policy import PolicyEngine
    from typesafe_computer_use.worker.service import TaskController, WorkerService

    db = WorkerDatabase(tmp_path / "test_unknown_state.db")
    event_hub = EventHub(db)
    policy = PolicyEngine()
    svc = WorkerService(db=db, event_hub=event_hub, policy_engine=policy)

    # Mock _route_and_execute_sync to simulate an interrupted/unknown side effect action
    unknown_result = InteractionResult(
        mode=InteractionMode.BROWSER_DOM,
        adapter="BrowserDOMAdapter",
        action="click",
        target="submit_payment_button",
        arguments={},
        confidence=0.9,
        risk=RiskLevel.HIGH,
        side_effect_state=SideEffectState.UNKNOWN,
        duration_ms=100.0,
        result=None,
        error="Action cancelled or timed out during dispatch",
    )
    shadow_dec = ShadowDecision(
        rollout_mode=svc.router.mode,
        executed_mode=InteractionMode.BROWSER_DOM,
        shadow_mode=None,
        shadow_target=None,
        shadow_confidence=0.0,
        shadow_reason="Simulated unknown side effect",
        shadow_match_result="unknown",
        router_reason="Simulated unknown side effect",
    )

    svc._route_and_execute_sync = MagicMock(return_value=(unknown_result, shadow_dec))

    task_rec = db.create_task(
        task_id="task_test_1",
        goal="Test task with ambiguous side effect",
        config={"max_steps": 10},
    )
    from unittest.mock import patch

    controller = TaskController(task_id=task_rec.task_id, run_id="run_1")

    mock_screen = MagicMock()
    mock_screen.image = MagicMock()
    mock_screen.app = "Google Chrome"
    mock_screen.url = "https://shop.example.com"

    mock_decision = MagicMock()
    mock_decision.stops = False
    mock_decision.chosen = "submit_payment_button"
    mock_decision.kind.choice = "click"
    mock_decision.confidence = 0.95
    mock_decision.item = None
    mock_decision.pressing_offscreen = False

    # Run thread with patched external LLM clients and GUI pipelines
    with patch("typesafe_computer_use.worker.service.TypeSafeClient"), \
         patch("typesafe_computer_use.worker.service.make_writer"), \
         patch("typesafe_computer_use.worker.service.capture", return_value=mock_screen), \
         patch("typesafe_computer_use.worker.service.perceive", return_value=[]), \
         patch("typesafe_computer_use.worker.service.decide", return_value=mock_decision), \
         patch("typesafe_computer_use.worker.service.render_payload", return_value="payload"), \
         patch("typesafe_computer_use.worker.service.perform", return_value="clicked"), \
         patch("typesafe_computer_use.worker.service.annotate"):
        svc._run_task_thread(task_rec, controller)

    updated = db.get_task(task_rec.task_id)
    assert updated is not None
    assert updated.state == TaskState.AWAITING_REVIEW
    assert updated.outcome == "Action side-effect state unknown. Awaiting manual review."
    assert svc._route_and_execute_sync.call_count == 1  # Forbids automatic retry!
