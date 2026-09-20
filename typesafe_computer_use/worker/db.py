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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approvals (
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
                CREATE TABLE IF NOT EXISTS sse_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id, event_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sse_tickets_task ON sse_tickets(task_id, ticket_id);")

            # Migration for extended event columns
            existing_cols = {col["name"] for col in conn.execute("PRAGMA table_info(events);").fetchall()}
            new_cols = [
                ("interaction_mode", "TEXT DEFAULT 'visual_grounded'"),
                ("verification_mode", "TEXT DEFAULT 'visual_grounded'"),
                ("adapter", "TEXT DEFAULT 'VisualComputerUseAdapter'"),
                ("capability_snapshot_id", "TEXT"),
                ("router_reason", "TEXT"),
                ("attempted_modes", "TEXT"),
                ("fallback_from", "TEXT"),
                ("fallback_to", "TEXT"),
                ("fallback_reason", "TEXT"),
                ("tool_name", "TEXT"),
                ("origin", "TEXT"),
                ("side_effect_state", "TEXT DEFAULT 'not_started'"),
                ("duration_ms", "REAL"),
                ("input_tokens", "INTEGER DEFAULT 0"),
                ("output_tokens", "INTEGER DEFAULT 0"),
                ("estimated_cost", "REAL DEFAULT 0.0"),
                ("router_mode", "TEXT DEFAULT 'legacy'"),
                ("executed_mode", "TEXT DEFAULT 'visual_grounded'"),
                ("shadow_mode", "TEXT"),
                ("shadow_target", "TEXT"),
                ("shadow_confidence", "REAL"),
                ("shadow_match_result", "TEXT"),
                ("browser_session_id", "TEXT"),
                ("page_id", "TEXT"),
                ("navigation_epoch", "INTEGER"),
            ]
            for col_name, col_type in new_cols:
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE events ADD COLUMN {col_name} {col_type};")

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
                    result, error, extra_json, created_at,
                    interaction_mode, verification_mode, adapter, capability_snapshot_id,
                    router_reason, attempted_modes, fallback_from, fallback_to,
                    fallback_reason, tool_name, origin, side_effect_state,
                    duration_ms, input_tokens, output_tokens, estimated_cost,
                    router_mode, executed_mode, shadow_mode, shadow_target,
                    shadow_confidence, shadow_match_result, browser_session_id, page_id, navigation_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    event.interaction_mode,
                    event.verification_mode,
                    event.adapter,
                    event.capability_snapshot_id,
                    event.router_reason,
                    json.dumps(event.attempted_modes),
                    event.fallback_from,
                    event.fallback_to,
                    event.fallback_reason,
                    event.tool_name,
                    event.origin,
                    event.side_effect_state,
                    event.duration_ms,
                    event.input_tokens,
                    event.output_tokens,
                    event.estimated_cost,
                    event.router_mode,
                    event.executed_mode,
                    event.shadow_mode,
                    event.shadow_target,
                    event.shadow_confidence,
                    event.shadow_match_result,
                    event.browser_session_id,
                    event.page_id,
                    event.navigation_epoch,
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
            for raw_r in cursor.fetchall():
                r = dict(raw_r)
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
                        interaction_mode=r.get("interaction_mode") or "visual_grounded",
                        verification_mode=r.get("verification_mode") or "visual_grounded",
                        adapter=r.get("adapter") or "VisualComputerUseAdapter",
                        capability_snapshot_id=r.get("capability_snapshot_id"),
                        router_reason=r.get("router_reason"),
                        attempted_modes=json.loads(r.get("attempted_modes") or "[]"),
                        fallback_from=r.get("fallback_from"),
                        fallback_to=r.get("fallback_to"),
                        fallback_reason=r.get("fallback_reason"),
                        tool_name=r.get("tool_name"),
                        origin=r.get("origin"),
                        side_effect_state=r.get("side_effect_state") or "not_started",
                        duration_ms=r.get("duration_ms"),
                        input_tokens=r.get("input_tokens") or 0,
                        output_tokens=r.get("output_tokens") or 0,
                        estimated_cost=r.get("estimated_cost") or 0.0,
                        router_mode=r.get("router_mode") or "legacy",
                        executed_mode=r.get("executed_mode") or "visual_grounded",
                        shadow_mode=r.get("shadow_mode"),
                        shadow_target=r.get("shadow_target"),
                        shadow_confidence=r.get("shadow_confidence"),
                        shadow_match_result=r.get("shadow_match_result"),
                        browser_session_id=r.get("browser_session_id"),
                        page_id=r.get("page_id"),
                        navigation_epoch=r.get("navigation_epoch"),
                        extra=json.loads(r.get("extra_json") or "{}"),
                    )
                )
            return events

    def create_approval(
        self,
        approval_id: str,
        task_id: str,
        event_id: str,
        step: int,
        action: str,
        target: str | None,
        screenshot_hash: str,
        action_fingerprint: str,
        ttl_seconds: float = 300.0,
    ) -> dict[str, Any]:
        now = time.time()
        expires_at = now + ttl_seconds
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, task_id, event_id, step, action, target,
                    screenshot_hash, action_fingerprint, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    event_id,
                    step,
                    action,
                    target,
                    screenshot_hash,
                    action_fingerprint,
                    expires_at,
                    now,
                ),
            )
            conn.commit()
        return {
            "approval_id": approval_id,
            "task_id": task_id,
            "event_id": event_id,
            "step": step,
            "action": action,
            "target": target,
            "screenshot_hash": screenshot_hash,
            "action_fingerprint": action_fingerprint,
            "expires_at": expires_at,
        }

    def get_active_approval(self, task_id: str, event_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM approvals
                WHERE task_id = ? AND event_id = ? AND consumed_at IS NULL AND expires_at > ?
                ORDER BY id DESC LIMIT 1
                """,
                (task_id, event_id, now),
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def consume_approval(self, approval_id: str) -> bool:
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE approvals SET consumed_at = ? WHERE approval_id = ? AND consumed_at IS NULL",
                (now, approval_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def check_and_restore_input_lock(self) -> bool:
        """Check if any task was emergency_stopped (un-reset) or in takeover before reboot.
        
        If so, re-engage the hardware/file input lock before accepting any new tasks.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT task_id, state, outcome FROM tasks 
                WHERE state = 'takeover' 
                   OR (state = 'stopped' AND outcome LIKE '%Emergency stop%')
                ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()
            if row:
                from .. import macos
                macos.set_input_lock(True)
                return True
        return False

    def create_sse_ticket(self, ticket_id: str, task_id: str, ttl_seconds: float = 60.0) -> dict[str, Any]:
        """Generate a short-lived single-use ticket for SSE streaming."""
        now = time.time()
        expires_at = now + ttl_seconds
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sse_tickets (ticket_id, task_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (ticket_id, task_id, expires_at, now),
            )
            conn.commit()
        return {
            "ticket": ticket_id,
            "task_id": task_id,
            "expires_at": expires_at,
            "expires_in": int(ttl_seconds),
        }

    def validate_and_consume_sse_ticket(self, ticket_id: str, task_id: str) -> bool:
        """Validate that an SSE ticket is valid for this task and mark it consumed."""
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE sse_tickets 
                SET consumed_at = ? 
                WHERE ticket_id = ? AND task_id = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (now, ticket_id, task_id, now),
            )
            conn.commit()
            return cursor.rowcount > 0

