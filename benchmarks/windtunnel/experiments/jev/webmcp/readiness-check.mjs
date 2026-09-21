import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';

import { installModelContextBridge } from '../../../arms/wm-claude.mjs';
import { runDecisionAgent } from '../frozen/arms/decision-agent.mjs';
import { createReadinessAdapter, waitForTools } from './readiness.mjs';

const { chromium } = createRequire(import.meta.url)('playwright');

let passed = 0;
{
  const audit = [];
  const tools = await waitForTools({ evaluate: async () => [{ name: 'preflight' }] }, {
    signal: new AbortController().signal,
    audit: event => audit.push(event),
    step: 0,
  });
  assert.deepEqual(tools.map(tool => tool.name), ['preflight']);
  assert.equal(audit[0].outcome, 'already-ready');
  passed++;
}

const server = createServer((_request, response) => {
  response.setHeader('content-type', 'text/html');
  response.end(`<!doctype html><title>Readiness fixture</title><script>
    if (location.pathname === '/integration') {
      const first = new AbortController();
      navigator.modelContext.registerTool({
        name: 'sign_in',
        description: 'Sign in and open the dashboard',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        execute: async () => {
          first.abort();
          history.pushState({}, '', '/dashboard');
          setTimeout(() => navigator.modelContext.registerTool({
            name: 'summary',
            description: 'Read the dashboard summary',
            inputSchema: { type: 'object', properties: {}, additionalProperties: false },
            execute: async () => ({ status: 'ready' }),
          }).catch(() => {}), 200);
          return { signed_in: true };
        },
      }, { signal: first.signal }).catch(() => {});
    }
  </script>`);
});

server.listen(0, '127.0.0.1');
await once(server, 'listening');
const baseUrl = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });

async function pageWithBridge(path = '/') {
  const context = await browser.newContext();
  await context.addInitScript(installModelContextBridge);
  const page = await context.newPage();
  await page.goto(`${baseUrl}${path}`);
  return { context, page };
}

function assertAudit(event, expected) {
  assert.deepEqual(Object.keys(event).sort(), [
    'final_count', 'initial_count', 'kind', 'outcome', 'polls', 'step', 'wait_ms', 'waited',
  ]);
  assert.equal(event.kind, 'tool_readiness');
  for (const [key, value] of Object.entries(expected)) assert.deepEqual(event[key], value);
  assert(Number.isFinite(event.wait_ms) && event.wait_ms >= 0);
}

