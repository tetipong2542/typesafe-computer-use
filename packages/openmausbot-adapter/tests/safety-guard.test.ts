/**
 * @fileoverview Unit tests for SafetyGuard.
 */

import { describe, test, expect, beforeAll, afterAll } from "bun:test";
import { SafetyGuard } from "../src/safety-guard.js";
import { TypeSafeWorkerClient } from "../src/client.js";
import { createMockWorkerServer, MockWorkerServer } from "./mock-worker.js";
import { UnknownSideEffectError } from "../src/errors.js";

describe("SafetyGuard", () => {
  let server: MockWorkerServer;
  let client: TypeSafeWorkerClient;
  let guard: SafetyGuard;

  beforeAll(async () => {
    server = await createMockWorkerServer();
    client = new TypeSafeWorkerClient({
      baseUrl: server.url,
      authToken: server.authToken,
    });
    guard = new SafetyGuard(client);
  });

  afterAll(async () => {
    await server.close();
  });

  test("assertSafeTransition throws UnknownSideEffectError when entering unknown state", () => {
    expect(() => {
      guard.assertSafeTransition("in_progress", "unknown", "submit_payment", "#checkout");
    }).toThrow(UnknownSideEffectError);
  });

  test("assertSafeTransition forbids moving away from unknown state without reset", () => {
    expect(() => {
      guard.assertSafeTransition("unknown", "in_progress", "retry_payment", "#checkout");
    }).toThrow(UnknownSideEffectError);
  });

  test("processTaskGating handles approved action callback", async () => {
    const task = await client.createTask({ goal: "Gated task test" });
    server.simulateGatedAction(task.taskId, "evt_999");

    const currentTask = await client.getTask(task.taskId);

    const outcome = await guard.processTaskGating(currentTask, async (details) => {
      expect(details.action).toBe("delete_database");
      expect(details.target).toBe("prod_db");
      return { approved: true, reason: "Operator verified" };
    });

    expect(outcome.status).toBe("approved");

    const after = await client.getTask(task.taskId);
    expect(after.state).toBe("running");
  });

  test("processTaskGating handles rejected action callback and stops task", async () => {
    const task = await client.createTask({ goal: "Reject task test" });
    server.simulateGatedAction(task.taskId, "evt_888");

    const currentTask = await client.getTask(task.taskId);

    const outcome = await guard.processTaskGating(currentTask, async (details) => {
      return { approved: false, reason: "Forbidden operation" };
    });

    expect(outcome.status).toBe("rejected");

    const after = await client.getTask(task.taskId);
    expect(after.state).toBe("failed");
  });
});
