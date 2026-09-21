// Portable extraction of the measured run.mjs method, without historical queue/date gates.
import {runDecisionAgent} from '../frozen/arms/decision-agent.mjs';
import {createDecisionProviders} from '../frozen/arms/decision-providers.mjs';
import {createReadinessAdapter, VERSION} from './readiness.mjs';
import {mercuryProvider} from './mercury.mjs';
import {fetchWithDeadline} from './transport.mjs';
export const TOOL_VERSION = VERSION;
export async function runWebMCP(args, {fetchCall = globalThis.fetch} = {}) {
  const readinessAudit = [];
  let decisionSignal;
  const readiness = {max_wait_ms:1000, poll_interval_ms:25, condition:'empty-only', attempt_budget:'actual-agent-AbortSignal', timeout:'preserve-empty-list', audit:'one-event-per-observation;outside-model-state'};
  const adapter = createReadinessAdapter(args, {getSignal:()=>decisionSignal, audit:e=>readinessAudit.push(e), timeoutMs:1000, pollMs:25});
  const result = await runDecisionAgent({...args, model:undefined, jevTransport:'typesafe'}, {
    adapter,
    providerFactory:opts=>{
      decisionSignal = opts.signal;
      const providers = createDecisionProviders({...opts, fetchCall:(url,options)=>fetchWithDeadline(url,options,{fetchCall,timeoutMs:60000,provider:'Jev'})});
      providers.fillLuna = mercuryProvider({...opts,fetchCall,requestTimeoutMs:60000});
      return providers;
    },
  });
  result.transcript.push(...readinessAudit);
  for (const event of result.transcript) {
    if (event.provider === 'luna') event.provider = 'mercury';
    if (event.requests) { event.requests.mercury = event.requests.luna; delete event.requests.luna; }
  }
  if (result.provider_usage.luna) { result.provider_usage.mercury = result.provider_usage.luna; delete result.provider_usage.luna; }
  Object.assign(result.decision_config, {
    version:VERSION, tool_readiness:readiness,
    tool_readiness_audit:{observations:readinessAudit.length,wait_count:readinessAudit.filter(e=>e.waited).length,total_wait_ms:readinessAudit.reduce((n,e)=>n+(e.wait_ms??0),0),outcomes:readinessAudit.map(e=>e.outcome)},
    argument_policy:'omit-unsupported-optional-fields;entity-specific-identifiers;non-finish-only',
    filler:'mercury-2.5', answer:'mercury-written', optional_arguments:'mercury-filled', answer_assembly:'mercury-written',
    experiment:'mercury-arguments-v2-readiness-v1',
    mercury_final_schema:'omit-transmitted-answer-minLength-maxLength;original-local-validation;messages-unchanged',
    mercury_reasoning_effort:'instant', mercury_max_completion_tokens:4096, mercury_request_timeout_ms:60000, jev_request_timeout_ms:60000,
    mercury_retry_policy:'no-internal-retry;external-outage-supervisor',
    reproduction:'portable-launcher;no-historical-date-gate;no-automatic-supervisor-retries',
  });
  return result;
}
