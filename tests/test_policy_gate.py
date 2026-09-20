"""Tests for PolicyEngine, confidence gating, and critical action blocking."""

from typesafe_computer_use.worker.policy import PolicyDecision, PolicyEngine


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
