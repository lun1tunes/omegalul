"""Practical SCHEDULE hybrid-RAG workflows for n8n 2.30.8.

The workflows implement the internal-MVP expert knowledge base. Credentials
are selected in UI; knowledge itself never enters the workflow export.
"""
from __future__ import annotations

import json


KEYWORDS = [
    "DATES", "INCLUDE", "GRUPTREE", "WELSPECS", "WELLTRACK", "COMPDATMD",
    "WCONHIST", "WCONPROD", "GCONPROD", "BRANPROP", "NODEPROP",
    "FRACTURE_SPECS", "FRACTURE_STAGE", "WECON", "WTEST",
]


def _credential(name: str) -> dict:
    return {"postgres": {"id": "REPLACE_IN_UI", "name": name}}


def _embedding_credential(name: str) -> dict:
    return {"openAiApi": {"id": "REPLACE_IN_UI", "name": name}}


INGEST_NORMALIZE = r"""
const incoming=$json.schedule_knowledge_block??$json.schedule_knowledge_document??$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}};
const list=v=>arr(v)?[...new Set(v.map(clean).filter(Boolean))]:typeof v==='string'?(()=>{try{const x=JSON.parse(v);return arr(x)?list(x):v.split(/[,;|]/).map(clean).filter(Boolean)}catch{return v.split(/[,;|]/).map(clean).filter(Boolean)}})():[];
const raw=obj(incoming)?incoming:{};
const m={...parse(raw.metadata_json),...(obj(raw.metadata)?raw.metadata:{})},catalogue=parse(raw.schema_catalogue_json??raw.schema_catalogue),catalogueProvided=Object.keys(catalogue).length>0,findings=[];
const allowed=new Set(KEYWORDS_PLACEHOLDER),targetBase=clean(raw.target_base||m.target_base||'schedule_mvp'),allowBases=new Set(['schedule_mvp']);
const knowledgeType=clean(raw.knowledge_type||m.knowledge_type).toLowerCase(),allowTypes=new Set(['keyword_instruction','worked_example']);
const knowledgeId=clean(raw.knowledge_id||raw.document_id||m.knowledge_id||m.document_id),revision=clean(raw.revision||raw.document_revision||m.revision||m.document_revision||'1'),status=clean(raw.status||raw.knowledge_status||m.status||m.knowledge_status||'active').toLowerCase();
const keywords=list(raw.keywords||raw.keyword_families||m.keywords||m.keyword_families).map(v=>v.toUpperCase()).filter(v=>allowed.has(v)),topics=list(raw.topics||m.topics),taskPatterns=list(raw.task_patterns||m.task_patterns),examples=arr(raw.examples)?raw.examples.filter(obj).slice(0,100):[];
const text=clean(raw.text||raw.document_text),title=clean(raw.title||m.title||knowledgeId),author=clean(raw.author||raw.approved_by||m.author||m.approved_by),accessScope=clean(raw.access_scope||m.access_scope||'petroleum-engineering');
const authority='department_expert',sourceHash=clean(raw.source_hash||m.source_hash).toLowerCase(),page=clean(raw.page||m.page),heading=clean(raw.heading||m.heading||title);
const body={contract:'schedule_knowledge_block',contract_version:'1.0',target_base:targetBase,knowledge_type:knowledgeType,knowledge_id:knowledgeId,revision,title,keywords,topics,task_patterns:taskPatterns,simulator_family:list(raw.simulator_family||m.simulator_family||['E100','E300','tNavigator']),status,author,text,examples,schema_catalogue:catalogueProvided?catalogue:null};
const searchable=[title,text,keywords.join(' '),topics.join(' '),taskPatterns.join(' '),...examples.flatMap(e=>[clean(e.title),clean(e.task),clean(e.schedule_text),clean(e.explanation)])].filter(Boolean).join('\n\n');
if(!allowBases.has(targetBase))findings.push({code:'TARGET_BASE_NOT_ALLOWLISTED',severity:'error',target_base:targetBase});
if(!allowTypes.has(knowledgeType))findings.push({code:'KNOWLEDGE_TYPE_INVALID',severity:'error'});if(!knowledgeId)findings.push({code:'KNOWLEDGE_ID_REQUIRED',severity:'error'});if(!revision)findings.push({code:'REVISION_REQUIRED',severity:'error'});if(!title)findings.push({code:'TITLE_REQUIRED',severity:'error'});if(!author)findings.push({code:'EXPERT_AUTHOR_REQUIRED',severity:'error'});if(!accessScope)findings.push({code:'ACCESS_SCOPE_REQUIRED',severity:'error'});
if(status!=='active')findings.push({code:'ACTIVE_KNOWLEDGE_REQUIRED',severity:'error'});if(!searchable)findings.push({code:'KNOWLEDGE_TEXT_REQUIRED',severity:'error'});if(searchable.length>2000000)findings.push({code:'KNOWLEDGE_TEXT_TOO_LARGE',severity:'error'});if(!keywords.length)findings.push({code:'NO_SCHEDULE_KEYWORD_FOUND',severity:'error'});if(knowledgeType==='keyword_instruction'&&!text)findings.push({code:'FULL_KEYWORD_INSTRUCTION_REQUIRED',severity:'error'});if(knowledgeType==='worked_example'&&!examples.length&&!text)findings.push({code:'WORKED_EXAMPLE_REQUIRED',severity:'error'});
const hash=s=>{let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return(h>>>0).toString(16).padStart(8,'0')},contentHash=sourceHash||`fnv1a32:${hash(JSON.stringify(body))}`,documentId=knowledgeId,documentRevision=revision;
if(catalogueProvided){const cp=obj(catalogue.simulator_profile)?catalogue.simulator_profile:{},schemas=arr(catalogue.schemas)?catalogue.schemas:[];
 if(catalogue.contract!=='schedule_schema_catalogue'||catalogue.contract_version!=='1.0')findings.push({code:'SCHEMA_CATALOGUE_CONTRACT_INVALID',severity:'error'});if(!/^sha256:[a-f0-9]{64}$/i.test(clean(catalogue.catalogue_hash)))findings.push({code:'SCHEMA_CATALOGUE_HASH_INVALID',severity:'error'});if(!/^sha256:[a-f0-9]{64}$/i.test(clean(catalogue.source_hash)))findings.push({code:'SCHEMA_SOURCE_HASH_INVALID',severity:'error'});if(clean(cp.simulator).toLowerCase()!=='tnavigator'||clean(cp.version)!=='22.2')findings.push({code:'SCHEMA_PROFILE_NOT_APPROVED',severity:'error'});if(!schemas.length)findings.push({code:'SCHEMA_CATALOGUE_EMPTY',severity:'error'});
 for(const entry of schemas){const kw=clean(entry.keyword).toUpperCase(),fields=arr(entry.fields)?entry.fields:[],names=new Set(fields.map(f=>clean(f?.name)).filter(Boolean)),sem=obj(entry.semantics)?entry.semantics:null,fieldOk=f=>clean(f)&&names.has(clean(f));if(!allowed.has(kw)||!clean(entry.schema_id)||!clean(entry.schema_revision)||!fields.length)findings.push({code:'SCHEMA_ENTRY_INVALID',severity:'error',keyword:kw});if(!sem)findings.push({code:'SCHEMA_SEMANTICS_REQUIRED',severity:'error',keyword:kw});else{for(const d of(arr(sem.definitions)?sem.definitions:[]))if(!obj(d)||!clean(d.entity_type)||!fieldOk(d.id_field))findings.push({code:'SEMANTIC_DEFINITION_INVALID',severity:'error',keyword:kw});for(const r of(arr(sem.references)?sem.references:[]))if(!obj(r)||!clean(r.entity_type)||!fieldOk(r.id_field))findings.push({code:'SEMANTIC_REFERENCE_INVALID',severity:'error',keyword:kw});}}
}
const normalizedCatalogue=catalogueProvided?{...catalogue,approved:catalogue.approved!==false,approved_by:clean(catalogue.approved_by||catalogue.author||author),author:clean(catalogue.author||catalogue.approved_by||author),approval_gate_id:clean(catalogue.approval_gate_id||`expert:${knowledgeId}:${revision}`),access_scope:clean(catalogue.access_scope||accessScope)}:null;
return[{json:{contract:'schedule_knowledge_document',contract_version:'1.0',status:findings.length?'needs_input':'approved_for_ingestion',text:searchable,knowledge_block:{...body,schema_catalogue:normalizedCatalogue},metadata:{document_id:documentId,document_revision:documentRevision,source_hash:contentHash,target_base:targetBase,knowledge_type:knowledgeType,knowledge_id:knowledgeId,revision,status:'active',access_scope:accessScope,author,approved_by:author,authority_level:authority,approval_status:'approved',section:'SCHEDULE',knowledge_status:'current',title,page,heading,keyword_families:keywords,topics,task_patterns:taskPatterns,parent_key:`${targetBase}:${knowledgeId}:${revision}`,ingest_key:`${targetBase}:${knowledgeId}:${revision}:${hash(searchable)}`,vendor:'department',simulator:'tNavigator',simulator_version:'22.2'},schema_catalogue:normalizedCatalogue,catalogue_present:catalogueProvided,findings}}];
""".replace("KEYWORDS_PLACEHOLDER", json.dumps(KEYWORDS))


