import pytest

from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.events import EventHub
from typesafe_computer_use.worker.state import (
    EventPhase,
    TaskEvent,
    TaskState,
    can_transition,
)


def test_state_transitions():
    assert can_transition(TaskState.QUEUED, TaskState.STARTING)
    assert can_transition(TaskState.STARTING, TaskState.RUNNING)
    assert can_transition(TaskState.RUNNING, TaskState.PAUSE_REQUESTED)
    assert can_transition(TaskState.PAUSE_REQUESTED, TaskState.PAUSED)
    assert can_transition(TaskState.PAUSED, TaskState.RUNNING)
    assert can_transition(TaskState.PAUSED, TaskState.TAKEOVER)
    assert can_transition(TaskState.TAKEOVER, TaskState.PAUSED)
    assert can_transition(TaskState.RUNNING, TaskState.STOPPING)
    assert can_transition(TaskState.STOPPING, TaskState.STOPPED)

    # Invalid transitions
    assert not can_transition(TaskState.STOPPED, TaskState.RUNNING)
    assert not can_transition(TaskState.SUCCEEDED, TaskState.STARTING)


def test_database_task_and_events(tmp_path):
    db_file = tmp_path / "test_worker.db"
    db = WorkerDatabase(db_file)

    task = db.create_task(task_id="task-001", goal="Test goal", config={"steps": 10}, run_id="run-001")
    assert task.task_id == "task-001"
    assert task.state == TaskState.QUEUED

    # Update state
    db.update_task("task-001", state=TaskState.RUNNING, current_step=1)
    fetched = db.get_task("task-001")
    assert fetched is not None
    assert fetched.state == TaskState.RUNNING
    assert fetched.current_step == 1

    # Save and retrieve events
    event1 = TaskEvent(
        event_id="evt-001",
        task_id="task-001",
        run_id="run-001",
        step=1,
        timestamp="2026-09-20T14:00:00Z",
        state=TaskState.RUNNING,
        phase=EventPhase.STEP_PERCEIVED,
        screenshot_id="step-001.png",
    )
    event2 = TaskEvent(
        event_id="evt-002",
        task_id="task-001",
        run_id="run-001",
        step=1,
        timestamp="2026-09-20T14:00:01Z",
        state=TaskState.RUNNING,
        phase=EventPhase.ACTION_EXECUTED,
        action="click_item",
        result="clicked Button",
    )

    db.save_event(event1)
    db.save_event(event2)

    events = db.get_events("task-001")
    assert len(events) == 2
    assert events[0].event_id == "evt-001"
    assert events[1].event_id == "evt-002"

    # Test last_event_id filtering (reconnect scenario)
    resumed = db.get_events("task-001", last_event_id="evt-001")
    assert len(resumed) == 1
    assert resumed[0].event_id == "evt-002"


def test_crash_recovery_marks_interrupted(tmp_path):
    db_file = tmp_path / "test_worker.db"
    db = WorkerDatabase(db_file)

    # Simulate a task left in RUNNING when worker crashed
    db.create_task("task-abandoned", "Never finished", {}, run_id="run-abandoned")
    db.update_task("task-abandoned", state=TaskState.RUNNING)

    # Another task already SUCCEEDED
    db.create_task("task-completed", "Finished", {}, run_id="run-done")
    db.update_task("task-completed", state=TaskState.SUCCEEDED)

    # On worker startup / restart:
    interrupted = db.recover_interrupted_tasks()
    assert "task-abandoned" in interrupted
    assert "task-completed" not in interrupted

    abandoned = db.get_task("task-abandoned")
    assert abandoned.state == TaskState.INTERRUPTED
    assert "interrupted" in (abandoned.error or "").lower()

    completed = db.get_task("task-completed")
    assert completed.state == TaskState.SUCCEEDED


@pytest.mark.anyio
async def test_event_hub_subscribe_and_replay(tmp_path):
    db_file = tmp_path / "test_hub.db"
    db = WorkerDatabase(db_file)
    hub = EventHub(db)

    # Publish an event
    evt = TaskEvent(
        event_id="evt-101",
        task_id="task-100",
        run_id="run-100",
        step=1,
        timestamp="2026-09-20T14:10:00Z",
        state=TaskState.RUNNING,
        phase=EventPhase.STEP_PERCEIVED,
    )
    await hub.publish(evt)

    # Subscribe and verify historical event is replayed immediately
    stream = hub.subscribe("task-100")
    first_item = await anext(stream)
    assert first_item["id"] == "evt-101"
    assert first_item["event"] == "step_perceived"
    assert "evt-101" in first_item["data"]
    await stream.aclose()
