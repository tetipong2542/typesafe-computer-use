import logging
import os
import secrets
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .db import DEFAULT_DB_PATH, WorkerDatabase
from .events import EventHub
from .gate import ExecutionGate
from .policy import DEFAULT_NORMAL_CONFIDENCE_THRESHOLD, PolicyEngine
from .service import WorkerService


def get_auth_token() -> str:
    """Retrieve WORKER_AUTH_TOKEN from environment or generate a secure random token."""
    token = os.environ.get("WORKER_AUTH_TOKEN")
    if not token:
        token = secrets.token_hex(32)
        os.environ["WORKER_AUTH_TOKEN"] = token
        print(f"[Worker Security] WORKER_AUTH_TOKEN not set. Generated secure random token: {token}")
    return token


WORKER_AUTH_TOKEN = get_auth_token()


def get_vm_ip() -> str:
    """Resolve the guest VM IP address for VNC Screen Sharing."""
    if env_ip := os.environ.get("TART_VM_IP"):
        return env_ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


db = WorkerDatabase(DEFAULT_DB_PATH)
event_hub = EventHub(db)
policy_engine = PolicyEngine(normal_threshold=DEFAULT_NORMAL_CONFIDENCE_THRESHOLD, block_critical=True)
service = WorkerService(db=db, event_hub=event_hub, policy_engine=policy_engine)


logger = logging.getLogger("typesafe.worker")

ALLOWED_MCP_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_MCP_ORIGINS",
        "http://localhost:*,http://127.0.0.1:*,https://localhost:*,https://127.0.0.1:*",
    ).split(",")
    if o.strip()
]

def _is_mcp_origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    for pattern in ALLOWED_MCP_ORIGINS:
        if pattern == "*":
            return True
        if pattern.endswith(":*"):
            prefix = pattern[:-2]
            if origin == prefix or origin.startswith(prefix + ":"):
                return True
        elif origin == pattern:
            return True
    return False


_mcp_rate_limit_records: dict[str, list[float]] = {}


class MCPAuthAndSecurityMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing Origin validation, Bearer token authentication, rate limiting, and audit logging on /mcp routes."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            # 1. Validate Origin
            origin = request.headers.get("origin")
            if not _is_mcp_origin_allowed(origin):
                logger.warning("AUDIT MCP: Blocked disallowed origin '%s' from %s", origin, request.client.host if request.client else "unknown")
                return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"error": "OriginForbidden", "detail": f"Origin '{origin}' is not allowed"})

            # 2. Universal Authentication Check
            current_token = os.environ.get("WORKER_AUTH_TOKEN", WORKER_AUTH_TOKEN)
            if current_token:
                auth_hdr = request.headers.get("authorization", "")
                x_tok = request.headers.get("x-worker-token", "")
                token = None
                if auth_hdr.startswith("Bearer "):
                    token = auth_hdr[7:].strip()
                elif x_tok:
                    token = x_tok.strip()

                if not token or token != current_token:
                    logger.warning("AUDIT MCP: Unauthorized request to %s from %s", request.url.path, request.client.host if request.client else "unknown")
                    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": "Unauthorized", "detail": "Missing or invalid authorization token"})

            # 3. Rate Limiting (120 req/min)
            client_ip = request.client.host if request.client else "127.0.0.1"
            now = time.time()
            ts_list = [t for t in _mcp_rate_limit_records.get(client_ip, []) if now - t < 60.0]
            if len(ts_list) >= 120:
                logger.warning("AUDIT MCP: Rate limit exceeded for %s", client_ip)
                return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={"error": "RateLimitExceeded", "detail": "Too many requests to MCP endpoint"})
            ts_list.append(now)
            _mcp_rate_limit_records[client_ip] = ts_list

            # 4. Audit Log Execution
            t0 = time.time()
            response = await call_next(request)
            dur = (time.time() - t0) * 1000
            logger.info("AUDIT MCP: method=%s path=%s client=%s status=%s duration_ms=%.1f", request.method, request.url.path, client_ip, response.status_code, dur)
            return response

        return await call_next(request)


# Prepare MCP Gateway if enabled
_app_stream = None
_app_sse = None
_mcp_lifespan_ctx = None