INGEST_RESULT = r"""
const x=$('Normalize approved SCHEDULE knowledge').first().json,db=$json||{};let parent={};try{parent=$('PostgreSQL — upsert full parent knowledge').first().json||{}}catch{}
return[{json:{contract:'schedule_knowledge_ingest_result',contract_version:'1.0',status:'ingested',target_base:x.metadata.target_base,knowledge_type:x.metadata.knowledge_type,knowledge_id:x.metadata.knowledge_id,revision:x.metadata.revision,parent_document_stored:Number(parent.documents_stored||0)===1,vector_table:'tnavigator_schedule_knowledge_v1',parent_table:'tnavigator_schedule_knowledge_documents_v1',schema_catalogue_table:'tnavigator_schedule_schema_catalogue_v1',schema_catalogue_stored:x.catalogue_present===true&&Number(db.catalogues_stored||0)===1,catalogue_hash:x.schema_catalogue?.catalogue_hash||null,embedding_profile:'configure one identical model/dimensions in ingestion and retrieval',idempotency_key:x.metadata.ingest_key,findings:[]}}];
"""


PREPARE_PARENT_PERSIST = r"""
const x=$('Normalize approved SCHEDULE knowledge').first().json,m=x.metadata,b=x.knowledge_block;
return[{json:{...x,sql_parameters:[m.target_base,m.knowledge_id,m.revision,m.knowledge_type,'active',JSON.stringify(m.keyword_families||[]),JSON.stringify(m.topics||[]),JSON.stringify(m.task_patterns||[]),m.title,JSON.stringify(b),x.text,m.source_hash,m.access_scope,m.author]}}];
"""


PARENT_UPSERT_SQL = """WITH superseded AS (UPDATE tnavigator_schedule_knowledge_documents_v1 SET status='superseded',stored_at=now() WHERE target_base=$1 AND knowledge_id=$2 AND revision<>$3 AND status='active' RETURNING 1), inserted AS (INSERT INTO tnavigator_schedule_knowledge_documents_v1
(target_base,knowledge_id,revision,knowledge_type,status,keywords,topics,task_patterns,title,body_json,searchable_text,content_hash,access_scope,author)
VALUES($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10::jsonb,$11,$12,$13,$14)
ON CONFLICT(target_base,knowledge_id,revision) DO UPDATE SET knowledge_type=EXCLUDED.knowledge_type,status=EXCLUDED.status,keywords=EXCLUDED.keywords,topics=EXCLUDED.topics,task_patterns=EXCLUDED.task_patterns,title=EXCLUDED.title,body_json=EXCLUDED.body_json,searchable_text=EXCLUDED.searchable_text,content_hash=EXCLUDED.content_hash,access_scope=EXCLUDED.access_scope,author=EXCLUDED.author,stored_at=now() RETURNING 1)
SELECT count(*)::int AS documents_stored FROM inserted"""


PREPARE_CATALOGUE_PERSIST = r"""
const x=$('Normalize approved SCHEDULE knowledge').first().json,c=x.schema_catalogue,m=x.metadata;
return[{json:{...x,sql_parameters:[c?JSON.stringify(c):'',c?.catalogue_hash||'',c?.catalogue_ref||`expert://${m.target_base}/${m.knowledge_id}/${m.revision}`,c?.source_hash||m.source_hash,c?.access_scope||m.access_scope,c?.approved_by||m.author,c?.approval_gate_id||`expert:${m.knowledge_id}:${m.revision}`,m.target_base]}}];
"""


