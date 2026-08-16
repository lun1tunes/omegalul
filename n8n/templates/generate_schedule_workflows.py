"""Generate runtime SCHEDULE workflows for n8n 2.30.8.

Emits only Knowledge Ingestion, Hybrid Retrieval, Builder and MAS Trace.
Stage algorithms live as Code nodes inside Builder; standalone diagnostic
mirrors are intentionally not generated.
"""
from __future__ import annotations
import json, uuid
from pathlib import Path
from schedule_pipeline import DECISION_RECORD_SCHEMA, build_schedule_pipeline
from schedule_rag_workflows import build_ingestion, build_retrieval
from schedule_lossless_runtime import build_baseline_js, build_merge_js
from schedule_baseline_decoder import build_baseline_decoder_js
from schedule_baseline_query import build_baseline_query_js
from schedule_intake_runtime import build_schedule_intake_js
from schedule_schema_runtime import build_schema_renderer_js
from schedule_semantic_runtime import build_schedule_validator_js
ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS=ROOT/'n8n'/'workflows'
CORE=WORKFLOWS/'core'
KEYWORDS=['DATES','INCLUDE','GRUPTREE','WELSPECS','WELLTRACK','COMPDATMD','WCONHIST','WCONPROD','WCONINJE','GCONPROD','GCONINJE','GUIDERAT','GSATPROD','GSATINJE','WELLSTRE','WINJGAS','GINJGAS','BRANPROP','NODEPROP','GNETDP','NETBALAN','FRACTURE_TEMPLATE','FRACTURE_SPECS','FRACTURE_STAGE','WECON','WTEST','WELTARG','WNETDP','WPIMULT','WDFAC','WEFAC','WELOPEN','WELDRAW','WLIST','WFRACP','WFRACPL','VFPPROD','WVFPDP','ACTIONX','DELAYACT','ENDACTIO','UDQ','UDT','APPLYSCRIPT']
def uid(name): return str(uuid.uuid5(uuid.NAMESPACE_URL,'omegalul/schedule-foundation/'+name))
def node(name,type_,version,pos,parameters,**extra):
 v={'parameters':parameters,'id':uid(name),'name':name,'type':type_,'typeVersion':version,'position':list(pos)};v.update(extra);return v
def note(name,pos,content,w=440,h=300): return node(name,'n8n-nodes-base.stickyNote',1,pos,{'content':content,'width':w,'height':h,'color':5})
def code(name,pos,js,**extra): return node(name,'n8n-nodes-base.code',2,pos,{'jsCode':js.strip()},**extra)
def set_fields(name,pos,fields):
 return node(name,'n8n-nodes-base.set',3.4,pos,{'assignments':{'assignments':[{'id':uid(name+'/field/'+str(i)),'name':field,'value':value,'type':type_} for i,(field,value,type_) in enumerate(fields,1)]},'options':{},'includeOtherFields':True})
def trigger(name,pos,example): return node(name,'n8n-nodes-base.executeWorkflowTrigger',1.2,pos,{'inputSource':'jsonExample','jsonExample':json.dumps(example,ensure_ascii=False)})
def ifnode(name,pos,left,right=True,value_type='boolean'):
 return node(name,'n8n-nodes-base.if',2.2,pos,{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},'conditions':[{'id':uid(name+'/condition'),'leftValue':left,'rightValue':right,'operator':{'type':value_type,'operation':'equals'}}],'combinator':'and'},'options':{}})
def connect(c,s,t,out='main',idx=0,itype='main',target_idx=0):
 a=c.setdefault(s,{}).setdefault(out,[])
 while len(a)<=idx:a.append([])
 a[idx].append({'node':t,'type':itype,'index':target_idx})
