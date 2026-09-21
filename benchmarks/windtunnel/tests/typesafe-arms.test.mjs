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
