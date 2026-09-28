import { isDeepStrictEqual } from "node:util";

function contains(actual, expected) {
  if (isDeepStrictEqual(actual, expected)) return true;
  if (expected && typeof expected === "object" && !Array.isArray(expected)) {
    if (actual && typeof actual === "object" && Object.entries(expected).every(([key, value]) => contains(actual[key], value))) return true;
  }
  return actual && typeof actual === "object" && Object.values(actual).some((value) => contains(value, expected));
}

function check(actual, assertion = {}) {
  if (Object.hasOwn(assertion, "equals")) return isDeepStrictEqual(actual, assertion.equals);
  if (Object.hasOwn(assertion, "contains")) return contains(actual, assertion.contains);
  if (Object.hasOwn(assertion, "matches")) return new RegExp(assertion.matches).test(String(actual));
  if (Object.hasOwn(assertion, "truthy")) return Boolean(actual) === Boolean(assertion.truthy);
  throw new Error(`unknown assertion: ${Object.keys(assertion).join(",") || "empty"}`);
}

// Agents restate site text with typographic dashes ("3–5 business days") or
// prose ranges ("3 to 5 business days"); score the meaning, not the glyph.
export const normalizeAnswer = (value) => String(value).toLowerCase()
  .replace(/https?:\/\/\S+/g, " ")
  .replace(/[‐-―−]/g, "-")
  .replace(/[’‘`´]/g, "'")
  .replace(/(\d)\s+to\s+(\d)/g, "$1-$2")
  .replace(/[-_]+/g, " ")
  .replace(/\s+/g, " ")
  .trim();

export async function score(predicate, capsule, finalText = "", context = {}) {
  try {
    return await evaluatePredicate(predicate, capsule, finalText, context);
  } catch (error) {
    return { pass: false, detail: `probe-error: ${error.message}` };
  }
}

async function evaluatePredicate(predicate, capsule, finalText = "", context = {}) {
  if (!predicate || typeof predicate !== "object") {
    return { pass: false, detail: "predicate must be a non-null object" };
  }

  const isOfflineTextOnly = capsule === null && !context.page && !context.result && !context.url;
  if (isOfflineTextOnly && predicate.type && predicate.type !== "answer") {
    return { pass: true, detail: `${predicate.type} skipped in offline text evaluation` };
  }

  // 1. Composite AND: predicate.all
  if (Array.isArray(predicate.all)) {
    if (predicate.all.length === 0) throw new Error("composite predicate 'all' cannot be empty");
    const subResults = [];
    for (const sub of predicate.all) {
      const res = await evaluatePredicate(sub, capsule, finalText, context);
      subResults.push({ type: sub.type || "probe", pass: res.pass, detail: res.detail });
    }
    const pass = subResults.every((r) => r.pass);
    const passedCount = subResults.filter((r) => r.pass).length;
    const summary = subResults.map((r) => `${r.type}: ${r.pass ? "pass" : "fail"}`).join(", ");
    const failDetails = subResults.filter((r) => !r.pass).map((r) => r.detail).join("; ");
    const detail = pass
      ? `composite all passed (${passedCount}/${subResults.length}): [${summary}]`
      : `composite all failed (${subResults.length - passedCount}/${subResults.length} failed): ${failDetails}`;
    return { pass, detail, sub_results: subResults };
  }

  // 2. Composite OR: predicate.any
  if (Array.isArray(predicate.any)) {
    if (predicate.any.length === 0) throw new Error("composite predicate 'any' cannot be empty");
    const subResults = [];
    for (const sub of predicate.any) {
      const res = await evaluatePredicate(sub, capsule, finalText, context);
      subResults.push({ type: sub.type || "probe", pass: res.pass, detail: res.detail });
    }
    const pass = subResults.some((r) => r.pass);
    const passedCount = subResults.filter((r) => r.pass).length;
    const summary = subResults.map((r) => `${r.type}: ${r.pass ? "pass" : "fail"}`).join(", ");
    const detail = pass
      ? `composite any passed (${passedCount}/${subResults.length}): [${summary}]`
      : "composite any failed: all sub-predicates failed";
    return { pass, detail, sub_results: subResults };
  }

  // 3. Final answer predicate: predicate.type === 'answer'
  if (predicate.type === "answer") {
    const text = normalizeAnswer(finalText);
    const all = predicate.contains ?? [];
    const any = predicate.contains_any ?? [];
    if (!all.length && !any.length && !predicate.matches) throw new Error("answer predicate has no expected text");
    const pass = all.every((value) => text.includes(normalizeAnswer(value)))
      && (!any.length || any.some((value) => text.includes(normalizeAnswer(value))))
      && (!predicate.matches || new RegExp(predicate.matches, "i").test(finalText));
    if (!pass) return { pass: false, detail: `answer predicate failed: ${JSON.stringify(predicate)}` };
    const forbidden = (predicate.not_contains ?? []).find((value) => text.includes(normalizeAnswer(value)));
    if (forbidden) return { pass: false, detail: `answer predicate failed (negation): ${forbidden}` };
    return { pass: true, detail: "answer predicate passed" };
  }

  // 4. Browser URL / State predicate: predicate.type === 'browser_state' | 'url'
  if (predicate.type === "browser_state" || predicate.type === "url" || predicate.type === "url_path") {
    const page = context.page;
    const currentUrl = page && typeof page.url === "function" ? page.url() : (context.result?.url || context.url || "");
    let urlObj = null;
    try { urlObj = new URL(currentUrl); } catch { /* best effort */ }

    if (predicate.url_path) {
      const expectedPath = predicate.url_path.toLowerCase();
      const actualPath = (urlObj ? urlObj.pathname : currentUrl).toLowerCase();
      const pass = actualPath === expectedPath || actualPath.endsWith(expectedPath) || actualPath.includes(expectedPath);
      if (!pass) return { pass: false, detail: `browser_state failed: path '${actualPath}' does not match expected '${expectedPath}'` };
    }
    if (predicate.url_contains) {
      const pass = currentUrl.toLowerCase().includes(String(predicate.url_contains).toLowerCase());
      if (!pass) return { pass: false, detail: `browser_state failed: URL '${currentUrl}' does not contain '${predicate.url_contains}'` };
    }
    if (predicate.url_matches) {
      const pass = new RegExp(predicate.url_matches, "i").test(currentUrl);
      if (!pass) return { pass: false, detail: `browser_state failed: URL '${currentUrl}' does not match regex '${predicate.url_matches}'` };
    }
    if (predicate.title_contains && page && typeof page.title === "function") {
      const title = await page.title().catch(() => "");
      const pass = title.toLowerCase().includes(String(predicate.title_contains).toLowerCase());
      if (!pass) return { pass: false, detail: `browser_state failed: page title '${title}' does not contain '${predicate.title_contains}'` };
    }
    return { pass: true, detail: `browser_state passed: ${currentUrl}` };
  }

  // 5. DOM element predicate: predicate.type === 'dom_element' | 'dom_state'
  if (predicate.type === "dom_element" || predicate.type === "dom_state") {
    const page = context.page;
    if (!page || typeof page.locator !== "function") {
      return { pass: false, detail: "dom_element predicate failed: no active Playwright page in context" };
    }
    const loc = page.locator(predicate.selector).first();
    if (predicate.visible !== undefined) {
      const isVis = await loc.isVisible().catch(() => false);
      if (isVis !== Boolean(predicate.visible)) {
        return { pass: false, detail: `dom_element failed: selector '${predicate.selector}' visible=${isVis}, expected ${predicate.visible}` };
      }
    }
    if (predicate.text_contains) {
      const text = (await loc.innerText().catch(() => "")).toLowerCase();
      if (!text.includes(String(predicate.text_contains).toLowerCase())) {
        return { pass: false, detail: `dom_element failed: selector '${predicate.selector}' text does not contain '${predicate.text_contains}'` };
      }
    }
    if (predicate.value_equals !== undefined) {
      const val = await loc.inputValue().catch(() => null);
      if (val !== predicate.value_equals) {
        return { pass: false, detail: `dom_element failed: selector '${predicate.selector}' value '${val}' != '${predicate.value_equals}'` };
      }
    }
    return { pass: true, detail: `dom_element passed: selector '${predicate.selector}' verified` };
  }

  // 6. Form value predicate: predicate.type === 'form_value'
  if (predicate.type === "form_value") {
    const page = context.page;
    if (!page || typeof page.locator !== "function") {
      return { pass: false, detail: "form_value predicate failed: no active Playwright page in context" };
    }
    const loc = page.locator(predicate.selector).first();
    const val = await loc.inputValue().catch(() => "");
    if (predicate.value_equals !== undefined && val !== predicate.value_equals) {
      return { pass: false, detail: `form_value failed: selector '${predicate.selector}' value '${val}' != '${predicate.value_equals}'` };
    }
    if (predicate.value_contains !== undefined && !val.toLowerCase().includes(String(predicate.value_contains).toLowerCase())) {
      return { pass: false, detail: `form_value failed: selector '${predicate.selector}' value '${val}' does not contain '${predicate.value_contains}'` };
    }
    return { pass: true, detail: `form_value passed on '${predicate.selector}'` };
  }

  // 7. Visible text predicate: predicate.type === 'visible_text'
  if (predicate.type === "visible_text") {
    let text = "";
    if (context.page && typeof context.page.locator === "function") {
      text = await context.page.locator("body").innerText().catch(() => "");
    } else {
      text = finalText || "";
    }
    const normalized = normalizeAnswer(text);
    if (predicate.contains) {
      const pass = normalized.includes(normalizeAnswer(predicate.contains));
      if (!pass) return { pass: false, detail: `visible_text failed: does not contain '${predicate.contains}'` };
    }
    if (predicate.matches) {
      const pass = new RegExp(predicate.matches, "i").test(text);
      if (!pass) return { pass: false, detail: `visible_text failed: does not match '${predicate.matches}'` };
    }
    return { pass: true, detail: "visible_text passed" };
  }

  // 8. Interaction mode predicate: predicate.type === 'interaction_mode'
  if (predicate.type === "interaction_mode") {
    const executed = context.result?.telemetry?.executed_mode || context.result?.executed_mode || "";
    const fallback = context.result?.telemetry?.fallback_to || "";
    const actual = fallback || executed;
    if (predicate.expected_mode && actual !== predicate.expected_mode && executed !== predicate.expected_mode) {
      return { pass: false, detail: `interaction_mode failed: expected '${predicate.expected_mode}', actual '${actual}'` };
    }
    if (predicate.forbidden_mode && (actual === predicate.forbidden_mode || executed === predicate.forbidden_mode)) {
      return { pass: false, detail: `interaction_mode failed: forbidden '${predicate.forbidden_mode}' was executed` };
    }
    return { pass: true, detail: `interaction_mode passed: ${actual}` };
  }

  // 9. Worker trace predicate: predicate.type === 'worker_trace'
  if (predicate.type === "worker_trace") {
    const transcript = context.result?.transcript || [];
    if (predicate.action) {
      const hasAction = transcript.some((t) => t.action === predicate.action || t.name === predicate.action);
      if (!hasAction) return { pass: false, detail: `worker_trace failed: action '${predicate.action}' not in transcript` };
    }
    if (predicate.min_actions && transcript.length < predicate.min_actions) {
      return { pass: false, detail: `worker_trace failed: transcript length ${transcript.length} < ${predicate.min_actions}` };
    }
    return { pass: true, detail: "worker_trace passed" };
  }

  // 10. Capsule probe: predicate.probe
  const { probe, query, args = {}, assert: assertion } = predicate;
  if (!probe) {
    throw new Error(`unrecognized predicate definition: ${JSON.stringify(predicate)}`);
  }
  const observed = await capsule.observe(probe, { query, ...args });
  const pass = check(observed, assertion);
  return { pass, detail: pass ? "predicate passed" : `predicate failed: ${JSON.stringify(assertion)}` };
}
