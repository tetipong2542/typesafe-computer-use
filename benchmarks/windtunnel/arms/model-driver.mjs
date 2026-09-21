/**
 * @fileoverview Benchmark-only Model Driver for TypeSafe WindTunnel Arms.
 * Provides a provider-agnostic abstraction for agentic multi-turn interaction loops,
 * capability validation, token accounting, and zero-cost fake simulation.
 */

import { costFor } from "../harness/lib.mjs";
import { claudeSampling, maxTokensFor } from "./prompts.mjs";

export const CAPABILITIES = {
  WEBMCP_TOOLS: "webmcp_tools",
  BROWSER_DOM: "browser_dom",
  COMPUTER_USE: "computer_use",
  VISION: "vision",
};

/**
 * Validates whether a model identifier supports the requested capability modality.
 * Fails fast before incurring paid token charges.
 * @param {string} model - Model identifier (e.g. "claude-sonnet-4-6")
 * @param {string} capability - Required capability
 */
export function validateModelCapability(model, capability) {
  const m = String(model || "").toLowerCase();

  if (m === "none" || m === "fake") return true;

  const isClaude = m.includes("claude");
  const isOpenAI = m.includes("gpt") || m.includes("astra");
  const isGemini = m.includes("gemini");

  switch (capability) {
    case CAPABILITIES.WEBMCP_TOOLS:
    case CAPABILITIES.BROWSER_DOM:
      // All modern evaluation models support function / tool calling
      return isClaude || isOpenAI || isGemini;

    case CAPABILITIES.COMPUTER_USE:
      // Only specific Anthropic computer-use capable models or OpenAI computer-use models
      if (isClaude) {
        return m.includes("sonnet") || m.includes("opus");
      }
      if (isOpenAI) {
        return m.includes("computer-use") || m.includes("luna") || m.includes("sol") || m.includes("astra");
      }
      if (isGemini) {
        return m.includes("flash") || m.includes("pro");
      }
      return false;

    case CAPABILITIES.VISION:
      return isClaude || isOpenAI || isGemini;

    default:
      return true;
  }
}

/**
 * Base ModelDriver interface for WindTunnel benchmark evaluation.
 */
export class ModelDriver {
  constructor({
    armId = "generic",
    model = "none",
    maxTurns = 5,
    maxOutputTokens = 1024,
    timeoutMs = 120000,
    budgetUsd = 0.75,
  } = {}) {
    this.armId = armId;
    this.model = model;
    this.maxTurns = maxTurns;
    this.maxOutputTokens = maxOutputTokens;
    this.timeoutMs = timeoutMs;
    this.budgetUsd = budgetUsd;

    this.turnIndex = 0;
    this.cancelled = false;
    this.cancelReason = null;
    this.turnsHistory = [];

    // Cumulative token & cost metrics
    this.cumulativeUsage = {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      image_tokens: 0,
    };
    this.totalCostUsd = 0.0;
    this.modelSnapshot = "unavailable";
    this.lastRequestId = "unavailable";
    this.totalLatencyMs = 0;
  }

  /**
   * Check if current expenditure exceeds budget.
   */
  checkBudget() {
    return this.totalCostUsd >= this.budgetUsd;
  }

  /**
   * Abort active interaction.
   */
  cancel(reason = "Aborted by operator") {
    this.cancelled = true;
    this.cancelReason = reason;
  }

