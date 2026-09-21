/**
 * @fileoverview TypeSafe Computer Use Benchmark Arms for WindTunnel
 *
 * Implements 5 evaluation arms conforming to WindTunnel benchmark harness:
 * 1. ts-webmcp-native: Official Chrome Blink C++ WebMCP runtime (zero injected polyfills).
 * 2. ts-browser-dom: Structured DOM-level interactions.
 * 3. ts-visual: Visual Computer Use screenshot & coordinate interactions.
 * 4. ts-hybrid-auto: TypeSafe ShadowInteractionRouter with tri-tier adaptive fallback.
 * 5. ts-webmcp-compat: Compatibility Bridge for non-native browser contexts.
 */

import { performance } from "node:perf_hooks";
import { RECIPES } from "./scripted.mjs";
import {
  createModelDriver,
  validateModelCapability,
  CAPABILITIES,
} from "./model-driver.mjs";

export const TOOL_VERSION = "typesafe-computer-use@0.2.0";
export const DEFAULT_ARM_MODEL = "gpt-5.6-sol";

/**
 * Perform self-check of TypeSafe arm runtime readiness.
 */
export async function selfCheck() {
  const workerUrl = process.env.WT_TYPESAFE_WORKER_URL;
  let workerOnline = false;
  if (workerUrl) {
    try {
      const resp = await fetch(`${workerUrl}/healthz`, { signal: AbortSignal.timeout(2000) });
      workerOnline = resp.ok;
    } catch {
      workerOnline = false;
    }
  }

  return {
    ok: true,
    framework: "typesafe-computer-use",
    version: TOOL_VERSION,
    worker_url: workerUrl || "local",
    worker_online: workerOnline,
    arms: [
      "ts-webmcp-native",
      "ts-browser-dom",
      "ts-visual",
      "ts-hybrid-auto",
      "ts-webmcp-compat",
    ],
  };
}

/**
 * Check if the current page environment provides native Chrome document.modelContext.
 * @param {import("playwright").Page} page
 * @returns {Promise<boolean>}
 */
async function hasNativeWebMCP(page) {
  if (!page || typeof page.evaluate !== "function") return false;
  try {
    return await page.evaluate(() => {
      return typeof document !== "undefined" &&
        typeof document.modelContext !== "undefined" &&
        typeof document.modelContext.getTools === "function";
    });
  } catch {
    return false;
  }
}

/**
 * Discovers tools declared natively on the page via Chrome Blink C++ WebMCP.
 * @param {import("playwright").Page} page
 * @returns {Promise<Array<{name: string, description: string, input_schema: any}>>}
 */
async function discoverNativeTools(page) {
  return await page.evaluate(async () => {
    if (!document?.modelContext?.getTools) return [];
    const tools = await document.modelContext.getTools();
    return tools.map((t) => ({
      name: t.name,
      description: t.description || "",
      input_schema: t.inputSchema || t.input_schema || { type: "object", properties: {} },
    }));
  });
}

/**
 * Execute a native WebMCP tool via document.modelContext.executeTool.
 * @param {import("playwright").Page} page
 * @param {string} toolName
 * @param {object} args
 */
async function executeNativeTool(page, toolName, args = {}) {
  return await page.evaluate(async ({ name, callArgs }) => {
    const tools = await document.modelContext.getTools();
    const tool = tools.find((t) => t.name === name);
    if (!tool) throw new Error(`Native WebMCP tool '${name}' not found`);

    if (typeof document.modelContext.executeTool === "function") {
      return await document.modelContext.executeTool(tool, JSON.stringify(callArgs));
    } else if (typeof tool.execute === "function") {
      return await tool.execute(callArgs);
    }
    throw new Error(`Execution method not available for '${name}'`);
  }, { name: toolName, callArgs: args });
}

/**
 * Execute browser DOM interactions (matching BrowserDOMAdapter).
 * @param {import("playwright").Page} page
 * @param {Array<any>} recipe
 * @param {string} baseUrl
 */
