import { spawn } from 'node:child_process';
import path from 'node:path';
import readline from 'node:readline';
import { startUrl, stepBudget } from '../../../harness/tasks.mjs';
import { mercuryProvider } from './provider/mercury.mjs';

export const TOOL_VERSION = 'ultrafast-windtunnel-v2';

const HERE = import.meta.dirname;
const UPSTREAM_COMMIT = '452c1ad2dd628008f1d5608f28158d76e49e6cc0';
const JEV_URL = 'https://api.typesafe.ai/v1/systemone';
const MERCURY_URL = 'https://api.inceptionlabs.ai/v1/chat/completions';
const VIEWPORT = { width: 1280, height: 800 };
const FINAL_ACTION = {
  id: '__finish__',
  description: 'The task is complete. Report an answer supported by the observations.',
  inputSchema: { type: 'object', properties: { answer: { type: 'string', minLength: 1, maxLength: 3900 } }, required: ['answer'], additionalProperties: false },
};

const finite = value => Number.isFinite(value) && value >= 0;
const reply = (child, id, result, error) => {
  if (child.stdin.writable) child.stdin.write(JSON.stringify({ id, ...(error ? { error } : { result }) }) + '\n');
};

function boundedFinalState(prompt, snapshot) {
  // Freshness guards are browser-internal and can contain unrelated form values.
  const page = snapshot?.page ?? {};
  const state = { task: prompt, observation: { url: page.url, title: page.title, text: page.text, elements: snapshot?.elements ?? [] }, history: [...(snapshot?.history ?? [])] };
  while (JSON.stringify(state).length > 48_000 && state.history.length) state.history.shift();
  if (JSON.stringify(state).length > 48_000 && typeof state.observation.text === 'string') {
    state.observation = { ...state.observation, text: state.observation.text.slice(0, 6_000) };
  }
  if (JSON.stringify(state).length > 48_000) throw new Error('final answer state exceeds 48000-character limit');
  return state;
}

