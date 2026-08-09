"""Production-shaped SCHEDULE RAG workflows for n8n 2.30.8.

The workflows remain portable: all credentials and installation-specific bindings
are selected in the n8n UI, and licensed manual content never enters the export.
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
const raw=$json.schedule_knowledge_document??$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}};
const m={...parse(raw.metadata_json),...(obj(raw.metadata)?raw.metadata:{})};
const catalogue=parse(raw.schema_catalogue_json??raw.schema_catalogue),catalogueProvided=Object.keys(catalogue).length>0;
for(const k of ['document_id','vendor','simulator','simulator_version','document_revision','source_hash','access_scope','approved_by','authority_level','title','page','heading'])if(clean(raw[k]))m[k]=clean(raw[k]);
if(!clean(m.vendor))m.vendor='Rock Flow Dynamics';if(!clean(m.simulator))m.simulator='tNavigator';if(!clean(m.simulator_version))m.simulator_version='22.2';if(!clean(m.authority_level))m.authority_level='vendor_manual';
const approved=raw.approved===true||['true','yes','1','approved'].includes(clean(raw.approved).toLowerCase());
const text=clean(raw.text||raw.document_text),findings=[];
const required=['document_id','document_revision','source_hash','access_scope','approved_by'];
if(!text)findings.push({code:'MANUAL_TEXT_REQUIRED',severity:'error'});
if(text.length>2000000)findings.push({code:'MANUAL_TEXT_TOO_LARGE',severity:'error'});
if(clean(m.vendor)!=='Rock Flow Dynamics'||clean(m.simulator).toLowerCase()!=='tnavigator'||clean(m.simulator_version)!=='22.2')findings.push({code:'MANUAL_VERSION_REQUIRED',severity:'error'});
if(clean(m.authority_level)!=='vendor_manual')findings.push({code:'AUTHORITY_LEVEL_REQUIRED',severity:'error'});
for(const k of required)if(!clean(m[k]))findings.push({code:`${k.toUpperCase()}_REQUIRED`,severity:'error'});
if(!clean(m.source_hash))findings.push({code:'SOURCE_HASH_REQUIRED',severity:'error'});else if(!/^sha256:[a-f0-9]{64}$/i.test(clean(m.source_hash)))findings.push({code:'SOURCE_HASH_INVALID',severity:'error'});
if(!approved)findings.push({code:'ACCOUNTABLE_APPROVAL_REQUIRED',severity:'error'});
if(!clean(m.page)&&!clean(m.heading))findings.push({code:'CITATION_LOCATION_REQUIRED',severity:'error'});
const allowed=new Set(KEYWORDS_PLACEHOLDER);
const keywordFamilies=[...new Set((text.match(/\b[A-Z][A-Z0-9_]+\b/g)||[]).filter(k=>allowed.has(k)))];
if(!keywordFamilies.length)findings.push({code:'NO_SCHEDULE_KEYWORD_FOUND',severity:'error'});
if(catalogueProvided){const cp=obj(catalogue.simulator_profile)?catalogue.simulator_profile:{},schemas=Array.isArray(catalogue.schemas)?catalogue.schemas:[];
  if(catalogue.contract!=='schedule_schema_catalogue'||catalogue.contract_version!=='1.0')findings.push({code:'SCHEMA_CATALOGUE_CONTRACT_INVALID',severity:'error'});
  if(!/^sha256:[a-f0-9]{64}$/i.test(clean(catalogue.catalogue_hash)))findings.push({code:'SCHEMA_CATALOGUE_HASH_INVALID',severity:'error'});
  if(clean(catalogue.source_hash).toLowerCase()!==clean(m.source_hash).toLowerCase())findings.push({code:'SCHEMA_SOURCE_HASH_MISMATCH',severity:'error'});
  if(clean(catalogue.access_scope)!==clean(m.access_scope))findings.push({code:'SCHEMA_ACCESS_SCOPE_MISMATCH',severity:'error'});
  if(clean(cp.vendor)!=='Rock Flow Dynamics'||clean(cp.simulator).toLowerCase()!=='tnavigator'||clean(cp.version)!=='22.2')findings.push({code:'SCHEMA_PROFILE_NOT_APPROVED',severity:'error'});
  if(catalogue.approved!==true||clean(catalogue.approved_by)!==clean(m.approved_by)||!clean(catalogue.approval_gate_id))findings.push({code:'SCHEMA_ACCOUNTABLE_APPROVAL_REQUIRED',severity:'error'});
  if(!schemas.length)findings.push({code:'SCHEMA_CATALOGUE_EMPTY',severity:'error'});
  for(const s of schemas){const kw=clean(s.keyword).toUpperCase(),c=obj(s.citation)?s.citation:{},fields=Array.isArray(s.fields)?s.fields:[],names=new Set(fields.map(f=>clean(f?.name)).filter(Boolean)),sem=obj(s.semantics)?s.semantics:null;
    if(!allowed.has(kw)||!clean(s.schema_id)||!clean(s.schema_revision)||!fields.length)findings.push({code:'SCHEMA_ENTRY_INVALID',severity:'error',keyword:kw});
    if(clean(c.document_revision)!=='22.2'||clean(c.source_hash).toLowerCase()!==clean(m.source_hash).toLowerCase()||(!clean(c.page)&&!clean(c.heading)))findings.push({code:'SCHEMA_CITATION_INVALID',severity:'error',keyword:kw});
    if(!sem)findings.push({code:'SCHEMA_SEMANTICS_REQUIRED',severity:'error',keyword:kw});else{
      const period=clean(sem.period||'ANY').toUpperCase(),clock=obj(sem.clock)?sem.clock:{},fieldOk=f=>clean(f)&&names.has(clean(f));
      if(!['ANY','HISTORY','FORECAST'].includes(period))findings.push({code:'SEMANTIC_PERIOD_INVALID',severity:'error',keyword:kw});for(const f of [clock.sets_from_field,clock.effective_date_field].filter(Boolean))if(!fieldOk(f))findings.push({code:'SEMANTIC_FIELD_UNKNOWN',severity:'error',keyword:kw,field:f});
      for(const d of(Array.isArray(sem.definitions)?sem.definitions:[]))if(!obj(d)||!clean(d.entity_type)||!fieldOk(d.id_field))findings.push({code:'SEMANTIC_DEFINITION_INVALID',severity:'error',keyword:kw});
      for(const r of(Array.isArray(sem.references)?sem.references:[]))if(!obj(r)||!clean(r.entity_type)||!fieldOk(r.id_field))findings.push({code:'SEMANTIC_REFERENCE_INVALID',severity:'error',keyword:kw});
      for(const e of(Array.isArray(sem.hierarchy_edges)?sem.hierarchy_edges:[]))if(!obj(e)||!clean(e.graph)||!clean(e.child_entity_type)||!clean(e.parent_entity_type)||!fieldOk(e.child_field)||!fieldOk(e.parent_field))findings.push({code:'SEMANTIC_HIERARCHY_RULE_INVALID',severity:'error',keyword:kw});
      for(const a of(Array.isArray(sem.state_assignments)?sem.state_assignments:[])){const keys=Array.isArray(a?.key_fields)?a.key_fields:[],values=Array.isArray(a?.value_fields)?a.value_fields:[];if(!obj(a)||!clean(a.namespace)||!clean(a.entity_type)||!fieldOk(a.entity_field)||!keys.every(fieldOk)||!values.length||!values.every(fieldOk))findings.push({code:'SEMANTIC_STATE_RULE_INVALID',severity:'error',keyword:kw})}
      for(const l of(Array.isArray(sem.lifecycle_effects)?sem.lifecycle_effects:[]))if(!obj(l)||!clean(l.entity_type)||!fieldOk(l.id_field)||!['RETIRE','DELETE','REACTIVATE'].includes(clean(l.action).toUpperCase()))findings.push({code:'SEMANTIC_LIFECYCLE_RULE_INVALID',severity:'error',keyword:kw});
      for(const q of(Array.isArray(sem.interval_rules)?sem.interval_rules:[]))if(!obj(q)||!clean(q.namespace)||!clean(q.entity_type)||!fieldOk(q.entity_field)||!fieldOk(q.start_field)||!fieldOk(q.end_field)||clean(q.start_field)===clean(q.end_field))findings.push({code:'SEMANTIC_INTERVAL_RULE_INVALID',severity:'error',keyword:kw});
      for(const n of(Array.isArray(sem.numeric_constraints)?sem.numeric_constraints:[]))if(!obj(n)||!fieldOk(n.field)||(n.min!==undefined&&!Number.isFinite(Number(n.min)))||(n.max!==undefined&&!Number.isFinite(Number(n.max)))||(n.min!==undefined&&n.max!==undefined&&Number(n.min)>Number(n.max)))findings.push({code:'SEMANTIC_NUMERIC_RULE_INVALID',severity:'error',keyword:kw});
      for(const w of(Array.isArray(sem.wildcard_rules)?sem.wildcard_rules:[]))if(!obj(w)||!fieldOk(w.field)||!clean(w.entity_type))findings.push({code:'SEMANTIC_WILDCARD_RULE_INVALID',severity:'error',keyword:kw});
    }
  }
}
const hash=s=>{let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return(h>>>0).toString(16).padStart(8,'0')};
const sourceHash=clean(m.source_hash).toLowerCase();
return[{json:{contract:'schedule_knowledge_document',contract_version:'1.0',status:findings.length?'needs_input':'approved_for_ingestion',text,metadata:{document_id:clean(m.document_id),vendor:'Rock Flow Dynamics',simulator:'tNavigator',simulator_version:'22.2',document_revision:clean(m.document_revision),source_hash:sourceHash,access_scope:clean(m.access_scope),approved_by:clean(m.approved_by),authority_level:'vendor_manual',approval_status:'approved',title:clean(m.title)||'tNavigator Technical Manual 22.2',page:clean(m.page),heading:clean(m.heading),keyword_families:keywordFamilies,ingest_key:`${sourceHash}:${hash(text)}`},schema_catalogue:catalogueProvided?catalogue:null,catalogue_present:catalogueProvided,findings}}];
""".replace("KEYWORDS_PLACEHOLDER", json.dumps(KEYWORDS))