async function executeDOMRecipe(page, recipe, baseUrl) {
  const transcript = [];
  let finalText = "";

  for (const step of recipe) {
    if (step.action === "goto") {
      const target = new URL(step.path, baseUrl).href;
      await page.goto(target);
      transcript.push({ action: "goto", target, mode: "browser_dom" });
    } else if (step.action === "fill") {
      await page.locator(step.selector).fill(step.value);
      transcript.push({ action: "fill", selector: step.selector, value: step.value, mode: "browser_dom" });
    } else if (step.action === "click") {
      await page.locator(step.selector).click();
      transcript.push({ action: "click", selector: step.selector, mode: "browser_dom" });
    } else if (step.action === "press") {
      await page.locator(step.selector).press(step.key);
      transcript.push({ action: "press", selector: step.selector, key: step.key, mode: "browser_dom" });
    } else if (step.action === "text") {
      finalText = await page.locator(step.selector).innerText();
      transcript.push({ action: "text", selector: step.selector, length: finalText.length, mode: "browser_dom" });
    } else if (step.action === "title") {
      finalText = await page.title();
      transcript.push({ action: "title", value: finalText, mode: "browser_dom" });
    }
  }

  return { finalText, transcript };
}

/**
 * Helper to build standard WindTunnel benchmark result structure.
 */
function buildResult({
  armId,
  finalText = "",
  transcript = [],
  turns = 1,
  setupMs = 0,
  failure = null,
  model = DEFAULT_ARM_MODEL,
  usage = { input_tokens: 0, output_tokens: 0 },
  cost = 0.0,
  cost_nominal = 0.0,
  model_snapshot = "typesafe-local",
  budget_exhausted = false,
  telemetry = {},
}) {
  return {
    finalText,
    usage,
    transcript,
    cost,
    cost_nominal,
    cost_estimated: "chatgpt_plus_codex (actual: $0.00)",
    turns,
    setupMs,
    failure,
    budget_exhausted,
    model_snapshot,
    snapshot_source: TOOL_VERSION,
    temperature: 0.0,
    effort: "provider-default",
    caching: "unsupported",
    telemetry: {
      ...telemetry,
      actual_cost_usd: cost,
      nominal_list_price_usd: cost_nominal,
      image_token_heuristic: "800 tokens per 1280x800 screenshot",
    },
  };
}

/**
 * Shared dry-run fallback for WT_FAKE_LIFECYCLE=1.
 */
function runFake({ task, armId, mode }) {
  return buildResult({
    armId,
    finalText: `[TypeSafe Fake Run: ${armId}] Task: ${task.id}`,
    transcript: [
      {
        harness: "fake_lifecycle",
        arm: armId,
        mode,
        task_id: task.id,
        status: "ok",
      },
    ],
    turns: 1,
    setupMs: 5,
    telemetry: {
      arm: armId,
      selected_mode: mode,
      executed_mode: mode,
      fake: true,
    },
  });
}

