"""Integration test proving that API-submitted tasks route through ShadowInteractionRouter.

Verifies the full pipeline:
POST /tasks -> WorkerService -> ShadowInteractionRouter -> BrowserDOMAdapter.probe()
-> VisualComputerUseAdapter.execute() -> SQLite persistence -> SSE event emission.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from typesafe_computer_use.adapters import (
    BrowserDOMAdapter,
    CapabilityReport,
    InteractionMode,
    InteractionResult,
    RiskLevel,
    RouterMode,
    SideEffectState,
    VisualComputerUseAdapter,
)
from typesafe_computer_use.router import ShadowInteractionRouter
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.events import EventHub
from typesafe_computer_use.worker.policy import PolicyEngine
from typesafe_computer_use.worker.server import app
from typesafe_computer_use.worker.service import WorkerService


def test_api_task_executes_through_shadow_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Submitting task via POST /tasks under shadow mode must probe DOM and execute visual."""
    monkeypatch.setenv("INTERACTION_ROUTER_MODE", "shadow")
    monkeypatch.setenv("WORKER_AUTH_TOKEN", "test-secret-token")

    test_db = WorkerDatabase(tmp_path / "integration_test.db")
    test_event_hub = EventHub(test_db)
    test_policy = PolicyEngine(normal_threshold=0.80, block_critical=False)

    # Prepare Mock DOM and Visual Adapters to track invocations
    mock_dom = MagicMock(spec=BrowserDOMAdapter)
    mock_dom.mode = InteractionMode.BROWSER_DOM
    mock_dom.probe = AsyncMock(return_value=CapabilityReport(
        mode=InteractionMode.BROWSER_DOM,
        available=True,
        supported_actions=["click", "fill"],
        locators=["#submit-btn"],
    ))
    mock_dom.execute = AsyncMock()
    mock_dom.session_manager = MagicMock()
    mock_dom.session_manager.session_id = "test_browser_sess_123"
    mock_dom.session_manager.get_navigation_epoch.return_value = 1

    mock_visual = MagicMock(spec=VisualComputerUseAdapter)
    mock_visual.mode = InteractionMode.VISUAL_GROUNDED
    mock_visual.execute = AsyncMock(return_value=InteractionResult(
        mode=InteractionMode.VISUAL_GROUNDED,
        adapter="VisualComputerUseAdapter",
        action="click",
        target="#submit-btn",
        arguments={},
        confidence=0.95,
        risk=RiskLevel.NORMAL,
        side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
        duration_ms=45.0,
        result="Clicked visually",
    ))

    router = ShadowInteractionRouter(
        visual_adapter=mock_visual,
        dom_adapter=mock_dom,
        mode=RouterMode.SHADOW,
    )

    test_service = WorkerService(
        db=test_db,
        event_hub=test_event_hub,
        policy_engine=test_policy,
        router=router,
        base_dir=tmp_path,
    )

    # Patch server singletons
    with patch("typesafe_computer_use.worker.server.db", test_db), \
         patch("typesafe_computer_use.worker.server.event_hub", test_event_hub), \
         patch("typesafe_computer_use.worker.server.service", test_service):

        client = TestClient(app)

        # Mock TypeSafe client, capture, perceive, decide to complete 1 step cleanly
        with patch("typesafe_computer_use.worker.service.TypeSafeClient"), \
             patch("typesafe_computer_use.worker.service.capture") as mock_cap, \
             patch("typesafe_computer_use.worker.service.perceive") as mock_perc, \
             patch("typesafe_computer_use.worker.service.decide") as mock_dec, \
             patch("typesafe_computer_use.worker.service.render_payload", return_value="payload"), \
             patch("typesafe_computer_use.worker.service.perform", return_value="clicked"), \
             patch("typesafe_computer_use.worker.service.annotate"):

            mock_screen = MagicMock()
            mock_screen.image = MagicMock()
            mock_screen.app = "Google Chrome"
            mock_screen.url = "https://shop.example.com"
            mock_cap.return_value = mock_screen
            mock_perc.return_value = []

            # 1 Step: click on #submit-btn, then stop
            mock_decision = MagicMock()
            mock_decision.stops = False
            mock_decision.chosen = "#submit-btn"
            mock_decision.kind.choice = "click"
            mock_decision.confidence = 0.95
            mock_decision.item = None
            mock_decision.pressing_offscreen = False

            mock_dec.side_effect = [
                mock_decision,
                MagicMock(stops=True, kind=MagicMock(choice="done")),
            ]

            headers = {"Authorization": "Bearer test-secret-token"}
            resp = client.post("/tasks", json={"goal": "Order test", "steps": 1, "delay": 0.01}, headers=headers)
            assert resp.status_code == 200
            task_id = resp.json()["task_id"]

            # Wait for background runner thread to execute step 1 and finish
            deadline = time.time() + 5.0
            task_done = False
            while time.time() < deadline:
                t = test_db.get_task(task_id)
                if t and t.state.value in ("succeeded", "stopped", "failed"):
                    task_done = True
                    break
                time.sleep(0.05)

            assert task_done is True, "Task did not complete within timeout"

            # 1. Verify Router called DOM probe
            assert mock_dom.probe.await_count >= 1, "DOM probe was not called!"

            # 2. Verify Router did NOT execute DOM
            assert mock_dom.execute.await_count == 0, "DOM execute was mistakenly called in shadow mode!"

            # 3. Verify Visual Adapter executed
            assert mock_visual.execute.await_count >= 1, "Visual adapter was not executed!"

            # 4. Verify SQLite events contain extended shadow telemetry
            events = test_db.get_events(task_id)
            exec_events = [e for e in events if getattr(e.phase, "value", str(e.phase)) == "action_executed"]
            assert len(exec_events) >= 1, "No action_executed event found in SQLite"

            evt = exec_events[0]
            assert evt.interaction_mode == "visual_grounded"
            assert evt.adapter == "VisualComputerUseAdapter"
            assert "Shadow mode:" in (evt.router_reason or "")

            extra = evt.extra or {}
            assert extra.get("router_mode") == "shadow"
            assert extra.get("executed_mode") == "visual_grounded"
            assert extra.get("shadow_mode") == "browser_dom"
            assert extra.get("shadow_target") == "#submit-btn"
            assert extra.get("shadow_confidence") == 0.95
            assert extra.get("shadow_match_result") == "match"
            assert extra.get("browser_session_id") == "test_browser_sess_123"
            assert extra.get("navigation_epoch") == 1

            # 5. Verify SSE Stream delivers shadow telemetry
            token_resp = client.post(f"/tasks/{task_id}/events/token", headers=headers)
            assert token_resp.status_code == 200
            ticket = token_resp.json()["ticket"]

            import json
            async def finite_subscribe(tid, last_event_id=None):
                for ev in test_db.get_events(tid):
                    yield {"id": ev.event_id, "event": ev.phase.value, "data": json.dumps(ev.to_dict())}

            with patch.object(test_event_hub, "subscribe", side_effect=finite_subscribe):
                sse_res = client.get(f"/tasks/{task_id}/events?ticket={ticket}")
                assert sse_res.status_code == 200
                assert "shadow_mode" in sse_res.text
                assert "executed_mode" in sse_res.text
                assert "browser_dom" in sse_res.text
                assert "visual_grounded" in sse_res.text
