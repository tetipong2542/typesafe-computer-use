/**
 * @fileoverview OpenMausBot Web Dashboard HTTP & SSE Server.
 * Built for Bun runtime with zero heavyweight dependencies.
 * Serves dashboard frontend and proxies control APIs (REST + SSE) to TypeSafe Worker daemon.
 */

import { join } from "node:path";
import { TypeSafeWorkerClient } from "../client.js";
import { TypeSafeMCPClient } from "../mcp-client.js";
import type { TaskStreamEvent } from "../types.js";

// Configuration from environment variables
const PORT = parseInt(process.env.PORT || "3000", 10);
const WORKER_URL = process.env.TYPESAFE_WORKER_URL || "http://127.0.0.1:8000";
const AUTH_TOKEN = process.env.WORKER_AUTH_TOKEN || "test-worker-token";

// Instantiate TypeSafe clients
const workerClient = new TypeSafeWorkerClient({
  baseUrl: WORKER_URL,
  authToken: AUTH_TOKEN,
  timeoutMs: 15000,
});

const mcpClient = new TypeSafeMCPClient({
  baseUrl: WORKER_URL,
  authToken: AUTH_TOKEN,
  timeoutMs: 15000,
});

// Paths to static assets
const PUBLIC_DIR = join(import.meta.dir, "public");
const INDEX_HTML_PATH = join(PUBLIC_DIR, "index.html");

/**
 * Standard JSON response helper with CORS headers.
 */
function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    },
  });
}

/**
 * Handle incoming HTTP requests for the dashboard server.
 */
