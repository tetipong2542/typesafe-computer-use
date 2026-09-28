/**
 * @fileoverview Unit tests for OpenMausBotOrchestrator.
 */

import { describe, test, expect, beforeAll, afterAll } from "bun:test";
import { OpenMausBotOrchestrator } from "../src/orchestrator.js";
import { TypeSafeWorkerClient } from "../src/client.js";
import { TypeSafeMCPClient } from "../src/mcp-client.js";
import { createMockWorkerServer, MockWorkerServer } from "./mock-worker.js";
import { UnknownSideEffectError, EmergencyStoppedError } from "../src/errors.js";

describe("OpenMausBotOrchestrator", () => {
  let server: MockWorkerServer;
  let workerClient: TypeSafeWorkerClient;
  let mcpClient: TypeSafeMCPClient;

  beforeAll(async () => {
    server = await createMockWorkerServer();
    workerClient = new TypeSafeWorkerClient({
      baseUrl: server.url,
      authToken: server.authToken,
    });
    mcpClient = new TypeSafeMCPClient({
      baseUrl: server.url,
      authToken: server.authToken,
    });
  });

  afterAll(async () => {
    await server.close();
  });

  test("executes goal to completion", async () => {
    const orchestrator = new OpenMausBotOrchestrator({
      workerClient,
      mcpClient,
      pollIntervalMs: 30,
      maxWaitTimeoutMs: 2000,
    });

    const goalPromise = orchestrator.executeGoal("Find Figma tool");

    const timer = setInterval(() => {
      server.completeAllTasks();
    }, 40);

    const result = await goalPromise;
    clearInterval(timer);

    expect(result.success).toBe(true);
    expect(result.finalAnswer).toBe("Goal achieved: Find Figma tool");
  });

  test("halts immediately on unknown side effect state", async () => {
    const orchestrator = new OpenMausBotOrchestrator({
      workerClient,
      mcpClient,
      pollIntervalMs: 50,
      maxWaitTimeoutMs: 2000,
    });

    const task = await workerClient.createTask({ goal: "Unknown state task" });
    server.simulateUnknownSideEffect(task.taskId);

    const current = await workerClient.getTask(task.taskId);
    expect(current.sideEffectState).toBe("unknown");

    // Safety guard must reject
    expect(() => {
      orchestrator.safetyGuard.assertSafeTransition("in_progress", current.sideEffectState as any, "commit", "#btn");
    }).toThrow(UnknownSideEffectError);
  });

  test("emergencyStop locks active task", async () => {
    const orchestrator = new OpenMausBotOrchestrator({
      workerClient,
      mcpClient,
    });

    const task = await workerClient.createTask({ goal: "Stop immediately" });
    const locked = await orchestrator.emergencyStop(task.taskId);
    expect(locked).toBe(true);

    const after = await workerClient.getTask(task.taskId);
    expect(after.state).toBe("emergency_stopped");
  });
});
