"""Integration tests for FastAPI Worker API endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient

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

        # 4. Resume task
        # Manually set state to paused to simulate safe boundary arrival
        test_svc.db.update_task(task_id, state=TaskState.PAUSED)
        res = client.post(f"/tasks/{task_id}/resume", json={"approve": True}, headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "resumed"

        # 5. Takeover
        test_svc.db.update_task(task_id, state=TaskState.PAUSED)
        res = client.post(f"/tasks/{task_id}/takeover", headers=AUTH_HEADERS)
        assert res.status_code == 200
        takeover_data = res.json()
        assert takeover_data["status"] == "takeover"
        assert takeover_data["input_locked"] is True
        assert "vnc://" in takeover_data["vnc_uri"]

        # 6. Release Takeover
        res = client.post(f"/tasks/{task_id}/release-takeover", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "takeover_released"

        # 7. Stop task
        res = client.post(f"/tasks/{task_id}/stop", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["status"] == "stopping"

        # 8. Emergency stop
        res = client.post(f"/tasks/{task_id}/emergency-stop", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["input_locked"] is True

        # 9. Test SSE endpoint 404 for unknown task
        res = client.get("/tasks/task-nonexistent/events")
        assert res.status_code == 404
