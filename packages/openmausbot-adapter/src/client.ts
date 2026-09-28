/**
 * @fileoverview TypeSafe Worker Client for OpenMausBot.
 * Implements high-level HTTP REST and SSE communication with TypeSafe Worker daemon.
 * Supports task lifecycle control, emergency stop, approvals, and event streaming.
 */

import {
  ApprovalPayload,
  CreateTaskOptions,
  TaskRecord,
  TaskStreamEvent,
  WebMCPApprovalPayload,
  WorkerClientConfig,
} from "./types.js";
import {
  EmergencyStoppedError,
  TaskNotFoundError,
  TypeSafeError,
  WorkerAuthError,
  WorkerConnectionError,
} from "./errors.js";

export class TypeSafeWorkerClient {
  public readonly baseUrl: string;
  private readonly authToken: string;
  private readonly timeoutMs: number;
  private readonly vncHost: string;

  constructor(config: WorkerClientConfig = {}) {
    this.baseUrl = (config.baseUrl || process.env.TYPESAFE_WORKER_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
    this.authToken = config.authToken || process.env.WORKER_AUTH_TOKEN || "test-worker-token";
    this.timeoutMs = config.timeoutMs ?? 30000;
    this.vncHost = config.vncHost || (new URL(this.baseUrl).hostname);
  }

  /**
   * Helper to perform authenticated HTTP requests with timeout and error classification.
   */
  private async request<T>(
    endpoint: string,
    options: {
      method?: "GET" | "POST" | "PUT" | "DELETE";
      body?: unknown;
      skipAuth?: boolean;
    } = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "Accept": "application/json",
    };

    if (!options.skipAuth) {
      headers["Authorization"] = `Bearer ${this.authToken}`;
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method: options.method || "GET",
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err: unknown) {
      throw new WorkerConnectionError(
        `Failed to connect to TypeSafe worker at ${url}: ${(err as Error).message}`,
        err
      );
    }

    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        throw new WorkerAuthError(`Authentication failed on ${endpoint}: HTTP ${response.status}`);
      }
      if (response.status === 404) {
        throw new TaskNotFoundError(endpoint);
      }

