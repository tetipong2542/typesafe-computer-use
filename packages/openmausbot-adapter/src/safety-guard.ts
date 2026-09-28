/**
 * @fileoverview Safety Guard and Human-in-the-Loop Gatekeeper for OpenMausBot.
 * Enforces SideEffectState invariants, TOCTOU action verification, and approval flows.
 */

import { SideEffectState, TaskRecord } from "./types.js";
import { ActionGatedError, UnknownSideEffectError } from "./errors.js";
import { TypeSafeWorkerClient } from "./client.js";

export type ApprovalDecision =
  | { approved: true; reason?: string }
  | { approved: false; reason: string };

export type ApprovalCallback = (details: {
  taskId: string;
  eventId: string;
  action: string;
  target: string;
  screenshotHash?: string;
  actionFingerprint?: string;
}) => Promise<ApprovalDecision>;

export class SafetyGuard {
  constructor(private readonly client: TypeSafeWorkerClient) {}

  /**
   * Enforces side-effect transition rules:
   * - UNKNOWN state triggers an immediate halt; retries are forbidden.
   */
  public assertSafeTransition(
    current: SideEffectState,
    next: SideEffectState,
    action: string,
    target: string,
    details: string = ""
  ): void {
    if (next === "unknown") {
      throw new UnknownSideEffectError(action, target, details || "Action produced ambiguous side-effect state");
    }

    if (current === "unknown" && next !== "not_started") {
      throw new UnknownSideEffectError(
        action,
        target,
        "Cannot transition away from UNKNOWN side-effect state without explicit system reset"
      );
    }
  }

  /**
   * Evaluates whether a task requires explicit approval and processes it through the callback.
   */
  public async processTaskGating(
    task: TaskRecord,
    onApprovalRequested: ApprovalCallback
  ): Promise<{ status: "approved" | "rejected" | "skipped"; reason?: string }> {
    if (task.state !== "awaiting_review" || !task.approvalRequired) {
      return { status: "skipped" };
    }

    const { eventId, action, target, screenshotHash, actionFingerprint } = task.approvalRequired;

    const decision = await onApprovalRequested({
      taskId: task.taskId,
      eventId,
      action,
      target,
      screenshotHash,
      actionFingerprint,
    });

    if (decision.approved) {
      // Submit approval to worker
      await this.client.approveAction(task.taskId, eventId, {
        step: task.stepsExecuted || 1,
        action,
        target,
        screenshotHash,
        actionFingerprint,
      });

      // Resume task execution
      await this.client.resumeTask(task.taskId);
      return { status: "approved", reason: decision.reason };
    } else {
      // Rejection: Stop task gracefully
      await this.client.stopTask(task.taskId);
      return { status: "rejected", reason: decision.reason };
    }
  }
}
