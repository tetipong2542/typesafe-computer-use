"""Unit tests for Interaction Adapters, Base Contracts, and Extended Event Schema."""

import os
from unittest.mock import patch

from typesafe_computer_use.adapters import (
    CapabilityReport,
    InteractionAdapter,
    InteractionMode,
    InteractionRequest,
    InteractionResult,
    RiskLevel,
    SideEffectState,
    VerificationExpectation,
    VisualComputerUseAdapter,
    is_interaction_router_enabled,
)
from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.state import EventPhase, TaskEvent, TaskState


def test_feature_flag_default():
    # By default, router is disabled
    with patch.dict(os.environ, {}, clear=True):
        assert is_interaction_router_enabled() is False

    with patch.dict(os.environ, {"INTERACTION_ROUTER_ENABLED": "true"}):
        assert is_interaction_router_enabled() is True

    with patch.dict(os.environ, {"INTERACTION_ROUTER_ENABLED": "1"}):
        assert is_interaction_router_enabled() is True

    with patch.dict(os.environ, {"INTERACTION_ROUTER_ENABLED": "false"}):
        assert is_interaction_router_enabled() is False


def test_adapter_models():
    # Enums
    assert InteractionMode.WEBMCP == "webmcp"
    assert InteractionMode.BROWSER_DOM == "browser_dom"
    assert InteractionMode.VISUAL_GROUNDED == "visual_grounded"

    assert SideEffectState.NOT_STARTED == "not_started"
    assert SideEffectState.CONFIRMED_SUCCESS == "confirmed_success"
    assert SideEffectState.UNKNOWN == "unknown"

    # Dataclasses instantiation
    report = CapabilityReport(
        mode=InteractionMode.WEBMCP,
        available=True,
        supported_actions=["tool_1"],
        tools=[{"name": "tool_1"}],
    )
    assert report.available is True
    assert len(report.tools) == 1

    req = InteractionRequest(
        mode=InteractionMode.BROWSER_DOM,
        action="click",
        target="#submit-btn",
        arguments={"selector": "#submit-btn"},
    )
    assert req.action == "click"

    res = InteractionResult(
        mode=InteractionMode.BROWSER_DOM,
        adapter="BrowserDOMAdapter",
        action="click",
        target="#submit-btn",
        arguments={},
        confidence=0.95,
        risk=RiskLevel.LOW,
        side_effect_state=SideEffectState.CONFIRMED_SUCCESS,
        duration_ms=45.2,
        result={"clicked": True},
    )
    assert res.side_effect_state == SideEffectState.CONFIRMED_SUCCESS


def test_visual_computer_use_adapter(tmp_path):
    import asyncio

    adapter = VisualComputerUseAdapter()
    assert adapter.mode == InteractionMode.VISUAL_GROUNDED
    assert isinstance(adapter, InteractionAdapter)

    # 1. Probe
    report = asyncio.run(adapter.probe({}))
    assert report.mode == InteractionMode.VISUAL_GROUNDED
    assert report.available is True
    assert "click_item" in report.supported_actions

    # 2. Execute normal
    req = InteractionRequest(
        mode=InteractionMode.VISUAL_GROUNDED,
        action="click_item",
        target="Login Button",
        arguments={"confidence": 0.92},
        execution_id="exec_01",
    )
    result = asyncio.run(adapter.execute(req))
    assert result.mode == InteractionMode.VISUAL_GROUNDED
    assert result.adapter == "VisualComputerUseAdapter"
    assert result.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    assert result.confidence == 0.92

    # 3. Cancelled execution
    asyncio.run(adapter.cancel("exec_cancel"))
    req_cancelled = InteractionRequest(
        mode=InteractionMode.VISUAL_GROUNDED,
        action="click_item",
        target="Cancel Button",
        execution_id="exec_cancel",
    )
    res_cancel = asyncio.run(adapter.execute(req_cancelled))
    assert res_cancel.side_effect_state == SideEffectState.NOT_STARTED
    assert "cancelled" in (res_cancel.error or "").lower()

    # 4. Verify
    verify_exp = VerificationExpectation(
        mode=InteractionMode.VISUAL_GROUNDED,
        condition="element_visible",
        target="Dashboard",
    )
    with patch("typesafe_computer_use.macos.screenshot") as mock_screen:
        from PIL import Image
        mock_screen.return_value = Image.new("RGB", (100, 100))
        v_res = asyncio.run(adapter.verify(verify_exp))
        assert v_res.verified is True


def test_extended_event_schema_persistence(tmp_path):
    db = WorkerDatabase(tmp_path / "extended_events.db")
    task_id = "task_ext_01"
    run_id = "run_ext_01"
    db.create_task(task_id, goal="Test Extended Schema", config={}, run_id=run_id)

    event = TaskEvent(
        event_id="evt_ext_001",
        task_id=task_id,
        run_id=run_id,
        step=3,
        timestamp="2026-09-20T14:50:00Z",
        state=TaskState.RUNNING,
        phase=EventPhase.ACTION_EXECUTED,
        action="filter_products",
        target="Category Dropdown",
        confidence=0.98,
        policy_decision="ALLOWED",
        # Extended Fields
        interaction_mode=InteractionMode.WEBMCP.value,
        verification_mode=InteractionMode.BROWSER_DOM.value,
        adapter="WebMCPAdapter",
        capability_snapshot_id="snap_123",
        router_reason="Structured tool covers filter_products intent",
        attempted_modes=["webmcp"],
        fallback_from=None,
        fallback_to=None,
        fallback_reason=None,
        tool_name="filter_products",
        origin="https://shop.example.com",
        side_effect_state=SideEffectState.CONFIRMED_SUCCESS.value,
        duration_ms=124.5,
        input_tokens=150,
        output_tokens=45,
        estimated_cost=0.0012,
        extra={"custom_attr": "value"},
    )

    db.save_event(event)

    events = db.get_events(task_id)
    assert len(events) == 1
    loaded = events[0]

    assert loaded.event_id == "evt_ext_001"
    assert loaded.interaction_mode == "webmcp"
    assert loaded.verification_mode == "browser_dom"
    assert loaded.adapter == "WebMCPAdapter"
    assert loaded.capability_snapshot_id == "snap_123"
    assert loaded.router_reason == "Structured tool covers filter_products intent"
    assert loaded.attempted_modes == ["webmcp"]
    assert loaded.tool_name == "filter_products"
    assert loaded.origin == "https://shop.example.com"
    assert loaded.side_effect_state == "confirmed_success"
    assert loaded.duration_ms == 124.5
    assert loaded.input_tokens == 150
    assert loaded.output_tokens == 45
    assert loaded.estimated_cost == 0.0012
    assert loaded.extra == {"custom_attr": "value"}