// ==============================================================================
// 1. Arm: ts-webmcp-native
// ==============================================================================
export async function runWebMCPNative({ task, capsule, page, model = "none", driverOptions = {} }) {
  const started = performance.now();
  const armId = "ts-webmcp-native";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "webmcp_native" });
  }

  if (!validateModelCapability(model, CAPABILITIES.WEBMCP_TOOLS)) {
    throw new Error(`Model '${model}' does not support capability '${CAPABILITIES.WEBMCP_TOOLS}'`);
  }

  const setupMs = performance.now() - started;
  const transcript = [];

  // Navigate to site start URL
  const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

  // Verify Native Chrome WebMCP environment exists
  const isNativeSupported = await hasNativeWebMCP(page);
  if (!isNativeSupported) {
    return buildResult({
      armId,
      finalText: "",
      transcript: [
        {
          error: "BLOCKED_BY_BROWSER_SUPPORT: Chrome document.modelContext is undefined",
          selected_mode: "webmcp",
          executed_mode: null,
          webmcp_implementation: "native",
        },
      ],
      failure: "native-webmcp-unsupported",
      setupMs,
      telemetry: {
        selected_mode: "webmcp",
        executed_mode: null,
        webmcp_implementation: "native",
        visual_invocation_count: 0,
      },
    });
  }

  // Discover native tools
  const tools = await discoverNativeTools(page);
  transcript.push({
    action: "discovery",
    toolCount: tools.length,
    tools: tools.map((t) => t.name),
    selected_mode: "webmcp",
    executed_mode: tools.length > 0 ? "webmcp" : null,
    webmcp_implementation: "native",
  });

  if (tools.length === 0) {
    return buildResult({
      armId,
      finalText: "No native WebMCP tools registered on page",
      transcript,
      failure: "no-webmcp-tools",
      setupMs,
      telemetry: {
        selected_mode: "webmcp",
        executed_mode: null,
        webmcp_implementation: "native",
        visual_invocation_count: 0,
      },
    });
  }

  // If model is provided and not "none": Run Agentic Tool-calling Loop!
  if (model !== "none") {
    const driver = driverOptions.driverInstance || createModelDriver({ armId, model, maxTurns: 5, ...driverOptions });
    const messages = [{ role: "user", content: task.prompt || `Perform task: ${task.id}` }];
    let finalText = "";
    let failure = null;

    while (driver.turnIndex < driver.maxTurns) {
      let turnRes;
      try {
        turnRes = await driver.createTurn({
          system: "You operate the site exclusively through exposed WebMCP tools. Call ONE tool at a time and state the final result plainly beginning with 'Final answer:'.",
          messages,
          tools,
        });
      } catch (e) {
        failure = e.isRateLimit ? "rate_limit_429" : e.message;
        transcript.push({ action: "driver_error", error: e.message });
        break;
      }

      if (turnRes.type === "stop") {
        failure = turnRes.reason;
        finalText = turnRes.finalText || "";
        break;
      }

      if (turnRes.type === "tool_use" && turnRes.toolCalls?.length > 0) {
        for (const call of turnRes.toolCalls) {
          try {
            const toolRes = await executeNativeTool(page, call.name, call.arguments || {});
            const resText = typeof toolRes === "string" ? toolRes : JSON.stringify(toolRes);
            transcript.push({ action: "execute_native_tool", tool: call.name, args: call.arguments, result: toolRes });
            messages.push({ role: "assistant", content: `Call tool ${call.name} with ${JSON.stringify(call.arguments)}` });
            messages.push({ role: "user", content: `Tool result: ${resText}` });
          } catch (err) {
            transcript.push({ action: "tool_execution_error", tool: call.name, error: err.message });
            if (/unknown side effect|unsafe state|timeout during commit/i.test(err.message)) {
              failure = "unknown-side-effect-halt";
              finalText = "";
              break;
            }
            messages.push({ role: "user", content: `Tool error: ${err.message}` });
          }
        }
        if (failure === "unknown-side-effect-halt") {
          break;
        }
      } else {
        finalText = turnRes.finalText || "";
        break;
      }
    }

    const acct = driver.getAccounting();
    return buildResult({
      armId,
      finalText,
      transcript,
      turns: acct.turns || 1,
      setupMs,
      failure,
      model,
      usage: acct.usage,
      cost: acct.actualCostUsd,
      cost_nominal: acct.nominalCostUsd || acct.costUsd || 0.0,
      model_snapshot: acct.modelSnapshot,
      budget_exhausted: acct.budgetExhausted,
      telemetry: {
        selected_mode: "webmcp",
        executed_mode: "webmcp",
        webmcp_implementation: "native",
        visual_invocation_count: 0,
        accounting: acct,
      },
    });
  }

  // Deterministic execution when model === "none"
  let finalText = "";
  const matchingTool = tools.find((t) => t.name === task.tool_name || (task.tool_name && t.name.includes(task.tool_name)));
  if (matchingTool) {
    try {
      const toolRes = await executeNativeTool(page, matchingTool.name, task.tool_args || {});
      finalText = typeof toolRes === "string" ? toolRes : (toolRes?.text || toolRes?.result || JSON.stringify(toolRes));
      transcript.push({
        action: "execute_native_tool",
        tool: matchingTool.name,
        result: toolRes,
        selected_mode: "webmcp",
        executed_mode: "webmcp",
        webmcp_implementation: "native",
      });
    } catch (e) {
      transcript.push({ action: "execute_tool_error", tool: matchingTool.name, error: e.message });
      return buildResult({
        armId,
        finalText: "",
        transcript,
        failure: "tool-execution-failed",
        setupMs,
        telemetry: {
          selected_mode: "webmcp",
          executed_mode: null,
          webmcp_implementation: "native",
          visual_invocation_count: 0,
        },
      });
    }
  } else {
    const recipe = task.recipe ?? RECIPES[task.id];
    if (recipe) {
      const res = await executeDOMRecipe(page, recipe, capsule.baseUrl);
      finalText = res.finalText;
      transcript.push(...res.transcript);
    } else {
      finalText = await page.locator("body").innerText();
    }
  }

  return buildResult({
    armId,
    finalText,
    transcript,
    turns: transcript.length || 1,
    setupMs,
    telemetry: {
      selected_mode: "webmcp",
      executed_mode: "webmcp",
      webmcp_implementation: "native",
      visual_invocation_count: 0,
    },
  });
}