  /**
   * Records provider-reported usage and computes estimated USD cost.
   */
  recordUsage({
    input_tokens = 0,
    output_tokens = 0,
    cache_read_tokens = 0,
    cache_write_tokens = 0,
    image_tokens = 0,
    model_snapshot = "unavailable",
    provider_request_id = "unavailable",
    latency_ms = 0,
  } = {}) {
    this.cumulativeUsage.input_tokens += input_tokens;
    this.cumulativeUsage.output_tokens += output_tokens;
    this.cumulativeUsage.cache_read_tokens += cache_read_tokens;
    this.cumulativeUsage.cache_write_tokens += cache_write_tokens;
    this.cumulativeUsage.image_tokens += image_tokens;

    this.modelSnapshot = model_snapshot;
    this.lastRequestId = provider_request_id;
    this.totalLatencyMs += latency_ms;

    const turnCost = costFor(this.model, {
      input_tokens,
      output_tokens,
      cached_input_tokens: cache_read_tokens,
      cache_creation_tokens: cache_write_tokens,
    });
    this.totalCostUsd += turnCost;

    return {
      turn: this.turnIndex,
      input_tokens,
      output_tokens,
      cache_read_tokens,
      cache_write_tokens,
      image_tokens,
      turn_cost_usd: turnCost,
      model_snapshot,
      provider_request_id,
      latency_ms,
    };
  }

  /**
   * Returns complete accounting summary for reporting.
   */
  getAccounting() {
    return {
      armId: this.armId,
      model: this.model,
      turns: this.turnIndex,
      modelSnapshot: this.modelSnapshot,
      lastRequestId: this.lastRequestId,
      totalLatencyMs: this.totalLatencyMs,
      usage: { ...this.cumulativeUsage },
      costUsd: this.totalCostUsd,
      budgetExhausted: this.checkBudget(),
      turnsHistory: this.turnsHistory,
    };
  }
}

/**
 * Deterministic Zero-Cost Model Driver for testing all edge-cases without spending money.
 */
export class FakeModelDriver extends ModelDriver {
  constructor(options = {}) {
    super(options);
    this.scenario = options.scenario || "default";
    this.scriptedTurns = options.scriptedTurns || [];
    this.simulatedError = options.simulatedError || null;
    this.modelSnapshot = `fake-${this.model}-2026-09-21`;
  }

  async createTurn({ system, messages, tools = [], screenshot = null }) {
    if (this.cancelled) {
      throw new Error(`Turn cancelled: ${this.cancelReason}`);
    }

    if (this.checkBudget()) {
      return {
        type: "stop",
        reason: "budget_exceeded",
        finalText: "Budget ceiling reached. Execution halted.",
        toolCalls: [],
      };
    }

    if (this.turnIndex >= this.maxTurns) {
      return {
        type: "stop",
        reason: "turn_limit_reached",
        finalText: "Turn limit reached.",
        toolCalls: [],
      };
    }

    if (this.simulatedError) {
      const err = this.simulatedError;
      this.simulatedError = null; // one-shot
      throw err;
    }

    this.turnIndex++;
    const startMs = Date.now();

    // Check if a scripted turn was supplied
    let turnOutput = null;
    if (this.scriptedTurns.length >= this.turnIndex) {
      turnOutput = this.scriptedTurns[this.turnIndex - 1];
    } else {
      turnOutput = this._synthesizeDefaultTurn(messages, tools, screenshot);
    }

    const latency_ms = Date.now() - startMs + 10;
    const usageData = this.recordUsage({
      input_tokens: turnOutput.usage?.input_tokens ?? (150 + this.turnIndex * 20),
      output_tokens: turnOutput.usage?.output_tokens ?? 35,
      cache_read_tokens: turnOutput.usage?.cache_read_tokens ?? 0,
      cache_write_tokens: turnOutput.usage?.cache_write_tokens ?? 0,
      image_tokens: screenshot ? 800 : 0,
      model_snapshot: this.modelSnapshot,
      provider_request_id: `fake-req-${this.armId}-${this.turnIndex}-${Math.random().toString(36).slice(2, 7)}`,
      latency_ms,
    });

    this.turnsHistory.push({
      turn: this.turnIndex,
      usage: usageData,
      response: turnOutput,
    });

    return turnOutput;
  }