CATALOGUE_UPSERT_SQL = """WITH payload AS (SELECT NULLIF($1,'')::jsonb AS body), inserted AS (INSERT INTO tnavigator_schedule_schema_catalogue_v1
(catalogue_hash,catalogue_ref,source_hash,access_scope,simulator_version,approved_by,approval_gate_id,target_base,schema_catalogue)
SELECT $2,$3,$4,$5,'22.2',$6,$7,$8,body FROM payload WHERE body IS NOT NULL
ON CONFLICT(catalogue_hash) DO UPDATE SET catalogue_ref=EXCLUDED.catalogue_ref,source_hash=EXCLUDED.source_hash,access_scope=EXCLUDED.access_scope,approved_by=EXCLUDED.approved_by,approval_gate_id=EXCLUDED.approval_gate_id,target_base=EXCLUDED.target_base,schema_catalogue=EXCLUDED.schema_catalogue,stored_at=now() RETURNING catalogue_hash)
SELECT count(*)::int AS catalogues_stored FROM inserted"""


FINALIZE_INGEST_SQL = """CREATE TABLE IF NOT EXISTS tnavigator_schedule_knowledge_documents_v1 (
 target_base text NOT NULL, knowledge_id text NOT NULL, revision text NOT NULL,
 knowledge_type text NOT NULL, status text NOT NULL, keywords jsonb NOT NULL,
 topics jsonb NOT NULL, task_patterns jsonb NOT NULL, title text NOT NULL,
 body_json jsonb NOT NULL, searchable_text text NOT NULL, content_hash text NOT NULL,
 access_scope text NOT NULL, author text NOT NULL, stored_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(target_base,knowledge_id,revision));
CREATE TABLE IF NOT EXISTS tnavigator_schedule_schema_catalogue_v1 (
 catalogue_hash text PRIMARY KEY,catalogue_ref text NOT NULL,source_hash text NOT NULL,
 access_scope text NOT NULL,simulator_version text NOT NULL CHECK(simulator_version='22.2'),
 approved_by text NOT NULL,approval_gate_id text NOT NULL,target_base text NOT NULL DEFAULT 'schedule_mvp',
 schema_catalogue jsonb NOT NULL,stored_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE tnavigator_schedule_schema_catalogue_v1 ADD COLUMN IF NOT EXISTS target_base text NOT NULL DEFAULT 'schedule_mvp';
CREATE INDEX IF NOT EXISTS tn_sched_kb_metadata_gin ON tnavigator_schedule_knowledge_v1 USING gin (metadata);
CREATE INDEX IF NOT EXISTS tn_sched_kb_lexical_gin ON tnavigator_schedule_knowledge_v1 USING gin (to_tsvector('simple',coalesce(text,'')));
WITH ranked AS (SELECT id,row_number() OVER(PARTITION BY metadata->>'ingest_key',md5(text) ORDER BY id::text) rn FROM tnavigator_schedule_knowledge_v1), deleted AS (DELETE FROM tnavigator_schedule_knowledge_v1 t USING ranked r WHERE t.id=r.id AND r.rn>1 RETURNING t.id) SELECT count(*)::int AS duplicates_removed FROM deleted"""


RETRIEVAL_NORMALIZE = r"""
const raw=$json.schedule_retrieval_request??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}},f={...parse(raw.filters_json),...(obj(raw.filters)?raw.filters:{})},findings=[];
const query=clean(raw.query),targetBase=clean(f.target_base||raw.target_base||'schedule_mvp'),accessScope=clean(f.access_scope||raw.access_scope||'petroleum-engineering'),requestId=clean(raw.request_id),allowedBases=new Set(['schedule_mvp']);
if(!query)findings.push({code:'QUERY_REQUIRED',severity:'error'});if(query.length>8000)findings.push({code:'QUERY_TOO_LARGE',severity:'error'});if(!allowedBases.has(targetBase))findings.push({code:'TARGET_BASE_NOT_ALLOWLISTED',severity:'error'});if(!accessScope)findings.push({code:'ACCESS_SCOPE_REQUIRED',severity:'error'});
const allowed=new Set(KEYWORDS_PLACEHOLDER),exact=[...new Set((query.match(/\b[A-Z][A-Z0-9_]+\b/g)||[]).filter(k=>allowed.has(k)))],tags=Array.isArray(f.keyword_families)?f.keyword_families.map(v=>clean(v).toUpperCase()).filter(v=>allowed.has(v)):exact;
const types=Array.isArray(f.knowledge_types)?[...new Set(f.knowledge_types.map(v=>clean(v).toLowerCase()).filter(v=>['keyword_instruction','worked_example'].includes(v)))]:['keyword_instruction','worked_example'],topics=Array.isArray(f.topics)?[...new Set(f.topics.map(clean).filter(Boolean))]:[],patterns=Array.isArray(f.task_patterns)?[...new Set(f.task_patterns.map(clean).filter(Boolean))]:[];
return[{json:{contract:'schedule_retrieval_query',contract_version:'1.0',status:findings.length?'needs_input':'query_ready',query,request_id:requestId||null,top_k:Math.min(30,Math.max(1,Math.trunc(Number(raw.top_k)||10))),filters:{target_base:targetBase,access_scope:accessScope,knowledge_types:types,keyword_families:tags,topics,task_patterns:patterns,simulator_version:'22.2',section:'SCHEDULE',knowledge_status:'current'},exact_keyword_terms:exact,findings}}];
""".replace("KEYWORDS_PLACEHOLDER", json.dumps(KEYWORDS))


