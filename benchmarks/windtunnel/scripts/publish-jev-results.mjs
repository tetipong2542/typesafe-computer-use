// Publish metrics from the two completed cohorts; never copy raw traces.
// node scripts/publish-jev-results.mjs <webmcp-cohort-dir> <page-cohort-dir>
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {CSV_COLUMNS, csv, verdictFor} from '../harness/lib.mjs';
import {loadTasksBySite, renderExplorerHTML} from '../harness/build_explorer.mjs';
import {resolveTask} from '../harness/tasks.mjs';
const root=path.resolve(import.meta.dirname,'..'), out=path.join(root,'results/canonical');
const source='2026-09-18-jev-mercury', release=path.join(root,'results',source);
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const write=(p,d)=>fs.writeFileSync(p,JSON.stringify(d,null,2)+'\n');
const hash=d=>crypto.createHash('sha256').update(JSON.stringify(d)).digest('hex');
const tasks=Object.fromEntries(Object.entries(loadTasksBySite()).map(([site,ts])=>[site,ts.filter(t=>!t.excluded).map(t=>resolveTask(t,1))]));
const credentials=new Set(Object.values(tasks).flat().flatMap(t=>[...t.prompt.matchAll(/password(?:\s*[:=]\s*|\s+(?:is\s+)?)[`"']?([^\s,`"']+)/gi)].map(m=>m[1].replace(/[.;]$/,''))));
const cleanText=s=>[...credentials].reduce((text,value)=>text.replaceAll(value,'[redacted]'),String(s));
function clean(value){
 if(typeof value==='string')return cleanText(value);
 if(Array.isArray(value))return value.map(clean);
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,/password|authorization|api[_-]?key/i.test(k)?'[redacted]':clean(v)]));
 return value;
}
const fields=new Set([...CSV_COLUMNS,'method','pass','model_snapshot','ms','tokens','final_text','evaluation_s']);
const publicRow=r=>clean(Object.fromEntries(Object.entries(r).filter(([key])=>fields.has(key))));
const definitions=[['wm-jev-mercury-v3','decision-scaffold-v5-mercury-readiness-v1'],['a11y-jev-mercury-ultrafast','ultrafast-windtunnel-v2']];
const dirs=process.argv.slice(2);assert.equal(dirs.length,2,'Supply both completed cohort directories');
const rows=[], cohorts=[];
for(let i=0;i<2;i++){
 const dir=dirs[i], completion=read(path.join(dir,'completion.json')), manifest=read(path.join(dir,'manifest.json'));
 assert.equal(completion.state,'complete');assert.equal(completion.completed,147);
 const records=fs.readdirSync(dir).filter(f=>/-mercury-[123]\.json$/.test(f)).sort().map(f=>read(path.join(dir,f)));
 assert.equal(records.length,147);
 const cells=new Map();
 for(const record of records){
  const {checksum,...body}=record;assert.equal(hash(body),checksum,'Checkpoint checksum');
  assert.equal(record.status,'complete');assert.equal(record.configuration_hash,manifest.configuration_hash);
  assert.equal(record.row.arm,definitions[i][0]);assert.equal(record.row.tool_version,definitions[i][1]);assert.equal(record.row.model,'typesafe-ai/jev+mercury-2.5');
  assert(!record.infra&&!record.flags.length);assert.equal(record.row.pass,record.row.success);
  const task=tasks[record.row.site].find(t=>t.id===record.row.task_id);assert(task);assert.equal(hash(task),record.task_hash);
  const key=record.row.site+'|'+record.row.task_id;cells.set(key,(cells.get(key)||0)+1);
  rows.push({...publicRow(record.row),source});
 }
 assert.equal(cells.size,49);assert([...cells.values()].every(n=>n===3));
 cohorts.push({arm:definitions[i][0],tool_version:definitions[i][1],configuration_hash:manifest.configuration_hash,attempts:147,
  known_attempt_cost_usd:records.reduce((sum,r)=>sum+r.row.est_cost_usd,0),
  known_total_cost_usd:completion.total_known_reported_cost_usd??completion.standard_estimated_cost_usd,
  unknown_cost_reserve_usd:(completion.outage_unknown_reserved_usd??0)+(completion.terminal_refusal_unknown_reserved_usd??0),
  model_snapshots:['jev-1.13.0','mercury-2.5']});
}
const groups=new Map();
for(const row of rows){const key=row.site+'|'+row.arm+'|'+row.task_id; if(!groups.has(key))groups.set(key,[]); groups.get(key).push(row);}
const verdicts=[...groups.values()].map(rs=>({site:rs[0].site,taskId:rs[0].task_id,tier:tasks[rs[0].site].find(t=>t.id===rs[0].task_id).tier,method:rs[0].arm,model:rs[0].model,...verdictFor(rs),source}));
const prior=read(path.join(out,'run.json'));
const oldRows=prior.rows.filter(r=>!definitions.some(([arm])=>r.arm===arm));assert.equal(oldRows.length,2793);
const allRows=[...oldRows.map(publicRow),...rows];assert.equal(new Set(allRows.map(r=>r.run_id)).size,3087);
fs.mkdirSync(release,{recursive:true});
write(path.join(release,'run.json'),{options:{release:'v1.2',privacy:'Metrics and redacted final answers only; transcripts and private runtime configuration omitted.',cohorts},rows,verdicts});
fs.writeFileSync(path.join(release,'results.csv'),csv(rows));
write(path.join(out,'run.json'),{options:{...prior.options,generated:'2026-09-18',release:'v1.2',sources:[...new Set([...prior.options.sources,source])],privacy:'Metrics and redacted final answers only; transcripts omitted.'},rows:allRows,verdicts:[...prior.verdicts.filter(v=>!definitions.some(([arm])=>v.method===arm)),...verdicts]});
fs.writeFileSync(path.join(out,'results.csv'),csv(allRows));
write(path.join(out,'tasks.json'),clean(tasks));
const html=renderExplorerHTML({rows:allRows,tasksBySite:clean(tasks),canonical:true,runCount:new Set([...prior.options.sources,source]).size,meta:{date:'2026-09-18',label:'Canonical board v1.2',preset:'full',sites:'full',n:3,notes:['21 configurations, 49 tasks, three attempts per task. Jev + Mercury 2.5 uses WebMCP or ultrafast DOM controls. Published data excludes transcripts and redacts fixture credentials; scoring is unchanged.','Costs are standard estimates from reported usage. Some refused or failed requests have unknown usage; reserves and excluded infrastructure spend are listed in the Jev release provenance.']}});
fs.writeFileSync(path.join(out,'explorer.html'),html);
fs.writeFileSync(path.join(out,'explorer-artifact.html'),(html.match(/<style>[\s\S]*?<\/style>/)||[''])[0]+'\n'+(html.match(/<body>([\s\S]*?)<\/body>/)||['',''])[1]);
console.log(JSON.stringify({configurations:21,attempts:allRows.length,verdicts:prior.verdicts.filter(v=>!definitions.some(([arm])=>v.method===arm)).length+verdicts.length,cohorts}));