INGEST_RESULT = r"""
const x=$('Normalize approved SCHEDULE knowledge').first().json;
const db=$json||{};
return[{json:{contract:'schedule_knowledge_ingest_result',contract_version:'1.0',status:'ingested',document_id:x.metadata.document_id,source_hash:x.metadata.source_hash,document_revision:x.metadata.document_revision,access_scope:x.metadata.access_scope,approved_by:x.metadata.approved_by,authority_level:x.metadata.authority_level,keyword_families:x.metadata.keyword_families,vector_table:'tnavigator_schedule_knowledge_v1',schema_catalogue_table:'tnavigator_schedule_schema_catalogue_v1',schema_catalogue_stored:x.catalogue_present===true&&Number(db.catalogues_stored||0)===1,catalogue_hash:x.schema_catalogue?.catalogue_hash||null,embedding_profile:'configure one identical model/dimensions in ingestion and retrieval',idempotency_key:x.metadata.ingest_key,findings:[]}}];
"""


PREPARE_CATALOGUE_PERSIST = r"""
const x=$('Normalize approved SCHEDULE knowledge').first().json,c=x.schema_catalogue;
return[{json:{...x,sql_parameters:[c?JSON.stringify(c):'',c?.catalogue_hash||'',c?.catalogue_ref||'',c?.source_hash||'',c?.access_scope||'',c?.approved_by||'',c?.approval_gate_id||'']}}];
"""


