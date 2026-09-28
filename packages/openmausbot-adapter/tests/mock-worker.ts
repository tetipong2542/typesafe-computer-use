/**
 * @fileoverview In-memory Mock Worker Server for zero-cost OpenMausBot testing.
 * Runs on Bun.serve without spinning up VMs or making real external API calls.
 */

export interface MockWorkerServer {
  url: string;
  authToken: string;
  close: () => Promise<void>;
  simulateTaskCompletion: (taskId: string) => void;
  simulateGatedAction: (taskId: string, eventId: string) => void;
  simulateUnknownSideEffect: (taskId: string) => void;
  completeAllTasks: () => void;
  getTask: (taskId: string) => any;
}

export async function createMockWorkerServer(port: number = 0): Promise<MockWorkerServer> {
  const authToken = "mock-secret-worker-token-xyz";
  const tasks = new Map<string, any>();
  const sseListeners = new Map<string, Set<(data: string) => void>>();

  const server = Bun.serve({
    port,
    async fetch(req) {
      const url = new URL(req.url);
      const path = url.pathname;
      const auth = req.headers.get("authorization");

      // 1. Health Probe (unauthenticated)
      if (path === "/healthz" && req.method === "GET") {
        return Response.json({ status: "ok", service: "typesafe-computer-worker" });
      }

      // Check Bearer Auth for all other routes
      if (!auth || auth !== `Bearer ${authToken}`) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }

      // 2. POST /tasks
      if (path === "/tasks" && req.method === "POST") {
        const body = (await req.json()) as any;
        const taskId = `task_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
        const record = {
          task_id: taskId,
          run_id: `run_${Date.now()}`,
          state: "running",
          goal: body.goal || "test goal",
          created_at: new Date().toISOString(),
          steps_executed: 0,
          side_effect_state: "in_progress",
          approval_required: null,
          error: null,
          executed_mode: "browser_dom",
        };
        tasks.set(taskId, record);
        return Response.json(record);
      }

      // 3. GET /tasks/:id
      const getTaskMatch = path.match(/^\/tasks\/([^/]+)$/);
      if (getTaskMatch && req.method === "GET") {
        const taskId = getTaskMatch[1];
        const record = tasks.get(taskId);
        if (!record) {
          return new Response(JSON.stringify({ detail: "Task not found" }), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          });
        }
        return Response.json(record);
      }

      // 4. POST /tasks/:id/pause
      const pauseMatch = path.match(/^\/tasks\/([^/]+)\/pause$/);
      if (pauseMatch && req.method === "POST") {
        const taskId = pauseMatch[1];
        const record = tasks.get(taskId);
        if (record) record.state = "paused";
        return Response.json({ status: "pause_requested", task_id: taskId });
      }

      // 5. POST /tasks/:id/resume
      const resumeMatch = path.match(/^\/tasks\/([^/]+)\/resume$/);
      if (resumeMatch && req.method === "POST") {
        const taskId = resumeMatch[1];
        const record = tasks.get(taskId);
        if (record) {
          record.state = "running";
          record.approval_required = null;
        }
        return Response.json({ status: "resumed", task_id: taskId });
      }

      // 6. POST /tasks/:id/stop
      const stopMatch = path.match(/^\/tasks\/([^/]+)\/stop$/);
      if (stopMatch && req.method === "POST") {
        const taskId = stopMatch[1];
        const record = tasks.get(taskId);
        if (record) {
          record.state = "failed";
          record.error = "Cancelled by user";
        }
        return Response.json({ status: "stopping", task_id: taskId });
      }

      // 7. POST /tasks/:id/emergency-stop
      const emStopMatch = path.match(/^\/tasks\/([^/]+)\/emergency-stop$/);
      if (emStopMatch && req.method === "POST") {
        const taskId = emStopMatch[1];
        const record = tasks.get(taskId);
        if (record) record.state = "emergency_stopped";
        return Response.json({ status: "emergency_stopped", task_id: taskId, input_locked: true });
      }

      // 8. POST /tasks/:id/reset
      const resetMatch = path.match(/^\/tasks\/([^/]+)\/reset$/);
      if (resetMatch && req.method === "POST") {
        const taskId = resetMatch[1];
        const record = tasks.get(taskId);
        if (record) {
          record.state = "queued";
          record.side_effect_state = "not_started";
        }
        return Response.json({ status: "reset", task_id: taskId, input_locked: false });
      }

      // 9. POST /tasks/:id/approvals/:eventId
      const approvalMatch = path.match(/^\/tasks\/([^/]+)\/approvals\/([^/]+)$/);
      if (approvalMatch && req.method === "POST") {
        const taskId = approvalMatch[1];
        const eventId = approvalMatch[2];
        const body = await req.json();
        return Response.json({
          status: "approved",
          task_id: taskId,
          event_id: eventId,
          approval: body,
        });
      }

      // 10. POST /tasks/:id/webmcp-approvals
      const webmcpApprovalMatch = path.match(/^\/tasks\/([^/]+)\/webmcp-approvals$/);
      if (webmcpApprovalMatch && req.method === "POST") {
        const taskId = webmcpApprovalMatch[1];
        return Response.json({ status: "approved", task_id: taskId, approval_id: `appr_${Date.now()}` });
      }

      // 11. GET /tasks/:id/stream (SSE)
      const streamMatch = path.match(/^\/tasks\/([^/]+)\/stream$/);
      if (streamMatch && req.method === "GET") {
        const taskId = streamMatch[1];
        let interval: any;

        const bodyStream = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "connected", task_id: taskId })}\n\n`));

            if (!sseListeners.has(taskId)) {
              sseListeners.set(taskId, new Set());
            }

            const listener = (data: string) => {
              try {
                controller.enqueue(new TextEncoder().encode(`data: ${data}\n\n`));
              } catch {}
            };
            sseListeners.get(taskId)!.add(listener);

            interval = setInterval(() => {
              try {
                controller.enqueue(new TextEncoder().encode(`: ping\n\n`));
              } catch {}
            }, 5000);
          },
          cancel() {
            clearInterval(interval);
          },
        });

        return new Response(bodyStream, {
          headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
          },
        });
      }

      // 12. POST /mcp (Model Context Protocol JSON-RPC)
      if (path === "/mcp" && req.method === "POST") {
        const body = (await req.json()) as any;
        const toolName = body.params?.name;
        const toolArgs = body.params?.arguments || {};

        let toolPayload: any;
        if (toolName === "browser_navigate") {
          toolPayload = {
            success: true,
            data: { url: toolArgs.url },
            side_effect_state: "confirmed_success",
            navigation_epoch: 1,
          };
        } else if (toolName === "browser_click") {
          toolPayload = {
            success: true,
            data: { clicked: toolArgs.target },
            side_effect_state: "confirmed_success",
            navigation_epoch: 1,
          };
        } else if (toolName === "browser_fill") {
          toolPayload = {
            success: true,
            data: { filled: toolArgs.target, value: toolArgs.value },
            side_effect_state: "confirmed_success",
            navigation_epoch: 1,
          };
        } else if (toolName === "browser_screenshot") {
          toolPayload = {
            success: true,
            data: { screenshotBase64: "fake_png_base64_data" },
            side_effect_state: "confirmed_success",
            screenshot: "fake_png_base64_data",
          };
        } else if (toolName === "browser_status") {
          toolPayload = {
            success: true,
            data: {
              url: "http://localhost:3000/dashboard",
              title: "Test Dashboard",
              navigation_epoch: 1,
              ready: true,
            },
            side_effect_state: "confirmed_success",
          };
        } else {
          toolPayload = {
            success: false,
            error: `Unknown tool: ${toolName}`,
            side_effect_state: "confirmed_failure",
          };
        }

        return Response.json({
          jsonrpc: "2.0",
          id: body.id,
          result: {
            content: [
              {
                type: "text",
                text: JSON.stringify(toolPayload),
              },
            ],
            isError: !toolPayload.success,
          },
        });
      }

      return new Response("Not Found", { status: 404 });
    },
  });

  const workerUrl = `http://127.0.0.1:${server.port}`;

  return {
    url: workerUrl,
    authToken,
    close: async () => {
      server.stop(true);
    },
    simulateTaskCompletion: (taskId: string) => {
      const t = tasks.get(taskId);
      if (t) {
        t.state = "completed";
        t.steps_executed = 3;
        t.side_effect_state = "confirmed_success";
        const listeners = sseListeners.get(taskId);
        if (listeners) {
          const payload = JSON.stringify({ type: "completed", task_id: taskId, status: "completed" });
          listeners.forEach((l) => l(payload));
        }
      }
    },
    simulateGatedAction: (taskId: string, eventId: string) => {
      const t = tasks.get(taskId);
      if (t) {
        t.state = "awaiting_review";
        t.approval_required = {
          event_id: eventId,
          action: "delete_database",
          target: "prod_db",
          screenshot_hash: "hash_abc_123",
          action_fingerprint: "fingerprint_xyz",
        };
        const listeners = sseListeners.get(taskId);
        if (listeners) {
          const payload = JSON.stringify({
            type: "action_gated",
            task_id: taskId,
            event_id: eventId,
            action: "delete_database",
          });
          listeners.forEach((l) => l(payload));
        }
      }
    },
    simulateUnknownSideEffect: (taskId: string) => {
      const t = tasks.get(taskId);
      if (t) {
        t.state = "failed";
        t.side_effect_state = "unknown";
        t.error = "Connection lost during transaction commit";
      }
    },
    completeAllTasks: () => {
      for (const [id] of tasks) {
        const t = tasks.get(id);
        if (t && t.state === "running") {
          t.state = "completed";
          t.side_effect_state = "confirmed_success";
          t.steps_executed = 3;
        }
      }
    },
    getTask: (taskId: string) => tasks.get(taskId),
  };
}