PREPARE_LEXICAL = r"""const x=$json;return[{json:{...x,branch:'lexical',sql_parameters:[x.query,x.filters.target_base,x.filters.access_scope,x.top_k,JSON.stringify(x.filters.keyword_families||[]),JSON.stringify(x.filters.knowledge_types||[])]}}];"""
LEXICAL_SQL = """WITH authorized AS (SELECT id,text,metadata FROM tnavigator_schedule_knowledge_v1 WHERE metadata->>'target_base'=$2 AND metadata->>'access_scope'=$3 AND metadata->>'knowledge_status'='current' AND metadata->>'knowledge_type' IN (SELECT jsonb_array_elements_text($6::jsonb))), ranked AS (SELECT id::text candidate_id,text page_content,metadata,CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements_text($5::jsonb) q WHERE upper(trim(q.value))=ANY(regexp_split_to_array(upper(regexp_replace(coalesce(metadata->>'keyword_families',''),'[\\[\\]\"]','','g')),'\\s*,\\s*'))) THEN 1 ELSE 0 END exact_hit,ts_rank_cd(to_tsvector('simple',coalesce(text,'')),websearch_to_tsquery('simple',$1)) lexical_score FROM authorized WHERE to_tsvector('simple',coalesce(text,''))@@websearch_to_tsquery('simple',$1) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text($5::jsonb) q WHERE upper(trim(q.value))=ANY(regexp_split_to_array(upper(regexp_replace(coalesce(metadata->>'keyword_families',''),'[\\[\\]\"]','','g')),'\\s*,\\s*')))) SELECT candidate_id,page_content,metadata,exact_hit,lexical_score,row_number() OVER(ORDER BY exact_hit DESC,lexical_score DESC,candidate_id) lexical_rank FROM ranked ORDER BY lexical_rank LIMIT $4"""
PREPARE_TAG = r"""const x=$('Validate SCHEDULE retrieval request').first().json;return[{json:{...x,branch:'tag',sql_parameters:[x.filters.target_base,x.filters.access_scope,JSON.stringify(x.filters.keyword_families||[]),JSON.stringify(x.filters.topics||[]),JSON.stringify(x.filters.task_patterns||[]),JSON.stringify(x.filters.knowledge_types||[]),x.top_k]}}];"""
TAG_SQL = """SELECT id::text candidate_id,text page_content,metadata,row_number() OVER(ORDER BY id::text) tag_rank FROM tnavigator_schedule_knowledge_v1 WHERE metadata->>'target_base'=$1 AND metadata->>'access_scope'=$2 AND metadata->>'knowledge_status'='current' AND metadata->>'knowledge_type' IN (SELECT jsonb_array_elements_text($6::jsonb)) AND (($3::jsonb='[]'::jsonb AND $4::jsonb='[]'::jsonb AND $5::jsonb='[]'::jsonb) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text($3::jsonb) q WHERE upper(trim(q.value))=ANY(regexp_split_to_array(upper(regexp_replace(coalesce(metadata->>'keyword_families',''),'[\\[\\]\"]','','g')),'\\s*,\\s*'))) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text($4::jsonb) q WHERE lower(trim(q.value))=ANY(regexp_split_to_array(lower(regexp_replace(coalesce(metadata->>'topics',''),'[\\[\\]\"]','','g')),'\\s*,\\s*'))) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text($5::jsonb) q WHERE lower(trim(q.value))=ANY(regexp_split_to_array(lower(regexp_replace(coalesce(metadata->>'task_patterns',''),'[\\[\\]\"]','','g')),'\\s*,\\s*')))) ORDER BY id::text LIMIT $7"""
WRAP_LEXICAL = r"""const q=$('Validate SCHEDULE retrieval request').first().json,input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error'),candidates=input.filter(i=>i.json?.candidate_id&&i.json?.page_content!==undefined).map((i,n)=>({candidate_id:String(i.json.candidate_id),page_content:String(i.json.page_content||''),metadata:i.json.metadata||{},rank:Number(i.json.lexical_rank||n+1),score:Number(i.json.lexical_score||0),exact_hit:Boolean(Number(i.json.exact_hit||0))}));return[{json:{branch:'lexical',query:q,candidates,branch_findings:error?[{code:'LEXICAL_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];"""
WRAP_TAG = r"""const q=$('Validate SCHEDULE retrieval request').first().json,input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error'),candidates=input.filter(i=>i.json?.candidate_id&&i.json?.page_content!==undefined).map((i,n)=>({candidate_id:String(i.json.candidate_id),page_content:String(i.json.page_content||''),metadata:i.json.metadata||{},rank:Number(i.json.tag_rank||n+1),score:0}));return[{json:{branch:'tag',query:q,candidates,branch_findings:error?[{code:'TAG_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];"""
WRAP_SEMANTIC = r"""const q=$('Validate SCHEDULE retrieval request').first().json,input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error'),candidates=input.map((i,n)=>{const d=i.json.document||{};return{candidate_id:String(d.metadata?.chunk_id||d.metadata?.ingest_key||''),page_content:String(d.pageContent||''),metadata:d.metadata||{},rank:n+1,score:Number(i.json.score||0)}}).filter(c=>c.candidate_id&&c.page_content);return[{json:{branch:'semantic',query:q,candidates,branch_findings:error?[{code:'SEMANTIC_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];"""


