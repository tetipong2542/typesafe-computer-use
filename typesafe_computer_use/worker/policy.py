"""Confidence Gate and Critical Action Policy Engine."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image

POLICY_MIN_CONFIDENCE_FLOOR = 0.80
DEFAULT_NORMAL_CONFIDENCE_THRESHOLD = 0.80


def clamp_confidence_to_floor(val: float) -> float:
    """Enforce server policy floor. Client cannot lower threshold below floor."""
    return max(val, POLICY_MIN_CONFIDENCE_FLOOR)


def compute_screenshot_hash(image: Image.Image) -> str:
    """Compute deterministic SHA-256 hash of an image."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def compute_action_fingerprint(action: str, target: str | None, step: int) -> str:
    """Compute SHA-256 fingerprint of an action tuple."""
    raw = f"{action}:{target or ''}:{step}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

CRITICAL_PATTERNS = [
    r"\bdelete\b",
    r"\bremove\b",
    r"\btrash\b",
    r"\bdestroy\b",
    r"\bpay\b",
    r"\bpayment\b",
    r"\bpurchase\b",
    r"\bcheckout\b",
    r"\brefund\b",
    r"\bpublish\b",
    r"\bsend\b",
    r"\btransfer\b",
    r"\bplace\s+order\b",
    r"\bsubmit\s+order\b",
    r"\bdrop\s+table\b",
    r"\btruncate\b",
    r"\bproduction\b",
    r"\bmutate\b",
]

_CRITICAL_REGEX = re.compile("|".join(CRITICAL_PATTERNS), re.IGNORECASE)


class PolicyDecision(StrEnum):
    ALLOWED = "ALLOWED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    BLOCKED = "BLOCKED"


@dataclass
class PolicyCheckResult:
    decision: PolicyDecision
    reason: str
    confidence: float
    is_critical: bool


class PolicyEngine:
    """Evaluates proposed actions against confidence thresholds and safety rules."""

    def __init__(
        self,
        normal_threshold: float = DEFAULT_NORMAL_CONFIDENCE_THRESHOLD,
        block_critical: bool = True,
    ):
        self.normal_threshold = normal_threshold
        self.block_critical = block_critical

    def is_critical_action(self, action_kind: str, target_text: str | None = None, extra_text: str | None = None) -> tuple[bool, str]:
        """Check if action or target matches critical operations."""
        texts_to_check = [action_kind]
        if target_text:
            texts_to_check.append(target_text)
        if extra_text:
            texts_to_check.append(extra_text)

        combined = " ".join(texts_to_check)
        match = _CRITICAL_REGEX.search(combined)
        if match:
            return True, f"Matched critical keyword: {match.group(0)!r}"
        return False, ""

    def evaluate(
        self,
        action_kind: str,
        confidence: float,
        target_text: str | None = None,
        extra_text: str | None = None,
        is_human_approved: bool = False,
    ) -> PolicyCheckResult:
        """Evaluate action. Returns ALLOWED, AWAITING_REVIEW, or BLOCKED."""
        if is_human_approved:
            return PolicyCheckResult(
                decision=PolicyDecision.ALLOWED,
                reason="Explicitly approved by human",
                confidence=confidence,
                is_critical=False,
            )

        is_critical, crit_reason = self.is_critical_action(action_kind, target_text, extra_text)
        if is_critical:
            decision = PolicyDecision.BLOCKED if self.block_critical else PolicyDecision.AWAITING_REVIEW
            return PolicyCheckResult(
                decision=decision,
                reason=f"Critical operation intercepted: {crit_reason}",
                confidence=confidence,
                is_critical=True,
            )

        if confidence < self.normal_threshold:
            return PolicyCheckResult(
                decision=PolicyDecision.AWAITING_REVIEW,
                reason=f"Confidence {confidence:.2f} is below threshold {self.normal_threshold:.2f}",
                confidence=confidence,
                is_critical=False,
            )

        return PolicyCheckResult(
            decision=PolicyDecision.ALLOWED,
            reason=f"Confidence {confidence:.2f} satisfies threshold {self.normal_threshold:.2f}",
            confidence=confidence,
            is_critical=False,
        )
