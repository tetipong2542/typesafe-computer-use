/**
 * @fileoverview Unit tests for TypeSafeWorkerClient.
 */

import { describe, test, expect, beforeAll, afterAll } from "bun:test";
import { TypeSafeWorkerClient } from "../src/client.js";
import { createMockWorkerServer, MockWorkerServer } from "./mock-worker.js";
import { WorkerAuthError, TaskNotFoundError } from "../src/errors.js";

describe("TypeSafeWorkerClient", () => {
  let server: MockWorkerServer;
  let client: TypeSafeWorkerClient;

  beforeAll(async () => {
    server = await createMockWorkerServer();
    client = new TypeSafeWorkerClient({
      baseUrl: server.url,
      authToken: server.authToken,
    });
  });

  afterAll(async () => {
    await server.close();
  });

  test("healthCheck returns service status", async () => {
    const health = await client.healthCheck();
    expect(health.status).toBe("ok");
    expect(health.service).toBe("typesafe-computer-worker");
  });

  test("rejects request when auth token is invalid", async () => {
    const badClient = new TypeSafeWorkerClient({
      baseUrl: server.url,
      authToken: "wrong-token",
    });

    expect(badClient.createTask({ goal: "test" })).rejects.toThrow(WorkerAuthError);
  });

  test("createTask submits a new task successfully", async () => {
    const task = await client.createTask({
      goal: "Search directory resources",
      act: true,
      steps: 5,
    });

    expect(task.taskId).toBeDefined();
    expect(task.state).toBe("running");
    expect(task.goal).toBe("Search directory resources");
  });

  test("getTask returns 404 for unknown task", async () => {
    expect(client.getTask("non_existent_task_id")).rejects.toThrow(TaskNotFoundError);
  });

  test("task lifecycle: pause, resume, stop", async () => {
    const task = await client.createTask({ goal: "Lifecycle task" });

    // Pause
    const pauseRes = await client.pauseTask(task.taskId);
    expect(pauseRes.status).toBe("pause_requested");
    let current = await client.getTask(task.taskId);
    expect(current.state).toBe("paused");

    // Resume
    const resumeRes = await client.resumeTask(task.taskId);
    expect(resumeRes.status).toBe("resumed");
    current = await client.getTask(task.taskId);
    expect(current.state).toBe("running");

    // Stop
    const stopRes = await client.stopTask(task.taskId);
    expect(stopRes.status).toBe("stopping");
    current = await client.getTask(task.taskId);
    expect(current.state).toBe("failed");
  });

  test("emergencyStop locks input and reset releases lock", async () => {
    const task = await client.createTask({ goal: "Emergency task" });

    const emRes = await client.emergencyStop(task.taskId);
    expect(emRes.inputLocked).toBe(true);
    let current = await client.getTask(task.taskId);
    expect(current.state).toBe("emergency_stopped");

    const resetRes = await client.resetTask(task.taskId);
    expect(resetRes.inputLocked).toBe(false);
    current = await client.getTask(task.taskId);
    expect(current.state).toBe("queued");
  });

  test("approveAction and approveWebMCPAction record approvals", async () => {
    const task = await client.createTask({ goal: "Approval task" });

    const appRes = await client.approveAction(task.taskId, "evt_101", {
      step: 1,
      action: "click",
      target: "confirm_button",
      screenshotHash: "hash_123",
      actionFingerprint: "fp_123",
    });
    expect(appRes.status).toBe("approved");

    const webmcpRes = await client.approveWebMCPAction(task.taskId, {
      origin: "http://example.com",
      toolName: "checkout_order",
      schemaHash: "sch_123",
      argumentsHash: "arg_123",
      navigationEpoch: 1,
    });
    expect(webmcpRes.status).toBe("approved");
    expect(webmcpRes.approvalId).toBeDefined();
  });

  test("subscribeTaskEvents receives real-time SSE messages", async () => {
    const task = await client.createTask({ goal: "Streaming task" });
    const events: any[] = [];

    const unsubscribe = client.subscribeTaskEvents(task.taskId, (ev) => {
      events.push(ev);
    });

    // Allow connection to establish
    await new Promise((r) => setTimeout(r, 50));

    // Simulate completion event
    server.simulateTaskCompletion(task.taskId);
    await new Promise((r) => setTimeout(r, 100));

    unsubscribe();
    expect(events.length).toBeGreaterThan(0);
    const completedEv = events.find((e) => e.type === "completed");
    expect(completedEv).toBeDefined();
  });
});
