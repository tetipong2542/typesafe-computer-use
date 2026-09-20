"""Integration tests for FastAPI Worker API endpoints."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Ensure a known auth token for integration testing
os.environ["WORKER_AUTH_TOKEN"] = "typesafe-worker-secret-token"

from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.events import EventHub
from typesafe_computer_use.worker.policy import PolicyEngine, compute_action_fingerprint
from typesafe_computer_use.worker.server import app
from typesafe_computer_use.worker.service import WorkerService
from typesafe_computer_use.worker.state import TaskState

AUTH_HEADERS = {"Authorization": "Bearer typesafe-worker-secret-token"}


def setup_test_service(tmp_path):
    test_db = WorkerDatabase(tmp_path / "api_test.db")
    test_hub = EventHub(test_db)
    test_policy = PolicyEngine(normal_threshold=0.80)
    test_service = WorkerService(test_db, test_hub, test_policy, base_dir=tmp_path)
    return test_service


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# Parameterized test: all protected endpoints reject missing or invalid auth
PROTECTED_ENDPOINTS = [
    ("POST", "/tasks", {"goal": "test"}),
    ("GET", "/tasks/task_test_id", None),
    ("POST", "/tasks/task_test_id/pause", None),
    ("POST", "/tasks/task_test_id/resume", None),
    ("POST", "/tasks/task_test_id/stop", None),
    ("POST", "/tasks/task_test_id/emergency-stop", None),
    ("POST", "/tasks/task_test_id/reset", None),
    ("POST", "/tasks/task_test_id/approvals/evt_1", {
        "step": 1, "action": "click", "target": "ok",
        "screenshot_hash": "h1", "action_fingerprint": "fp1"
    }),
    ("POST", "/tasks/task_test_id/takeover", None),
    ("POST", "/tasks/task_test_id/release-takeover", None),
    ("POST", "/tasks/task_test_id/events/token", None),
    ("GET", "/tasks/task_test_id/screenshot", None),
]


@pytest.mark.parametrize("method,path,payload", PROTECTED_ENDPOINTS)
def test_all_endpoints_auth_rejection(method, path, payload):
    client = TestClient(app)

    # 1. No auth header -> 401
    kwargs = {"json": payload} if payload else {}
    res = client.request(method, path, **kwargs)
    assert res.status_code == 401, f"{method} {path} should reject without auth"

    # 2. Wrong auth header -> 401
    res_wrong = client.request(method, path, headers={"Authorization": "Bearer wrong-token"}, **kwargs)
    assert res_wrong.status_code == 401, f"{method} {path} should reject invalid token"


# Parameterized test: task-scoped endpoints return 404 for nonexistent tasks
TASK_SCOPED_ENDPOINTS = [
    ("GET", "/tasks/task_nonexistent", None),
    ("POST", "/tasks/task_nonexistent/pause", None),
    ("POST", "/tasks/task_nonexistent/resume", None),
    ("POST", "/tasks/task_nonexistent/stop", None),
    ("POST", "/tasks/task_nonexistent/emergency-stop", None),
    ("POST", "/tasks/task_nonexistent/approvals/evt_1", {
        "step": 1, "action": "click", "target": "ok",
        "screenshot_hash": "h1", "action_fingerprint": "fp1"
    }),
    ("POST", "/tasks/task_nonexistent/takeover", None),
    ("POST", "/tasks/task_nonexistent/release-takeover", None),
    ("POST", "/tasks/task_nonexistent/events/token", None),
    ("GET", "/tasks/task_nonexistent/screenshot", None),
]


@pytest.mark.parametrize("method,path,payload", TASK_SCOPED_ENDPOINTS)
def test_all_endpoints_not_found(method, path, payload, tmp_path):
    test_svc = setup_test_service(tmp_path)
    with patch("typesafe_computer_use.worker.server.service", test_svc):
        client = TestClient(app)
        kwargs = {"json": payload} if payload else {}
        res = client.request(method, path, headers=AUTH_HEADERS, **kwargs)
        # Should be 400 or 404 depending on whether it's an action or lookup
        assert res.status_code in (400, 404), f"{method} {path} should fail on nonexistent task"


def test_invalid_state_transitions(tmp_path):
    test_svc = setup_test_service(tmp_path)
    with patch("typesafe_computer_use.worker.server.service", test_svc):
        client = TestClient(app)

        # Create task in QUEUED state
        test_svc.db.create_task("task_trans_01", goal="Goal", config={})
        controller = test_svc._controllers.get("task_trans_01")
        if not controller:
            from typesafe_computer_use.worker.service import TaskController
            test_svc._controllers["task_trans_01"] = TaskController("task_trans_01", "run_1")

        # Calling resume when queued -> 400
        res = client.post("/tasks/task_trans_01/resume", headers=AUTH_HEADERS)
        assert res.status_code == 400

        # Calling release-takeover when not in takeover -> 400
        res = client.post("/tasks/task_trans_01/release-takeover", headers=AUTH_HEADERS)
        assert res.status_code == 400

        # Mark task STOPPED
        test_svc.db.update_task("task_trans_01", state=TaskState.STOPPED)

        # Calling pause on STOPPED task -> 400
        res = client.post("/tasks/task_trans_01/pause", headers=AUTH_HEADERS)
        assert res.status_code == 400


def test_sse_ticket_lifecycle(tmp_path):
    test_svc = setup_test_service(tmp_path)
    with patch("typesafe_computer_use.worker.server.service", test_svc):
        client = TestClient(app)

        test_svc.db.create_task("task_sse_01", goal="Goal", config={})

        # 1. Main token in ?token= is NOT allowed
        res = client.get("/tasks/task_sse_01/events?token=typesafe-worker-secret-token")
        assert res.status_code == 401

        async def mock_sub(task_id, last_event_id=None):
            yield {"event": "state_changed", "data": "{}"}

        with patch("typesafe_computer_use.worker.server.event_hub.subscribe", mock_sub):
            # 2. Main token in Authorization header IS allowed
            res = client.get("/tasks/task_sse_01/events", headers=AUTH_HEADERS)
            assert res.status_code == 200
            assert "text/event-stream" in res.headers["content-type"]

            # 3. Issue a short-lived ticket via POST /tasks/{id}/events/token
            token_res = client.post("/tasks/task_sse_01/events/token", headers=AUTH_HEADERS)
            assert token_res.status_code == 200
            ticket_data = token_res.json()
            assert ticket_data["status"] == "ticket_issued"
            ticket = ticket_data["ticket"]
            assert ticket.startswith("ticket_")

            # 4. Connect with ticket via query string
            res = client.get(f"/tasks/task_sse_01/events?ticket={ticket}")
            assert res.status_code == 200

            # 5. Ticket is single-use; connecting a second time must return 401
            res_repeat = client.get(f"/tasks/task_sse_01/events?ticket={ticket}")
            assert res_repeat.status_code == 401

            # 6. Ticket for wrong task must return 401
            test_svc.db.create_task("task_sse_other", goal="Other", config={})
            t2_res = client.post("/tasks/task_sse_01/events/token", headers=AUTH_HEADERS)
            t2_ticket = t2_res.json()["ticket"]
            res_wrong_task = client.get(f"/tasks/task_sse_other/events?ticket={t2_ticket}")
            assert res_wrong_task.status_code == 401


def test_create_and_manage_task(tmp_path):
    test_svc = setup_test_service(tmp_path)
    with (
        patch("typesafe_computer_use.worker.server.service", test_svc),
        patch.object(test_svc, "_run_task_thread", return_value=None),
    ):
        client = TestClient(app)

        # 1. Create task
        res = client.post(
            "/tasks",
            json={"goal": "Search something", "steps": 5},
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200
        data = res.json()
        task_id = data["task_id"]
        assert task_id.startswith("task_")

        # 2. Get task
        res = client.get(f"/tasks/{task_id}", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["goal"] == "Search something"

        # 3. Pause task
        res = client.post(f"/tasks/{task_id}/pause", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "pause_requested"

        # 4. Resume task from paused state
        test_svc.db.update_task(task_id, state=TaskState.PAUSED)
        res = client.post(f"/tasks/{task_id}/resume", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "resumed"

        # 5. Takeover: returns clean vnc://admin@<ip> without password
        test_svc.db.update_task(task_id, state=TaskState.PAUSED)
        res = client.post(f"/tasks/{task_id}/takeover", headers=AUTH_HEADERS)
        assert res.status_code == 200
        takeover_data = res.json()
        assert takeover_data["status"] == "takeover"
        assert takeover_data["input_locked"] is True
        assert "vnc://admin@" in takeover_data["vnc_uri"]
        assert "admin:admin" not in takeover_data["vnc_uri"]

        # 6. Release Takeover
        res = client.post(f"/tasks/{task_id}/release-takeover", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "takeover_released"

        # 7. Approvals flow: when in awaiting_review
        fp = compute_action_fingerprint("click", "Delete Account", 2)
        test_svc.db.update_task(task_id, state=TaskState.AWAITING_REVIEW)
        controller = test_svc._controllers[task_id]
        controller.current_review_event_id = "evt_review_01"
        controller.current_step = 2
        controller.current_action_fingerprint = fp
        controller.current_screenshot_hash = "hash_screen_abc"

        # Resuming without approval must fail with 400
        fail_resume = client.post(f"/tasks/{task_id}/resume", headers=AUTH_HEADERS)
        assert fail_resume.status_code == 400

        # Grant approval
        approval_res = client.post(
            f"/tasks/{task_id}/approvals/evt_review_01",
            json={
                "step": 2,
                "action": "click",
                "target": "Delete Account",
                "screenshot_hash": "hash_screen_abc",
                "action_fingerprint": fp,
            },
            headers=AUTH_HEADERS,
        )
        assert approval_res.status_code == 200
        assert approval_res.json()["status"] == "approved"

        # Now resume succeeds
        ok_resume = client.post(f"/tasks/{task_id}/resume", headers=AUTH_HEADERS)
        assert ok_resume.status_code == 200
        assert ok_resume.json()["status"] == "resumed"

        # 8. Stop task
        res = client.post(f"/tasks/{task_id}/stop", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "stopping"

        # 9. Emergency stop
        res = client.post(f"/tasks/{task_id}/emergency-stop", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["input_locked"] is True

        # 10. Reset task after emergency stop
        reset_res = client.post(f"/tasks/{task_id}/reset", headers=AUTH_HEADERS)
        assert reset_res.status_code == 200
        assert reset_res.json()["status"] == "reset"
        assert reset_res.json()["input_locked"] is False
