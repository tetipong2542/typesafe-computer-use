"""FastAPI HTTP and SSE Worker Server."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .db import DEFAULT_DB_PATH, WorkerDatabase
from .events import EventHub
from .policy import DEFAULT_NORMAL_CONFIDENCE_THRESHOLD, PolicyEngine
from .service import WorkerService

# Environment configurations
WORKER_AUTH_TOKEN = os.environ.get("WORKER_AUTH_TOKEN", "typesafe-worker-secret-token")
TART_VM_IP = os.environ.get("TART_VM_IP", "127.0.0.1")
VNC_PORT = os.environ.get("VNC_PORT", "5900")
VNC_USER = os.environ.get("VNC_USER", "admin")
VNC_PASSWORD = os.environ.get("VNC_PASSWORD", "admin")

db = WorkerDatabase(DEFAULT_DB_PATH)
event_hub = EventHub(db)
policy_engine = PolicyEngine(normal_threshold=DEFAULT_NORMAL_CONFIDENCE_THRESHOLD, block_critical=True)
service = WorkerService(db=db, event_hub=event_hub, policy_engine=policy_engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Recover any interrupted tasks from previous worker crashes or restarts."""
    interrupted = db.recover_interrupted_tasks()
    if interrupted:
        print(f"[Worker Startup] Recovered {len(interrupted)} interrupted task(s): {', '.join(interrupted)}")
    yield


app = FastAPI(
    title="typesafe-computer-use Worker API",
    description="Background Computer Worker API for isolated macOS VM environments.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_auth_token(
    authorization: str | None = Header(default=None),
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
) -> None:
    """Verify authorization header against configured WORKER_AUTH_TOKEN."""
    if not WORKER_AUTH_TOKEN:
        return

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif x_worker_token:
        token = x_worker_token.strip()

    if token != WORKER_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )


# Request schemas
class CreateTaskRequest(BaseModel):
    goal: str = Field(..., description="Goal for the computer use worker")
    act: bool = Field(True, description="Whether to execute real synthetic inputs")
    steps: int = Field(50, description="Maximum number of steps before stopping")
    min_confidence: float = Field(DEFAULT_NORMAL_CONFIDENCE_THRESHOLD, description="Confidence threshold")
    delay: float = Field(2.0, description="Delay in seconds after each action")
    browser: str | None = Field(None, description="Target browser (defaults to Google Chrome)")


class ResumeTaskRequest(BaseModel):
    approve: bool = Field(False, description="Approve sensitive or low-confidence action if paused in awaiting_review")


# ------------------------------------------------------------------ Endpoints


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "typesafe-computer-worker"}


@app.post("/tasks", dependencies=[Depends(verify_auth_token)])
def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    """Submit and start a new background task."""
    config_override: dict[str, Any] = {
        "act": req.act,
        "steps": req.steps,
        "min_confidence": req.min_confidence,
        "delay": req.delay,
    }
    if req.browser:
        config_override["browser"] = req.browser

    task = service.create_task(goal=req.goal, config_override=config_override)
    return {"task_id": task.task_id, "run_id": task.run_id, "state": task.state.value, "goal": task.goal}


@app.get("/tasks/{task_id}", dependencies=[Depends(verify_auth_token)])
def get_task(task_id: str) -> dict[str, Any]:
    """Get current status and metadata of a task."""
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@app.post("/tasks/{task_id}/pause", dependencies=[Depends(verify_auth_token)])
def pause_task(task_id: str) -> dict[str, Any]:
    """Request a task to pause at the next safe boundary."""
    ok = service.pause_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to pause task (task not found or in invalid state)")
    return {"status": "pause_requested", "task_id": task_id}


@app.post("/tasks/{task_id}/resume", dependencies=[Depends(verify_auth_token)])
def resume_task(task_id: str, body: ResumeTaskRequest | None = None) -> dict[str, Any]:
    """Resume a paused or awaiting_review task."""
    approve = body.approve if body else False
    ok = service.resume_task(task_id, approve=approve)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to resume task (task not in paused or awaiting_review state)")
    return {"status": "resumed", "task_id": task_id}


@app.post("/tasks/{task_id}/stop", dependencies=[Depends(verify_auth_token)])
def stop_task(task_id: str) -> dict[str, Any]:
    """Request graceful cancellation of a task."""
    ok = service.stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to stop task")
    return {"status": "stopping", "task_id": task_id}


@app.post("/tasks/{task_id}/emergency-stop", dependencies=[Depends(verify_auth_token)])
def emergency_stop_task(task_id: str) -> dict[str, Any]:
    """Emergency stop: locks synthetic input immediately and terminates runner."""
    ok = service.emergency_stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to emergency stop task")
    return {"status": "emergency_stopped", "task_id": task_id, "input_locked": True}


@app.post("/tasks/{task_id}/takeover", dependencies=[Depends(verify_auth_token)])
def takeover_task(task_id: str) -> dict[str, Any]:
    """Acquire exclusive interactive control: pauses agent and locks synthetic inputs."""
    ok, msg = service.takeover_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    vnc_uri = f"vnc://{VNC_USER}:{VNC_PASSWORD}@{TART_VM_IP}:{VNC_PORT}"
    return {
        "status": "takeover",
        "task_id": task_id,
        "input_locked": True,
        "vnc_uri": vnc_uri,
        "instructions": f"Open Screen Sharing on host: open {vnc_uri}",
    }


@app.post("/tasks/{task_id}/release-takeover", dependencies=[Depends(verify_auth_token)])
def release_takeover_task(task_id: str) -> dict[str, Any]:
    """Release exclusive interactive control: unlocks inputs and resets perception cache."""
    ok = service.release_takeover_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to release takeover")
    return {
        "status": "takeover_released",
        "task_id": task_id,
        "next_state": "paused",
        "perception_cache_cleared": True,
        "instructions": "Call /resume when ready for agent to re-perceive screen and continue",
    }


@app.get("/tasks/{task_id}/events")
async def get_task_events(
    task_id: str,
    request: Request,
    last_event_id: str | None = Query(None, alias="last_event_id"),
) -> EventSourceResponse:
    """Stream realtime task events via Server-Sent Events (SSE). Supports Last-Event-ID reconnect."""
    # Check Last-Event-ID header if not provided in query param
    header_last_id = request.headers.get("Last-Event-ID")
    effective_last_id = last_event_id or header_last_id

    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return EventSourceResponse(
        event_hub.subscribe(task_id, last_event_id=effective_last_id)
    )


@app.get("/tasks/{task_id}/screenshot", dependencies=[Depends(verify_auth_token)])
def get_task_screenshot(
    task_id: str,
    type: str = Query("latest", pattern="^(latest|annotated|raw)$"),
) -> FileResponse:
    """Get the current or latest screenshot of the worker."""
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    run_dir = Path.cwd() / "runs" / (task.run_id or "")
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run directory not found")

    # Find matching screenshot
    target_file: Path | None = None
    if task.latest_screenshot_id:
        target_file = run_dir / task.latest_screenshot_id

    if not target_file or not target_file.exists():
        # Fallback to finding latest png in run dir
        pngs = sorted(run_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pngs:
            target_file = pngs[0]

    if not target_file or not target_file.exists():
        raise HTTPException(status_code=404, detail="No screenshot available yet")

    return FileResponse(target_file, media_type="image/png")


def run_server() -> None:
    """CLI script runner for clicker-worker."""
    import uvicorn

    host = os.environ.get("WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("WORKER_PORT", "8000"))
    print(f"Starting typesafe-computer-worker on http://{host}:{port}")
    uvicorn.run("typesafe_computer_use.worker.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