  _synthesizeDefaultTurn(messages, tools, screenshot) {
    const lastMsg = messages[messages.length - 1];
    const promptText = typeof lastMsg?.content === "string"
      ? lastMsg.content
      : JSON.stringify(lastMsg?.content || "");

    // 1. If tool result was received, produce final answer
    if (promptText.includes("tool_result") || promptText.includes("collaborative") || promptText.includes("Figma") || promptText.includes("GitHub")) {
      return {
        type: "final_answer",
        finalText: promptText.includes("GitHub") ? "Final answer: GitHub" : "Final answer: Figma",
        toolCalls: [],
      };
    }

    // 2. If tools are available (WebMCP), invoke first matching tool
    if (tools.length > 0) {
      const targetTool = tools.find((t) => t.name.includes("search") || t.name.includes("filter")) || tools[0];
      return {
        type: "tool_use",
        finalText: "",
        toolCalls: [
          {
            id: `call_${Math.random().toString(36).slice(2, 9)}`,
            name: targetTool.name,
            arguments: promptText.includes("filter") ? { category: "developer" } : { query: "design" },
          },
        ],
      };
    }

    // 3. If screenshot provided (Visual), produce computer mouse action
    if (screenshot) {
      if (this.turnIndex === 1) {
        return {
          type: "computer_action",
          action: "left_click",
          coordinate: [240, 180],
          toolCalls: [{ id: "call_cu_1", name: "computer", action: "left_click", coordinate: [240, 180] }],
        };
      }
      return {
        type: "final_answer",
        finalText: "Final answer: Figma collaborative design tool",
        toolCalls: [],
      };
    }

    // 4. Default DOM / Text turn
    if (promptText.includes("filter")) {
      return {
        type: "dom_action",
        action: "click",
        selector: "text=Developer Tools",
        finalText: "Final answer: GitHub",
        toolCalls: [],
      };
    }

    return {
      type: "final_answer",
      finalText: "Final answer: Figma",
      toolCalls: [],
    };
  }
}

/**
 * OpenAI / Codex-compatible Model Driver.
 * Routes to codex-openai-proxy (http://localhost:8888/v1) by default,
 * communicating with ChatGPT backend via Codex authentication (ChatGPT Plus tokens),
 * ensuring $0.00 actual API billing cost while accurately tracking token metrics.
 */
export class OpenAICodexModelDriver extends ModelDriver {
  constructor(options = {}) {
    super({
      armId: options.armId || "typesafe",
      model: options.model || "gpt-5.6-sol",
      ...options,
    });
    this.baseUrl = options.baseUrl || process.env.OPENAI_BASE_URL || "http://localhost:8888/v1";
    this.apiKey = options.apiKey || process.env.OPENAI_API_KEY || "codex-chatgpt-plus";
    this.isCodex = options.isCodex ?? true;
  }

  async createTurn({ system = "", messages = [], tools = [], screenshot = null }) {
    if (this.cancelled) {
      throw new Error(`Turn cancelled: ${this.cancelReason}`);
    }

    if (this.checkBudget()) {
      return {
        type: "stop",
        reason: "budget_exceeded",
        finalText: "Budget ceiling reached. Execution halted.",
        toolCalls: [],
      };
    }

    if (this.turnIndex >= this.maxTurns) {
      return {
        type: "stop",
        reason: "turn_limit_reached",
        finalText: "Maximum turn limit reached.",
        toolCalls: [],
      };
    }

    this.turnIndex++;
    const startMs = Date.now();

    // Prepare OpenAI Chat Completions payload
    const formattedMessages = [];

    // Prepend system prompt if provided
    let effectiveSystem = system;

    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      let content = m.content;

      // In Codex proxy, role: "system" should be embedded into the first user message
      if (i === 0 && effectiveSystem) {
        if (typeof content === "string") {
          content = `[Instructions]\n${effectiveSystem}\n\n[Task]\n${content}`;
        } else if (Array.isArray(content)) {
          content = [
            { type: "text", text: `[Instructions]\n${effectiveSystem}\n\n` },
            ...content,
          ];
        }
      }

      formattedMessages.push({
        role: m.role === "system" ? "user" : m.role,
        content,
      });
    }