RRF = r"""
const packets=$input.all().map(i=>i.json),q=packets.find(p=>p.query)?.query||{},by=new Map(),k=60,arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const validMeta=m=>m&&String(m.target_base||'')===String(q.filters?.target_base||'')&&String(m.access_scope||'')===String(q.filters?.access_scope||'')&&String(m.knowledge_status||'')==='current'&&(q.filters?.knowledge_types||[]).includes(String(m.knowledge_type||'')),findings=packets.flatMap(p=>arr(p.branch_findings)?p.branch_findings:[]);
for(const p of packets)for(const c of(arr(p.candidates)?p.candidates:[])){if(!validMeta(c.metadata))continue;const id=String(c.candidate_id||''),parent=String(c.metadata.parent_key||`${c.metadata.target_base}:${c.metadata.knowledge_id}:${c.metadata.revision}`);if(!id||!parent)continue;const x=by.get(parent)||{parent_key:parent,representative_chunk_id:id,page_content:String(c.page_content||''),metadata:c.metadata,rrf_score:0,branches:[],chunk_ids:[]};x.rrf_score+=1/(k+Math.max(1,Number(c.rank)||999));if(c.exact_hit)x.rrf_score+=.02;x.branches.push(p.branch);x.chunk_ids.push(id);if(String(c.page_content||'').length>x.page_content.length)x.page_content=String(c.page_content||'');by.set(parent,x)}
const ranked=[...by.values()].sort((a,b)=>b.rrf_score-a.rrf_score||a.parent_key.localeCompare(b.parent_key)).slice(0,Number(q.top_k)||10),tags=v=>arr(v)?v.map(String).map(x=>x.trim().toUpperCase()).filter(Boolean):typeof v==='string'?(()=>{try{return tags(JSON.parse(v))}catch{return v.replace(/[\[\]"]/g,'').split(',').map(x=>x.trim().toUpperCase()).filter(Boolean)}})():[];
const requested=arr(q.exact_keyword_terms)?q.exact_keyword_terms.map(String).map(x=>x.toUpperCase()):[],instructionCoverage=new Set(ranked.filter(r=>r.metadata.knowledge_type==='keyword_instruction').flatMap(r=>tags(r.metadata.keyword_families))),uncovered=requested.filter(v=>!instructionCoverage.has(v));if(!ranked.length)findings.push({code:'NO_AUTHORIZED_EVIDENCE',severity:'error'});if(uncovered.length)findings.push({code:'KEYWORD_INSTRUCTION_COVERAGE_INCOMPLETE',severity:'error',keywords:uncovered});const hard=findings.some(f=>f.severity==='error');
return[{json:{contract:'schedule_retrieval_result',contract_version:'1.0',status:hard?'abstain':'ranked',request_id:q.request_id||null,query:q.query,filters:q.filters,exact_keyword_terms:requested,ranked_parents:hard?[]:ranked.map(r=>({parent_key:r.parent_key,target_base:r.metadata.target_base,knowledge_id:r.metadata.knowledge_id,revision:r.metadata.revision,knowledge_type:r.metadata.knowledge_type,keyword_families:tags(r.metadata.keyword_families),rrf_score:Number(r.rrf_score.toFixed(8)),branches:[...new Set(r.branches)].sort(),chunk_ids:[...new Set(r.chunk_ids)]})),findings,retrieval:{algorithm:'rrf',rrf_k:k,branches:['lexical','semantic','tag'],candidate_count:by.size,returned:hard?0:ranked.length},evidence_ready:false}}];
"""


PREPARE_PARENT_LOOKUP = r"""
const e=$json||{},parents=Array.isArray(e.ranked_parents)?e.ranked_parents:[];
return[{json:{evidence:e,sql_parameters:[String(e.filters?.target_base||''),String(e.filters?.access_scope||''),JSON.stringify(parents.map(p=>({knowledge_id:p.knowledge_id,revision:p.revision}))) ]}}];
"""
PARENT_LOOKUP_SQL = """SELECT target_base,knowledge_id,revision,knowledge_type,status,keywords,topics,task_patterns,title,body_json,content_hash,access_scope,author FROM tnavigator_schedule_knowledge_documents_v1 WHERE target_base=$1 AND access_scope=$2 AND status='active' AND EXISTS(SELECT 1 FROM jsonb_to_recordset($3::jsonb) x(knowledge_id text,revision text) WHERE x.knowledge_id=tnavigator_schedule_knowledge_documents_v1.knowledge_id AND x.revision=tnavigator_schedule_knowledge_documents_v1.revision)"""
HYDRATE_PARENTS = r"""
const prepared=$('Prepare full parent knowledge lookup').first().json,e=prepared.evidence||{},arr=Array.isArray,obj=v=>v&&typeof v==='object'&&!arr(v),parse=v=>{if(obj(v))return v;if(typeof v==='string'){try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}}return{}},rows=$input.all().map(i=>i.json||{}),ranked=arr(e.ranked_parents)?e.ranked_parents:[],by=new Map(rows.filter(r=>r.knowledge_id).map(r=>[`${r.knowledge_id}:${r.revision}`,r])),findings=arr(e.findings)?e.findings.slice():[];
const results=[];for(const rank of ranked){const row=by.get(`${rank.knowledge_id}:${rank.revision}`);if(!row){findings.push({code:'INACTIVE_OR_MISSING_PARENT_SKIPPED',severity:'warning',knowledge_id:rank.knowledge_id,revision:rank.revision});continue}results.push({knowledge_id:row.knowledge_id,revision:row.revision,knowledge_type:row.knowledge_type,title:row.title,keywords:row.keywords,topics:row.topics,task_patterns:row.task_patterns,body:parse(row.body_json),content_hash:row.content_hash,author:row.author,rrf_score:rank.rrf_score,branches:rank.branches})}
const requested=arr(e.exact_keyword_terms)?e.exact_keyword_terms:arr(e.filters?.keyword_families)?e.filters.keyword_families:[],covered=new Set(results.filter(r=>r.knowledge_type==='keyword_instruction').flatMap(r=>arr(r.keywords)?r.keywords:[]).map(String).map(x=>x.toUpperCase())),uncovered=requested.map(String).map(x=>x.toUpperCase()).filter(k=>!covered.has(k));if(uncovered.length)findings.push({code:'KEYWORD_INSTRUCTION_COVERAGE_INCOMPLETE',severity:'error',keywords:uncovered});if(rows.some(r=>r.error||r.message&&r.level==='error'))findings.push({code:'PARENT_KNOWLEDGE_LOOKUP_FAILED',severity:'error'});const hard=findings.some(f=>f.severity==='error');
return[{json:{...e,status:hard?'abstain':'succeeded',results:hard?[]:results,citations:hard?[]:results.map(r=>({knowledge_id:r.knowledge_id,revision:r.revision,knowledge_type:r.knowledge_type,content_hash:r.content_hash,author:r.author,keyword_families:r.keywords,rrf_score:r.rrf_score,branches:r.branches})),findings,evidence_ready:!hard}}];
"""


