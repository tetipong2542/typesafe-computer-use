import assert from 'node:assert/strict';
import {createServer} from 'node:http';import {once} from 'node:events';
import {createRequire} from 'node:module';
import {runDecisionAgent} from '../frozen/arms/decision-agent.mjs';
import {createReadinessAdapter} from './readiness.mjs';
const {chromium}=createRequire(import.meta.url)('playwright');
const server=createServer((req,res)=>{const delay=new URL(req.url,'http://local').searchParams.get('delay');res.setHeader('content-type','text/html');res.end(`<script>const old=new AbortController();navigator.modelContext.registerTool({name:'advance',inputSchema:{type:'object',properties:{}},execute:async()=>{old.abort();setTimeout(()=>navigator.modelContext.registerTool({name:'read',inputSchema:{type:'object',properties:{}},execute:async()=>({observed:'ready'})}).catch(()=>{}),${Number(delay)});return {advanced:true}}},{signal:old.signal}).catch(()=>{});</script>`);});
server.listen(0,'127.0.0.1');await once(server,'listening');const baseUrl=`http://127.0.0.1:${server.address().port}`;const browser=await chromium.launch({headless:true});
try{
 for(const [delay,attemptMs,expectAbort]of[[100,2000,false],[650,100,true]]){
  const context=await browser.newContext(),page=await context.newPage();let actualSignal,choices=0;const audit=[];
  const task={prompt:'Advance, read the observed result, and report it.',start_path:`/?delay=${delay}`,max_steps:4};
  const adapter=createReadinessAdapter({task,capsule:{baseUrl},page},{getSignal:()=>actualSignal,audit:e=>audit.push(e),timeoutMs:1000,pollMs:25});
  const result=await runDecisionAgent({task,capsule:{baseUrl},page},{adapter,attemptMs,providerFactory:({signal,record})=>{
   actualSignal=signal;
   const counted=provider=>{record({provider,kind:'request'});record({provider,model_snapshot:'scripted',usage:{input_tokens:0,output_tokens:0},cost:0});};
   return{chooseJev:async state=>{signal.throwIfAborted();counted('jev');const value=['advance','read','__finish__'][choices++];assert(state.actions.some(a=>a.id===value));return{decision:{value,confidence:1}};},fillLuna:async()=>{signal.throwIfAborted();counted('luna');return{answer:'ready'}}};
  }});
  assert.deepEqual(Object.keys(result.provider_usage).sort(),expectAbort?['jev']:['jev','luna']);
  if(expectAbort){assert.match(result.failure,/attempt timeout/);assert(actualSignal.aborted);assert.equal(choices,1);assert.equal(result.turns,1);assert.deepEqual(audit.map(e=>e.outcome),['already-ready','aborted']);const saved=JSON.stringify(audit);await new Promise(r=>setTimeout(r,150));assert.equal(JSON.stringify(audit),saved);}
  else{assert.equal(result.failure,'');assert.equal(result.finalText,'Final answer: ready');assert.equal(result.turns,4);assert.equal(choices,3);assert.deepEqual(audit.map(e=>e.outcome),['already-ready','ready-after-wait','already-ready']);}
  await context.close();
 }
 console.log('PASS: actual providerFactory signal seam caps remaining attempt; readiness audits add no provider usage; completed and aborted rows remain stable (2 real Chromium scenarios).');
}finally{await browser.close();server.close();}