export async function runUltrafast({ task, capsule, page, model: _model }, options = {}) {
  const started = performance.now();
  const attemptMs = options.attemptMs ?? 600_000;
  const requestTimeoutMs = options.requestTimeoutMs ?? 60_000;
  const maxSteps = stepBudget(task, 'structured', 20);
  const fetchCall = options.fetchCall ?? globalThis.fetch;
  const pythonPath = options.pythonPath ?? path.join(HERE, '.venv/bin/python');
  const transcript = [], provider_usage = {};
  const usage = { input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, cache_creation_tokens: 0 };
  let cost = 0, turns = 0, setupMs = 0, child, cdp, timer, exited, timedOut = false, bridgeResult;
  const controller = new AbortController();

  const decision_config = {
    version: TOOL_VERSION,
    upstream_commit: UPSTREAM_COMMIT,
    interface: 'page-controls',
    max_steps: maxSteps,
    attempt_ms: attemptMs,
    provider_request_timeout_ms: requestTimeoutMs,
    password_controls: 'generic-password-textbox-support;observed-value-is-empty-or-filled-presence;local-guards-not-model-state',
    snapshot_patch: 'snapshot-windtunnel.js;upstream-file-preserved',
    snapshot_dom_action_cap: 250,
    snapshot_overflow_policy: 'when-over-cap,round-robin-one-action-per-observed-node-per-pass;DOM-node-order;per-node-action-order;no-task-filtering',
    snapshot_occlusion_policy: 'offer-controls-only-when-center-hit-test-matches-executor;e.contains(document.elementFromPoint(x,y))',
    snapshot_audit_counts: 'observed-actions,retained-actions,omitted-actions,observed-nodes,represented-nodes,omitted-nodes,covered-controls,fallback-controls,skipped-fallback-wrappers',
    original_policy_batching: 'one TypeSafe request per decision;operation-and-operation-specific-target-heads',
    viewport: VIEWPORT,
    answer: 'mercury-written',
    selection_policy: 'policy.txt + policy-overrides.json;post-hoc-v1-informed;NEXT_ACTION-and-terminal-descriptions-only;TARGET-TEXT_VALUE-unchanged',
    fallback_controls: 'aria-selected-option/tabindex-button;nonsemantic-leaves-only;skip-wrappers-with-control-descendants;original-disabled-hidden-and-center-hit-tests',
    select_omissions: 'observed-number-of-options-omitted-per-select-in-elements.options_omitted;no-goal-filter',
    terminal_freshness: 'strict-original-global-marker-including-text/title;may-discard-terminal-decisions-during-visible-updates;navigation/answer/control-changes-reject;not-narrowed',
    failure_semantics: 'harness-run.mjs-result.failure-always-pass-false;no-outcome-probe-override',
    adaptation: 'modified-pinned-upstream-v2;policy/allocation/occlusion-audit/settling/scoped-fill/fallback-controls;post-hoc-v1-informed;no-single-change-attribution',
    fill_freshness: 'selected-fill-act-phase-only;existing-pageKey-and-target-guard-before-helper-and-input;predict-and-terminal-global-marker-unchanged;inside-form-live-text-can-still-reject',
    stale_audit: 'fresh-false/act-StalePage/post-action-document-replacement;reason/scope/terminal-context/global-changed-component-names-only;no-guard-values',
    transport: 'Python JSONL RPC to Playwright CDP;direct TypeSafe and Inception HTTP in Node',
    task_input: 'prompt-and-start-url-only;no-predicates-oracles-seed;no-date-hint',
    model_state: 'upstream page text/URL/title and recent-10 action history;observed controls plus per-select numeric options_omitted',
    decision_limit: 'outer cap counts upstream decisions;upstream two-times retry allowance disabled',
    browser_ownership: 'WindTunnel-owned page;upstream create/navigate/close replaced;observe/fresh/act/command wrapped as disclosed;upstream source and executor mutations unchanged',
    snapshot_source_policy: 'upstream Agent.snapshot before model calls and after ticks;omit browser-internal guards,page_key,marker and duplicate decision requests from audit only',
    fill_readback: 'valueAfter-or-valueAfterError in action audit only;no-policy-history-or-model-call',
    field_text_transport: 'remove reasoning,thinking,max_tokens;reasoning_effort instant;max_completion_tokens 1024;model mercury-2.5',
    final_answer_transport: 'frozen mercuryProvider;finish minLength/maxLength omitted outbound only;original local validation',
    retry_policy: 'none;parent supervisor owns infrastructure retries;mutations never retried',
    post_action_observation: 'bridge-only;includes-WAIT;unchanged-awaits-first-change;changed-needs-two-identical-fingerprints-and-productive-controls;zero-controls-await-bound;readonly-no-mutation-replay',
    post_action_observation_wait_ms: 1500,
    post_action_observation_poll_ms: 50,
    post_action_observation_deadline_limit: '1500ms-poll-budget;initial-and-final-upstream-observe-retries-and-inflight-read-may-extend;outer-attempt-cancellation-bounds-all;timers-may-consume-full-budget',
    request_accounting: 'per-step logical requests;final answer belongs to DONE step;no internal retries',
    shutdown: 'abort transport;reject new RPC;kill and await Python;detach CDP;drain pending records before timeout result',
    limits: { page_text_characters: 6000, final_state_characters: 48000, final_answer_characters: 3900 },
    standard_prices_per_million: { jev_input: 0.042, mercury_input: 0.20, mercury_cached_input: 0.02, mercury_output: 0.75 },
    mercury_promotion_multiplier: 0.2,
    cost_basis: 'standard estimated cost;promotion disclosed only',
  };

  const aggregate = (provider, requestedModel, snapshot, rawUsage, normalized, callCost, costUnknown) => {
    const item = provider_usage[provider] ??= {
      model: requestedModel,
      model_snapshot: snapshot ?? '',
      snapshot_source: 'provider-response-alias',
      calls: 0,
      cost: 0,
      usage: { input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, cache_creation_tokens: 0 },
    };
    item.calls++;
    item.model_snapshot = snapshot ?? '';
    item.raw_usage = rawUsage;
    if (costUnknown) item.cost_unknown = true;
    if (!costUnknown) {
      item.cost += callCost;
      cost += callCost;
      for (const key of Object.keys(usage)) {
        item.usage[key] += normalized[key] ?? 0;
        usage[key] += normalized[key] ?? 0;
      }
    }
  };

  const auditedFetch = async (provider, url, body, { recordRequest = true } = {}) => {
    const requestedModel = provider === 'jev' ? 'jev-latest' : 'mercury-2.5';
    const endpoint = provider === 'jev' ? JEV_URL : MERCURY_URL;
    if (url !== endpoint) throw new Error(`Unsupported provider URL: ${url}`);
    if (recordRequest) {
      transcript.push({ kind: 'request', provider, model: requestedModel, url: endpoint, request_body: body, retries: 0 });
      turns++;
    }
    const keyName = provider === 'jev' ? 'TYPESAFE_API_KEY' : 'INCEPTION_API_KEY';
    if (fetchCall === globalThis.fetch && !process.env[keyName]) throw new Error(`Missing ${keyName}`);
    const requestStarted = performance.now();
    let response, raw;
    const requestController = new AbortController();
    const requestTimer = setTimeout(() => requestController.abort(new Error('provider request timeout')), requestTimeoutMs);
    try {
      response = await fetchCall(endpoint, {
        method: 'POST', signal: AbortSignal.any([controller.signal, requestController.signal]),
        headers: { 'content-type': 'application/json', authorization: `Bearer ${process.env[keyName] ?? ''}` },
        body: JSON.stringify(body),
      });
      raw = await response.text();
    } catch (error) {
      const timeout = requestController.signal.aborted && !controller.signal.aborted;
      transcript.push({ kind: 'transport_error', provider, model: requestedModel, url: endpoint, error: error.message, error_type: timeout ? 'provider_request_timeout' : 'transport_error', timeout_ms: timeout ? requestTimeoutMs : undefined, elapsed_ms: performance.now() - requestStarted, retries: 0 });
      throw new Error(timeout ? `${provider} provider request timeout: ${requestTimeoutMs}ms` : `${provider} provider transport error: ${error.message}`);
    } finally { clearTimeout(requestTimer); }
    const elapsed_ms = performance.now() - requestStarted;
    if (!response.ok) {
      transcript.push({ kind: 'http_error', provider, model: requestedModel, url: endpoint, status: response.status, response_body: raw, elapsed_ms, retries: 0 });
      throw new Error(`${provider} provider HTTP ${response.status}: ${raw}`);
    }
    let parsed;
    try { parsed = JSON.parse(raw); }
    catch {
      transcript.push({ kind: 'response', provider, model: requestedModel, url: endpoint, status: response.status, response_body: raw, cost_unknown: true, elapsed_ms, retries: 0 });
      throw new Error(`${provider} provider returned invalid JSON`);
    }

    const rawUsage = parsed.usage ?? {};
    let known, normalized, callCost;
    if (provider === 'jev') {
      const input = rawUsage.input_tokens ?? rawUsage.inputTokens;
      const output = rawUsage.output_tokens ?? rawUsage.outputTokens;
      known = finite(input) && finite(output);
      normalized = { input_tokens: known ? input : 0, output_tokens: known ? output : 0, cached_input_tokens: 0, cache_creation_tokens: 0 };
      callCost = known ? input * 0.042 / 1e6 : 0;
    } else {
      const prompt = rawUsage.prompt_tokens;
      const output = rawUsage.completion_tokens;
      const cached = rawUsage.prompt_tokens_details?.cached_tokens ?? rawUsage.cached_input_tokens ?? 0;
      known = finite(prompt) && finite(output) && finite(cached) && cached <= prompt;
      normalized = { input_tokens: known ? prompt - cached : 0, output_tokens: known ? output : 0, cached_input_tokens: known ? cached : 0, cache_creation_tokens: 0 };
      callCost = known ? (normalized.input_tokens * 0.20 + cached * 0.02 + output * 0.75) / 1e6 : 0;
    }
    aggregate(provider, requestedModel, parsed.model, rawUsage, normalized, callCost, !known);
    transcript.push({
      kind: 'response', provider, model: requestedModel, model_snapshot: parsed.model ?? '',
      snapshot_source: 'provider-response-alias', url: endpoint, status: response.status,
      response_body: raw, response: parsed, usage: known ? normalized : rawUsage,
      cost: known ? callCost : null, cost_estimated: true, cost_unknown: !known,
      ...(provider === 'mercury' && known ? { promotional_est_cost_usd: callCost * 0.2 } : {}),
      elapsed_ms, retries: 0,
    });
    if (!known) throw new Error(`${provider} provider missing or invalid usage; cost unknown`);
    const expected = provider === 'jev' ? 'jev-1.13.0' : 'mercury-2.5';
    if (parsed.model !== expected) throw new Error(`${provider} provider model mismatch: ${parsed.model ?? 'missing'} (expected ${expected})`);
    return { parsed, raw, status: response.status, headers: response.headers };
  };

  let requestsAudited = false;
  const recordStepRequests = () => {
    if (requestsAudited) return;
    requestsAudited = true;
    let step = 0, requests = { jev: 0, mercury: 0 };
    const flush = () => { if (step) transcript.push({ kind: 'step_requests', step, requests, total: requests.jev + requests.mercury, retries: 0 }); };
    for (const event of [...transcript]) {
      if (event.kind === 'snapshot' && event.phase === 'before_model') { flush(); step++; requests = { jev: 0, mercury: 0 }; }
      if (event.kind === 'request') requests[event.provider]++;
    }
    flush();
  };
  const baseResult = (overrides = {}) => {
    recordStepRequests();
    return ({
    finalText: '', failure: '', usage, cost, cost_estimated: true, transcript, turns,
    actions: bridgeResult?.actions ?? transcript.filter(e => e.kind === 'action').length, steps: bridgeResult?.steps ?? transcript.filter(e => e.kind === 'request' && e.provider === 'jev').length, setupMs,
    provider_usage,
    model_snapshot: Object.values(provider_usage).map(item => item.model_snapshot).filter(Boolean).join('+'),
    snapshot_source: 'per-provider:provider_usage', decision_config,
    stop_reason: '', budget_exhausted: false, retries: 0, retry_wait_ms: 0,
    ...overrides,
    });
  };

  try {
    await page.setViewportSize(VIEWPORT);
    await page.goto(startUrl(task, capsule), { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => {});
    cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    setupMs = performance.now() - started;

    const timeout = new Promise(resolve => {
      timer = setTimeout(() => {
        timedOut = true;
        controller.abort(new Error('attempt timeout: ultrafast agent budget'));
        child?.kill('SIGKILL');
        resolve(null);
      }, attemptMs);
    });

    child = spawn(pythonPath, [path.join(HERE, 'bridge.py')], {
      cwd: HERE,
      env: {
        PATH: process.env.PATH ?? '',
        PYTHONPATH: path.join(HERE, 'upstream'),
        TYPESAFE_API_KEY: 'node-owned',
        TYPESAFE_MODEL: 'jev-latest',
        TEXT_MODEL_API_KEY: 'node-owned',
        TEXT_MODEL_BASE_URL: 'https://api.inceptionlabs.ai/v1',
        TEXT_MODEL: 'mercury-2.5',
        TEXT_MODEL_REASONING: 'none',
        PYTHONDONTWRITEBYTECODE: '1',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    child.stdin.on('error', () => {}); // Child can exit while an aborted RPC unwinds.
    let stderr = '';
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4_000); });
    exited = new Promise(resolve => {
      child.once('exit', (code, signal) => resolve({ code, signal }));
      child.once('error', error => { stderr = error.message; resolve({ code: 'spawn-error' }); });
    });
    child.stdin.write(JSON.stringify({ type: 'init', taskPrompt: task.prompt, url: startUrl(task, capsule), stepBudget: maxSteps }) + '\n');

    const processLines = (async () => {
      const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
      for await (const line of lines) {
        let message;
        try { message = JSON.parse(line); }
        catch { throw new Error('Python bridge emitted non-JSON stdout'); }
        if (message.type === 'event') transcript.push(message.event);
        else if (message.type === 'rpc') {
          if (controller.signal.aborted) { reply(child, message.id, undefined, 'attempt cancelled'); continue; }
          try { reply(child, message.id, await cdp.send(message.method, message.params ?? {})); }
          catch (error) { reply(child, message.id, undefined, error.message); }
        } else if (message.type === 'provider') {
          if (controller.signal.aborted) { reply(child, message.id, undefined, 'attempt cancelled'); continue; }
          try {
            const provider = message.url === JEV_URL ? 'jev' : message.url.endsWith('/chat/completions') ? 'mercury' : 'unknown';
            let body = structuredClone(message.body);
            if (provider === 'mercury') {
              delete body.reasoning;
              delete body.thinking;
              delete body.max_tokens;
              Object.assign(body, { model: 'mercury-2.5', reasoning_effort: 'instant', max_completion_tokens: 1024 });
            }
            const result = await auditedFetch(provider, message.url, body);
            reply(child, message.id, result.parsed);
          } catch (error) { reply(child, message.id, undefined, error.message); }
        } else if (message.type === 'result') bridgeResult = message;
        else throw new Error(`Unknown Python bridge message: ${message.type}`);
      }
      const exit = await exited;
      if (!bridgeResult) throw new Error(`Python bridge exited without a result (${exit.code ?? exit.signal})${stderr ? `: ${stderr}` : ''}`);
      return bridgeResult;
    })();
    await Promise.race([processLines, timeout]);
    if (timedOut) {
      // Detach interrupts outstanding CDP work; drain late provider accounting before freezing the row.
      await cdp.detach().catch(() => {});
      await processLines.catch(() => {});
      return baseResult({ failure: 'attempt timeout: ultrafast agent budget', stop_reason: 'timeout' });
    }
    await processLines;

    if (bridgeResult.status === 'done') {
      try {
        const finalState = boundedFinalState(task.prompt, bridgeResult.snapshot);
        const fill = mercuryProvider({
          signal: controller.signal,
          record: entry => {
            if (entry.kind === 'request') {
              transcript.push({ ...entry, provider: 'mercury', url: MERCURY_URL, retries: 0 });
              turns++;
            }
          },
          fetchCall: async (url, init) => {
            const result = await auditedFetch('mercury', url, JSON.parse(init.body), { recordRequest: false });
            return new Response(result.raw, { status: result.status, headers: result.headers });
          },
        });
        const answer = await fill(finalState, FINAL_ACTION);
        if (timedOut) return baseResult({ failure: 'attempt timeout: ultrafast agent budget', stop_reason: 'timeout' });
        return baseResult({ finalText: `Final answer: ${answer.answer}`, stop_reason: 'done' });
      } catch (error) {
        return baseResult({ failure: timedOut ? 'attempt timeout: ultrafast agent budget' : error.message, stop_reason: timedOut ? 'timeout' : 'error' });
      }
    }
    if (bridgeResult.status === 'blocked') return baseResult({ failure: 'agent blocked', stop_reason: 'blocked' });
    if (bridgeResult.status === 'budget_exhausted') return baseResult({ failure: 'agent budget exhausted', stop_reason: 'budget_exhausted', budget_exhausted: true });
    return baseResult({ failure: bridgeResult.failure || 'Python bridge failed', stop_reason: 'error' });
  } catch (error) {
    return baseResult({ failure: timedOut ? 'attempt timeout: ultrafast agent budget' : error.message, stop_reason: timedOut ? 'timeout' : 'error' });
  } finally {
    clearTimeout(timer);
    controller.abort();
    if (child && child.exitCode === null) child.kill('SIGKILL');
    await cdp?.detach().catch(() => {});
    await exited;
  }
}