PREPARE_SCHEMA_LOOKUP = r"""const e=$json||{},hashes=[...new Set((Array.isArray(e.results)?e.results:[]).map(r=>String(r.content_hash||'')).filter(Boolean))];return[{json:{evidence:e,sql_parameters:[String(e.filters?.target_base||''),String(e.filters?.access_scope||''),JSON.stringify(hashes)]}}];"""
CATALOGUE_LOOKUP_SQL = """SELECT catalogue_hash,catalogue_ref,source_hash,access_scope,approved_by,approval_gate_id,schema_catalogue FROM tnavigator_schedule_schema_catalogue_v1 WHERE target_base=$1 AND access_scope=$2 ORDER BY stored_at DESC,catalogue_hash LIMIT 20"""
ATTACH_SCHEMA_CATALOGUE = r"""
const prepared=$('Prepare approved schema catalogue lookup').first().json,e=prepared.evidence||{},obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'',parse=v=>{if(obj(v))return v;if(typeof v==='string'){try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}}return{}},findings=arr(e.findings)?e.findings.slice():[],requested=arr(e.filters?.keyword_families)?e.filters.keyword_families.map(v=>clean(v).toUpperCase()):[],valid=[];
for(const row of $input.all().map(i=>i.json||{})){const c=parse(row.schema_catalogue),p=obj(c.simulator_profile)?c.simulator_profile:{},schemas=arr(c.schemas)?c.schemas:[],covered=new Set(schemas.map(s=>clean(s.keyword).toUpperCase())),okay=c.contract==='schedule_schema_catalogue'&&c.contract_version==='1.0'&&clean(p.simulator).toLowerCase()==='tnavigator'&&clean(p.version)==='22.2'&&/^sha256:[a-f0-9]{64}$/i.test(clean(c.catalogue_hash))&&requested.every(k=>covered.has(k))&&schemas.every(s=>obj(s.semantics)&&arr(s.fields)&&s.fields.length);if(okay)valid.push(c)}
if(!requested.length)findings.push({code:'SCHEMA_KEYWORD_SCOPE_REQUIRED',severity:'error'});if(!valid.length)findings.push({code:'EXPERT_SCHEMA_CATALOGUE_NOT_FOUND',severity:'error',keywords:requested});if(valid.length>1&&new Set(valid.map(c=>clean(c.catalogue_hash))).size>1)findings.push({code:'SCHEMA_CATALOGUE_AMBIGUOUS',severity:'error'});const hard=findings.some(f=>f.severity==='error'),selected=hard?null:valid[0];return[{json:{...e,status:hard?'abstain':'succeeded',results:hard?[]:e.results,citations:hard?[]:e.citations,findings,evidence_ready:!hard,schema_catalogue:selected,retrieval:{...(e.retrieval||{}),full_parent_hydration:true,schema_catalogue_lookup:true,catalogue_hash:selected?.catalogue_hash||null}}}];
"""


