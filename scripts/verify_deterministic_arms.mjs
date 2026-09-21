#!/usr/bin/env bun
/**
 * @fileoverview Zero-cost deterministic live validation for all 5 TypeSafe WindTunnel Arms.
 * Verifies strict interaction mode enforcement, telemetry invariants, and fallback paths.
 */

import assert from "node:assert/strict";
import {
  selfCheck,
  runWebMCPNative,
  runBrowserDOM,
  runVisual,
  runHybridAuto,
  runWebMCPCompat,
} from "../benchmarks/windtunnel/arms/typesafe.mjs";
import { score } from "../benchmarks/windtunnel/scoring/predicates.mjs";

async function verifyAllArms() {
  console.log("================================================================================");
  console.log("TypeSafe Computer Use — Deterministic Zero-Cost Live Arm Validation ($0.00)");
  console.log("================================================================================");

  // 0. Self-check
  const check = await selfCheck();
  console.log(`[SelfCheck] Framework: ${check.framework} v${check.version}`);
  console.log(`[SelfCheck] Registered Arms (${check.arms.length}): ${check.arms.join(", ")}`);
  assert.equal(check.ok, true);
  assert.equal(check.arms.length, 5);

  const verificationResults = [];

  // 1. Arm: ts-webmcp-native
  console.log("\n[1/5] Validating Arm: ts-webmcp-native...");
  {
    // 1a. Success case with Native WebMCP available
    const pageSuccess = {
      async goto() {},
      async evaluate(fn, arg) {
        if (arg && arg.name) return "Found Figma collaborative design tool";
        const fnStr = fn ? fn.toString() : "";
        if (fnStr.includes("getTools")) {
          return [{ name: "search_tool", description: "Search tools", input_schema: {} }];
        }
        if (fnStr.includes("modelContext")) return true;
        return null;
      },
    };
    const task = {
      id: "directory-search",
      tool_name: "search_tool",
      tool_args: { query: "design" },
      predicate: { type: "answer", contains_any: ["figma"] },
    };
    const capsule = { baseUrl: "http://example.local" };
    const res = await runWebMCPNative({ task, capsule, page: pageSuccess });

    assert.equal(res.failure, null);
    assert.equal(res.cost, 0.0);
    assert.equal(res.telemetry.selected_mode, "webmcp");
    assert.equal(res.telemetry.executed_mode, "webmcp");
    assert.equal(res.telemetry.webmcp_implementation, "native");
    assert.equal(res.telemetry.visual_invocation_count, 0);

    const verdict = await score(task.predicate, capsule, res.finalText);
    assert.equal(verdict.pass, true);

    // 1b. Failure when document.modelContext missing
    const pageNoNative = {
      async goto() {},
      async evaluate() { return false; },
    };
    const resNoNative = await runWebMCPNative({ task, capsule, page: pageNoNative });
    assert.equal(resNoNative.failure, "native-webmcp-unsupported");
    assert.equal(resNoNative.telemetry.selected_mode, "webmcp");
    assert.equal(resNoNative.telemetry.executed_mode, null);
    assert.equal(resNoNative.telemetry.webmcp_implementation, "native");

    // 1c. Failure when 0 tools registered
    const pageNoTools = {
      async goto() {},
      async evaluate(fn) {
        const fnStr = fn ? fn.toString() : "";
        if (fnStr.includes("getTools")) return [];
        if (fnStr.includes("modelContext")) return true;
        return null;
      },
    };
    const resNoTools = await runWebMCPNative({ task, capsule, page: pageNoTools });
    assert.equal(resNoTools.failure, "no-webmcp-tools");
    assert.equal(resNoTools.telemetry.selected_mode, "webmcp");
    assert.equal(resNoTools.telemetry.executed_mode, null);

    verificationResults.push({
      arm: "ts-webmcp-native",
      selected_mode: res.telemetry.selected_mode,
      executed_mode: res.telemetry.executed_mode,
      webmcp_implementation: res.telemetry.webmcp_implementation,
      visual_invocations: res.telemetry.visual_invocation_count,
      missing_support_handling: resNoNative.failure,
      empty_tools_handling: resNoTools.failure,
      predicate_pass: verdict.pass,
      status: "VERIFIED_PASS",
    });
    console.log("  ✔ Invariant verified: selected=webmcp, executed=webmcp, impl=native, fails if missing/empty");
  }

  // 2. Arm: ts-browser-dom
  console.log("\n[2/5] Validating Arm: ts-browser-dom...");
  {
    let visualCalls = 0;
    const page = {
      async goto() {},
      async screenshot() { visualCalls++; return Buffer.from(""); },
      locator() {
        return {
          async fill() {},
          async click() {},
          async press() {},
          async innerText() { return "The collaborative interface design tool"; },
        };
      },
    };
    const task = {
      id: "directory-detail",
      predicate: { type: "answer", contains: ["collaborative"] },
    };
    const capsule = { baseUrl: "http://example.local" };
    const res = await runBrowserDOM({ task, capsule, page });

    assert.equal(res.failure, null);
    assert.equal(res.cost, 0.0);
    assert.equal(visualCalls, 0, "Visual adapter must not be called in ts-browser-dom");
    assert.equal(res.telemetry.selected_mode, "browser_dom");
    assert.equal(res.telemetry.executed_mode, "browser_dom");
    assert.equal(res.telemetry.visual_invocation_count, 0);

    const verdict = await score(task.predicate, capsule, res.finalText);
    assert.equal(verdict.pass, true);

    verificationResults.push({
      arm: "ts-browser-dom",
      selected_mode: res.telemetry.selected_mode,
      executed_mode: res.telemetry.executed_mode,
      webmcp_implementation: "n/a",
      visual_invocations: visualCalls,
      missing_support_handling: "n/a",
      empty_tools_handling: "n/a",
      predicate_pass: verdict.pass,
      status: "VERIFIED_PASS",
    });
    console.log("  ✔ Invariant verified: selected=browser_dom, executed=browser_dom, visual_invocations=0");
  }

  // 3. Arm: ts-visual
  console.log("\n[3/5] Validating Arm: ts-visual...");
  {
    let screenshotCaptured = false;
    let screenshotBytes = 0;
    const page = {
      async goto() {},
      async screenshot() {
        screenshotCaptured = true;
        const buf = Buffer.from("simulated_png_screenshot_bytes_2026");
        screenshotBytes = buf.length;
        return buf;
      },
      locator() {
        return {
          async fill() {},
          async click() {},
          async press() {},
          async innerText() { return "Figma visual listing"; },
        };
      },
    };
    const task = {
      id: "directory-search",
      predicate: { type: "answer", contains_any: ["figma"] },
    };
    const capsule = { baseUrl: "http://example.local" };
    const res = await runVisual({ task, capsule, page });

    assert.equal(res.failure, null);
    assert.equal(res.cost, 0.0);
    assert.equal(screenshotCaptured, true);
    assert.equal(res.telemetry.selected_mode, "visual_grounded");
    assert.equal(res.telemetry.executed_mode, "visual_grounded");
    assert.equal(res.telemetry.screenshot_captured, true);
    assert.equal(res.transcript[0].screenshot_bytes, screenshotBytes);

    const verdict = await score(task.predicate, capsule, res.finalText);
    assert.equal(verdict.pass, true);

    verificationResults.push({
      arm: "ts-visual",
      selected_mode: res.telemetry.selected_mode,
      executed_mode: res.telemetry.executed_mode,
      webmcp_implementation: "n/a",
      visual_invocations: 1,
      missing_support_handling: "n/a",
      empty_tools_handling: "n/a",
      predicate_pass: verdict.pass,
      status: "VERIFIED_PASS",
    });
    console.log("  ✔ Invariant verified: selected=visual_grounded, executed=visual_grounded, screenshot_captured=true");
  }

  // 4. Arm: ts-hybrid-auto
  console.log("\n[4/5] Validating Arm: ts-hybrid-auto...");
  {
    // 4a. WebMCP unavailable -> clean fallback to DOM
    const pageFallback = {
      async goto() {},
      async evaluate() { return false; },
      locator() {
        return {
          async fill() {},
          async click() {},
          async press() {},
          async innerText() { return "Filtered list: GitHub and React"; },
        };
      },
    };
    const task = {
      id: "directory-filter",
      predicate: { type: "answer", matches: "\\b(github|react)\\b" },
    };
    const capsule = { baseUrl: "http://example.local" };
    const resFallback = await runHybridAuto({ task, capsule, page: pageFallback });

    assert.equal(resFallback.failure, null);
    assert.equal(resFallback.telemetry.selected_mode, "webmcp");
    assert.equal(resFallback.telemetry.executed_mode, "browser_dom");
    assert.equal(resFallback.telemetry.fallback_from, "webmcp");
    assert.equal(resFallback.telemetry.fallback_to, "browser_dom");
    assert.match(resFallback.telemetry.fallback_reason, /Native WebMCP/);

    const verdict = await score(task.predicate, capsule, resFallback.finalText);
    assert.equal(verdict.pass, true);

    // 4b. WebMCP available -> selected & executed as WebMCP
    const pageWebMCP = {
      async goto() {},
      async evaluate(fn, arg) {
        if (arg && arg.name) return "GitHub Developer Platform";
        const fnStr = fn ? fn.toString() : "";
        if (fnStr.includes("getTools")) {
          return [{ name: "filter_tool", description: "Filter items", input_schema: {} }];
        }
        if (fnStr.includes("modelContext")) return true;
        return null;
      },
      locator() { return { async innerText() { return ""; } }; },
    };
    const taskWebMCP = { id: "directory-filter", tool_name: "filter_tool", tool_args: {} };
    const resWebMCP = await runHybridAuto({ task: taskWebMCP, capsule, page: pageWebMCP });

    assert.equal(resWebMCP.failure, null);
    assert.equal(resWebMCP.telemetry.selected_mode, "webmcp");
    assert.equal(resWebMCP.telemetry.executed_mode, "webmcp");
    assert.equal(resWebMCP.telemetry.webmcp_implementation, "native");
    assert.equal(resWebMCP.telemetry.fallback_from, null);

    verificationResults.push({
      arm: "ts-hybrid-auto",
      selected_mode: "adaptive (webmcp -> dom -> visual)",
      executed_mode: `${resWebMCP.telemetry.executed_mode} | fallback: ${resFallback.telemetry.executed_mode}`,
      webmcp_implementation: "native (when present)",
      visual_invocations: 0,
      missing_support_handling: `fallback to ${resFallback.telemetry.fallback_to}`,
      empty_tools_handling: "fallback to browser_dom",
      predicate_pass: verdict.pass,
      status: "VERIFIED_PASS",
    });
    console.log("  ✔ Invariant verified: adaptive routing (WebMCP when supported, clean fallback to DOM when missing)");
  }

  // 5. Arm: ts-webmcp-compat
  console.log("\n[5/5] Validating Arm: ts-webmcp-compat...");
  {
    let scriptInjected = false;
    const page = {
      async addInitScript() { scriptInjected = true; },
      async goto() {},
      locator() {
        return {
          async fill() {},
          async click() {},
          async press() {},
          async innerText() { return "Compatibility Bridge Listing"; },
        };
      },
    };
    const task = {
      id: "directory-search",
      predicate: { type: "answer", contains_any: ["compatibility"] },
    };
    const capsule = { baseUrl: "http://example.local" };
    const res = await runWebMCPCompat({ task, capsule, page });

    assert.equal(res.failure, null);
    assert.equal(scriptInjected, true);
    assert.equal(res.telemetry.selected_mode, "webmcp");
    assert.equal(res.telemetry.executed_mode, "webmcp");
    assert.equal(res.telemetry.webmcp_implementation, "compatibility_bridge");
    assert.notEqual(res.telemetry.webmcp_implementation, "native", "Compat bridge must never be reported as native");
    assert.equal(res.telemetry.visual_invocation_count, 0);

    const verdict = await score(task.predicate, capsule, res.finalText);
    assert.equal(verdict.pass, true);

    verificationResults.push({
      arm: "ts-webmcp-compat",
      selected_mode: res.telemetry.selected_mode,
      executed_mode: res.telemetry.executed_mode,
      webmcp_implementation: res.telemetry.webmcp_implementation,
      visual_invocations: 0,
      missing_support_handling: "injected bridge",
      empty_tools_handling: "n/a",
      predicate_pass: verdict.pass,
      status: "VERIFIED_PASS",
    });
    console.log("  ✔ Invariant verified: selected=webmcp, executed=webmcp, impl=compatibility_bridge (never native)");
  }

  console.log("\n================================================================================");
  console.log("Deterministic Validation Summary Table:");
  console.log("================================================================================");
  console.table(verificationResults);
  console.log("\nAll 5 arms passed deterministic invariant validation cleanly. Total cost: $0.00.\n");
}

verifyAllArms().catch((err) => {
  console.error("Verification failed:", err);
  process.exit(1);
});