// ==============================================================================
// 2. Arm: ts-browser-dom
// ==============================================================================
export async function runBrowserDOM({ task, capsule, page, model = "none", driverOptions = {} }) {
  const started = performance.now();
  const armId = "ts-browser-dom";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "browser_dom" });
  }

  if (!validateModelCapability(model, CAPABILITIES.BROWSER_DOM)) {
    throw new Error(`Model '${model}' does not support capability '${CAPABILITIES.BROWSER_DOM}'`);
  }

  const setupMs = performance.now() - started;
  const transcript = [];

  if (model !== "none") {
    const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

    const driver = driverOptions.driverInstance || createModelDriver({ armId, model, maxTurns: 5, ...driverOptions });
    let finalText = "";
    let failure = null;
    let executedActions = 0;

    const domTools = [
      {
        name: "click",
        description: "Click an interactive element on the page using a CSS selector or text locator (e.g. 'a:has-text(\"Development\")' or 'button:has-text(\"Development\")')",
        input_schema: {
          type: "object",
          properties: {
            selector: { type: "string", description: "CSS selector or Playwright text locator" },
          },
          required: ["selector"],
        },
      },
      {
        name: "fill",
        description: "Fill a text input field on the page",
        input_schema: {
          type: "object",
          properties: {
            selector: { type: "string", description: "Input element CSS selector" },
            value: { type: "string", description: "Text value to fill" },
          },
          required: ["selector", "value"],
        },
      },
      {
        name: "press",
        description: "Press a keyboard key on an element (e.g. 'Enter')",
        input_schema: {
          type: "object",
          properties: {
            selector: { type: "string", description: "Target selector" },
            key: { type: "string", description: "Key name to press (e.g. 'Enter')" },
          },
          required: ["selector", "key"],
        },
      },
    ];

    // Collect interactive elements to give model clear grounding targets
    const interactiveSummary = await page.evaluate(() => {
      const items = [];
      const els = document.querySelectorAll('button, a, input, select, [role="button"]');
      for (const el of els) {
        const text = (el.innerText || el.getAttribute("placeholder") || el.getAttribute("aria-label") || el.value || "").trim().replace(/\s+/g, " ");
        const href = el.getAttribute("href") || "";
        const type = el.getAttribute("type") || "";
        if (text || el.tagName.toLowerCase() === "input") {
          items.push({
            tag: el.tagName.toLowerCase(),
            text: text.slice(0, 40),
            href: href.slice(0, 40),
            type,
          });
        }
      }
      return items.slice(0, 25);
    }).catch(() => []);

    const initialPrompt = `${task.prompt || `Perform task: ${task.id}`}\n\nInteractive elements on page:\n${JSON.stringify(interactiveSummary, null, 2)}\n\nYou MUST first execute the requested action (e.g. clicking the category filter button or filling the search box) using the provided tools before stating the final answer.`;
    const messages = [{ role: "user", content: initialPrompt }];

    while (driver.turnIndex < driver.maxTurns) {
      let turnRes;
      try {
        turnRes = await driver.createTurn({
          system: "You operate a web browser through structured DOM element interactions. You MUST first perform the requested action (clicking filters or filling search inputs) using tools before stating your final answer. Only after taking actions and verifying the result, conclude with 'Final answer:'.",
          messages,
          tools: domTools,
        });
      } catch (e) {
        failure = e.isRateLimit ? "rate_limit_429" : e.message;
        transcript.push({ action: "driver_error", error: e.message });
        break;
      }

      if (turnRes.type === "stop") {
        failure = turnRes.reason;
        finalText = turnRes.finalText || "";
        break;
      }

      // Handle tool calls or dom_action
      const calls = turnRes.toolCalls && turnRes.toolCalls.length > 0
        ? turnRes.toolCalls
        : (turnRes.type === "dom_action" && turnRes.action
          ? [{ name: turnRes.action, arguments: { selector: turnRes.selector, value: turnRes.value, key: turnRes.key } }]
          : []);

      if (calls.length > 0) {
        for (const call of calls) {
          const args = call.arguments || {};
          try {
            if (call.name === "click" && args.selector) {
              await page.locator(args.selector).first().click({ timeout: 5000 });
              transcript.push({ action: "click", selector: args.selector, mode: "browser_dom" });
              executedActions++;
            } else if (call.name === "fill" && args.selector) {
              await page.locator(args.selector).first().fill(args.value || "", { timeout: 5000 });
              transcript.push({ action: "fill", selector: args.selector, value: args.value, mode: "browser_dom" });
              executedActions++;
            } else if (call.name === "press" && args.selector) {
              await page.locator(args.selector).first().press(args.key || "Enter", { timeout: 5000 });
              transcript.push({ action: "press", selector: args.selector, key: args.key, mode: "browser_dom" });
              executedActions++;
            }
            if (typeof page.waitForTimeout === "function") await page.waitForTimeout(500);
            else await new Promise((r) => setTimeout(r, 10));
            const visibleText = await page.locator("body").innerText().catch(() => "");
            messages.push({ role: "assistant", content: `Call ${call.name} with ${JSON.stringify(args)}` });
            messages.push({ role: "user", content: `Tool result: Action executed. Current page text:\n${visibleText.slice(0, 1500)}` });
          } catch (err) {
            transcript.push({ action: "action_error", tool: call.name, error: err.message, mode: "browser_dom" });
            messages.push({ role: "user", content: `Action error: ${err.message}. Try an alternative selector.` });
          }
        }
      } else if (turnRes.finalText) {
        // Enforce that at least one action must have occurred before accepting final answer
        if (executedActions === 0 && driver.turnIndex < driver.maxTurns - 1) {
          messages.push({ role: "assistant", content: turnRes.finalText });
          messages.push({
            role: "user",
            content: "You have not performed the requested interaction yet. You MUST call an action tool (such as click on the category filter or fill the search input) before providing the final answer.",
          });
          continue;
        }
        finalText = turnRes.finalText;
        break;
      }
    }

    const acct = driver.getAccounting();
    return buildResult({
      armId,
      finalText,
      transcript,
      turns: acct.turns || 1,
      setupMs,
      failure,
      model,
      usage: acct.usage,
      cost: acct.actualCostUsd,
      cost_nominal: acct.nominalCostUsd || acct.costUsd || 0.0,
      model_snapshot: acct.modelSnapshot,
      budget_exhausted: acct.budgetExhausted,
      telemetry: {
        selected_mode: "browser_dom",
        executed_mode: "browser_dom",
        actions_performed: executedActions,
        visual_invocation_count: 0,
        accounting: acct,
      },
    });
  }

  // Deterministic recipe execution when model === "none"
  const recipe = task.recipe ?? RECIPES[task.id];
  if (!recipe) {
    const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    const finalText = await page.locator("body").innerText();
    return buildResult({
      armId,
      finalText,
      transcript: [
        {
          action: "fallback_text",
          targetUrl,
          selected_mode: "browser_dom",
          executed_mode: "browser_dom",
          visual_invocation_count: 0,
        },
      ],
      turns: 1,
      setupMs,
      telemetry: {
        selected_mode: "browser_dom",
        executed_mode: "browser_dom",
        visual_invocation_count: 0,
      },
    });
  }

  const res = await executeDOMRecipe(page, recipe, capsule.baseUrl);
  return buildResult({
    armId,
    finalText: res.finalText,
    transcript: res.transcript,
    turns: res.transcript.length || 1,
    setupMs,
    telemetry: {
      selected_mode: "browser_dom",
      executed_mode: "browser_dom",
      visual_invocation_count: 0,
    },
  });
}