    // Attach screenshot if in visual mode
    if (screenshot && formattedMessages.length > 0) {
      const lastMsg = formattedMessages[formattedMessages.length - 1];
      const base64Img = Buffer.isBuffer(screenshot) ? screenshot.toString("base64") : screenshot;
      const imageItem = {
        type: "image_url",
        image_url: { url: `data:image/png;base64,${base64Img}` },
      };

      if (Array.isArray(lastMsg.content)) {
        lastMsg.content.push(imageItem);
      } else {
        lastMsg.content = [
          { type: "text", text: String(lastMsg.content || "") },
          imageItem,
        ];
      }
    }

    // Format tools for OpenAI API
    const formattedTools = tools.length > 0
      ? tools.map((t) => ({
          type: "function",
          function: {
            name: t.name,
            description: t.description || "",
            parameters: t.input_schema || t.parameters || { type: "object", properties: {} },
          },
        }))
      : undefined;

    const requestBody = {
      model: this.model,
      messages: formattedMessages,
      max_tokens: this.maxOutputTokens,
      tools: formattedTools,
    };

    const targetUrl = `${this.baseUrl.replace(/\/+$/, "")}/chat/completions`;
    const res = await fetch(targetUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify(requestBody),
      signal: AbortSignal.timeout(this.timeoutMs),
    });

    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      if (res.status === 429) {
        const err = new Error(`Rate limit exceeded (429): ${errText}`);
        err.isRateLimit = true;
        err.status = 429;
        throw err;
      }
      throw new Error(`OpenAI proxy HTTP ${res.status}: ${errText}`);
    }

    const data = await res.json();
    const latency_ms = Date.now() - startMs;

    const usage = data.usage || {};
    const inputTokens = usage.prompt_tokens || 0;
    const outputTokens = usage.completion_tokens || 0;
    const modelSnapshot = data.model || this.model;
    const requestId = data.id || `codex-req-${Date.now()}`;

    // Record metrics. Note: Since this uses ChatGPT Plus tokens via Codex authentication,
    // the actual financial billing to API accounts is $0.00, while token usage is tracked.
    this.recordUsage({
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      image_tokens: screenshot ? 800 : 0,
      model_snapshot: modelSnapshot,
      provider_request_id: requestId,
      latency_ms,
    });

    const choice = data.choices?.[0];
    const message = choice?.message || {};
    const toolCalls = [];

    if (Array.isArray(message.tool_calls)) {
      for (const tc of message.tool_calls) {
        let args = {};
        if (typeof tc.function?.arguments === "string") {
          try {
            args = JSON.parse(tc.function.arguments);
          } catch {
            args = { raw: tc.function.arguments };
          }
        } else if (typeof tc.function?.arguments === "object") {
          args = tc.function.arguments;
        }
        toolCalls.push({
          id: tc.id || `call_${Math.random().toString(36).slice(2, 9)}`,
          name: tc.function?.name || "",
          arguments: args,
        });
      }
    }

    const finalText = message.content || "";

    return {
      type: toolCalls.length > 0 ? "tool_use" : "final_answer",
      finalText,
      toolCalls,
      rawResponse: data,
    };
  }

  getAccounting() {
    const base = super.getAccounting();
    return {
      ...base,
      actualCostUsd: this.isCodex ? 0.0 : this.totalCostUsd,
      nominalCostUsd: this.totalCostUsd,
      billingType: this.isCodex ? "chatgpt_plus_codex" : "pay_per_token_api",
      endpoint: this.baseUrl,
    };
  }
}

/**
 * Real Anthropic Claude Model Driver.
 * Only instantiated when real API keys are present and live benchmark is authorized.
 */
export class AnthropicModelDriver extends ModelDriver {
  constructor(options = {}) {
    super(options);
    const apiKey = options.apiKey || process.env.ANTHROPIC_API_KEY;
    if (!apiKey) {
      throw new Error("ANTHROPIC_API_KEY is not set in environment");
    }

    // Lazy load Anthropic SDK to avoid requiring it in offline/test environments
    this.apiKey = apiKey;
    this.client = null;
  }

