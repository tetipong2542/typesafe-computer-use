"""Worker Service: task lifecycle manager, execution engine, and supervisor."""

from __future__ import annotations

import asyncio
import datetime
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from typesafe_sdk import TypeSafeClient

from .. import config, macos
from ..actions import Context, is_noop, perform
from ..adapters import InteractionMode, InteractionRequest, InteractionResult, SideEffectState, VerificationExpectation
from ..config import DEFAULT_DELAY, DEFAULT_MIN_CONFIDENCE, DEFAULT_STEPS, MAX_OPTIONS
from ..decide import Decision, decide
from ..models import Abort
from ..perception import OcrCache, capture, perceive
from ..report import Log, annotate, ax_count, render_payload
from ..router import ShadowDecision, ShadowInteractionRouter
from ..writer import make_writer
from .db import WorkerDatabase
from .events import EventHub
from .gate import ExecutionGate
from .policy import (
    PolicyDecision,
    PolicyEngine,
    clamp_confidence_to_floor,
    compute_action_fingerprint,
    compute_screenshot_hash,
)
from .state import EventPhase, TaskEvent, TaskRecord, TaskState, can_transition


class TaskController:
    """Thread-safe controller for a running task's control flags."""

    def __init__(self, task_id: str, run_id: str):
        self.task_id = task_id
        self.run_id = run_id
        self._pause_event = threading.Event()
        self._pause_event.set()  # set = running, clear = paused
        self._stop_event = threading.Event()
        self._emergency_stop_event = threading.Event()
        self._takeover_event = threading.Event()
        self._action_lock = threading.Lock()
        self.is_action_in_progress = False
        self.human_approved = False
        self.rebuild_perception_needed = False
        self.current_review_event_id: str | None = None
        self.current_screenshot_hash: str | None = None
        self.current_action_fingerprint: str | None = None
        self.current_step: int = 0
        self.active_approval_id: str | None = None
        self.emergency_stopped: bool = False
        self.process: subprocess.Popen | None = None
        self.exit_code: int | None = None

    def request_pause(self) -> None:
        self._pause_event.clear()

    def request_resume(self) -> None:
        self._pause_event.set()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()

    def request_emergency_stop(self) -> int | None:
        """Emergency stop: locks input immediately, sends SIGTERM, waits, sends SIGKILL if needed."""
        self.emergency_stopped = True
        # 1. Engage hardware/file input lock FIRST
        macos.set_input_lock(True)
        self._emergency_stop_event.set()
        self._stop_event.set()
        self._pause_event.set()

        exit_code = None
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    exit_code = self.process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    exit_code = self.process.wait(timeout=1.0)
            except Exception:
                pass
            self.exit_code = exit_code
        return exit_code

    def request_reset(self) -> None:
        self.emergency_stopped = False
        macos.set_input_lock(False)

    def request_takeover(self) -> None:
        self._takeover_event.set()
        self._pause_event.clear()

    def release_takeover(self) -> None:
        self._takeover_event.clear()
        self.rebuild_perception_needed = True

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    @property
    def is_emergency_stop(self) -> bool:
        return self._emergency_stop_event.is_set()

    @property
    def is_takeover(self) -> bool:
        return self._takeover_event.is_set()


