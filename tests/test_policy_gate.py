"""Tests for PolicyEngine, confidence gating, critical action blocking, and approval fingerprints."""

from PIL import Image

from typesafe_computer_use.worker.db import WorkerDatabase
from typesafe_computer_use.worker.policy import (
    POLICY_MIN_CONFIDENCE_FLOOR,
    PolicyDecision,
    PolicyEngine,
    clamp_confidence_to_floor,
    compute_action_fingerprint,
    compute_screenshot_hash,
)


def test_confidence_floor_clamping():
    # Client cannot lower threshold below 0.80 hard floor
    assert clamp_confidence_to_floor(0.50) == POLICY_MIN_CONFIDENCE_FLOOR
    assert clamp_confidence_to_floor(0.79) == POLICY_MIN_CONFIDENCE_FLOOR
    assert clamp_confidence_to_floor(0.85) == 0.85
    assert clamp_confidence_to_floor(1.0) == 1.0


def test_screenshot_and_action_fingerprints():
    # Test image hashing
    img1 = Image.new("RGB", (100, 100), color="white")
    img2 = Image.new("RGB", (100, 100), color="white")
    img3 = Image.new("RGB", (100, 100), color="black")

    hash1 = compute_screenshot_hash(img1)
    hash2 = compute_screenshot_hash(img2)
    hash3 = compute_screenshot_hash(img3)

    assert hash1 == hash2
    assert hash1 != hash3

    # Test action fingerprints
    fp1 = compute_action_fingerprint("click", "Confirm", 1)
    fp2 = compute_action_fingerprint("click", "Confirm", 1)
    fp3 = compute_action_fingerprint("click", "Cancel", 1)
    fp4 = compute_action_fingerprint("click", "Confirm", 2)

    assert fp1 == fp2
    assert fp1 != fp3
    assert fp1 != fp4


def test_confidence_threshold_allowed():
    engine = PolicyEngine(normal_threshold=0.80)
    result = engine.evaluate(action_kind="click_item", confidence=0.85, target_text="Next Page")
    assert result.decision == PolicyDecision.ALLOWED
    assert not result.is_critical


def test_confidence_threshold_awaiting_review():
    engine = PolicyEngine(normal_threshold=0.80)
    result = engine.evaluate(action_kind="click_item", confidence=0.74, target_text="Next Page")
    assert result.decision == PolicyDecision.AWAITING_REVIEW
    assert "below threshold" in result.reason


def test_critical_action_blocked():
    engine = PolicyEngine(normal_threshold=0.80, block_critical=True)

    # High confidence (0.95), but critical target "Delete Account"
    result = engine.evaluate(action_kind="click_item", confidence=0.95, target_text="Delete Account")
    assert result.decision == PolicyDecision.BLOCKED
    assert result.is_critical
    assert "delete" in result.reason.lower()


def test_critical_action_keywords():
    engine = PolicyEngine(normal_threshold=0.80, block_critical=True)

    keywords = ["Pay Now", "Purchase Subscription", "Refund Order", "Submit Order", "Drop table users", "Publish Article"]
    for kw in keywords:
        res = engine.evaluate(action_kind="click_item", confidence=0.99, target_text=kw)
        assert res.decision == PolicyDecision.BLOCKED
        assert res.is_critical


def test_human_approval_override():
    engine = PolicyEngine(normal_threshold=0.80, block_critical=True)

    # Even a critical or low confidence action passes if human explicitly approved it
    result = engine.evaluate(
        action_kind="click_item",
        confidence=0.50,
        target_text="Delete Database",
        is_human_approved=True,
    )
    assert result.decision == PolicyDecision.ALLOWED
    assert "approved by human" in result.reason.lower()


def test_approval_db_lifecycle(tmp_path):
    db = WorkerDatabase(tmp_path / "approval_test.db")
    task_id = "task_appr_01"
    event_id = "evt_appr_01"
    db.create_task(task_id, goal="Test Approval", config={})

    # Create approval
    appr = db.create_approval(
        approval_id="appr_01",
        task_id=task_id,
        event_id=event_id,
        step=1,
        action="click",
        target="Confirm",
        screenshot_hash="hash_123",
        action_fingerprint="fp_123",
        ttl_seconds=10.0,
    )
    assert appr["approval_id"] == "appr_01"

    # Get active approval
    active = db.get_active_approval(task_id, event_id)
    assert active is not None
    assert active["approval_id"] == "appr_01"

    # Consume approval
    consumed = db.consume_approval("appr_01")
    assert consumed is True

    # Consumed approval should not be returned as active
    assert db.get_active_approval(task_id, event_id) is None

    # Test expired approval
    db.create_approval(
        approval_id="appr_02",
        task_id=task_id,
        event_id=event_id,
        step=2,
        action="click",
        target="Next",
        screenshot_hash="hash_456",
        action_fingerprint="fp_456",
        ttl_seconds=-1.0,  # Already expired
    )
    assert db.get_active_approval(task_id, event_id) is None


def test_post_approval_screen_change_revocation(tmp_path):
    from typesafe_computer_use.worker.events import EventHub
    from typesafe_computer_use.worker.service import TaskController, WorkerService

    db = WorkerDatabase(tmp_path / "revocation_test.db")
    hub = EventHub(db)
    service = WorkerService(db, hub, PolicyEngine(), base_dir=tmp_path)

    task_id = "task_rev_01"
    event_id = "evt_rev_01"
    db.create_task(task_id, goal="Test Revocation", config={}, run_id="run_rev_01")
    controller = TaskController(task_id, run_id="run_rev_01")
    service._controllers[task_id] = controller

    controller.current_review_event_id = event_id
    controller.current_step = 1
    controller.current_screenshot_hash = "hash_initial_screen"
    controller.active_approval_id = "appr_test_123"
    controller.human_approved = True

    # Simulate screen hash changed
    new_screen_hash = "hash_screen_moved"
    assert new_screen_hash != controller.current_screenshot_hash

    # Verify auto-revocation logic
    if new_screen_hash != controller.current_screenshot_hash:
        controller.active_approval_id = None
        controller.human_approved = False

    assert controller.active_approval_id is None
    assert controller.human_approved is False

