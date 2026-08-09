"""Build the concrete stateless tNavigator SCHEDULE specialist pipeline."""
from __future__ import annotations

import json


DECISION_RECORD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "contract", "contract_version", "objective", "considered_inputs",
        "proposed_actions", "selected_action", "rejected_actions",
        "assumptions", "evidence_refs", "citations", "tool_call_ids",
        "unresolved_questions", "acceptance_check_results",
    ],
    "properties": {
        "contract": {"enum": ["decision_record"]},
        "contract_version": {"enum": ["1.0"]},
        "objective": {"type": "string"},
        "considered_inputs": {"type": "array", "items": {"type": "object"}},
        "proposed_actions": {"type": "array", "items": {"type": "object"}},
        "selected_action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "reason_codes"],
            "properties": {
                "action": {"type": "string"},
                "reason_codes": {"type": "array", "items": {"type": "string"}},
            },
        },
        "rejected_actions": {"type": "array", "items": {"type": "object"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "object"}},
        "citations": {"type": "array", "items": {"type": "object"}},
        "tool_call_ids": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "object"}},
        "acceptance_check_results": {"type": "array", "items": {"type": "object"}},
    },
}


BUILDER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status", "summary", "build_mode", "generated_schedule", "ir_events", "changes",
        "requirements_matrix", "source_map", "completeness_report",
        "preservation_report", "evidence_gap", "deliverables", "artifact_refs",
        "assumptions", "warnings", "evidence", "self_check", "human_request",
        "error", "continuation", "decision_record",
    ],
    "properties": {
        "status": {"enum": ["succeeded", "partial", "needs_input", "needs_decision", "needs_approval", "retryable_error", "fatal_error"]},
        "summary": {"type": "string"},
        "build_mode": {"enum": ["CREATE", "REVISE"]},
        "generated_schedule": {"type": "string"},
        "ir_events": {"type": "array", "items": {"type": "object"}},
        "changes": {"type": "array", "items": {"type": "object"}},
        "requirements_matrix": {"type": "array", "items": {"type": "object"}},
        "source_map": {"type": "array", "items": {"type": "object"}},
        "completeness_report": {"type": "object"},
        "preservation_report": {"type": "object"},
        "evidence_gap": {"type": "array", "items": {"type": "object"}},
        "deliverables": {"type": "array", "items": {"type": "object"}},
        "artifact_refs": {"type": "array", "items": {"type": "object"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "self_check": {
            "type": "object",
            "additionalProperties": False,
            "required": ["performed", "passed", "checks", "reproducibility"],
            "properties": {
                "performed": {"type": "boolean"},
                "passed": {"type": "boolean"},
                "checks": {"type": "array", "items": {"type": "object"}},
                "reproducibility": {"type": "string"},
            },
        },
        "human_request": {"type": ["object", "null"]},
        "error": {"type": ["object", "null"]},
        "continuation": {"type": ["object", "null"]},
        "decision_record": DECISION_RECORD_SCHEMA,
    },
}


BUILDER_SYSTEM = """# tNavigator SCHEDULE Builder stage

You are a bounded petroleum SCHEDULE draft builder inside a visible deterministic pipeline. The Universal Orchestrator owns durable task state, Excel extraction, HITL and release. Never call Excel, FastAPI, another workflow, or claim approval.

Use only the supplied approved plan, normalized source facts, baseline inventory, and approved tNavigator 22.2 schema evidence. Never invent field order, defaults, units, dates, well/group identities, or simulator grammar.

CREATE: create only supported records and return requirements_matrix, source_map, completeness_report, typed ir_events and ADD operations. Do not render record text yourself when an approved machine-readable schema catalogue is supplied.
REVISE: preserve every unmentioned baseline construct. Return explicit KEEP/MODIFY/ADD/REMOVE changes. MODIFY/REMOVE may reference only target_node_id + expected_raw_hash pairs present in decoded_baseline_inventory.records; planning samples are not mutation authority. Absence from new Excel evidence means KEEP, never REMOVE. REMOVE requires explicit approval.

If a mandatory fact or citation is absent or conflicting, return needs_input with evidence_gap entries containing entity, effective_at, keyword, field, reason, expected_format and a concrete question.

Return a concise decision_record/v1 containing only observable input references, proposed operations, selected action with policy reason codes, citations, unresolved questions and acceptance-check results. Do not expose hidden chain-of-thought or invent post-hoc reasoning. Return exactly the connected schema."""


NORMALIZE = r"""
const incoming=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;if(typeof v==='string'){try{const p=JSON.parse(v);return obj(p)?p:{}}catch{return {}}}return {}};
const packet=parse(incoming.specialist_packet??incoming);
const previous=parse(incoming.previous_specialist_result);
const request=obj(packet.inputs?.schedule_request)?packet.inputs.schedule_request:{};
const artifacts=Array.isArray(packet.artifact_refs)?packet.artifact_refs:[];
let size=999999;try{size=JSON.stringify(packet).length}catch{}
const rag=obj(request.rag_evidence)?request.rag_evidence:{};const ragValid=rag.contract==='schedule_rag_evidence'&&rag.contract_version==='1.0'&&Array.isArray(rag.citations)&&rag.citations.length>0&&rag.citations.every(c=>obj(c)&&typeof c.document_id==='string'&&c.document_id.trim()&&typeof c.document_revision==='string'&&c.document_revision.trim()&&typeof c.source_hash==='string'&&c.source_hash.trim()&&(String(c.page||'').trim()||String(c.heading||'').trim()));
const valid=packet.contract==='specialist_packet'&&packet.contract_version==='1.0'&&packet.specialist_id==='schedule_builder_specialist'&&typeof packet.task_id==='string'&&packet.task_id.trim()&&Number.isInteger(packet.attempt)&&packet.attempt>=1&&typeof packet.objective==='string'&&packet.objective.trim()&&obj(packet.inputs)&&obj(packet.controls)&&Array.isArray(packet.acceptance_criteria)&&artifacts.every(a=>obj(a)&&['ref','kind','revision','description'].every(k=>typeof a[k]==='string'&&a[k].trim()))&&size<=262144&&ragValid;
const inherited=previous.specialist_id==='excel_extraction_specialist'&&obj(previous.compact_data)?previous.compact_data:null;
const normalizedRequest={...request,objective:String(request.objective||packet.objective||''),artifact_refs:[...(Array.isArray(request.artifact_refs)?request.artifact_refs:[]),...artifacts],source_facts_packet:obj(request.source_facts_packet)?request.source_facts_packet:inherited};
return[{json:{packet,request:normalizedRequest,previous_specialist_result:previous,latest_human_response:incoming.latest_human_response??null,packet_valid:Boolean(valid),packet_findings:ragValid?[]:[{code:'SCHEDULE_RAG_EVIDENCE_REQUIRED',severity:'error'}],task_id:String(packet.task_id||''),attempt:Number(packet.attempt||1),trace_id:String(request.trace_id||`trace_${packet.task_id||'invalid'}`)}}];
"""


PREPARE_INTAKE = r"""
const root=$json;
return[{json:{schedule_intake_request:{...root.request,task:{objective:root.request.objective},artifact_refs:root.request.artifact_refs||[]}}}];
"""


PREPARE_BASELINE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
return[{json:{baseline_request:{baseline_schedule_text:String(root.request.baseline_schedule_text||''),include_files:Array.isArray(root.request.include_files)?root.request.include_files:[]}}}];
"""


PREPARE_BASELINE_DECODE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,baseline=$('Analyze lossless baseline inventory').first().json,req=root.request;
const catalogue=req.schema_catalogue||req.rag_evidence?.schema_catalogue||{};
return[{json:{baseline_decode_request:{baseline_analysis:baseline,schema_catalogue:catalogue,change_effective_from:String(req.change_effective_from||''),model_start_date:String(req.model_start_date||''),initial_semantic_snapshot:req.initial_semantic_snapshot||null}}}];
"""


PREPARE_BASELINE_REPLAY = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,decoded=$('Decode typed baseline records').first().json,req=root.request;
const catalogue=req.schema_catalogue||req.rag_evidence?.schema_catalogue||{},temporalPolicy=req.temporal_policy||{history_end:req.history_end||null,forecast_start:req.forecast_start||null};
return[{json:{schedule_validation_request:{validation_phase:'BASELINE_PREFIX',mode:'CREATE',ir_events:Array.isArray(decoded.prefix_ir_events)?decoded.prefix_ir_events:[],baseline_decode_result:decoded,baseline_package_hash:decoded.baseline_package_hash||null,change_effective_from:decoded.change_effective_from||req.change_effective_from||null,model_start_date:decoded.model_start_date||req.model_start_date||null,simulator_profile:req.simulator_profile||{},schema_catalogue:catalogue,schema_catalogue_ref:String(catalogue.catalogue_ref||req.schema_catalogue_ref||''),schema_catalogue_approved:decoded.status==='decoded',approved_keyword_schemas:Array.isArray(catalogue.schemas)?catalogue.schemas:[],temporal_policy:temporalPolicy,initial_semantic_snapshot:req.initial_semantic_snapshot||null,render_result:{status:'baseline_decoded',catalogue_hash:decoded.catalogue_hash||null,rendered_records:[]}}}}];
"""


PREPARE_BASELINE_PLANNING_QUERY = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,intake=$('Run deterministic SCHEDULE intake').first().json,decoded=$('Decode typed baseline records').first().json,req=root.request;
const arr=Array.isArray,obj=v=>v&&typeof v==='object'&&!arr(v),clean=v=>typeof v==='string'?v.trim():'';
const change=obj(req.requested_change_scope)?req.requested_change_scope:{},entries=Object.values(change).flatMap(v=>arr(v)?v:[]).filter(obj);
const keywords=[...new Set([...(arr(intake.requested_keyword_scope)?intake.requested_keyword_scope:[]),...entries.map(v=>v.keyword)].map(v=>clean(v).toUpperCase()).filter(Boolean))];
const explicit=obj(req.baseline_query_filters)?req.baseline_query_filters:{};
return[{json:{baseline_query_request:{baseline_decode_result:decoded,expected_decoded_hash:decoded.decoded_hash,query:{purpose:'PLANNING',phase:'ALL',keywords,source_node_ids:arr(explicit.source_node_ids)?explicit.source_node_ids:[],file_refs:arr(explicit.file_refs)?explicit.file_refs:[],entity_values:arr(explicit.entity_values)?explicit.entity_values:[],field_filters:arr(explicit.field_filters)?explicit.field_filters:[],effective_from:explicit.effective_from||null,effective_to:explicit.effective_to||null,cursor:0,limit:250,summary_only:true,sample_limit:100,require_complete:false}}}}];
"""


PREPARE_BASELINE_BUILDER_QUERY = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,plan=$('Validate SCHEDULE pipeline plan').first().json,decoded=$('Decode typed baseline records').first().json,req=root.request;
const arr=Array.isArray,obj=v=>v&&typeof v==='object'&&!arr(v),clean=v=>typeof v==='string'?v.trim():'';
const explicit=obj(req.baseline_query_filters)?req.baseline_query_filters:{},change=obj(req.requested_change_scope)?req.requested_change_scope:{},entries=Object.values(change).flatMap(v=>arr(v)?v:[]).filter(obj);
const explicitEntities=arr(explicit.entity_values)?explicit.entity_values:entries.flatMap(v=>[v.entity,v.well,v.group,v.entity_id]).filter(Boolean);
const plannedEntities=(arr(plan.stages)?plan.stages:[]).flatMap(s=>arr(s.entity_scope)?s.entity_scope:[]).map(clean).filter(v=>v&&!/^(all|any|global|all wells|all groups|field-wide)$/i.test(v));
const entityValues=[...new Set((explicitEntities.length?explicitEntities:plannedEntities).map(v=>clean(String(v))).filter(Boolean))];
const explicitNodes=arr(explicit.source_node_ids)?explicit.source_node_ids:entries.flatMap(v=>[v.source_node_id,v.target_node_id,v.node_id]).filter(Boolean);
const temporal=(arr(plan.stages)?plan.stages:[]).flatMap(s=>arr(s.temporal_scope)?s.temporal_scope:[]),dates=temporal.flatMap(v=>String(v).match(/\b\d{4}-\d{2}-\d{2}\b/g)||[]).sort();
const requestedLimit=Number(req.baseline_query_limit||explicit.limit||2000),limit=Number.isInteger(requestedLimit)&&requestedLimit>0&&requestedLimit<=2000?requestedLimit:2000;
return[{json:{baseline_query_request:{baseline_decode_result:decoded,expected_decoded_hash:decoded.decoded_hash,query:{purpose:'BUILD',phase:'ALL',keywords:arr(plan.keyword_scope)?plan.keyword_scope:[],source_node_ids:explicitNodes,file_refs:arr(explicit.file_refs)?explicit.file_refs:[],entity_values:entityValues,field_filters:arr(explicit.field_filters)?explicit.field_filters:[],effective_from:explicit.effective_from||dates[0]||null,effective_to:explicit.effective_to||dates[dates.length-1]||null,cursor:0,limit,summary_only:false,require_complete:true}}}}];
"""


PREPARE_PLAN = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
const intake=$('Run deterministic SCHEDULE intake').first().json;
let baseline=null;try{baseline=$('Analyze lossless baseline inventory').first().json}catch{}
let decoded=null;try{decoded=$('Decode typed baseline records').first().json}catch{}
let replay=null;try{replay=$('Replay baseline prefix into semantic boundary').first().json}catch{}
let baselineQuery=null;try{baselineQuery=$('Query baseline planning context').first().json}catch{}
const req=root.request;const evidence=[];
if(req.source_facts_packet)evidence.push({kind:'source_facts_packet',value:req.source_facts_packet});
if(Array.isArray(req.source_facts))evidence.push(...req.source_facts.slice(0,1000));
if(Array.isArray(req.knowledge_evidence))evidence.push(...req.knowledge_evidence.slice(0,100));
if(req.rag_evidence)evidence.push({kind:'schedule_rag_evidence',value:req.rag_evidence});
// Raw package text/CST never enters the LLM prompt.  It remains in the
// deterministic branch and is handed only to the merger by immutable state.
const compactBaseline=baseline&&typeof baseline==='object'?{contract:baseline.contract,contract_version:baseline.contract_version,status:baseline.status,cst_version:baseline.cst_version,offset_unit:baseline.offset_unit,package_hash:baseline.package?.package_hash||null,compact_inventory:baseline.compact_inventory||null,file_manifest:baseline.file_manifest||[],include_graph:baseline.include_graph||{},keyword_inventory:baseline.keyword_inventory||{},opaque_keywords:baseline.opaque_keywords||[],findings:baseline.findings||[],preservation_token:baseline.preservation_token||null}:null;
const decodedInventory=baselineQuery&&typeof baselineQuery==='object'?{contract:baselineQuery.contract,status:baselineQuery.status,query_hash:baselineQuery.query_hash||null,decoded_hash:baselineQuery.decoded_hash||null,baseline_package_hash:baselineQuery.baseline_package_hash||null,catalogue_hash:baselineQuery.catalogue_hash||null,total_source_records:baselineQuery.total_source_records||0,total_matches:baselineQuery.total_matches||0,summary:baselineQuery.summary||{},samples:Array.isArray(baselineQuery.samples)?baselineQuery.samples:[],findings:baselineQuery.findings||[]}:null;
const semanticBoundary=replay&&replay.semantic_state_snapshot?{snapshot_kind:replay.semantic_state_snapshot.snapshot_kind,replay_through:replay.semantic_state_snapshot.replay_through,change_effective_from:replay.semantic_state_snapshot.change_effective_from,boundary_hash:replay.semantic_state_snapshot.boundary_hash,entity_count:replay.semantic_replay?.entities||0,state_assignment_count:replay.semantic_replay?.state_assignments||0}:null;
const plannerRequest={task:{objective:intake.objective},build_mode:intake.build_mode,requested_keyword_scope:intake.requested_keyword_scope,requested_change_scope:req.requested_change_scope||{},baseline_analysis:compactBaseline,decoded_baseline_inventory:decodedInventory,semantic_boundary:semanticBoundary,evidence,simulator_profile:intake.simulator_profile,preservation_policy:intake.build_mode==='REVISE'?'preserve_unmentioned':'not_applicable'};
return[{json:{planner_request:plannerRequest,planner_input:JSON.stringify(plannerRequest)}}];
"""


PREPARE_BUILD = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
const intake=$('Run deterministic SCHEDULE intake').first().json;
const plan=$('Validate SCHEDULE pipeline plan').first().json;
let baseline=null;try{baseline=$('Analyze lossless baseline inventory').first().json}catch{}
let decoded=null;try{decoded=$('Decode typed baseline records').first().json}catch{}
let replay=null;try{replay=$('Replay baseline prefix into semantic boundary').first().json}catch{}
let baselineQuery=null;try{baselineQuery=$('Query targeted baseline records').first().json}catch{}
const req=root.request;
const compactBaseline=baseline&&typeof baseline==='object'?{contract:baseline.contract,contract_version:baseline.contract_version,status:baseline.status,cst_version:baseline.cst_version,offset_unit:baseline.offset_unit,package_hash:baseline.package?.package_hash||null,compact_inventory:baseline.compact_inventory||null,file_manifest:baseline.file_manifest||[],include_graph:baseline.include_graph||{},keyword_inventory:baseline.keyword_inventory||{},opaque_keywords:baseline.opaque_keywords||[],findings:baseline.findings||[],preservation_token:baseline.preservation_token||null}:null;
const decodedInventory=baselineQuery&&typeof baselineQuery==='object'?{contract:baselineQuery.contract,status:baselineQuery.status,query_hash:baselineQuery.query_hash||null,decoded_hash:baselineQuery.decoded_hash||null,baseline_package_hash:baselineQuery.baseline_package_hash||null,catalogue_hash:baselineQuery.catalogue_hash||null,total_source_records:baselineQuery.total_source_records||0,total_matches:baselineQuery.total_matches||0,summary:baselineQuery.summary||{},records:Array.isArray(baselineQuery.records)?baselineQuery.records:[],findings:baselineQuery.findings||[]}:null;
const semanticBoundary=replay&&replay.semantic_state_snapshot?replay.semantic_state_snapshot:null;
const payload={schedule_request:req,intake_result:intake,approved_plan:plan,baseline_analysis:compactBaseline,decoded_baseline_inventory:decodedInventory,semantic_boundary:semanticBoundary,source_facts:req.source_facts??[],source_facts_packet:req.source_facts_packet??null,knowledge_evidence:req.knowledge_evidence??[],rag_evidence:req.rag_evidence??null,instruction:'Return a typed draft or an exact evidence_gap. Do not bypass deterministic validation.'};
return[{json:{builder_context:payload,builder_input:JSON.stringify(payload)}}];
"""


def validate_builder(keywords: list[str]) -> str:
    allowed = json.dumps(keywords, ensure_ascii=False)
    return r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
const req=root.request,plan=$('Validate SCHEDULE pipeline plan').first().json;
let work=$json.output??$json;if(typeof work==='string'){try{work=JSON.parse(work)}catch{work={}}}
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const allowed=new Set(__KEYWORDS__),findings=[];
const mode=clean(work.build_mode||plan.build_mode).toUpperCase();
const scope=[...new Set((arr(plan.keyword_scope)?plan.keyword_scope:[]).map(v=>clean(v).toUpperCase()).filter(Boolean))];
const changes=arr(work.changes)?work.changes.filter(obj):[],gaps=arr(work.evidence_gap)?work.evidence_gap.filter(obj):[];
const irEvents=arr(work.ir_events)?work.ir_events.filter(obj):[];
const requirements=arr(work.requirements_matrix)?work.requirements_matrix.filter(obj):[],sourceMap=arr(work.source_map)?work.source_map.filter(obj):[];
const evidencePacket=obj(req.source_facts_packet)?req.source_facts_packet:{},facts=arr(evidencePacket.facts)?evidencePacket.facts:(arr(req.source_facts)?req.source_facts:[]),conflicts=arr(evidencePacket.conflicts)?evidencePacket.conflicts:[];
const rag=obj(req.rag_evidence)?req.rag_evidence:{},citations=arr(rag.citations)?rag.citations.filter(obj).slice(0,100):[],ragResults=arr(rag.results)?rag.results.filter(obj):[];
const tags=v=>{const m=obj(v.metadata)?v.metadata:{},raw=v.keyword_families??v.keyword_family??v.keyword??m.keyword_families??m.keyword_family??m.keyword??[];if(arr(raw))return raw.map(x=>clean(x).toUpperCase()).filter(Boolean);if(typeof raw==='string'){try{const p=JSON.parse(raw);if(arr(p))return p.map(x=>clean(x).toUpperCase()).filter(Boolean)}catch{}return raw.split(/[,;|\s]+/).map(x=>clean(x).toUpperCase()).filter(Boolean)}return[]};
const citedKeywords=new Set([...ragResults,...citations].flatMap(tags).filter(k=>allowed.has(k)));
const approval=obj(req.remove_approval)?req.remove_approval:{},accountableRemove=req.explicit_remove_approved===true&&clean(approval.actor)&&clean(approval.reason)&&clean(approval.gate_id);
const operations=irEvents.length?irEvents:changes;
let baselineQuery=null;try{baselineQuery=$('Query targeted baseline records').first().json}catch{}
const queriedRecords=arr(baselineQuery?.records)?baselineQuery.records.filter(obj):[],targetMap=new Map();for(const r of queriedRecords){const id=clean(r.target_node_id||r.source_node_id);if(id&&!targetMap.has(id))targetMap.set(id,r)}
if(!['CREATE','REVISE'].includes(mode))findings.push({code:'INVALID_BUILD_MODE',severity:'error'});
if(mode==='REVISE'&&work.preservation_report?.policy!=='preserve_unmentioned')findings.push({code:'PRESERVATION_POLICY_REQUIRED',severity:'error'});
if(mode==='REVISE'&&baselineQuery?.status!=='succeeded')findings.push({code:'TARGETED_BASELINE_QUERY_REQUIRED',severity:'error',status:baselineQuery?.status||'missing'});
for(const c of operations){const op=clean(c.operation).toUpperCase(),kw=clean(c.keyword).toUpperCase();if(!['KEEP','MODIFY','ADD','REMOVE'].includes(op))findings.push({code:'INVALID_CHANGE_OPERATION',severity:'error',operation:op});if(!allowed.has(kw))findings.push({code:'UNSUPPORTED_KEYWORD',severity:'error',keyword:kw});if(op==='REMOVE'&&!accountableRemove)findings.push({code:'REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL',severity:'error',keyword:kw});if(mode==='REVISE'&&['MODIFY','REMOVE'].includes(op)){const id=clean(c.target_node_id||c.source_node_id||c.node_id),hit=targetMap.get(id);if(!id||!hit)findings.push({code:'CHANGE_TARGET_OUTSIDE_BASELINE_QUERY',severity:'error',keyword:kw,target_node_id:id||null});else if(clean(c.expected_raw_hash).toLowerCase()!==clean(hit.expected_raw_hash).toLowerCase())findings.push({code:'CHANGE_TARGET_HASH_MISMATCH',severity:'error',keyword:kw,target_node_id:id})}}
const nonKeep=operations.filter(c=>clean(c.operation).toUpperCase()!=='KEEP'),represented=new Set([...operations,...requirements].map(v=>clean(v.keyword).toUpperCase()).filter(Boolean));
if((obj(req.schema_catalogue)||obj(req.rag_evidence?.schema_catalogue))&&!irEvents.length&&changes.length)findings.push({code:'TYPED_IR_REQUIRED_FOR_CATALOGUE_RENDER',severity:'error'});
if(mode==='CREATE'&&['succeeded','partial'].includes(work.status)&&!requirements.length)findings.push({code:'REQUIRED_DATA_MATRIX_MISSING',severity:'error'});
if(nonKeep.length&&sourceMap.length<nonKeep.length)findings.push({code:'SOURCE_MAP_INCOMPLETE',severity:'error',required:nonKeep.length,mapped:sourceMap.length});
const missingCitationScope=scope.filter(k=>!citedKeywords.has(k));if(missingCitationScope.length)findings.push({code:'RAG_KEYWORD_COVERAGE_MISSING',severity:'error',keywords:missingCitationScope});
if(conflicts.length)findings.push({code:'CONFLICTING_SOURCE_FACTS',severity:'error',count:conflicts.length});
const modelDecision=obj(work.decision_record)?work.decision_record:{};if(modelDecision.contract!=='decision_record'||modelDecision.contract_version!=='1.0'||!clean(modelDecision.objective)||!obj(modelDecision.selected_action)||!arr(modelDecision.selected_action.reason_codes))findings.push({code:'DECISION_RECORD_INVALID',severity:'error'});
let status=new Set(['succeeded','partial','needs_input','needs_decision','needs_approval','retryable_error','fatal_error']).has(work.status)?work.status:'retryable_error';
if(gaps.length&&['succeeded','partial'].includes(status))status='needs_input';
if(findings.length&&['succeeded','partial'].includes(status))status=findings.some(f=>f.code==='REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL')?'needs_approval':'needs_input';
if(['succeeded','partial'].includes(status)&&(!work.self_check?.performed||!work.self_check?.passed))status='retryable_error';
const supported=r=>['supported','covered','resolved','approved'].includes(clean(r.status).toLowerCase())||clean(r.source_ref)||clean(r.fact_id)||(arr(r.source_refs)&&r.source_refs.length>0);
const required=requirements.filter(r=>r.required!==false),requiredSupported=required.filter(supported).length;
const requirementCoverage=required.length?Math.round(100*requiredSupported/required.length):(nonKeep.length?Math.min(100,Math.round(100*sourceMap.length/nonKeep.length)):100);
const sourceCoverage=nonKeep.length?Math.min(100,Math.round(100*sourceMap.length/nonKeep.length)):100;
const scopeFit=scope.length?Math.round(100*scope.filter(k=>represented.has(k)).length/scope.length):0;
const evidenceCompleteness=Math.round(.6*requirementCoverage+.4*sourceCoverage);
const sourceAuthority=scope.length?Math.round(100*scope.filter(k=>citedKeywords.has(k)).length/scope.length):0;
const entityTemporalConsistency=(gaps.length||conflicts.length)?0:100;
const deterministicValidationHealth=findings.length?0:(work.self_check?.performed&&work.self_check?.passed?100:50);
const score=Math.round(.25*scopeFit+.25*evidenceCompleteness+.20*sourceAuthority+.15*entityTemporalConsistency+.15*deterministicValidationHealth);
const hardBlockers=findings.filter(f=>f.severity==='error').map(f=>f.code),decision=hardBlockers.length||score<70?'hitl':score<85?'attention':'continue';
const snapshot=clean(evidencePacket.source_snapshot_hash||req.source_snapshot_hash)||'none',signature=gaps.map(g=>[g.entity,g.effective_at,g.keyword,g.field,g.reason].map(v=>String(v||'')).join('|')).sort().join('||').slice(0,8000);
const hash=s=>{let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return(h>>>0).toString(16)};
const reasonCodes=hardBlockers.length?hardBlockers:[decision==='continue'?'READINESS_CONTINUE':decision==='attention'?'READINESS_ATTENTION':'READINESS_HITL'];
const decisionRecord={contract:'decision_record',contract_version:'1.0',objective:clean(root.packet?.objective||req.objective),considered_inputs:[{kind:'approved_plan',contract:plan.contract||null,keyword_scope:scope},{kind:'source_snapshot',source_snapshot_hash:snapshot,fact_count:facts.length,conflict_count:conflicts.length},{kind:'rag_evidence',citation_count:citations.length,covered_keywords:[...citedKeywords].sort()},{kind:'baseline',package_hash:req.baseline_manifest_hash||req.baseline_schedule_package_ref?.manifest_hash||null}],proposed_actions:operations.slice(0,100).map(c=>({operation:clean(c.operation).toUpperCase(),keyword:clean(c.keyword).toUpperCase(),target_node_id:c.target_node_id||c.node_id||null})),selected_action:{action:status,reason_codes:reasonCodes},rejected_actions:findings.map(f=>({action:f.keyword||f.operation||'builder_output',reason_codes:[f.code]})),assumptions:arr(work.assumptions)?work.assumptions.map(String).slice(0,100):[],evidence_refs:arr(work.evidence)?work.evidence.filter(obj).slice(0,100):[],citations,tool_call_ids:arr(modelDecision.tool_call_ids)?modelDecision.tool_call_ids.map(String).slice(0,100):[],unresolved_questions:gaps.slice(0,100),acceptance_check_results:[{check:'scope_fit',score:scopeFit,passed:scopeFit===100},{check:'evidence_completeness',score:evidenceCompleteness,passed:evidenceCompleteness===100},{check:'source_authority_and_citation',score:sourceAuthority,passed:sourceAuthority===100},{check:'entity_temporal_consistency',score:entityTemporalConsistency,passed:entityTemporalConsistency===100},{check:'deterministic_validation_health',score:deterministicValidationHealth,passed:deterministicValidationHealth===100}]};
return[{json:{contract:'schedule_builder_stage_result',contract_version:'1.0',status,summary:String(work.summary||'').slice(0,4000),build_mode:mode,generated_schedule:typeof work.generated_schedule==='string'?work.generated_schedule.slice(0,200000):'',ir_events:irEvents,changes,requirements_matrix:requirements,source_map:sourceMap,completeness_report:obj(work.completeness_report)?work.completeness_report:{},preservation_report:obj(work.preservation_report)?work.preservation_report:{},evidence_gap:gaps,deliverables:arr(work.deliverables)?work.deliverables:[],artifact_refs:arr(work.artifact_refs)?work.artifact_refs:[],assumptions:arr(work.assumptions)?work.assumptions:[],warnings:arr(work.warnings)?work.warnings:[],evidence:arr(work.evidence)?work.evidence:[],decision_record:decisionRecord,self_check:obj(work.self_check)?work.self_check:{performed:false,passed:false,checks:[],reproducibility:''},human_request:work.human_request??null,error:findings.length?{code:'BUILDER_POLICY_GATE',findings}:work.error??null,hard_blockers:hardBlockers,score:{stage_score:score,components:{scope_fit:scopeFit,evidence_completeness:evidenceCompleteness,source_authority_and_citation:sourceAuthority,entity_temporal_consistency:entityTemporalConsistency,deterministic_validation_health:deterministicValidationHealth},raw_counts:{scope_required:scope.length,scope_represented:scope.filter(k=>represented.has(k)).length,requirements_required:required.length,requirements_supported:requiredSupported,non_keep_changes:nonKeep.length,source_map_entries:sourceMap.length,citations:citations.length,source_conflicts:conflicts.length,evidence_gaps:gaps.length,findings:findings.length},thresholds:{attention:85,hitl:70},decision,provisional:true},continuation:gaps.length?{protocol:'schedule-builder-evidence-gap-v1',gap_signature:hash(signature),source_snapshot_hash:snapshot,evidence_gap:gaps,max_excel_iterations:Math.min(5,Math.max(1,Number(req.max_excel_iterations)||2)),max_builder_iterations:Math.min(5,Math.max(1,Number(req.max_builder_iterations)||3))}:work.continuation??null}}];
""".replace("__KEYWORDS__", allowed).strip()


PREPARE_RENDER = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,intake=$('Run deterministic SCHEDULE intake').first().json,b=$('Validate SCHEDULE builder stage').first().json;
const catalogue=root.request.schema_catalogue||root.request.rag_evidence?.schema_catalogue||{};
return[{json:{schedule_render_request:{mode:intake.build_mode,schema_catalogue:catalogue,ir_events:b.ir_events||[]}}}];
"""


PREPARE_MERGE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,intake=$('Run deterministic SCHEDULE intake').first().json,r=$('Render typed SCHEDULE IR deterministically').first().json;
let baseline=null;try{baseline=$('Analyze lossless baseline inventory').first().json}catch{}
return[{json:{merge_request:{mode:intake.build_mode,baseline_schedule_text:String(root.request.baseline_schedule_text||''),include_files:Array.isArray(root.request.include_files)?root.request.include_files:[],baseline_analysis:baseline,changes:r.changes||[],schema_render_result:r,explicit_remove_approved:root.request.explicit_remove_approved===true,remove_approval:root.request.remove_approval||null}}}];
"""


PREPARE_VALIDATE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,m=$('Merge SCHEDULE draft deterministically').first().json;
const r=$('Render typed SCHEDULE IR deterministically').first().json;
const b=$('Validate SCHEDULE builder stage').first().json;
let replay=null;try{replay=$('Replay baseline prefix into semantic boundary').first().json}catch{}
const catalogue=root.request.schema_catalogue||root.request.rag_evidence?.schema_catalogue||{};
const temporalPolicy=root.request.temporal_policy||{history_end:root.request.history_end||null,forecast_start:root.request.forecast_start||null};
const generatedBoundary=b.build_mode==='REVISE'?replay?.semantic_state_snapshot:null;
return[{json:{schedule_validation_request:{validation_phase:'CANDIDATE',mode:b.build_mode,build_mode:b.build_mode,schedule_text:m.generated_schedule,output_package:m.output_package,render_result:r,ir_events:b.ir_events||[],simulator_profile:root.request.simulator_profile||{},schema_catalogue:catalogue,schema_catalogue_ref:String(catalogue.catalogue_ref||root.request.schema_catalogue_ref||''),schema_catalogue_approved:r.status==='rendered',approved_keyword_schemas:Array.isArray(catalogue.schemas)?catalogue.schemas:[],temporal_policy:temporalPolicy,initial_semantic_snapshot:b.build_mode==='CREATE'?(root.request.initial_semantic_snapshot||null):null,semantic_baseline_snapshot:generatedBoundary,baseline_package_hash:m.baseline_package_hash||null}}}];
"""


PREPARE_VERIFY = r"""
const b=$('Validate SCHEDULE builder stage').first().json,r=$('Render typed SCHEDULE IR deterministically').first().json,m=$('Merge SCHEDULE draft deterministically').first().json,v=$('Validate merged SCHEDULE package').first().json;
return[{json:{builder_result:b,render_result:r,merge_result:m,validation_result:v}}];
"""


FINAL_RESULT = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,b=$('Validate SCHEDULE builder stage').first().json,r=$('Render typed SCHEDULE IR deterministically').first().json,m=$('Merge SCHEDULE draft deterministically').first().json,v=$('Validate merged SCHEDULE package').first().json,review=$json;
const plan=$('Validate SCHEDULE pipeline plan').first().json;
const score=Math.min(Number(b.score?.stage_score||0),Number(v.score?.stage_score||0),Number(review.score?.stage_score||0));
const generated=String(m.generated_schedule||'').slice(0,200000);const hash=s=>{let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return(h>>>0).toString(16).padStart(8,'0')};
const artifactRefs=Array.isArray(b.artifact_refs)?b.artifact_refs.filter(x=>x&&typeof x==='object'):[];if(generated)artifactRefs.push({ref:`inline-schedule://${root.task_id}/${hash(generated)}`,kind:'schedule-draft-inline',revision:`fnv1a32:${hash(generated)}`,description:'Content-addressed SCHEDULE draft carried in the specialist result; replace with governed artifact storage before production.'});
const reviewDecision=review.decision_record??{contract:'decision_record',contract_version:'1.0',objective:'Independently verify the deterministic SCHEDULE draft.',considered_inputs:[{kind:'builder_result',status:b.status},{kind:'merge_result',status:m.status},{kind:'validation_result',status:v.status}],proposed_actions:[{action:'pass'},{action:'pass_with_warnings'},{action:'reject'}],selected_action:{action:String(review.verdict||'reject'),reason_codes:(review.required_corrections||[]).length?review.required_corrections:[review.can_release?'VERIFIER_PASS':'VERIFIER_NOT_RELEASE_READY']},rejected_actions:[],assumptions:[],evidence_refs:[],citations:[],tool_call_ids:[],unresolved_questions:[],acceptance_check_results:[{check:'merge',passed:m.status==='merged'},{check:'validation',passed:v.status==='valid'},{check:'independent_verifier',passed:review.verdict==='pass'}]};
const gateDecisions=[{stage:'plan',decision:plan.score?.decision||'hitl',score:plan.score?.stage_score??null,reason_codes:plan.decision_record?.selected_action?.reason_codes||plan.hard_blockers||[]},{stage:'builder',decision:b.score?.decision||'hitl',score:b.score?.stage_score??null,reason_codes:b.decision_record?.selected_action?.reason_codes||b.hard_blockers||[]},{stage:'render',decision:r.status==='rendered'?'continue':'hitl',score:r.status==='rendered'?100:0,reason_codes:r.hard_blockers||[]},{stage:'validation',decision:v.score?.gate||'hitl',score:v.score?.stage_score??null,reason_codes:v.hard_blockers||[]},{stage:'verification',decision:review.can_release?'continue':'hitl',score:review.score?.stage_score??null,reason_codes:reviewDecision.selected_action.reason_codes}];
let replay=null;try{replay=$('Replay baseline prefix into semantic boundary').first().json}catch{}
const trace=[{stage:'intake',status:'accepted'},{stage:'baseline',status:b.build_mode==='REVISE'?'decoded_and_replayed':'not_applicable',score:replay?.score||null,findings:replay?.findings||[]},{stage:'plan',status:plan.status,score:plan.score,decision_record:plan.decision_record},{stage:'builder',status:b.status,score:b.score,decision_record:b.decision_record},{stage:'render',status:r.status,score:{stage_score:r.status==='rendered'?100:0},findings:r.findings},{stage:'merge',status:m.status},{stage:'validate',status:v.status,score:v.score},{stage:'verify',status:review.verdict,score:review.score,decision_record:reviewDecision}];
return[{json:{specialist_result:{contract:'specialist_result',contract_version:'1.0',task_id:root.task_id,specialist_id:'schedule_builder_specialist',attempt:root.attempt,status:review.can_release?'succeeded':'needs_approval',summary:review.can_release?'SCHEDULE draft passed bounded pipeline gates and is ready for accountable human release.':'SCHEDULE draft did not pass every release prerequisite.',deliverables:[{kind:'schedule_draft',description:'Validated SCHEDULE draft; not approved until the orchestrator human release gate.',inline_preview:generated.slice(0,20000)}],artifact_refs:artifactRefs,compact_data:{build_mode:b.build_mode,generated_schedule:generated,plan,render_result:r,merge_result:m,validation_result:v,schedule_verifier_result:review,preservation_report:m.preservation_report,semantic_diff:m.semantic_diff,requirements_matrix:b.requirements_matrix,source_map:b.source_map,completeness_report:b.completeness_report,decision_records:[plan.decision_record,b.decision_record,reviewDecision].filter(Boolean),gate_decisions:gateDecisions,stage_scores:trace.filter(x=>x.score).map(x=>({stage:x.stage,...x.score})),overall_score:score,trace_summary:trace,release_ready:review.can_release},assumptions:b.assumptions,warnings:b.warnings,evidence:b.evidence,self_check:{performed:true,passed:Boolean(review.can_release),checks:[{check:'schema_render',passed:r.status==='rendered'},{check:'merge',passed:m.status==='merged'},{check:'validation',passed:v.status==='valid'},{check:'independent_schedule_verifier',passed:review.verdict==='pass'}],reproducibility:'Replay the immutable input packet, approved schema catalogue, evidence snapshot and typed IR.'},human_request:review.can_release?null:{kind:'needs_approval',questions:review.required_corrections||[]},error:review.can_release?null:{code:'SCHEDULE_PIPELINE_NOT_RELEASE_READY',findings:review.findings||[]},continuation:null}}}];
"""


GATE_RESULT = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,x=$json||{};
const findings=Array.isArray(x.findings)?x.findings:(Array.isArray(x.error?.findings)?x.error.findings:[]),gaps=Array.isArray(x.evidence_gap)?x.evidence_gap:[];
let status=['needs_input','needs_decision','needs_approval','retryable_error','fatal_error'].includes(x.status)?x.status:'needs_input';
if(x.contract==='schedule_verifier_result'&&x.verdict==='pass_with_warnings')status='needs_approval';
if(x.contract==='schedule_builder_stage_result'&&Array.isArray(x.evidence_gap)&&x.evidence_gap.length)status='needs_input';
const normalizedFindings=findings.length?findings:(Array.isArray(root.packet_findings)?root.packet_findings:[]),reasonCodes=normalizedFindings.map(f=>String(f.code||'SCHEDULE_PIPELINE_GATE'));
const questions=gaps.length?gaps.map((g,i)=>({id:`schedule_gap_${i+1}`,question:String(g.question||`${g.keyword||'SCHEDULE'} ${g.entity||''} ${g.field||''}: ${g.reason||'required evidence is missing'}`).trim(),expected_format:String(g.expected_format||'value with units and provenance'),required:true})):normalizedFindings.map((f,i)=>({id:`schedule_finding_${i+1}`,question:String(f.message||f.code||'Resolve the SCHEDULE pipeline finding.'),required:true}));
const decisionRecord=x.decision_record??{contract:'decision_record',contract_version:'1.0',objective:String(root.packet?.objective||root.request?.objective||'Resolve the failed SCHEDULE stage.'),considered_inputs:[{kind:'failed_stage',contract:String(x.contract||'unknown'),status:String(x.status||x.verdict||'blocked')}],proposed_actions:[{action:'request_targeted_input'},{action:'retry_with_new_snapshot'}],selected_action:{action:status,reason_codes:reasonCodes.length?reasonCodes:['SCHEDULE_PIPELINE_GATE']},rejected_actions:[],assumptions:[],evidence_refs:[],citations:[],tool_call_ids:[],unresolved_questions:questions,acceptance_check_results:[]};
return[{json:{specialist_result:{contract:'specialist_result',contract_version:'1.0',task_id:root.task_id||'unknown',specialist_id:'schedule_builder_specialist',attempt:root.attempt||1,status,summary:String(x.summary||x.rationale||'SCHEDULE pipeline requires additional evidence or a controlled decision.').slice(0,4000),deliverables:[],artifact_refs:[],compact_data:{failed_stage:String(x.contract||'unknown'),findings:normalizedFindings,evidence_gap:gaps,score:x.score||null,decision_record:decisionRecord,gate_decisions:[{stage:String(x.contract||'unknown'),decision:'hitl',score:x.score?.stage_score??null,reason_codes:decisionRecord.selected_action.reason_codes}],trace_summary:[{stage:String(x.contract||'unknown'),status,score:x.score||null,decision_record:decisionRecord}]},assumptions:[],warnings:[],evidence:[],self_check:{performed:false,passed:false,checks:[],reproducibility:'Resume with the same task and a new versioned evidence snapshot.'},human_request:{kind:status,questions},error:x.error||{code:'SCHEDULE_PIPELINE_GATE',findings:normalizedFindings},continuation:x.continuation||null}}}];
"""


INVALID_RESULT = """const x=$json,findings=Array.isArray(x.packet_findings)?x.packet_findings:[];const ragMissing=findings.some(f=>f.code==='SCHEDULE_RAG_EVIDENCE_REQUIRED'),status=ragMissing?'needs_input':'fatal_error',code=ragMissing?'SCHEDULE_RAG_EVIDENCE_REQUIRED':'INVALID_SCHEDULE_SPECIALIST_PACKET',questions=ragMissing?[{id:'rag_evidence',question:'Load approved tNavigator 22.2 knowledge and provide authorized cited retrieval evidence.',required:true}]:[];const decisionRecord={contract:'decision_record',contract_version:'1.0',objective:'Validate the SCHEDULE specialist request before any model or tool call.',considered_inputs:[{kind:'specialist_packet',task_id:x.task_id||'unknown',packet_valid:false}],proposed_actions:[{action:'reject_invalid_packet'}],selected_action:{action:status,reason_codes:[code]},rejected_actions:[],assumptions:[],evidence_refs:[],citations:[],tool_call_ids:[],unresolved_questions:questions,acceptance_check_results:[{check:'packet_contract',passed:false}]};return[{json:{specialist_result:{contract:'specialist_result',contract_version:'1.0',task_id:x.task_id||'unknown',specialist_id:'schedule_builder_specialist',attempt:x.attempt||1,status,summary:ragMissing?'Approved tNavigator 22.2 RAG evidence with complete citations is required before SCHEDULE planning.':'Invalid specialist_packet v1.0 for SCHEDULE pipeline.',deliverables:[],artifact_refs:[],compact_data:{findings,decision_record:decisionRecord,gate_decisions:[{stage:'intake',decision:'hitl',score:0,reason_codes:[code]}],trace_summary:[{stage:'intake',status,decision_record:decisionRecord}]},assumptions:[],warnings:[],evidence:[],self_check:{performed:false,passed:false,checks:[],reproducibility:''},human_request:ragMissing?{kind:'needs_input',questions}:null,error:{code,findings},continuation:null}}}];"""


def build_schedule_pipeline(*, node, note, code, trigger, ifnode, connect, workflow, keywords, planner_schema, planner_system, intake_js, baseline_js, baseline_decode_js, baseline_query_js, plan_validate_js, render_js, merge_js, validate_js, verify_js):
    example = {"specialist_packet": {"contract": "specialist_packet", "contract_version": "1.0", "task_id": "eng_example", "specialist_id": "schedule_builder_specialist", "attempt": 1, "objective": "Create forecast SCHEDULE", "inputs": {"schedule_request": {"build_mode": "CREATE", "simulator_profile": {"vendor": "Rock Flow Dynamics", "simulator": "tNavigator", "version": "22.2", "unit_system": "METRIC"}, "requested_keyword_scope": ["DATES", "WCONPROD"], "source_facts": [], "rag_evidence": {"contract": "schedule_rag_evidence", "contract_version": "1.0", "citations": [{"document_id": "tnav-22.2", "document_revision": "22.2", "source_hash": "sha256:replace", "page": "replace", "heading": "SCHEDULE"}], "results": []}, "schema_catalogue": {"contract": "schedule_schema_catalogue", "contract_version": "1.0", "catalogue_ref": "catalogue://tnavigator/22.2/replace", "catalogue_hash": "sha256:replace", "source_hash": "sha256:replace", "simulator_profile": {"vendor": "Rock Flow Dynamics", "simulator": "tNavigator", "version": "22.2"}, "approved": False, "approved_by": "", "approval_gate_id": "", "schemas": []}}}, "controls": {"preservation_policy": "preserve_unmentioned"}, "acceptance_criteria": [], "artifact_refs": []}}
    nodes = [
        note("SCHEDULE pipeline README", (-1200, -760), "## Governed SCHEDULE pipeline — n8n 2.30.8\n\nVisible bounded CREATE/REVISE specialist: intake → lossless baseline analysis → catalogue decode → pre-change semantic replay → planning summary → plan → targeted mutation-safe baseline query → typed IR → deterministic render → merge → candidate replay/validation → independent review.\n\nIt never calls Excel and owns no durable state. Bind this workflow in Universal Orchestrator. Without an accountable machine-readable 22.2 catalogue it stops before planning a REVISE or rendering any candidate.", 660, 480),
        trigger("Receive specialist packet", (-1200, -100), example),
        code("Normalize SCHEDULE pipeline packet", (-980, -100), NORMALIZE),
        ifnode("SCHEDULE packet valid?", (-760, -100), "={{ $json.packet_valid }}"),
        code("Prepare deterministic intake", (-540, -260), PREPARE_INTAKE),
        code("Run deterministic SCHEDULE intake", (-320, -260), intake_js),
        ifnode("SCHEDULE intake accepted?", (-100, -260), "={{ $json.status }}", "accepted", "string"),
        ifnode("REVISE needs baseline analysis?", (120, -260), "={{ $json.build_mode }}", "REVISE", "string"),
        code("Prepare baseline analysis", (340, -400), PREPARE_BASELINE),
        code("Analyze lossless baseline inventory", (560, -400), baseline_js),
        ifnode("Baseline analysis accepted?", (780, -400), "={{ $json.status }}", "analyzed", "string"),
        code("Prepare catalogue baseline decode", (1000, -400), PREPARE_BASELINE_DECODE),
        code("Decode typed baseline records", (1220, -400), baseline_decode_js),
        ifnode("Baseline decode accepted?", (1440, -400), "={{ $json.status }}", "decoded", "string"),
        code("Prepare baseline prefix replay", (1660, -400), PREPARE_BASELINE_REPLAY),
        code("Replay baseline prefix into semantic boundary", (1880, -400), validate_js),
        ifnode("Semantic boundary accepted?", (2100, -400), "={{ $json.status }}", "valid", "string"),
        code("Prepare baseline planning query", (2320, -400), PREPARE_BASELINE_PLANNING_QUERY),
        code("Query baseline planning context", (2540, -400), baseline_query_js),
        ifnode("Baseline planning context accepted?", (2760, -400), "={{ $json.status }}", "succeeded", "string"),
        code("Prepare SCHEDULE pipeline plan", (2980, -100), PREPARE_PLAN),
        node("SCHEDULE Planner Agent", "@n8n/n8n-nodes-langchain.agent", 3.1, (3200, -100), {"promptType": "define", "text": "={{ $json.planner_input }}", "hasOutputParser": True, "needsFallback": False, "options": {"systemMessage": planner_system, "maxIterations": 6, "returnIntermediateSteps": True, "enableStreaming": False}}),
        node("SCHEDULE Planner Chat Model — configure in UI", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, (3100, -360), {"model": {"mode": "id", "value": "gpt-4.1-nano"}, "options": {"maxTokens": 3500, "temperature": 0, "timeout": 120000, "maxRetries": 2}, "responsesApiEnabled": False}, credentials={"openAiApi": {"id": "REPLACE_IN_UI", "name": "REPLACE: schedule planner chat credential"}}),
        node("SCHEDULE Planner Structured Output", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3, (3320, -360), {"schemaType": "manual", "inputSchema": json.dumps(planner_schema), "autoFix": False}),
        code("Validate SCHEDULE pipeline plan", (3420, -100), plan_validate_js),
        ifnode("SCHEDULE plan ready?", (3640, -100), '={{ ["proposed"].includes($json.status) && $json.score.decision !== "hitl" }}'),
        ifnode("REVISE needs targeted baseline context?", (3860, -100), "={{ $json.build_mode }}", "REVISE", "string"),
        code("Prepare targeted baseline query", (4080, -260), PREPARE_BASELINE_BUILDER_QUERY),
        code("Query targeted baseline records", (4300, -260), baseline_query_js),
        ifnode("Targeted baseline context complete?", (4520, -260), "={{ $json.status }}", "succeeded", "string"),
        code("Prepare SCHEDULE builder stage", (4740, -100), PREPARE_BUILD),
        node("SCHEDULE Builder Agent", "@n8n/n8n-nodes-langchain.agent", 3.1, (4960, -100), {"promptType": "define", "text": "={{ $json.builder_input }}", "hasOutputParser": True, "needsFallback": False, "options": {"systemMessage": BUILDER_SYSTEM, "maxIterations": 8, "returnIntermediateSteps": True, "enableStreaming": False}}),
        node("SCHEDULE Builder Chat Model — configure in UI", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, (4860, -360), {"model": {"mode": "id", "value": "gpt-4.1-nano"}, "options": {"maxTokens": 6000, "temperature": 0, "timeout": 120000, "maxRetries": 2}, "responsesApiEnabled": False}, credentials={"openAiApi": {"id": "REPLACE_IN_UI", "name": "REPLACE: schedule builder chat credential"}}),
        node("SCHEDULE Builder Structured Output", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3, (5080, -360), {"schemaType": "manual", "inputSchema": json.dumps(BUILDER_SCHEMA), "autoFix": False}),
        code("Validate SCHEDULE builder stage", (5180, -100), validate_builder(keywords)),
        ifnode("Builder draft ready?", (5400, -100), '={{ ["succeeded","partial"].includes($json.status) && $json.score.decision !== "hitl" }}'),
        code("Prepare deterministic schema render", (5620, -100), PREPARE_RENDER),
        code("Render typed SCHEDULE IR deterministically", (5840, -100), render_js),
        ifnode("SCHEDULE schema render accepted?", (6060, -100), "={{ $json.status }}", "rendered", "string"),
        code("Prepare deterministic merge", (6280, -100), PREPARE_MERGE),
        code("Merge SCHEDULE draft deterministically", (6500, -100), merge_js),
        ifnode("SCHEDULE merge accepted?", (6720, -100), "={{ $json.status }}", "merged", "string"),
        code("Prepare SCHEDULE validation", (6940, -100), PREPARE_VALIDATE),
        code("Validate merged SCHEDULE package", (7160, -100), validate_js),
        ifnode("SCHEDULE validation passed?", (7380, -100), "={{ $json.status }}", "valid", "string"),
        code("Prepare independent SCHEDULE review", (7600, -100), PREPARE_VERIFY),
        code("Run independent SCHEDULE verifier", (7820, -100), verify_js),
        ifnode("SCHEDULE verifier passed?", (8040, -100), "={{ $json.verdict }}", "pass", "string"),
        code("Build release-ready specialist result", (8260, -100), FINAL_RESULT),
        code("Build SCHEDULE pipeline gate result", (6940, 220), GATE_RESULT),
        code("Build invalid SCHEDULE packet result", (-540, 120), INVALID_RESULT),
    ]
    connections = {}
    connect(connections, "Receive specialist packet", "Normalize SCHEDULE pipeline packet")
    connect(connections, "Normalize SCHEDULE pipeline packet", "SCHEDULE packet valid?")
    connect(connections, "SCHEDULE packet valid?", "Prepare deterministic intake", idx=0)
    connect(connections, "SCHEDULE packet valid?", "Build invalid SCHEDULE packet result", idx=1)
    connect(connections, "Prepare deterministic intake", "Run deterministic SCHEDULE intake")
    connect(connections, "Run deterministic SCHEDULE intake", "SCHEDULE intake accepted?")
    connect(connections, "SCHEDULE intake accepted?", "REVISE needs baseline analysis?", idx=0)
    connect(connections, "SCHEDULE intake accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "REVISE needs baseline analysis?", "Prepare baseline analysis", idx=0)
    connect(connections, "REVISE needs baseline analysis?", "Prepare SCHEDULE pipeline plan", idx=1)
    connect(connections, "Prepare baseline analysis", "Analyze lossless baseline inventory")
    connect(connections, "Analyze lossless baseline inventory", "Baseline analysis accepted?")
    connect(connections, "Baseline analysis accepted?", "Prepare catalogue baseline decode", idx=0)
    connect(connections, "Baseline analysis accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare catalogue baseline decode", "Decode typed baseline records")
    connect(connections, "Decode typed baseline records", "Baseline decode accepted?")
    connect(connections, "Baseline decode accepted?", "Prepare baseline prefix replay", idx=0)
    connect(connections, "Baseline decode accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare baseline prefix replay", "Replay baseline prefix into semantic boundary")
    connect(connections, "Replay baseline prefix into semantic boundary", "Semantic boundary accepted?")
    connect(connections, "Semantic boundary accepted?", "Prepare baseline planning query", idx=0)
    connect(connections, "Semantic boundary accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare baseline planning query", "Query baseline planning context")
    connect(connections, "Query baseline planning context", "Baseline planning context accepted?")
    connect(connections, "Baseline planning context accepted?", "Prepare SCHEDULE pipeline plan", idx=0)
    connect(connections, "Baseline planning context accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare SCHEDULE pipeline plan", "SCHEDULE Planner Agent")
    connect(connections, "SCHEDULE Planner Chat Model — configure in UI", "SCHEDULE Planner Agent", "ai_languageModel", 0, "ai_languageModel")
    connect(connections, "SCHEDULE Planner Structured Output", "SCHEDULE Planner Agent", "ai_outputParser", 0, "ai_outputParser")
    connect(connections, "SCHEDULE Planner Agent", "Validate SCHEDULE pipeline plan")
    connect(connections, "Validate SCHEDULE pipeline plan", "SCHEDULE plan ready?")
    connect(connections, "SCHEDULE plan ready?", "REVISE needs targeted baseline context?", idx=0)
    connect(connections, "SCHEDULE plan ready?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "REVISE needs targeted baseline context?", "Prepare targeted baseline query", idx=0)
    connect(connections, "REVISE needs targeted baseline context?", "Prepare SCHEDULE builder stage", idx=1)
    connect(connections, "Prepare targeted baseline query", "Query targeted baseline records")
    connect(connections, "Query targeted baseline records", "Targeted baseline context complete?")
    connect(connections, "Targeted baseline context complete?", "Prepare SCHEDULE builder stage", idx=0)
    connect(connections, "Targeted baseline context complete?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare SCHEDULE builder stage", "SCHEDULE Builder Agent")
    connect(connections, "SCHEDULE Builder Chat Model — configure in UI", "SCHEDULE Builder Agent", "ai_languageModel", 0, "ai_languageModel")
    connect(connections, "SCHEDULE Builder Structured Output", "SCHEDULE Builder Agent", "ai_outputParser", 0, "ai_outputParser")
    connect(connections, "SCHEDULE Builder Agent", "Validate SCHEDULE builder stage")
    connect(connections, "Validate SCHEDULE builder stage", "Builder draft ready?")
    connect(connections, "Builder draft ready?", "Prepare deterministic schema render", idx=0)
    connect(connections, "Builder draft ready?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare deterministic schema render", "Render typed SCHEDULE IR deterministically")
    connect(connections, "Render typed SCHEDULE IR deterministically", "SCHEDULE schema render accepted?")
    connect(connections, "SCHEDULE schema render accepted?", "Prepare deterministic merge", idx=0)
    connect(connections, "SCHEDULE schema render accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare deterministic merge", "Merge SCHEDULE draft deterministically")
    connect(connections, "Merge SCHEDULE draft deterministically", "SCHEDULE merge accepted?")
    connect(connections, "SCHEDULE merge accepted?", "Prepare SCHEDULE validation", idx=0)
    connect(connections, "SCHEDULE merge accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare SCHEDULE validation", "Validate merged SCHEDULE package")
    connect(connections, "Validate merged SCHEDULE package", "SCHEDULE validation passed?")
    connect(connections, "SCHEDULE validation passed?", "Prepare independent SCHEDULE review", idx=0)
    connect(connections, "SCHEDULE validation passed?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare independent SCHEDULE review", "Run independent SCHEDULE verifier")
    connect(connections, "Run independent SCHEDULE verifier", "SCHEDULE verifier passed?")
    connect(connections, "SCHEDULE verifier passed?", "Build release-ready specialist result", idx=0)
    connect(connections, "SCHEDULE verifier passed?", "Build SCHEDULE pipeline gate result", idx=1)
    return workflow(
        "tNavigator SCHEDULE Builder — governed CREATE/REVISE pipeline",
        "Visible intake, lossless catalogue decode, pre-change state replay, targeted baseline retrieval, plan, typed IR, deterministic render, merge, candidate validation and independent review. Durable state, Excel and release remain in the Orchestrator.",
        nodes,
        connections,
        "specialist_result/v1",
    )
