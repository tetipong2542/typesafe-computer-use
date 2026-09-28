/**
 * @fileoverview Dual-Engine Orchestrator for OpenMausBot.
 * Coordinates high-level OpenMausBot workflows with TypeSafe Worker,
 * managing real-time SSE streaming, human-in-the-loop approvals, and tri-tier routing.
 */

import { TypeSafeWorkerClient } from "./client.js";
import { TypeSafeMCPClient } from "./mcp-client.js";
import { SafetyGuard, ApprovalCallback } from "./safety-guard.js";
import {
  CreateTaskOptions,
  InteractionMode,
  OrchestrationResult,
  TaskRecord,
  TaskStreamEvent,
} from "./types.js";
import { EmergencyStoppedError, UnknownSideEffectError } from "./errors.js";

export interface OrchestratorOptions {
  workerClient?: TypeSafeWorkerClient;
  mcpClient?: TypeSafeMCPClient;
  approvalHandler?: ApprovalCallback;
  pollIntervalMs?: number;
  maxWaitTimeoutMs?: number;
}

export class OpenMausBotOrchestrator {
  public readonly workerClient: TypeSafeWorkerClient;
  public readonly mcpClient: TypeSafeMCPClient;
  public readonly safetyGuard: SafetyGuard;
  private readonly approvalHandler?: ApprovalCallback;
  private readonly pollIntervalMs: number;
  private readonly maxWaitTimeoutMs: number;

  constructor(options: OrchestratorOptions = {}) {
    this.workerClient = options.workerClient || new TypeSafeWorkerClient();
    this.mcpClient = options.mcpClient || new TypeSafeMCPClient();
    this.safetyGuard = new SafetyGuard(this.workerClient);
    this.approvalHandler = options.approvalHandler;
    this.pollIntervalMs = options.pollIntervalMs ?? 500;
    this.maxWaitTimeoutMs = options.maxWaitTimeoutMs ?? 120000;
  }

  /**
   * Execute an OpenMausBot goal end-to-end through TypeSafe Engine.
   * Tracks task lifecycle, streams real-time logs, handles approvals, and returns final outcome.
   */
  public async executeGoal(
    goal: string,
    options: Omit<CreateTaskOptions, "goal"> = {},
    onLogEvent?: (event: TaskStreamEvent) => void
  ): Promise<OrchestrationResult> {
    // 1. Submit task to worker
    const initialTask = await this.workerClient.createTask({
      goal,
      ...options,
    });

    const fallbackHistory: Array<{
      from: InteractionMode;
      to: InteractionMode;
      reason: string;
    }> = [];

    let currentTask: TaskRecord = initialTask;
    let finalAnswer: string | undefined;
    let unsubscribeEvents: (() => void) | undefined;

    // 2. Set up SSE stream for real-time telemetry and logs
    try {
      unsubscribeEvents = this.workerClient.subscribeTaskEvents(
        initialTask.taskId,
        (event) => {
          if (onLogEvent) onLogEvent(event);

          if (event.type === "log" && event.data.fallback) {
            const fb = event.data.fallback as any;
            fallbackHistory.push({
              from: fb.from || "browser_dom",
              to: fb.to || "visual_grounded",
              reason: fb.reason || "stalled_progress",
            });
          }
        },
        (err) => {
          // SSE connection dropped; polling loop below acts as fallback
        }
      );

      // 3. Execution polling loop with safety gating
      const startTime = Date.now();
      while (Date.now() - startTime < this.maxWaitTimeoutMs) {
        currentTask = await this.workerClient.getTask(initialTask.taskId);

        // Strict Invariant Check
        if (currentTask.sideEffectState === "unknown") {
          throw new UnknownSideEffectError(
            "executeGoal",
            currentTask.taskId,
            currentTask.error || "Task entered SideEffectState.UNKNOWN"
          );
        }

        if (currentTask.state === "completed") {
          finalAnswer = `Goal achieved: ${currentTask.goal}`;
          break;
        }

        if (currentTask.state === "failed") {
          break;
        }

        if (currentTask.state === "emergency_stopped") {
          throw new EmergencyStoppedError(currentTask.taskId);
        }

        // Handle Gated Action / Human in the loop
        if (currentTask.state === "awaiting_review" && currentTask.approvalRequired) {
          if (this.approvalHandler) {
            await this.safetyGuard.processTaskGating(currentTask, this.approvalHandler);
          }
        }

        await new Promise((r) => setTimeout(r, this.pollIntervalMs));
      }
    } finally {
      if (unsubscribeEvents) unsubscribeEvents();
    }

    const executedMode: InteractionMode = currentTask.executedMode || "browser_dom";

    return {
      taskId: currentTask.taskId,
      goal: currentTask.goal,
      success: currentTask.state === "completed",
      finalAnswer,
      executedMode,
      fallbackHistory,
      turns: currentTask.stepsExecuted || 1,
      totalTokens: 0,
      actualCostUsd: 0.0,
      error: currentTask.error || (currentTask.state === "failed" ? "Task failed on worker" : null),
    };
  }

  /**
   * Fast emergency stop trigger: halts the active task immediately and engages synthetic input lock.
   */
  public async emergencyStop(taskId: string): Promise<boolean> {
    const res = await this.workerClient.emergencyStop(taskId);
    return res.inputLocked;
  }
}