def workflow(name,description,nodes,c,contract): return {'id':uid(name),'name':name,'description':description,'nodes':nodes,'pinData':{},'connections':c,'active':False,'settings':{'executionOrder':'v1','saveManualExecutions':True,'callerPolicy':'workflowsFromSameOwner'},'versionId':uid(name+'/version'),'meta':{'templateCredsSetupCompleted':False,'targetN8nVersion':'2.30.8','contractVersion':contract},'tags':[]}
K=json.dumps(KEYWORDS)
INTAKE=f"""
const raw=$json.schedule_intake_request??$json;const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);const arr=Array.isArray;const clean=v=>typeof v==='string'?v.trim():'';const allowed=new Set({K});const request=obj(raw)?raw:{{}};const modeRaw=clean(request.build_mode||'AUTO').toUpperCase();const baseline=obj(request.baseline_schedule_package_ref)||clean(request.baseline_schedule_text);const mode=modeRaw==='AUTO'?(baseline?'REVISE':'CREATE'):modeRaw;const profile=obj(request.simulator_profile)?request.simulator_profile:{{}};const scope=arr(request.requested_keyword_scope)?[...new Set(request.requested_keyword_scope.map(v=>clean(v).toUpperCase()).filter(Boolean))]:[];const refs=arr(request.artifact_refs)?request.artifact_refs:[];const findings=[];
if(!['CREATE','REVISE'].includes(mode))findings.push({{code:'INVALID_BUILD_MODE',severity:'error'}});if(mode==='REVISE'&&!baseline)findings.push({{code:'BASELINE_REQUIRED',severity:'error'}});if(!clean(request.task?.objective||request.objective||request.problem_statement))findings.push({{code:'OBJECTIVE_REQUIRED',severity:'error'}});if(!clean(profile.vendor)||!clean(profile.simulator)||!clean(profile.version))findings.push({{code:'SIMULATOR_PROFILE_REQUIRED',severity:'error'}});if(clean(profile.simulator).toLowerCase()==='tnavigator'&&clean(profile.version)!=='22.2')findings.push({{code:'UNAPPROVED_RUNTIME_PROFILE',severity:'error'}});const unsupported=scope.filter(k=>!allowed.has(k));if(unsupported.length)findings.push({{code:'UNSUPPORTED_KEYWORD',severity:'error',keywords:unsupported}});if(!scope.length)findings.push({{code:'KEYWORD_SCOPE_UNRESOLVED',severity:'warning'}});if(!refs.every(r=>obj(r)&&clean(r.ref)&&clean(r.kind)&&clean(r.revision)))findings.push({{code:'INVALID_ARTIFACT_REF',severity:'error'}});
const hash=v=>{{let s='';try{{s=JSON.stringify(v)}}catch{{s=String(v)}}let h=2166136261;for(const ch of s){{h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}}return(h>>>0).toString(16).padStart(8,'0')}};const errors=findings.filter(f=>f.severity==='error');return [{{json:{{contract:'schedule_intake_result',contract_version:'1.0',status:errors.length?'needs_input':'accepted',build_mode:mode,request_id:clean(request.request_id)||`sched_${{Date.now().toString(36)}}`,input_hash:hash(request),simulator_profile:profile,objective:clean(request.task?.objective||request.objective||request.problem_statement),requested_keyword_scope:scope,artifact_refs:refs,baseline_present:Boolean(baseline),findings,score:{{scope_readiness:scope.length?100:60,profile_readiness:errors.some(f=>f.code.includes('PROFILE'))?0:100}},next_action:errors.length?'ask_user':'plan'}}}}];
"""
BASELINE=f"""
const x=$json.baseline_request??$json;const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);const clean=v=>typeof v==='string'?v.trim():'';const allowed=new Set({K});const text=typeof x.baseline_schedule_text==='string'?x.baseline_schedule_text:'';const files=Array.isArray(x.include_files)?x.include_files:[];const hash=v=>{{let h=2166136261;for(const ch of String(v??'')){{h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}}return(h>>>0).toString(16).padStart(8,'0')}};
const parse=(source,fileRef)=>{{const lines=source.replace(/\\r\\n?/g,'\\n').split('\\n');const blocks=[];const inventory={{}};let cur=null;const finish=end=>{{if(!cur)return;cur.end=end;cur.raw=lines.slice(cur.start-1,end).join('\\n');blocks.push(cur);inventory[cur.keyword]=(inventory[cur.keyword]||0)+1;cur=null}};for(let i=0;i<lines.length;i++){{const m=lines[i].match(/^\\s*([A-Za-z][A-Za-z0-9_]*)\\b/);const token=m?m[1].toUpperCase():'';if(!cur&&allowed.has(token))cur={{keyword:token,start:i+1,end:i+1,file_ref:fileRef,raw:''}};if(cur&&i+1>cur.start&&lines[i].trim()==='/')finish(i+1)}}if(cur)finish(lines.length);const includes=[];for(const line of lines){{const m=line.match(/^\\s*INCLUDE\\s+['\"]([^'\"]+)['\"]/i);if(m)includes.push(m[1])}}return{{file_ref:fileRef,line_count:lines.length,blocks,inventory,includes,byte_length:source.length}}}};
if(!text)return[{{json:{{contract:'baseline_analysis',contract_version:'1.0',status:'needs_input',findings:[{{code:'BASELINE_TEXT_REQUIRED',severity:'error'}}]}}}}];const main=parse(text,'schedule.inc');const child=files.filter(f=>obj(f)&&clean(f.path)&&typeof f.text==='string').map(f=>parse(f.text,f.path));const all=[main,...child],inventory={{}},graph={{}};for(const p of all){{graph[p.file_ref]=p.includes;for(const[k,v]of Object.entries(p.inventory))inventory[k]=(inventory[k]||0)+v}}const findings=[];for(const path of Object.keys(graph))if(!clean(path)||String(path).includes('\\0'))findings.push({{code:'INCLUDE_PATH_INVALID',severity:'error',path}});const visit=(p,stack)=>{{if(stack.includes(p)){{findings.push({{code:'INCLUDE_CYCLE',severity:'error',cycle:[...stack,p]}});return}}for(const n of(graph[p]||[]))visit(n,[...stack,p])}};visit('schedule.inc',[]);return[{{json:{{contract:'baseline_analysis',contract_version:'1.0',status:findings.some(f=>f.severity==='error')?'needs_input':'analyzed',baseline_hash:hash(text),preservation_token:hash(text+'|'+JSON.stringify(graph)),cst:all.flatMap(p=>p.blocks).slice(0,10000),keyword_inventory:inventory,include_graph:graph,entity_snapshot:{{block_count:all.reduce((n,p)=>n+p.blocks.length,0),files:all.length}},findings,limits:{{max_blocks:10000,max_bytes:2000000}}}}}}];
"""
PLANNER_SCHEMA = {
    'type': 'object',
    'additionalProperties': True,
    'required': [
        'status', 'build_mode', 'keyword_scope', 'stages',
        'preservation_policy', 'rationale',
    ],
    'properties': {
        'status': {'enum': ['proposed', 'needs_input', 'needs_decision']},
        'build_mode': {'enum': ['CREATE', 'REVISE']},
        'keyword_scope': {'type': 'array', 'items': {'type': 'string'}},
        'stages': {
            'type': 'array',
            'items': {'type': 'object'},
        },
        'questions': {'type': 'array', 'items': {'type': 'object'}},
        'preservation_policy': {'enum': ['preserve_unmentioned', 'not_applicable']},
        'rationale': {'type': 'string'},
        # Loose: Code synthesizes decision_record; nested strict schema breaks LLM parse.
        'decision_record': {'type': 'object'},
    },
}
PLANNER_SYS = """# tNavigator SCHEDULE Planner

## Role
Plan CREATE/REVISE of tNavigator 22.2 SCHEDULE only. Return the connected structured schema — nothing else.
Do not invent grammar, defaults, field order, workflow IDs, or facts. No hidden chain-of-thought.
Readiness/confidence scores are computed by deterministic Code after you — do not invent them.

## Modes
- CREATE if no baseline; REVISE if baseline present (`preservation_policy=preserve_unmentioned`).

## Comments in `.data` / `.inc` / model keyword files (`--`)
In ECLIPSE / tNavigator text, `--` starts a comment through end-of-line (full-line or trailing after a keyword/record).
- **Skip structurally:** do not treat `--…` as a keyword, record, field value, or evidence of an allowlisted construct. Empty/comment-only lines are not records.
- **Still read the text:** comments often hold useful engineer notes (cutover rationale, well aliases, units reminders, “do not touch”, temporary shut-in reasons, sheet/source refs). Use them as clarifying context for scope, gaps, and questions.
- **Not fact authority:** comments never override `source_facts`, schema_catalogue, or decoded baseline records. If a comment conflicts with facts/schema → prefer facts/schema and ask (`needs_input`); do not invent rates/dates/names from comments alone.
- **REVISE:** preserve existing comments (`preserve_unmentioned`); do not strip or rewrite comments as a side effect of an unrelated MODIFY.

## INCLUDE call sites and included files
- **Same date by default:** keep each INCLUDE on the same DATES slot as in the baseline/source. INCLUDE does not set the clock; do not move includes to cutover/first-well/file-top “for cleanliness”.
- **Explicit exception only:** relocate/rebind an INCLUDE when the task/facts give clear instructions for that specific include (e.g. “INCLUDE GRUPTREE_BASE sync with first-well intro date”). All other includes stay put.
- **Read when present:** if the referenced file is in package/`include_files`, you must open and read it (keywords, nested INCLUDE, comments) — same discipline as the root `.inc`. Missing body → KEEP the call site; do not invent file contents. Editing that fragment requires the body in evidence.

## Domain (§12.20) — plan only allowlisted keywords
Allowlist: """ + ", ".join(KEYWORDS) + """

File order matters. Typical DAG dependencies:
1. DATES (clock) before events on that date.
2. INCLUDE only for package structure facts you are given — do not invent paths; preserve call-site date unless an explicit per-INCLUDE instruction says otherwise; read included bodies that exist in the package.
3. GRUPTREE before leaf WELSPECS groups (non-FIELD) and before GCONPROD/GCONINJE on those groups; satellite groups for GSATPROD should appear in GRUPTREE first (else they attach under FIELD).
4. WELSPECS before WELLTRACK/COMPDATMD/controls on that well; WELSPECS↔WELLTRACK free; COMPDATMD after both.
5. WLIST after member WELSPECS; before consumers that reference `*LIST` (WCONPROD/WCONHIST/WECON/…).
6. WCONHIST only in history interval; WCONPROD / WCONINJE / GCONPROD / GCONINJE in forecast after cutover (producers vs injectors).
7. GUIDERAT when GCONPROD needs FORM guides / WCUT-GOR-aware split (after GRUPTREE/GCONPROD facts); WGRUPCON still outside allowlist.
8. GSATPROD / GSATINJE for satellite production/injection sources (no wells); GSATCOMP still outside allowlist.
9. WELLSTRE before WINJGAS/GINJGAS (and before GSATCOMP if it appears in baseline); E3/tN only; mole fractions from facts (Σ=1). WINJGAS wires well inject composition after WCONINJE GAS; GINJGAS after GCONINJE GAS; GSATCOMP/WINJMIX/WELLSTRW still outside allowlist.
10. VFPPROD table **number** must exist in baseline/facts before WCONPROD/WCONHIST reference it (THP/ALQ paths); never invent VFP table body. WVFPDP (BHP add-on / tubing ΔP scale) after the well exists and typically with VFP in use; not a synonym of inventing VFPDP — emit `WVFPDP` only.
11. WECON/WTEST/WELTARG/WPIMULT/WDFAC/WEFAC/WELOPEN/WELDRAW after the well (or group/FIELD for WELDRAW) exists; WELTARG after a prior control exists when changing a target.
12. BRANPROP→NODEPROP→WNETDP only if NETWORK already in evidence/baseline; GNETDP for fixed-pressure group/node rate band (after group/node exists); NETBALAN for NETWORK balance solver tolerances (NETWORK required); GNETINJE still outside allowlist.
13. Fractures — pick one path: WFRACP|WFRACPL **or** FRACTURE_TEMPLATE→FRACTURE_SPECS→FRACTURE_STAGE (do not mix without facts).
14. LGR plane frac → WFRACPL (needs LGR + WELSPECL/COMPDATL in baseline); never plan WELSPECL/COMPDAT/ACTION/property edits (SATNUM/PORO/MULT*/…).
15. ACTIONX|DELAYACT→(allowlisted body keywords)→ENDACTIO; no DATES/TSTEP inside; DELAYACT needs a trigger action name (usually prior ACTIONX); other ACTION* forms still outside allowlist.
16. UDQ DEFINE/ASSIGN before consumers (ACTIONX conditions or UDA fields); UDT before UDQ that uses TU*[…]; never invent expression/table bodies; UDQPARAM/UDTDIMS still outside allowlist.
17. APPLYSCRIPT wires SCRIPT_FILE + FUNCTION_NAME only; never invent Python script body — file/function must come from package facts; not GUI Python calculator.

Out of allowlist → omit from keyword_scope and add a concrete question (do not substitute a “similar” keyword).

## Stages
Every stage: keywords ⊆ allowlist, required_evidence, entity_scope (string[]), temporal_scope (string[]), observable acceptance_checks.
`dependencies` must list other stage_id values only (or []). Unknown names are dropped.

## Output
Return top-level fields only. Leave `questions=[]` when Excel/source_facts already give commissioning dates.
`decision_record` is optional — deterministic Code synthesizes it after you.
Human-facing: optional `user_message` — 1–3 short Russian sentences for Activity/HITL; keep keyword/field names in Latin; no English filler in Russian prose.
"""
PLAN_VALIDATE=rf"""
let plan=$json.output??$json;if(typeof plan==='string'){{try{{plan=JSON.parse(plan)}}catch{{plan={{}}}}}}
const arr=Array.isArray,obj=v=>v&&typeof v==='object'&&!arr(v),clean=v=>typeof v==='string'?v.trim():'',allowed=new Set({K}),findings=[];
const asList=v=>arr(v)?v.map(clean).filter(Boolean):(clean(v)?[clean(v)]:[]);
let request={{}};try{{const prepared=$('Prepare SCHEDULE planner input').first().json;request=prepared.planner_request??prepared??{{}}}}catch{{try{{const prepared=$('Prepare SCHEDULE pipeline plan').first().json;request=prepared.planner_request??prepared??{{}}}}catch{{request={{}}}}}}
const mode=clean(plan.build_mode).toUpperCase(),scope=arr(plan.keyword_scope)?[...new Set(plan.keyword_scope.map(k=>clean(k).toUpperCase()).filter(Boolean))]:[];
if(!['CREATE','REVISE'].includes(mode))findings.push({{code:'PLAN_MODE_INVALID',severity:'error'}});
const unsupported=scope.filter(k=>!allowed.has(k));if(unsupported.length)findings.push({{code:'PLAN_KEYWORD_UNSUPPORTED',severity:'error',keywords:unsupported}});
if(mode==='REVISE'&&clean(plan.preservation_policy)&&plan.preservation_policy!=='preserve_unmentioned')findings.push({{code:'PRESERVATION_POLICY_MISSING',severity:'error'}});
const stagesRaw=arr(plan.stages)?plan.stages.filter(obj).map((s,i)=>({{stage_id:clean(s.stage_id)||`stage_${{i+1}}`,capability:clean(s.capability),keywords:arr(s.keywords)?[...new Set(s.keywords.map(k=>clean(k).toUpperCase()).filter(k=>allowed.has(k)))]:[],required_evidence:arr(s.required_evidence)?s.required_evidence.filter(obj).map(r=>({{...r,required:r.required===true,status:clean(r.status)||'supported'}})):(obj(s.required_evidence)?[s.required_evidence]:[]),dependencies:asList(s.dependencies),entity_scope:asList(s.entity_scope).filter(v=>v&&!/^(wells?|groups?|blocks?|entities|field|all|any|global)$/i.test(v)),temporal_scope:asList(s.temporal_scope),acceptance_checks:arr(s.acceptance_checks)?s.acceptance_checks.filter(obj):(obj(s.acceptance_checks)?[s.acceptance_checks]:[])}})):[];
const factWells=(()=>{{const packet=obj(request.source_facts_packet)?request.source_facts_packet:(obj(request.evidence)?{{}}:null);const facts=arr(packet?.facts)?packet.facts:(arr(request.evidence)?request.evidence.flatMap(e=>arr(e?.value?.facts)?e.value.facts:[]):[]);const change=obj(request.requested_change_scope)?request.requested_change_scope:{{}};const fromChange=[...(arr(change.wells)?change.wells:[]),...(arr(change.groups)?change.groups:[])];const fromFacts=facts.map(f=>{{const values=obj(f?.values)?f.values:{{}};return clean(f?.well||f?.entity||f?.entity_id||values['Скважина']||values.WELL||values.well||values['Группа']||values.GROUP)}}).filter(Boolean);return [...new Set([...fromChange,...fromFacts].map(v=>clean(String(v))).filter(Boolean))];}})();
const stagesFilled=stagesRaw.map(s=>({{...s,entity_scope:s.entity_scope.length?s.entity_scope:factWells.slice(0,50),temporal_scope:s.temporal_scope.length?s.temporal_scope:['forecast']}}));
const stageIds=new Set(stagesFilled.map(s=>s.stage_id));
const droppedDeps=stagesFilled.flatMap(s=>s.dependencies.filter(d=>d===s.stage_id||!stageIds.has(d)).map(d=>({{stage_id:s.stage_id,dependency:d}})));
let stages=stagesFilled.map(s=>({{...s,dependencies:s.dependencies.filter(d=>d!==s.stage_id&&stageIds.has(d))}}));
if(droppedDeps.length)findings.push({{code:'PLAN_DEPENDENCY_DROPPED',severity:'warning',dependencies:droppedDeps}});
if(!stages.length)findings.push({{code:'PLAN_STAGES_MISSING',severity:'error'}});
if(stageIds.size!==stages.length)findings.push({{code:'PLAN_STAGE_ID_DUPLICATE',severity:'error'}});
const invalidDeps=[];
const visiting=new Set(),visited=new Set();let cycle=false;const visit=id=>{{if(visiting.has(id)){{cycle=true;return}}if(visited.has(id))return;visiting.add(id);const s=stages.find(v=>v.stage_id===id);for(const d of(s?.dependencies||[]))visit(d);visiting.delete(id);visited.add(id)}};for(const s of stages)visit(s.stage_id);if(cycle)findings.push({{code:'PLAN_DEPENDENCY_CYCLE',severity:'error'}});
const requested=arr(request.requested_keyword_scope)?[...new Set(request.requested_keyword_scope.map(k=>clean(k).toUpperCase()).filter(k=>allowed.has(k)))]:scope;
const plannedKeywords=new Set(stages.flatMap(s=>s.keywords));let coveredScope=requested.filter(k=>scope.includes(k)&&plannedKeywords.has(k));
const requirements=stages.flatMap(s=>s.required_evidence),supported=r=>r.required===false||['supported','covered','resolved','approved','available'].includes(clean(r.status).toLowerCase())||clean(r.source_ref)||clean(r.fact_id)||(arr(r.source_refs)&&r.source_refs.length>0)||clean(r.type)||clean(r.details);
const required=requirements.filter(r=>r.required===true),requiredSupported=required.filter(supported).length;
const evidence=arr(request.evidence)?request.evidence:[],ragPackets=evidence.map(e=>obj(e)&&obj(e.value)?e.value:e).filter(obj),citations=ragPackets.flatMap(e=>arr(e.citations)?e.citations:[]).filter(obj);
const tags=v=>{{const m=obj(v.metadata)?v.metadata:{{}},raw=v.keyword_families??v.keyword_family??v.keyword??v.keywords??m.keyword_families??m.keyword_family??m.keyword??[];if(arr(raw))return raw.map(x=>clean(x).toUpperCase()).filter(Boolean);if(typeof raw==='string'){{try{{const p=JSON.parse(raw);if(arr(p))return p.map(x=>clean(x).toUpperCase()).filter(Boolean)}}catch{{}}return raw.split(/[,;|\s]+/).map(x=>clean(x).toUpperCase()).filter(Boolean)}}return[]}};
const citedKeywords=new Set(ragPackets.flatMap(e=>[...(arr(e.results)?e.results:[]),...(arr(e.citations)?e.citations:[])]).flatMap(tags));
const conflictCount=ragPackets.reduce((n,e)=>n+(arr(e.conflicts)?e.conflicts.length:0),0);let questions=arr(plan.questions)?plan.questions.filter(obj):[];
const hasSourceFacts=evidence.some(e=>clean(e?.kind).includes('source_fact')||(obj(e?.value)&&((arr(e.value.facts)&&e.value.facts.length)||Number(e.value.fact_count)>0)))||factWells.length>0;
const modelDecision=obj(plan.decision_record)?plan.decision_record:{{}};if(modelDecision.contract&&(modelDecision.contract!=='decision_record'||modelDecision.contract_version!=='1.0'||!clean(modelDecision.objective)||!obj(modelDecision.selected_action)||!arr(modelDecision.selected_action?.reason_codes)))findings.push({{code:'DECISION_RECORD_INVALID',severity:'warning'}});
// Commissioning REVISE with Excel well dates: deterministic timeline after merge — do not block on planner confirmations / empty stages.
const commissioningRevise=mode==='REVISE'&&factWells.length>0;
if(commissioningRevise){{
  const fallbackKw=(requested.length?requested:(scope.length?scope:['DATES','WELOPEN','WCONPROD','WEFAC'])).filter(k=>allowed.has(k));
  if(!stages.length){{
    stages=[{{stage_id:'commissioning_dates',capability:'timeline_revise',keywords:fallbackKw,required_evidence:[{{type:'source_facts_packet',status:'supported',required:true}}],dependencies:[],entity_scope:factWells.slice(0,50),temporal_scope:['forecast'],acceptance_checks:[{{check:'dates_shifted_for_excel_wells'}}]}}];
  }}
  for(const s of stages){{ if(!s.keywords.length) s.keywords=fallbackKw; if(!s.entity_scope.length) s.entity_scope=factWells.slice(0,50); }}
  coveredScope=requested.length?requested.filter(k=>stages.some(s=>s.keywords.includes(k))):fallbackKw;
  questions=[];
  for(let i=findings.length-1;i>=0;i--){{ if(['PLAN_STAGES_MISSING','PLAN_SCOPE_UNCOVERED','PLAN_MANDATORY_EVIDENCE_GAP'].includes(findings[i].code)) findings.splice(i,1); }}
}}
if(requested.length&&!coveredScope.length)findings.push({{code:'PLAN_SCOPE_UNCOVERED',severity:'error',keywords:requested}});
if(required.length>requiredSupported||(questions.length&&!hasSourceFacts))findings.push({{code:'PLAN_MANDATORY_EVIDENCE_GAP',severity:'error',required:required.length,supported:requiredSupported,questions:questions.length}});
if(conflictCount)findings.push({{code:'PLAN_SOURCE_CONFLICT',severity:'error',count:conflictCount}});
const scopeFit=requested.length?Math.round(100*coveredScope.length/requested.length):(commissioningRevise?100:0);
const evidenceCompleteness=required.length?Math.round(100*requiredSupported/required.length):(stages.length||commissioningRevise?100:0);
const sourceAuthority=scope.length?Math.round(100*scope.filter(k=>citedKeywords.has(k)).length/scope.length):(citations.length||hasSourceFacts?100:0);
const entityTemporalConsistency=(stages.length||commissioningRevise)&&!cycle&&(stages.length?stages.every(s=>s.entity_scope.length&&s.temporal_scope.length):true)&&!conflictCount?100:0;
const hardFindings=findings.filter(f=>f.severity==='error');
const deterministicValidationHealth=hardFindings.length?0:100;
const stageScore=Math.round(.25*scopeFit+.25*evidenceCompleteness+.20*sourceAuthority+.15*entityTemporalConsistency+.15*deterministicValidationHealth);
const hardBlockers=[...new Set(findings.filter(f=>f.severity==='error').map(f=>f.code))],decision=hardBlockers.length||stageScore<70?'hitl':stageScore<85?'attention':'continue';
const reasonCodes=hardBlockers.length?hardBlockers:[decision==='continue'?'READINESS_CONTINUE':decision==='attention'?'READINESS_ATTENTION':'READINESS_HITL'];
const decisionRecord={{contract:'decision_record',contract_version:'1.0',objective:clean(request.task?.objective||request.objective),considered_inputs:[{{kind:'planner_request',build_mode:mode,requested_keyword_scope:requested,evidence_packet_count:evidence.length}},{{kind:'baseline_inventory',package_hash:request.baseline_analysis?.package_hash||null}}],proposed_actions:stages.map(s=>({{stage_id:s.stage_id,capability:s.capability,keywords:s.keywords,depends_on:s.dependencies}})),selected_action:{{action:decision,reason_codes:reasonCodes}},rejected_actions:findings.map(f=>({{action:'schedule_plan',reason_codes:[f.code]}})),assumptions:arr(modelDecision.assumptions)?modelDecision.assumptions.map(String).slice(0,100):[],evidence_refs:arr(modelDecision.evidence_refs)?modelDecision.evidence_refs.filter(obj).slice(0,100):[],citations:citations.slice(0,100),tool_call_ids:arr(modelDecision.tool_call_ids)?modelDecision.tool_call_ids.map(String).slice(0,100):[],unresolved_questions:questions,acceptance_check_results:[{{check:'scope_fit',score:scopeFit,passed:scopeFit===100}},{{check:'evidence_completeness',score:evidenceCompleteness,passed:evidenceCompleteness===100}},{{check:'source_authority_and_citation',score:sourceAuthority,passed:sourceAuthority===100}},{{check:'entity_temporal_consistency',score:entityTemporalConsistency,passed:entityTemporalConsistency===100}},{{check:'deterministic_validation_health',score:deterministicValidationHealth,passed:deterministicValidationHealth===100}}]}};
const outStatus=hardBlockers.length?'needs_input':(commissioningRevise?'proposed':(plan.status||'proposed'));
return[{{json:{{contract:'schedule_plan',contract_version:'1.0',status:outStatus,build_mode:mode,keyword_scope:scope.length?scope:(commissioningRevise?coveredScope:scope),stages,questions,preservation_policy:mode==='REVISE'?'preserve_unmentioned':'not_applicable',rationale:clean(plan.rationale).slice(0,4000),decision_record:decisionRecord,findings,hard_blockers:hardBlockers,score:{{stage_score:stageScore,components:{{scope_fit:scopeFit,evidence_completeness:evidenceCompleteness,source_authority_and_citation:sourceAuthority,entity_temporal_consistency:entityTemporalConsistency,deterministic_validation_health:deterministicValidationHealth}},raw_counts:{{requested_keywords:requested.length,covered_keywords:coveredScope.length,required_evidence:required.length,supported_evidence:requiredSupported,citations:citations.length,invalid_dependencies:droppedDeps.length,conflicts:conflictCount,questions:questions.length,findings:findings.length}},thresholds:{{attention:85,hitl:70}},decision,provisional:true}}}}}}];
"""
MERGE=f"""
const x=$json.merge_request??$json,arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'',allowed=new Set({K});const mode=clean(x.mode).toUpperCase(),baseline=typeof x.baseline_schedule_text==='string'?x.baseline_schedule_text:'',changes=arr(x.changes)?x.changes:[],findings=[];const hash=v=>{{let h=2166136261;for(const ch of String(v??'')){{h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}}return(h>>>0).toString(16).padStart(8,'0')}};if(mode==='REVISE'&&!baseline)findings.push({{code:'BASELINE_REQUIRED',severity:'error'}});if(!['CREATE','REVISE'].includes(mode))findings.push({{code:'MODE_INVALID',severity:'error'}});let output=mode==='REVISE'?baseline:'',applied=[];for(const c of changes){{const op=clean(c.operation).toUpperCase(),kw=clean(c.keyword).toUpperCase();if(op==='KEEP'){{applied.push({{...c,operation:'KEEP'}});continue}}if(!['ADD','MODIFY','REMOVE'].includes(op)||!allowed.has(kw)){{findings.push({{code:'CHANGE_INVALID',severity:'error'}});continue}}if(op==='REMOVE'&&!x.explicit_remove_approved){{findings.push({{code:'REMOVE_REQUIRES_APPROVAL',severity:'error',keyword:kw}});continue}}const rendered=typeof c.rendered_text==='string'?c.rendered_text.trim():'';if(op==='ADD'){{if(!rendered){{findings.push({{code:'ADD_TEXT_REQUIRED',severity:'error'}});continue}}output=(output.trimEnd()?output.trimEnd()+'\\n':'')+rendered+(rendered.endsWith('/')?'':'\\n/')+'\\n';applied.push({{...c,operation:'ADD'}});continue}}const selector=clean(c.selector?.contains||c.selector||kw);const escaped=selector.replace(/[.*+?^${{}}()|[\\]\\\\]/g,'\\\\$&');const re=new RegExp('(^|\\n)([\\s\\S]*?'+escaped+'[\\s\\S]*?\\n\\s*\\/)','i');const m=output.match(re);if(!m){{findings.push({{code:'TARGET_NOT_FOUND',severity:'error',keyword:kw,selector}});continue}}if(op==='MODIFY'){{if(!rendered){{findings.push({{code:'MODIFY_TEXT_REQUIRED',severity:'error'}});continue}}output=output.replace(m[2],rendered+(rendered.endsWith('/')?'':'\\n/'));applied.push({{...c,operation:'MODIFY'}})}}else{{output=output.replace(m[2],'');applied.push({{...c,operation:'REMOVE'}})}}}}const mutations=changes.filter(c=>clean(c.operation).toUpperCase()!=='KEEP').length;return[{{json:{{contract:'schedule_merge_result',contract_version:'1.0',status:findings.some(f=>f.severity==='error')?'needs_input':'merged',mode,baseline_hash:hash(baseline),output_hash:hash(output),generated_schedule:output,applied_changes:applied,preservation_report:mode==='REVISE'?{{policy:'preserve_unmentioned',zero_change_byte_identical:mutations===0?output===baseline:null,removed_count:applied.filter(c=>c.operation==='REMOVE').length,modified_count:applied.filter(c=>c.operation==='MODIFY').length,added_count:applied.filter(c=>c.operation==='ADD').length}}:{{policy:'not_applicable'}},semantic_diff:{{changed_keywords:[...new Set(applied.filter(c=>c.operation!=='KEEP').map(c=>c.keyword))]}},findings}}}}];
"""
# Keep the legacy compact strings above only as historical context for old
# exports.  The generated workflow uses the reviewed lossless/CST runtime.
INTAKE=build_schedule_intake_js(KEYWORDS)
BASELINE=build_baseline_js(KEYWORDS)
BASELINE_DECODE=build_baseline_decoder_js(KEYWORDS)
BASELINE_QUERY=build_baseline_query_js(KEYWORDS)
MERGE=build_merge_js(KEYWORDS)
RENDER=build_schema_renderer_js(KEYWORDS)