if os.environ.get("WEBMCP_ENABLED", "true").lower() in ("true", "1", "yes"):
    from typesafe_computer_use.mcp import create_webmcp_server

    _dom_adapter = getattr(service.router, "dom_adapter", None)
    _session_mgr = getattr(_dom_adapter, "session_manager", None)
    mcp_server = create_webmcp_server(
        session_manager=_session_mgr,
        dom_adapter=_dom_adapter,
        execution_gate=ExecutionGate.get_instance(),
    )
    _sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    _app_stream = mcp_server.streamable_http_app(streamable_http_path="/", stateless_http=True, transport_security=_sec)
    _app_sse = mcp_server.sse_app(sse_path="/", message_path="/messages/", transport_security=_sec)
    _mcp_lifespan_ctx = _app_stream.router.lifespan_context


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle startup: restore input lock if needed and recover interrupted tasks."""
    # 1. Check if input lock needs to be restored via ExecutionGate
    gate = ExecutionGate.get_instance()
    locked = gate.rehydrate_on_boot(db)
    if locked:
        print("[Worker Startup] Restored synthetic input lock from previous emergency stop / takeover session.")

    # 2. Recover any interrupted active tasks
    interrupted = db.recover_interrupted_tasks()
    if interrupted:
        print(f"[Worker Startup] Recovered {len(interrupted)} interrupted task(s): {', '.join(interrupted)}")

    if _mcp_lifespan_ctx:
        async with _mcp_lifespan_ctx(app):
            yield
    else:
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
app.add_middleware(MCPAuthAndSecurityMiddleware)

if _app_stream is not None:
    # Legacy SSE is disabled by default; enabled only via explicit feature flag
    if os.environ.get("ENABLE_LEGACY_MCP_SSE", "false").lower() in ("true", "1", "yes"):
        app.mount("/mcp/legacy/sse", _app_sse)
        app.mount("/mcp/sse", _app_sse)
    app.mount("/mcp", _app_stream)



def verify_auth_token(
    authorization: str | None = Header(default=None),
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
) -> None:
    """Verify authorization header against configured WORKER_AUTH_TOKEN."""
    current_token = os.environ.get("WORKER_AUTH_TOKEN", WORKER_AUTH_TOKEN)
    if not current_token:
        return

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif x_worker_token:
        token = x_worker_token.strip()

    if not token or token != current_token:
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


class CreateApprovalRequest(BaseModel):
    step: int = Field(..., description="Step number awaiting approval")
    action: str = Field(..., description="Action name, e.g. click")
    target: str = Field(..., description="Action target description or text")
    screenshot_hash: str = Field(..., description="Hash of screenshot when action was gated")
    action_fingerprint: str = Field(..., description="Fingerprint of action to execute")


class CreateWebMCPApprovalRequest(BaseModel):
    event_id: str = Field(..., description="Event or step ID requesting approval")
    origin: str = Field(..., description="Target origin")
    tool_name: str = Field(..., description="WebMCP tool name")
    schema_hash: str = Field(..., description="Schema hash")
    arguments_hash: str = Field(..., description="Deterministic arguments hash")
    navigation_epoch: int = Field(0, description="Navigation epoch")
    ttl_seconds: float = Field(300.0, description="Approval validity in seconds")
    single_use: bool = Field(True, description="Whether approval expires upon single use")
    approved_by: str = Field("operator", description="Principal granting approval")



# ------------------------------------------------------------------ Endpoints (14 Total)


# 1. Health Probe
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "typesafe-computer-worker"}


# 2. Submit Task
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


# 3. Get Task Status
@app.get("/tasks/{task_id}", dependencies=[Depends(verify_auth_token)])
def get_task(task_id: str) -> dict[str, Any]:
    """Get current status and metadata of a task."""
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


# 4. Pause Task
@app.post("/tasks/{task_id}/pause", dependencies=[Depends(verify_auth_token)])
def pause_task(task_id: str) -> dict[str, Any]:
    """Request a task to pause at the next safe boundary."""
    ok = service.pause_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to pause task (task not found or in invalid state)")
    return {"status": "pause_requested", "task_id": task_id}


# 5. Resume Task
@app.post("/tasks/{task_id}/resume", dependencies=[Depends(verify_auth_token)])
def resume_task(task_id: str) -> dict[str, Any]:
    """Resume a paused task (if in awaiting_review, strictly requires prior explicit approval)."""
    ok, msg = service.resume_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "resumed", "task_id": task_id, "message": msg}


# 6. Stop Task (Graceful)
@app.post("/tasks/{task_id}/stop", dependencies=[Depends(verify_auth_token)])
def stop_task(task_id: str) -> dict[str, Any]:
    """Request graceful cancellation of a task."""
    ok = service.stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to stop task")
    return {"status": "stopping", "task_id": task_id}


# 7. Emergency Stop (Hard input lock + terminate)
@app.post("/tasks/{task_id}/emergency-stop", dependencies=[Depends(verify_auth_token)])
def emergency_stop_task(task_id: str) -> dict[str, Any]:
    """Emergency stop: locks synthetic input immediately and terminates runner."""
    ok = service.emergency_stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to emergency stop task")
    return {"status": "emergency_stopped", "task_id": task_id, "input_locked": True}


# 8. Reset Emergency Lock
@app.post("/tasks/{task_id}/reset", dependencies=[Depends(verify_auth_token)])
def reset_task(task_id: str) -> dict[str, Any]:
    """Explicitly reset an emergency-stopped task and release synthetic input lock."""
    ok, msg = service.reset_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "reset", "task_id": task_id, "input_locked": False, "message": msg}


# 9. Explicit Human Approval
@app.post("/tasks/{task_id}/approvals/{event_id}", dependencies=[Depends(verify_auth_token)])
def create_approval(task_id: str, event_id: str, req: CreateApprovalRequest) -> dict[str, Any]:
    """Grant explicit human approval for a gated action."""
    ok, result = service.create_approval(
        task_id=task_id,
        event_id=event_id,
        step=req.step,
        action=req.action,
        target=req.target,
        screenshot_hash=req.screenshot_hash,
        action_fingerprint=req.action_fingerprint,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=str(result))
    return {
        "status": "approved",
        "task_id": task_id,
        "event_id": event_id,
        "approval": result,
    }


# 9b. Explicit WebMCP Consequential Approval
@app.post("/tasks/{task_id}/webmcp-approvals", dependencies=[Depends(verify_auth_token)])
def create_webmcp_approval(task_id: str, req: CreateWebMCPApprovalRequest) -> dict[str, Any]:
    """Grant cryptographically verified server-side approval for a consequential WebMCP tool."""
    approval_id = f"appr_wmcp_{uuid.uuid4().hex[:12]}"
    record = db.create_webmcp_approval(
        approval_id=approval_id,
        task_id=task_id,
        event_id=req.event_id,
        origin=req.origin,
        tool_name=req.tool_name,
        schema_hash=req.schema_hash,
        arguments_hash=req.arguments_hash,
        navigation_epoch=req.navigation_epoch,
        ttl_seconds=req.ttl_seconds,
        single_use=req.single_use,
        approved_by=req.approved_by,
    )
    logger.info(
        "AUDIT WebMCP approval granted: task=%s tool=%s origin=%s schema=%s args=%s approval_id=%s",
        task_id, req.tool_name, req.origin, req.schema_hash, req.arguments_hash, approval_id
    )
    return {
        "status": "approved",
        "task_id": task_id,
        "approval": record,
    }


# 10. Exclusive Takeover
@app.post("/tasks/{task_id}/takeover", dependencies=[Depends(verify_auth_token)])
def takeover_task(task_id: str) -> dict[str, Any]:
    """Acquire exclusive interactive control: pauses agent and locks synthetic inputs."""
    ok, msg = service.takeover_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    guest_ip = get_vm_ip()
    vnc_user = os.environ.get("VNC_USER", "admin")
    vnc_uri = f"vnc://{vnc_user}@{guest_ip}"
    return {
        "status": "takeover",
        "task_id": task_id,
        "input_locked": True,
        "vnc_uri": vnc_uri,
        "guest_ip": guest_ip,
        "instructions": f"Open native macOS Screen Sharing on host: open '{vnc_uri}'. Enter guest credentials in macOS Screen Sharing UI.",
    }


# 11. Release Takeover
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


# 12. Generate Short-Lived Single-Use SSE Ticket
@app.post("/tasks/{task_id}/events/token", dependencies=[Depends(verify_auth_token)])
def create_events_token(task_id: str) -> dict[str, Any]:
    """Issue a short-lived, task-scoped, single-use ticket for SSE streaming."""
    ticket_data = service.create_sse_ticket(task_id)
    if not ticket_data:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "status": "ticket_issued",
        "task_id": task_id,
        "ticket": ticket_data["ticket"],
        "expires_in": ticket_data["expires_in"],
        "stream_url": f"/tasks/{task_id}/events?ticket={ticket_data['ticket']}",
    }


# 13. Stream Realtime Events (SSE)
@app.get("/tasks/{task_id}/events")
async def get_task_events(
    task_id: str,
    request: Request,
    ticket: str | None = Query(None, alias="ticket"),
    last_event_id: str | None = Query(None, alias="last_event_id"),
) -> EventSourceResponse:
    """Stream realtime task events via Server-Sent Events (SSE). Supports Last-Event-ID reconnect.
    
    Authentication requires either:
    1. Standard Authorization: Bearer <main_token> header (e.g. from fetch() streams), OR
    2. A short-lived, task-scoped, single-use ticket via ?ticket=<ticket> (e.g. from EventSource).
    """
    auth_header = request.headers.get("Authorization")
    x_token = request.headers.get("X-Worker-Token")
    current_main_token = os.environ.get("WORKER_AUTH_TOKEN", WORKER_AUTH_TOKEN)

    authenticated = False
    if auth_header and auth_header.startswith("Bearer "):
        if auth_header[7:].strip() == current_main_token:
            authenticated = True
    elif (x_token and x_token.strip() == current_main_token) or (ticket and service.validate_and_consume_sse_ticket(ticket, task_id)):
        authenticated = True

    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, missing, or expired authentication (use Bearer header or valid ?ticket=)",
        )

    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    header_last_id = request.headers.get("Last-Event-ID")
    effective_last_id = last_event_id or header_last_id

    return EventSourceResponse(
        event_hub.subscribe(task_id, last_event_id=effective_last_id)
    )


# 14. Screenshot
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
        pngs = sorted(run_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pngs:
            target_file = pngs[0]

    if not target_file or not target_file.exists():
        raise HTTPException(status_code=404, detail="No screenshot available yet")

    return FileResponse(target_file, media_type="image/png")


def run_server() -> None:
    """CLI script runner for clicker-worker."""
    import uvicorn

    host = os.environ.get("WORKER_HOST", "0.0.0.0")
    port = int(os.environ.get("WORKER_PORT", "8000"))
    print(f"Starting typesafe-computer-worker on http://{host}:{port}")
    uvicorn.run("typesafe_computer_use.worker.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
