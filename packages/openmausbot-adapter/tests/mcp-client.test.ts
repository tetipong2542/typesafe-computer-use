/**
 * @fileoverview Unit tests for TypeSafeMCPClient.
 */

import { describe, test, expect, beforeAll, afterAll } from "bun:test";
import { TypeSafeMCPClient } from "../src/mcp-client.js";
import { createMockWorkerServer, MockWorkerServer } from "./mock-worker.js";
import { UnknownSideEffectError, WorkerAuthError } from "../src/errors.js";

describe("TypeSafeMCPClient", () => {
  let server: MockWorkerServer;
  let client: TypeSafeMCPClient;

  beforeAll(async () => {
    server = await createMockWorkerServer();
    client = new TypeSafeMCPClient({
      baseUrl: server.url,
      authToken: server.authToken,
    });
  });

  afterAll(async () => {
    await server.close();
  });

  test("rejects when authentication is invalid", async () => {
    const badClient = new TypeSafeMCPClient({
      baseUrl: server.url,
      authToken: "invalid-token",
    });
    expect(badClient.browserNavigate("http://example.com")).rejects.toThrow(WorkerAuthError);
  });

  test("browserNavigate executes and returns success", async () => {
    const res = await client.browserNavigate("http://localhost:3000");
    expect(res.success).toBe(true);
    expect(res.sideEffectState).toBe("confirmed_success");
    expect((res.data as any).url).toBe("http://localhost:3000");
  });

  test("browserClick executes with selector", async () => {
    const res = await client.browserClick("button.submit-btn", { role: "button" });
    expect(res.success).toBe(true);
    expect((res.data as any).clicked).toBe("button.submit-btn");
  });

  test("browserFill inputs text value", async () => {
    const res = await client.browserFill("input#search", "Developer tools");
    expect(res.success).toBe(true);
    expect((res.data as any).filled).toBe("input#search");
    expect((res.data as any).value).toBe("Developer tools");
  });

  test("browserScreenshot captures viewport screenshot", async () => {
    const res = await client.browserScreenshot();
    expect(res.success).toBe(true);
    expect(res.screenshotBase64).toBe("fake_png_base64_data");
  });

  test("browserStatus checks active tab status", async () => {
    const res = await client.browserStatus();
    expect(res.success).toBe(true);
    expect(res.data?.ready).toBe(true);
    expect(res.data?.title).toBe("Test Dashboard");
  });
});