  async _getClient() {
    if (!this.client) {
      const { default: Anthropic } = await import("@anthropic-ai/sdk");
      this.client = new Anthropic({ apiKey: this.apiKey, maxRetries: 3 });
    }
    return this.client;
  }

  async createTurn({ system = "", messages = [], tools = [], screenshot = null }) {
    if (this.cancelled) {
      throw new Error(`Turn cancelled: ${this.cancelReason}`);
    }

    if (this.checkBudget()) {
      return {
        type: "stop",
        reason: "budget_exceeded",
        finalText: "Budget ceiling reached. Execution halted.",
        toolCalls: [],
      };
    }

    if (this.turnIndex >= this.maxTurns) {
      return {
        type: "stop",
        reason: "turn_limit_reached",
        finalText: "Maximum turn limit reached.",
        toolCalls: [],
      };
    }

    this.turnIndex++;
    const client = await this._getClient();
    const sampling = claudeSampling(this.model);
    const maxTokens = Math.min(this.maxOutputTokens, maxTokensFor(this.model));

    // Construct formatted messages
    const formattedMessages = messages.map((m) => ({ ...m }));

    // Attach screenshot if in visual mode
    if (screenshot && formattedMessages.length > 0) {
      const lastMsg = formattedMessages[formattedMessages.length - 1];
      const base64Img = Buffer.isBuffer(screenshot) ? screenshot.toString("base64") : screenshot;
      if (Array.isArray(lastMsg.content)) {
        lastMsg.content.push({
          type: "image",
          source: { type: "base64", media_type: "image/png", data: base64Img },
        });
      } else {
        lastMsg.content = [
          { type: "text", text: String(lastMsg.content || "") },
          { type: "image", source: { type: "base64", media_type: "image/png", data: base64Img } },
        ];
      }
    }

    const requestParams = {
      model: this.model,
      max_tokens: maxTokens,
      system: system || undefined,
      messages: formattedMessages,
      ...sampling.request,
    };

    if (tools.length > 0) {
      requestParams.tools = tools;
    }

    const startMs = Date.now();
    const response = await client.messages.create(requestParams);
    const latency_ms = Date.now() - startMs;

    // Parse usage
    const usage = response.usage || {};
    const model_snapshot = response.model || this.model;
    const provider_request_id = response._request_id || response.id || "unavailable";

    this.recordUsage({
      input_tokens: usage.input_tokens || 0,
      output_tokens: usage.output_tokens || 0,
      cache_read_tokens: usage.cache_read_input_tokens || 0,
      cache_write_tokens: usage.cache_creation_input_tokens || 0,
      image_tokens: screenshot ? 1600 : 0,
      model_snapshot,
      provider_request_id,
      latency_ms,
    });

    // Parse model output blocks
    let finalText = "";
    const toolCalls = [];

    for (const block of response.content || []) {
      if (block.type === "text") {
        finalText += block.text;
      } else if (block.type === "tool_use") {
        toolCalls.push({
          id: block.id,
          name: block.name,
          arguments: block.input || {},
        });
      }
    }

    return {
      type: toolCalls.length > 0 ? "tool_use" : "final_answer",
      finalText,
      toolCalls,
      rawResponse: response,
    };
  }
}

/**
 * Factory function to create appropriate ModelDriver based on configuration.
 */
export function createModelDriver(options = {}) {
  const model = options.model || process.env.WT_MODEL || "gpt-5.6-sol";
  const isFake = process.env.WT_FAKE_LIFECYCLE === "1"
    || process.env.WT_USE_FAKE_DRIVER === "1"
    || options.driver === "fake"
    || model === "none"
    || model === "fake";

  if (isFake) {
    return new FakeModelDriver({ ...options, model });
  }

  // If Anthropic Claude model and explicitly configured
  if (model.toLowerCase().includes("claude") && process.env.ANTHROPIC_API_KEY) {
    return new AnthropicModelDriver({ ...options, model });
  }

  // Default: OpenAICodexModelDriver routing through codex-openai-proxy (port 8888) with gpt-5.6-sol
  return new OpenAICodexModelDriver({ ...options, model });
}