      const errText = await response.text().catch(() => "");
      throw new TypeSafeError(
        `TypeSafe worker returned HTTP ${response.status} on ${endpoint}: ${errText}`,
        `HTTP_${response.status}`
      );
    }

    return (await response.json()) as T;
  }

  /**
   * Health probe to verify worker daemon readiness.
   */
  public async healthCheck(): Promise<{ status: string; service: string }> {
    return await this.request<{ status: string; service: string }>("/healthz", {
      skipAuth: true,
    });
  }

  /**
   * Submit and start a new background task on the TypeSafe engine.
   */
  public async createTask(options: CreateTaskOptions): Promise<TaskRecord> {
    const raw = await this.request<{
      task_id: string;
      run_id: string;
      state: string;
      goal: string;
    }>("/tasks", {
      method: "POST",
      body: {
        goal: options.goal,
        act: options.act ?? true,
        steps: options.steps ?? 10,
        min_confidence: options.minConfidence ?? 0.6,
        delay: options.delay ?? 1.0,
        browser: options.browser,
      },
    });

    return {
      taskId: raw.task_id,
      runId: raw.run_id,
      state: raw.state as any,
      goal: raw.goal,
      createdAt: new Date().toISOString(),
    };
  }

  /**
   * Fetch current task state and execution metadata.
   */
  public async getTask(taskId: string): Promise<TaskRecord> {
    const raw = await this.request<any>(`/tasks/${encodeURIComponent(taskId)}`);
    return {
      taskId: raw.task_id || taskId,
      runId: raw.run_id || "",
      state: raw.state,
      goal: raw.goal,
      createdAt: raw.created_at,
      updatedAt: raw.updated_at,
      error: raw.error,
      sideEffectState: raw.side_effect_state,
      navigationEpoch: raw.navigation_epoch,
      stepsExecuted: raw.steps_executed,
      approvalRequired: raw.approval_required
        ? {
            eventId: raw.approval_required.event_id,
            action: raw.approval_required.action,
            target: raw.approval_required.target,
            screenshotHash: raw.approval_required.screenshot_hash,
            actionFingerprint: raw.approval_required.action_fingerprint,
          }
        : null,
    };
  }

  /**
   * Request a running task to pause at the next safe boundary.
   */
  public async pauseTask(taskId: string): Promise<{ status: string; taskId: string }> {
    const res = await this.request<{ status: string; task_id: string }>(
      `/tasks/${encodeURIComponent(taskId)}/pause`,
      { method: "POST" }
    );
    return { status: res.status, taskId: res.task_id };
  }

  /**
   * Resume a paused task (verifies approvals if in awaiting_review).
   */
  public async resumeTask(taskId: string): Promise<{ status: string; taskId: string; message?: string }> {
    const res = await this.request<{ status: string; task_id: string; message?: string }>(
      `/tasks/${encodeURIComponent(taskId)}/resume`,
      { method: "POST" }
    );
    return { status: res.status, taskId: res.task_id, message: res.message };
  }

  /**
   * Request graceful stop/cancellation of a task.
   */
  public async stopTask(taskId: string): Promise<{ status: string; taskId: string }> {
    const res = await this.request<{ status: string; task_id: string }>(
      `/tasks/${encodeURIComponent(taskId)}/stop`,
      { method: "POST" }
    );
    return { status: res.status, taskId: res.task_id };
  }

  /**
   * Emergency Stop: immediately locks synthetic inputs and halts runner.
   */
  public async emergencyStop(taskId: string): Promise<{ status: string; taskId: string; inputLocked: boolean }> {
    const res = await this.request<{ status: string; task_id: string; input_locked: boolean }>(
      `/tasks/${encodeURIComponent(taskId)}/emergency-stop`,
      { method: "POST" }
    );
    return { status: res.status, taskId: res.task_id, inputLocked: res.input_locked };
  }

  /**
   * Reset task emergency stop lock.
   */
  public async resetTask(taskId: string): Promise<{ status: string; taskId: string; inputLocked: boolean }> {
    const res = await this.request<{ status: string; task_id: string; input_locked: boolean }>(
      `/tasks/${encodeURIComponent(taskId)}/reset`,
      { method: "POST" }
    );
    return { status: res.status, taskId: res.task_id, inputLocked: res.input_locked };
  }

  /**
   * Grant human approval for a gated interaction step.
   */
  public async approveAction(
    taskId: string,
    eventId: string,
    payload: ApprovalPayload
  ): Promise<{ status: string; approval: unknown }> {
    const res = await this.request<any>(
      `/tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(eventId)}`,
      {
        method: "POST",
        body: {
          step: payload.step,
          action: payload.action,
          target: payload.target,
          screenshot_hash: payload.screenshotHash,
          action_fingerprint: payload.actionFingerprint,
        },
      }
    );
    return { status: res.status, approval: res.approval };
  }

  /**
   * Grant cryptographically verified approval for consequential WebMCP tools.
   */
  public async approveWebMCPAction(
    taskId: string,
    payload: WebMCPApprovalPayload
  ): Promise<{ status: string; approvalId: string }> {
    const res = await this.request<any>(
      `/tasks/${encodeURIComponent(taskId)}/webmcp-approvals`,
      {
        method: "POST",
        body: {
          event_id: payload.eventId || `evt_${Date.now()}`,
          origin: payload.origin,
          tool_name: payload.toolName,
          schema_hash: payload.schemaHash,
          arguments_hash: payload.argumentsHash,
          navigation_epoch: payload.navigationEpoch,
          ttl_seconds: payload.ttlSeconds ?? 300,
          single_use: payload.singleUse ?? true,
          approved_by: payload.approvedBy ?? "openmausbot-operator",
        },
      }
    );
    return { status: res.status, approvalId: res.approval_id };
  }

  /**
   * Subscribe to real-time Server-Sent Events (SSE) stream for a task.
   * Returns a teardown function to safely close the connection.
   */
  public subscribeTaskEvents(
    taskId: string,
    onEvent: (event: TaskStreamEvent) => void,
    onError?: (err: Error) => void
  ): () => void {
    const url = `${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/stream`;
    const controller = new AbortController();

    (async () => {
      try {
        const response = await fetch(url, {
          headers: {
            "Accept": "text/event-stream",
            "Authorization": `Bearer ${this.authToken}`,
          },
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          throw new TypeSafeError(`SSE subscription failed with HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith("data:")) {
              const dataStr = trimmed.slice(5).trim();
              if (dataStr) {
                try {
                  const parsed = JSON.parse(dataStr);
                  onEvent({
                    type: parsed.type || "status",
                    taskId: parsed.task_id || taskId,
                    timestamp: parsed.timestamp || Date.now(),
                    data: parsed,
                  });
                } catch {
                  // Ignore heartbeat or non-JSON payloads
                }
              }
            }
          }
        }
      } catch (err: unknown) {
        if (!controller.signal.aborted && onError) {
          onError(err instanceof Error ? err : new Error(String(err)));
        }
      }
    })();

    return () => {
      controller.abort();
    };
  }

  /**
   * Return VNC screen sharing URL for live human supervision.
   */
  public getVncUrl(port: number = 5900): string {
    return `vnc://${this.vncHost}:${port}`;
  }
}