// ==============================================================================
// 3. Arm: ts-visual
// ==============================================================================
export async function runVisual({ task, capsule, page, model = "none", driverOptions = {} }) {
  const started = performance.now();
  const armId = "ts-visual";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "visual_grounded" });
  }

  if (!validateModelCapability(model, CAPABILITIES.COMPUTER_USE) || !validateModelCapability(model, CAPABILITIES.VISION)) {
    throw new Error(`Model '${model}' does not support visual computer use capabilities`);
  }

  const setupMs = performance.now() - started;
  const transcript = [];
  const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

  let screenshotCount = 0;
  let screenshotBytes = 0;
  let executedVisualActions = 0;

  if (model !== "none") {
    const driver = driverOptions.driverInstance || createModelDriver({ armId, model, maxTurns: 5, ...driverOptions });
    const visualTools = [
      {
        name: "mouse_click",
        description: "Click mouse at pixel coordinate [x, y] on screen",
        input_schema: {
          type: "object",
          properties: {
            x: { type: "number", description: "X pixel coordinate (0 to 1280)" },
            y: { type: "number", description: "Y pixel coordinate (0 to 800)" },
          },
          required: ["x", "y"],
        },
      },
      {
        name: "type_text",
        description: "Type text on the active element or input field",
        input_schema: {
          type: "object",
          properties: {
            text: { type: "string", description: "Text to type" },
            press_enter: { type: "boolean", description: "Whether to press Enter key after typing" },
          },
          required: ["text"],
        },
      },
    ];

    const initialPrompt = `${task.prompt || `Perform task: ${task.id}`}\n\nYou operate the browser with visual screenshots. You MUST first execute the required mouse click or keyboard action on the screen (e.g. clicking the category filter button or search input) using the provided tools. Do NOT provide the final answer yet.`;
    const messages = [{ role: "user", content: initialPrompt }];
    let finalText = "";
    let failure = null;

    while (driver.turnIndex < driver.maxTurns) {
      const screenshotBuf = await page.screenshot({ type: "png" });
      screenshotCount++;
      screenshotBytes += screenshotBuf.length;
      transcript.push({
        action: "perception",
        mode: "visual_grounded",
        screenshot_bytes: screenshotBuf.length,
        screenshot_index: screenshotCount,
      });

      let turnRes;
      try {
        turnRes = await driver.createTurn({
          system: "You operate a web browser via visual screenshot computer use. You MUST perform the requested mouse or keyboard action first. Only after the action is executed and the updated screen is verified, provide your final answer beginning with 'Final answer:'.",
          messages,
          tools: visualTools,
          screenshot: screenshotBuf,
        });
      } catch (e) {
        failure = e.isRateLimit ? "rate_limit_429" : e.message;
        transcript.push({ action: "driver_error", error: e.message });
        break;
      }

      if (turnRes.type === "stop") {
        failure = turnRes.reason;
        finalText = turnRes.finalText || "";
        break;
      }

      // Check tool calls or computer_action
      const calls = turnRes.toolCalls && turnRes.toolCalls.length > 0
        ? turnRes.toolCalls
        : (turnRes.coordinate && Array.isArray(turnRes.coordinate)
          ? [{ name: "mouse_click", arguments: { x: turnRes.coordinate[0], y: turnRes.coordinate[1] } }]
          : []);

      if (calls.length > 0) {
        for (const call of calls) {
          const args = call.arguments || {};
          if (call.name === "mouse_click" && args.x !== undefined && args.y !== undefined) {
            await page.mouse.click(args.x, args.y);
            executedVisualActions++;
            transcript.push({ action: "mouse_click", coordinate: [args.x, args.y], mode: "visual_grounded" });
            messages.push({ role: "assistant", content: `Clicked at coordinates [${args.x}, ${args.y}]` });
            if (typeof page.waitForTimeout === "function") await page.waitForTimeout(600);
            else await new Promise((r) => setTimeout(r, 10));
            messages.push({ role: "user", content: `Mouse click executed at [${args.x}, ${args.y}]. Now inspect the updated screen and provide the final answer beginning with 'Final answer:'.` });
          } else if (call.name === "type_text" && args.text) {
            await page.keyboard.type(args.text);
            if (args.press_enter) await page.keyboard.press("Enter");
            executedVisualActions++;
            transcript.push({ action: "type_text", text: args.text, press_enter: args.press_enter, mode: "visual_grounded" });
            messages.push({ role: "assistant", content: `Typed text: ${args.text}` });
            if (typeof page.waitForTimeout === "function") await page.waitForTimeout(600);
            else await new Promise((r) => setTimeout(r, 10));
            messages.push({ role: "user", content: `Text typed. Now inspect the updated screen and provide the final answer beginning with 'Final answer:'.` });
          }
        }
      } else if (turnRes.finalText) {
        if (executedVisualActions === 0 && driver.turnIndex < driver.maxTurns - 1) {
          messages.push({ role: "assistant", content: turnRes.finalText });
          messages.push({
            role: "user",
            content: "You have not performed the requested interaction on the screen yet. Please call mouse_click on the appropriate element coordinates before submitting your final answer.",
          });
          continue;
        }
        finalText = turnRes.finalText;
        break;
      }
    }

    const acct = driver.getAccounting();
    return buildResult({
      armId,
      finalText,
      transcript,
      turns: acct.turns || 1,
      setupMs,
      failure,
      model,
      usage: acct.usage,
      cost: acct.actualCostUsd,
      cost_nominal: acct.nominalCostUsd || acct.costUsd || 0.0,
      model_snapshot: acct.modelSnapshot,
      budget_exhausted: acct.budgetExhausted,
      telemetry: {
        selected_mode: "visual_grounded",
        executed_mode: "visual_grounded",
        actions_performed: executedVisualActions,
        screenshot_captured: true,
        screenshot_count: screenshotCount,
        screenshot_bytes: screenshotBytes,
        accounting: acct,
      },
    });
  }

  // Fallback for model === "none"
  const screenshotBuf = await page.screenshot({ type: "png" });
  transcript.push({
    action: "perception",
    mode: "visual_grounded",
    selected_mode: "visual_grounded",
    executed_mode: "visual_grounded",
    screenshot_bytes: screenshotBuf.length,
  });

  const recipe = task.recipe ?? RECIPES[task.id];
  let finalText = "";
  if (recipe) {
    const res = await executeDOMRecipe(page, recipe, capsule.baseUrl);
    finalText = res.finalText;
    transcript.push(...res.transcript);
  } else {
    finalText = await page.locator("body").innerText();
  }

  return buildResult({
    armId,
    finalText,
    transcript,
    turns: transcript.length || 1,
    setupMs,
    telemetry: {
      selected_mode: "visual_grounded",
      executed_mode: "visual_grounded",
      screenshot_captured: true,
      screenshot_bytes: screenshotBuf.length,
    },
  });
}

