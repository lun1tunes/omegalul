'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(path.join(workspace,'n8n/workflows/tnavigator-schedule-hybrid-retrieval.workflow.json'),'utf8'));
const ingestion = JSON.parse(fs.readFileSync(path.join(workspace,'n8n/workflows/tnavigator-schedule-knowledge-ingestion.workflow.json'),'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const src=(wf,name)=>{const n=wf.nodes.find(x=>x.name===name);assert(n&&n.type==='n8n-nodes-base.code',`missing ${name}`);return n.parameters.jsCode};
async function run(name,{json={},items=[],nodes={}}={}){const fn=new AsyncFunction('$json','$input','$',(src(workflow,name)));const result=await fn(json,{all:()=>items.map(json=>({json}))},name=>({first:()=>({json:nodes[name]||{}})}));assert(result?.[0]?.json);return result[0].json}
async function ingest(json){const fn=new AsyncFunction('$json',src(ingestion,'Normalize approved SCHEDULE knowledge'));const r=await fn(json);return r[0].json}
const block=(over={})=>({schedule_knowledge_block:{contract:'schedule_knowledge_block',contract_version:'1.0',target_base:'schedule_mvp',knowledge_type:'keyword_instruction',knowledge_id:'wconprod-v1',revision:'1',title:'WCONPROD — прогноз',keywords:['WCONPROD'],topics:['Контроль по скважинам','Прогноз'],task_patterns:['задать лимит по воде'],status:'active',author:'expert',access_scope:'petroleum-engineering',text:'Полная инструкция по WCONPROD.',...over}});
const meta=(over={})=>({target_base:'schedule_mvp',knowledge_type:'keyword_instruction',knowledge_id:'wconprod-v1',revision:'1',parent_key:'schedule_mvp:wconprod-v1:1',keyword_families:['WCONPROD'],access_scope:'petroleum-engineering',knowledge_status:'current',ingest_key:'chunk-1',...over});
const query={contract:'schedule_retrieval_query',contract_version:'1.0',query:'WCONPROD лимит по воде',top_k:10,exact_keyword_terms:['WCONPROD'],filters:{target_base:'schedule_mvp',access_scope:'petroleum-engineering',knowledge_types:['keyword_instruction','worked_example'],keyword_families:['WCONPROD']}};
const catalogue=()=>({contract:'schedule_schema_catalogue',contract_version:'1.0',catalogue_hash:`sha256:${'b'.repeat(64)}`,simulator_profile:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2'},schemas:[{schema_id:'expert:WCONPROD',schema_revision:'1',keyword:'WCONPROD',fields:[{name:'WELL',position:1,type:'string',required:true}],semantics:{period:'FORECAST'}}]});
(async()=>{
 const valid=await ingest(block());assert.equal(valid.status,'approved_for_ingestion');assert.equal(valid.metadata.target_base,'schedule_mvp');assert.equal(valid.metadata.knowledge_type,'keyword_instruction');assert.equal(valid.knowledge_block.text,'Полная инструкция по WCONPROD.');
 const example=await ingest(block({knowledge_type:'worked_example',knowledge_id:'water-limit-example',text:'',examples:[{task:'лимит воды',schedule_text:'WCONPROD ... /',explanation:'пример'}]}));assert.equal(example.status,'approved_for_ingestion');
 const badBase=await ingest(block({target_base:'arbitrary_sql_table'}));assert(badBase.findings.some(x=>x.code==='TARGET_BASE_NOT_ALLOWLISTED'));
 const inactive=await ingest(block({status:'inactive'}));assert(inactive.findings.some(x=>x.code==='ACTIVE_KNOWLEDGE_REQUIRED'));
 const lookup={'Validate SCHEDULE retrieval request':query};
 const lex=await run('Wrap lexical candidates',{nodes:lookup,items:[{candidate_id:'chunk-1',page_content:'WCONPROD инструкция',metadata:meta(),lexical_rank:1,lexical_score:.9,exact_hit:1}]});
 const tag=await run('Wrap tag candidates',{nodes:lookup,items:[{candidate_id:'chunk-1',page_content:'WCONPROD инструкция',metadata:meta(),tag_rank:1}]});
 const sem=await run('Wrap semantic candidates',{nodes:lookup,items:[{document:{pageContent:'WCONPROD инструкция',metadata:meta()},score:.95}]});
 const ranked=await run('Fuse authorized candidates with deterministic RRF',{items:[lex,tag,sem]});assert.equal(ranked.status,'ranked');assert.equal(ranked.ranked_parents.length,1);assert.deepEqual(ranked.ranked_parents[0].branches,['lexical','semantic','tag']);
 const hydrated=await run('Hydrate full parent knowledge blocks',{nodes:{'Prepare full parent knowledge lookup':{evidence:ranked}},items:[{target_base:'schedule_mvp',knowledge_id:'wconprod-v1',revision:'1',knowledge_type:'keyword_instruction',status:'active',keywords:['WCONPROD'],topics:['Прогноз'],task_patterns:['лимит воды'],title:'WCONPROD',body_json:block().schedule_knowledge_block,content_hash:'fnv1a32:12345678',access_scope:'petroleum-engineering',author:'expert'}]});assert.equal(hydrated.status,'succeeded');assert.equal(hydrated.results[0].body.text,'Полная инструкция по WCONPROD.');
 const final=await run('Attach approved schema catalogue',{nodes:{'Prepare approved schema catalogue lookup':{evidence:hydrated}},items:[{schema_catalogue:catalogue()}]});assert.equal(final.status,'succeeded');assert.equal(final.evidence_ready,true);assert.equal(final.retrieval.full_parent_hydration,true);
 const isolated=await run('Fuse authorized candidates with deterministic RRF',{items:[{...lex,candidates:lex.candidates.map(c=>({...c,metadata:meta({target_base:'other'}),rank:1}))},tag,sem]});assert.equal(isolated.ranked_parents.length,1); // authorized tag/semantic candidates survive, foreign lexical one is discarded
 const noInstruction=await run('Fuse authorized candidates with deterministic RRF',{items:[{branch:'lexical',query,candidates:[{candidate_id:'ex',page_content:'пример',metadata:meta({knowledge_type:'worked_example',knowledge_id:'ex',parent_key:'schedule_mvp:ex:1'}),rank:1}],branch_findings:[]}]});assert.equal(noInstruction.status,'abstain');assert(noInstruction.findings.some(x=>x.code==='KEYWORD_INSTRUCTION_COVERAGE_INCOMPLETE'));
 console.log('SCHEDULE RAG runtime smoke: 10 scenarios passed');
})().catch(e=>{console.error(e.stack||e);process.exit(1)});
