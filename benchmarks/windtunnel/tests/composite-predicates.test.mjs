import assert from "node:assert/strict";
import test from "node:test";
import { score } from "../scoring/predicates.mjs";

test("composite predicate: all passes when every sub-condition passes", async () => {
  const predicate = {
    all: [
      { type: "answer", contains: ["Figma"] },
      { type: "browser_state", url_path: "/figma" },
      { type: "worker_trace", action: "click" },
    ],
  };

  const mockPage = {
    url: () => "http://127.0.0.1:3215/figma",
  };
  const mockContext = {
    page: mockPage,
    result: {
      url: "http://127.0.0.1:3215/figma",
      transcript: [{ action: "click", selector: "a.card" }],
    },
  };

  const result = await score(predicate, {}, "Figma is the design tool", mockContext);
  assert.equal(result.pass, true);
  assert.match(result.detail, /composite all passed \(3\/3\)/);
  assert.equal(result.sub_results.length, 3);
  assert.equal(result.sub_results.every((r) => r.pass), true);
});

test("composite predicate: all fails when one sub-condition fails and isolates failure", async () => {
  const predicate = {
    all: [
      { type: "answer", contains: ["Figma"] },
      { type: "browser_state", url_path: "/figma" },
    ],
  };

  const mockPage = {
    url: () => "http://127.0.0.1:3215/wrong-page",
  };
  const mockContext = {
    page: mockPage,
    result: { url: "http://127.0.0.1:3215/wrong-page" },
  };

  const result = await score(predicate, {}, "Figma is great", mockContext);
  assert.equal(result.pass, false);
  assert.match(result.detail, /composite all failed \(1\/2 failed\)/);
  assert.match(result.detail, /browser_state failed/);
  assert.equal(result.sub_results[0].pass, true);
  assert.equal(result.sub_results[1].pass, false);
});

test("composite predicate: any passes when at least one sub-condition passes", async () => {
  const predicate = {
    any: [
      { type: "browser_state", url_path: "/figma" },
      { type: "worker_trace", action: "execute_native_tool" },
    ],
  };

  const mockContext = {
    result: {
      transcript: [{ action: "execute_native_tool", tool: "get_bookmark" }],
    },
  };

  const result = await score(predicate, {}, "Some answer", mockContext);
  assert.equal(result.pass, true);
  assert.match(result.detail, /composite any passed/);
});

test("dom_element and form_value predicates verify live page state", async () => {
  const mockPage = {
    url: () => "http://127.0.0.1:3215/search",
    locator: (selector) => ({
      first: () => ({
        isVisible: async () => selector === "#results",
        innerText: async () => selector === "#results" ? "Found 5 items" : "",
        inputValue: async () => selector === "input#query" ? "design" : "",
      }),
    }),
  };

  const context = { page: mockPage };

  // DOM element pass
  const domRes = await score({ type: "dom_element", selector: "#results", visible: true, text_contains: "Found" }, {}, "", context);
  assert.equal(domRes.pass, true);

  // DOM element fail (hidden selector)
  const domFail = await score({ type: "dom_element", selector: "#hidden", visible: true }, {}, "", context);
  assert.equal(domFail.pass, false);

  // Form value pass
  const formRes = await score({ type: "form_value", selector: "input#query", value_equals: "design" }, {}, "", context);
  assert.equal(formRes.pass, true);

  // Form value fail
  const formFail = await score({ type: "form_value", selector: "input#query", value_equals: "dev" }, {}, "", context);
  assert.equal(formFail.pass, false);
});

test("interaction_mode and worker_trace predicates verify telemetry and actions", async () => {
  const context = {
    result: {
      telemetry: {
        executed_mode: "visual_grounded",
        fallback_from: "browser_dom",
        fallback_to: "visual_grounded",
      },
      transcript: [
        { action: "click", selector: "button" },
        { action: "fallback", to_mode: "visual_grounded" },
        { action: "mouse_click", coordinates: [100, 200] },
      ],
    },
  };

  // Expected mode pass
  const modeRes = await score({ type: "interaction_mode", expected_mode: "visual_grounded" }, {}, "", context);
  assert.equal(modeRes.pass, true);

  // Forbidden mode pass (it didn't execute dom alone)
  const forbiddenPass = await score({ type: "interaction_mode", forbidden_mode: "unsupported_mode" }, {}, "", context);
  assert.equal(forbiddenPass.pass, true);

  // Worker trace pass
  const traceRes = await score({ type: "worker_trace", action: "mouse_click", min_actions: 2 }, {}, "", context);
  assert.equal(traceRes.pass, true);

  // Worker trace fail
  const traceFail = await score({ type: "worker_trace", action: "non_existent_action" }, {}, "", context);
  assert.equal(traceFail.pass, false);
});