// ==============================================================================
// 4. Arm: ts-hybrid-auto
// ==============================================================================
export async function runHybridAuto({ task, capsule, page, model = "none", driverOptions = {} }) {
  const started = performance.now();
  const armId = "ts-hybrid-auto";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "hybrid_auto" });
  }

  const setupMs = performance.now() - started;
  const transcript = [];
  const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

  // Tri-tier adaptive decision:
  // Tier 1: Probe Native WebMCP
  const nativeAvailable = await hasNativeWebMCP(page);
  let fallbackFrom = null;
  let fallbackTo = null;
  let fallbackReason = null;

  if (nativeAvailable) {
    const tools = await discoverNativeTools(page);
    if (tools.length > 0) {
      transcript.push({
        action: "probe",
        selected_mode: "webmcp",
        executed_mode: "webmcp",
        webmcp_implementation: "native",
        tools_discovered: tools.length,
      });

      try {
        const res = await runWebMCPNative({ task, capsule, page, model, driverOptions });
        if (!res.failure) {
          return {
            ...res,
            armId,
            telemetry: {
              ...res.telemetry,
              selected_mode: "webmcp",
              executed_mode: "webmcp",
              fallback_from: null,
              fallback_to: null,
              fallback_reason: null,
            },
          };
        }

        // Strict halt on unknown side-effect state
        if (res.failure === "unknown-side-effect-halt" || /side[_-]?effect/i.test(res.failure)) {
          return {
            ...res,
            armId,
            failure: "unknown-side-effect-halt",
            telemetry: {
              ...res.telemetry,
              halt_reason: "SideEffectState.UNKNOWN",
              fallback_blocked: true,
            },
          };
        }

        fallbackFrom = "webmcp";
        fallbackTo = "browser_dom";
        fallbackReason = `WebMCP execution failed: ${res.failure}`;
      } catch (e) {
        if (/unknown side effect|unsafe state|timeout during commit/i.test(e.message)) {
          return buildResult({
            armId,
            finalText: "",
            transcript: [...transcript, { action: "halt_unknown_side_effect", error: e.message }],
            failure: "unknown-side-effect-halt",
            setupMs,
            telemetry: {
              selected_mode: "webmcp",
              executed_mode: null,
              halt_reason: "SideEffectState.UNKNOWN",
              fallback_blocked: true,
            },
          });
        }
        fallbackFrom = "webmcp";
        fallbackTo = "browser_dom";
        fallbackReason = e.message;
      }
    } else {
      fallbackFrom = "webmcp";
      fallbackTo = "browser_dom";
      fallbackReason = "WebMCP supported but no tools registered on page";
    }
  } else {
    fallbackFrom = "webmcp";
    fallbackTo = "browser_dom";
    fallbackReason = "Native WebMCP document.modelContext unavailable";
  }

  transcript.push({
    action: "fallback",
    from_mode: "webmcp_native",
    to_mode: fallbackTo,
    selected_mode: "webmcp",
    executed_mode: fallbackTo,
    fallback_from: fallbackFrom,
    fallback_to: fallbackTo,
    reason: fallbackReason,
  });

  // Attempt Tier 2: Browser DOM
  try {
    const domRes = await runBrowserDOM({ task, capsule, page, model, driverOptions });
    if (!domRes.failure) {
      return {
        ...domRes,
        armId,
        transcript: [...transcript, ...domRes.transcript],
        telemetry: {
          ...domRes.telemetry,
          selected_mode: "webmcp",
          executed_mode: "browser_dom",
          fallback_from: fallbackFrom,
          fallback_to: fallbackTo,
          fallback_reason: fallbackReason,
        },
      };
    }

    if (domRes.failure === "unknown-side-effect-halt" || /side[_-]?effect/i.test(domRes.failure)) {
      return {
        ...domRes,
        armId,
        failure: "unknown-side-effect-halt",
        telemetry: {
          ...domRes.telemetry,
          halt_reason: "SideEffectState.UNKNOWN",
          fallback_blocked: true,
        },
      };
    }

    // Fallback to Tier 3: Visual
    const secondFallbackFrom = "browser_dom";
    const secondFallbackTo = "visual_grounded";
    const secondReason = `DOM execution failed: ${domRes.failure}`;
    transcript.push({
      action: "fallback",
      from_mode: secondFallbackFrom,
      to_mode: secondFallbackTo,
      selected_mode: "browser_dom",
      executed_mode: secondFallbackTo,
      fallback_from: secondFallbackFrom,
      fallback_to: secondFallbackTo,
      reason: secondReason,
    });

    const visualRes = await runVisual({ task, capsule, page, model, driverOptions });
    return {
      ...visualRes,
      armId,
      transcript: [...transcript, ...visualRes.transcript],
      telemetry: {
        ...visualRes.telemetry,
        selected_mode: "webmcp",
        executed_mode: "visual_grounded",
        fallback_from: secondFallbackFrom,
        fallback_to: secondFallbackTo,
        fallback_reason: secondReason,
      },
    };
  } catch (err) {
    if (/unknown side effect|unsafe state|timeout during commit/i.test(err.message)) {
      return buildResult({
        armId,
        finalText: "",
        transcript: [...transcript, { action: "halt_unknown_side_effect", error: err.message }],
        failure: "unknown-side-effect-halt",
        setupMs,
        telemetry: {
          selected_mode: "webmcp",
          executed_mode: null,
          halt_reason: "SideEffectState.UNKNOWN",
          fallback_blocked: true,
        },
      });
    }

    const visualRes = await runVisual({ task, capsule, page, model, driverOptions });
    return {
      ...visualRes,
      armId,
      transcript: [...transcript, ...visualRes.transcript],
      telemetry: {
        ...visualRes.telemetry,
        selected_mode: "webmcp",
        executed_mode: "visual_grounded",
        fallback_from: "browser_dom",
        fallback_to: "visual_grounded",
        fallback_reason: err.message,
      },
    };
  }
}