LEGACY_VALIDATE=f"""
const x=$json.schedule_validation_request??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'',allowed=new Set({K}),findings=[];
const pkg=obj(x.output_package)?x.output_package:{{}},files=arr(pkg.files)&&pkg.files.length?pkg.files.filter(obj).map(f=>({{file_ref:clean(f.file_ref)||'unknown',text:String(f.text??'')}})):[{{file_ref:'schedule.inc',text:typeof x.schedule_text==='string'?x.schedule_text:''}}];
const schemas=arr(x.approved_keyword_schemas)?x.approved_keyword_schemas.filter(obj):[],schemaKeywords=new Set(schemas.map(s=>clean(s.keyword).toUpperCase()).filter(Boolean)),render=obj(x.render_result)?x.render_result:{{}};
const blocks=[],dates=[],includes=[],month={{JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11}};
const parseDate=raw=>{{const t=clean(raw).toUpperCase();let m=t.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/),y,mo,d;if(m){{y=Number(m[1]);mo=Number(m[2])-1;d=Number(m[3])}}else{{m=t.match(/^(\\d{{1,2}})\\s+([A-Z]{{3}})\\s+(\\d{{4}})$/);if(!m||month[m[2]]===undefined)return null;d=Number(m[1]);mo=month[m[2]];y=Number(m[3])}}const dt=Date.UTC(y,mo,d);return new Date(dt).getUTCFullYear()===y&&new Date(dt).getUTCMonth()===mo&&new Date(dt).getUTCDate()===d?{{raw:t,epoch:dt}}:null}};
for(const file of files){{const lines=file.text.replace(/\\r\\n?/g,'\\n').split('\\n'),headers=[];for(let i=0;i<lines.length;i++){{const m=lines[i].match(/^\\s*([A-Za-z][A-Za-z0-9_]*)\\s*(?:--.*)?$/),kw=m?m[1].toUpperCase():'';if(allowed.has(kw))headers.push({{keyword:kw,line:i}})}}for(let h=0;h<headers.length;h++){{const start=headers[h].line,end=h+1<headers.length?headers[h+1].line:lines.length,kw=headers[h].keyword,body=lines.slice(start+1,end),nonempty=body.map(v=>v.trim()).filter(v=>v&&!v.startsWith('--'));blocks.push({{file_ref:file.file_ref,keyword:kw,start_line:start+1,end_line:end,record_lines:nonempty.length}});if(!schemaKeywords.has(kw))findings.push({{code:'KEYWORD_SCHEMA_NOT_APPROVED',severity:'error',file_ref:file.file_ref,keyword:kw,line:start+1}});if(!nonempty.length)findings.push({{code:'KEYWORD_RECORD_MISSING',severity:'error',file_ref:file.file_ref,keyword:kw,line:start+1}});if(kw==='INCLUDE'){{const q=body.join('\\n').match(/['\"]([^'\"\\r\\n]+)['\"]/);if(q)includes.push({{file_ref:file.file_ref,path:q[1]}});else findings.push({{code:'INCLUDE_TARGET_MISSING',severity:'error',file_ref:file.file_ref,line:start+1}})}}if(kw==='DATES')for(const line of body){{const candidate=line.replace(/--.*$/,'').replace(/\\/.*$/,'').trim();if(!candidate)continue;const d=parseDate(candidate);if(d)dates.push({{...d,file_ref:file.file_ref,line:start+2+body.indexOf(line)}});else findings.push({{code:'DATES_VALUE_INVALID',severity:'error',file_ref:file.file_ref,line:start+2+body.indexOf(line),value:candidate}})}}}}}}
if(!files.some(f=>f.text.trim()))findings.push({{code:'SCHEDULE_TEXT_REQUIRED',severity:'error'}});
for(const inc of includes)if(!clean(inc.path)||String(inc.path).includes('\\0'))findings.push({{code:'INCLUDE_PATH_INVALID',severity:'error',...inc}});
for(let i=1;i<dates.length;i++)if(dates[i].epoch<=dates[i-1].epoch)findings.push({{code:'DATES_NOT_STRICTLY_INCREASING',severity:'error',previous:dates[i-1],current:dates[i]}});
const p=obj(x.simulator_profile)?x.simulator_profile:{{}};if(clean(p.vendor)!=='Rock Flow Dynamics'||clean(p.simulator).toLowerCase()!=='tnavigator'||clean(p.version)!=='22.2')findings.push({{code:'PROFILE_NOT_APPROVED',severity:'error'}});
if(!clean(x.schema_catalogue_ref)||x.schema_catalogue_approved!==true||render.status!=='rendered')findings.push({{code:'SCHEMA_CATALOGUE_NOT_APPROVED',severity:'error'}});
if(!schemas.length)findings.push({{code:'APPROVED_KEYWORD_SCHEMAS_REQUIRED',severity:'error'}});
const hard=findings.filter(f=>f.severity==='error'),syntax=hard.some(f=>['SCHEDULE_TEXT_REQUIRED','KEYWORD_RECORD_MISSING','DATES_VALUE_INVALID'].includes(f.code))?0:100,temporal=hard.some(f=>f.code==='DATES_NOT_STRICTLY_INCREASING')?0:100,profile=hard.some(f=>['PROFILE_NOT_APPROVED','SCHEMA_CATALOGUE_NOT_APPROVED','APPROVED_KEYWORD_SCHEMAS_REQUIRED','KEYWORD_SCHEMA_NOT_APPROVED'].includes(f.code))?0:100,score=Math.round(.4*syntax+.25*temporal+.35*profile);
return[{{json:{{contract:'schedule_validation_result',contract_version:'1.0',status:hard.length?'invalid':'valid',findings,file_count:files.length,block_count:blocks.length,keyword_counts:blocks.reduce((a,b)=>(a[b.keyword]=(a[b.keyword]||0)+1,a),{{}}),dates:dates.map(d=>d.raw),include_paths:includes.map(i=>i.path),render_catalogue_hash:render.catalogue_hash||null,score:{{stage_score:score,syntax,temporal,profile,thresholds:{{attention:85,hitl:70}},gate:hard.length||score<70?'hitl':score<85?'attention':'continue'}},hard_blockers:hard.map(f=>f.code)}}}}];
"""
# The old lexical-only validator above is retained as migration context for
# reviewers of previous exports.  Delivery workflows use stateful replay.
VALIDATE=build_schedule_validator_js(KEYWORDS)
VERIFY="""const x=$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),val=obj(x.validation_result)?x.validation_result:{},render=obj(x.render_result)?x.render_result:{},merge=obj(x.merge_result)?x.merge_result:{},builder=obj(x.builder_result)?x.builder_result:{},findings=[];if(render.status!=='rendered')findings.push({code:'DETERMINISTIC_RENDER_NOT_PASSED',severity:'error'});if(val.status!=='valid')findings.push({code:'VALIDATION_NOT_PASSED',severity:'error'});if(builder.status==='succeeded'&&!builder.self_check?.passed)findings.push({code:'SPECIALIST_SELF_CHECK_FAILED',severity:'error'});if(merge.mode==='REVISE'&&merge.preservation_report?.policy!=='preserve_unmentioned')findings.push({code:'PRESERVATION_POLICY_MISSING',severity:'error'});const commissioningApplied=merge.commissioning_revise?.status==='applied'||merge.preservation_report?.commissioning_shift_applied===true;if(merge.mode==='REVISE'&&!commissioningApplied&&merge.preservation_report?.zero_change_byte_identical===false&&!(Array.isArray(merge.applied_changes)&&merge.applied_changes.some(c=>String(c.operation||'').toUpperCase()!=='KEEP'))) findings.push({code:'ZERO_CHANGE_NOT_IDEMPOTENT',severity:'error'});if(commissioningApplied&&merge.commissioning_revise?.monthly_dates_check&&merge.commissioning_revise.monthly_dates_check.ok===false)findings.push({code:'MONTHLY_DATES_GAP',severity:'error'});const score=Math.min(render.status==='rendered'?100:0,Number(val.score?.stage_score??0),Number(builder.score?.stage_score??100)),verdict=findings.length?'reject':score<70?'needs_input':score<85?'pass_with_warnings':'pass';return[{json:{contract:'schedule_verifier_result',contract_version:'1.0',verdict,summary:findings.length?'Hard blockers found.':'Independent review completed.',findings,score:{stage_score:score,thresholds:{attention:85,hitl:70}},required_corrections:findings.map(f=>f.code),approval_required:true,can_release:verdict==='pass'}}];"""
RELEASE="""const x=$json,action=String(x.action||'request').toLowerCase(),v=x.verifier_result||{},d=x.validation_result||{},a=x.approval||{},scheduleText=String(x.schedule_text||''),findings=[];if(!['request','approve','reject'].includes(action))findings.push({code:'ACTION_INVALID'});if(d.status!=='valid')findings.push({code:'VALIDATION_REQUIRED'});if(v.verdict!=='pass')findings.push({code:'INDEPENDENT_REVIEW_REQUIRED'});if(!scheduleText.trim())findings.push({code:'SCHEDULE_TEXT_REQUIRED'});if(scheduleText.length>10485760)findings.push({code:'SCHEDULE_TEXT_TOO_LARGE'});if(action==='approve'&&(!String(a.actor||'').trim()||!String(a.gate_id||'').trim()))findings.push({code:'ACCOUNTABLE_APPROVAL_REQUIRED'});if(action==='approve'&&a.gate_id!==x.gate_id)findings.push({code:'GATE_MISMATCH'});const status=action==='reject'?'rejected':findings.length?'blocked':action==='approve'?'approved':'pending_approval';return[{json:{contract:'schedule_release_result',contract_version:'1.0',status,filename:String(x.filename||'schedule.inc'),schedule_text:status==='approved'?scheduleText:null,approval:{actor:a.actor||null,at:status==='approved'?new Date().toISOString():null,gate_id:a.gate_id||null},findings}}];"""
TRACE=r"""
const root=$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'',allowed=new Set(['intake','plan','rag','baseline','excel','builder','merge','validate','verify','hitl','release','error']);
const source=arr(root.mas_trace_events)&&root.mas_trace_events.length?root.mas_trace_events.slice(0,100):(root.mas_trace_event?[root.mas_trace_event]:[root]);
const safeRef=v=>obj(v)?Object.fromEntries(Object.entries(v).filter(([k,val])=>!/(prompt|secret|token|password|authorization|binary|content|text)/i.test(k)&&['string','number','boolean'].includes(typeof val)).slice(0,20)):v;
const sanitizeDecision=rawDecision=>rawDecision&&rawDecision.contract==='decision_record'&&rawDecision.contract_version==='1.0'&&clean(rawDecision.objective)&&obj(rawDecision.selected_action)&&arr(rawDecision.selected_action.reason_codes)?{contract:'decision_record',contract_version:'1.0',objective:clean(rawDecision.objective).slice(0,1000),considered_inputs:arr(rawDecision.considered_inputs)?rawDecision.considered_inputs.slice(0,100).map(safeRef):[],proposed_actions:arr(rawDecision.proposed_actions)?rawDecision.proposed_actions.slice(0,100).map(safeRef):[],selected_action:{action:clean(rawDecision.selected_action.action).slice(0,300),reason_codes:rawDecision.selected_action.reason_codes.map(v=>clean(v).slice(0,200)).filter(Boolean).slice(0,100)},rejected_actions:arr(rawDecision.rejected_actions)?rawDecision.rejected_actions.slice(0,100).map(safeRef):[],assumptions:arr(rawDecision.assumptions)?rawDecision.assumptions.map(v=>clean(v).slice(0,500)).slice(0,100):[],evidence_refs:arr(rawDecision.evidence_refs)?rawDecision.evidence_refs.slice(0,100).map(safeRef):[],citations:arr(rawDecision.citations)?rawDecision.citations.slice(0,100).map(safeRef):[],tool_call_ids:arr(rawDecision.tool_call_ids)?rawDecision.tool_call_ids.map(v=>clean(v).slice(0,200)).slice(0,100):[],unresolved_questions:arr(rawDecision.unresolved_questions)?rawDecision.unresolved_questions.slice(0,100).map(safeRef):[],acceptance_check_results:arr(rawDecision.acceptance_check_results)?rawDecision.acceptance_check_results.slice(0,100).map(safeRef):[]}:null;
return source.map((candidate,index)=>{const x=obj(candidate)?candidate:{},stage=clean(x.stage).toLowerCase(),now=new Date().toISOString(),decision=sanitizeDecision(obj(x.decision_record)?x.decision_record:null);const safeHash=v=>/^(?:fnv1a32:[a-f0-9]{8}|sha256:[a-f0-9]{64})$/i.test(clean(v))?clean(v).toLowerCase():null,safeCount=v=>Number.isInteger(Number(v))&&Number(v)>=0&&Number(v)<=16777216?Number(v):null;const toolCalls=arr(x.tool_calls)?x.tool_calls.slice(0,50).map((v,i)=>obj(v)?{name:clean(v.name).slice(0,120),tool_call_id:clean(v.tool_call_id).slice(0,200)||null,status:clean(v.status).slice(0,30),stage:clean(v.stage).slice(0,40)||null,sequence:Number.isInteger(Number(v.sequence))?Number(v.sequence):i+1,input_hash:safeHash(v.input_hash),input_bytes:safeCount(v.input_bytes),output_hash:safeHash(v.output_hash),output_bytes:safeCount(v.output_bytes)}:null).filter(Boolean):[];const handoff=obj(x.handoff)?{from_specialist:clean(x.handoff.from_specialist)||null,to_specialist:clean(x.handoff.to_specialist)||null,from_role:clean(x.handoff.from_role)||null,to_role:clean(x.handoff.to_role)||null,details:obj(x.handoff.details)?safeRef(x.handoff.details):{}}:null;const e={contract:'mas_trace_event',contract_version:'1.0',trace_id:clean(x.trace_id)||`trace_${Date.now().toString(36)}`,task_id:clean(x.task_id)||null,event_id:clean(x.event_id)||`evt_${Date.now().toString(36)}_${index}_${Math.random().toString(36).slice(2,8)}`,at:clean(x.at)||now,stage:allowed.has(stage)?stage:'error',event_type:clean(x.event_type)||'stage_event',actor:clean(x.actor)||'system',status:clean(x.status)||'observed',summary:clean(x.summary).slice(0,2000),brief:clean(x.brief||(obj(x.handoff)?x.handoff.brief:'')).slice(0,800)||null,duration_ms:Number.isFinite(Number(x.duration_ms))?Math.trunc(Number(x.duration_ms)):(Number.isFinite(Number(obj(x.handoff)?.details?.duration_ms))?Math.trunc(Number(x.handoff.details.duration_ms)):null),tool_calls:toolCalls,evidence_refs:arr(x.evidence_refs)?x.evidence_refs.slice(0,100).map(safeRef):[],findings:arr(x.findings)?x.findings.slice(0,100).map(safeRef):[],score:obj(x.score)?x.score:null,gate:obj(x.gate)?x.gate:null,decision_record:decision,handoff,redaction:{raw_prompt:false,secret:false,binary:false}};return{json:{trace_event:e,trace_row:{event_id:e.event_id,trace_id:e.trace_id,task_id:e.task_id,at:e.at,stage:e.stage,event_type:e.event_type,actor:e.actor,status:e.status,summary:e.summary,details_json:JSON.stringify({tool_calls:e.tool_calls,evidence_refs:e.evidence_refs,findings:e.findings,score:e.score,gate:e.gate,decision_record:e.decision_record,handoff:e.handoff,brief:e.brief,duration_ms:e.duration_ms})}}}});
"""
INGEST=f"""const x=$json.schedule_knowledge_document??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'',allowed=new Set({K}),m=obj(x.metadata)?x.metadata:{{}},text=clean(x.text||x.document_text),findings=[];if(!text)findings.push({{code:'MANUAL_TEXT_REQUIRED',severity:'error'}});if(clean(m.vendor)!=='Rock Flow Dynamics'||clean(m.simulator).toLowerCase()!=='tnavigator'||clean(m.simulator_version)!=='22.2')findings.push({{code:'MANUAL_VERSION_REQUIRED',severity:'error'}});if(!clean(m.source_hash))findings.push({{code:'SOURCE_HASH_REQUIRED',severity:'error'}});const kws=[...new Set([...text.matchAll(/\\b[A-Z][A-Z0-9_]+\\b/g)].map(x=>x[0]).filter(k=>allowed.has(k)))],chunks=[];for(let i=0;i<text.length;i+=1020)chunks.push({{chunk_id:`${{clean(m.document_id)||'manual'}}:${{chunks.length+1}}`,text:text.slice(i,i+1200),metadata:{{...m,keyword_families:kws,authority_level:'vendor_manual',chunking:'recursive_char_1200_180'}}}});const status=findings.length?'needs_input':'staged';return[{{json:{{contract:'schedule_knowledge_ingest_result',contract_version:'1.0',status,document_id:clean(m.document_id),metadata:m,keyword_families:kws,chunks:chunks.slice(0,5000),chunk_count:chunks.length,findings,approval_required:true,vector_write_allowed:false,next_action:status==='staged'?'approve_and_embed':'correct_metadata_or_text'}}}}];"""
RETRIEVE=f"""const x=$json.schedule_retrieval_request??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'',allowed=new Set({K}),query=clean(x.query),f=obj(x.filters)?x.filters:{{}},findings=[];if(!query)findings.push({{code:'QUERY_REQUIRED',severity:'error'}});if(clean(f.simulator_version)!=='22.2')findings.push({{code:'EXACT_VERSION_REQUIRED',severity:'error'}});if(clean(f.authority_level)!=='vendor_manual')findings.push({{code:'AUTHORITY_FILTER_REQUIRED',severity:'error'}});const exact=[...new Set((query.match(/\\b[A-Z][A-Z0-9_]+\\b/g)||[]).filter(k=>allowed.has(k)))];return[{{json:{{contract:'schedule_retrieval_request',contract_version:'1.0',status:findings.length?'needs_input':'query_ready',query,filters:{{vendor:'Rock Flow Dynamics',simulator:'tNavigator',simulator_version:'22.2',authority_level:'vendor_manual',...f}},exact_keyword_terms:exact,retrieval_plan:{{lexical:'PostgreSQL full-text',semantic:'PGVector same embedding dimensions',tags:'metadata containment',fusion:'reciprocal_rank_fusion',top_k:Math.min(50,Math.max(1,Number(x.top_k)||10))}},findings,results:[],note:'Connect approved PostgreSQL/PGVector retrieval nodes after UI credential and embedding configuration.'}}}}];"""
def simple(name,desc,readme,trigname,example,codename,js,contract):
 ns=[note(name+' README',(-900,-450),readme),trigger(trigname,(-900,-80),example),code(codename,(-520,-80),js)];c={};connect(c,trigname,codename);return workflow(name,desc,ns,c,contract)
