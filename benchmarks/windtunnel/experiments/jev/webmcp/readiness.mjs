import { startUrl } from '../../../harness/tasks.mjs';
import { executeBridgeTool, listLiveTools, prepareWebMCPPage } from '../../../arms/wm-claude.mjs';
import { EMPTY_SCHEMA } from '../frozen/arms/decision-page.mjs';

export const VERSION = 'decision-scaffold-v5-mercury-readiness-v1';

const TIMEOUT = Symbol('timeout');

const abortReason = signal => signal.reason instanceof Error
  ? signal.reason
  : Object.assign(new Error('operation aborted'), { name: 'AbortError' });

export async function waitForTools(page, {
  signal = new AbortController().signal,
  audit = () => {},
  timeoutMs = 1000,
  pollMs = 25,
  step = 1,
} = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0 || !Number.isFinite(pollMs) || pollMs <= 0) throw new Error('Invalid tool readiness timing');
  let waitStarted;
  let initialCount = 0;
  let finalCount = 0;
  let waited = false;
  let polls = 0;
  let finished = false;
  let timeoutTimer;
  let stopResolve;
  let stopReject;
  const stop = new Promise((resolve, reject) => { stopResolve = resolve; stopReject = reject; });
  const finalize = outcome => {
    if (finished) return;
    finished = true;
    audit({
      kind: 'tool_readiness',
      step,
      initial_count: initialCount,
      final_count: finalCount,
      waited,
      wait_ms: waited ? Math.round(performance.now() - waitStarted) : 0,
      polls,
      outcome,
    });
  };
  const abort = () => {
    if (finished) return;
    finalize('aborted');
    stopReject(abortReason(signal));
  };
  const timeout = () => {
    if (finished) return;
    finalize('empty-timeout');
    stopResolve(TIMEOUT);
  };
  const read = isPoll => Promise.race([
    Promise.resolve().then(async () => {
      if (finished || signal.aborted) return TIMEOUT;
      if (isPoll) polls++;
      const tools = await listLiveTools(page);
      if (signal.aborted) throw abortReason(signal);
      return tools;
    }),
    stop,
  ]);
  const pause = async milliseconds => {
    let timer;
    try {
      return await Promise.race([
        new Promise(resolve => { timer = setTimeout(resolve, milliseconds); }),
        stop,
      ]);
    } finally {
      clearTimeout(timer);
    }
  };

  signal.addEventListener('abort', abort, { once: true });
  try {
    if (signal.aborted) {
      abort();
      return await stop;
    }
    const initial = await read(false);
    if (initial === TIMEOUT) return [];
    initialCount = initial.length;
    finalCount = initial.length;
    if (initial.length) {
      finalize('already-ready');
      return initial;
    }

    waited = true;
    waitStarted = performance.now();
    timeoutTimer = setTimeout(timeout, timeoutMs);
    const deadline = waitStarted + timeoutMs;
    while (!finished) {
      const remaining = deadline - performance.now();
      if (remaining <= 0) {
        timeout();
        return [];
      }
      const stopped = await pause(Math.min(pollMs, remaining));
      if (stopped === TIMEOUT || finished) return [];
      const tools = await read(true);
      if (tools === TIMEOUT) return [];
      finalCount = tools.length;
      if (tools.length) {
        finalize('ready-after-wait');
        return tools;
      }
    }
    return [];
  } catch (error) {
    if (!finished) finalize(signal.aborted ? 'aborted' : 'error');
    throw error;
  } finally {
    clearTimeout(timeoutTimer);
    signal.removeEventListener('abort', abort);
  }
}

export function createReadinessAdapter({ task, capsule, page }, {
  getSignal,
  audit = () => {},
  timeoutMs = 1000,
  pollMs = 25,
}) {
  let observations = 0;
  return {
    prepare: () => prepareWebMCPPage(page, startUrl(task, capsule)),
    observe: async () => {
      const tools = await waitForTools(page, {
        signal: getSignal(),
        audit,
        timeoutMs,
        pollMs,
        step: ++observations,
      });
      return {
        observation: { tools: tools.map(tool => tool.name) },
        actions: tools.map(tool => ({
          id: tool.name,
          description: tool.description,
          inputSchema: tool.inputSchema ?? EMPTY_SCHEMA,
        })),
      };
    },
    execute: (action, args) => executeBridgeTool(page, action.id, args),
  };
}