CATALOGUE_UPSERT_SQL = """CREATE TABLE IF NOT EXISTS tnavigator_schedule_schema_catalogue_v1 (
  catalogue_hash text PRIMARY KEY,
  catalogue_ref text NOT NULL,
  source_hash text NOT NULL,
  access_scope text NOT NULL,
  simulator_version text NOT NULL CHECK (simulator_version = '22.2'),
  approved_by text NOT NULL,
  approval_gate_id text NOT NULL,
  schema_catalogue jsonb NOT NULL,
  stored_at timestamptz NOT NULL DEFAULT now()
);
WITH payload AS (SELECT NULLIF($1,'')::jsonb AS body), inserted AS (
  INSERT INTO tnavigator_schedule_schema_catalogue_v1 (
    catalogue_hash,catalogue_ref,source_hash,access_scope,simulator_version,
    approved_by,approval_gate_id,schema_catalogue
  )
  SELECT $2,$3,$4,$5,'22.2',$6,$7,body FROM payload WHERE body IS NOT NULL
  ON CONFLICT (catalogue_hash) DO UPDATE SET
    catalogue_ref=EXCLUDED.catalogue_ref,source_hash=EXCLUDED.source_hash,
    access_scope=EXCLUDED.access_scope,approved_by=EXCLUDED.approved_by,
    approval_gate_id=EXCLUDED.approval_gate_id,schema_catalogue=EXCLUDED.schema_catalogue,
    stored_at=now()
  RETURNING catalogue_hash
)
SELECT count(*)::int AS catalogues_stored FROM inserted"""


RETRIEVAL_NORMALIZE = r"""
const raw=$json.schedule_retrieval_request??$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}};
const f={...parse(raw.filters_json),...(obj(raw.filters)?raw.filters:{})},findings=[];
const query=clean(raw.query),accessScope=clean(f.access_scope||raw.access_scope),version=clean(f.simulator_version||raw.simulator_version),authority=clean(f.authority_level||raw.authority_level);
if(!query)findings.push({code:'QUERY_REQUIRED',severity:'error'});
if(query.length>8000)findings.push({code:'QUERY_TOO_LARGE',severity:'error'});
if(version!=='22.2')findings.push({code:'EXACT_VERSION_REQUIRED',severity:'error'});
if(authority!=='vendor_manual')findings.push({code:'AUTHORITY_FILTER_REQUIRED',severity:'error'});
if(!accessScope)findings.push({code:'ACCESS_SCOPE_REQUIRED',severity:'error'});
const allowed=new Set(KEYWORDS_PLACEHOLDER),exact=[...new Set((query.match(/\b[A-Z][A-Z0-9_]+\b/g)||[]).filter(k=>allowed.has(k)))];
const tags=Array.isArray(f.keyword_families)?f.keyword_families.map(v=>clean(v).toUpperCase()).filter(v=>allowed.has(v)):exact;
const topK=Math.min(30,Math.max(1,Math.trunc(Number(raw.top_k)||10)));
return[{json:{contract:'schedule_retrieval_query',contract_version:'1.0',status:findings.length?'needs_input':'query_ready',query,top_k:topK,filters:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',simulator_version:'22.2',authority_level:'vendor_manual',approval_status:'approved',access_scope:accessScope,keyword_families:tags},exact_keyword_terms:exact,findings}}];
""".replace("KEYWORDS_PLACEHOLDER", json.dumps(KEYWORDS))


PREPARE_LEXICAL = r"""
const x=$json;
return[{json:{...x,branch:'lexical',sql_parameters:[x.query,x.filters.access_scope,x.top_k,JSON.stringify(x.filters.keyword_families||[])]}}];
"""