def build_all():
 # Runtime SCHEDULE delivery is intentionally three workflows plus the shared
 # MAS Trace writer. Diagnostic one-node mirrors of Builder stages are not
 # emitted: regenerate must not resurrect dead import surfaces.
 #
 # Hand-authored HITL / deploy forms are NOT generated here and must stay as
 # committed JSON under n8n/workflows/core/ (imported via import-manifest):
 #   mvp-entry-form.workflow.json
 #   mas-human-gate-form.workflow.json
 #   mas-deployment-health-check.workflow.json
 # Clean import reads those files directly; it does not run this generator.
 d={}
 d['tnavigator-schedule-knowledge-ingestion.workflow.json']=build_ingestion(node=node,note=note,code=code,trigger=trigger,ifnode=ifnode,connect=connect,workflow=workflow)
 d['tnavigator-schedule-hybrid-retrieval.workflow.json']=build_retrieval(node=node,note=note,code=code,trigger=trigger,ifnode=ifnode,connect=connect,workflow=workflow)
 d['tnavigator-schedule-builder.workflow.json']=build_schedule_pipeline(node=node,note=note,code=code,trigger=trigger,ifnode=ifnode,connect=connect,workflow=workflow,keywords=KEYWORDS,planner_schema=PLANNER_SCHEMA,planner_system=PLANNER_SYS,intake_js=INTAKE,baseline_js=BASELINE,baseline_decode_js=BASELINE_DECODE,baseline_query_js=BASELINE_QUERY,plan_validate_js=PLAN_VALIDATE,render_js=RENDER,merge_js=MERGE,validate_js=VALIDATE,verify_js=VERIFY)
 ns=[
  note('Trace writer README',(-920,-480),'Structured redacted trace only; no raw prompts, secrets, binary or hidden chain-of-thought. Accepts one event or a bounded event batch. Select Data Table in UI.\n\nOptional: after insert, handoff events POST to mas-activity-service (`/v1/sync`) for the chat presentation UI. Set URL/key in “Prepare MAS activity sync”. continueOnFail keeps Trace durable even if the UI service is down.',620,420),
  trigger('Receive MAS trace event',(-920,-80),{'mas_trace_event':{'trace_id':'trace_example','task_id':'eng_example','stage':'builder','event_type':'stage_completed','summary':'Draft produced'},'mas_trace_events':[],'passthrough':{}}),
  code('Normalize MAS trace event',(-640,-80),TRACE),
  node('Insert MAS trace event','n8n-nodes-base.dataTable',1.1,(-300,-80),{'operation':'insert','dataTableId':{'__rl':True,'mode':'list','value':'REPLACE_IN_UI','cachedResultName':'MAS trace events v1'},'columns':{'mappingMode':'defineBelow','value':{k:'={{ $json.trace_row.'+k+' }}' for k in ['event_id','trace_id','task_id','at','stage','event_type','actor','status','summary','details_json']},'matchingColumns':[],'schema':[],'attemptToConvertTypes':False,'convertFieldsToString':False}}),
  code('Prepare MAS activity sync',(20,-80),r"""
const source=$('Receive MAS trace event').first().json||{};
const rows=$('Normalize MAS trace event').all().map(i=>i.json||{});
const events=rows.map(r=>r.trace_event).filter(e=>e&&e.event_type==='handoff');
const taskId=String(events[0]?.task_id||source.mas_trace_event?.task_id||source.passthrough?.task_id||'').trim();
const traceId=String(events[0]?.trace_id||source.mas_trace_event?.trace_id||'').trim();
// UI-only config: change these two strings in the Code node after import.
const ACTIVITY_BASE_URL='http://127.0.0.1:8200';
const ACTIVITY_KEY='dev-local';
const skipActivity=source.passthrough?.skip_activity_sync===true||source.skip_activity_sync===true;
const ready=Boolean(!skipActivity&&taskId&&events.length&&ACTIVITY_BASE_URL);
return[{json:{
  activity_sync_ready:ready,
  activity_url:`${String(ACTIVITY_BASE_URL).replace(/\/$/,'')}/v1/sync`,
  activity_key:ACTIVITY_KEY,
  activity_body:{task_id:taskId,trace_id:traceId||null,events},
  stored_count:rows.length,
  handoff_count:events.length,
  event_id:rows[0]?.trace_event?.event_id||null,
  trace_id:traceId||null,
  redaction:rows[0]?.trace_event?.redaction||null,
  passthrough:source.passthrough??null,
  activity_sync_skipped:skipActivity
}}];
"""),
  ifnode('Activity sync needed?',(260,-80),"={{ $json.activity_sync_ready }}",True,'boolean'),
  node('POST handoffs to MAS Activity','n8n-nodes-base.httpRequest',4.4,(520,-200),{
    'method':'POST',
    'url':'={{ $json.activity_url }}',
    'sendHeaders':True,
    'headerParameters':{'parameters':[
      {'name':'Content-Type','value':'application/json'},
      {'name':'X-Activity-Key','value':'={{ $json.activity_key }}'},
    ]},
    'sendBody':True,
    'specifyBody':'json',
    'jsonBody':'={{ $json.activity_body }}',
    'options':{'timeout':5000,'response':{'response':{'neverError':True}}},
  }, onError='continueRegularOutput'),
  code('Return trace acknowledgement',(780,-80),r"""
const prepared=$('Prepare MAS activity sync').first().json||{};
const source=$('Receive MAS trace event').first().json||{};
let activity={attempted:Boolean(prepared.activity_sync_ready),stored:false,count:0};
try{
  const http=$('POST handoffs to MAS Activity').first().json||{};
  activity={attempted:true,stored:Boolean(http.stored),count:Number(http.count||0),task_id:http.task_id||prepared.activity_body?.task_id||null};
}catch(_){ activity={attempted:Boolean(prepared.activity_sync_ready),stored:false,count:0,skipped:!prepared.activity_sync_ready}; }
return[{json:{
  contract:'mas_trace_ack',
  contract_version:'1.0',
  stored:true,
  stored_count:Number(prepared.stored_count||0),
  event_id:prepared.event_id||null,
  trace_id:prepared.trace_id||null,
  redaction:prepared.redaction||null,
  activity,
  passthrough:prepared.passthrough??source.passthrough??null
}}];
"""),
 ]
 c={}
 connect(c,'Receive MAS trace event','Normalize MAS trace event')
 connect(c,'Normalize MAS trace event','Insert MAS trace event')
 connect(c,'Insert MAS trace event','Prepare MAS activity sync')
 connect(c,'Prepare MAS activity sync','Activity sync needed?')
 connect(c,'Activity sync needed?','POST handoffs to MAS Activity',idx=0)
 connect(c,'Activity sync needed?','Return trace acknowledgement',idx=1)
 connect(c,'POST handoffs to MAS Activity','Return trace acknowledgement')
 d['mas-trace-event-writer.workflow.json']=workflow('Writer — MAS Trace','Portable durable trace writer with optional MAS Activity UI sync.',ns,c,'mas_trace_event/v1')
 return d

def main():
 CORE.mkdir(parents=True,exist_ok=True)
 for fn,w in build_all().items(): (CORE/fn).write_text(json.dumps(w,ensure_ascii=False,indent=2)+'\n');print(fn,len(w['nodes']))
 # Compact canvas + yellow "edit after import" stickies for UI import
 import subprocess,sys
 subprocess.check_call([sys.executable,str(Path(__file__).resolve().parent/'relayout_core_workflows.py')])
if __name__=='__main__':main()
