import assert from 'node:assert/strict';
import http from 'node:http';
import { createRequire } from 'node:module';
import { once } from 'node:events';
import { runUltrafast } from './arm.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const pageHtml = `<!doctype html><html><body>
  <label>Name <input aria-label="Name" id="name"></label>
  <button id="submit">Submit</button><p id="status">Ready</p>
  <input type="password" aria-label="Existing password" value="UNRELATED_STORED_PASSWORD">
  <script>window.mutations=0;document.querySelector('#submit').onclick=()=>{window.mutations++;document.querySelector('#status').textContent='Saved '+document.querySelector('#name').value}</script>
</body></html>`;

const answer = (criteria, choice) => ({ choice, confidence: 1, probabilities: Object.fromEntries(Object.keys(criteria).map(key => [key, Number(key === choice)])) });
const jevResponse = (body, operation, target) => {
  const answers = { operation: answer(body.questions.operation.criteria, operation) };
  if (target) answers[operation.toLowerCase() + '_target'] = answer(body.questions[operation.toLowerCase() + '_target'].criteria, target);
  return { model: 'jev-1.13.0', answers, usage: { input_tokens: 100, output_tokens: 10 } };
};
const mercuryResponse = content => ({ model: 'mercury-2.5', choices: [{ finish_reason: 'stop', message: { content } }], usage: { prompt_tokens: 100, completion_tokens: 10, prompt_tokens_details: { cached_tokens: 20 } } });
const finalResponse = answer => ({ model: 'mercury-2.5', choices: [{ finish_reason: 'tool_calls', message: { tool_calls: [{ function: { name: 'submit_arguments', arguments: JSON.stringify({ answer }) } }] } }], usage: { prompt_tokens: 100, completion_tokens: 10, prompt_tokens_details: { cached_tokens: 20 } } });

async function withPage(browser, baseUrl, work) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  try { return await work(await context.newPage(), { baseUrl }); }
  finally { await context.close(); }
}

const task = { prompt: 'Enter Ada in the Name field, submit it, and report the visible result.', max_steps: { structured: 8 }, predicate: { hidden: 'NEVER_SEND_PREDICATE' }, oracle: 'NEVER_SEND_ORACLE', seed: 'NEVER_SEND_SEED' };

const server = http.createServer((request, response) => {
  response.writeHead(200, { 'content-type': 'text/html' });
  response.end(request.url === '/navigate' ? '<title>Start</title><a href="/destination">Open destination</a>' : request.url === '/destination' ? '<title>Destination</title><p>Navigation complete</p>' : pageHtml);
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const baseUrl = `http://127.0.0.1:${server.address().port}/`;
const browser = await chromium.launch({ headless: true });