try {
  {
    const { context, page } = await pageWithBridge();
    await page.evaluate(() => { navigator.modelContext.registerTool({
      name: 'ready_now', description: 'Ready now', inputSchema: { type: 'object', properties: {} }, execute: async () => ({}),
    }).catch(() => {}); });
    const audit = [];
    const started = performance.now();
    const tools = await waitForTools(page, { signal: new AbortController().signal, audit: event => audit.push(event), step: 1 });
    assert.deepEqual(tools.map(tool => tool.name), ['ready_now']);
    assert(performance.now() - started < 100);
    assert.equal(audit.length, 1);
    assertAudit(audit[0], { step: 1, initial_count: 1, final_count: 1, waited: false, polls: 0, outcome: 'already-ready' });
    assert.equal(audit[0].wait_ms, 0);
    passed++;
    await context.close();
  }

  for (const delayMs of [200, 650]) {
    const { context, page } = await pageWithBridge();
    await page.evaluate(delay => setTimeout(() => navigator.modelContext.registerTool({
      name: `ready_after_${delay}`, description: 'Delayed tool', inputSchema: { type: 'object', properties: {} }, execute: async () => ({}),
    }).catch(() => {}), delay), delayMs);
    const audit = [];
    const tools = await waitForTools(page, { signal: new AbortController().signal, audit: event => audit.push(event), step: delayMs });
    assert.deepEqual(tools.map(tool => tool.name), [`ready_after_${delayMs}`]);
    assert.equal(audit.length, 1);
    assertAudit(audit[0], { step: delayMs, initial_count: 0, final_count: 1, waited: true, outcome: 'ready-after-wait' });
    assert(audit[0].polls > 0);
    assert(audit[0].wait_ms >= delayMs - 40 && audit[0].wait_ms < 1000);
    passed++;
    await context.close();
  }

  {
    const { context, page } = await pageWithBridge();
    await page.evaluate(() => { navigator.modelContext.registerTool({name:'already_registered',inputSchema:{type:'object',properties:{}},execute:async()=>({})}).catch(()=>{}); });
    const audit=[];
    const slowInitialPage={evaluate:(...args)=>page.evaluate(...args).then(value=>new Promise(resolve=>setTimeout(()=>resolve(value),200)))};
    const started=performance.now();
    const tools=await waitForTools(slowInitialPage,{signal:new AbortController().signal,audit:e=>audit.push(e),timeoutMs:100,step:10});
    assert.deepEqual(tools.map(t=>t.name),['already_registered']);
    assert(performance.now()-started>=190);
    assert.equal(audit.length,1);
    assertAudit(audit[0],{initial_count:1,final_count:1,waited:false,wait_ms:0,polls:0,outcome:'already-ready'});
    passed++;
    await context.close();
  }

  {
    const { context, page } = await pageWithBridge();
    const audit = [];
    const started = performance.now();
    const tools = await waitForTools(page, { signal: new AbortController().signal, audit: event => audit.push(event), timeoutMs: 80, pollMs: 25, step: 4 });
    const elapsed = performance.now() - started;
    assert.deepEqual(tools, []);
    assert(elapsed >= 70 && elapsed < 300);
    assert.equal(audit.length, 1);
    assertAudit(audit[0], { step: 4, initial_count: 0, final_count: 0, waited: true, outcome: 'empty-timeout' });
    assert(audit[0].polls > 0);
    passed++;
    await context.close();
  }

  {
    const { context, page } = await pageWithBridge();
    const controller = new AbortController();
    const audit = [];
    let reads = 0;
    const proxyPage = { evaluate: (...args) => { reads++; return page.evaluate(...args); } };
    const timer = setTimeout(() => controller.abort(new Error('actual attempt deadline')), 50);
    const started = performance.now();
    await assert.rejects(waitForTools(proxyPage, { signal: controller.signal, audit: event => audit.push(event), timeoutMs: 1000, pollMs: 25, step: 5 }), /actual attempt deadline/);
    clearTimeout(timer);
    assert(performance.now() - started < 250);
    assert.equal(audit.length, 1);
    assertAudit(audit[0], { step: 5, initial_count: 0, final_count: 0, waited: true, outcome: 'aborted' });
    const settled = { reads, audit: structuredClone(audit) };
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.equal(reads, settled.reads);
    assert.deepEqual(audit, settled.audit);
    passed++;
    await context.close();
  }

  {
    const controller = new AbortController();
    controller.abort(new Error('already expired'));
    const audit = [];
    let reads = 0;
    const page = { evaluate: () => { reads++; return Promise.resolve([]); } };
    await assert.rejects(waitForTools(page, { signal: controller.signal, audit: event => audit.push(event), step: 6 }), /already expired/);
    assert.equal(reads, 0);
    assert.equal(audit.length, 1);
    assertAudit(audit[0], { step: 6, initial_count: 0, final_count: 0, waited: false, polls: 0, outcome: 'aborted' });
    passed++;
  }

  {
    const { context, page } = await pageWithBridge();
    const audit = [];
    let reads = 0;
    const proxyPage = {
      evaluate: (...args) => {
        reads++;
        return reads === 1 ? page.evaluate(...args) : page.evaluate(...args).then(value => new Promise(resolve => setTimeout(() => resolve(value), 200)));
      },
    };
    const started = performance.now();
    const tools = await waitForTools(proxyPage, { signal: new AbortController().signal, audit: event => audit.push(event), timeoutMs: 100, pollMs: 25, step: 7 });
    assert.deepEqual(tools, []);
    assert(performance.now() - started < 180);
    assert.equal(reads, 2);
    assert.equal(audit.length, 1);
    assertAudit(audit[0], { step: 7, initial_count: 0, final_count: 0, waited: true, polls: 1, outcome: 'empty-timeout' });
    const settled = structuredClone(audit);
    await new Promise(resolve => setTimeout(resolve, 160));
    assert.deepEqual(audit, settled);
    assert.equal(reads, 2);
    passed++;
    await context.close();
  }

  {
    const context = await browser.newContext();
    const page = await context.newPage();
    const task = { prompt: 'Sign in, read the summary, and finish.', start_path: '/integration', max_steps: 4 };
    const capsule = { baseUrl };
    const choices = ['sign_in', 'summary', '__finish__'];
    const states = [];
    let jevCalls = 0;
    let lunaCalls = 0;
    const providers = {
      chooseJev: async (state, questions) => {
        states.push(structuredClone(state));
        const value = choices[jevCalls++];
        assert(questions.decision.options.some(option => option.value === value));
        return { decision: { value, confidence: 1 } };
      },
      fillLuna: async (_state, action) => {
        lunaCalls++;
        assert.equal(action.id, '__finish__');
        return { answer: 'summary ready' };
      },
    };
    const readinessAudit = [];
    const integrationController = new AbortController();
    const adapter = createReadinessAdapter({ task, capsule, page }, {
      getSignal: () => integrationController.signal,
      audit: event => readinessAudit.push(event),
      timeoutMs: 1000,
      pollMs: 25,
    });
    const result = await runDecisionAgent({ task, capsule, page }, { providers, adapter, attemptMs: 5000 });
    assert.equal(result.failure, '');
    assert.equal(result.finalText, 'Final answer: summary ready');
    assert.equal(result.actions, 2);
    assert.equal(result.steps, 3);
    assert.equal(result.turns, 4);
    assert.equal(jevCalls, 3);
    assert.equal(lunaCalls, 1);
    assert.deepEqual(result.provider_usage, {});
    assert.deepEqual(states.map(state => state.observation.tools), [['sign_in'], ['summary'], ['summary']]);
    assert(!states.some(state => state.observation.tools.length === 0));
    assert(!JSON.stringify(states).includes('tool_readiness'));
    assert.deepEqual(result.transcript.filter(entry => entry.executed).map(entry => entry.tool), ['sign_in', 'summary']);
    assert.deepEqual(readinessAudit.map(entry => entry.outcome), ['already-ready', 'ready-after-wait', 'already-ready']);
    assert.deepEqual(readinessAudit.map(entry => entry.step), [1, 2, 3]);
    passed++;
    await context.close();
  }

  console.log(JSON.stringify({ passed }));
} finally {
  await browser.close();
  server.close();
}