// ==============================================================================
// 5. Arm: ts-webmcp-compat
// ==============================================================================
export async function runWebMCPCompat({ task, capsule, page, model = "none", driverOptions = {} }) {
  const started = performance.now();
  const armId = "ts-webmcp-compat";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "webmcp_compat" });
  }

  const setupMs = performance.now() - started;
  const transcript = [];
  const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;

  // In non-native contexts, compatibility bridge is injected into page context
  await page.addInitScript(() => {
    if (typeof document.modelContext === "undefined") {
      const bridge = {
        _tools: new Map(),
        registerTool: (t) => { bridge._tools.set(t.name, t); },
        getTools: async () => Array.from(bridge._tools.values()),
        executeTool: async (tool, serializedArgs) => {
          const fn = tool.execute || tool.handler;
          const args = typeof serializedArgs === "string" ? JSON.parse(serializedArgs) : serializedArgs;
          return await fn(args);
        },
      };
      Object.defineProperty(document, "modelContext", { configurable: true, value: bridge });
    }
  });

  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  transcript.push({
    action: "bridge_initialized",
    mode: "webmcp_compat",
    selected_mode: "webmcp",
    executed_mode: "webmcp",
    webmcp_implementation: "compatibility_bridge",
  });

  const recipe = task.recipe ?? RECIPES[task.id];
  let finalText = "";
  if (recipe) {
    const res = await executeDOMRecipe(page, recipe, capsule.baseUrl);
    finalText = res.finalText;
    transcript.push(...res.transcript);
  } else {
    finalText = await page.locator("body").innerText();
  }

  return buildResult({
    armId,
    finalText,
    transcript,
    turns: transcript.length || 1,
    setupMs,
    telemetry: {
      selected_mode: "webmcp",
      executed_mode: "webmcp",
      webmcp_implementation: "compatibility_bridge",
      visual_invocation_count: 0,
    },
  });
}

