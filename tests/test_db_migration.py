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


def test_v1_legacy_schema_migration_to_v3(tmp_path: Path):
    """Explicitly verify that a v1 pre-Phase 2A database (matching commit d8f5235) migrates cleanly to v3."""
    import sqlite3
    import time

    db_path = tmp_path / "legacy_v1.db"

    # 1. Manually construct exact v1 SQLite schema matching commit d8f5235
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA user_version = 1;")
        conn.execute("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                run_id TEXT,
                current_step INTEGER DEFAULT 0,
                outcome TEXT,
                error TEXT,
                config_json TEXT,
                latest_screenshot_id TEXT
            );
        """)
        # Exact 17 columns in commit d8f5235
        conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                task_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                phase TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                state TEXT NOT NULL,
                action TEXT,
                target TEXT,
                confidence REAL,
                policy_decision TEXT,
                screenshot_id TEXT,
                result TEXT,
                error TEXT,
                extra_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );
        """)
        conn.execute("""
            CREATE TABLE approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT UNIQUE NOT NULL,
                task_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                screenshot_hash TEXT NOT NULL,
                action_fingerprint TEXT NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                created_at REAL NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );
        """)
        conn.execute("""
            CREATE TABLE sse_tickets (
                ticket_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                created_at REAL NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );
        """)

        # Insert a pre-existing task and event into v1 DB
        now = time.time()
        conn.execute(
            "INSERT INTO tasks (task_id, goal, state, created_at, updated_at, run_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("task_pre_2a", "Old goal before Phase 2A", "succeeded", now, now, "run_old_1"),
        )
        conn.execute(
            """
            INSERT INTO events (
                event_id, task_id, run_id, step, phase, timestamp, state, action, target,
                confidence, policy_decision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_v1_001",
                "task_pre_2a",
                "run_old_1",
                1,
                "action_executed",
                "2026-09-19T10:00:00Z",
                "running",
                "click",
                "Submit Button",
                0.90,
                "ALLOWED",
                now,
            ),
        )
        conn.commit()

        # Verify exactly 17 columns in v1 events table
        v1_cols = [row[1] for row in conn.execute("PRAGMA table_info(events);").fetchall()]
        assert len(v1_cols) == 17

    # 2. Open with WorkerDatabase -> must trigger migration
    db = WorkerDatabase(db_path=db_path)

    # 3. Verify user_version upgraded to current schema version (4)
    assert db.get_schema_version() == 4

    # 4. Verify all 44 columns exist (17 v1 + 16 Phase 2A + 9 Phase 2B + 2 Phase 2D)
    with db._get_connection() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(events);").fetchall()}
        assert len(cols) == 44

    # 5. Verify pre-existing v1 event is retrieved and backwards-compatible
    events = db.get_events("task_pre_2a")
    assert len(events) == 1
    v1_evt = events[0]
    assert v1_evt.event_id == "evt_v1_001"
    assert v1_evt.action == "click"
    assert v1_evt.target == "Submit Button"
    assert v1_evt.router_mode == "legacy"
    assert v1_evt.executed_mode == "visual_grounded"
    assert v1_evt.side_effect_state == "not_started"

    # 6. Save a new Phase 2B event into migrated database
    new_evt = TaskEvent(
        event_id="evt_v3_002",
        task_id="task_pre_2a",
        run_id="run_old_1",
        step=2,
        phase=EventPhase.ACTION_EXECUTED,
        timestamp="2026-09-20T12:00:00Z",
        state=TaskState.RUNNING,
        action="click",
        target="#nav-link",
        confidence=0.98,
        router_mode="shadow",
        executed_mode="visual_grounded",
        shadow_mode="browser_dom",
        shadow_target="#nav-link",
        shadow_confidence=0.95,
        shadow_match_result="match",
        browser_session_id="sess_v3_test",
        page_id="page_v3_1",
        navigation_epoch=3,
    )
    db.save_event(new_evt)

    events_after = db.get_events("task_pre_2a")
    assert len(events_after) == 2
    assert events_after[1].event_id == "evt_v3_002"
    assert events_after[1].router_mode == "shadow"
    assert events_after[1].shadow_match_result == "match"

