"""Practical hybrid-RAG workflows for n8n 2.30.8.

One physical PGVector/parent table pair; logical isolation is `target_base`.
Credentials are selected in UI; knowledge itself never enters the workflow export.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parents[1] / "rag"


KEYWORDS = [
    "DATES", "INCLUDE", "GRUPTREE", "WELSPECS", "WELLTRACK", "COMPDATMD",
    "WCONHIST", "WCONPROD", "WCONINJE", "GCONPROD", "GCONINJE", "GUIDERAT", "GSATPROD", "GSATINJE", "WELLSTRE", "WINJGAS", "GINJGAS", "BRANPROP", "NODEPROP", "GNETDP", "NETBALAN",
    "FRACTURE_TEMPLATE", "FRACTURE_SPECS", "FRACTURE_STAGE", "WECON", "WTEST",
    "WELTARG", "WNETDP", "WPIMULT", "WDFAC", "WEFAC", "WELOPEN", "WELDRAW", "WLIST", "WFRACP", "WFRACPL",
    "VFPPROD", "WVFPDP", "ACTIONX", "DELAYACT", "ENDACTIO", "UDQ", "UDT", "APPLYSCRIPT",
]

NAMESPACES_JS = r"""
const NAMESPACES={schedule_mvp:{types:['keyword_instruction','worked_example'],keywordMode:'schedule',requireCoverage:true,requireSchema:true,section:'SCHEDULE'},excel_protocol:{types:['protocol_instruction'],keywordMode:'open',requireCoverage:true,requireSchema:false,section:'EXCEL'},orchestrator_routing:{types:['routing_card'],keywordMode:'open',requireCoverage:false,requireSchema:false,section:'ORCHESTRATOR'},specialist_template:{types:['capability_instruction','worked_example'],keywordMode:'open',requireCoverage:true,requireSchema:false,section:'SPECIALIST'}};
const COVERAGE_TYPES=new Set(['keyword_instruction','protocol_instruction','capability_instruction']);
const ALLOWED_BASES=new Set(Object.keys(NAMESPACES));
""".strip()


def _credential(name: str) -> dict:
    return {"postgres": {"id": "REPLACE_IN_UI", "name": name}}


def _embedding_credential(name: str) -> dict:
    return {"openAiApi": {"id": "REPLACE_IN_UI", "name": name}}


INGEST_NORMALIZE = (NAMESPACES_JS + r"""
// Code v2 defaults to run-once-for-all-items; normalize every incoming block.
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}};
const list=v=>arr(v)?[...new Set(v.map(clean).filter(Boolean))]:typeof v==='string'?(()=>{try{const x=JSON.parse(v);return arr(x)?list(x):v.split(/[,;|]/).map(clean).filter(Boolean)}catch{return v.split(/[,;|]/).map(clean).filter(Boolean)}})():[];
const allowed=new Set(KEYWORDS_PLACEHOLDER);
const hash=s=>{let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return(h>>>0).toString(16).padStart(8,'0')};
const normalizeOne=(wrapper)=>{
const incoming=wrapper.schedule_knowledge_block??wrapper.mas_knowledge_block??wrapper.schedule_knowledge_document??wrapper;
const raw=obj(incoming)?incoming:{};
const m={...parse(raw.metadata_json),...(obj(raw.metadata)?raw.metadata:{})},catalogue=parse(raw.schema_catalogue_json??raw.schema_catalogue),catalogueProvided=Object.keys(catalogue).length>0,findings=[];
const targetBase=clean(raw.target_base||m.target_base||'schedule_mvp'),ns=NAMESPACES[targetBase]||{};
const knowledgeType=clean(raw.knowledge_type||m.knowledge_type).toLowerCase(),allowTypes=new Set(ns.types||[]);
const knowledgeId=clean(raw.knowledge_id||raw.document_id||m.knowledge_id||m.document_id),revision=clean(raw.revision||raw.document_revision||m.revision||m.document_revision||'1'),status=clean(raw.status||raw.knowledge_status||m.status||m.knowledge_status||'active').toLowerCase();
const rawKeywords=list(raw.keywords||raw.keyword_families||m.keywords||m.keyword_families);
const keywords=ns.keywordMode==='schedule'?rawKeywords.map(v=>v.toUpperCase()).filter(v=>allowed.has(v)):rawKeywords.map(v=>v.toUpperCase()).filter(Boolean);
const topics=list(raw.topics||m.topics),taskPatterns=list(raw.task_patterns||m.task_patterns),examples=arr(raw.examples)?raw.examples.filter(obj).slice(0,100):[];
const text=clean(raw.text||raw.document_text),title=clean(raw.title||m.title||knowledgeId),author=clean(raw.author||raw.approved_by||m.author||m.approved_by),accessScope=clean(raw.access_scope||m.access_scope||'petroleum-engineering');
const authority='department_expert',sourceHash=clean(raw.source_hash||m.source_hash).toLowerCase(),page=clean(raw.page||m.page),heading=clean(raw.heading||m.heading||title);
const simulatorFamily=targetBase==='schedule_mvp'?list(raw.simulator_family||m.simulator_family||['E100','E300','tNavigator']):list(raw.simulator_family||m.simulator_family||[]);
const body={contract:'schedule_knowledge_block',contract_version:'1.0',target_base:targetBase,knowledge_type:knowledgeType,knowledge_id:knowledgeId,revision,title,keywords,topics,task_patterns:taskPatterns,simulator_family:simulatorFamily,status,author,text,examples,schema_catalogue:catalogueProvided?catalogue:null};
const searchable=[title,text,keywords.join(' '),topics.join(' '),taskPatterns.join(' '),...examples.flatMap(e=>[clean(e.title),clean(e.task),clean(e.schedule_text),clean(e.explanation)])].filter(Boolean).join('\n\n');
if(!ALLOWED_BASES.has(targetBase))findings.push({code:'TARGET_BASE_NOT_ALLOWLISTED',severity:'error',target_base:targetBase});
if(!allowTypes.has(knowledgeType))findings.push({code:'KNOWLEDGE_TYPE_INVALID',severity:'error'});if(!knowledgeId)findings.push({code:'KNOWLEDGE_ID_REQUIRED',severity:'error'});if(!revision)findings.push({code:'REVISION_REQUIRED',severity:'error'});if(!title)findings.push({code:'TITLE_REQUIRED',severity:'error'});if(!author)findings.push({code:'EXPERT_AUTHOR_REQUIRED',severity:'error'});if(!accessScope)findings.push({code:'ACCESS_SCOPE_REQUIRED',severity:'error'});
if(status!=='active')findings.push({code:'ACTIVE_KNOWLEDGE_REQUIRED',severity:'error'});if(!searchable)findings.push({code:'KNOWLEDGE_TEXT_REQUIRED',severity:'error'});if(searchable.length>2000000)findings.push({code:'KNOWLEDGE_TEXT_TOO_LARGE',severity:'error'});
if(!keywords.length)findings.push({code:ns.keywordMode==='schedule'?'NO_SCHEDULE_KEYWORD_FOUND':'KNOWLEDGE_TAGS_REQUIRED',severity:'error'});
if(knowledgeType==='keyword_instruction'&&!text)findings.push({code:'FULL_KEYWORD_INSTRUCTION_REQUIRED',severity:'error'});
if(knowledgeType==='protocol_instruction'&&!text)findings.push({code:'PROTOCOL_INSTRUCTION_REQUIRED',severity:'error'});
if(knowledgeType==='routing_card'&&!text)findings.push({code:'ROUTING_CARD_TEXT_REQUIRED',severity:'error'});
if(knowledgeType==='capability_instruction'&&!text)findings.push({code:'CAPABILITY_INSTRUCTION_REQUIRED',severity:'error'});
if(knowledgeType==='worked_example'&&!examples.length&&!text)findings.push({code:'WORKED_EXAMPLE_REQUIRED',severity:'error'});
const contentHash=sourceHash||`fnv1a32:${hash(JSON.stringify(body))}`,documentId=knowledgeId,documentRevision=revision;
if(catalogueProvided){const cp=obj(catalogue.simulator_profile)?catalogue.simulator_profile:{},schemas=arr(catalogue.schemas)?catalogue.schemas:[];
 if(catalogue.contract!=='schedule_schema_catalogue'||catalogue.contract_version!=='1.0')findings.push({code:'SCHEMA_CATALOGUE_CONTRACT_INVALID',severity:'error'});if(!/^sha256:[a-f0-9]{64}$/i.test(clean(catalogue.catalogue_hash)))findings.push({code:'SCHEMA_CATALOGUE_HASH_INVALID',severity:'error'});if(!/^sha256:[a-f0-9]{64}$/i.test(clean(catalogue.source_hash)))findings.push({code:'SCHEMA_SOURCE_HASH_INVALID',severity:'error'});if(clean(cp.simulator).toLowerCase()!=='tnavigator'||clean(cp.version)!=='22.2')findings.push({code:'SCHEMA_PROFILE_NOT_APPROVED',severity:'error'});if(!schemas.length)findings.push({code:'SCHEMA_CATALOGUE_EMPTY',severity:'error'});
 for(const entry of schemas){const kw=clean(entry.keyword).toUpperCase(),fields=arr(entry.fields)?entry.fields:[],names=new Set(fields.map(f=>clean(f?.name)).filter(Boolean)),sem=obj(entry.semantics)?entry.semantics:null,fieldOk=f=>clean(f)&&names.has(clean(f));if(!allowed.has(kw)||!clean(entry.schema_id)||!clean(entry.schema_revision)||!fields.length)findings.push({code:'SCHEMA_ENTRY_INVALID',severity:'error',keyword:kw});if(!sem)findings.push({code:'SCHEMA_SEMANTICS_REQUIRED',severity:'error',keyword:kw});else{for(const d of(arr(sem.definitions)?sem.definitions:[]))if(!obj(d)||!clean(d.entity_type)||!fieldOk(d.id_field))findings.push({code:'SEMANTIC_DEFINITION_INVALID',severity:'error',keyword:kw});for(const r of(arr(sem.references)?sem.references:[]))if(!obj(r)||!clean(r.entity_type)||!fieldOk(r.id_field))findings.push({code:'SEMANTIC_REFERENCE_INVALID',severity:'error',keyword:kw});}}
}
const normalizedCatalogue=catalogueProvided?{...catalogue,approved:catalogue.approved!==false,approved_by:clean(catalogue.approved_by||catalogue.author||author),author:clean(catalogue.author||catalogue.approved_by||author),approval_gate_id:clean(catalogue.approval_gate_id||`expert:${knowledgeId}:${revision}`),access_scope:clean(catalogue.access_scope||accessScope)}:null;
const isSchedule=targetBase==='schedule_mvp';
return{json:{contract:'schedule_knowledge_document',contract_version:'1.0',status:findings.length?'needs_input':'approved_for_ingestion',text:searchable,knowledge_block:{...body,schema_catalogue:normalizedCatalogue},metadata:{document_id:documentId,document_revision:documentRevision,source_hash:contentHash,target_base:targetBase,knowledge_type:knowledgeType,knowledge_id:knowledgeId,revision,status:'active',access_scope:accessScope,author,approved_by:author,authority_level:authority,approval_status:'approved',section:ns.section||'KNOWLEDGE',knowledge_status:'current',title,page,heading,keyword_families:keywords,topics,task_patterns:taskPatterns,parent_key:`${targetBase}:${knowledgeId}:${revision}`,ingest_key:`${targetBase}:${knowledgeId}:${revision}:${hash(searchable)}`,vendor:'department',simulator:isSchedule?'tNavigator':'',simulator_version:isSchedule?'22.2':''},schema_catalogue:normalizedCatalogue,catalogue_present:catalogueProvided,findings}};
};
return $input.all().map(item=>normalizeOne(item.json||{}));
""").replace("KEYWORDS_PLACEHOLDER", json.dumps(KEYWORDS))


INGEST_RESULT = r"""
const approved=$('Normalize approved SCHEDULE knowledge').all().map(i=>i.json).filter(x=>x&&x.status==='approved_for_ingestion');
const diff=$('Select new MAS knowledge').first().json||{};
const skipped=Array.isArray(diff.skipped_ids)?diff.skipped_ids:(diff.sync_meta&&Array.isArray(diff.sync_meta.skipped_ids)?diff.sync_meta.skipped_ids:[]);
return[{json:{contract:'schedule_knowledge_ingest_result',contract_version:'1.0',status:approved.length?'ingested':'already_present',inserted:approved.length,skipped:skipped.length,skipped_ids:skipped,knowledge_ids:approved.map(x=>x.metadata?.knowledge_id).filter(Boolean),vector_table:'tnavigator_schedule_knowledge_v1',parent_table:'tnavigator_schedule_knowledge_documents_v1',schema_catalogue_table:'tnavigator_schedule_schema_catalogue_v1',embedding_profile:'configure one identical model/dimensions in ingestion and retrieval',findings:[]}}];
"""


PREPARE_PARENT_PERSIST = r"""
const items=$('Normalize approved SCHEDULE knowledge').all().filter(i=>i.json?.status==='approved_for_ingestion');
return items.map(entry=>{
  const x=entry.json,m=x.metadata||{},b=x.knowledge_block||{};
  return {json:{...x,sql_parameters:[m.target_base,m.knowledge_id,m.revision,m.knowledge_type,'active',JSON.stringify(m.keyword_families||[]),JSON.stringify(m.topics||[]),JSON.stringify(m.task_patterns||[]),m.title,JSON.stringify(b),x.text,m.source_hash,m.access_scope,m.author]}};
});
"""


PARENT_UPSERT_SQL = """WITH superseded AS (UPDATE tnavigator_schedule_knowledge_documents_v1 SET status='superseded',stored_at=now() WHERE target_base=$1 AND knowledge_id=$2 AND revision<>$3 AND status='active' RETURNING 1), inserted AS (INSERT INTO tnavigator_schedule_knowledge_documents_v1
(target_base,knowledge_id,revision,knowledge_type,status,keywords,topics,task_patterns,title,body_json,searchable_text,content_hash,access_scope,author)
VALUES($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10::jsonb,$11,$12,$13,$14)
ON CONFLICT(target_base,knowledge_id,revision) DO UPDATE SET knowledge_type=EXCLUDED.knowledge_type,status=EXCLUDED.status,keywords=EXCLUDED.keywords,topics=EXCLUDED.topics,task_patterns=EXCLUDED.task_patterns,title=EXCLUDED.title,body_json=EXCLUDED.body_json,searchable_text=EXCLUDED.searchable_text,content_hash=EXCLUDED.content_hash,access_scope=EXCLUDED.access_scope,author=EXCLUDED.author,stored_at=now() RETURNING 1), chunks AS (UPDATE tnavigator_schedule_knowledge_v1 t SET metadata=jsonb_set(coalesce(t.metadata,'{}'::jsonb),'{knowledge_status}','"superseded"'::jsonb) WHERE t.metadata->>'target_base'=$1 AND t.metadata->>'knowledge_id'=$2 AND coalesce(t.metadata->>'revision','')<>$3 AND coalesce(t.metadata->>'knowledge_status','')='current' RETURNING 1)
SELECT count(*)::int AS documents_stored FROM inserted"""


PREPARE_CATALOGUE_PERSIST = r"""
const items=$('Normalize approved SCHEDULE knowledge').all().filter(i=>i.json?.catalogue_present===true&&i.json?.schema_catalogue);
if(!items.length)return[{json:{sql_parameters:['','','','','','','','schedule_mvp'],catalogue_present:false}}];
return items.map(entry=>{
  const x=entry.json,c=x.schema_catalogue,m=x.metadata||{};
  return {json:{...x,sql_parameters:[c?JSON.stringify(c):'',c?.catalogue_hash||'',c?.catalogue_ref||`expert://${m.target_base}/${m.knowledge_id}/${m.revision}`,c?.source_hash||m.source_hash,c?.access_scope||m.access_scope,c?.approved_by||m.author,c?.approval_gate_id||`expert:${m.knowledge_id}:${m.revision}`,m.target_base]}};
});
"""


CATALOGUE_UPSERT_SQL = """WITH payload AS (SELECT NULLIF($1,'')::jsonb AS body), inserted AS (INSERT INTO tnavigator_schedule_schema_catalogue_v1
(catalogue_hash,catalogue_ref,source_hash,access_scope,simulator_version,approved_by,approval_gate_id,target_base,schema_catalogue)
SELECT $2,$3,$4,$5,'22.2',$6,$7,$8,body FROM payload WHERE body IS NOT NULL
ON CONFLICT(catalogue_hash) DO UPDATE SET catalogue_ref=EXCLUDED.catalogue_ref,source_hash=EXCLUDED.source_hash,access_scope=EXCLUDED.access_scope,approved_by=EXCLUDED.approved_by,approval_gate_id=EXCLUDED.approval_gate_id,target_base=EXCLUDED.target_base,schema_catalogue=EXCLUDED.schema_catalogue,stored_at=now() RETURNING catalogue_hash)
SELECT count(*)::int AS catalogues_stored FROM inserted"""


ENSURE_PARENT_SQL = """CREATE TABLE IF NOT EXISTS tnavigator_schedule_knowledge_documents_v1 (
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
SELECT count(*)::int AS parent_tables_ready FROM tnavigator_schedule_knowledge_documents_v1"""


LOOKUP_EXISTING_SQL = """SELECT target_base, knowledge_id, revision, content_hash
FROM tnavigator_schedule_knowledge_documents_v1
WHERE status='active'"""


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


RETRIEVAL_NORMALIZE = (NAMESPACES_JS + r"""
const raw=$json.schedule_retrieval_request??$json.mas_retrieval_request??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}},f={...parse(raw.filters_json),...(obj(raw.filters)?raw.filters:{})},findings=[];
const query=clean(raw.query),targetBase=clean(f.target_base||raw.target_base||'schedule_mvp'),accessScope=clean(f.access_scope||raw.access_scope||'petroleum-engineering'),requestId=clean(raw.request_id),ns=NAMESPACES[targetBase]||{};
if(!query)findings.push({code:'QUERY_REQUIRED',severity:'error'});if(query.length>8000)findings.push({code:'QUERY_TOO_LARGE',severity:'error'});if(!ALLOWED_BASES.has(targetBase))findings.push({code:'TARGET_BASE_NOT_ALLOWLISTED',severity:'error'});if(!accessScope)findings.push({code:'ACCESS_SCOPE_REQUIRED',severity:'error'});
const allowed=new Set(KEYWORDS_PLACEHOLDER);
const supplied=Array.isArray(f.keyword_families)?f.keyword_families.map(v=>clean(v)).filter(Boolean):[];
const exactSchedule=[...new Set((query.match(/\b[A-Z][A-Z0-9_]+\b/g)||[]).filter(k=>allowed.has(k)))];
// Coverage must follow the retrieval scope. Do not treat every ALLCAPS token in the
// query as required evidence (e.g. "do not invent WELSPECS" must not force WELSPECS RAG).
const tags=ns.keywordMode==='schedule'?(supplied.length?supplied.map(v=>v.toUpperCase()).filter(v=>allowed.has(v)):exactSchedule):supplied.map(v=>v.toUpperCase());
const exact=tags.slice();
const allowTypes=new Set(ns.types||[]);
const types=Array.isArray(f.knowledge_types)?[...new Set(f.knowledge_types.map(v=>clean(v).toLowerCase()).filter(v=>allowTypes.has(v)))]:[...allowTypes];
const topics=Array.isArray(f.topics)?[...new Set(f.topics.map(clean).filter(Boolean))]:[],patterns=Array.isArray(f.task_patterns)?[...new Set(f.task_patterns.map(clean).filter(Boolean))]:[];
return[{json:{contract:'schedule_retrieval_query',contract_version:'1.0',status:findings.length?'needs_input':'query_ready',query,request_id:requestId||null,top_k:Math.min(80,Math.max(Math.trunc(Number(raw.top_k)||10),tags.length*4,20)),filters:{target_base:targetBase,access_scope:accessScope,knowledge_types:types,keyword_families:tags,topics,task_patterns:patterns,simulator_version:targetBase==='schedule_mvp'?'22.2':'',section:ns.section||'KNOWLEDGE',knowledge_status:'current',require_coverage:ns.requireCoverage===true,require_schema:ns.requireSchema===true},exact_keyword_terms:exact,findings}}];
""").replace("KEYWORDS_PLACEHOLDER", json.dumps(KEYWORDS))


PREPARE_LEXICAL = r"""const x=$json;const tags=Array.isArray(x.filters?.keyword_families)?x.filters.keyword_families:[];const branchLimit=Math.min(120,Math.max(Number(x.top_k)||10,tags.length*4,24));return[{json:{...x,branch:'lexical',sql_parameters:[x.query,x.filters.target_base,x.filters.access_scope,branchLimit,JSON.stringify(x.filters.keyword_families||[]),JSON.stringify(x.filters.knowledge_types||[])]}}];"""
LEXICAL_SQL = """WITH authorized AS (SELECT id,text,metadata FROM tnavigator_schedule_knowledge_v1 WHERE metadata->>'target_base'=$2 AND metadata->>'access_scope'=$3 AND metadata->>'knowledge_status'='current' AND metadata->>'knowledge_type' IN (SELECT jsonb_array_elements_text($6::jsonb))), ranked AS (SELECT id::text candidate_id,text page_content,metadata,CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements_text($5::jsonb) q WHERE upper(trim(q.value))=ANY(regexp_split_to_array(upper(regexp_replace(coalesce(metadata->>'keyword_families',''),'[\\[\\]\"]','','g')),'\\s*,\\s*'))) THEN 1 ELSE 0 END exact_hit,ts_rank_cd(to_tsvector('simple',coalesce(text,'')),websearch_to_tsquery('simple',$1)) lexical_score FROM authorized WHERE to_tsvector('simple',coalesce(text,''))@@websearch_to_tsquery('simple',$1) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text($5::jsonb) q WHERE upper(trim(q.value))=ANY(regexp_split_to_array(upper(regexp_replace(coalesce(metadata->>'keyword_families',''),'[\\[\\]\"]','','g')),'\\s*,\\s*')))) SELECT candidate_id,page_content,metadata,exact_hit,lexical_score,row_number() OVER(ORDER BY exact_hit DESC,lexical_score DESC,candidate_id) lexical_rank FROM ranked ORDER BY lexical_rank LIMIT $4"""
PREPARE_TAG = r"""const x=$('Validate SCHEDULE retrieval request').first().json;const tags=Array.isArray(x.filters?.keyword_families)?x.filters.keyword_families:[];const branchLimit=Math.min(120,Math.max(Number(x.top_k)||10,tags.length*4,24));return[{json:{...x,branch:'tag',sql_parameters:[x.filters.target_base,x.filters.access_scope,JSON.stringify(x.filters.keyword_families||[]),JSON.stringify(x.filters.topics||[]),JSON.stringify(x.filters.task_patterns||[]),JSON.stringify(x.filters.knowledge_types||[]),branchLimit]}}];"""
TAG_SQL = """WITH matched AS (SELECT id,text,metadata,coalesce(metadata->>'knowledge_id','') AS kid FROM tnavigator_schedule_knowledge_v1 WHERE metadata->>'target_base'=$1 AND metadata->>'access_scope'=$2 AND metadata->>'knowledge_status'='current' AND metadata->>'knowledge_type' IN (SELECT jsonb_array_elements_text($6::jsonb)) AND (($3::jsonb<>'[]'::jsonb AND EXISTS(SELECT 1 FROM jsonb_array_elements_text($3::jsonb) q WHERE upper(trim(q.value))=ANY(regexp_split_to_array(upper(regexp_replace(coalesce(metadata->>'keyword_families',''),'[\\[\\]\"]','','g')),'\\s*,\\s*')))) OR ($4::jsonb<>'[]'::jsonb AND EXISTS(SELECT 1 FROM jsonb_array_elements_text($4::jsonb) q WHERE lower(trim(q.value))=ANY(regexp_split_to_array(lower(regexp_replace(coalesce(metadata->>'topics',''),'[\\[\\]\"]','','g')),'\\s*,\\s*')))) OR ($5::jsonb<>'[]'::jsonb AND EXISTS(SELECT 1 FROM jsonb_array_elements_text($5::jsonb) q WHERE lower(trim(q.value))=ANY(regexp_split_to_array(lower(regexp_replace(coalesce(metadata->>'task_patterns',''),'[\\[\\]\"]','','g')),'\\s*,\\s*')))))), per_kid AS (SELECT id::text candidate_id,text page_content,metadata,row_number() OVER(PARTITION BY kid ORDER BY id::text) AS per_kid_rn FROM matched) SELECT candidate_id,page_content,metadata,row_number() OVER(ORDER BY per_kid_rn,candidate_id) AS tag_rank FROM per_kid WHERE per_kid_rn<=3 ORDER BY tag_rank LIMIT $7"""
WRAP_LEXICAL = r"""const q=$('Validate SCHEDULE retrieval request').first().json,input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error'),candidates=input.filter(i=>i.json?.candidate_id&&i.json?.page_content!==undefined).map((i,n)=>({candidate_id:String(i.json.candidate_id),page_content:String(i.json.page_content||''),metadata:i.json.metadata||{},rank:Number(i.json.lexical_rank||n+1),score:Number(i.json.lexical_score||0),exact_hit:Boolean(Number(i.json.exact_hit||0))}));return[{json:{branch:'lexical',query:q,candidates,branch_findings:error?[{code:'LEXICAL_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];"""
WRAP_TAG = r"""const q=$('Validate SCHEDULE retrieval request').first().json,input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error'),candidates=input.filter(i=>i.json?.candidate_id&&i.json?.page_content!==undefined).map((i,n)=>({candidate_id:String(i.json.candidate_id),page_content:String(i.json.page_content||''),metadata:i.json.metadata||{},rank:Number(i.json.tag_rank||n+1),score:0}));return[{json:{branch:'tag',query:q,candidates,branch_findings:error?[{code:'TAG_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];"""
WRAP_SEMANTIC = r"""const q=$('Validate SCHEDULE retrieval request').first().json,input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error'),candidates=input.map((i,n)=>{const d=i.json.document||{};return{candidate_id:String(d.metadata?.chunk_id||d.metadata?.ingest_key||''),page_content:String(d.pageContent||''),metadata:d.metadata||{},rank:n+1,score:Number(i.json.score||0)}}).filter(c=>c.candidate_id&&c.page_content);return[{json:{branch:'semantic',query:q,candidates,branch_findings:error?[{code:'SEMANTIC_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];"""


RRF = NAMESPACES_JS + r"""
const packets=$input.all().map(i=>i.json),q=packets.find(p=>p.query)?.query||{},by=new Map(),k=60,arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const validMeta=m=>m&&String(m.target_base||'')===String(q.filters?.target_base||'')&&String(m.access_scope||'')===String(q.filters?.access_scope||'')&&String(m.knowledge_status||'')==='current'&&(q.filters?.knowledge_types||[]).includes(String(m.knowledge_type||'')),findings=packets.flatMap(p=>arr(p.branch_findings)?p.branch_findings:[]);
for(const p of packets)for(const c of(arr(p.candidates)?p.candidates:[])){if(!validMeta(c.metadata))continue;const id=String(c.candidate_id||''),parent=String(c.metadata.parent_key||`${c.metadata.target_base}:${c.metadata.knowledge_id}:${c.metadata.revision}`);if(!id||!parent)continue;const x=by.get(parent)||{parent_key:parent,representative_chunk_id:id,page_content:String(c.page_content||''),metadata:c.metadata,rrf_score:0,branches:[],chunk_ids:[]};x.rrf_score+=1/(k+Math.max(1,Number(c.rank)||999));if(c.exact_hit)x.rrf_score+=.02;x.branches.push(p.branch);x.chunk_ids.push(id);if(String(c.page_content||'').length>x.page_content.length)x.page_content=String(c.page_content||'');by.set(parent,x)}
const requested=arr(q.exact_keyword_terms)?q.exact_keyword_terms.map(String).map(x=>x.toUpperCase()):[],keep=Math.max(Number(q.top_k)||10,requested.length||0);
const ranked=[...by.values()].sort((a,b)=>b.rrf_score-a.rrf_score||a.parent_key.localeCompare(b.parent_key)).slice(0,keep),tags=v=>arr(v)?v.map(String).map(x=>x.trim().toUpperCase()).filter(Boolean):typeof v==='string'?(()=>{try{return tags(JSON.parse(v))}catch{return v.replace(/[\[\]"]/g,'').split(',').map(x=>x.trim().toUpperCase()).filter(Boolean)}})():[];
const instructionCoverage=new Set(ranked.filter(r=>COVERAGE_TYPES.has(String(r.metadata.knowledge_type||''))).flatMap(r=>tags(r.metadata.keyword_families))),uncovered=(q.filters?.require_coverage===true)?requested.filter(v=>!instructionCoverage.has(v)):[];if(!ranked.length)findings.push({code:'NO_AUTHORIZED_EVIDENCE',severity:'error'});if(uncovered.length)findings.push({code:'KEYWORD_INSTRUCTION_COVERAGE_INCOMPLETE',severity:'error',keywords:uncovered});const hard=findings.some(f=>f.severity==='error');
return[{json:{contract:'schedule_retrieval_result',contract_version:'1.0',status:hard?'abstain':'ranked',request_id:q.request_id||null,query:q.query,filters:q.filters,exact_keyword_terms:requested,ranked_parents:hard?[]:ranked.map(r=>({parent_key:r.parent_key,target_base:r.metadata.target_base,knowledge_id:r.metadata.knowledge_id,revision:r.metadata.revision,knowledge_type:r.metadata.knowledge_type,keyword_families:tags(r.metadata.keyword_families),rrf_score:Number(r.rrf_score.toFixed(8)),branches:[...new Set(r.branches)].sort(),chunk_ids:[...new Set(r.chunk_ids)]})),findings,retrieval:{algorithm:'rrf',rrf_k:k,branches:['lexical','semantic','tag'],candidate_count:by.size,returned:hard?0:ranked.length},evidence_ready:false}}];
"""


PREPARE_PARENT_LOOKUP = r"""
const e=$json||{},parents=Array.isArray(e.ranked_parents)?e.ranked_parents:[];
return[{json:{evidence:e,sql_parameters:[String(e.filters?.target_base||''),String(e.filters?.access_scope||''),JSON.stringify(parents.map(p=>({knowledge_id:p.knowledge_id,revision:p.revision}))) ]}}];
"""
PARENT_LOOKUP_SQL = """SELECT target_base,knowledge_id,revision,knowledge_type,status,keywords,topics,task_patterns,title,body_json,content_hash,access_scope,author FROM tnavigator_schedule_knowledge_documents_v1 WHERE target_base=$1 AND access_scope=$2 AND status='active' AND EXISTS(SELECT 1 FROM jsonb_to_recordset($3::jsonb) x(knowledge_id text,revision text) WHERE x.knowledge_id=tnavigator_schedule_knowledge_documents_v1.knowledge_id AND x.revision=tnavigator_schedule_knowledge_documents_v1.revision)"""
HYDRATE_PARENTS = NAMESPACES_JS + r"""
const prepared=$('Prepare full parent knowledge lookup').first().json,e=prepared.evidence||{},arr=Array.isArray,obj=v=>v&&typeof v==='object'&&!arr(v),parse=v=>{if(obj(v))return v;if(typeof v==='string'){try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}}return{}},rows=$input.all().map(i=>i.json||{}),ranked=arr(e.ranked_parents)?e.ranked_parents:[],by=new Map(rows.filter(r=>r.knowledge_id).map(r=>[`${r.knowledge_id}:${r.revision}`,r])),findings=arr(e.findings)?e.findings.slice():[];
const results=[];for(const rank of ranked){const row=by.get(`${rank.knowledge_id}:${rank.revision}`);if(!row){findings.push({code:'INACTIVE_OR_MISSING_PARENT_SKIPPED',severity:'warning',knowledge_id:rank.knowledge_id,revision:rank.revision});continue}results.push({knowledge_id:row.knowledge_id,revision:row.revision,knowledge_type:row.knowledge_type,title:row.title,keywords:row.keywords,topics:row.topics,task_patterns:row.task_patterns,body:parse(row.body_json),content_hash:row.content_hash,author:row.author,rrf_score:rank.rrf_score,branches:rank.branches})}
const requested=arr(e.exact_keyword_terms)?e.exact_keyword_terms:arr(e.filters?.keyword_families)?e.filters.keyword_families:[],covered=new Set(results.filter(r=>COVERAGE_TYPES.has(String(r.knowledge_type||''))).flatMap(r=>arr(r.keywords)?r.keywords:[]).map(String).map(x=>x.toUpperCase())),uncovered=(e.filters?.require_coverage===true)?requested.map(String).map(x=>x.toUpperCase()).filter(k=>!covered.has(k)):[];if(uncovered.length)findings.push({code:'KEYWORD_INSTRUCTION_COVERAGE_INCOMPLETE',severity:'error',keywords:uncovered});if(rows.some(r=>r.error||r.message&&r.level==='error'))findings.push({code:'PARENT_KNOWLEDGE_LOOKUP_FAILED',severity:'error'});const hard=findings.some(f=>f.severity==='error');
return[{json:{...e,status:hard?'abstain':'succeeded',results:hard?[]:results,citations:hard?[]:results.map(r=>({knowledge_id:r.knowledge_id,revision:r.revision,knowledge_type:r.knowledge_type,content_hash:r.content_hash,author:r.author,keyword_families:r.keywords,rrf_score:r.rrf_score,branches:r.branches})),findings,evidence_ready:!hard}}];
"""


PREPARE_SCHEMA_LOOKUP = r"""const e=$json||{},requested=[...new Set((Array.isArray(e.filters?.keyword_families)?e.filters.keyword_families:[]).map(v=>String(v||'').trim().toUpperCase()).filter(Boolean))];return[{json:{evidence:e,sql_parameters:[String(e.filters?.target_base||''),String(e.filters?.access_scope||''),JSON.stringify(requested)]}}];"""
CATALOGUE_LOOKUP_SQL = """SELECT catalogue_hash,catalogue_ref,source_hash,access_scope,approved_by,approval_gate_id,schema_catalogue FROM tnavigator_schedule_schema_catalogue_v1 WHERE target_base=$1 AND access_scope=$2 AND ($3::jsonb='[]'::jsonb OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(schema_catalogue->'schemas','[]'::jsonb)) s JOIN jsonb_array_elements_text($3::jsonb) q ON upper(trim(coalesce(s->>'keyword','')))=upper(trim(q.value)))) ORDER BY stored_at DESC,catalogue_hash LIMIT 100"""
ATTACH_SCHEMA_CATALOGUE = r"""
const prepared=$('Prepare approved schema catalogue lookup').first().json,e=prepared.evidence||{},obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'',parse=v=>{if(obj(v))return v;if(typeof v==='string'){try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}}return{}},findings=arr(e.findings)?e.findings.slice():[],requested=arr(e.filters?.keyword_families)?e.filters.keyword_families.map(v=>clean(v).toUpperCase()).filter(Boolean):[];
if(e.filters?.require_schema!==true){const hard=findings.some(f=>f.severity==='error')||e.status==='abstain';return[{json:{...e,status:hard?'abstain':(e.status||'succeeded'),results:hard?[]:e.results,citations:hard?[]:e.citations,findings,evidence_ready:!hard&&e.evidence_ready!==false,schema_catalogue:null,retrieval:{...(e.retrieval||{}),full_parent_hydration:true,schema_catalogue_lookup:false,catalogue_hash:null}}}];}
const shaRotr=(x,n)=>((x>>>n)|(x<<(32-n)))>>>0,shaUtf8=s=>{const out=[];for(let i=0;i<String(s).length;i++){let c=String(s).charCodeAt(i);if(c<0x80)out.push(c);else if(c<0x800)out.push(0xc0|(c>>6),0x80|(c&63));else if(c>=0xd800&&c<=0xdbff){const u=0x10000+((c&1023)<<10)|(String(s).charCodeAt(++i)&1023);out.push(0xf0|(u>>18),0x80|((u>>12)&63),0x80|((u>>6)&63),0x80|(u&63));}else out.push(0xe0|(c>>12),0x80|((c>>6)&63),0x80|(c&63));}return out;};
const SHA_K=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc0a7f,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
const sha256=s=>{const b=shaUtf8(s),bit=b.length*8,b2=b.slice();b2.push(0x80);while((b2.length%64)!==56)b2.push(0);for(let i=7;i>=0;i--)b2.push((bit/Math.pow(2,i*8))&255);let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;for(let off=0;off<b2.length;off+=64){const w=new Array(64);for(let i=0;i<16;i++){const p=off+i*4;w[i]=((b2[p]<<24)|(b2[p+1]<<16)|(b2[p+2]<<8)|b2[p+3])>>>0}for(let i=16;i<64;i++){const a=w[i-15],c=w[i-2],s0=(shaRotr(a,7)^shaRotr(a,18)^(a>>>3))>>>0,s1=(shaRotr(c,17)^shaRotr(c,19)^(c>>>10))>>>0;w[i]=(w[i-16]+s0+w[i-7]+s1)>>>0}let a=h0,bv=h1,c=h2,d=h3,e=h4,f=h5,g=h6,hh=h7;for(let i=0;i<64;i++){const S1=(shaRotr(e,6)^shaRotr(e,11)^shaRotr(e,25))>>>0,ch=((e&f)^((~e)&g))>>>0,t1=(hh+S1+ch+SHA_K[i]+w[i])>>>0,S0=(shaRotr(a,2)^shaRotr(a,13)^shaRotr(a,22))>>>0,maj=((a&bv)^(a&c)^(bv&c))>>>0,t2=(S0+maj)>>>0;hh=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=bv;bv=a;a=(t1+t2)>>>0}h0=(h0+a)>>>0;h1=(h1+bv)>>>0;h2=(h2+c)>>>0;h3=(h3+d)>>>0;h4=(h4+e)>>>0;h5=(h5+f)>>>0;h6=(h6+g)>>>0;h7=(h7+hh)>>>0}return[h0,h1,h2,h3,h4,h5,h6,h7].map(x=>x.toString(16).padStart(8,'0')).join('')};
const mergeHash=vals=>{const uniq=[...new Set(vals.map(clean).filter(Boolean))].sort();if(!uniq.length)return null;if(uniq.length===1)return uniq[0].toLowerCase();return `sha256:${sha256(uniq.join('|'))}`;};
const byKeyword=new Map(),sourceHashes=[],catalogueHashes=[],approvers=[],gateIds=[];
for(const row of $input.all().map(i=>i.json||{})){
  const c=parse(row.schema_catalogue),p=obj(c.simulator_profile)?c.simulator_profile:{},schemas=arr(c.schemas)?c.schemas:[];
  const baseOk=c.contract==='schedule_schema_catalogue'&&c.contract_version==='1.0'&&clean(p.simulator).toLowerCase()==='tnavigator'&&clean(p.version)==='22.2'&&/^sha256:[a-f0-9]{64}$/i.test(clean(c.catalogue_hash))&&/^sha256:[a-f0-9]{64}$/i.test(clean(c.source_hash));
  if(!baseOk||!schemas.length)continue;
  const approver=clean(row.approved_by||c.approved_by||c.author);
  const gate=clean(row.approval_gate_id||c.approval_gate_id||'schedule-schema-catalogue-approved');
  for(const entry of schemas){
    const kw=clean(entry.keyword).toUpperCase();
    if(!requested.includes(kw))continue;
    const fields=arr(entry.fields)?entry.fields:[],sem=obj(entry.semantics)?entry.semantics:null;
    if(!clean(entry.schema_id)||!clean(entry.schema_revision)||!fields.length||!sem)continue;
    const variant=clean(entry.variant)||'default';
    const list=byKeyword.get(kw)||[];
    if(list.some(e=> (clean(e.variant)||'default')===variant))continue;
    list.push(entry);byKeyword.set(kw,list);catalogueHashes.push(clean(c.catalogue_hash));sourceHashes.push(clean(c.source_hash));if(approver)approvers.push(approver);if(gate)gateIds.push(gate);
  }
}
const missing=requested.filter(k=>!byKeyword.has(k));
if(!requested.length)findings.push({code:'SCHEMA_KEYWORD_SCOPE_REQUIRED',severity:'error'});
if(missing.length)findings.push({code:'EXPERT_SCHEMA_CATALOGUE_NOT_FOUND',severity:'error',keywords:missing});
const hard=findings.some(f=>f.severity==='error');
const selected=hard?null:{contract:'schedule_schema_catalogue',contract_version:'1.0',catalogue_ref:`catalogue://tnavigator/22.2/merged/${requested.slice().sort().join('+')||'empty'}`,simulator_profile:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2'},catalogue_hash:mergeHash(catalogueHashes),source_hash:mergeHash(sourceHashes),schemas:requested.flatMap(k=>byKeyword.get(k)||[]),approved:true,approved_by:approvers[0]||'department-hydrodynamic-expert',approval_gate_id:gateIds[0]||'schedule-schema-catalogue-approved',access_scope:clean(e.filters?.access_scope||'petroleum-engineering')};
return[{json:{...e,status:hard?'abstain':'succeeded',results:hard?[]:e.results,citations:hard?[]:e.citations,findings,evidence_ready:!hard,schema_catalogue:selected,retrieval:{...(e.retrieval||{}),full_parent_hydration:true,schema_catalogue_lookup:true,catalogue_hash:selected?.catalogue_hash||null,merged_catalogue_hashes:catalogueHashes}}}];
"""


PORTABLE_BLOCK_FIELDS = (
    "contract", "contract_version", "target_base", "knowledge_type", "knowledge_id",
    "revision", "title", "keywords", "topics", "task_patterns", "simulator_family",
    "status", "author", "access_scope", "text", "examples", "schema_catalogue",
)


def ingestible_blocks_from_payload(payload: object) -> list[dict]:
    if isinstance(payload, list):
        docs = payload
    elif isinstance(payload, dict):
        docs = payload.get("documents") or payload.get("schedule_knowledge_blocks")
        if not isinstance(docs, list):
            docs = [payload]
    else:
        return []
    blocks = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if doc.get("role") == "injection_template" or doc.get("do_not_ingest") is True:
            continue
        raw = doc.get("schedule_knowledge_block") if isinstance(doc.get("schedule_knowledge_block"), dict) else doc
        if not isinstance(raw, dict) or raw.get("contract") != "schedule_knowledge_block":
            continue
        if raw.get("role") == "injection_template" or raw.get("do_not_ingest") is True:
            continue
        block = {key: raw[key] for key in PORTABLE_BLOCK_FIELDS if key in raw}
        if block.get("knowledge_id") and block.get("text"):
            block["target_base"] = block.get("target_base") or "schedule_mvp"
            block["revision"] = str(block.get("revision") or "1")
            blocks.append(block)
    by: dict[tuple[str, str, str], dict] = {}
    for block in blocks:
        by[(block["target_base"], block["knowledge_id"], block["revision"])] = block
    return list(by.values())


def packaged_knowledge_blocks() -> list[dict]:
    payload = json.loads((RAG_DIR / "excel-agent-operating-guide.documents.json").read_text(encoding="utf-8"))
    return ingestible_blocks_from_payload(payload)


INGEST_WEBHOOK_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "omegalul/schedule-foundation/mas-knowledge-ingest-webhook"))


COLLECT_KNOWLEDGE_JS = r"""
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);const arr=Array.isArray;const clean=v=>typeof v==='string'?v.trim():'';
const skipDoc=d=>!d||d.role==='injection_template'||d.do_not_ingest===true;
const asBlock=v=>{if(!obj(v))return null;if(obj(v.schedule_knowledge_block))return v.schedule_knowledge_block;if(v.contract==='schedule_knowledge_block'||clean(v.knowledge_id))return v;return null;};
const parseJson=v=>{if(obj(v)||arr(v))return v;if(typeof v!=='string'||!v.trim())return null;try{let x=JSON.parse(v.replace(/^\uFEFF/,''));if(typeof x==='string'){try{x=JSON.parse(x)}catch{}}return (obj(x)||arr(x))?x:null;}catch{return null;}};
const fromPayload=p=>{if(!p)return[];let docs;if(arr(p.documents))docs=p.documents;else if(arr(p.schedule_knowledge_blocks))docs=p.schedule_knowledge_blocks;else if(arr(p))docs=p;else if(obj(p)&&(obj(p.schedule_knowledge_block)||p.contract==='schedule_knowledge_block'||clean(p.knowledge_id)))docs=[p];else docs=[];return docs.filter(d=>!skipDoc(d)).map(asBlock).filter(b=>b&&!skipDoc(b)&&clean(b.knowledge_id)&&clean(b.text||b.document_text));};
const canon=b=>{const target_base=clean(b.target_base||(obj(b.metadata)&&b.metadata.target_base)||'schedule_mvp');const revision=clean(b.revision||b.document_revision||(obj(b.metadata)&&b.metadata.revision)||'1');return {...b,target_base,revision};};
const dedupe=list=>{const by=new Map();for(const b of list){const x=canon(b);by.set(`${x.target_base}|${x.knowledge_id}|${x.revision}`,x);}return [...by.values()];};
const raw0=$json||{};
const inner=obj(raw0.body)||arr(raw0.body)||typeof raw0.body==='string'?raw0.body:raw0;
const raw=typeof inner==='string'?(parseJson(inner)||{}):inner;
const corpusRaw=clean((obj(raw)?raw.corpus_json:'')||raw0.corpus_json);
const pasted=corpusRaw?parseJson(corpusRaw):null;
const corpusInvalid=Boolean(corpusRaw)&&pasted==null;
if(corpusInvalid)return[{json:{ingest_error:'CORPUS_JSON_INVALID',collect_error:'CORPUS_JSON_INVALID'}}];
const explicit=pasted!=null||arr(raw.documents)||arr(raw.schedule_knowledge_blocks)||arr(raw0.documents)||arr(raw0.schedule_knowledge_blocks)||arr(raw);
let blocks=fromPayload(pasted||raw);
if(!blocks.length)blocks=fromPayload(raw0);
if(!blocks.length&&!explicit){const one=asBlock(raw);if(one&&!skipDoc(one)&&clean(one.knowledge_id)&&clean(one.text||one.document_text))blocks=[one];}
blocks=dedupe(blocks);
if(!blocks.length)return[{json:{collect_empty:true,collect_error:'CORPUS_EMPTY',ingest_error:'CORPUS_EMPTY'}}];
return blocks.map(b=>({json:canon(b)}));
""".strip()


SHAPE_INGEST_RESPONSE_JS = r"""
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const x=$json||{};
let diff={};
try{diff=$('Select new MAS knowledge').first().json||{};}catch(_){diff={};}
if(!obj(diff))diff={};
const findings=Array.isArray(x.findings)?x.findings:(Array.isArray(diff.findings)?diff.findings:[]);
const skippedList=Array.isArray(x.skipped_existing)?x.skipped_existing:(Array.isArray(diff.skipped_ids)?diff.skipped_ids:[]);
const ingestAction=String(x.ingest_action||diff.ingest_action||'');
const status=String(x.status||diff.status||'unknown');
const gateBlocked=x.vector_write_allowed===false||status==='needs_input'||['collect_failed','lookup_failed'].includes(ingestAction);
const added=gateBlocked?0:Math.max(0,Number(x.inserted??x.added??diff.inserted)||0);
const skipped=Math.max(0,Number(x.skipped??diff.skipped)||skippedList.length||0);
const totalSent=Math.max(added+skipped,Number(x.total_sent)||0);
const totalInRag=Math.max(0,Number(x.distinct_documents??x.total_in_rag)||0);
const hasError=findings.some(f=>f&&f.severity==='error')||['collect_failed','lookup_failed','needs_input'].includes(ingestAction)||status==='needs_input';
const ok=!hasError;
const missing=Array.isArray(x.missing_document_ids)?x.missing_document_ids.filter(Boolean):[];
let message=`Добавлено ${added}, пропущено (уже есть) ${skipped}, всего в RAG ${totalInRag} карточек.`;
if(!ok){
  const code=(findings[0]&&findings[0].code)||status||ingestAction||'INGEST_FAILED';
  message=`Ошибка ingest (${code}). Добавлено ${added}, пропущено ${skipped}, в RAG ${totalInRag}.`;
}else if(status==='rag_inventory_incomplete'&&missing.length){
  message+=` Не найдены в RAG: ${missing.slice(0,8).join(', ')}${missing.length>8?'…':''}.`;
}
return[{json:{ok,added,skipped,total_sent:totalSent,total_in_rag:totalInRag,status,message,findings}}];
""".strip()


SELECT_NEW_KNOWLEDGE_JS = r"""
const clean=v=>typeof v==='string'?v.trim():'';
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const keyOf=row=>{
  const target=clean(row.target_base||(obj(row.metadata)&&row.metadata.target_base)||'schedule_mvp');
  const id=clean(row.knowledge_id);
  const rev=clean(row.revision||row.document_revision||'1');
  return `${target}|${id}|${rev}`;
};
const isErr=row=>Boolean(row.error)||row.success===false||(clean(row.level)==='error'&&clean(row.message));
const lookupRows=$input.all().map(i=>i.json||{});
const collected=$('Collect MAS knowledge blocks').all().map(i=>i.json||{});
const collectError=collected.find(j=>j.ingest_error||j.collect_error);
if(collectError){
  return [{json:{ingest_action:'collect_failed',status:'needs_input',inserted:0,skipped:0,skipped_ids:[],findings:[{code:collectError.ingest_error||collectError.collect_error,severity:'error'}]}}];
}
if(lookupRows.some(isErr)){
  return [{json:{ingest_action:'lookup_failed',status:'needs_input',inserted:0,skipped:0,skipped_ids:[],findings:[{code:'EXISTING_KNOWLEDGE_LOOKUP_FAILED',severity:'error'}]}}];
}
const existing=new Set();
for(const row of lookupRows){
  if(clean(row.knowledge_id))existing.add(keyOf(row));
}
const candidates=collected.filter(j=>j&&clean(j.knowledge_id));
const fresh=[],skipped=[],seen=new Set();
for(const block of candidates){
  const k=keyOf(block);
  if(seen.has(k)||existing.has(k)){
    skipped.push({target_base:block.target_base||'schedule_mvp',knowledge_id:block.knowledge_id,revision:block.revision||'1'});
    continue;
  }
  seen.add(k);
  fresh.push(block);
}
if(!fresh.length){
  return [{json:{ingest_action:'skip_all',status:'already_present',inserted:0,skipped:skipped.length,skipped_ids:skipped}}];
}
return fresh.map(block=>({json:{ingest_action:'insert',schedule_knowledge_block:block,inserted:fresh.length,skipped:skipped.length,skipped_ids:skipped}}));
"""


def _inventory_prepare_js(expected_ids: list[str]) -> str:
    expected = ",\n  ".join(json.dumps(item) for item in expected_ids)
    return f"""const expected = [
  {expected},
];
const table = 'tnavigator_schedule_knowledge_v1';
const query = [
  'WITH inv AS (',
  '  SELECT',
  "    coalesce(metadata->>'document_id', metadata->>'knowledge_id', '(missing document_id)') AS document_id",
  "    , coalesce(metadata->>'target_base', '') AS target_base",
  '    , count(*)::int AS chunk_count',
  '    , min(left(text, 120)) AS text_preview',
  '  FROM ' + table,
  '  GROUP BY 1, 2',
  '), meta AS (',
  '  SELECT (SELECT count(*)::int FROM ' + table + ') AS total_rows',
  "       , (SELECT count(DISTINCT nullif(coalesce(metadata->>'document_id', metadata->>'knowledge_id', ''), ''))::int FROM " + table + ') AS distinct_documents',
  '       , (',
  '            SELECT pg_catalog.format_type(a.atttypid, a.atttypmod)',
  '            FROM pg_catalog.pg_attribute AS a',
  "            WHERE a.attrelid = to_regclass('" + table + "')",
  "              AND a.attname = 'embedding'",
  '              AND NOT a.attisdropped',
  '            LIMIT 1',
  '          ) AS embedding_type',
  ')',
  "SELECT coalesce(inv.document_id, '(none)') AS document_id,",
  "       coalesce(inv.target_base, '') AS target_base,",
  '       coalesce(inv.chunk_count, 0)::int AS chunk_count,',
  '       inv.text_preview,',
  '       meta.total_rows, meta.distinct_documents, meta.embedding_type',
  'FROM meta',
  'LEFT JOIN inv ON TRUE',
  'ORDER BY 1;',
].join('\\n');
return [{{ json: {{
  rag_table_name: table,
  expected_document_ids: expected,
  expected_document_count: expected.length,
  query,
}} }}];
"""


INVENTORY_SUMMARIZE_JS = r"""
const prepared = $('Prepare RAG inventory query').first().json || {};
const rows = $input.all().map((item) => item.json || {});
const expected = Array.isArray(prepared.expected_document_ids) ? prepared.expected_document_ids : [];
let skipped = [];
let diff = {};
try {
  diff = $('Select new MAS knowledge').first().json || {};
  skipped = Array.isArray(diff.skipped_ids) ? diff.skipped_ids : [];
} catch {}
if (diff.ingest_action === 'lookup_failed' || diff.ingest_action === 'collect_failed') {
  return [{ json: {
    status: 'rag_inventory_incomplete',
    rag_table_name: prepared.rag_table_name,
    ingest_action: diff.ingest_action,
    findings: Array.isArray(diff.findings) ? diff.findings : [],
    inserted: Number(diff.inserted) || 0,
    skipped: Number(diff.skipped) || skipped.length,
    skipped_existing: skipped,
    warnings: [diff.ingest_action],
    note: 'Ingest refused to insert because collect or existing-key lookup failed. Existing rows were not treated as absent.',
  } }];
}
const found = [...new Set(
  rows.map((row) => String(row.document_id || '')).filter((id) => id && id !== '(none)' && id !== '(missing document_id)')
)];
const missing = expected.filter((id) => !found.includes(id));
const totalRows = Number(rows[0]?.total_rows ?? 0);
const distinctDocuments = Number(rows[0]?.distinct_documents ?? found.length);
const embeddingType = typeof rows[0]?.embedding_type === 'string' ? rows[0].embedding_type : null;
const ok = missing.length === 0 && distinctDocuments >= expected.length && totalRows > 0;
const duplicateIngestSuspected = ok && totalRows >= expected.length * 4;
const warnings = [];
if (duplicateIngestSuspected) warnings.push('total_rows looks high for a single ingest; re-running insert without skip would append chunks.');
if (!embeddingType) warnings.push('embedding column type was not found; confirm the table exists in this credential/database.');
return [{ json: {
  status: ok ? 'rag_inventory_ok' : 'rag_inventory_incomplete',
  rag_table_name: prepared.rag_table_name,
  total_rows: totalRows,
  distinct_documents: distinctDocuments,
  expected_document_count: expected.length,
  expected_document_ids: expected,
  found_document_ids: found.sort(),
  missing_document_ids: missing,
  inserted: Number(diff.inserted) || 0,
  skipped: Number(diff.skipped) || skipped.length,
  skipped_existing: skipped,
  embedding_type: embeddingType,
  duplicate_ingest_suspected: duplicateIngestSuspected,
  warnings,
  note: 'Packaged corpus is skipped when target_base+knowledge_id+revision already exists. Compare distinct_documents/found_document_ids, not only total_rows.',
  documents: rows
    .filter((row) => row.document_id && row.document_id !== '(none)')
    .map((row) => ({
      document_id: row.document_id,
      target_base: row.target_base,
      chunk_count: Number(row.chunk_count || 0),
      text_preview: row.text_preview,
    })),
} }];
"""


def build_ingestion(*, node, note, code, trigger, ifnode, connect, workflow, set_fields):
    packaged = packaged_knowledge_blocks()
    expected_ids = [block["knowledge_id"] for block in packaged]
    pg = _credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")
    form = node(
        "SCHEDULE manual ingestion form", "n8n-nodes-base.formTrigger", 2.6, (-1200, -80),
        {
            "authentication": "n8nUserAuth",
            "formTitle": "MAS knowledge ingestion",
            "formDescription": "Field path: Activity Knowledge → Загрузить в RAG. Or paste the whole excel-agent-operating-guide.documents.json sheet. Existing target_base+knowledge_id+revision rows are skipped; injection_template is ignored. One-card fields below are optional.",
            "formFields": {"values": [
                {"fieldName": "corpus_json", "fieldLabel": "Paste the full knowledge sheet (excel-agent-operating-guide.documents.json)", "fieldType": "textarea", "requiredField": False},
                {"fieldName": "target_base", "fieldLabel": "Optional one-card: target_base (schedule_mvp | excel_protocol | orchestrator_routing | specialist_template)", "fieldType": "text", "requiredField": False},
                {"fieldName": "knowledge_type", "fieldLabel": "Optional one-card: keyword_instruction, worked_example, protocol_instruction, routing_card, capability_instruction", "fieldType": "text", "requiredField": False},
                {"fieldName": "knowledge_id", "fieldLabel": "Optional one-card: stable knowledge ID", "fieldType": "text", "requiredField": False},
                {"fieldName": "revision", "fieldLabel": "Optional one-card: revision", "fieldType": "text", "requiredField": False},
                {"fieldName": "title", "fieldLabel": "Optional one-card: title", "fieldType": "text", "requiredField": False},
                {"fieldName": "keywords", "fieldLabel": "Optional one-card: keywords, comma-separated", "fieldType": "text", "requiredField": False},
                {"fieldName": "topics", "fieldLabel": "Optional one-card: topics, comma-separated", "fieldType": "text", "requiredField": False},
                {"fieldName": "task_patterns", "fieldLabel": "Optional one-card: task patterns, comma-separated", "fieldType": "text", "requiredField": False},
                {"fieldName": "text", "fieldLabel": "Optional one-card: full self-contained instruction or example", "fieldType": "textarea", "requiredField": False},
                {"fieldName": "author", "fieldLabel": "Optional one-card: expert author", "fieldType": "text", "requiredField": False},
                {"fieldName": "access_scope", "fieldLabel": "Optional one-card: access scope", "fieldType": "text", "requiredField": False},
                {"fieldName": "schema_catalogue_json", "fieldLabel": "Optional one-card: schema JSON (needed when Schedule Builder renders this keyword)", "fieldType": "textarea", "requiredField": False},
            ]},
            "responseMode": "lastNode",
            "options": {"path": "tnavigator-schedule-knowledge-ingestion", "appendAttribution": False, "buttonLabel": "Validate and ingest", "ignoreBots": True, "includeUserInOutput": True},
        },
    )
    example = {"schema_version": "1.1.0", "title": "MAS knowledge corpus", "documents": [{"contract": "schedule_knowledge_block", "contract_version": "1.0", "target_base": "schedule_mvp", "knowledge_type": "keyword_instruction", "knowledge_id": "wconprod-forecast-v1", "revision": "1", "title": "WCONPROD forecast control", "keywords": ["WCONPROD"], "topics": ["Контроль по скважинам", "Прогноз"], "task_patterns": ["задать лимит по воде"], "status": "active", "author": "department-hydrodynamic-expert", "access_scope": "petroleum-engineering", "text": "Полная самодостаточная инструкция."}]}
    ns = [
        note("MAS ingestion README", (-1240, -700), "## MAS Knowledge Ingestion — n8n 2.30.8\n\nOne ingest for Excel, Orchestrator, and Schedule (`target_base`). Field path: Activity Knowledge → **Загрузить в RAG** (`POST /webhook/mas-knowledge-ingest`). That posts the live `excel-agent-operating-guide.documents.json` sheet. Form paste and Execute Sub-workflow still work. Manual trigger uses the **Packaged MAS corpus** Set snapshot from import time (not the live file).\n\nThe workflow skips `target_base` + `knowledge_id` + `revision` rows that already exist and ignores `injection_template`. To update text of an existing card, bump `revision`. Use one embedding model/dimensions in ingestion and retrieval. Activate this workflow so the Activity webhook is registered.", 620, 620),
        node(
            "Activity knowledge ingest webhook",
            "n8n-nodes-base.webhook",
            2.1,
            (-1200, -260),
            {
                "httpMethod": "POST",
                "path": "mas-knowledge-ingest",
                "responseMode": "lastNode",
                "options": {},
            },
            webhookId=INGEST_WEBHOOK_ID,
            notesInFlow=True,
            notes="POST from MAS Activity Knowledge. Live corpus in body.documents — does not use the packaged Set snapshot.",
        ),
        trigger("Receive SCHEDULE knowledge document", (-1200, 100), example),
        form,
        node("Sync packaged MAS knowledge", "n8n-nodes-base.manualTrigger", 1, (-1200, 280), {}, notesInFlow=True, notes="Uses Packaged MAS corpus (import-time snapshot). Activity Knowledge posts the live file instead."),
        set_fields("Packaged MAS corpus", (-920, 280), [("documents", packaged, "array")]),
        code("Collect MAS knowledge blocks", (-920, 80), COLLECT_KNOWLEDGE_JS),
        node("Ensure parent knowledge tables", "n8n-nodes-base.postgres", 2.6, (-700, 80), {"operation": "executeQuery", "query": ENSURE_PARENT_SQL, "options": {"queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=pg, executeOnce=True, alwaysOutputData=True),
        node("Lookup existing knowledge keys", "n8n-nodes-base.postgres", 2.6, (-480, 80), {"operation": "executeQuery", "query": LOOKUP_EXISTING_SQL, "options": {"queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=pg, executeOnce=True, alwaysOutputData=True, onError="continueRegularOutput"),
        code("Select new MAS knowledge", (-260, 80), SELECT_NEW_KNOWLEDGE_JS, executeOnce=True),
        ifnode("New knowledge to insert?", (-40, 80), "={{ $json.ingest_action }}", "insert", "string"),
        code("Normalize approved SCHEDULE knowledge", (200, -40), INGEST_NORMALIZE),
        ifnode("Knowledge approved for ingestion?", (440, -40), "={{ $json.status }}", "approved_for_ingestion", "string"),
        node("PGVector — insert approved SCHEDULE knowledge", "@n8n/n8n-nodes-langchain.vectorStorePGVector", 1.3, (700, -180), {"mode": "insert", "tableName": "tnavigator_schedule_knowledge_v1", "embeddingBatchSize": 64, "options": {"columnNames": {"values": {"idColumnName": "id", "vectorColumnName": "embedding", "contentColumnName": "text", "metadataColumnName": "metadata"}}}}, credentials=pg),
        node("SCHEDULE Default Data Loader", "@n8n/n8n-nodes-langchain.documentDefaultDataLoader", 1.1, (680, -500), {"dataType": "json", "jsonMode": "expressionData", "jsonData": "={{ $json.text }}", "textSplittingMode": "custom", "options": {"metadata": {"metadataValues": [{"name": k, "value": "={{ $json.metadata." + k + " }}"} for k in ["document_id","document_revision","source_hash","target_base","knowledge_type","knowledge_id","revision","status","access_scope","author","authority_level","approval_status","section","knowledge_status","title","page","heading","keyword_families","topics","task_patterns","parent_key","ingest_key","vendor","simulator","simulator_version"]]}}}),
        node("SCHEDULE Recursive Text Splitter — 1200/180", "@n8n/n8n-nodes-langchain.textSplitterRecursiveCharacterTextSplitter", 1, (400, -500), {"chunkSize": 1200, "chunkOverlap": 180, "options": {"splitCode": "markdown"}}),
        node("SCHEDULE Embeddings — configure same model in retrieval", "@n8n/n8n-nodes-langchain.embeddingsOpenAi", 1.2, (940, -500), {"model": "text-embedding-3-small", "options": {"batchSize": 16, "stripNewLines": True, "timeout": 600, "encodingFormat": "float"}}, credentials=_embedding_credential("REPLACE: SCHEDULE embedding credential")),
        node("Finalize indexes and deduplicate chunks", "n8n-nodes-base.postgres", 2.6, (980, -180), {"operation": "executeQuery", "query": FINALIZE_INGEST_SQL, "options": {"queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=pg, executeOnce=True),
        code("Prepare full parent knowledge persistence", (1220, -180), PREPARE_PARENT_PERSIST, executeOnce=True),
        node("PostgreSQL — upsert full parent knowledge", "n8n-nodes-base.postgres", 2.6, (1460, -180), {"operation": "executeQuery", "query": PARENT_UPSERT_SQL, "options": {"queryReplacement": "={{ $json.sql_parameters }}", "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=pg),
        code("Prepare approved schema catalogue persistence", (1700, -180), PREPARE_CATALOGUE_PERSIST, executeOnce=True),
        node("PostgreSQL — upsert approved schema catalogue", "n8n-nodes-base.postgres", 2.6, (1940, -180), {"operation": "executeQuery", "query": CATALOGUE_UPSERT_SQL, "options": {"queryReplacement": "={{ $json.sql_parameters }}", "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=pg, alwaysOutputData=True),
        code("Prepare RAG inventory query", (1220, 80), _inventory_prepare_js(expected_ids), executeOnce=True),
        node("Postgres — inspect RAG table contents", "n8n-nodes-base.postgres", 2.6, (1460, 80), {"operation": "executeQuery", "query": "={{ $json.query }}", "options": {"queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=pg, executeOnce=True, alwaysOutputData=True),
        code("Summarize RAG inventory", (1700, 80), INVENTORY_SUMMARIZE_JS, executeOnce=True, notesInFlow=True, notes="status=rag_inventory_ok means every packaged knowledge_id is present. skipped_existing lists keys that were already in the parent table."),
        code("Return SCHEDULE ingestion gate", (440, 140), "return [{json:{contract:'schedule_knowledge_ingest_result',contract_version:'1.0',status:$json.status,findings:$json.findings,vector_write_allowed:false}}];"),
        code("Shape MAS ingest response", (1940, 80), SHAPE_INGEST_RESPONSE_JS, executeOnce=True, notesInFlow=True, notes="Webhook lastNode: added / skipped / total_in_rag. Keep this the terminal node."),
    ]
    c = {}
    connect(c, "Activity knowledge ingest webhook", "Collect MAS knowledge blocks")
    connect(c, "Receive SCHEDULE knowledge document", "Collect MAS knowledge blocks")
    connect(c, "SCHEDULE manual ingestion form", "Collect MAS knowledge blocks")
    connect(c, "Sync packaged MAS knowledge", "Packaged MAS corpus")
    connect(c, "Packaged MAS corpus", "Collect MAS knowledge blocks")
    connect(c, "Collect MAS knowledge blocks", "Ensure parent knowledge tables")
    connect(c, "Ensure parent knowledge tables", "Lookup existing knowledge keys")
    connect(c, "Lookup existing knowledge keys", "Select new MAS knowledge")
    connect(c, "Select new MAS knowledge", "New knowledge to insert?")
    connect(c, "New knowledge to insert?", "Normalize approved SCHEDULE knowledge", idx=0)
    connect(c, "New knowledge to insert?", "Prepare RAG inventory query", idx=1)
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
    connect(c, "PostgreSQL — upsert approved schema catalogue", "Prepare RAG inventory query")
    connect(c, "Prepare RAG inventory query", "Postgres — inspect RAG table contents")
    connect(c, "Postgres — inspect RAG table contents", "Summarize RAG inventory")
    connect(c, "Summarize RAG inventory", "Shape MAS ingest response")
    connect(c, "Return SCHEDULE ingestion gate", "Shape MAS ingest response")
    return workflow("MAS — Knowledge Ingestion", "Single MAS ingest: Activity Knowledge webhook, form paste, or packaged Set snapshot. Skips target_base+knowledge_id+revision rows that already exist; ignores injection_template.", ns, c, "schedule_knowledge_ingest/v1")


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
        note("SCHEDULE hybrid retrieval README",(-1280,-700),"## Hybrid retrieval + full parent hydration — n8n 2.30.8\n\nPostgreSQL full-text + PGVector semantic + exact tags → deterministic RRF per parent block → full active parent hydration. `target_base` isolates corpora in one physical table. Schema catalogue lookup is required only for `schedule_mvp`. Empty tag filters do not match the whole namespace. Missing required instruction coverage causes `abstain`.",610,450),
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
        node("SCHEDULE Retrieval Embeddings — same model as ingestion","@n8n/n8n-nodes-langchain.embeddingsOpenAi",1.2,(-280,-500),{"model":"text-embedding-3-small","options":{"batchSize":16,"stripNewLines":True,"timeout":600,"encodingFormat":"float"}},credentials=_embedding_credential("REPLACE: SCHEDULE embedding credential")),
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
    return workflow("MAS — Knowledge Retrieval", "Shared hybrid retrieval for all MAS agents: lexical + semantic + tags, RRF, target_base isolation, full parent hydration and fail-closed coverage/schema rules per namespace.", ns, c, "schedule_retrieval/v1")
