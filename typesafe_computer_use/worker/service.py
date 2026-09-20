"""Worker Service: task lifecycle manager, execution engine, and supervisor."""

from __future__ import annotations

import asyncio
import datetime
import os
import threading
import time
from pathlib import Path
from typing import Any

from typesafe_sdk import TypeSafeClient

from .. import config, macos
from ..actions import Context, is_noop, perform
from ..config import DEFAULT_DELAY, DEFAULT_MIN_CONFIDENCE, DEFAULT_STEPS, MAX_OPTIONS
from ..decide import Decision, decide
from ..models import Abort
from ..perception import OcrCache, capture, perceive
from ..report import Log, annotate, ax_count, render_payload
from ..writer import make_writer
from .db import WorkerDatabase
from .events import EventHub
from .policy import PolicyDecision, PolicyEngine
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

    def request_pause(self) -> None:
        self._pause_event.clear()

    def request_resume(self, approve: bool = False) -> None:
        if approve:
            self.human_approved = True
        self._pause_event.set()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()

    def request_emergency_stop(self) -> None:
        self._emergency_stop_event.set()
        self._stop_event.set()
        self._pause_event.set()
        macos.set_input_lock(True)

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
        base_dir: Path | None = None,
    ):
        self.db = db
        self.event_hub = event_hub
        self.policy_engine = policy_engine or PolicyEngine()
        self.base_dir = Path(base_dir or Path.cwd())
        self._controllers: dict[str, TaskController] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._event_counter = 0

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
            extra=extra or {},
        )
        await self.event_hub.publish(event)
        return event

    def emit_event_sync(self, *args, **kwargs) -> TaskEvent:
        """Synchronous wrapper for emitting events from worker threads."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.emit_event(*args, **kwargs), loop)
            return future.result(timeout=5.0)
        return loop.run_until_complete(self.emit_event(*args, **kwargs))

    def create_task(self, goal: str, config_override: dict[str, Any] | None = None) -> TaskRecord:
        """Create and queue a new background computer use task."""
        now_str = time.strftime("%Y%m%d-%H%M%S")
        task_id = f"task_{now_str}_{os.urandom(3).hex()}"
        run_id = f"run_{now_str}"

        cfg = {
            "act": True,
            "steps": DEFAULT_STEPS,
            "min_confidence": DEFAULT_MIN_CONFIDENCE,
            "delay": DEFAULT_DELAY,
            "browser": config.browser(),
            **(config_override or {}),
        }

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

    def resume_task(self, task_id: str, approve: bool = False) -> bool:
        task = self.db.get_task(task_id)
        controller = self._controllers.get(task_id)
        if not task or not controller:
            return False

        if task.state not in (TaskState.PAUSED, TaskState.AWAITING_REVIEW):
            return False

        controller.request_resume(approve=approve)
        self.db.update_task(task_id, state=TaskState.RUNNING)
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.RUNNING,
            result="Task resumed",
        )
        return True

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

        controller.request_emergency_stop()
        self.db.update_task(task_id, state=TaskState.STOPPED, outcome="Emergency stop executed", error="Emergency stop requested by user")
        self.emit_event_sync(
            task_id=task_id,
            run_id=task.run_id or "",
            step=task.current_step,
            phase=EventPhase.STATE_CHANGED,
            state=TaskState.STOPPED,
            result="Emergency stop: input locked and task stopped immediately",
            error="Emergency stop executed",
        )
        return True

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

                    if policy_res.decision == PolicyDecision.BLOCKED:
                        log(f"Step {step}: Action BLOCKED by policy: {policy_res.reason}")
                        self.db.update_task(task_id, state=TaskState.AWAITING_REVIEW, outcome="Policy Blocked")
                        self.emit_event_sync(
                            task_id=task_id,
                            run_id=run_id,
                            step=step,
                            phase=EventPhase.STATE_CHANGED,
                            state=TaskState.AWAITING_REVIEW,
                            action=decision.kind.choice,
                            target=target_text,
                            policy_decision="BLOCKED",
                            error=policy_res.reason,
                        )
                        # Wait in review state
                        controller.request_pause()
                        if not self._wait_for_review_or_resume(controller):
                            outcome = "stopped"
                            break

                    elif policy_res.decision == PolicyDecision.AWAITING_REVIEW:
                        log(f"Step {step}: Low confidence or sensitive action awaiting review: {policy_res.reason}")
                        self.db.update_task(task_id, state=TaskState.AWAITING_REVIEW)
                        self.emit_event_sync(
                            task_id=task_id,
                            run_id=run_id,
                            step=step,
                            phase=EventPhase.STATE_CHANGED,
                            state=TaskState.AWAITING_REVIEW,
                            action=decision.kind.choice,
                            target=target_text,
                            confidence=decision.confidence,
                            policy_decision="AWAITING_REVIEW",
                            result=policy_res.reason,
                        )
                        controller.request_pause()
                        if not self._wait_for_review_or_resume(controller):
                            outcome = "stopped"
                            break

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

                    # 4. Perform Action
                    with controller._action_lock:
                        controller.is_action_in_progress = True
                        try:
                            what = perform(decision, screen, items, ctx)
                        finally:
                            controller.is_action_in_progress = False

                    history.append(what)
                    log(f"Step {step}: did {what}")
                    timing["total"] = round(time.perf_counter() - step_started, 3)
                    timings.append(timing)

                    # Multi-point Trace (3): Post-Action
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
                    )

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
            macos.set_input_lock(False)
            final_state = TaskState.SUCCEEDED if outcome in ("done", "succeeded") else TaskState.STOPPED
            if outcome == "failed":
                final_state = TaskState.FAILED

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