LEXICAL_SQL = """WITH authorized AS (
  SELECT id, text, metadata,
         regexp_split_to_array(
           upper(regexp_replace(coalesce(metadata->>'keyword_families',''), '[\\[\\]\"]', '', 'g')),
           '\\s*,\\s*'
         ) AS keyword_tags
  FROM tnavigator_schedule_knowledge_v1
  WHERE metadata @> jsonb_build_object(
    'vendor','Rock Flow Dynamics','simulator','tNavigator','simulator_version','22.2',
    'authority_level','vendor_manual','approval_status','approved','access_scope',$2
  )
), ranked AS (
  SELECT id::text AS candidate_id,
         text AS page_content,
         metadata,
         CASE WHEN EXISTS (
           SELECT 1 FROM jsonb_array_elements_text($4::jsonb) requested
           WHERE upper(trim(requested.value)) = ANY (keyword_tags)
         ) THEN 1 ELSE 0 END AS exact_hit,
         ts_rank_cd(to_tsvector('simple', coalesce(text,'')), websearch_to_tsquery('simple',$1)) AS lexical_score
  FROM authorized
  WHERE to_tsvector('simple', coalesce(text,'')) @@ websearch_to_tsquery('simple',$1)
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text($4::jsonb) requested
       WHERE upper(trim(requested.value)) = ANY (keyword_tags)
     )
)
SELECT candidate_id,page_content,metadata,exact_hit,lexical_score,
       row_number() OVER (ORDER BY exact_hit DESC, lexical_score DESC, candidate_id) AS lexical_rank
FROM ranked
ORDER BY lexical_rank
LIMIT $3"""


TAG_SQL = """SELECT id::text AS candidate_id,text AS page_content,metadata,
       row_number() OVER (ORDER BY id::text) AS tag_rank
FROM tnavigator_schedule_knowledge_v1
WHERE metadata @> jsonb_build_object(
  'vendor','Rock Flow Dynamics','simulator','tNavigator','simulator_version','22.2',
  'authority_level','vendor_manual','approval_status','approved','access_scope',$1
)
AND ($2::jsonb = '[]'::jsonb OR EXISTS (
  SELECT 1 FROM jsonb_array_elements_text($2::jsonb) q
  WHERE upper(trim(q.value)) = ANY (
    regexp_split_to_array(
      upper(regexp_replace(coalesce(metadata->>'keyword_families',''), '[\\[\\]\"]', '', 'g')),
      '\\s*,\\s*'
    )
  )
))
ORDER BY id::text
LIMIT $3"""


PREPARE_TAG = r"""
const x=$('Validate SCHEDULE retrieval request').first().json;
return[{json:{...x,branch:'tag',sql_parameters:[x.filters.access_scope,JSON.stringify(x.filters.keyword_families||[]),x.top_k]}}];
"""


WRAP_LEXICAL = r"""
const q=$('Validate SCHEDULE retrieval request').first().json;
const input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error');
const candidates=input.filter(i=>i.json?.candidate_id&&i.json?.page_content!==undefined).map((i,n)=>({candidate_id:String(i.json.candidate_id),page_content:String(i.json.page_content||''),metadata:i.json.metadata||{},rank:Number(i.json.lexical_rank||n+1),score:Number(i.json.lexical_score||0),exact_hit:Boolean(Number(i.json.exact_hit||0))}));
return[{json:{branch:'lexical',query:q,candidates,branch_findings:error?[{code:'LEXICAL_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];
"""


WRAP_TAG = r"""
const q=$('Validate SCHEDULE retrieval request').first().json;
const input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error');
const candidates=input.filter(i=>i.json?.candidate_id&&i.json?.page_content!==undefined).map((i,n)=>({candidate_id:String(i.json.candidate_id),page_content:String(i.json.page_content||''),metadata:i.json.metadata||{},rank:Number(i.json.tag_rank||n+1),score:0}));
return[{json:{branch:'tag',query:q,candidates,branch_findings:error?[{code:'TAG_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];
"""


WRAP_SEMANTIC = r"""
const q=$('Validate SCHEDULE retrieval request').first().json;
const input=$input.all(),error=input.find(i=>i.json?.error||i.json?.message&&i.json?.level==='error');
const candidates=input.map((i,n)=>{const d=i.json.document||{};return{candidate_id:String(d.metadata?.chunk_id||d.metadata?.ingest_key||''),page_content:String(d.pageContent||''),metadata:d.metadata||{},rank:n+1,score:Number(i.json.score||0)}}).filter(c=>c.candidate_id&&c.page_content);
return[{json:{branch:'semantic',query:q,candidates,branch_findings:error?[{code:'SEMANTIC_BRANCH_FAILED',severity:'error',message:String(error.json.error||error.json.message)}]:[]}}];
"""