class WorkerService:
    """Coordinates task submission, state transitions, execution, and persistence."""

    def __init__(
        self,
        db: WorkerDatabase,
        event_hub: EventHub,
        policy_engine: PolicyEngine | None = None,
        router: ShadowInteractionRouter | None = None,
        base_dir: Path | None = None,
    ):
        self.db = db
        self.event_hub = event_hub
        self.policy_engine = policy_engine or PolicyEngine()
        self.router = router or ShadowInteractionRouter()
        self.base_dir = Path(base_dir or Path.cwd())
        self._controllers: dict[str, TaskController] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._event_counter = 0

    def _route_and_execute_sync(
        self,
        request: InteractionRequest,
        expectation: VerificationExpectation | None = None,
    ) -> tuple[InteractionResult, ShadowDecision]:
        """Synchronous wrapper for routing and executing actions via ShadowInteractionRouter."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.router.route_and_execute(request, expectation),
                loop,
            )
            return future.result(timeout=15.0)
        return loop.run_until_complete(self.router.route_and_execute(request, expectation))

    def _verify_sync(self, expectation: VerificationExpectation) -> Any | None:
        """Synchronous wrapper for verifying DOM state from worker threads."""
        if not hasattr(self.router, "dom_adapter") or not hasattr(self.router.dom_adapter, "verify"):
            return None
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()

            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self.router.dom_adapter.verify(expectation),
                    loop,
                )
                return future.result(timeout=5.0)
            return loop.run_until_complete(self.router.dom_adapter.verify(expectation))
        except Exception:
            return None

    def next_event_id(self, task_id: str) -> str:
        self._event_counter += 1
        return f"evt_{task_id}_{self._event_counter:06d}"

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.db.get_task(task_id)

    async def emit_event(
        self,
        task_id: str,
        run_id: str,
        step: int,
        phase: EventPhase,
        state: TaskState,
        action: str | None = None,
        target: str | None = None,
        confidence: float | None = None,
        policy_decision: str | None = None,
        screenshot_id: str | None = None,
        result: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TaskEvent:
        extra_dict = extra or {}
        event = TaskEvent(
            event_id=self.next_event_id(task_id),
            task_id=task_id,
            run_id=run_id,
            step=step,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            state=state,
            phase=phase,
            action=action,
            target=target,
            confidence=confidence,
            policy_decision=policy_decision,
            screenshot_id=screenshot_id,
            result=result,
            error=error,
            interaction_mode=extra_dict.get("executed_mode") or "visual_grounded",
            verification_mode=extra_dict.get("verification_mode") or "visual_grounded",
            adapter=extra_dict.get("adapter") or "VisualComputerUseAdapter",
            router_reason=extra_dict.get("router_reason"),
            side_effect_state=extra_dict.get("side_effect_state") or "not_started",
            duration_ms=extra_dict.get("duration_ms"),
            router_mode=extra_dict.get("router_mode") or "legacy",
            executed_mode=extra_dict.get("executed_mode") or "visual_grounded",
            shadow_mode=extra_dict.get("shadow_mode"),
            shadow_target=extra_dict.get("shadow_target"),
            shadow_confidence=extra_dict.get("shadow_confidence"),
            shadow_match_result=extra_dict.get("shadow_match_result"),
            browser_session_id=extra_dict.get("browser_session_id"),
            page_id=extra_dict.get("page_id"),
            navigation_epoch=extra_dict.get("navigation_epoch"),
            extra=extra_dict,
        )
        await self.event_hub.publish(event)
        return event

    def emit_event_sync(self, *args, **kwargs) -> TaskEvent:
        """Synchronous wrapper for emitting events from worker threads."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.emit_event(*args, **kwargs), loop)
            return future.result(timeout=5.0)
        return loop.run_until_complete(self.emit_event(*args, **kwargs))

    def create_task(self, goal: str, config_override: dict[str, Any] | None = None) -> TaskRecord:
        """Create and queue a new background computer use task."""
        now_str = time.strftime("%Y%m%d-%H%M%S")
        task_id = f"task_{now_str}_{uuid.uuid4().hex[:6]}"
        run_id = f"run_{now_str}_{uuid.uuid4().hex[:8]}"

        cfg = {
            "act": True,
            "steps": DEFAULT_STEPS,
            "min_confidence": DEFAULT_MIN_CONFIDENCE,
            "delay": DEFAULT_DELAY,
            "browser": config.browser(),
            **(config_override or {}),
        }
        # Enforce server policy floor on min_confidence
        cfg["min_confidence"] = clamp_confidence_to_floor(float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE)))

        task = self.db.create_task(task_id=task_id, goal=goal, config=cfg, run_id=run_id)
        controller = TaskController(task_id=task_id, run_id=run_id)
        self._controllers[task_id] = controller

        self.emit_event_sync(
            task_id=task_id,
            run_id=run_id,
            step=0,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.QUEUED,
            result="Task queued",
        )

        # Launch execution thread
        t = threading.Thread(
            target=self._run_task_thread,
            args=(task, controller),
            daemon=True,
            name=f"worker-{task_id}",
        )
        self._threads[task_id] = t
        t.start()
        return task

    def pause_task(self, task_id: str) -> bool:
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False

        if not can_transition(task.state, TaskState.PAUSE_REQUESTED):
            return False

        controller.request_pause()
        self.db.update_task(task_id, state=TaskState.PAUSE_REQUESTED)
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.PAUSE_REQUESTED,
            result="Pause requested; will pause at next safe point",
        )
        return True

    def create_approval(
        self,
        task_id: str,
        event_id: str,
        step: int,
        action: str,
        target: str | None,
        screenshot_hash: str,
        action_fingerprint: str,
    ) -> tuple[bool, dict[str, Any] | str]:
        """Validate and grant a one-time approval for a specific event and perception state."""
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False, "Task not found"

        if task.state != TaskState.AWAITING_REVIEW:
            return False, f"Task is not in awaiting_review state (current: {task.state.value})"

        if event_id != controller.current_review_event_id:
            return False, f"event_id mismatch: expected {controller.current_review_event_id}, got {event_id}"

        if step != controller.current_step:
            return False, f"step mismatch: expected {controller.current_step}, got {step}"

        expected_fingerprint = compute_action_fingerprint(action, target, step)
        if action_fingerprint != expected_fingerprint or action_fingerprint != controller.current_action_fingerprint:
            return False, "action_fingerprint mismatch"

        if screenshot_hash != controller.current_screenshot_hash:
            return False, "screenshot_hash mismatch (perception revision has changed)"

        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        record = self.db.create_approval(
            approval_id=approval_id,
            task_id=task_id,
            event_id=event_id,
            step=step,
            action=action,
            target=target,
            screenshot_hash=screenshot_hash,
            action_fingerprint=action_fingerprint,
            ttl_seconds=300.0,
        )
        controller.active_approval_id = approval_id
        controller.human_approved = True
        return True, record

    def resume_task(self, task_id: str) -> tuple[bool, str]:
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False, "Task not found"

        if task.state not in (TaskState.PAUSED, TaskState.AWAITING_REVIEW):
            return False, f"Task cannot be resumed from state {task.state.value}"

        if task.state == TaskState.AWAITING_REVIEW:
            if not controller.active_approval_id:
                return False, "Cannot resume task in awaiting_review without an explicit approval via POST /tasks/:id/approvals/:event_id"
            active = self.db.get_active_approval(task_id, controller.current_review_event_id or "")
            if not active or active["approval_id"] != controller.active_approval_id:
                return False, "No active or valid approval found (it may have expired or already been consumed)"

        controller.request_resume()
        self.db.update_task(task_id, state=TaskState.RUNNING)
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.RUNNING,
            result="Task resumed",
        )
        return True, "Task resumed"

    def reset_task(self, task_id: str) -> tuple[bool, str]:
        """Explicitly reset an emergency-stopped task and release input lock."""
        controller = self._controllers.get(task_id)
        if controller:
            controller.request_reset()
        else:
            macos.set_input_lock(False)
        self.db.update_task(task_id, outcome="Reset")
        ExecutionGate.get_instance().open_gate()
        return True, "Emergency lock reset. Synthetic input unlocked."

    def stop_task(self, task_id: str) -> bool:
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False

        controller.request_stop()
        self.db.update_task(task_id, state=TaskState.STOPPING)
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.STOPPING,
            result="Stop requested; winding down task gracefully",
        )
        return True

    def emergency_stop_task(self, task_id: str) -> bool:
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False

        # Input lock is engaged FIRST inside request_emergency_stop
        exit_code = controller.request_emergency_stop()
        exit_str = f" (exitcode: {exit_code})" if exit_code is not None else ""
        outcome_msg = f"Emergency stop executed{exit_str}"

        # Cancel any in-flight adapter operations via ExecutionGate
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(
                ExecutionGate.get_instance().cancel_all_in_flight(reason="Emergency Stop"),
                loop,
            )
        else:
            loop.run_until_complete(
                ExecutionGate.get_instance().cancel_all_in_flight(reason="Emergency Stop")
            )

        self.db.update_task(task_id, state=TaskState.STOPPED, outcome=outcome_msg, error="Emergency stop requested by user")
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.STOPPED,
            result=f"Emergency stop: input locked and runner terminated immediately{exit_str}",
            error="Emergency stop executed",
        )
        return True

    def create_sse_ticket(self, task_id: str) -> dict[str, Any] | None:
        """Create a short-lived single-use SSE ticket for streaming."""
        task = self.db.get_task(task_id)
        if not task:
            return None
        import secrets
        ticket_id = f"ticket_{secrets.token_urlsafe(24)}"
        return self.db.create_sse_ticket(ticket_id=ticket_id, task_id=task_id, ttl_seconds=60.0)

    def validate_and_consume_sse_ticket(self, ticket_id: str, task_id: str) -> bool:
        """Verify and consume an SSE streaming ticket."""
        return self.db.validate_and_consume_sse_ticket(ticket_id=ticket_id, task_id=task_id)

    def takeover_task(self, task_id: str) -> tuple[bool, str]:
        """Exclusive takeover: pause runner, verify no action in progress, lock synthetic input."""
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False, "Task not found"

        # 1. Request pause and wait for action lock
        controller.request_takeover()
        timeout = 5.0
        start = time.time()
        while controller.is_action_in_progress and time.time() - start < timeout:
            time.sleep(0.05)

        if controller.is_action_in_progress:
            return False, "Failed to takeover: action still in progress"

        # 2. Lock input
        macos.set_input_lock(True)

        # Cancel any in-flight adapter operations via ExecutionGate
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(
                ExecutionGate.get_instance().cancel_all_in_flight(reason="Human Takeover"),
                loop,
            )
        else:
            loop.run_until_complete(
                ExecutionGate.get_instance().cancel_all_in_flight(reason="Human Takeover")
            )

        self.db.update_task(task_id, state=TaskState.TAKEOVER)
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.TAKEOVER,
            result="Exclusive takeover active: synthetic input locked. User has full control.",
        )
        return True, "Takeover granted"

    def release_takeover_task(self, task_id: str) -> bool:
        """Release takeover: unlock input, mark perception rebuild needed, enter paused state."""
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller or task.state != TaskState.TAKEOVER:
            return False

        macos.set_input_lock(False)
        controller.release_takeover()
        self.db.update_task(task_id, state=TaskState.PAUSED)
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.PAUSED,
            result="Takeover released: input unlocked. Perception cache invalidated.",
        )
        return True

    def _run_task_thread(self, task: TaskRecord, controller: TaskController) -> None:
        """Background runner thread coordinating step perception, policy, and execution."""
        task_id = task.task_id
        run_id = task.run_id or f"run_{int(time.time())}"
        out_dir = self.base_dir / "runs" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        log = Log(out_dir / "run.log")
        log(f"Worker task {task_id} (run {run_id}) started for goal: {task.goal!r}")

        self.db.update_task(task_id, state=TaskState.STARTING, current_step=0)
        self.emit_event_sync(
            task_id=task_id,
            run_id=run_id,
            step=0,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.STARTING,
            result="Worker starting",
        )

        writer = make_writer()
        history: list[str] = []
        timings: list[dict[str, float]] = []
        consecutive_noops = 0
        last_url: str | None = None
        ocr_cache = OcrCache()

        cfg = task.config
        max_steps = int(cfg.get("steps", DEFAULT_STEPS))
        delay = float(cfg.get("delay", DEFAULT_DELAY))
        act = bool(cfg.get("act", True))
        browser_name = str(cfg.get("browser", config.browser()))
        user_email = config.email()

        self.db.update_task(task_id, state=TaskState.RUNNING)
        self.emit_event_sync(
            task_id=task_id,
            run_id=run_id,
            step=0,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.RUNNING,
            result="Worker running",
        )

        outcome = "succeeded"
        error_msg: str | None = None

        try:
            with TypeSafeClient() as typesafe:
                ctx = Context(
                    goal=task.goal,
                    browser=browser_name,
                    email=user_email,
                    typesafe=typesafe,
                    writer=writer,
                    history=history,
                )

                for step in range(1, max_steps + 1):
                    # --- Safe Boundary Check (1) ---
                    if not self._check_safe_boundary(task_id, run_id, step, controller, log):
                        outcome = "stopped"
                        break

                    # Check if takeover was released: reset cache and force re-perception
                    if controller.rebuild_perception_needed:
                        ocr_cache = OcrCache()
                        controller.rebuild_perception_needed = False
                        log("Perception cache cleared following takeover release")

                    self.db.update_task(task_id, current_step=step)
                    timing: dict[str, float] = {}
                    step_started = time.perf_counter()

                    # 1. Capture & Perceive
                    screen = capture(browser=ctx.browser, timing=timing)
                    raw_filename = f"step-{step:03d}-raw.png"
                    screen.image.save(out_dir / raw_filename)
                    self.db.update_task(task_id, latest_screenshot_id=raw_filename)

                    items = perceive(screen, MAX_OPTIONS, task.goal, timing, ocr_cache)
                    (out_dir / f"step-{step:03d}-payload.txt").write_text(
                        render_payload(task.goal, screen, items, history, ctx.browser, ctx.email)
                    )

                    # Save preliminary annotated screenshot
                    annotated_filename = f"step-{step:03d}.png"
                    annotate(screen, items, chosen="", out=out_dir / annotated_filename)
                    self.db.update_task(task_id, latest_screenshot_id=annotated_filename)

                    # Multi-point Trace (1): Post-perception
                    self.emit_event_sync(
                        task_id=task_id,
                        run_id=run_id,
                        step=step,
                        phase=EventPhase.STEP_PERCEIVED,
                        state=TaskState.RUNNING,
                        screenshot_id=annotated_filename,
                        extra={"items_count": len(items), "ax_count": ax_count(items), "app": screen.app, "url": screen.url},
                    )

                    # --- Safe Boundary Check (2) ---
                    if not self._check_safe_boundary(task_id, run_id, step, controller, log):
                        outcome = "stopped"
                        break

                    # 2. Decide Next Action
                    decision: Decision = decide(ctx.typesafe, task.goal, screen, items, history, ctx.browser, ctx.email)
                    by_index = {str(it.index): it for it in items}

                    # Re-annotate with chosen action
                    annotate(screen, items, decision.chosen, out_dir / annotated_filename)

                    target_text = by_index[decision.item.choice].text if decision.item and decision.item.choice in by_index else None
                    if decision.pressing_offscreen and decision.offscreen:
                        target_text = screen.offscreen[int(decision.offscreen.choice)].label if int(decision.offscreen.choice) < len(screen.offscreen) else None

                    # 3. Policy Evaluation & Confidence Gate
                    policy_res = self.policy_engine.evaluate(
                        action_kind=decision.kind.choice,
                        confidence=decision.confidence,
                        target_text=target_text,
                        is_human_approved=controller.human_approved,
                    )
                    controller.human_approved = False  # consume one-shot approval

                    # Multi-point Trace (2): Before Action / Evaluating
                    self.emit_event_sync(
                        task_id=task_id,
                        run_id=run_id,
                        step=step,
                        phase=EventPhase.ACTION_EVALUATING,
                        state=TaskState.RUNNING,
                        action=decision.kind.choice,
                        target=target_text,
                        confidence=decision.confidence,
                        policy_decision=policy_res.decision.value,
                        screenshot_id=annotated_filename,
                        result=policy_res.reason,
                    )

                    if policy_res.decision in (PolicyDecision.BLOCKED, PolicyDecision.AWAITING_REVIEW):
                        is_blocked = policy_res.decision == PolicyDecision.BLOCKED
                        decision_label = "BLOCKED" if is_blocked else "AWAITING_REVIEW"
                        log(f"Step {step}: Action {decision_label} by policy: {policy_res.reason}")

                        screenshot_hash = compute_screenshot_hash(screen.image)
                        action_fingerprint = compute_action_fingerprint(decision.kind.choice, target_text, step)

                        self.db.update_task(task_id, state=TaskState.AWAITING_REVIEW, outcome="Policy Blocked" if is_blocked else None)
                        awaiting_event = self.emit_event_sync(
                            task_id=task_id,
                            run_id=run_id,
                            step=step,
                            phase=EventPhase.STATE_CHANGED,
                            state=TaskState.AWAITING_REVIEW,
                            action=decision.kind.choice,
                            target=target_text,
                            confidence=decision.confidence,
                            policy_decision=decision_label,
                            result=policy_res.reason,
                            screenshot_id=annotated_filename,
                            extra={
                                "screenshot_hash": screenshot_hash,
                                "action_fingerprint": action_fingerprint,
                                "step": step,
                            },
                        )

                        controller.current_review_event_id = awaiting_event.event_id
                        controller.current_screenshot_hash = screenshot_hash
                        controller.current_action_fingerprint = action_fingerprint
                        controller.current_step = step
                        controller.active_approval_id = None

                        # Wait in review state
                        controller.request_pause()
                        if not self._wait_for_review_or_resume(controller):
                            outcome = "stopped"
                            break

                        # When resumed: verify screenshot has NOT changed since approval
                        if controller.active_approval_id:
                            fresh_screen = capture(browser=ctx.browser)
                            fresh_hash = compute_screenshot_hash(fresh_screen.image)
                            if fresh_hash != controller.current_screenshot_hash:
                                log(f"Step {step}: Screen changed after approval was granted! Revoking approval and re-perceiving.")
                                self.emit_event_sync(
                                    task_id=task_id,
                                    run_id=run_id,
                                    step=step,
                                    phase=EventPhase.STATE_CHANGED,
                                    state=TaskState.RUNNING,
                                    result="Screen changed after approval; revoking approval and re-perceiving screen",
                                )
                                ocr_cache = OcrCache()
                                controller.active_approval_id = None
                                controller.human_approved = False
                                continue
                            self.db.consume_approval(controller.active_approval_id)
                            controller.active_approval_id = None

                    # Stop conditions from decision model
                    if decision.stops:
                        log(f"Model says {decision.kind.choice!r}; stopping")
                        outcome = "done" if decision.kind.choice == "done" else "nothing helps"
                        break

                    if not act:
                        log(f"Dry run (act=False). Would do {decision.chosen}")
                        outcome = "dry run"
                        break

                    # --- Safe Boundary Check (3) ---
                    if not self._check_safe_boundary(task_id, run_id, step, controller, log):
                        outcome = "stopped"
                        break

                    # 4. Perform Action via ShadowInteractionRouter
                    with controller._action_lock:
                        controller.is_action_in_progress = True
                        try:
                            def do_visual_perform(d=decision, s=screen, it=items, c=ctx):
                                return perform(d, s, it, c)

                            # Inject executor into Visual adapter dependency
                            if hasattr(self.router, "visual_adapter") and hasattr(self.router.visual_adapter, "set_executor"):
                                self.router.visual_adapter.set_executor(do_visual_perform)

                            target_str = target_text or decision.chosen
                            req = InteractionRequest(
                                mode=InteractionMode.VISUAL_GROUNDED,
                                action=decision.kind.choice,
                                target=target_str,
                                arguments={
                                    "confidence": decision.confidence,
                                },
                                execution_id=f"exec_{task_id}_{step}",
                                context={"task_id": task_id, "step": step, "url": screen.url},
                            )
                            res, shadow_dec = self._route_and_execute_sync(req)
                            what = str(res.result)
                        finally:
                            controller.is_action_in_progress = False

                    history.append(what)
                    log(f"Step {step}: did {what}")
                    timing["total"] = round(time.perf_counter() - step_started, 3)
                    timings.append(timing)

                    # Multi-point Trace (3): Post-Action with Shadow Telemetry
                    browser_session_id = getattr(getattr(getattr(self.router, "dom_adapter", None), "session_manager", None), "session_id", "")
                    nav_epoch = getattr(getattr(getattr(self.router, "dom_adapter", None), "session_manager", None), "get_navigation_epoch", lambda: 0)()

                    self.emit_event_sync(
                        task_id=task_id,
                        run_id=run_id,
                        step=step,
                        phase=EventPhase.ACTION_EXECUTED,
                        state=TaskState.RUNNING,
                        action=decision.kind.choice,
                        target=target_text,
                        confidence=decision.confidence,
                        result=what,
                        screenshot_id=annotated_filename,
                        extra={
                            "router_mode": shadow_dec.rollout_mode.value,
                            "executed_mode": shadow_dec.executed_mode.value,
                            "shadow_mode": shadow_dec.shadow_mode.value if shadow_dec.shadow_mode else None,
                            "shadow_target": shadow_dec.shadow_target,
                            "shadow_confidence": shadow_dec.shadow_confidence,
                            "shadow_match_result": shadow_dec.shadow_match_result,
                            "router_reason": shadow_dec.router_reason,
                            "browser_session_id": browser_session_id,
                            "page_id": shadow_dec.metadata.get("page_id", "default"),
                            "navigation_epoch": nav_epoch,
                            "side_effect_state": res.side_effect_state.value,
                            "duration_ms": res.duration_ms,
                            "adapter": res.adapter,
                        },
                    )

                    # Safety Invariant: Check for ambiguous side effects (SideEffectState.UNKNOWN)
                    if res.side_effect_state == SideEffectState.UNKNOWN:
                        log(f"Step {step}: Action side_effect_state is UNKNOWN. Automatic retry and fallback forbidden.")
                        action_name = str(decision.kind.choice).lower()
                        target_name = str(target_text or "").lower()
                        is_critical = any(
                            kw in (action_name + " " + target_name)
                            for kw in ("payment", "send", "publish", "delete", "transfer", "checkout")
                        )
                        verified = False
                        if not is_critical:
                            v_res = self._verify_sync(
                                VerificationExpectation(
                                    condition="element_present",
                                    target=target_text or "",
                                )
                            )
                            verified = v_res.verified if v_res else False

                        if not verified:
                            outcome = "Action side-effect state unknown. Awaiting manual review."
                            log(f"Step {step}: Side effect is UNKNOWN and unverified. Escalating to AWAITING_REVIEW.")
                            self.db.update_task(
                                task_id,
                                state=TaskState.AWAITING_REVIEW,
                                outcome=outcome,
                                error="Ambiguous mutation state: operation was cancelled or timed out during dispatch",
                            )
                            self.emit_event_sync(
                                task_id=task_id,
                                run_id=run_id,
                                step=step,
                                phase=EventPhase.STATE_CHANGED,
                                state=TaskState.AWAITING_REVIEW,
                                action=decision.kind.choice,
                                target=target_text,
                                confidence=decision.confidence,
                                result="Awaiting review: ambiguous side effect state",
                                extra={
                                    "side_effect_state": "unknown",
                                    "reason": "Mutation dispatch unconfirmed; automatic retry and fallback forbidden to prevent duplicate side effects.",
                                },
                            )
                            break

                    # Stall detection
                    repeated = bool(history) and len(history) > 1 and history[-2] == what and screen.url == last_url
                    last_url = screen.url
                    if is_noop(what) or repeated:
                        consecutive_noops += 1
                        if consecutive_noops >= 2:
                            log("Two consecutive no-ops or identical stalls; stopping")
                            outcome = "stalled"
                            break
                    else:
                        consecutive_noops = 0

                    # Sleep delay between steps
                    self._sleep_watching_controller(delay, controller)

                else:
                    outcome = "step limit"

        except Abort as e:
            outcome = f"aborted ({e})"
            error_msg = str(e)
        except Exception as e:
            outcome = "failed"
            error_msg = str(e)
            log(f"Worker task error: {e}")
        finally:
            if not controller.emergency_stopped:
                macos.set_input_lock(False)
            final_state = TaskState.SUCCEEDED if outcome in ("done", "succeeded") else TaskState.STOPPED
            if controller.emergency_stopped:
                final_state = TaskState.STOPPED
            elif outcome == "failed":
                final_state = TaskState.FAILED
            elif outcome == "awaiting_review" or "awaiting manual review" in outcome.lower():
                final_state = TaskState.AWAITING_REVIEW

            self.db.update_task(task_id, state=final_state, outcome=outcome, error=error_msg)
            self.emit_event_sync(
                task_id=task_id,
                run_id=run_id,
                step=task.current_step,
                phase=EventPhase.STATE_CHANGED,
                state=final_state,
                result=f"Task finished with outcome: {outcome}",
                error=error_msg,
            )
            log(f"Task finished. Outcome: {outcome}")

    def _check_safe_boundary(
        self,
        task_id: str,
        run_id: str,
        step: int,
        controller: TaskController,
        log: Log,
    ) -> bool:
        """Check at safe points between step phases. Handles pause, stop, and takeover."""
        if controller.is_emergency_stop:
            log("Emergency stop active at safe boundary")
            return False

        if controller.is_stop_requested:
            log("Stop requested at safe boundary")
            return False

        # If takeover is active, wait until released
        if controller.is_takeover:
            log("Takeover active at safe boundary; waiting for user release...")
            while controller.is_takeover and not controller.is_stop_requested:
                time.sleep(0.1)
            if controller.is_stop_requested:
                return False

        # If pause requested or paused, wait until resumed
        if controller.is_paused:
            self.db.update_task(task_id, state=TaskState.PAUSED)
            self.emit_event_sync(
                task_id=task_id,
                run_id=run_id,
                step=step,
                phase=EventPhase.STATE_CHANGED,
                state=TaskState.PAUSED,
                result="Paused safely at boundary",
            )
            log(f"Task safely paused at step {step}")
            while controller.is_paused and not controller.is_stop_requested:
                time.sleep(0.1)

            if controller.is_stop_requested:
                return False

            self.db.update_task(task_id, state=TaskState.RUNNING)
            self.emit_event_sync(
                task_id=task_id,
                run_id=run_id,
                step=step,
                phase=EventPhase.STATE_CHANGED,
                state=TaskState.RUNNING,
                result="Resuming from safe boundary",
            )

        return True

    def _wait_for_review_or_resume(self, controller: TaskController) -> bool:
        while controller.is_paused and not controller.is_stop_requested:
            time.sleep(0.1)
        return not controller.is_stop_requested

    def _sleep_watching_controller(self, seconds: float, controller: TaskController) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if controller.is_stop_requested or controller.is_paused or controller.is_takeover:
                break
            time.sleep(0.05)