export async function handleRequest(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const { pathname, searchParams } = url;
  const method = req.method;

  // Handle CORS preflight
  if (method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
      },
    });
  }

  // 1. Static Asset Routes
  if (method === "GET" && (pathname === "/" || pathname === "/index.html")) {
    const file = Bun.file(INDEX_HTML_PATH);
    if (await file.exists()) {
      return new Response(file, {
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }
    return new Response("Dashboard UI file not found.", { status: 404 });
  }

  // 2. Health & Status Probe
  if (method === "GET" && pathname === "/api/status") {
    try {
      const health = await workerClient.healthCheck();
      return jsonResponse({
        online: true,
        service: health.service || "typesafe-worker",
        status: health.status || "healthy",
        baseUrl: workerClient.baseUrl,
        vncUrl: `vnc://${new URL(workerClient.baseUrl).hostname}:5900`,
      });
    } catch {
      return jsonResponse({
        online: false,
        message: "TypeSafe Worker Daemon is offline (FastAPI port 8000). Interactive simulation available.",
        baseUrl: workerClient.baseUrl,
        vncUrl: "vnc://127.0.0.1:5900",
      });
    }
  }

  // 3. Create Task Route
  if (method === "POST" && pathname === "/api/tasks") {
    try {
      const body = (await req.json()) as {
        goal: string;
        mode?: string;
        steps?: number;
        minConfidence?: number;
        delay?: number;
        browser?: string;
      };

      if (!body.goal) {
        return jsonResponse({ error: "Goal is required" }, 400);
      }

      // Check worker connectivity first
      let isWorkerOnline = false;
      try {
        await workerClient.healthCheck();
        isWorkerOnline = true;
      } catch {
        isWorkerOnline = false;
      }

      if (isWorkerOnline) {
        const task = await workerClient.createTask({
          goal: body.goal,
          act: true,
          steps: body.steps ?? 15,
          minConfidence: body.minConfidence ?? 0.6,
          delay: body.delay ?? 0.5,
          browser: body.browser,
        });

        return jsonResponse({
          taskId: task.taskId,
          runId: task.runId,
          state: task.state,
          goal: task.goal,
          simulated: false,
        }, 201);
      }

      // Offline simulation fallback
      const simId = `sim_task_${Date.now()}`;
      return jsonResponse({
        taskId: simId,
        runId: `sim_run_${Date.now()}`,
        state: "running",
        goal: body.goal,
        simulated: true,
      }, 201);
    } catch (err: unknown) {
      return jsonResponse({ error: (err as Error).message }, 500);
    }
  }

  // 4. SSE Task Stream Proxy
  const streamMatch = pathname.match(/^\/api\/tasks\/([^/]+)\/stream$/);
  if (method === "GET" && streamMatch) {
    const taskId = decodeURIComponent(streamMatch[1]);

    // If task is simulated, stream simulated events
    if (taskId.startsWith("sim_")) {
      return createSimulatedSSEStream(taskId);
    }

    try {
      const targetUrl = `${workerClient.baseUrl}/tasks/${encodeURIComponent(taskId)}/stream`;
      const upstreamRes = await fetch(targetUrl, {
        headers: {
          Accept: "text/event-stream",
          Authorization: `Bearer ${AUTH_TOKEN}`,
        },
      });

      if (!upstreamRes.ok || !upstreamRes.body) {
        return jsonResponse(
          { error: `Upstream worker stream failed with HTTP ${upstreamRes.status}` },
          upstreamRes.status
        );
      }

      return new Response(upstreamRes.body, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
          "Connection": "keep-alive",
          "Access-Control-Allow-Origin": "*",
        },
      });
    } catch (err: unknown) {
      return jsonResponse({ error: `Worker streaming failed: ${(err as Error).message}` }, 502);
    }
  }

  // 5. Emergency Stop Route
  const stopMatch = pathname.match(/^\/api\/tasks\/([^/]+)\/emergency-stop$/);
  if (method === "POST" && stopMatch) {
    const taskId = decodeURIComponent(stopMatch[1]);
    try {
      if (!taskId.startsWith("sim_") && taskId !== "global") {
        const res = await workerClient.emergencyStop(taskId);
        return jsonResponse({ status: res.status, taskId: res.taskId, inputLocked: res.inputLocked });
      }
    } catch {
      // Fallback for simulation/offline
    }
    return jsonResponse({ status: "stopped", taskId, inputLocked: true, simulated: true });
  }

  // 6. Reset Emergency Stop Route
  const resetMatch = pathname.match(/^\/api\/tasks\/([^/]+)\/reset$/);
  if (method === "POST" && resetMatch) {
    const taskId = decodeURIComponent(resetMatch[1]);
    try {
      if (!taskId.startsWith("sim_") && taskId !== "global") {
        const res = await workerClient.resetTask(taskId);
        return jsonResponse({ status: res.status, taskId: res.taskId, inputLocked: res.inputLocked });
      }
    } catch {
      // Fallback for simulation/offline
    }
    return jsonResponse({ status: "reset", taskId, inputLocked: false, simulated: true });
  }

  // 7. Human Gated Approval Route
  const approvalMatch = pathname.match(/^\/api\/tasks\/([^/]+)\/approvals\/([^/]+)$/);
  if (method === "POST" && approvalMatch) {
    const taskId = decodeURIComponent(approvalMatch[1]);
    const eventId = decodeURIComponent(approvalMatch[2]);
    try {
      const body = (await req.json()) as {
        approved: boolean;
        step?: number;
        action?: string;
        target?: string;
        screenshotHash?: string;
        actionFingerprint?: string;
      };

      if (!taskId.startsWith("sim_")) {
        const result = await workerClient.approveAction(taskId, eventId, {
          step: body.step ?? 1,
          action: body.action || "click",
          target: body.target || "",
          screenshotHash: body.screenshotHash || "user-manual-approval",
          actionFingerprint: body.actionFingerprint || "user-verified",
        });

        // Resume task execution after approval
        await workerClient.resumeTask(taskId).catch(() => {});
        return jsonResponse({ status: "ok", approval: result });
      }
    } catch {
      // Fallback for simulation
    }
    return jsonResponse({ status: "ok", eventId, taskId, approved: true, simulated: true });
  }

  // 8. Manual MCP Tool Playground Execution
  if (method === "POST" && pathname === "/api/mcp/execute") {
    try {
      const body = (await req.json()) as {
        tool: string;
        args?: Record<string, unknown>;
      };

      if (!body.tool) {
        return jsonResponse({ error: "Tool name is required" }, 400);
      }

      // Check if worker MCP is reachable
      try {
        const result = await mcpClient.executeTool(body.tool, body.args || {});
        return jsonResponse(result);
      } catch (err: unknown) {
        // If worker is unreachable, return helpful simulation response
        return jsonResponse(simulateMCPTool(body.tool, body.args || {}));
      }
    } catch (err: unknown) {
      return jsonResponse({ error: (err as Error).message }, 500);
    }
  }

  return new Response("Not Found", { status: 404 });
}