try {
  await withPage(browser, baseUrl, async (page, capsule) => {
    const requests = [];
    const fetchCall = async (url, init) => {
      const body = JSON.parse(init.body);
      requests.push({ url, body });
      if (url.includes('typesafe')) {
        const count = requests.filter(item => item.url.includes('typesafe')).length;
        return Response.json(count === 1 ? jevResponse(body, 'TYPE_TEXT', '1') : count === 2 ? jevResponse(body, 'CLICK', '2') : jevResponse(body, 'DONE'));
      }
      return Response.json(requests.filter(item => item.url.includes('inceptionlabs')).length === 1 ? mercuryResponse('{"text":"Ada"}') : finalResponse('Saved Ada'));
    };
    const result = await runUltrafast({ task, capsule, page, model: 'unused' }, { fetchCall, attemptMs: 20_000 });
    assert.equal(result.failure, '');
    assert.equal(result.finalText, 'Final answer: Saved Ada');
    assert.equal(requests.length, 5);
    assert.deepEqual(Object.keys(requests[0].body.questions), ['operation', 'type_text_target', 'click_target']);
    assert.equal(requests[0].body.state.page.title, '');
    assert.equal(requests[1].body.reasoning_effort, 'instant');
    assert.equal(requests[1].body.max_completion_tokens, 1024);
    assert(!('max_tokens' in requests[1].body) && !('reasoning' in requests[1].body) && !('thinking' in requests[1].body));
    assert.equal(requests[4].body.max_completion_tokens, 4096);
    assert.equal(requests[4].body.tools[0].function.parameters.properties.answer.minLength, undefined);
    assert.equal(requests[4].body.tools[0].function.parameters.properties.answer.maxLength, undefined);
    const serialized = JSON.stringify(requests);
    for (const hidden of ['NEVER_SEND_PREDICATE', 'NEVER_SEND_ORACLE', 'NEVER_SEND_SEED', 'UNRELATED_STORED_PASSWORD']) assert(!serialized.includes(hidden));
    const decisions = result.transcript.filter(event => event.kind === 'decision').map(event => event.decision);
    assert.deepEqual(decisions.map(event => event.operation), ['TYPE_TEXT', 'CLICK', 'DONE']);
    assert.deepEqual(decisions.map(event => event.choice), ['e1', 'e3', 'DONE']);
    assert.deepEqual(requests[0].body.state.elements.map(element => [element.label, element.operations]), [
      ['Name', ['TYPE_TEXT', 'CLICK']], ['Submit', ['CLICK']], ['Existing password', ['TYPE_TEXT', 'CLICK']],
    ]);
    assert(result.transcript.some(event => event.kind === 'snapshot' && event.phase === 'initial_observation'));
    assert(result.transcript.filter(event => event.kind === 'snapshot' && event.phase === 'before_model').every(event => event.snapshot.page.actions.length >= 1));
    for (const event of result.transcript.filter(event => event.snapshot)) {
      for (const key of ['guards', 'page_key', 'marker']) assert(!(key in event.snapshot.page));
      assert(event.snapshot.decisions.every(decision => !('request' in decision)));
    }
    assert(!JSON.stringify(result.transcript).includes('UNRELATED_STORED_PASSWORD'));
    assert.equal(result.transcript.find(event => event.kind === 'action' && event.action.kind === 'fill').result.valueAfter, 'Ada');
    assert.equal(await page.evaluate(() => window.mutations), 1);
    assert.equal(await page.inputValue('#name'), 'Ada');
    assert.equal(await page.textContent('#status'), 'Saved Ada');
    assert.equal(result.provider_usage.jev.calls, 3);
    assert.equal(result.provider_usage.mercury.calls, 2);
    assert.equal(result.turns, 5);
    assert.deepEqual(result.transcript.filter(e => e.kind === 'step_requests').map(e => e.total), [2, 1, 2]);
    assert(Math.abs(result.cost - 0.0000604) < 1e-12);
    assert.deepEqual(result.usage, { input_tokens: 460, output_tokens: 50, cached_input_tokens: 40, cache_creation_tokens: 0 });
    assert.equal(result.decision_config.max_steps, 8);
    assert.equal(result.decision_config.upstream_commit, '452c1ad2dd628008f1d5608f28158d76e49e6cc0');
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    const raw = '{"error":"temporarily unavailable"}\n';
    const result = await runUltrafast({ task, capsule, page }, { fetchCall: async () => new Response(raw, { status: 503 }), attemptMs: 10_000 });
    assert.match(result.failure, /HTTP 503/);
    assert.equal(result.retries, 0);
    assert.equal(result.transcript.find(event => event.kind === 'http_error').response_body, raw);
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    let aborted = false, settled = false;
    const result = await runUltrafast({ task, capsule, page }, { attemptMs: 1000, fetchCall: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => { aborted = true; setTimeout(() => { settled = true; reject(new Error('aborted')); }, 100); }, { once: true });
    }) });
    assert.equal(result.stop_reason, 'timeout');
    assert.match(result.failure, /attempt timeout/);
    assert(aborted);
    assert(settled, 'provider transport settles before the result is returned');
    const saved = JSON.stringify(result);
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(JSON.stringify(result), saved, 'late transport cannot mutate the returned checkpoint');
    assert.equal(await page.evaluate(() => window.mutations), 0);
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    let entered = false, settled = false;
    const context = page.context(), createSession = context.newCDPSession.bind(context);
    context.newCDPSession = async target => {
      const session = await createSession(target), send = session.send.bind(session);
      session.send = async (method, params) => {
        if (method === 'Input.dispatchMouseEvent' && params.type === 'mousePressed') {
          entered = true; await new Promise(resolve => setTimeout(resolve, 800)); settled = true;
        }
        return send(method, params);
      };
      return session;
    };
    const result = await runUltrafast({ task, capsule, page }, { attemptMs: 600, fetchCall: async (_url, init) => Response.json(jevResponse(JSON.parse(init.body), 'CLICK', '2')) });
    assert.equal(result.stop_reason, 'timeout');
    assert(entered && settled, 'in-flight CDP work is drained before returning');
    assert.equal(await page.evaluate(() => window.mutations), 0);
    const saved = JSON.stringify(result);
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(await page.evaluate(() => window.mutations), 0);
    assert.equal(JSON.stringify(result), saved);
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    const result = await runUltrafast({ task, capsule, page }, { fetchCall: async (_url, init) => {
      const body = JSON.parse(init.body);
      return Response.json({ ...jevResponse(body, 'DONE'), model: 'jev-latest' });
    }, attemptMs: 10_000 });
    assert.match(result.failure, /model mismatch/);
    assert.equal(result.provider_usage.jev.calls, 1);
    assert(result.provider_usage.jev.cost > 0);
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    let call = 0;
    const result = await runUltrafast({ task, capsule, page }, { fetchCall: async (url, init) => {
      const body = JSON.parse(init.body);
      if (url.includes('typesafe')) return Response.json(jevResponse(body, 'TYPE_TEXT', '1'));
      call++;
      return Response.json({ ...mercuryResponse('{"text":"Ada"}'), usage: { prompt_tokens: 'unknown', completion_tokens: 1 } });
    }, attemptMs: 10_000 });
    assert.equal(call, 1);
    assert.match(result.failure, /invalid usage/);
    assert.equal(result.provider_usage.mercury.calls, 1);
    assert.equal(result.provider_usage.mercury.cost_unknown, true);
    assert(result.transcript.some(event => event.provider === 'mercury' && event.cost_unknown));
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    let aborted = false;
    const result = await runUltrafast({ task, capsule, page }, { attemptMs: 5000, requestTimeoutMs: 25, fetchCall: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => { aborted = true; reject(new Error('aborted')); }, { once: true });
    }) });
    assert.match(result.failure, /provider request timeout: 25ms/);
    assert(aborted);
    assert(result.transcript.some(event => event.error_type === 'provider_request_timeout'));
    assert.equal(result.turns, 1);
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    let jevCalls = 0;
    const requests = [];
    const result = await runUltrafast({ task: { prompt: 'Replace Existing password with user-authorized-value.', max_steps: 3 }, capsule, page }, { fetchCall: async (url, init) => {
      const body = JSON.parse(init.body); requests.push(body);
      if (url.includes('typesafe')) return Response.json(++jevCalls === 1 ? jevResponse(body, 'TYPE_TEXT', '3') : jevResponse(body, 'DONE'));
      return Response.json(body.tools ? finalResponse('Updated the field.') : mercuryResponse('{"text":"user-authorized-value"}'));
    } });
    assert.equal(result.failure, '');
    assert.equal(await page.inputValue('input[type=password]'), 'user-authorized-value');
    assert.equal(result.transcript.find(e => e.kind === 'action').result.valueAfter, '[filled password]');
    assert(!JSON.stringify(requests).includes('UNRELATED_STORED_PASSWORD'));
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    const result = await runUltrafast({ task, capsule, page }, { pythonPath: '/does-not-exist/python', attemptMs: 2000, fetchCall: () => { throw new Error('must not spend'); } });
    assert.match(result.failure, /spawn-error|ENOENT/);
    assert.equal(result.turns, 0);
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    const result = await runUltrafast({ task, capsule, page }, { attemptMs: 500, fetchCall: (_url, { signal, body }) => new Promise(resolve => {
      signal.addEventListener('abort', () => setTimeout(() => resolve(Response.json(jevResponse(JSON.parse(body), 'DONE'))), 100), { once: true });
    }) });
    assert.equal(result.stop_reason, 'timeout');
    assert.equal(result.provider_usage.jev.calls, 1);
    assert(Math.abs(result.cost - 0.0000042) < 1e-12);
    assert.equal(result.actions, 0);
    const saved = JSON.stringify(result);
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(JSON.stringify(result), saved, 'late paid response is accounted before return');
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    const result = await runUltrafast({ task: { prompt: 'Open the destination page and report its visible result.', start_path: '/navigate', max_steps: 6 }, capsule, page }, { fetchCall: async (url, init) => {
      const body = JSON.parse(init.body);
      if (url.includes('typesafe')) return Response.json(body.state.page.url.endsWith('/destination') ? jevResponse(body, 'DONE') : jevResponse(body, 'CLICK', '1'));
      return Response.json(finalResponse('Navigation complete'));
    } });
    assert.equal(result.failure, '');
    assert.equal(page.url(), baseUrl + 'destination');
    assert.equal(await page.title(), 'Destination');
    assert.equal(result.actions, 1);
    assert.equal(result.finalText, 'Final answer: Navigation complete');
    assert(result.transcript.some(event => event.snapshot?.page.url.endsWith('/destination')));
  });

  await withPage(browser, baseUrl, async (page, capsule) => {
    const result = await runUltrafast({ task, capsule, page }, { attemptMs: 500, fetchCall: (url, { signal, body }) => {
      if (url.includes('typesafe')) return Promise.resolve(Response.json(jevResponse(JSON.parse(body), 'DONE')));
      return new Promise(resolve => signal.addEventListener('abort', () => setTimeout(() => resolve(Response.json(finalResponse('Too late'))), 100), { once: true }));
    } });
    assert.equal(result.stop_reason, 'timeout');
    assert.match(result.failure, /attempt timeout/);
    assert.equal(result.finalText, '');
    assert.equal(result.provider_usage.jev.calls, 1);
    assert.equal(result.provider_usage.mercury.calls, 1);
    assert(result.provider_usage.mercury.cost > 0, 'late final answer is billed but cannot turn timeout into success');
  });

  console.log('PASS: upstream policy bridge, real Chromium, accounting, timeout and fail-closed provider checks');
} finally {
  await browser.close();
  server.close();
}
