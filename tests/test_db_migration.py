"""Tests verifying SQLite schema migration idempotency and shadow telemetry column completeness."""

from __future__ import annotations

import tempfile
from pathlib import Path

from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.state import EventPhase, TaskEvent, TaskState


def test_events_table_contains_all_shadow_columns():
    """Verify the events table in SQLite contains all 9 required shadow telemetry columns."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_migration.db"
        db = WorkerDatabase(db_path=db_path)

        with db._get_connection() as conn:
            cols = {col["name"] for col in conn.execute("PRAGMA table_info(events);").fetchall()}

        required_shadow_cols = {
            "router_mode",
            "executed_mode",
            "shadow_mode",
            "shadow_target",
            "shadow_confidence",
            "shadow_match_result",
            "browser_session_id",
            "page_id",
            "navigation_epoch",
        }

        missing = required_shadow_cols - cols
        assert not missing, f"Missing required shadow columns in events table: {missing}"


def test_db_migration_idempotent():
    """Verify multiple re-initializations of WorkerDatabase do not fail and preserve data."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_idempotent.db"

        # 1. First initialization
        db1 = WorkerDatabase(db_path=db_path)

        db1.create_task(task_id="task_mig_1", goal="Test DB Migration Idempotency", config={})


        event = TaskEvent(
            event_id="evt_mig_1",
            task_id="task_mig_1",
            run_id="run_1",
            step=1,
            phase=EventPhase.ACTION_EXECUTED,
            timestamp="2026-09-20T12:00:00Z",
            state=TaskState.RUNNING,
            action="click",
            target="#login-btn",
            confidence=0.95,
            router_mode="shadow",
            executed_mode="visual_grounded",
            shadow_mode="browser_dom",
            shadow_target="#login-btn",
            shadow_confidence=0.95,
            shadow_match_result="exact_match",
            browser_session_id="sess_mig_test",
            page_id="page_tab_1",
            navigation_epoch=2,
            extra={"note": "migration test event"},
        )
        db1.save_event(event)

        # 2. Re-initialize on existing DB (Second time)
        _ = WorkerDatabase(db_path=db_path)

        # 3. Re-initialize on existing DB (Third time)
        db3 = WorkerDatabase(db_path=db_path)

        # 4. Verify data preserved intact
        loaded_task = db3.get_task("task_mig_1")
        assert loaded_task is not None
        assert loaded_task.goal == "Test DB Migration Idempotency"

        events = db3.get_events("task_mig_1")
        assert len(events) == 1
        e = events[0]
        assert e.event_id == "evt_mig_1"
        assert e.router_mode == "shadow"
        assert e.executed_mode == "visual_grounded"
        assert e.shadow_mode == "browser_dom"
        assert e.shadow_target == "#login-btn"
        assert e.shadow_confidence == 0.95
        assert e.shadow_match_result == "exact_match"
        assert e.browser_session_id == "sess_mig_test"
        assert e.page_id == "page_tab_1"
        assert e.navigation_epoch == 2
