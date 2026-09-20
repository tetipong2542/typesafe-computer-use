"""Integration tests for FastAPI Worker API endpoints."""

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

# Ensure a known auth token for integration testing
os.environ["WORKER_AUTH_TOKEN"] = "typesafe-worker-secret-token"

from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.events import EventHub
from typesafe_computer_use.worker.policy import PolicyEngine
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


def test_auth_rejection():
    client = TestClient(app)
    # No auth header
    response = client.post("/tasks", json={"goal": "test"})
    assert response.status_code == 401

    # Wrong auth header
    response = client.post("/tasks", json={"goal": "test"}, headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_sse_auth():
    client = TestClient(app)
    # SSE without token -> 401
    res = client.get("/tasks/task_123/events")
    assert res.status_code == 401

    # SSE with query param token -> 404 (because task not found, but authenticated!)
    res = client.get("/tasks/task_123/events?token=typesafe-worker-secret-token")
    assert res.status_code == 404

    # SSE with header -> 404 (authenticated)
    res = client.get("/tasks/task_123/events", headers=AUTH_HEADERS)
    assert res.status_code == 404


def test_create_and_manage_task(tmp_path):
    test_svc = setup_test_service(tmp_path)
    # Patch the global service in server
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
        assert "admin:admin" not in takeover_data["vnc_uri"]  # No embedded password!

        # 6. Release Takeover
        res = client.post(f"/tasks/{task_id}/release-takeover", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "takeover_released"

        # 7. Approvals flow: when in awaiting_review
        from typesafe_computer_use.worker.policy import compute_action_fingerprint

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
