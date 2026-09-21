import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {chromium} from 'playwright';
import {runWebMCP} from './arm.mjs';

const server=createServer((_req,res)=>res.end(`<!doctype html><script>
navigator.modelContext.registerTool({name:'echo',description:'Echo the requested value',inputSchema:{type:'object',properties:{value:{type:'string'}},required:['value'],additionalProperties:false},execute:async({value})=>({echoed:value})});
</script>`));
server.listen(0,'127.0.0.1');await once(server,'listening');
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();let selections=0,fillers=0;
 const result=await runWebMCP({page,capsule:{baseUrl:`http://127.0.0.1:${server.address().port}`},task:{prompt:'Echo Ada and report the result.',max_steps:{webmcp:3},predicate:{private:'must-not-reach-provider'}}},{
  fetchCall:async(url,options)=>{
   const body=JSON.parse(options.body);assert(!options.body.includes('must-not-reach-provider'));
   if(url.includes('typesafe')){
    assert.equal(body.model,'jev-latest');const id=selections++===0?'echo:':'__finish__:';
    const criteria=body.questions.decision.criteria;const choice=Object.keys(criteria).find(k=>criteria[k].startsWith(id));assert(choice);
    return Response.json({model:'jev-1.13.0',answers:{decision:{choice,confidence:1,probabilities:Object.fromEntries(Object.keys(criteria).map(k=>[k,Number(k===choice)]))}},usage:{input_tokens:100,output_tokens:10}});
   }
   assert.equal(body.model,'mercury-2.5');assert.equal(body.reasoning_effort,'instant');
   const args=fillers++===0?{value:'Ada'}:{answer:'Ada'};
   return Response.json({model:'mercury-2.5',choices:[{finish_reason:'tool_calls',message:{tool_calls:[{function:{name:'submit_arguments',arguments:JSON.stringify(args)}}]}}],usage:{prompt_tokens:100,completion_tokens:10,prompt_tokens_details:{cached_tokens:0}}});
  }
 });
 assert.equal(result.failure,'');assert(result.finalText.includes('Ada'));
 assert.equal(selections,2);assert.equal(fillers,2);
 assert(result.transcript.some(e=>e.tool==='echo'&&e.executed&&e.result.echoed==='Ada'));
 assert(result.transcript.some(e=>e.kind==='tool_readiness'));
 assert(!result.transcript.some(e=>e.provider==='luna'));assert.equal(result.provider_usage.mercury.calls,2);
 console.log('PASS: portable WebMCP method selects, fills, executes and finishes with both measured providers; no predicate leakage.');
}finally{await browser.close();server.closeAllConnections();server.close();}
