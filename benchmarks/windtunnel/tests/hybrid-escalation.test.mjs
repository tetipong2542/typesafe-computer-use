/**
 * @fileoverview Test suite for Dynamic DOM -> Visual Escalation in TypeSafe WindTunnel arms.
 * Verifies stall detection, action loops, unified accounting, SideEffectState.UNKNOWN halt,
 * and zero-visual invocation guarantees for non-hybrid arms.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { FakeModelDriver } from "../arms/model-driver.mjs";
import {
  runBrowserDOM,
  runVisual,
  runHybridAuto,
  runWebMCPNative,
} from "../arms/typesafe.mjs";

function createMockPage({ onGoto, onScreenshot, onClick, onFill, initialText = "Initial page text" } = {}) {
  let currentText = initialText;
  return {
    async goto(url) {
      if (onGoto) onGoto(url);
    },
    url() {
      return "http://localhost:3000/directory";
    },
    async screenshot() {
      if (onScreenshot) onScreenshot();
      return Buffer.from("fake_png_screenshot");
    },
    async evaluate(fn) {
      // Return false for document.modelContext to simulate DOM-only page
      return false;
    },
    mouse: {
      async click(x, y) {},
      async dblclick(x, y) {},
      async move(x, y) {},
      async down() {},
      async up() {},
      async wheel() {},
    },
    keyboard: {
      async type(text) {},
      async press(key) {},
      async down(key) {},
      async up(key) {},
    },
    locator(selector) {
      return {
        first() { return this; },
        async click() {
          if (onClick) onClick(selector);
        },
        async fill(val) {
          if (onFill) onFill(selector, val);
        },
        async press() {},
        async innerText() {
          return currentText;
        },
      };
    },
    setText(t) {
      currentText = t;
    },
  };
}

// ==============================================================================
// 1. DOM stall detection (consecutive unchanged state >= 2)
// ==============================================================================
test("1. runBrowserDOM with allowEscalation: halts with dom_stalled_or_budget_exhausted when progress stalls", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = createMockPage();

  // Driver produces 2 clicks that do not change page text or URL
  const driver = new FakeModelDriver({
    armId: "ts-browser-dom",
    model: "fake",
    maxTurns: 5,
    scriptedTurns: [
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "c1", name: "click", arguments: { selector: "button#stall-1" } }],
      },
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "c2", name: "click", arguments: { selector: "button#stall-2" } }],
      },
      {
        type: "final_answer",
        finalText: "Final answer: never reached",
        toolCalls: [],
      },
    ],
  });

  const task = { id: "directory-detail", prompt: "Open details for Vercel" };
  const capsule = { baseUrl: "http://localhost:3000" };

  const res = await runBrowserDOM({
    task,
    capsule,
    page,
    model: "gpt-5.6-sol",
    driverOptions: { driverInstance: driver, allowEscalation: true },
  });

  assert.equal(res.failure, "dom_stalled_or_budget_exhausted");
  assert.equal(res.telemetry.visual_invocation_count, 0, "Standalone DOM mode must have zero visual invocations");
  assert.equal(res.telemetry.consecutive_stalls >= 2, true);
  const stallEvent = res.transcript.find((t) => t.action === "dom_stall_detected");
  assert.ok(stallEvent, "Transcript must record dom_stall_detected");
  assert.equal(stallEvent.reason, "dom_progress_stalled_consecutive");
});

// ==============================================================================
// 2. Action loop detection in DOM mode
// ==============================================================================
test("2. runBrowserDOM with allowEscalation: detects action loop and escalates", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = createMockPage();

  // Driver repeats the exact same selector click twice without state change
  const driver = new FakeModelDriver({
    armId: "ts-browser-dom",
    model: "fake",
    maxTurns: 5,
    scriptedTurns: [
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "c1", name: "click", arguments: { selector: "button.same-target" } }],
      },
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "c2", name: "click", arguments: { selector: "button.same-target" } }],
      },
    ],
  });

  const task = { id: "directory-detail", prompt: "Click target" };
  const capsule = { baseUrl: "http://localhost:3000" };

  const res = await runBrowserDOM({
    task,
    capsule,
    page,
    model: "gpt-5.6-sol",
    driverOptions: { driverInstance: driver, allowEscalation: true },
  });

  assert.equal(res.failure, "dom_stalled_or_budget_exhausted");
  assert.equal(res.telemetry.loop_detected, true);
  const stallEvent = res.transcript.find((t) => t.action === "dom_stall_detected");
  assert.ok(stallEvent);
  assert.equal(stallEvent.reason, "dom_action_loop_detected");
});

// ==============================================================================
// 3. ts-hybrid-auto dynamic escalation with unified accounting
// ==============================================================================
test("3. ts-hybrid-auto: escalates from stalled DOM to Visual and unifies accounting", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  let visualScreenshotCount = 0;
  let gotoCalls = 0;

  const page = createMockPage({
    onGoto: () => gotoCalls++,
    onScreenshot: () => visualScreenshotCount++,
  });

  // Turn 1 & 2 in DOM mode: stall.
  // Then visual mode takes over with fresh turn.
  let domDriverCreated = false;
  const originalCreate = FakeModelDriver;

  // We test runHybridAuto using driverOptions injection
  const domDriver = new FakeModelDriver({
    armId: "ts-browser-dom",
    model: "fake",
    maxTurns: 5,
    scriptedTurns: [
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "c1", name: "click", arguments: { selector: "button#broken" } }],
      },
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "c2", name: "click", arguments: { selector: "button#broken" } }],
      },
    ],
  });

  const visualDriver = new FakeModelDriver({
    armId: "ts-visual",
    model: "fake",
    maxTurns: 5,
    scriptedTurns: [
      {
        type: "tool_use",
        finalText: "",
        toolCalls: [{ id: "v1", name: "mouse_click", arguments: { x: 450, y: 320 } }],
      },
      {
        type: "final_answer",
        finalText: "Final answer: Vercel frontend cloud platform",
        toolCalls: [],
      },
    ],
  });

  const task = { id: "directory-detail", prompt: "Inspect Vercel details" };
  const capsule = { baseUrl: "http://localhost:3000" };

  // To simulate custom driver per phase in hybrid test, wrap driverOptions
  let callCount = 0;
  const hybridDriverFactory = {
    get driverInstance() {
      callCount++;
      return callCount === 1 ? domDriver : visualDriver;
    },
  };

  const res = await runHybridAuto({
    task,
    capsule,
    page,
    model: "gpt-5.6-sol",
    driverOptions: hybridDriverFactory,
  });

  assert.equal(res.failure, null);
  assert.equal(res.finalText, "Final answer: Vercel frontend cloud platform");
  assert.equal(res.telemetry.selected_mode, "webmcp");
  assert.equal(res.telemetry.executed_mode, "visual_grounded");
  assert.equal(res.telemetry.fallback_from, "browser_dom");
  assert.equal(res.telemetry.fallback_to, "visual_grounded");
  assert.equal(res.telemetry.fallback_reason, "dom_stalled_or_budget_exhausted");
  assert.equal(res.telemetry.visual_invocation_count >= 1, true);

  // Accounting must combine DOM turns (2) + Visual turns (2) = 4
  assert.equal(res.turns, 4);
  assert.equal(res.telemetry.dom_phase_turns, 2);
  assert.equal(res.telemetry.visual_phase_turns, 2);

  // goto should only have been called once at start of hybrid (not reloaded on escalation)
  assert.equal(gotoCalls, 1, "Page must not reload on DOM -> Visual escalation");
});

// ==============================================================================
// 4. Strict halt on SideEffectState.UNKNOWN (no visual retry/fallback)
// ==============================================================================
test("4. ts-hybrid-auto: halts strictly when DOM reports unknown side-effect", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = createMockPage();

  const domDriver = new FakeModelDriver({
    armId: "ts-browser-dom",
    model: "fake",
    maxTurns: 5,
    scriptedTurns: [
      {
        type: "stop",
        reason: "unknown-side-effect-halt",
        finalText: "",
      },
    ],
  });

  const task = { id: "payment-submit", prompt: "Submit payment" };
  const capsule = { baseUrl: "http://localhost:3000" };

  const res = await runHybridAuto({
    task,
    capsule,
    page,
    model: "gpt-5.6-sol",
    driverOptions: { driverInstance: domDriver },
  });

  assert.equal(res.failure, "unknown-side-effect-halt");
  assert.equal(res.telemetry.halt_reason, "SideEffectState.UNKNOWN");
  assert.equal(res.telemetry.fallback_blocked, true);
  assert.equal(res.telemetry.visual_invocation_count, 0, "Visual retry must NOT happen on unknown side-effect");
});

// ==============================================================================
// 5. Zero visual invocation guarantee on non-hybrid arms
// ==============================================================================
test("5. ts-browser-dom and ts-webmcp-native guarantee zero visual invocations", async () => {
  delete process.env.WT_FAKE_LIFECYCLE;
  const page = createMockPage();

  const domDriver = new FakeModelDriver({
    armId: "ts-browser-dom",
    model: "fake",
    maxTurns: 5,
    scriptedTurns: [
      {
        type: "final_answer",
        finalText: "Final answer: DOM answer",
        toolCalls: [],
      },
    ],
  });

  const task = { id: "directory-search", prompt: "Search" };
  const capsule = { baseUrl: "http://localhost:3000" };

  const domRes = await runBrowserDOM({
    task,
    capsule,
    page,
    model: "gpt-5.6-sol",
    driverOptions: { driverInstance: domDriver },
  });
  assert.equal(domRes.telemetry.visual_invocation_count, 0);

  const nativeRes = await runWebMCPNative({
    task,
    capsule,
    page,
    model: "gpt-5.6-sol",
  });
  assert.equal(nativeRes.telemetry.visual_invocation_count, 0);
});