RRF = r"""
const packets=$input.all().map(i=>i.json),q=packets.find(p=>p.query)?.query||{},by=new Map(),k=60;
const required={vendor:'Rock Flow Dynamics',simulator:'tNavigator',simulator_version:'22.2',authority_level:'vendor_manual',approval_status:'approved',access_scope:q.filters?.access_scope};
const validMeta=m=>m&&Object.entries(required).every(([a,b])=>String(m[a]??'')===String(b??''));
const branchFindings=packets.flatMap(p=>Array.isArray(p.branch_findings)?p.branch_findings:[]),findings=[...branchFindings];
for(const p of packets){for(const c of(Array.isArray(p.candidates)?p.candidates:[])){if(!validMeta(c.metadata))continue;const id=String(c.candidate_id||'');if(!id)continue;const x=by.get(id)||{candidate_id:id,page_content:String(c.page_content||''),metadata:c.metadata,rrf_score:0,branches:[],ranks:{}};x.rrf_score+=1/(k+Math.max(1,Number(c.rank)||999));x.branches.push(p.branch);x.ranks[p.branch]=Number(c.rank)||999;if(c.exact_hit)x.rrf_score+=0.02;by.set(id,x)}}
const results=[...by.values()].sort((a,b)=>b.rrf_score-a.rrf_score||a.candidate_id.localeCompare(b.candidate_id)).slice(0,Number(q.top_k)||10);
const normalizeTags=v=>{if(Array.isArray(v))return v.map(String).map(x=>x.trim().toUpperCase()).filter(Boolean);if(typeof v==='string'){try{const p=JSON.parse(v);if(Array.isArray(p))return normalizeTags(p)}catch{}return v.replace(/[\[\]"]/g,'').split(',').map(x=>x.trim().toUpperCase()).filter(Boolean)}return[]};
const citations=results.map(r=>({candidate_id:r.candidate_id,document_id:String(r.metadata.document_id||''),document_revision:String(r.metadata.document_revision||''),source_hash:String(r.metadata.source_hash||''),page:String(r.metadata.page||''),heading:String(r.metadata.heading||''),keyword_families:normalizeTags(r.metadata.keyword_families),rrf_score:Number(r.rrf_score.toFixed(8)),branches:[...new Set(r.branches)].sort()}));
const missing=citations.filter(c=>!c.document_id||!c.document_revision||!c.source_hash||(!c.page&&!c.heading));
const requested=Array.isArray(q.exact_keyword_terms)?q.exact_keyword_terms.map(String).map(x=>x.toUpperCase()):[],covered=new Set(citations.flatMap(c=>c.keyword_families));
const uncovered=requested.filter(k=>!covered.has(k));
if(!results.length)findings.push({code:'NO_AUTHORIZED_EVIDENCE',severity:'error'});
if(missing.length)findings.push({code:'CITATION_INCOMPLETE',severity:'error',candidate_ids:missing.map(x=>x.candidate_id)});
if(uncovered.length)findings.push({code:'KEYWORD_COVERAGE_INCOMPLETE',severity:'error',keywords:uncovered});
const hard=findings.length>0;
return[{json:{contract:'schedule_retrieval_result',contract_version:'1.0',status:hard?'abstain':'succeeded',query:q.query,filters:q.filters,results:hard?[]:results.map(r=>({candidate_id:r.candidate_id,text:r.page_content,metadata:r.metadata,rrf_score:Number(r.rrf_score.toFixed(8)),branches:[...new Set(r.branches)].sort()})),citations:hard?[]:citations,findings,retrieval:{algorithm:'rrf',rrf_k:k,branches:['lexical','semantic','tag'],candidate_count:by.size,returned:hard?0:results.length},evidence_ready:!hard}}];
"""


PREPARE_SCHEMA_LOOKUP = r"""
const evidence=$json||{},hashes=[...new Set((Array.isArray(evidence.citations)?evidence.citations:[]).map(c=>String(c.source_hash||'').toLowerCase()).filter(Boolean))];
return[{json:{evidence,sql_parameters:[String(evidence.filters?.access_scope||''),JSON.stringify(hashes)]}}];
"""


CATALOGUE_LOOKUP_SQL = """SELECT catalogue_hash,catalogue_ref,source_hash,access_scope,
       approved_by,approval_gate_id,schema_catalogue
FROM tnavigator_schedule_schema_catalogue_v1
WHERE access_scope=$1 AND simulator_version='22.2'
  AND source_hash IN (SELECT jsonb_array_elements_text($2::jsonb))
ORDER BY stored_at DESC,catalogue_hash
LIMIT 20"""


