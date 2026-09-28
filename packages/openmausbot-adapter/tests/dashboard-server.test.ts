/**
 * @fileoverview Test suite for OpenMausBot Dashboard Web Server.
 * Validates static asset delivery, status probe, task lifecycle, SSE streaming,
 * emergency controls, and MCP execution endpoints.
 */

import { describe, expect, it } from "bun:test";
import { handleRequest } from "../src/dashboard/server.js";

describe("OpenMausBot Dashboard Server", () => {
  it("serves the dashboard HTML on GET /", async () => {
    const req = new Request("http://localhost:3000/");
    const res = await handleRequest(req);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toContain("text/html");
    const html = await res.text();
    expect(html).toContain("OpenMausBot");
    expect(html).toContain("TypeSafe Engine");
  });

  it("returns system status on GET /api/status", async () => {
    const req = new Request("http://localhost:3000/api/status");
    const res = await handleRequest(req);

    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(typeof data.online).toBe("boolean");
    expect(data.baseUrl).toBeDefined();
    expect(data.vncUrl).toContain("vnc://");
  });

  it("creates a new task on POST /api/tasks", async () => {
    const req = new Request("http://localhost:3000/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: "Find Figma in directory",
        mode: "auto",
      }),
    });
    const res = await handleRequest(req);

    expect(res.status).toBe(201);
    const data = (await res.json()) as any;
    expect(data.taskId).toBeDefined();
    expect(data.goal).toBe("Find Figma in directory");
  });

  it("handles emergency stop and reset on /api/tasks/:id", async () => {
    const stopReq = new Request("http://localhost:3000/api/tasks/sim_123/emergency-stop", {
      method: "POST",
    });
    const stopRes = await handleRequest(stopReq);
    expect(stopRes.status).toBe(200);
    const stopData = (await stopRes.json()) as any;
    expect(stopData.inputLocked).toBe(true);

    const resetReq = new Request("http://localhost:3000/api/tasks/sim_123/reset", {
      method: "POST",
    });
    const resetRes = await handleRequest(resetReq);
    expect(resetRes.status).toBe(200);
    const resetData = (await resetRes.json()) as any;
    expect(resetData.inputLocked).toBe(false);
  });

  it("handles human-in-the-loop approvals on /api/tasks/:id/approvals/:eventId", async () => {
    const req = new Request("http://localhost:3000/api/tasks/sim_123/approvals/evt_pay_99", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        approved: true,
        action: "submit_payment",
      }),
    });
    const res = await handleRequest(req);

    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.status).toBe("ok");
    expect(data.eventId).toBe("evt_pay_99");
  });

  it("executes simulated MCP tools on POST /api/mcp/execute", async () => {
    // 1. browser_navigate
    const navReq = new Request("http://localhost:3000/api/mcp/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool: "browser_navigate",
        args: { url: "https://example.com" },
      }),
    });
    const navRes = await handleRequest(navReq);
    expect(navRes.status).toBe(200);
    const navData = (await navRes.json()) as any;
    expect(navData.success).toBe(true);

    // 2. browser_screenshot
    const shotReq = new Request("http://localhost:3000/api/mcp/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool: "browser_screenshot",
        args: {},
      }),
    });
    const shotRes = await handleRequest(shotReq);
    expect(shotRes.status).toBe(200);
    const shotData = (await shotRes.json()) as any;
    expect(shotData.success).toBe(true);
    expect(shotData.data.screenshot_data_url).toContain("data:image/svg+xml");
  });

  it("streams simulated events on GET /api/tasks/:id/stream", async () => {
    const req = new Request("http://localhost:3000/api/tasks/sim_test_stream/stream");
    const res = await handleRequest(req);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("text/event-stream");
    expect(res.body).toBeDefined();

    // Read the first chunk from the stream
    const reader = res.body!.getReader();
    const { value, done } = await reader.read();
    expect(done).toBe(false);

    const chunk = new TextDecoder().decode(value);
    expect(chunk).toContain("task_update");
    await reader.cancel();
  });
});
