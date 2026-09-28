/**
 * @fileoverview Direct Model Context Protocol (MCP) Client for OpenMausBot.
 * Invokes verified browser automation tools over the TypeSafe MCP HTTP Streamable endpoint.
 * Enforces zero-credential parameter signatures and strict SideEffectState checks.
 */

import { BrowserStatusData, BrowserToolResult, WorkerClientConfig } from "./types.js";
import { UnknownSideEffectError, WorkerAuthError, WorkerConnectionError } from "./errors.js";

export interface MCPToolCallPayload {
  name: string;
  arguments: Record<string, unknown>;
}

export class TypeSafeMCPClient {
  public readonly mcpEndpoint: string;
  private readonly authToken: string;
  private readonly timeoutMs: number;

  constructor(config: WorkerClientConfig = {}) {
    const base = (config.baseUrl || process.env.TYPESAFE_WORKER_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
    this.mcpEndpoint = `${base}/mcp`;
    this.authToken = config.authToken || process.env.WORKER_AUTH_TOKEN || "test-worker-token";
    this.timeoutMs = config.timeoutMs ?? 30000;
  }

  /**
   * Execute an MCP tool via JSON-RPC 2.0 payload over HTTP POST.
   */
  public async executeTool<T = unknown>(name: string, args: Record<string, unknown> = {}): Promise<BrowserToolResult<T>> {
    const payload = {
      jsonrpc: "2.0",
      id: `call_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      method: "tools/call",
      params: {
        name,
        arguments: args,
      },
    };

    let response: Response;
    try {
      response = await fetch(this.mcpEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "Authorization": `Bearer ${this.authToken}`,
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err: unknown) {
      throw new WorkerConnectionError(`Failed to call MCP tool '${name}': ${(err as Error).message}`, err);
    }

    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        throw new WorkerAuthError(`MCP Authorization failed: HTTP ${response.status}`);
      }
      const text = await response.text().catch(() => "");
      throw new Error(`MCP tool call failed with HTTP ${response.status}: ${text}`);
    }

    const json = (await response.json()) as any;
    if (json.error) {
      throw new Error(`MCP returned error: ${json.error.message || JSON.stringify(json.error)}`);
    }

    const content = json.result?.content || [];
    let toolResult: BrowserToolResult<T>;

    // TypeSafe WebMCP tools return structured JSON inside text content
    if (Array.isArray(content) && content.length > 0 && content[0].text) {
      try {
        const parsed = JSON.parse(content[0].text);
        toolResult = {
          success: Boolean(parsed.success),
          data: parsed.data,
          error: parsed.error || null,
          sideEffectState: parsed.side_effect_state || "in_progress",
          navigationEpoch: parsed.navigation_epoch,
          screenshotBase64: parsed.screenshot,
        };
      } catch {
        toolResult = {
          success: !json.result.isError,
          data: content[0].text as unknown as T,
          sideEffectState: "in_progress",
        };
      }
    } else {
      toolResult = {
        success: !json.result?.isError,
        data: json.result as unknown as T,
        sideEffectState: "in_progress",
      };
    }

    // Invariant Enforcement: Never proceed if side-effect state is UNKNOWN
    if (toolResult.sideEffectState === "unknown") {
      throw new UnknownSideEffectError(
        name,
        JSON.stringify(args),
        toolResult.error || "Ambiguous execution outcome detected by TypeSafe gate"
      );
    }

    return toolResult;
  }

  /**
   * Navigate the browser to a destination URL.
   */
  public async browserNavigate(url: string): Promise<BrowserToolResult> {
    return await this.executeTool("browser_navigate", { url });
  }

  /**
   * Click an element on the page using accessible selector, text, or test ID.
   */
  public async browserClick(
    target: string,
    options: { role?: string; name?: string; testId?: string } = {}
  ): Promise<BrowserToolResult> {
    return await this.executeTool("browser_click", {
      target,
      role: options.role,
      name: options.name,
      test_id: options.testId,
    });
  }

  /**
   * Fill text into an interactive input element.
   */
  public async browserFill(target: string, value: string): Promise<BrowserToolResult> {
    return await this.executeTool("browser_fill", { target, value });
  }

  /**
   * Capture a PNG screenshot of the current viewport.
   */
  public async browserScreenshot(): Promise<BrowserToolResult<{ screenshotBase64: string }>> {
    return await this.executeTool<{ screenshotBase64: string }>("browser_screenshot");
  }

  /**
   * Inspect current browser health, URL, and navigation epoch.
   */
  public async browserStatus(): Promise<BrowserToolResult<BrowserStatusData>> {
    return await this.executeTool<BrowserStatusData>("browser_status");
  }
}