ATTACH_SCHEMA_CATALOGUE = r"""
const prepared=$('Prepare approved schema catalogue lookup').first().json,e=prepared.evidence||{},obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const parse=v=>{if(obj(v))return v;if(typeof v!=='string'||!v.trim())return{};try{const x=JSON.parse(v);return obj(x)?x:{}}catch{return{}}};
const rows=$input.all().map(i=>i.json||{}),findings=arr(e.findings)?e.findings.slice():[],requested=arr(e.filters?.keyword_families)&&e.filters.keyword_families.length?e.filters.keyword_families.map(v=>clean(v).toUpperCase()):(clean(e.query).match(/\b[A-Z][A-Z0-9_]+\b/g)||[]);
const citationHashes=new Set((arr(e.citations)?e.citations:[]).map(c=>clean(c.source_hash).toLowerCase()).filter(Boolean));
const valid=[];for(const row of rows){const c=parse(row.schema_catalogue);if(!Object.keys(c).length)continue;const p=obj(c.simulator_profile)?c.simulator_profile:{},schemas=arr(c.schemas)?c.schemas:[],covered=new Set(schemas.map(s=>clean(s.keyword).toUpperCase()).filter(Boolean)),semanticsComplete=schemas.every(s=>obj(s.semantics));const okay=c.contract==='schedule_schema_catalogue'&&c.contract_version==='1.0'&&c.approved===true&&clean(c.approved_by)&&clean(c.approval_gate_id)&&clean(c.access_scope)===clean(e.filters?.access_scope)&&clean(p.vendor)==='Rock Flow Dynamics'&&clean(p.simulator).toLowerCase()==='tnavigator'&&clean(p.version)==='22.2'&&/^sha256:[a-f0-9]{64}$/i.test(clean(c.catalogue_hash))&&citationHashes.has(clean(c.source_hash).toLowerCase())&&requested.every(k=>covered.has(k))&&semanticsComplete;if(okay)valid.push(c)}
if(rows.some(r=>r.error||r.message&&r.level==='error'))findings.push({code:'SCHEMA_CATALOGUE_LOOKUP_FAILED',severity:'error'});
if(!requested.length)findings.push({code:'SCHEMA_KEYWORD_SCOPE_REQUIRED',severity:'error'});
if(!valid.length)findings.push({code:'APPROVED_SCHEMA_CATALOGUE_NOT_FOUND',severity:'error',keywords:requested});
if(valid.length>1&&new Set(valid.map(c=>clean(c.catalogue_hash).toLowerCase())).size>1)findings.push({code:'SCHEMA_CATALOGUE_AMBIGUOUS',severity:'error',catalogue_hashes:[...new Set(valid.map(c=>clean(c.catalogue_hash)))]});
const hard=findings.some(f=>f.severity==='error'),selected=hard?null:valid[0];
return[{json:{...e,status:hard?'abstain':'succeeded',results:hard?[]:e.results,citations:hard?[]:e.citations,findings,evidence_ready:!hard,schema_catalogue:selected,retrieval:{...(e.retrieval||{}),schema_catalogue_lookup:true,catalogue_hash:selected?.catalogue_hash||null}}}];
"""