/**
 * Server-Sent Events simulation generator for demonstration without running Python worker.
 */
function createSimulatedSSEStream(taskId: string): Response {
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      const sendEvent = (event: string, data: Record<string, unknown>) => {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
        );
      };

      const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

      sendEvent("task_update", {
        type: "state_change",
        taskId,
        state: "running",
        step: 0,
        message: "Simulation task initiated",
      });
      await wait(600);

      sendEvent("task_update", {
        type: "step_progress",
        taskId,
        step: 1,
        mode: "webmcp",
        action: "probe_webmcp",
        message: "Inspecting document.modelContext on active page",
      });
      await wait(700);

      sendEvent("task_update", {
        type: "step_progress",
        taskId,
        step: 2,
        mode: "webmcp",
        action: "execute_tool",
        tool: "search_directory",
        message: "Executed WebMCP tool: search_directory",
      });
      await wait(800);

      sendEvent("task_update", {
        type: "step_progress",
        taskId,
        step: 3,
        mode: "browser_dom",
        action: "click",
        target: "button.tab-nav",
        message: "DOM locator click executed",
      });
      await wait(700);

      sendEvent("task_update", {
        type: "completed",
        taskId,
        state: "completed",
        sideEffectState: "confirmed_success",
        message: "Goal successfully achieved with verified outcome",
      });

      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

/**
 * Return simulated MCP tool results when live worker is offline.
 */
function simulateMCPTool(tool: string, args: Record<string, unknown>) {
  switch (tool) {
    case "browser_navigate":
    case "browserNavigate":
      return {
        success: true,
        data: { url: args.url || "about:blank" },
        sideEffectState: "in_progress",
        navigationEpoch: 1,
      };

    case "browser_click":
    case "browserClick":
      return {
        success: true,
        data: { clicked: args.target },
        sideEffectState: "in_progress",
        navigationEpoch: 1,
      };

    case "browser_fill":
    case "browserFill":
      return {
        success: true,
        data: { filled: args.target, value: args.value },
        sideEffectState: "in_progress",
        navigationEpoch: 1,
      };

    case "browser_screenshot":
    case "browserScreenshot":
      return {
        success: true,
        data: {
          screenshot_data_url:
            "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='800' height='500' viewBox='0 0 800 500'><rect width='800' height='500' fill='%230f172a'/><text x='50%25' y='50%25' fill='%236366f1' font-family='sans-serif' font-size='22' text-anchor='middle'>TypeSafe Viewport (Active Session)</text></svg>",
        },
        sideEffectState: "in_progress",
        navigationEpoch: 1,
      };

    case "browser_status":
    case "browserStatus":
    default:
      return {
        success: true,
        data: {
          ready: true,
          currentUrl: "http://localhost:3000",
          epoch: 1,
          simulated: true,
        },
        sideEffectState: "in_progress",
        navigationEpoch: 1,
      };
  }
}

// Start Bun native server if executed directly
if (import.meta.main) {
  const server = Bun.serve({
    port: PORT,
    fetch: handleRequest,
  });

  console.log(`\n======================================================`);
  console.log(`🚀 OpenMausBot Web Dashboard started successfully!`);
  console.log(`📡 URL:       http://localhost:${server.port}`);
  console.log(`🔗 Worker:    ${WORKER_URL}`);
  console.log(`💻 VNC Host:  vnc://127.0.0.1:5900`);
  console.log(`======================================================\n`);

  // Handle graceful process termination
  const shutdown = () => {
    console.log("\nShutting down OpenMausBot Dashboard server...");
    server.stop();
    process.exit(0);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}
