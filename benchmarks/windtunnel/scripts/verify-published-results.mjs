// Offline release check: dimensions, frozen metrics, credential-free publication.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const run=read('results/canonical/run.json');
const base=JSON.parse(execFileSync('git',['show','8362c13:results/canonical/run.json'],{maxBuffer:100*1024*1024}));
const byId=new Map(run.rows.map(r=>[r.run_id,r]));
assert.equal(run.rows.length,3087);assert.equal(byId.size,3087);assert.equal(run.verdicts.length,1029);
for(const row of base.rows){
 const published=byId.get(row.run_id);assert(published);
 for(const [key,value] of Object.entries(row))if(typeof value==='number'||typeof value==='boolean')assert.equal(published[key],value,`${row.run_id}: ${key}`);
}
const expected=[['wm-jev-mercury-v3',141,49],['a11y-jev-mercury-ultrafast',76,25]];
for(const [arm,passes,solved] of expected){
 const rows=run.rows.filter(r=>r.arm===arm);assert.equal(rows.length,147);assert.equal(rows.filter(r=>r.success).length,passes);
 assert.equal(run.verdicts.filter(v=>v.method===arm&&v.solved).length,solved);
 const cells=new Map();for(const r of rows){const k=r.site+'/'+r.task_id;cells.set(k,(cells.get(k)||0)+1);}
 assert.equal(cells.size,49);assert([...cells.values()].every(n=>n===3));
}
for(const r of run.rows)assert(!('transcript' in r)&&!('decision_config' in r));
for(const file of ['results/canonical/run.json','results/canonical/tasks.json','results/canonical/explorer.html','results/2026-09-18-jev-mercury/run.json']){
 const text=fs.readFileSync(file,'utf8');assert(!/\/Users\/|\/private\/tmp\/|\b(?:sk-ant-api\d\d-|sk-proj-|ghp_)[A-Za-z0-9_-]{10,}/.test(text),file);
}
for(const tasks of Object.values(read('results/canonical/tasks.json')))for(const task of tasks){
 for(const match of task.prompt.matchAll(/password(?:\s*[:=]\s*|\s+(?:is\s+)?)(\S+)/gi))assert.match(match[1],/^\[redacted\][.,;]?$/);
}
assert(!fs.readFileSync('results/canonical/explorer.html','utf8').includes('0×–13× cheaper'));
console.log('Publication verified: 21 configurations, 3087 attempts; prior numerical metrics unchanged; both Jev cohorts complete; no raw traces or unredacted task passwords.');