def build_ingestion(*, node, note, code, trigger, ifnode, connect, workflow):
    form = node(
        "SCHEDULE manual ingestion form", "n8n-nodes-base.formTrigger", 2.6, (-1200, -80),
        {
            "authentication": "n8nUserAuth",
            "formTitle": "SCHEDULE expert knowledge ingestion",
            "formDescription": "Load one expert keyword instruction or worked example into the allowlisted hybrid-RAG namespace.",
            "formFields": {"values": [
                {"fieldName": "target_base", "fieldLabel": "Target base (schedule_mvp)", "fieldType": "text", "requiredField": True},
                {"fieldName": "knowledge_type", "fieldLabel": "Type: keyword_instruction or worked_example", "fieldType": "text", "requiredField": True},
                {"fieldName": "knowledge_id", "fieldLabel": "Stable knowledge ID", "fieldType": "text", "requiredField": True},
                {"fieldName": "revision", "fieldLabel": "Revision", "fieldType": "text", "requiredField": True},
                {"fieldName": "title", "fieldLabel": "Title", "fieldType": "text", "requiredField": True},
                {"fieldName": "keywords", "fieldLabel": "Keywords, comma-separated", "fieldType": "text", "requiredField": True},
                {"fieldName": "topics", "fieldLabel": "Topics, comma-separated", "fieldType": "text", "requiredField": False},
                {"fieldName": "task_patterns", "fieldLabel": "Task patterns, comma-separated", "fieldType": "text", "requiredField": False},
                {"fieldName": "text", "fieldLabel": "Full self-contained instruction or example", "fieldType": "textarea", "requiredField": True},
                {"fieldName": "author", "fieldLabel": "Hydrodynamic expert", "fieldType": "text", "requiredField": True},
                {"fieldName": "access_scope", "fieldLabel": "Access scope", "fieldType": "text", "requiredField": True},
                {"fieldName": "schema_catalogue_json", "fieldLabel": "Expert machine-readable schema JSON (optional unless Builder renders this keyword)", "fieldType": "textarea", "requiredField": False},
            ]},
            "responseMode": "lastNode",
            "options": {"path": "tnavigator-schedule-knowledge-ingestion", "appendAttribution": False, "buttonLabel": "Validate and ingest", "ignoreBots": True, "includeUserInOutput": True},
        },
    )
    example = {"schedule_knowledge_block": {"contract": "schedule_knowledge_block", "contract_version": "1.0", "target_base": "schedule_mvp", "knowledge_type": "keyword_instruction", "knowledge_id": "wconprod-forecast-v1", "revision": "1", "title": "WCONPROD forecast control", "keywords": ["WCONPROD"], "topics": ["Контроль по скважинам", "Прогноз"], "task_patterns": ["задать лимит по воде"], "status": "active", "author": "department-hydrodynamic-expert", "access_scope": "petroleum-engineering", "text": "Полная самодостаточная инструкция."}}
    ns = [
        note("SCHEDULE ingestion README", (-1240, -650), "## Expert knowledge + schema ingestion — n8n 2.30.8\n\nForm or Execute Sub-workflow input → validation → full parent block in PostgreSQL + searchable chunks in PGVector. `target_base` is an allowlisted metadata namespace, never a dynamic SQL table. Optional expert schema JSON remains the deterministic renderer grammar.\n\nUse one embedding model/dimensions in ingestion and retrieval.", 590, 460),
        trigger("Receive SCHEDULE knowledge document", (-1200, 100), example),
        form,
        code("Normalize approved SCHEDULE knowledge", (-920, -20), INGEST_NORMALIZE),
        ifnode("Knowledge approved for ingestion?", (-680, -20), "={{ $json.status }}", "approved_for_ingestion", "string"),
        node("PGVector — insert approved SCHEDULE knowledge", "@n8n/n8n-nodes-langchain.vectorStorePGVector", 1.3, (-400, -180), {"mode": "insert", "tableName": "tnavigator_schedule_knowledge_v1", "embeddingBatchSize": 64, "options": {"columnNames": {"values": {"idColumnName": "id", "vectorColumnName": "embedding", "contentColumnName": "text", "metadataColumnName": "metadata"}}}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        node("SCHEDULE Default Data Loader", "@n8n/n8n-nodes-langchain.documentDefaultDataLoader", 1.1, (-420, -500), {"dataType": "json", "jsonMode": "expressionData", "jsonData": "={{ $json.text }}", "textSplittingMode": "custom", "options": {"metadata": {"metadataValues": [{"name": k, "value": f"={{ $json.metadata.{k} }}"} for k in ["document_id","document_revision","source_hash","target_base","knowledge_type","knowledge_id","revision","status","access_scope","author","authority_level","approval_status","section","knowledge_status","title","page","heading","keyword_families","topics","task_patterns","parent_key","ingest_key","vendor","simulator","simulator_version"]]}}}),
        node("SCHEDULE Recursive Text Splitter — 1200/180", "@n8n/n8n-nodes-langchain.textSplitterRecursiveCharacterTextSplitter", 1, (-700, -500), {"chunkSize": 1200, "chunkOverlap": 180, "options": {"splitCode": "markdown"}}),
        node("SCHEDULE Embeddings — configure same model in retrieval", "@n8n/n8n-nodes-langchain.embeddingsOpenAi", 1.2, (-160, -500), {"model": "text-embedding-3-small", "options": {"dimensions": 1536, "batchSize": 128, "stripNewLines": True, "timeout": 180, "encodingFormat": "float"}}, credentials=_embedding_credential("REPLACE: SCHEDULE embedding credential")),
        node("Finalize indexes and deduplicate chunks", "n8n-nodes-base.postgres", 2.6, (-120, -180), {"operation": "executeQuery", "query": FINALIZE_INGEST_SQL, "options": {"queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        code("Prepare full parent knowledge persistence", (120, -180), PREPARE_PARENT_PERSIST),
        node("PostgreSQL — upsert full parent knowledge", "n8n-nodes-base.postgres", 2.6, (360, -180), {"operation": "executeQuery", "query": PARENT_UPSERT_SQL, "options": {"queryReplacement": "={{ $json.sql_parameters }}", "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        code("Prepare approved schema catalogue persistence", (600, -180), PREPARE_CATALOGUE_PERSIST),
        node("PostgreSQL — upsert approved schema catalogue", "n8n-nodes-base.postgres", 2.6, (840, -180), {"operation": "executeQuery", "query": CATALOGUE_UPSERT_SQL, "options": {"queryReplacement": "={{ $json.sql_parameters }}", "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        code("Return SCHEDULE ingestion result", (1080, -180), INGEST_RESULT),
        code("Return SCHEDULE ingestion gate", (-400, 140), "return [{json:{contract:'schedule_knowledge_ingest_result',contract_version:'1.0',status:$json.status,findings:$json.findings,vector_write_allowed:false}}];"),
    ]
    c = {}
    connect(c, "SCHEDULE manual ingestion form", "Normalize approved SCHEDULE knowledge")
    connect(c, "Receive SCHEDULE knowledge document", "Normalize approved SCHEDULE knowledge")
    connect(c, "Normalize approved SCHEDULE knowledge", "Knowledge approved for ingestion?")
    connect(c, "Knowledge approved for ingestion?", "PGVector — insert approved SCHEDULE knowledge", idx=0)
    connect(c, "Knowledge approved for ingestion?", "Return SCHEDULE ingestion gate", idx=1)
    connect(c, "SCHEDULE Default Data Loader", "PGVector — insert approved SCHEDULE knowledge", "ai_document", 0, "ai_document")
    connect(c, "SCHEDULE Recursive Text Splitter — 1200/180", "SCHEDULE Default Data Loader", "ai_textSplitter", 0, "ai_textSplitter")
    connect(c, "SCHEDULE Embeddings — configure same model in retrieval", "PGVector — insert approved SCHEDULE knowledge", "ai_embedding", 0, "ai_embedding")
    connect(c, "PGVector — insert approved SCHEDULE knowledge", "Finalize indexes and deduplicate chunks")
    connect(c, "Finalize indexes and deduplicate chunks", "Prepare full parent knowledge persistence")
    connect(c, "Prepare full parent knowledge persistence", "PostgreSQL — upsert full parent knowledge")
    connect(c, "PostgreSQL — upsert full parent knowledge", "Prepare approved schema catalogue persistence")
    connect(c, "Prepare approved schema catalogue persistence", "PostgreSQL — upsert approved schema catalogue")
    connect(c, "PostgreSQL — upsert approved schema catalogue", "Return SCHEDULE ingestion result")
    return workflow("tNavigator SCHEDULE Knowledge Ingestion — approved PGVector runtime", "UI-only expert knowledge ingestion with parent-document persistence and hybrid-RAG metadata.", ns, c, "schedule_knowledge_ingest/v1")


def _postgres(name, pos, query, params):
    return {
        "name": name, "pos": pos, "query": query,
        "parameters": {"operation": "executeQuery", "query": query, "options": {"queryReplacement": params, "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}},
    }


def build_retrieval(*, node, note, code, trigger, ifnode, connect, workflow):
    ex={"schedule_retrieval_request":{"query":"WCONPROD лимит по воде прогноз","filters":{"target_base":"schedule_mvp","access_scope":"petroleum-engineering","knowledge_types":["keyword_instruction","worked_example"],"keyword_families":["WCONPROD"]},"top_k":10}}
    lex=_postgres("PostgreSQL lexical + exact candidates",(-280,-320),LEXICAL_SQL,"={{ $json.sql_parameters }}")
    tag=_postgres("PostgreSQL tag candidates",(-280,40),TAG_SQL,"={{ $json.sql_parameters }}")
    ns=[
        note("SCHEDULE hybrid retrieval README",(-1280,-700),"## Hybrid retrieval + full parent hydration — n8n 2.30.8\n\nPostgreSQL full-text + PGVector semantic + exact tags → deterministic RRF per parent block → full active instruction/example hydration → expert schema lookup. Namespace and access filters are applied in every branch. Missing keyword instruction or schema causes `abstain`; prose never defines renderer field order.",610,450),
        trigger("Receive SCHEDULE retrieval request",(-1240,-80),ex),
        code("Validate SCHEDULE retrieval request",(-1000,-80),RETRIEVAL_NORMALIZE),
        ifnode("Retrieval request authorized?",(-760,-80),"={{ $json.status }}","query_ready","string"),
        code("Prepare lexical retrieval",(-520,-320),PREPARE_LEXICAL),
        node(lex["name"],"n8n-nodes-base.postgres",2.6,lex["pos"],lex["parameters"],credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"),alwaysOutputData=True,onError="continueRegularOutput"),
        code("Wrap lexical candidates",(-20,-320),WRAP_LEXICAL),
        code("Prepare tag retrieval",(-520,40),PREPARE_TAG),
        node(tag["name"],"n8n-nodes-base.postgres",2.6,tag["pos"],tag["parameters"],credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"),alwaysOutputData=True,onError="continueRegularOutput"),
        code("Wrap tag candidates",(-20,40),WRAP_TAG),
        node("PGVector semantic candidates","@n8n/n8n-nodes-langchain.vectorStorePGVector",1.3,(-280,-140),{"mode":"load","prompt":"={{ $json.query }}","topK":"={{ $json.top_k }}","includeDocumentMetadata":True,"tableName":"tnavigator_schedule_knowledge_v1","options":{"distanceStrategy":"cosine","columnNames":{"values":{"idColumnName":"id","vectorColumnName":"embedding","contentColumnName":"text","metadataColumnName":"metadata"}},"metadata":{"metadataValues":[{"name":"target_base","value":"={{ $json.filters.target_base }}"},{"name":"access_scope","value":"={{ $json.filters.access_scope }}"},{"name":"knowledge_status","value":"current"}]}}},credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"),alwaysOutputData=True,onError="continueRegularOutput"),
        node("SCHEDULE Retrieval Embeddings — same model as ingestion","@n8n/n8n-nodes-langchain.embeddingsOpenAi",1.2,(-280,-500),{"model":"text-embedding-3-small","options":{"dimensions":1536,"batchSize":128,"stripNewLines":True,"timeout":180,"encodingFormat":"float"}},credentials=_embedding_credential("REPLACE: SCHEDULE embedding credential")),
        code("Wrap semantic candidates",(-20,-140),WRAP_SEMANTIC),
        node("Collect hybrid candidate branches","n8n-nodes-base.merge",3.2,(220,-140),{"numberInputs":3,"mode":"append"}),
        code("Fuse authorized candidates with deterministic RRF",(480,-140),RRF),
        code("Prepare full parent knowledge lookup",(720,-140),PREPARE_PARENT_LOOKUP),
        node("PostgreSQL full parent knowledge", "n8n-nodes-base.postgres", 2.6, (960,-140), {"operation":"executeQuery","query":PARENT_LOOKUP_SQL,"options":{"queryReplacement":"={{ $json.sql_parameters }}","queryBatching":"single","largeNumbersOutput":"text","replaceEmptyStrings":False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"), alwaysOutputData=True, onError="continueRegularOutput"),
        code("Hydrate full parent knowledge blocks",(1200,-140),HYDRATE_PARENTS),
        code("Prepare approved schema catalogue lookup",(1440,-140),PREPARE_SCHEMA_LOOKUP),
        node("PostgreSQL approved schema catalogue", "n8n-nodes-base.postgres", 2.6, (1680,-140), {"operation":"executeQuery","query":CATALOGUE_LOOKUP_SQL,"options":{"queryReplacement":"={{ $json.sql_parameters }}","queryBatching":"single","largeNumbersOutput":"text","replaceEmptyStrings":False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"), alwaysOutputData=True, onError="continueRegularOutput"),
        code("Attach approved schema catalogue",(1920,-140),ATTACH_SCHEMA_CATALOGUE),
        code("Return retrieval authorization gate",(-520,240),"return[{json:{contract:'schedule_retrieval_result',contract_version:'1.0',status:'needs_input',request_id:$json.request_id||null,query:$json.query||'',filters:$json.filters||{},results:[],citations:[],findings:$json.findings,evidence_ready:false}}];"),
    ]
    c={}
    connect(c,"Receive SCHEDULE retrieval request","Validate SCHEDULE retrieval request")
    connect(c,"Validate SCHEDULE retrieval request","Retrieval request authorized?")
    for target in ["Prepare lexical retrieval","Prepare tag retrieval","PGVector semantic candidates"]: connect(c,"Retrieval request authorized?",target,idx=0)
    connect(c,"Retrieval request authorized?","Return retrieval authorization gate",idx=1)
    connect(c,"Prepare lexical retrieval","PostgreSQL lexical + exact candidates");connect(c,"PostgreSQL lexical + exact candidates","Wrap lexical candidates");connect(c,"Wrap lexical candidates","Collect hybrid candidate branches",target_idx=0)
    connect(c,"Prepare tag retrieval","PostgreSQL tag candidates");connect(c,"PostgreSQL tag candidates","Wrap tag candidates");connect(c,"Wrap tag candidates","Collect hybrid candidate branches",target_idx=1)
    connect(c,"PGVector semantic candidates","Wrap semantic candidates");connect(c,"Wrap semantic candidates","Collect hybrid candidate branches",target_idx=2)
    connect(c,"SCHEDULE Retrieval Embeddings — same model as ingestion","PGVector semantic candidates","ai_embedding",0,"ai_embedding")
    connect(c,"Collect hybrid candidate branches","Fuse authorized candidates with deterministic RRF")
    connect(c,"Fuse authorized candidates with deterministic RRF","Prepare full parent knowledge lookup")
    connect(c,"Prepare full parent knowledge lookup","PostgreSQL full parent knowledge")
    connect(c,"PostgreSQL full parent knowledge","Hydrate full parent knowledge blocks")
    connect(c,"Hydrate full parent knowledge blocks","Prepare approved schema catalogue lookup")
    connect(c,"Prepare approved schema catalogue lookup","PostgreSQL approved schema catalogue")
    connect(c,"PostgreSQL approved schema catalogue","Attach approved schema catalogue")
    return workflow("tNavigator SCHEDULE Hybrid Retrieval — executable RRF runtime", "Lexical, semantic and exact-tag RRF with namespace isolation, full parent hydration and fail-closed expert schema lookup.", ns, c, "schedule_retrieval/v1")