def build_ingestion(*, node, note, code, trigger, ifnode, connect, workflow):
    form = node(
        "SCHEDULE manual ingestion form", "n8n-nodes-base.formTrigger", 2.6, (-1200, -80),
        {
            "authentication": "n8nUserAuth",
            "formTitle": "tNavigator 22.2 SCHEDULE knowledge ingestion",
            "formDescription": "Paste only licensed, internally approved excerpts. The manual is stored in PostgreSQL/PGVector, never in this workflow export.",
            "formFields": {"values": [
                {"fieldName": "document_text", "fieldLabel": "Approved manual text", "fieldType": "textarea", "requiredField": True},
                {"fieldName": "document_id", "fieldLabel": "Document ID", "fieldType": "text", "requiredField": True},
                {"fieldName": "document_revision", "fieldLabel": "Document revision", "fieldType": "text", "requiredField": True},
                {"fieldName": "source_hash", "fieldLabel": "Source SHA-256 (sha256:...)", "fieldType": "text", "requiredField": True},
                {"fieldName": "access_scope", "fieldLabel": "Access scope", "fieldType": "text", "requiredField": True},
                {"fieldName": "approved_by", "fieldLabel": "Accountable approver", "fieldType": "text", "requiredField": True},
                {"fieldName": "page", "fieldLabel": "Page/range", "fieldType": "text", "requiredField": False},
                {"fieldName": "heading", "fieldLabel": "Heading", "fieldType": "text", "requiredField": True},
                {"fieldName": "approved", "fieldLabel": "Approval flag (true)", "fieldType": "text", "requiredField": True},
                {"fieldName": "schema_catalogue_json", "fieldLabel": "Approved machine-readable schema catalogue JSON (optional for knowledge-only ingestion)", "fieldType": "textarea", "requiredField": False},
            ]},
            "responseMode": "lastNode",
            "options": {"path": "tnavigator-schedule-knowledge-ingestion", "appendAttribution": False, "buttonLabel": "Validate and ingest", "ignoreBots": True, "includeUserInOutput": True},
        },
    )
    example = {"schedule_knowledge_document": {"document_text": "DATES — approved excerpt", "document_id": "tnav-22.2-schedule", "document_revision": "22.2", "source_hash": "sha256:" + "a"*64, "access_scope": "petroleum-engineering", "approved_by": "responsible-engineer", "authority_level": "vendor_manual", "vendor": "Rock Flow Dynamics", "simulator": "tNavigator", "simulator_version": "22.2", "heading": "SCHEDULE / DATES", "page": "1", "approved": True}}
    ns = [
        note("SCHEDULE ingestion README", (-1240, -650), "## Licensed knowledge + schema ingestion — n8n 2.30.8\n\nForm or Execute Sub-workflow input → fail-closed metadata/approval gate → chunks/embeddings → PGVector. Optional machine-readable catalogue JSON is validated and stored separately in PostgreSQL; vendor text never becomes renderer grammar automatically.\n\nUse one embedding model/dimensions in ingestion and retrieval. Re-ingestion is content-addressed; catalogue replacement requires a new approved hash.", 590, 460),
        trigger("Receive SCHEDULE knowledge document", (-1200, 100), example),
        form,
        code("Normalize approved SCHEDULE knowledge", (-920, -20), INGEST_NORMALIZE),
        ifnode("Knowledge approved for ingestion?", (-680, -20), "={{ $json.status }}", "approved_for_ingestion", "string"),
        node("PGVector — insert approved SCHEDULE knowledge", "@n8n/n8n-nodes-langchain.vectorStorePGVector", 1.3, (-400, -180), {"mode": "insert", "tableName": "tnavigator_schedule_knowledge_v1", "embeddingBatchSize": 64, "options": {"columnNames": {"values": {"idColumnName": "id", "vectorColumnName": "embedding", "contentColumnName": "text", "metadataColumnName": "metadata"}}}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        node("SCHEDULE Default Data Loader", "@n8n/n8n-nodes-langchain.documentDefaultDataLoader", 1.1, (-420, -500), {"dataType": "json", "jsonMode": "expressionData", "jsonData": "={{ $json.text }}", "textSplittingMode": "custom", "options": {"metadata": {"metadataValues": [{"name": k, "value": f"={{ $json.metadata.{k} }}"} for k in ["document_id","vendor","simulator","simulator_version","document_revision","source_hash","access_scope","approved_by","authority_level","approval_status","title","page","heading","keyword_families","ingest_key"]]}}}),
        node("SCHEDULE Recursive Text Splitter — 1200/180", "@n8n/n8n-nodes-langchain.textSplitterRecursiveCharacterTextSplitter", 1, (-700, -500), {"chunkSize": 1200, "chunkOverlap": 180, "options": {"splitCode": "markdown"}}),
        node("SCHEDULE Embeddings — configure same model in retrieval", "@n8n/n8n-nodes-langchain.embeddingsOpenAi", 1.2, (-160, -500), {"model": "text-embedding-3-small", "options": {"dimensions": 1536, "batchSize": 128, "stripNewLines": True, "timeout": 180, "encodingFormat": "float"}}, credentials=_embedding_credential("REPLACE: SCHEDULE embedding credential")),
        node("Finalize indexes and deduplicate chunks", "n8n-nodes-base.postgres", 2.6, (-120, -180), {"operation": "executeQuery", "query": "CREATE INDEX IF NOT EXISTS tn_sched_kb_metadata_gin ON tnavigator_schedule_knowledge_v1 USING gin (metadata);\nCREATE INDEX IF NOT EXISTS tn_sched_kb_lexical_gin ON tnavigator_schedule_knowledge_v1 USING gin (to_tsvector('simple', coalesce(text,'')));\nWITH ranked AS (SELECT id,row_number() OVER (PARTITION BY metadata->>'ingest_key',md5(text) ORDER BY id::text) AS rn FROM tnavigator_schedule_knowledge_v1), deleted AS (DELETE FROM tnavigator_schedule_knowledge_v1 t USING ranked r WHERE t.id=r.id AND r.rn>1 RETURNING t.id) SELECT count(*)::int AS duplicates_removed FROM deleted", "options": {"queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        code("Prepare approved schema catalogue persistence", (140, -180), PREPARE_CATALOGUE_PERSIST),
        node("PostgreSQL — upsert approved schema catalogue", "n8n-nodes-base.postgres", 2.6, (400, -180), {"operation": "executeQuery", "query": CATALOGUE_UPSERT_SQL, "options": {"queryReplacement": "={{ $json.sql_parameters }}", "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential")),
        code("Return SCHEDULE ingestion result", (660, -180), INGEST_RESULT),
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
    connect(c, "Finalize indexes and deduplicate chunks", "Prepare approved schema catalogue persistence")
    connect(c, "Prepare approved schema catalogue persistence", "PostgreSQL — upsert approved schema catalogue")
    connect(c, "PostgreSQL — upsert approved schema catalogue", "Return SCHEDULE ingestion result")
    return workflow("tNavigator SCHEDULE Knowledge Ingestion — approved PGVector runtime", "UI-only licensed ingestion with accountable approval, exact metadata and PGVector insertion.", ns, c, "schedule_knowledge_ingest/v1")


def _postgres(name, pos, query, params):
    return {
        "name": name, "pos": pos, "query": query,
        "parameters": {"operation": "executeQuery", "query": query, "options": {"queryReplacement": params, "queryBatching": "single", "largeNumbersOutput": "text", "replaceEmptyStrings": False}},
    }


def build_retrieval(*, node, note, code, trigger, ifnode, connect, workflow):
    ex={"schedule_retrieval_request":{"query":"WCONPROD BHP forecast","filters":{"simulator_version":"22.2","authority_level":"vendor_manual","access_scope":"petroleum-engineering"},"top_k":10}}
    lex=_postgres("PostgreSQL lexical + exact candidates",(-280,-320),LEXICAL_SQL,"={{ $json.sql_parameters }}")
    tag=_postgres("PostgreSQL tag candidates",(-280,40),TAG_SQL,"={{ $json.sql_parameters }}")
    ns=[
        note("SCHEDULE hybrid retrieval README",(-1280,-700),"## Hybrid retrieval + exact schema lookup — n8n 2.30.8\n\nExact keyword + PostgreSQL full-text + PGVector semantic + tags → deterministic RRF, followed by a separate lookup of an accountable machine-readable catalogue. Every branch is constrained to approved tNavigator 22.2 content/access scope. Missing citations, keyword coverage or catalogue causes `abstain`; free text never defines grammar.",610,450),
        trigger("Receive SCHEDULE retrieval request",(-1240,-80),ex),
        code("Validate SCHEDULE retrieval request",(-1000,-80),RETRIEVAL_NORMALIZE),
        ifnode("Retrieval request authorized?",(-760,-80),"={{ $json.status }}","query_ready","string"),
        code("Prepare lexical retrieval",(-520,-320),PREPARE_LEXICAL),
        node(lex["name"],"n8n-nodes-base.postgres",2.6,lex["pos"],lex["parameters"],credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"),alwaysOutputData=True,onError="continueRegularOutput"),
        code("Wrap lexical candidates",(-20,-320),WRAP_LEXICAL),
        code("Prepare tag retrieval",(-520,40),PREPARE_TAG),
        node(tag["name"],"n8n-nodes-base.postgres",2.6,tag["pos"],tag["parameters"],credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"),alwaysOutputData=True,onError="continueRegularOutput"),
        code("Wrap tag candidates",(-20,40),WRAP_TAG),
        node("PGVector semantic candidates","@n8n/n8n-nodes-langchain.vectorStorePGVector",1.3,(-280,-140),{"mode":"load","prompt":"={{ $json.query }}","topK":"={{ $json.top_k }}","includeDocumentMetadata":True,"tableName":"tnavigator_schedule_knowledge_v1","options":{"distanceStrategy":"cosine","columnNames":{"values":{"idColumnName":"id","vectorColumnName":"embedding","contentColumnName":"text","metadataColumnName":"metadata"}},"metadata":{"metadataValues":[{"name":"vendor","value":"Rock Flow Dynamics"},{"name":"simulator","value":"tNavigator"},{"name":"simulator_version","value":"22.2"},{"name":"authority_level","value":"vendor_manual"},{"name":"approval_status","value":"approved"},{"name":"access_scope","value":"={{ $json.filters.access_scope }}"}]}}},credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"),alwaysOutputData=True,onError="continueRegularOutput"),
        node("SCHEDULE Retrieval Embeddings — same model as ingestion","@n8n/n8n-nodes-langchain.embeddingsOpenAi",1.2,(-280,-500),{"model":"text-embedding-3-small","options":{"dimensions":1536,"batchSize":128,"stripNewLines":True,"timeout":180,"encodingFormat":"float"}},credentials=_embedding_credential("REPLACE: SCHEDULE embedding credential")),
        code("Wrap semantic candidates",(-20,-140),WRAP_SEMANTIC),
        node("Collect hybrid candidate branches","n8n-nodes-base.merge",3.2,(220,-140),{"numberInputs":3,"mode":"append"}),
        code("Fuse authorized candidates with deterministic RRF",(480,-140),RRF),
        code("Prepare approved schema catalogue lookup",(720,-140),PREPARE_SCHEMA_LOOKUP),
        node("PostgreSQL approved schema catalogue", "n8n-nodes-base.postgres", 2.6, (960,-140), {"operation":"executeQuery","query":CATALOGUE_LOOKUP_SQL,"options":{"queryReplacement":"={{ $json.sql_parameters }}","queryBatching":"single","largeNumbersOutput":"text","replaceEmptyStrings":False}}, credentials=_credential("REPLACE: SCHEDULE PostgreSQL / PGVector credential"), alwaysOutputData=True, onError="continueRegularOutput"),
        code("Attach approved schema catalogue",(1220,-140),ATTACH_SCHEMA_CATALOGUE),
        code("Return retrieval authorization gate",(-520,240),"return[{json:{contract:'schedule_retrieval_result',contract_version:'1.0',status:'needs_input',results:[],citations:[],findings:$json.findings,evidence_ready:false}}];"),
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
    connect(c,"Fuse authorized candidates with deterministic RRF","Prepare approved schema catalogue lookup")
    connect(c,"Prepare approved schema catalogue lookup","PostgreSQL approved schema catalogue")
    connect(c,"PostgreSQL approved schema catalogue","Attach approved schema catalogue")
    return workflow("tNavigator SCHEDULE Hybrid Retrieval — executable RRF runtime", "Exact, PostgreSQL lexical, PGVector semantic and tag candidates with deterministic RRF and fail-closed citations.", ns, c, "schedule_retrieval/v1")
