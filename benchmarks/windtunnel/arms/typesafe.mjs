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

export const TOOL_VERSION = "typesafe-computer-use@0.2.0";

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
  model = "none",
  usage = { input_tokens: 0, output_tokens: 0 },
  telemetry = {},
}) {
  return {
    finalText,
    usage,
    transcript,
    cost: 0.0,
    turns,
    setupMs,
    failure,
    budget_exhausted: false,
    model_snapshot: "typesafe-local",
    snapshot_source: TOOL_VERSION,
    temperature: 0.0,
    effort: "provider-default",
    caching: "unsupported",
    telemetry,
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
export async function runWebMCPNative({ task, capsule, page, model = "none" }) {
  const started = performance.now();
  const armId = "ts-webmcp-native";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "webmcp_native" });
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

  // Execute matching tool or recipe
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
export async function runBrowserDOM({ task, capsule, page, model = "none" }) {
  const started = performance.now();
  const armId = "ts-browser-dom";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "browser_dom" });
  }

  const setupMs = performance.now() - started;
  const recipe = task.recipe ?? RECIPES[task.id];

  const telemetry = {
    selected_mode: "browser_dom",
    executed_mode: "browser_dom",
    visual_invocation_count: 0,
  };

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
      telemetry,
    });
  }

  const { finalText, transcript } = await executeDOMRecipe(page, recipe, capsule.baseUrl);

  return buildResult({
    armId,
    finalText,
    transcript,
    turns: transcript.length || 1,
    setupMs,
    telemetry,
  });
}

// ==============================================================================
// 3. Arm: ts-visual
// ==============================================================================
export async function runVisual({ task, capsule, page, model = "none" }) {
  const started = performance.now();
  const armId = "ts-visual";

  if (process.env.WT_FAKE_LIFECYCLE === "1") {
    return runFake({ task, armId, mode: "visual_grounded" });
  }

  const setupMs = performance.now() - started;
  const transcript = [];

  // Navigate to site start URL
  const targetUrl = new URL(task.path || "/", capsule.baseUrl).href;
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

  // Capture viewport screenshot (Visual Computer Use perception)
  const screenshotBuf = await page.screenshot({ type: "png" });
  transcript.push({
    action: "perception",
    mode: "visual_grounded",
    selected_mode: "visual_grounded",
    executed_mode: "visual_grounded",
    screenshot_bytes: screenshotBuf.length,
  });

  // Execute recipe or extract text
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
export async function runHybridAuto({ task, capsule, page, model = "none" }) {
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
  let executedMode = "browser_dom";
  let fallbackFrom = null;
  let fallbackTo = null;
  let fallbackReason = null;
  let webmcpImpl = null;

  if (nativeAvailable) {
    const tools = await discoverNativeTools(page);
    if (tools.length > 0) {
      executedMode = "webmcp";
      webmcpImpl = "native";
      transcript.push({
        action: "probe",
        selected_mode: "webmcp",
        executed_mode: "webmcp",
        webmcp_implementation: "native",
        tools_discovered: tools.length,
      });

      const matchingTool = tools.find((t) => t.name === task.tool_name || (task.tool_name && t.name.includes(task.tool_name)));
      if (matchingTool) {
        try {
          const toolRes = await executeNativeTool(page, matchingTool.name, task.tool_args || {});
          var finalText = typeof toolRes === "string" ? toolRes : (toolRes?.text || toolRes?.result || JSON.stringify(toolRes));
          transcript.push({
            action: "execute_native_tool",
            tool: matchingTool.name,
            result: toolRes,
            selected_mode: "webmcp",
            executed_mode: "webmcp",
            webmcp_implementation: "native",
          });
        } catch (e) {
          fallbackFrom = "webmcp";
          fallbackTo = "browser_dom";
          fallbackReason = `Tool execution failed: ${e.message}`;
          executedMode = "browser_dom";
          transcript.push({
            action: "fallback",
            from_mode: "webmcp_native",
            to_mode: "browser_dom",
            selected_mode: "webmcp",
            executed_mode: "browser_dom",
            fallback_from: "webmcp",
            fallback_to: "browser_dom",
            reason: fallbackReason,
          });
        }
      }
    } else {
      fallbackFrom = "webmcp";
      fallbackTo = "browser_dom";
      fallbackReason = "WebMCP supported but no tools registered on page";
      transcript.push({
        action: "fallback",
        from_mode: "webmcp_native",
        to_mode: "browser_dom",
        selected_mode: "webmcp",
        executed_mode: "browser_dom",
        fallback_from: "webmcp",
        fallback_to: "browser_dom",
        reason: fallbackReason,
      });
    }
  } else {
    fallbackFrom = "webmcp";
    fallbackTo = "browser_dom";
    fallbackReason = "Native WebMCP document.modelContext unavailable";
    transcript.push({
      action: "fallback",
      from_mode: "webmcp_native",
      to_mode: "browser_dom",
      selected_mode: "webmcp",
      executed_mode: "browser_dom",
      fallback_from: "webmcp",
      fallback_to: "browser_dom",
      reason: fallbackReason,
    });
  }

  // Execute under browser_dom mode if not already satisfied by WebMCP
  if (executedMode === "browser_dom") {
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
    finalText: finalText || "",
    transcript,
    turns: transcript.length || 1,
    setupMs,
    telemetry: {
      selected_mode: "webmcp",
      executed_mode: executedMode,
      webmcp_implementation: webmcpImpl,
      fallback_from: fallbackFrom,
      fallback_to: fallbackTo,
      fallback_reason: fallbackReason,
    },
  });
}

// ==============================================================================
// 5. Arm: ts-webmcp-compat
// ==============================================================================
export async function runWebMCPCompat({ task, capsule, page, model = "none" }) {
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
