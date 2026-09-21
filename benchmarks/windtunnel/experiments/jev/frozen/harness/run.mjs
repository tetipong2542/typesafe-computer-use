import { bootCapsule } from "../../../../harness/capsule.mjs";
import { classifyFailure, newRunId, verdictFor } from "./lib.mjs";
import { score } from "../../../../scoring/predicates.mjs";

export const majority = (passes) => passes.filter(Boolean).length > passes.length / 2;

export async function runBatch({
  siteId,
  method,
  tasks,
  n = 3,
  seed = 1,
  port,
  model = method.model ?? "none",
  perturbed = false,
  boot = bootCapsule,
  bootOptions = {},
  page,
  browser,
  onResult,
  onAttemptStart,
  captureEvidence = false,
}) {
  const capsule = await boot(siteId, { seed, port, ...bootOptions });
  const rows = [];
  try {
    runs: for (const task of tasks) {
      for (let repeat = 0; repeat < n; repeat++) {
        await onAttemptStart?.({ task, repeat, capsule: capsule.meta });
        let resetMs = 0;
        const evidence = [];
        let closeError;
        const capture = async (phase) => {
          if (!captureEvidence || !task.predicate.probe) return;
          const { probe, query, args = {} } = task.predicate;
          const entry = { phase, probe, args: { ...(query !== undefined ? { query } : {}), ...args }, timestamp: new Date().toISOString() };
          const started = performance.now();
          try { entry.observed = await capsule.observe(probe, entry.args); }
          catch (error) { entry.error = error.message; }
          entry.elapsed_ms = performance.now() - started;
          evidence.push(entry);
          return entry;
        };
        // An arm blowing up (API overload, browser crash, missing tools) is a
        // failed attempt, not a failed run — record it and keep going, so paid
        // rows already collected still make it into the report.
        let result = {}, verdict, context;
        try {
          const resetStarted = performance.now();
          await capsule.reset();
          resetMs = performance.now() - resetStarted;
          const before = await capture('before');
          if (before?.error) throw new Error(`probe-error: baseline: ${before.error}`);
          if (browser) {
            context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
            page = await context.newPage();
          }
          const started = performance.now();
          result = await method.run({ task, capsule, page, model,
            ...(method.jevTransport !== undefined ? { jevTransport: method.jevTransport } : {}),
            ...(method.minConfidence !== undefined ? { minConfidence: method.minConfidence } : {}) });
          result.attemptMs = performance.now() - started;
          const after = await capture('after');
          verdict = result.failure
            ? { pass: false, detail: `harness-${classifyFailure(result.failure)}: ${result.failure}` }
            : await score(task.predicate, after ? { observe: async () => {
              if (after.error) throw new Error(after.error);
              return after.observed;
            } } : capsule, result.finalText);
        } catch (error) {
          const msg = error.message;
          verdict = { pass: false, detail: `harness-${classifyFailure(msg)}: ${msg}` };
        } finally {
          try { await context?.close(); } catch (error) { closeError = error.message; }
        }
        const attemptMs = result.attemptMs ?? 0;
        const setupMs = result.setupMs ?? 0;
        const agentMs = Math.max(0, attemptMs - setupMs);
        const evaluationMs = evidence.reduce((sum, e) => sum + e.elapsed_ms, 0);
        const ms = Math.round(resetMs + attemptMs + evaluationMs);
        const usage = result.usage ?? {};
        const row = {
          run_id: newRunId(task.id, method.id),
          task_id: task.id,
          arm: method.id,
          method: method.id,
          model,
          model_snapshot: result.model_snapshot ?? "",
          tool_version: method.version ?? "local",
          site: siteId,
          start_url: capsule.baseUrl,
          success: verdict.pass,
          pass: verdict.pass,
          failure_category: verdict.pass ? "" : verdict.detail.startsWith("probe-error:") ? `harness-${classifyFailure(verdict.detail)}: ${verdict.detail}` : verdict.detail,
          wall_clock_s: (ms / 1000).toFixed(3),
          reset_s: (resetMs / 1000).toFixed(3),
          setup_s: (setupMs / 1000).toFixed(3),
          agent_s: (agentMs / 1000).toFixed(3),
          ms,
          model_turns: result.turns ?? 0,
          actions_or_tool_calls: result.actions ?? result.transcript?.length ?? 0,
          ...(result.decision_config ? { decision_config: result.decision_config, provider_usage: result.provider_usage, decision_steps: result.steps } : {}),
          input_tokens: usage.input_tokens ?? 0,
          output_tokens: usage.output_tokens ?? 0,
          cached_tokens: usage.cached_input_tokens ?? 0,
          cache_write_tokens: usage.cache_creation_tokens ?? 0,
          // Compatibility signals the pre-flight smoke gate checks: a run that
          // truncates or is refused is not a measurement of task ability.
          snapshot_source: result.snapshot_source ?? (result.model_snapshot ? "provider-response" : ""),
          stop_reason: result.stop_reason ?? "",
          refusal: result.refusal ?? false,
          truncated: result.truncated ?? false,
          effort: result.effort ?? "",
          caching: result.caching ?? "unsupported",
          tokens: (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0),
          est_cost_usd: result.cost ?? 0,
          cost_estimated: result.cost_estimated ?? "",
          retries: result.retries ?? 0,
          retry_wait_ms: result.retry_wait_ms ?? 0,
          budget_exhausted: result.budget_exhausted ?? false,
          temperature: result.temperature ?? "default",
          perturbation_id: perturbed ? "enabled" : "none",
          spec_shape: "none",
          timestamp: new Date().toISOString(),
          transcript: result.transcript ?? [],
          final_text: String(result.finalText ?? "").slice(0, 4000),
          ...(captureEvidence ? { evaluator_observations: evidence, evaluation_s: (evaluationMs / 1000).toFixed(3) } : {}),
          ...(closeError ? { context_close_error: closeError } : {}),
        };
        rows.push(row);
        if (await onResult?.(row) === false) break runs;
      }
    }
  } finally {
    // A failed teardown is recorded, not thrown: throwing here made cli.mjs
    // drop every finished row of the batch (hi-events, 2026-09-05).
    var teardown_error = await capsule.down().then(() => "", (error) => { console.warn(`teardown failed for ${siteId}: ${error.message}`); return error.message; });
  }
  const verdicts = tasks.map((task) => {
    const attempts = rows.filter((row) => row.task_id === task.id);
    return { site: siteId, taskId: task.id, tier: task.tier, method: method.id, ...verdictFor(attempts) };
  });
  return { rows, verdicts, teardown_error, capsule: capsule.meta };
}
