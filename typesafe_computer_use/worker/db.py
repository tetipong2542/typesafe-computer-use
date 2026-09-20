"""SQLite persistence layer for tasks and events, including startup recovery."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .state import ACTIVE_STATES, EventPhase, TaskEvent, TaskRecord, TaskState

DEFAULT_DB_PATH = Path("worker.db")


class WorkerDatabase:
    """Thread-safe SQLite manager for tasks, events, and recovery."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);")
            conn.commit()

    def recover_interrupted_tasks(self) -> list[str]:
        """Mark any active tasks as interrupted upon worker start/restart.
        
        Guarantees that no interrupted GUI actions are automatically replayed.
        """
        active_state_values = [s.value for s in ACTIVE_STATES]
        placeholders = ",".join("?" for _ in active_state_values)
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT task_id FROM tasks WHERE state IN ({placeholders})",
                active_state_values,
            )
            interrupted_ids = [row["task_id"] for row in cursor.fetchall()]

            if interrupted_ids:
                id_placeholders = ",".join("?" for _ in interrupted_ids)
                conn.execute(
                    f"""
                    UPDATE tasks
                    SET state = ?, updated_at = ?, outcome = ?, error = ?
                    WHERE task_id IN ({id_placeholders})
                    """,
                    [TaskState.INTERRUPTED.value, now, "Worker restart/crash detected", "Task marked interrupted; GUI actions not replayed", *interrupted_ids],
                )
                conn.commit()

        return interrupted_ids

    def create_task(self, task_id: str, goal: str, config: dict[str, Any], run_id: str | None = None) -> TaskRecord:
        now = time.time()
        record = TaskRecord(
            task_id=task_id,
            goal=goal,
            state=TaskState.QUEUED,
            created_at=now,
            updated_at=now,
            run_id=run_id,
            config=config,
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (task_id, goal, state, created_at, updated_at, run_id, config_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, goal, record.state.value, now, now, run_id, json.dumps(config)),
            )
            conn.commit()
        return record

    def update_task(
        self,
        task_id: str,
        state: TaskState | None = None,
        run_id: str | None = None,
        current_step: int | None = None,
        outcome: str | None = None,
        error: str | None = None,
        latest_screenshot_id: str | None = None,
    ) -> None:
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [time.time()]

        if state is not None:
            updates.append("state = ?")
            params.append(state.value if isinstance(state, TaskState) else str(state))
        if run_id is not None:
            updates.append("run_id = ?")
            params.append(run_id)
        if current_step is not None:
            updates.append("current_step = ?")
            params.append(current_step)
        if outcome is not None:
            updates.append("outcome = ?")
            params.append(outcome)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if latest_screenshot_id is not None:
            updates.append("latest_screenshot_id = ?")
            params.append(latest_screenshot_id)

        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?"

        with self._get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                return None
            return TaskRecord(
                task_id=row["task_id"],
                goal=row["goal"],
                state=TaskState(row["state"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                run_id=row["run_id"],
                current_step=row["current_step"],
                outcome=row["outcome"],
                error=row["error"],
                config=json.loads(row["config_json"] or "{}"),
                latest_screenshot_id=row["latest_screenshot_id"],
            )

    def save_event(self, event: TaskEvent) -> int:
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (
                    event_id, task_id, run_id, step, phase, timestamp, state,
                    action, target, confidence, policy_decision, screenshot_id,
                    result, error, extra_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.run_id,
                    event.step,
                    event.phase.value if isinstance(event.phase, EventPhase) else str(event.phase),
                    event.timestamp,
                    event.state.value if isinstance(event.state, TaskState) else str(event.state),
                    event.action,
                    event.target,
                    event.confidence,
                    event.policy_decision,
                    event.screenshot_id,
                    event.result,
                    event.error,
                    json.dumps(event.extra),
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_events(self, task_id: str, last_event_id: str | None = None) -> list[TaskEvent]:
        with self._get_connection() as conn:
            if last_event_id:
                # Find the row id of the last_event_id
                row = conn.execute("SELECT id FROM events WHERE event_id = ?", (last_event_id,)).fetchone()
                after_id = row["id"] if row else 0
                cursor = conn.execute(
                    "SELECT * FROM events WHERE task_id = ? AND id > ? ORDER BY id ASC",
                    (task_id, after_id),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM events WHERE task_id = ? ORDER BY id ASC",
                    (task_id,),
                )

            events: list[TaskEvent] = []
            for r in cursor.fetchall():
                events.append(
                    TaskEvent(
                        event_id=r["event_id"],
                        task_id=r["task_id"],
                        run_id=r["run_id"],
                        step=r["step"],
                        timestamp=r["timestamp"],
                        state=TaskState(r["state"]),
                        phase=EventPhase(r["phase"]),
                        action=r["action"],
                        target=r["target"],
                        confidence=r["confidence"],
                        policy_decision=r["policy_decision"],
                        screenshot_id=r["screenshot_id"],
                        result=r["result"],
                        error=r["error"],
                        extra=json.loads(r["extra_json"] or "{}"),
                    )
                )
            return events
