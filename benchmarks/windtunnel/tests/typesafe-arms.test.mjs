/**
 * @fileoverview Unit tests for TypeSafe Computer Use WindTunnel Benchmark Arms.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  selfCheck,
  runWebMCPNative,
  runBrowserDOM,
  runVisual,
  runHybridAuto,
  runWebMCPCompat,
} from "../arms/typesafe.mjs";
import { score } from "../scoring/predicates.mjs";

test("selfCheck reports framework metadata and all 5 arms", async () => {
  const check = await selfCheck();
  assert.equal(check.ok, true);
  assert.equal(check.framework, "typesafe-computer-use");
  assert.equal(check.arms.length, 5);
  assert.deepEqual(check.arms, [
    "ts-webmcp-native",
    "ts-browser-dom",
    "ts-visual",
    "ts-hybrid-auto",
    "ts-webmcp-compat",
  ]);
});

test("all 5 arms execute cleanly under WT_FAKE_LIFECYCLE=1 with $0 cost", async () => {
  process.env.WT_FAKE_LIFECYCLE = "1";
  const dummyTask = { id: "test-directory-search", prompt: "Find design tool" };
  const dummyCapsule = { baseUrl: "http://localhost:3000" };

  const arms = [
    { name: "ts-webmcp-native", fn: runWebMCPNative },
    { name: "ts-browser-dom", fn: runBrowserDOM },
    { name: "ts-visual", fn: runVisual },
    { name: "ts-hybrid-auto", fn: runHybridAuto },
    { name: "ts-webmcp-compat", fn: runWebMCPCompat },
  ];

  for (const arm of arms) {
    const res = await arm.fn({ task: dummyTask, capsule: dummyCapsule, page: null });
    assert.equal(res.cost, 0.0, `${arm.name} cost must be 0`);
    assert.equal(res.usage.input_tokens, 0);
    assert.equal(res.usage.output_tokens, 0);
    assert.match(res.finalText, /TypeSafe Fake Run/);
    assert.equal(res.transcript[0].arm, arm.name);
  }
});

test("ts-browser-dom executes recipe against mock page", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const calls = [];
  const page = {
    async goto(url) { calls.push({ type: "goto", url }); },
    locator(selector) {
      return {
        async fill(val) { calls.push({ type: "fill", selector, val }); },
        async click() { calls.push({ type: "click", selector }); },
        async innerText() { return "Extracted DOM text"; },
      };
    },
  };

  const res = await runBrowserDOM({
    task: {
      id: "custom-task",
      recipe: [
        { action: "goto", path: "/items" },
        { action: "fill", selector: "#q", value: "search term" },
        { action: "click", selector: "#btn" },
        { action: "text", selector: "#res" },
      ],
    },
    capsule: { baseUrl: "http://example.local" },
    page,
  });

  assert.equal(res.finalText, "Extracted DOM text");
  assert.equal(calls.length, 3);
  assert.equal(calls[0].url, "http://example.local/items");
  assert.equal(res.transcript.length, 4);
});

test("ts-webmcp-native halts gracefully when browser lacks document.modelContext", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = {
    async goto() {},
    async evaluate() {
      // Simulate non-WebMCP browser (document.modelContext is undefined)
      return false;
    },
  };

  const res = await runWebMCPNative({
    task: { id: "test-task" },
    capsule: { baseUrl: "http://example.local" },
    page,
  });

  assert.equal(res.failure, "native-webmcp-unsupported");
  assert.match(res.transcript[0].error, /document\.modelContext is undefined/);
});

test("ts-hybrid-auto probes WebMCP and cleanly falls back to DOM", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const calls = [];
  const page = {
    async goto(url) { calls.push(url); },
    async evaluate() {
      // Native WebMCP unavailable
      return false;
    },
    locator(selector) {
      return {
        async innerText() { return "Fallback body text"; },
      };
    },
  };

  const res = await runHybridAuto({
    task: { id: "test-task" },
    capsule: { baseUrl: "http://example.local" },
    page,
  });

  assert.equal(res.finalText, "Fallback body text");
  assert.equal(res.transcript[0].action, "fallback");
  assert.equal(res.transcript[0].from_mode, "webmcp_native");
  assert.equal(res.transcript[0].to_mode, "browser_dom");
});

test("ts-webmcp-native deterministic pipeline: request -> tool execution -> score pass", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = {
    async goto() {},
    async evaluate(fn, arg) {
      if (arg && arg.name) {
        return "Found Figma collaborative design tool";
      }
      const fnStr = fn ? fn.toString() : "";
      if (fnStr.includes("getTools")) {
        return [{ name: "search_tool", description: "Search tools", input_schema: {} }];
      }
      if (fnStr.includes("modelContext")) {
        return true;
      }
      return null;
    },
  };

  const task = {
    id: "directory-search",
    tool_name: "search_tool",
    tool_args: { query: "design" },
    predicate: {
      type: "answer",
      contains_any: ["figma", "dribbble"],
    },
  };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runWebMCPNative({ task, capsule, page });
  assert.equal(res.failure, null);
  assert.match(res.finalText, /Figma/i);
  assert.equal(res.telemetry.selected_mode, "webmcp");
  assert.equal(res.telemetry.executed_mode, "webmcp");
  assert.equal(res.telemetry.webmcp_implementation, "native");
  assert.equal(res.telemetry.visual_invocation_count, 0);

  const verdict = await score(task.predicate, capsule, res.finalText);
  assert.equal(verdict.pass, true, "WebMCP result must satisfy task predicate");
});

test("ts-browser-dom deterministic pipeline: request -> locator action -> score pass", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  let visualCalls = 0;
  const page = {
    async goto() {},
    async screenshot() { visualCalls++; return Buffer.from(""); },
    locator(selector) {
      return {
        async fill(val) {},
        async click() {},
        async press(k) {},
        async innerText() { return "The collaborative interface design tool"; },
      };
    },
  };
  const task = {
    id: "directory-detail",
    predicate: {
      type: "answer",
      contains: ["collaborative"],
    },
  };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runBrowserDOM({ task, capsule, page });
  assert.equal(res.failure, null);
  assert.equal(visualCalls, 0, "Visual screenshot must never be invoked in ts-browser-dom");
  assert.equal(res.telemetry.selected_mode, "browser_dom");
  assert.equal(res.telemetry.executed_mode, "browser_dom");
  assert.equal(res.telemetry.visual_invocation_count, 0);

  const verdict = await score(task.predicate, capsule, res.finalText);
  assert.equal(verdict.pass, true, "DOM result must satisfy task predicate");
});

test("ts-visual deterministic pipeline: request -> screenshot perception -> score pass", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  let screenshotCaptured = false;
  const page = {
    async goto() {},
    async screenshot() {
      screenshotCaptured = true;
      return Buffer.from("fake_png_bytes");
    },
    locator(selector) {
      return {
        async fill(val) {},
        async click() {},
        async press(k) {},
        async innerText() { return "Figma design listing"; },
      };
    },
  };
  const task = {
    id: "directory-search",
    predicate: {
      type: "answer",
      contains_any: ["figma"],
    },
  };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runVisual({ task, capsule, page });
  assert.equal(res.failure, null);
  assert.equal(screenshotCaptured, true);
  assert.equal(res.telemetry.selected_mode, "visual_grounded");
  assert.equal(res.telemetry.executed_mode, "visual_grounded");
  assert.equal(res.telemetry.screenshot_captured, true);
  assert.equal(res.transcript[0].mode, "visual_grounded");
  assert.equal(res.transcript[0].screenshot_bytes, 14);

  const verdict = await score(task.predicate, capsule, res.finalText);
  assert.equal(verdict.pass, true, "Visual result must satisfy task predicate");
});

test("ts-hybrid-auto deterministic pipeline: WebMCP miss -> DOM fallback -> score pass", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = {
    async goto() {},
    async evaluate() { return false; }, // Native WebMCP unavailable -> cascades
    locator(selector) {
      return {
        async fill(val) {},
        async click() {},
        async press(k) {},
        async innerText() { return "Filtered list: GitHub and React"; },
      };
    },
  };
  const task = {
    id: "directory-filter",
    predicate: {
      type: "answer",
      matches: "\\b(github|react)\\b",
    },
  };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runHybridAuto({ task, capsule, page });
  assert.equal(res.failure, null);
  assert.equal(res.telemetry.selected_mode, "webmcp");
  assert.equal(res.telemetry.executed_mode, "browser_dom");
  assert.equal(res.telemetry.fallback_from, "webmcp");
  assert.equal(res.telemetry.fallback_to, "browser_dom");
  assert.match(res.telemetry.fallback_reason, /Native WebMCP/);
  assert.equal(res.transcript[0].action, "fallback");
  assert.equal(res.transcript[0].to_mode, "browser_dom");

  const verdict = await score(task.predicate, capsule, res.finalText);
  assert.equal(verdict.pass, true, "Hybrid auto fallback result must satisfy task predicate");
});

test("ts-hybrid-auto adaptive routing selects native WebMCP when available", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = {
    async goto() {},
    async evaluate(fn, arg) {
      if (arg && arg.name) return "Direct Native WebMCP Result";
      const fnStr = fn ? fn.toString() : "";
      if (fnStr.includes("getTools")) {
        return [{ name: "filter_tool", description: "Filter items", input_schema: {} }];
      }
      if (fnStr.includes("modelContext")) return true;
      return null;
    },
    locator() {
      return { async innerText() { return "DOM fallback"; } };
    },
  };
  const task = {
    id: "directory-filter",
    tool_name: "filter_tool",
    tool_args: {},
  };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runHybridAuto({ task, capsule, page });
  assert.equal(res.failure, null);
  assert.equal(res.telemetry.selected_mode, "webmcp");
  assert.equal(res.telemetry.executed_mode, "webmcp");
  assert.equal(res.telemetry.webmcp_implementation, "native");
  assert.equal(res.telemetry.fallback_from, null);
});

test("ts-webmcp-compat sets compatibility_bridge and never reports as native", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  let scriptInjected = false;
  const page = {
    async addInitScript() { scriptInjected = true; },
    async goto() {},
    locator() {
      return { async innerText() { return "Compat Bridge Result"; } };
    },
  };
  const task = { id: "test-compat" };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runWebMCPCompat({ task, capsule, page });
  assert.equal(res.failure, null);
  assert.equal(scriptInjected, true);
  assert.equal(res.telemetry.selected_mode, "webmcp");
  assert.equal(res.telemetry.executed_mode, "webmcp");
  assert.equal(res.telemetry.webmcp_implementation, "compatibility_bridge");
  assert.notEqual(res.telemetry.webmcp_implementation, "native");
  assert.equal(res.telemetry.visual_invocation_count, 0);
  assert.equal(res.transcript[0].action, "bridge_initialized");
  assert.equal(res.transcript[0].webmcp_implementation, "compatibility_bridge");
});

test("ts-webmcp-native fails when tool list is empty on page", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = {
    async goto() {},
    async evaluate(fn) {
      const fnStr = fn ? fn.toString() : "";
      if (fnStr.includes("getTools")) return []; // 0 tools
      if (fnStr.includes("modelContext")) return true; // native supported
      return null;
    },
  };
  const task = { id: "test-empty-tools" };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runWebMCPNative({ task, capsule, page });
  assert.equal(res.failure, "no-webmcp-tools");
  assert.equal(res.telemetry.selected_mode, "webmcp");
  assert.equal(res.telemetry.executed_mode, null);
  assert.equal(res.telemetry.webmcp_implementation, "native");
  assert.equal(res.telemetry.tools_wait_timed_out, true);
  assert.equal(res.telemetry.tools_discovered, 0);
});

test("ts-webmcp-native waits for tools to stabilize and records diagnostics", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  let evaluateCount = 0;
  const page = {
    async goto() {},
    async evaluate(fn, arg) {
      if (arg && arg.name) {
        return "Tool executed";
      }
      const fnStr = fn ? fn.toString() : "";
      if (fnStr.includes("modelContext") && !fnStr.includes("getTools")) {
        return true;
      }
      if (fnStr.includes("getTools")) {
        evaluateCount++;
        // First check returns 0 tools (simulating Next.js React pre-hydration)
        // Subsequent checks return 1 tool (simulating post-hydration stabilization)
        if (evaluateCount === 1) return [];
        return [{ name: "search_tool", description: "Search", input_schema: {} }];
      }
      return null;
    },
  };
  const task = { id: "directory-search", tool_name: "search_tool" };
  const capsule = { baseUrl: "http://example.local" };

  const res = await runWebMCPNative({ task, capsule, page, driverOptions: { toolsWaitTimeout: 500 } });
  assert.equal(res.failure, null);
  assert.equal(res.telemetry.tools_wait_timed_out, false);
  assert.equal(res.telemetry.tools_discovered, 1);
  assert.equal(typeof res.telemetry.tools_wait_ms, "number");
  const waitEntry = res.transcript.find((e) => e.action === "wait_tools");
  assert.ok(waitEntry);
  assert.equal(waitEntry.tools_wait_timed_out, false);
  assert.equal(waitEntry.tools_stabilized_count, 1);
});

