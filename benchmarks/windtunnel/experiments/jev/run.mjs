import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {parseArgs} from 'node:util';
import {loadTasks,resolveTask} from '../../harness/tasks.mjs';
import {bootCapsule} from '../../harness/capsule.mjs';
import {runBatch} from './frozen/harness/run.mjs';
import {runWebMCP,TOOL_VERSION as WEBMCP_VERSION} from './webmcp/arm.mjs';
import {runUltrafast,TOOL_VERSION as PAGE_VERSION} from './ultrafast/arm.mjs';

const {values:args} = parseArgs({options:{interface:{type:'string'},site:{type:'string'},task:{type:'string'},repeats:{type:'string',default:'3'},out:{type:'string'},port:{type:'string',default:'3215'},run:{type:'boolean',default:false}}});
assert(['webmcp','dom'].includes(args.interface),'Use --interface webmcp or --interface dom');
const repeats=Number(args.repeats),port=Number(args.port);
assert(Number.isInteger(repeats)&&repeats>=1&&repeats<=3,'--repeats must be 1, 2 or 3');
assert(Number.isInteger(port)&&port>=1024&&port<=65535,'Invalid --port');
assert(!args.task||args.site,'--task requires --site');
const root=path.resolve(import.meta.dirname,'../..');
const hashes=JSON.parse(fs.readFileSync(new URL('./source-manifest.json',import.meta.url)));
for(const entry of [...hashes.files,...hashes.dependencies])assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,entry.file))).digest('hex'),entry.published_sha256,`Runner source changed: ${entry.file}`);
const tasks=JSON.parse(fs.readFileSync(new URL('./tasks.json',import.meta.url))).filter(t=>(!args.site||t.site===args.site)&&(!args.task||t.id===args.task)).map(t=>{
 const definition=resolveTask(loadTasks(t.site).find(x=>x.id===t.id),1);
 assert.equal(crypto.createHash('sha256').update(JSON.stringify(definition)).digest('hex'),t.sha256,`Task changed: ${t.id}`);
 return {...t,definition};
});
assert(tasks.length,'No matching benchmark tasks');
console.log(JSON.stringify({interface:args.interface,tasks:tasks.length,attempts:tasks.length*repeats,mode:args.run?'live':'check-only'}));
if(!args.run)process.exit(0);
assert(Number(process.versions.node.split('.')[0])>=22,'Node 22+ required');
assert(args.out,'--run requires a fresh --out directory');
for(const key of ['TYPESAFE_API_KEY','INCEPTION_API_KEY'])assert(process.env[key],`Missing ${key}`);
assert(!process.env.WT_FAKE_LIFECYCLE&&!process.env.WT_MANUAL_BASEURL,'Real benchmark capsules required');
const out=path.resolve(args.out);
assert(!fs.existsSync(out),'Output already exists; use a new directory');
fs.mkdirSync(out,{recursive:true,mode:0o700});
const {chromium}=await import('playwright');
const browser=await chromium.launch({headless:true,...(process.env.WT_CHROME?{executablePath:process.env.WT_CHROME}:{})});
const webmcp=args.interface==='webmcp';
const method={id:webmcp?'wm-jev-mercury-v3':'a11y-jev-mercury-ultrafast',version:webmcp?WEBMCP_VERSION:PAGE_VERSION,model:'typesafe-ai/jev+mercury-2.5',webmcp,jevTransport:'typesafe',run:webmcp?runWebMCP:runUltrafast};
const rows=[]; let stopped=false;
try{
 for(const site of [...new Set(tasks.map(t=>t.site))]){
  const batch=await runBatch({siteId:site,method,tasks:tasks.filter(t=>t.site===site).map(t=>t.definition),n:repeats,seed:1,port,browser,captureEvidence:true,
   boot:(id,options)=>bootCapsule(id,{...options,env:{...process.env,WT_WEBMCP:webmcp?'1':'0'}}),
   onResult:async row=>{
    rows.push(row);fs.writeFileSync(path.join(out,`${rows.length}.json`),JSON.stringify(row,null,2)+'\n',{mode:0o600});
    // Stop on drift/unknown accounting; never silently pool or automatically rerun it.
    stopped = !!row.transcript?.some(e=>e.cost_unknown||(e.model_snapshot&&!['jev-1.13.0','mercury-2.5'].includes(e.model_snapshot)))||/harness-infra:/.test(row.failure_category??'')||!!row.context_close_error;
    if(stopped)console.error(`Stopped after ${row.run_id}: model drift, unknown cost or infrastructure failure; inspect the saved attempt.`);
    return !stopped;
   }});
  if(batch.teardown_error)throw Error(batch.teardown_error);
  if(stopped)break;
 }
}finally{
 await browser.close();
 fs.writeFileSync(path.join(out,'run.json'),JSON.stringify({options:{reproduction:true,interface:args.interface,automatic_retries:false},rows},null,2)+'\n',{mode:0o600});
}
if(stopped)process.exitCode=1;
