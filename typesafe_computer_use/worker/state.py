"""Task states, lifecycle events, and transition rules for background workers."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class TaskState(enum.StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    AWAITING_REVIEW = "awaiting_review"
    TAKEOVER = "takeover"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


TERMINAL_STATES = {
    TaskState.STOPPED,
    TaskState.SUCCEEDED,
    TaskState.FAILED,
    TaskState.INTERRUPTED,
}

ACTIVE_STATES = {
    TaskState.STARTING,
    TaskState.RUNNING,
    TaskState.PAUSE_REQUESTED,
    TaskState.PAUSED,
    TaskState.AWAITING_REVIEW,
    TaskState.TAKEOVER,
    TaskState.STOPPING,
}


class EventPhase(enum.StrEnum):
    STEP_PERCEIVED = "step_perceived"
    ACTION_EVALUATING = "action_evaluating"
    ACTION_EXECUTED = "action_executed"
    STATE_CHANGED = "state_changed"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {
        TaskState.STARTING,
        TaskState.PAUSE_REQUESTED,
        TaskState.PAUSED,
        TaskState.STOPPING,
        TaskState.STOPPED,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.STARTING: {
        TaskState.RUNNING,
        TaskState.PAUSE_REQUESTED,
        TaskState.PAUSED,
        TaskState.STOPPING,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.RUNNING: {
        TaskState.PAUSE_REQUESTED,
        TaskState.AWAITING_REVIEW,
        TaskState.STOPPING,
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.PAUSE_REQUESTED: {
        TaskState.PAUSED,
        TaskState.STOPPING,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.PAUSED: {
        TaskState.RUNNING,
        TaskState.TAKEOVER,
        TaskState.STOPPING,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.AWAITING_REVIEW: {
        TaskState.RUNNING,
        TaskState.PAUSED,
        TaskState.TAKEOVER,
        TaskState.STOPPING,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.TAKEOVER: {
        TaskState.PAUSED,
        TaskState.RUNNING,
        TaskState.STOPPING,
        TaskState.FAILED,
        TaskState.INTERRUPTED,
    },
    TaskState.STOPPING: {TaskState.STOPPED, TaskState.FAILED, TaskState.INTERRUPTED},
    TaskState.STOPPED: set(),
    TaskState.SUCCEEDED: set(),
    TaskState.FAILED: set(),
    TaskState.INTERRUPTED: set(),
}


def can_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """Check if state transition from_state -> to_state is valid."""
    if from_state == to_state:
        return True
    return to_state in VALID_TRANSITIONS.get(from_state, set())


@dataclass
class TaskEvent:
    event_id: str
    task_id: str
    run_id: str
    step: int
    timestamp: str
    state: TaskState
    phase: EventPhase
    action: str | None = None
    target: str | None = None
    confidence: float | None = None
    policy_decision: str | None = None
    screenshot_id: str | None = None
    result: str | None = None
    error: str | None = None
    # Extended Hybrid Interaction & Shadow Telemetry fields
    interaction_mode: str = "visual_grounded"
    verification_mode: str = "visual_grounded"
    adapter: str = "VisualComputerUseAdapter"
    capability_snapshot_id: str | None = None
    router_reason: str | None = None
    attempted_modes: list[str] = field(default_factory=list)
    fallback_from: str | None = None
    fallback_to: str | None = None
    fallback_reason: str | None = None
    tool_name: str | None = None
    origin: str | None = None
    side_effect_state: str = "not_started"
    duration_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    router_mode: str = "legacy"
    executed_mode: str = "visual_grounded"
    shadow_mode: str | None = None
    shadow_target: str | None = None
    shadow_confidence: float | None = None
    shadow_match_result: str | None = None
    browser_session_id: str | None = None
    page_id: str | None = None
    navigation_epoch: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value if isinstance(self.state, TaskState) else str(self.state)
        data["phase"] = self.phase.value if isinstance(self.phase, EventPhase) else str(self.phase)
        return data


@dataclass
class TaskRecord:
    task_id: str
    goal: str
    state: TaskState
    created_at: float
    updated_at: float
    run_id: str | None = None
    current_step: int = 0
    outcome: str | None = None
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    latest_screenshot_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "state": self.state.value if isinstance(self.state, TaskState) else str(self.state),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_id": self.run_id,
            "current_step": self.current_step,
            "outcome": self.outcome,
            "error": self.error,
            "config": self.config,
            "latest_screenshot_id": self.latest_screenshot_id,
        }
