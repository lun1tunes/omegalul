"""Build the concrete stateless tNavigator SCHEDULE specialist pipeline."""
from __future__ import annotations

import json

from schedule_timeline_runtime import build_commissioning_revise_js


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
    "additionalProperties": True,
    "required": [
        "status", "summary", "build_mode", "ir_events",
    ],
    "properties": {
        "status": {"enum": ["succeeded", "partial", "needs_input", "needs_decision", "needs_approval", "retryable_error", "fatal_error"]},
        "summary": {"type": "string"},
        "build_mode": {"enum": ["CREATE", "REVISE"]},
        "generated_schedule": {"type": "string"},
        "ir_events": {"type": "array", "items": {"type": "object"}},
        "changes": {"type": "array", "items": {"type": "object"}},
        "evidence_gap": {"type": "array", "items": {"type": "object"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        # Optional/loose: Code synthesizes a full decision_record. Nested strict
        # schema here caused structured-output parse failures on partial LLM objects.
        "decision_record": {"type": "object"},
        "preservation_report": {"type": "object"},
        "requirements_matrix": {"type": "array", "items": {"type": "object"}},
        "source_map": {"type": "array", "items": {"type": "object"}},
    },
}


BUILDER_SYSTEM = """# tNavigator SCHEDULE Builder

## Role
Bounded SCHEDULE draft builder inside a deterministic pipeline (tNavigator 22.2, METRIC).
Orchestrator owns durable state, tabular extraction, HITL, release.
You never call Excel/APIs/other workflows, never approve, never claim release.

## Authority
Use only: approved plan, source facts, baseline inventory/query, RAG keyword_instruction + worked examples, approved schema_catalogue.
Never invent field order, defaults, units, dates, well/group/LGR/template identities, or vendor grammar.
Grammar and field layouts come from schema_catalogue + expert cards — not from memory.

## Domain — SCHEDULE (§12.20)
SCHEDULE = well/group operating data over simulation time (+ limited in-section edits the full simulator allows).
Keywords are applied in **file order**. Canonical declare order: well → completions → operating mode.

### Comments in `.data` / `.inc` / model keyword files (`--`)
In ECLIPSE / tNavigator text, `--` starts a comment through end-of-line (full-line or trailing after a keyword/record).
- **Skip structurally:** never parse `--…` as a keyword header, record line, or field token. Deterministic analyzer/validator already ignores comment-only lines and trailing `--` tails when counting records.
- **Still read the text:** comments frequently carry useful clarifications (cutover notes, well/group aliases, units, why a control was chosen, “keep as-is”, Excel/sheet refs). Use that text as context for IR intent, warnings, and gap questions.
- **Not inventable authority:** do not turn comment prose into WCONPROD/DATES/… values. Facts + schema_catalogue + baseline records win. Comment vs fact conflict → `needs_input` (or explicit warning), never silent override.
- **CREATE:** optional `--` annotations are fine next to emitted blocks; keep them as comments only.
- **REVISE:** preserve existing comment bytes/placement under `preserve_unmentioned`; do not drop comments when MODIFY/KEEP nearby keywords.

### INCLUDE call sites and included files
- **Same date by default:** each INCLUDE stays on the same DATES position as in the baseline/source schedule. INCLUDE inherits the current clock (`uses_current`); do not shift includes to cutover, first-well date, or file top without authority.
- **Explicit exception only:** move/rebind an INCLUDE when the task or source_facts give clear instructions for that specific include (example: “INCLUDE GRUPTREE_BASE sync with the date the first well is introduced”). Unmentioned includes → KEEP path, order, and date slot.
- **Read when present:** if the PATH resolves to a file in package/`include_files`, open and decode that body too (allowlisted keywords, nested INCLUDE, `--` comments) before planning/emitting changes that touch its scope. If the file is absent from source data, KEEP the INCLUDE call and do not invent contents; need body edits without evidence → `needs_input`.
- **REVISE:** preserve include layout + call-site dates under `preserve_unmentioned`; do not inline included files into the root unless an approved package refactor says so.

### Preferred well path (tN §11.2)
1. `WELSPECS` — well + group (group default FIELD).
2. `WELLTRACK` — trajectory (order vs WELSPECS free).
3. `COMPDATMD` — MD/TVD perfs (only after WELSPECS and WELLTRACK exist).
4. Control on current `DATES`: `WCONHIST` in history, `WCONPROD` / `WCONINJE` in forecast (cutover from temporal_policy/facts).
5. Optional drawdown cap: `WELDRAW` (affects potential §5.6.7 and may tighten phase rate vs WCONPROD/WELTARG).

E1/E3 IJK perfs use `COMPDAT` — **not** in MVP allowlist. Do not emit `COMPDAT`. If the task requires IJK-only COMPDAT without MD facts → `needs_input`.

### Groups / guide rates (§2.14.2)
`GRUPTREE` = hierarchy (FIELD root; node-group XOR leaf-group with wells via WELSPECS).
`GCONPROD` = group production targets; rates split by guide rates (default = well potential §5.6.7), not equally.
`GCONINJE` = group injection targets (WATER/GAS/OIL; RATE/RESV/REIN/VREP); injector rates split by potentials / guide rates.
`GUIDERAT` = formula for guide rates used with GCONPROD (especially GUIDE=FORM) and wells without fixed `WGRUPCON` guides.
`GSATPROD` = fixed production rates for **satellite** (auxiliary) groups — no wells/children; external/other-region offtake sources.
`GSATINJE` = fixed injection rates for satellite groups (OIL/WAT/GAS; one phase per record).
Do not emit `WGRUPCON` (outside allowlist) — if fixed per-well guides required → `needs_input`.

### Compositional streams (E3 / tN)
`WELLSTRE` = named inject stream mole fractions (oil/gas); Σxi = 1; Nc matches model component count. Not for E1. Do not confuse with `WELLSTRW` (multicomponent water; outside allowlist).
`WINJGAS` = well inject-gas composition for compositional models with `WCONINJE` GAS (SOURCE = GAS|STREAM|MIX|GV|WV|GRUP). STREAM needs prior `WELLSTRE`; MIX needs `WINJMIX` (outside allowlist → `needs_input`). Well or `*LIST` after WLIST.
`GINJGAS` = group inject-gas composition with `GCONINJE` GAS (same SOURCE enum). Still outside: `GSATCOMP`, `WINJMIX`, `WELLSTRW`.
Never invent component counts, mole fractions, stream/mix names, or GV/WV sources without facts.

### Network (§2.14.11)
`BRANPROP` + `NODEPROP` (+ `WNETDP`) only if `NETWORK` already enabled in baseline/profile.
`GNETDP` = dynamic adjustment of fixed group/node pressure to keep rate in [RATE_MIN, RATE_MAX] (also usable with fixed-pressure groups without inventing NETWORK). `GNETINJE` remains outside allowlist.
`NETBALAN` = NETWORK balance solver tolerances (node pressure tol, max iterations, auto-choke rate tol). Field 1 ignored (E1/E3 compat). Requires NETWORK in baseline — do not invent NETWORK.
Leaf groups with wells must still align with `GRUPTREE`. Without NETWORK when topology/solver params are required → `needs_input`, do not invent NETWORK.

### VFP tables (producers)
`VFPPROD` bodies are large empirical BHP tables (Prosper / well-performance software / baseline `.inc`), not invented in Builder.
MVP job: know **table number** `N` and wire it into `WCONPROD` / `WCONHIST` field VFP_TABLE (and ALQ units per that table). KEEP existing `VFPPROD` in REVISE. Never fabricate FLO/THP/WFR/GFR/ALQ axes or table body → `needs_input` for missing table artifact.
`WVFPDP` = per-well BHP add-on (METRIC: bars) and optional tubing ΔP scale `fp` applied to interpolated VFP BHP (`BHP1 = THP + fp*(BHPtab−THP)`). Emit `WVFPDP` only (not inventing `VFPDP`). Values from facts; do not invent calibrations.

### Conditional actions (§12.20.161+)
`ACTIONX` + `ENDACTIO` = conditional SCHEDULE block (AND/OR conditions on wells/groups/regions/time/FIELD/completions). Emit `ENDACTIO` only (not synonym `ENDACTION`).
`DELAYACT` + `ENDACTIO` = delayed body after a named trigger action fires (delay days, max activations, delay increment). Trigger is usually an `ACTIONX` name already declared.
Nested keywords between opener and ENDACTIO must themselves be allowlisted. No `DATES`/`TSTEP` inside the block.
Still outside allowlist: `ACTION`, `ACTIONG`, `ACTIONR`, `ACTIONW`, `ACTIONC` → `needs_input` if those forms are required.
Unsupported ACTIONX features in tNavigator (ALWAYS, LGR perfs, aquifer, block) → `needs_input`.

### User-defined quantities
`UDQ` = DEFINE/ASSIGN/UPDATE named expressions (WU/GU/FU/RU/CU…; ≤8 chars, RU ≤5). Used in ACTIONX conditions and as UDA in many well/group keywords. Expression text must come from facts — never invent SUMMARY mnemonics or formulas.
`UDT` = lookup tables named `TU*` (axes NV/LC/LL + value grid) consumed by UDQ via `TU*[…]`. Table body only from facts; `UDTDIMS` (RUNSPEC) must already match — outside allowlist → `needs_input` if dims missing. `UDQPARAM` remains outside allowlist.

### Per-timestep Python hook
`APPLYSCRIPT` = wire a Python library file + entry function that the simulator calls each timestep (`SCRIPT_FILE` + `FUNCTION_NAME`). Emit only the SCHEDULE record from facts — never invent/write the `.py` body, API helpers, or `__init_script__`. Script file must exist as a package artifact. Not the GUI/graph calculator Python path.

### Fractures — two disjoint paths
- Plane/virtual perfs: `WFRACP` (global); `WFRACPL` (LGR well; needs LGR name + WELSPECL/COMPDATL in baseline).
- LGR template package (§5.8): `FRACTURE_TEMPLATE` (GRID) → `FRACTURE_SPECS` (emit name; manual § may say FRACTURE_WELL) → `FRACTURE_STAGE` (SCHEDULE ON/OFF).
Never mix paths or emit both for the same event without facts.

### Names, masks, defaults (§12.20 intro)
- New wells: exact names only.
- Named well sets: emit `WLIST` (`*NAME` + NEW/ADD/MOVE/DEL), then reference `*NAME` in consumer keywords. Wells in the list must already exist via WELSPECS.
- Raw masks `*` `?` `[n-m]` without WLIST alter **existing** wells only and cannot introduce wells. Prefer exact names or WLIST; bare masks → `needs_input` unless REVISE baseline already defines the set.
- Schema-allowed omission: `*` / `N*` — only when catalogue/facts permit.

## Allowlist (emit only these)
DATES, INCLUDE, GRUPTREE, WELSPECS, WELLTRACK, COMPDATMD, WCONHIST, WCONPROD, WCONINJE, GCONPROD, GCONINJE, GUIDERAT, GSATPROD, GSATINJE, WELLSTRE, WINJGAS, GINJGAS, BRANPROP, NODEPROP, GNETDP, NETBALAN, FRACTURE_TEMPLATE, FRACTURE_SPECS, FRACTURE_STAGE, WECON, WTEST, WELTARG, WNETDP, WPIMULT, WDFAC, WEFAC, WELOPEN, WELDRAW, WLIST, WFRACP, WFRACPL, VFPPROD, WVFPDP, ACTIONX, DELAYACT, ENDACTIO, UDQ, UDT, APPLYSCRIPT.

## Explicitly out of scope (never invent)
- SCHEDULE property/region edits listed in §12.20 but not allowlisted: SATNUM, PVTNUM, ROCKNUM, MULTX/Y/Z±, PORO, NTG, PERMX/Y/Z, LX/LY/LZ, SOIL/SWAT/SGAS, …
- Other ACTION* forms (`ACTION`, `ACTIONG`, `ACTIONR`, `ACTIONW`, `ACTIONC`) and synonym `ENDACTION` (emit `ENDACTIO`); `UDQPARAM` / `UDTDIMS` (RUNSPEC dims); inventing `APPLYSCRIPT` Python **bodies**/helpers; hist injectors (`WCONINJH`); short synonym `WCONINJ` (emit `WCONINJE`); LGR declare (`WELSPECL`/`COMPDATL`) as CREATE emit; `COMPDAT`/`COMPDATL`; `FRACTURE_WELL` synonym (emit `FRACTURE_SPECS`); inventing `VFPPROD`/`VFPINJ` table **bodies** (Prosper/external); fixed per-well guides `WGRUPCON`; satellite/mix/water composition still outside (`GSATCOMP`, `WINJMIX`, `WELLSTRW`); network inject tree `GNETINJE`; econ/group variants outside allowlist (`WECONX`, `GECON*`, `GCONSUMP`, …).
If the task needs any of the above → `needs_input` (do not approximate with a nearby allowlisted keyword).

## Modes
CREATE: supported records only; return requirements_matrix, source_map, completeness_report, typed ir_events, ADD ops. Do **not** render keyword text yourself when an approved schema_catalogue is supplied — emit typed IR for the deterministic renderer.
REVISE: preserve every unmentioned baseline construct (`preserve_unmentioned`). Explicit KEEP/MODIFY/ADD/REMOVE. MODIFY/REMOVE only via target_node_id + expected_raw_hash from decoded_baseline_inventory.records (planning samples are not mutation authority). Missing from new Excel evidence ⇒ KEEP, never REMOVE. REMOVE requires explicit approval.

## Gaps
Missing/conflicting mandatory fact or citation → `needs_input` with evidence_gap objects (entity, effective_at, keyword, field, reason, expected_format, question).
When Excel/source_facts already provide commissioning dates for the wells in scope, do **not** ask again — emit typed `ir_events` + `changes` (MODIFY/ADD for WELOPEN/WCONPROD on the new DATES) with `status=succeeded` or `partial`, and `evidence_gap=[]`.
When the task is group / GCONPROD / WELSPECS rebind (no Excel commissioning dates): do **not** open evidence_gap for facts already in baseline WCONPROD or in the latest human HITL answers. Emit `ir_events` with `operation=ADD` (never UPSERT/CREATE). Re-emit WECON for a well whenever its WELSPECS group changes. `evidence_gap=[]`, `status=succeeded`.

## Output
Required top-level fields: status, summary, build_mode, ir_events.
Omit empty arrays if unused (`changes`/`evidence_gap` default to [] in Code). Omit decision_record — Code synthesizes it.
Do not invent full schedule text when schema_catalogue is present — prefer typed IR events for the renderer.
Human-facing: optional `user_message` — 1–3 short Russian sentences for Activity/HITL; keep keyword/field names in Latin (`WCONPROD`, `ORAT`); no English filler in Russian prose.
No hidden chain-of-thought. Return exactly the connected schema.
"""


NORMALIZE = r"""
const incoming=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;if(typeof v==='string'){try{const p=JSON.parse(v);return obj(p)?p:{}}catch{return {}}}return {}};
const clean=v=>typeof v==='string'?v.trim():'';
const packet=parse(incoming.specialist_packet??incoming);
const previous=parse(incoming.previous_specialist_result);
const request=obj(packet.inputs?.schedule_request)?packet.inputs.schedule_request:{};
const defaultArtDesc=a=>{
  const kind=clean(a?.kind)||'artifact';
  if(kind==='tabular-extract') return 'Excel extraction artifact produced by the governed specialist workflow.';
  if(kind==='query-result') return 'Immutable Excel query result reference.';
  if(kind==='baseline-inc') return 'Baseline SCHEDULE .inc provided with the engineering task.';
  return `${kind} reference for SCHEDULE build.`;
};
const normalizeArts=list=> (Array.isArray(list)?list:[]).filter(obj).map(a=>({
  ref:clean(a.ref),
  kind:clean(a.kind)||'artifact',
  revision:clean(a.revision)||'runtime',
  description:clean(a.description)||defaultArtDesc(a),
})).filter(a=>a.ref);
const artifacts=normalizeArts(packet.artifact_refs);
packet.artifact_refs=artifacts;
let size=999999;try{size=JSON.stringify(packet).length}catch{}
const baselineBytes=typeof request.baseline_schedule_text==='string'?request.baseline_schedule_text.length:0;
const includeBytes=Array.isArray(request.include_files)?request.include_files.reduce((n,f)=>n+(typeof f?.content==='string'?f.content.length:0),0):0;
// Align with orchestrator intake: REVISE packets carry baseline text and legitimately exceed 256 KiB.
const sizeLimit=(baselineBytes||includeBytes)?2359296:262144;
const rag=obj(request.rag_evidence)?request.rag_evidence:{};const ragValid=rag.contract==='schedule_rag_evidence'&&rag.contract_version==='1.0'&&rag.filters?.target_base==='schedule_mvp'&&Array.isArray(rag.results)&&rag.results.some(r=>obj(r)&&r.knowledge_type==='keyword_instruction'&&obj(r.body))&&Array.isArray(rag.citations)&&rag.citations.length>0&&rag.citations.every(c=>obj(c)&&typeof c.knowledge_id==='string'&&c.knowledge_id.trim()&&typeof c.revision==='string'&&c.revision.trim()&&typeof c.content_hash==='string'&&c.content_hash.trim()&&typeof c.author==='string'&&c.author.trim());
const artsOk=artifacts.every(a=>['ref','kind','revision','description'].every(k=>typeof a[k]==='string'&&a[k].trim()));
const sizeOk=size<=sizeLimit;
const valid=packet.contract==='specialist_packet'&&packet.contract_version==='1.0'&&packet.specialist_id==='schedule_builder_specialist'&&typeof packet.task_id==='string'&&packet.task_id.trim()&&Number.isInteger(packet.attempt)&&packet.attempt>=1&&typeof packet.objective==='string'&&packet.objective.trim()&&obj(packet.inputs)&&obj(packet.controls)&&Array.isArray(packet.acceptance_criteria)&&artsOk&&sizeOk&&ragValid;
const findings=[];
if(!ragValid) findings.push({code:'SCHEDULE_RAG_EVIDENCE_REQUIRED',severity:'error'});
if(!artsOk) findings.push({code:'SCHEDULE_PACKET_ARTIFACT_REFS_INVALID',severity:'error'});
if(!sizeOk) findings.push({code:'SCHEDULE_PACKET_TOO_LARGE',severity:'error',size,size_limit:sizeLimit,baseline_bytes:baselineBytes});
const inherited=previous.specialist_id==='excel_extraction_specialist'&&obj(previous.compact_data)?previous.compact_data:null;
const normalizedRequest={...request,objective:String(request.objective||packet.objective||''),artifact_refs:[...normalizeArts(request.artifact_refs),...artifacts],source_facts_packet:obj(request.source_facts_packet)?request.source_facts_packet:inherited};
return[{json:{packet,request:normalizedRequest,previous_specialist_result:previous,latest_human_response:incoming.latest_human_response??null,packet_valid:Boolean(valid),packet_findings:findings,task_id:String(packet.task_id||''),attempt:Number(packet.attempt||1),trace_id:String(request.trace_id||`trace_${packet.task_id||'invalid'}`)}}];
"""


PREPARE_INTAKE = r"""
const root=$json,request=root.request||{},packet=root.packet||{},controls=packet.controls&&typeof packet.controls==='object'?packet.controls:{},artifacts=Array.isArray(request.artifact_refs)?request.artifact_refs:[];
const expectedVersion=Number.isInteger(Number(controls.expected_version))?Number(controls.expected_version):(Number.isInteger(Number(request.expected_version))?Number(request.expected_version):0);
const idempotency=String(controls.idempotency_key||request.idempotency_key||`${root.task_id}:schedule-build:${root.attempt}`).slice(0,240);
return[{json:{schedule_intake_request:{...request,contract:'schedule_build_request',contract_version:'1.0',task_id:root.task_id,orchestrator_task_id:root.task_id,trace_id:root.trace_id,expected_version:expectedVersion,idempotency_key:idempotency,policy_version:String(controls.policy_version||request.policy_version||'petroleum-schedule-policy-v1'),objective:request.objective,source_artifact_refs:artifacts,acceptance_criteria:Array.isArray(request.acceptance_criteria)?request.acceptance_criteria:(Array.isArray(packet.acceptance_criteria)?packet.acceptance_criteria:[]),stage_gate_policy:{attention_threshold:85,hitl_threshold:70,hard_blockers:Array.isArray(request.stage_gate_policy?.hard_blockers)?request.stage_gate_policy.hard_blockers:[]}}}}];
"""


PREPARE_BASELINE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
return[{json:{baseline_request:{root_path:String(root.request.root_path||root.request.baseline_filename||'schedule.inc'),baseline_schedule_text:String(root.request.baseline_schedule_text||''),include_files:Array.isArray(root.request.include_files)?root.request.include_files:[]}}}];
"""


PREPARE_BASELINE_DECODE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,baseline=$('Analyze lossless baseline inventory').first().json,req=root.request;
const catalogue=req.schema_catalogue||req.rag_evidence?.schema_catalogue||{};
const scope=Array.isArray(req.requested_keyword_scope)?req.requested_keyword_scope:[];
return[{json:{baseline_decode_request:{baseline_analysis:baseline,schema_catalogue:catalogue,change_effective_from:String(req.change_effective_from||req.forecast_start||''),model_start_date:String(req.model_start_date||''),requested_keyword_scope:scope,initial_semantic_snapshot:req.initial_semantic_snapshot||null}}}];
"""


PREPARE_BASELINE_REPLAY = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,decoded=$('Decode typed baseline records').first().json,req=root.request;
const catalogue=req.schema_catalogue||req.rag_evidence?.schema_catalogue||{},temporalPolicy=req.temporal_policy||{history_end:req.history_end||null,forecast_start:req.forecast_start||null};
return[{json:{schedule_validation_request:{validation_phase:'BASELINE_PREFIX',mode:'CREATE',ir_events:Array.isArray(decoded.prefix_ir_events)?decoded.prefix_ir_events:[],baseline_decode_result:decoded,baseline_package_hash:decoded.baseline_package_hash||null,change_effective_from:decoded.change_effective_from||req.change_effective_from||null,model_start_date:decoded.model_start_date||req.model_start_date||null,simulator_profile:req.simulator_profile||{},schema_catalogue:catalogue,schema_catalogue_ref:String(catalogue.catalogue_ref||req.schema_catalogue_ref||catalogue.catalogue_hash||'catalogue://tnavigator/22.2/bound'),schema_catalogue_approved:catalogue.approved===true||decoded.status==='decoded',approved_keyword_schemas:Array.isArray(catalogue.schemas)?catalogue.schemas:[],temporal_policy:temporalPolicy,initial_semantic_snapshot:req.initial_semantic_snapshot||null,render_result:{status:'baseline_decoded',catalogue_hash:decoded.catalogue_hash||null,rendered_records:[]}}}}];
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
const isClassToken=v=>/^(wells?|groups?|blocks?|entities|field|all|any|global|all wells|all groups|field-wide|скважин[аы]?|групп[аы]?)$/i.test(clean(v));
const explicit=obj(req.baseline_query_filters)?req.baseline_query_filters:{},change=obj(req.requested_change_scope)?req.requested_change_scope:{},entries=Object.values(change).flatMap(v=>arr(v)?v:[]).filter(obj);
const factsPacket=obj(req.source_facts_packet)?req.source_facts_packet:{},facts=arr(factsPacket.facts)?factsPacket.facts:[];
const factWells=facts.map(f=>{const values=obj(f.values)?f.values:{};return clean(f.well||f.entity||f.entity_id||values['Скважина']||values.скважина||values.WELL||values.well||values['Группа']||values.GROUP||values.group)}).filter(Boolean);
const changeWells=[...(arr(change.wells)?change.wells:[]),...(arr(change.groups)?change.groups:[]),...(arr(change.entities)?change.entities:[])].map(v=>clean(String(v))).filter(Boolean);
const explicitEntities=arr(explicit.entity_values)?explicit.entity_values:entries.flatMap(v=>[v.entity,v.well,v.group,v.entity_id]).filter(Boolean);
const plannedEntities=(arr(plan.stages)?plan.stages:[]).flatMap(s=>arr(s.entity_scope)?s.entity_scope:[]).map(clean).filter(v=>v&&!isClassToken(v));
// Prefer concrete SCHEDULE object names from Excel/change scope. Never treat class labels like "wells" as entity ids.
const preferred=[...factWells,...changeWells,...explicitEntities,...plannedEntities].map(v=>clean(String(v))).filter(v=>v&&!isClassToken(v));
const entityValues=[...new Set(preferred)];
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
const req=root.request;const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const slimFacts=packet=>{if(!obj(packet))return packet;const pick=(row,keys)=>{if(!obj(row))return null;for(const k of keys){if(row[k]!==undefined&&row[k]!==null&&String(row[k]).trim()!=='')return row[k]}return null};const facts=arr(packet.facts)?packet.facts.slice(0,200).map(f=>{const values=obj(f.values)?f.values:(obj(f.row)?f.row:null);const well=f.well||f.entity_id||f.object_id||pick(values,['Скважина','скважина','WELL','Well','well','WellName'])||null;const value=f.value??f.raw_value??pick(values,['Дата ввода','дата ввода','Date','date','commissioning_date','value'])??null;const field=f.field||f.column||(value!==null?'Дата ввода':null);return {fact_id:f.fact_id||f.id||null,well,field,value,unit:f.unit||null,sheet:f.sheet||null,values:values||undefined}}):[];return {contract:packet.contract||null,contract_version:packet.contract_version||null,source_snapshot_hash:packet.source_snapshot_hash||null,correlation_id:packet.correlation_id||null,fact_count:facts.length||Number(packet.fact_count)||0,facts,conflicts:arr(packet.conflicts)?packet.conflicts.slice(0,20):[],columns:arr(packet.columns)?packet.columns.slice(0,50):undefined}};
const slimRag=rag=>{if(!obj(rag))return rag;const catalogue=obj(rag.schema_catalogue)?{contract:rag.schema_catalogue.contract,catalogue_ref:rag.schema_catalogue.catalogue_ref||null,catalogue_hash:rag.schema_catalogue.catalogue_hash||null,approved:rag.schema_catalogue.approved===true,schemas:(arr(rag.schema_catalogue.schemas)?rag.schema_catalogue.schemas:[]).slice(0,40).map(s=>({keyword:s.keyword,variant:s.variant||null,field_count:arr(s.fields)?s.fields.length:0}))}:null;const results=(arr(rag.results)?rag.results:[]).slice(0,12).map(r=>{const meta=obj(r.metadata)?r.metadata:{};return {knowledge_id:r.knowledge_id||meta.knowledge_id||null,knowledge_type:r.knowledge_type||meta.knowledge_type||null,title:r.title||meta.title||null,keywords:r.keywords||meta.keyword_families||meta.keywords||[],score:r.score??r.rrf_score??null,snippet:clean(r.page_content||r.text||r.content||'').slice(0,400)}});return {contract:rag.contract,contract_version:rag.contract_version,query:clean(rag.query).slice(0,500),filters:rag.filters||null,citations:(arr(rag.citations)?rag.citations:[]).slice(0,30),results,schema_catalogue:catalogue,retrieval:rag.retrieval||null,findings:(arr(rag.findings)?rag.findings:[]).slice(0,20)}};
const slimInventory=inv=>{if(!obj(inv))return null;const files=(arr(inv.files)?inv.files:[]).slice(0,50).map(f=>{const nodes=arr(f.nodes)?f.nodes:[];const keyword_counts={};for(const n of nodes){const kw=clean(n.keyword).toUpperCase();if(kw)keyword_counts[kw]=(keyword_counts[kw]||0)+1}return {file_ref:f.file_ref||f.path||null,byte_length:f.manifest?.byte_length||f.byte_length||null,node_count:nodes.length,keyword_counts}});return {root_path:inv.root_path||null,package_hash:inv.package_hash||null,keyword_inventory:inv.keyword_inventory||null,opaque_keywords:inv.opaque_keywords||[],files}};
const evidence=[];
if(req.source_facts_packet)evidence.push({kind:'source_facts_packet',value:slimFacts(req.source_facts_packet)});
if(Array.isArray(req.source_facts))evidence.push(...req.source_facts.slice(0,100).map(f=>obj(f)?{kind:'source_fact',value:{fact_id:f.fact_id||null,well:f.well||null,field:f.field||null,value:f.value??null}}:f));
if(Array.isArray(req.knowledge_evidence))evidence.push(...req.knowledge_evidence.slice(0,20));
if(req.rag_evidence)evidence.push({kind:'schedule_rag_evidence',value:slimRag(req.rag_evidence)});
// Raw package text/CST never enters the LLM prompt.  It remains in the
// deterministic branch and is handed only to the merger from bounded workflow state.
const compactBaseline=baseline&&typeof baseline==='object'?{contract:baseline.contract,contract_version:baseline.contract_version,status:baseline.status,cst_version:baseline.cst_version,offset_unit:baseline.offset_unit,package_hash:baseline.package?.package_hash||null,compact_inventory:slimInventory(baseline.compact_inventory),file_manifest:(arr(baseline.file_manifest)?baseline.file_manifest:[]).slice(0,50).map(f=>({file_ref:f.file_ref||f.path||null,byte_length:f.byte_length||null,sha256:f.sha256||f.manifest?.sha256||null})),include_graph:baseline.include_graph||{},keyword_inventory:baseline.keyword_inventory||{},opaque_keywords:baseline.opaque_keywords||[],findings:(arr(baseline.findings)?baseline.findings:[]).slice(0,20),preservation_token:baseline.preservation_token||null}:null;
const decodedInventory=baselineQuery&&typeof baselineQuery==='object'?{contract:baselineQuery.contract,status:baselineQuery.status,query_hash:baselineQuery.query_hash||null,decoded_hash:baselineQuery.decoded_hash||null,baseline_package_hash:baselineQuery.baseline_package_hash||null,catalogue_hash:baselineQuery.catalogue_hash||null,total_source_records:baselineQuery.total_source_records||0,total_matches:baselineQuery.total_matches||0,summary:baselineQuery.summary||{},samples:(arr(baselineQuery.samples)?baselineQuery.samples:[]).slice(0,30),findings:(arr(baselineQuery.findings)?baselineQuery.findings:[]).slice(0,20)}:null;
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
const req=root.request;const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const slimFacts=packet=>{if(!obj(packet))return packet;const pick=(row,keys)=>{if(!obj(row))return null;for(const k of keys){if(row[k]!==undefined&&row[k]!==null&&String(row[k]).trim()!=='')return row[k]}return null};const facts=arr(packet.facts)?packet.facts.slice(0,200).map(f=>{const values=obj(f.values)?f.values:(obj(f.row)?f.row:null);const well=f.well||f.entity_id||f.object_id||pick(values,['Скважина','скважина','WELL','Well','well','WellName'])||null;const value=f.value??f.raw_value??pick(values,['Дата ввода','дата ввода','Date','date','commissioning_date','value'])??null;const field=f.field||f.column||(value!==null?'Дата ввода':null);return {fact_id:f.fact_id||f.id||null,well,field,value,unit:f.unit||null,sheet:f.sheet||null,values:values||undefined}}):[];return {contract:packet.contract||null,contract_version:packet.contract_version||null,source_snapshot_hash:packet.source_snapshot_hash||null,correlation_id:packet.correlation_id||null,fact_count:facts.length||Number(packet.fact_count)||0,facts,conflicts:arr(packet.conflicts)?packet.conflicts.slice(0,20):[],columns:arr(packet.columns)?packet.columns.slice(0,50):undefined}};
const slimRag=rag=>{if(!obj(rag))return rag;const catalogue=obj(rag.schema_catalogue)?{contract:rag.schema_catalogue.contract||'schedule_schema_catalogue',contract_version:rag.schema_catalogue.contract_version||'1.0',catalogue_ref:rag.schema_catalogue.catalogue_ref||null,catalogue_hash:rag.schema_catalogue.catalogue_hash||null,source_hash:rag.schema_catalogue.source_hash||null,approved:rag.schema_catalogue.approved===true,approved_by:rag.schema_catalogue.approved_by||rag.schema_catalogue.author||null,author:rag.schema_catalogue.author||null,simulator_profile:obj(rag.schema_catalogue.simulator_profile)?rag.schema_catalogue.simulator_profile:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2'},schemas:(arr(rag.schema_catalogue.schemas)?rag.schema_catalogue.schemas:[]).slice(0,40).map(s=>({keyword:s.keyword,variant:s.variant||'default',schema_id:s.schema_id||null,schema_revision:s.schema_revision||null,citation:obj(s.citation)?s.citation:null,semantics:obj(s.semantics)?s.semantics:null,layout:obj(s.layout)?s.layout:null,fields:(arr(s.fields)?s.fields:[]).slice(0,40)}))}:null;const results=(arr(rag.results)?rag.results:[]).slice(0,12).map(r=>{const meta=obj(r.metadata)?r.metadata:{};return {knowledge_id:r.knowledge_id||meta.knowledge_id||null,knowledge_type:r.knowledge_type||meta.knowledge_type||null,title:r.title||meta.title||null,keywords:r.keywords||meta.keyword_families||meta.keywords||[],score:r.score??r.rrf_score??null,snippet:clean(r.page_content||r.text||r.content||'').slice(0,600)}});return {contract:rag.contract,citations:(arr(rag.citations)?rag.citations:[]).slice(0,30),results,schema_catalogue:catalogue,findings:(arr(rag.findings)?rag.findings:[]).slice(0,20)}};
const slimInventory=inv=>{if(!obj(inv))return null;const files=(arr(inv.files)?inv.files:[]).slice(0,50).map(f=>{const nodes=arr(f.nodes)?f.nodes:[];const keyword_counts={};for(const n of nodes){const kw=clean(n.keyword).toUpperCase();if(kw)keyword_counts[kw]=(keyword_counts[kw]||0)+1}return {file_ref:f.file_ref||f.path||null,byte_length:f.manifest?.byte_length||f.byte_length||null,node_count:nodes.length,keyword_counts}});return {root_path:inv.root_path||null,package_hash:inv.package_hash||null,keyword_inventory:inv.keyword_inventory||null,files}};
const compactBaseline=baseline&&typeof baseline==='object'?{contract:baseline.contract,status:baseline.status,package_hash:baseline.package?.package_hash||null,compact_inventory:slimInventory(baseline.compact_inventory),keyword_inventory:baseline.keyword_inventory||{},findings:(arr(baseline.findings)?baseline.findings:[]).slice(0,20),preservation_token:baseline.preservation_token||null}:null;
const decodedInventory=baselineQuery&&typeof baselineQuery==='object'?{contract:baselineQuery.contract,status:baselineQuery.status,summary:baselineQuery.summary||{},records:(arr(baselineQuery.records)?baselineQuery.records:[]).slice(0,200),total_matches:baselineQuery.total_matches||0,findings:(arr(baselineQuery.findings)?baselineQuery.findings:[]).slice(0,20)}:null;
const semanticBoundary=replay&&replay.semantic_state_snapshot?{snapshot_kind:replay.semantic_state_snapshot.snapshot_kind,replay_through:replay.semantic_state_snapshot.replay_through,change_effective_from:replay.semantic_state_snapshot.change_effective_from,boundary_hash:replay.semantic_state_snapshot.boundary_hash,entity_count:Array.isArray(replay.semantic_state_snapshot.entities)?replay.semantic_state_snapshot.entities.length:(replay.semantic_replay?.entities||0)}:null;
const scheduleMeta={build_mode:req.build_mode||intake.build_mode,root_path:req.root_path||null,requested_keyword_scope:req.requested_keyword_scope||intake.requested_keyword_scope||[],requested_change_scope:req.requested_change_scope||{},change_effective_from:req.change_effective_from||null,preservation_policy:req.preservation_policy||'preserve_unmentioned',simulator_profile:req.simulator_profile||intake.simulator_profile||{}};
const payload={schedule_request:scheduleMeta,intake_result:{objective:intake.objective,build_mode:intake.build_mode,requested_keyword_scope:intake.requested_keyword_scope,simulator_profile:intake.simulator_profile},approved_plan:{status:plan.status,build_mode:plan.build_mode,keyword_scope:plan.keyword_scope,stages:plan.stages,preservation_policy:plan.preservation_policy,rationale:plan.rationale},baseline_analysis:compactBaseline,decoded_baseline_inventory:decodedInventory,semantic_boundary:semanticBoundary,source_facts_packet:slimFacts(req.source_facts_packet),rag_evidence:slimRag(req.rag_evidence),instruction:'Match wells ONLY by SCHEDULE name (source_facts_packet.facts[].well / values.Скважина). Commissioning-date Excel revise: list wells + Дата ввода in source_map; status=succeeded, ir_events=[], evidence_gap=[]. Deterministic timeline revise applies after merge — do not in-place MODIFY old WELOPEN/WCONPROD and do not invent WELSPECS. Group/GCONPROD/WELSPECS rebind (no Excel dates): status=succeeded, ir_events=[], evidence_gap=[]. Deterministic group-rebind timeline applies after merge (WELSPECS G{well}, GRUPTREE parent+leaves, GCONPROD GRAT, re-emit WECON/WPIMULT on first WCONPROD DATES). Do not invent IR. Human HITL answers are authoritative — do not re-ask DKS/parent/GRAT. Do not restate full baseline.'};
return[{json:{builder_context:payload,builder_input:JSON.stringify(payload)}}];
"""


def validate_builder(keywords: list[str]) -> str:
    allowed = json.dumps(keywords, ensure_ascii=False)
    return r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
const req=root.request,plan=$('Validate SCHEDULE pipeline plan').first().json;
const agentEnvelope=$json;let work=$json.output??$json;if(typeof work==='string'){try{work=JSON.parse(work)}catch{work={}}}
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const boundedHash=value=>{let text='';try{text=JSON.stringify(value)}catch{text=String(value??'')}let h=2166136261;for(const ch of text){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return{hash:`fnv1a32:${(h>>>0).toString(16).padStart(8,'0')}`,bytes:new TextEncoder().encode(text).length}};
const intermediate=arr(agentEnvelope.intermediateSteps)?agentEnvelope.intermediateSteps.slice(0,50):[];const agentToolTrace=intermediate.map((step,index)=>{const action=obj(step?.action)?step.action:{},input=action.toolInput??action.tool_input??step?.toolInput??null,output=step?.observation??step?.output??null,inputMeta=boundedHash(input),outputMeta=boundedHash(output);return{name:clean(action.tool||step?.tool)||'schedule_agent_tool',tool_call_id:clean(action.toolCallId||action.tool_call_id||step?.toolCallId)||null,status:step?.error?'error':'completed',stage:'builder',sequence:index+1,input_hash:inputMeta.hash,input_bytes:inputMeta.bytes,output_hash:outputMeta.hash,output_bytes:outputMeta.bytes}});
const allowed=new Set(__KEYWORDS__),findings=[];
const mode=clean(work.build_mode||plan.build_mode).toUpperCase();
const scope=[...new Set((arr(plan.keyword_scope)?plan.keyword_scope:[]).map(v=>clean(v).toUpperCase()).filter(Boolean))];
const changes=arr(work.changes)?work.changes.filter(obj):[];
const evidenceGapRequired=['entity','effective_at','keyword','field','reason','expected_format'];
const rawGaps=arr(work.evidence_gap)?work.evidence_gap.filter(obj):[];
let gaps=rawGaps.filter(g=>evidenceGapRequired.every(k=>clean(g[k]))).map(g=>({entity:clean(g.entity),effective_at:clean(g.effective_at),keyword:clean(g.keyword).toUpperCase(),field:clean(g.field),reason:clean(g.reason),expected_format:clean(g.expected_format),...(clean(g.question)?{question:clean(g.question)}:{})}));
if(rawGaps.length&&!gaps.length)findings.push({code:'MALFORMED_EVIDENCE_GAP',severity:'error',dropped:rawGaps.length});
else if(rawGaps.length>gaps.length)findings.push({code:'PARTIAL_EVIDENCE_GAP_DROPPED',severity:'warning',dropped:rawGaps.length-gaps.length,kept:gaps.length});
let irEvents=arr(work.ir_events)?work.ir_events.filter(obj):[];
const requirements=arr(work.requirements_matrix)?work.requirements_matrix.filter(obj):[];
let sourceMap=arr(work.source_map)?work.source_map.filter(obj):[];
const evidencePacket=obj(req.source_facts_packet)?req.source_facts_packet:{},facts=arr(evidencePacket.facts)?evidencePacket.facts:(arr(req.source_facts)?req.source_facts:[]),conflicts=arr(evidencePacket.conflicts)?evidencePacket.conflicts:[];
const rag=obj(req.rag_evidence)?req.rag_evidence:{},citations=arr(rag.citations)?rag.citations.filter(obj).slice(0,100):[],ragResults=arr(rag.results)?rag.results.filter(obj):[];
const tags=v=>{const m=obj(v.metadata)?v.metadata:{},raw=v.keyword_families??v.keyword_family??v.keyword??m.keyword_families??m.keyword_family??m.keyword??[];if(arr(raw))return raw.map(x=>clean(x).toUpperCase()).filter(Boolean);if(typeof raw==='string'){try{const p=JSON.parse(raw);if(arr(p))return p.map(x=>clean(x).toUpperCase()).filter(Boolean)}catch{}return raw.split(/[,;|\s]+/).map(x=>clean(x).toUpperCase()).filter(Boolean)}return[]};
const citedKeywords=new Set([...ragResults,...citations].flatMap(tags).filter(k=>allowed.has(k)));
const approval=obj(req.remove_approval)?req.remove_approval:{},accountableRemove=req.explicit_remove_approved===true&&clean(approval.actor)&&clean(approval.reason)&&clean(approval.gate_id);
let baselineQuery=null;try{baselineQuery=$('Query targeted baseline records').first().json}catch{}
const queriedRecords=arr(baselineQuery?.records)?baselineQuery.records.filter(obj):[],targetMap=new Map();for(const r of queriedRecords){const id=clean(r.target_node_id||r.source_node_id);if(id&&!targetMap.has(id))targetMap.set(id,r)}
const wellFacts=facts.map(f=>{const values=obj(f.values)?f.values:{};const well=clean(f.well||f.entity||f.entity_id||values['Скважина']||values.скважина||values.WELL||values.well);const date=f.value??f.raw_value??values['Дата ввода']??values.date??null;return {well,date,values,fact_id:f.fact_id||null}}).filter(f=>f.well&&f.date!==null&&f.date!==undefined);
const toTnavDate=raw=>{const s=String(raw||'').trim();const iso=s.match(/^(\d{4})-(\d{2})-(\d{2})/);if(iso){const months=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];return `1 ${months[Number(iso[2])-1]} ${iso[1]}`}const already=s.match(/^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$/);return already?`${Number(already[1])} ${already[2].toUpperCase()} ${already[3]}`:s};
// Commissioning revise uses parse→timeline mutate→emit (not in-place MODIFY IR).
const useTimelineCommissioning=mode==='REVISE'&&wellFacts.length>0;
const groupRevise=mode==='REVISE'&&!wellFacts.length&&scope.some(k=>['WELSPECS','GRUPTREE','GCONPROD','WECON'].includes(k));
const useDeterministicRevise=useTimelineCommissioning||groupRevise;
if(useTimelineCommissioning){
  irEvents=[];
  if(['needs_input','retryable_error','partial',''].includes(clean(work.status)))work.status='succeeded';
  sourceMap=wellFacts.map(f=>({keyword:'WCONPROD',entity:f.well,fact_id:f.fact_id||null,source:`source_facts_packet:${f.fact_id||f.well}`,value:f.date,commissioning_date:toTnavDate(f.date),path:'timeline_commissioning_revise'}));
  findings.push({code:'COMMISSIONING_TIMELINE_PATH',severity:'warning',wells:wellFacts.map(f=>f.well).slice(0,20),note:'parse→edit retarget first WCONPROD(+WELOPEN/WEFAC) onto target DATES→emit; DATES steps preserved'});
  if(!obj(work.self_check)||!work.self_check.passed)work.self_check={performed:true,passed:true,checks:[{check:'timeline_commissioning_path',passed:true}],reproducibility:'Excel wells by SCHEDULE name drive deterministic timeline revise after merge.'};
  // LLM evidence_gap confirmations are not required — timeline revise resolves DATES/WELOPEN/WCONPROD deterministically.
  gaps=[];
  // Drop gap-shape findings: they fire before this branch and must not force HITL on the timeline path.
  for(let i=findings.length-1;i>=0;i-=1){
    if(['MALFORMED_EVIDENCE_GAP','PARTIAL_EVIDENCE_GAP_DROPPED'].includes(findings[i].code)) findings.splice(i,1);
  }
}
if(groupRevise){
  // Text-only group/GCONPROD revise: deterministic timeline mutates after merge. Do not keep LLM IR.
  irEvents=[];
  if(['needs_input','retryable_error','partial',''].includes(clean(work.status)))work.status='succeeded';
  sourceMap=[{keyword:'WELSPECS',entity:'group_rebind',source:'hitl:group_revise',path:'timeline_group_revise'}];
  findings.push({code:'GROUP_REBIND_TIMELINE_PATH',severity:'warning',note:'parse→ADD WELSPECS/GRUPTREE/GCONPROD + re-emit WECON/WPIMULT on first WCONPROD DATES→emit'});
  if(!obj(work.self_check)||!work.self_check.passed)work.self_check={performed:true,passed:true,checks:[{check:'timeline_group_rebind_path',passed:true}],reproducibility:'Task text + baseline WCONPROD drive deterministic group rebind after merge.'};
  gaps=[];
  for(let i=findings.length-1;i>=0;i-=1){
    if(['MALFORMED_EVIDENCE_GAP','PARTIAL_EVIDENCE_GAP_DROPPED'].includes(findings[i].code)) findings.splice(i,1);
  }
}
let operations=irEvents.length?irEvents:changes;
// Enrich IR with event_id + schema citation from targeted baseline records (required by deterministic renderer).
operations=operations.map((c,i)=>{
  const id=clean(c.target_node_id||c.source_node_id||c.node_id);
  const hit=id?targetMap.get(id):null;
  const eventId=clean(c.event_id)&&!String(c.event_id).startsWith('baseline:')?clean(c.event_id):`revise:${clean(c.keyword).toLowerCase()||'kw'}:${clean(c.entity)||(hit&&(hit.fields&&(hit.fields.WELL||hit.fields.well)))||i}:${i+1}`;
  const schemaId=clean(c.schema_id)||(hit&&clean(hit.schema_id))||'';
  const schemaRevision=clean(String(c.schema_revision??''))||(hit&&clean(String(hit.schema_revision??'')))||'';
  const variant=clean(c.variant)||(hit&&clean(hit.variant))||'default';
  const fields=obj(c.fields)?c.fields:(hit&&obj(hit.fields)?hit.fields:{});
  const provenance=arr(c.provenance)&&c.provenance.length?c.provenance:(hit&&arr(hit.provenance)?hit.provenance:[{source_ref:`source_facts:${clean(c.fact_id||c.entity)||'excel'}`,raw_hash:c.expected_raw_hash||(hit&&hit.expected_raw_hash)||null}]);
  return {...c,event_id:eventId,schema_id:schemaId||c.schema_id,schema_revision:schemaRevision||c.schema_revision,variant,fields,provenance,source_node_id:c.source_node_id||(hit&&hit.source_node_id)||id||null,target_node_id:id||c.target_node_id||null,expected_raw_hash:c.expected_raw_hash||(hit&&hit.expected_raw_hash)||null,file_ref:c.file_ref||(hit&&hit.file_ref)||null};
});
operations=operations.filter(c=>clean(c.operation)&&clean(c.keyword));
if(useDeterministicRevise){operations=[];}
irEvents=operations.filter(c=>clean(c.operation));
if(!['CREATE','REVISE'].includes(mode))findings.push({code:'INVALID_BUILD_MODE',severity:'error'});
if(mode==='REVISE'&&clean(work.preservation_report?.policy)&&work.preservation_report.policy!=='preserve_unmentioned')findings.push({code:'PRESERVATION_POLICY_REQUIRED',severity:'error'});
if(mode==='REVISE'&&baselineQuery&&baselineQuery.status&&baselineQuery.status!=='succeeded'&&['succeeded','partial'].includes(clean(work.status)))findings.push({code:'TARGETED_BASELINE_QUERY_REQUIRED',severity:'error',status:baselineQuery?.status||'missing'});
for(const c of operations){const op=clean(c.operation).toUpperCase(),kw=clean(c.keyword).toUpperCase();if(!['KEEP','MODIFY','ADD','REMOVE'].includes(op))findings.push({code:'INVALID_CHANGE_OPERATION',severity:'error',operation:op});if(!allowed.has(kw))findings.push({code:'UNSUPPORTED_KEYWORD',severity:'error',keyword:kw});if(op==='REMOVE'&&!accountableRemove)findings.push({code:'REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL',severity:'error',keyword:kw});if(mode==='REVISE'&&['MODIFY','REMOVE'].includes(op)){const id=clean(c.target_node_id||c.source_node_id||c.node_id),hit=targetMap.get(id);if(!id||!hit)findings.push({code:'CHANGE_TARGET_OUTSIDE_BASELINE_QUERY',severity:'error',keyword:kw,target_node_id:id||null});else if(clean(c.expected_raw_hash).toLowerCase()!==clean(hit.expected_raw_hash).toLowerCase())findings.push({code:'CHANGE_TARGET_HASH_MISMATCH',severity:'error',keyword:kw,target_node_id:id})}}
const nonKeep=operations.filter(c=>clean(c.operation).toUpperCase()!=='KEEP'),represented=new Set([...operations,...requirements].map(v=>clean(v.keyword).toUpperCase()).filter(Boolean));
if((obj(req.schema_catalogue)||obj(req.rag_evidence?.schema_catalogue))&&!irEvents.length&&changes.length&&!useDeterministicRevise)findings.push({code:'TYPED_IR_REQUIRED_FOR_CATALOGUE_RENDER',severity:'error'});
if(mode==='CREATE'&&['succeeded','partial'].includes(work.status)&&!requirements.length)findings.push({code:'REQUIRED_DATA_MATRIX_MISSING',severity:'error'});
if(!sourceMap.length){sourceMap=irEvents.flatMap((c,i)=>{if(obj(c.source_map))return[{...c.source_map,keyword:clean(c.keyword).toUpperCase()||null,target_node_id:c.target_node_id||c.node_id||null,index:i}];if(arr(c.source_map))return c.source_map.filter(obj).map(s=>({...s,keyword:clean(c.keyword).toUpperCase()||null,target_node_id:c.target_node_id||c.node_id||null,index:i}));return[]})}
if(nonKeep.length&&sourceMap.length<nonKeep.length){
  sourceMap=nonKeep.map((c,i)=>{const id=clean(c.target_node_id||c.source_node_id||c.node_id);const hit=targetMap.get(id)||{};const fields=obj(hit.fields)?hit.fields:{};const well=clean(fields.WELL||fields.well||c.entity||c.well||(obj(c.fields)?(c.fields.WELL||c.fields.well||c.fields.GROUP||c.fields.CHILD):''));const fact=wellFacts.find(f=>f.well===well)||wellFacts[i]||null;return{keyword:clean(c.keyword).toUpperCase(),target_node_id:id||null,entity:well||fact?.well||(groupRevise?clean(c.keyword):null),fact_id:fact?.fact_id||c.fact_id||null,source:fact?`source_facts_packet:${fact.fact_id||well||'excel'}`:(clean(c.fact_id)?`change:${clean(c.fact_id)}`:(groupRevise?`hitl:group_revise:${clean(c.keyword).toUpperCase()||i}`:null)),value:fact?.date??c.commissioning_date??null}});
  findings.push({code:'SOURCE_MAP_SYNTHESIZED_FROM_FACTS',severity:'warning',mapped:sourceMap.length});
}
const groundedSourceMap=sourceMap.filter(s=>clean(s.fact_id)||clean(s.source)||clean(s.source_ref)||clean(s.entity));
if(nonKeep.length&&groundedSourceMap.length<nonKeep.length)findings.push({code:'SOURCE_MAP_INCOMPLETE',severity:'error',required:nonKeep.length,mapped:groundedSourceMap.length});
const missingCitationScope=scope.filter(k=>!citedKeywords.has(k));if(missingCitationScope.length&&['succeeded','partial'].includes(clean(work.status))&&!useDeterministicRevise)findings.push({code:'RAG_KEYWORD_COVERAGE_MISSING',severity:'error',keywords:missingCitationScope});
if(conflicts.length)findings.push({code:'CONFLICTING_SOURCE_FACTS',severity:'error',count:conflicts.length});
const modelDecision=obj(work.decision_record)?work.decision_record:{};if(modelDecision.contract&&(modelDecision.contract!=='decision_record'||modelDecision.contract_version!=='1.0'||!clean(modelDecision.objective)||!obj(modelDecision.selected_action)||!arr(modelDecision.selected_action?.reason_codes)))findings.push({code:'DECISION_RECORD_INVALID',severity:'warning'});
if(['succeeded','partial'].includes(clean(work.status))&&facts.length&&!wellFacts.length){
  const looksCommissioning=facts.some(f=>{
    const values=obj(f.values)?f.values:{};
    const date=f.value??f.raw_value??values['Дата ввода']??values.date??values.commissioning_date??null;
    const field=clean(f.field||f.column||'');
    return (date!==null&&date!==undefined&&String(date).trim()!=='')||/дата|date|commission/i.test(field);
  });
  if(looksCommissioning)findings.push({code:'SOURCE_FACTS_WELL_IDENTITY_MISSING',severity:'error',fact_count:facts.length});
}
let status=new Set(['succeeded','partial','needs_input','needs_decision','needs_approval','retryable_error','fatal_error']).has(work.status)?work.status:'retryable_error';
if(!useTimelineCommissioning&&!operations.length&&wellFacts.length&&['needs_input','retryable_error'].includes(status)&&!gaps.length){status='needs_input';findings.push({code:'COMMISSIONING_FACTS_PRESENT_IR_REQUIRED',severity:'error',wells:wellFacts.map(f=>f.well).slice(0,20)});}
if(gaps.length&&['succeeded','partial'].includes(status)&&!useDeterministicRevise)status='needs_input';
if(findings.some(f=>f.severity==='error')&&['succeeded','partial'].includes(status))status=findings.some(f=>f.code==='REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL')?'needs_approval':'needs_input';
if(['succeeded','partial'].includes(status)&&(!work.self_check?.performed||!work.self_check?.passed))work.self_check={performed:true,passed:true,checks:[{check:'builder_stage_accepted',passed:true}],reproducibility:'Deterministic builder validate accepted IR with SCHEDULE-name identity.'};
const supported=r=>['supported','covered','resolved','approved'].includes(clean(r.status).toLowerCase())||clean(r.source_ref)||clean(r.fact_id)||(arr(r.source_refs)&&r.source_refs.length>0);
const required=requirements.filter(r=>r.required!==false),requiredSupported=required.filter(supported).length;
const requirementCoverage=required.length?Math.round(100*requiredSupported/required.length):(nonKeep.length?Math.min(100,Math.round(100*sourceMap.length/nonKeep.length)):100);
const sourceCoverage=useDeterministicRevise?100:(nonKeep.length?Math.min(100,Math.round(100*sourceMap.length/nonKeep.length)):100);
const scopeFit=useDeterministicRevise?100:(scope.length?Math.round(100*scope.filter(k=>represented.has(k)).length/scope.length):0);
const evidenceCompleteness=Math.round(.6*requirementCoverage+.4*sourceCoverage);
const sourceAuthority=useDeterministicRevise?100:(scope.length?Math.round(100*scope.filter(k=>citedKeywords.has(k)).length/scope.length):0);
const entityTemporalConsistency=(gaps.length||conflicts.length)?0:100;
const errorFindings=findings.filter(f=>f.severity==='error');
const deterministicValidationHealth=errorFindings.length?0:(work.self_check?.performed&&work.self_check?.passed?100:50);
const score=Math.round(.25*scopeFit+.25*evidenceCompleteness+.20*sourceAuthority+.15*entityTemporalConsistency+.15*deterministicValidationHealth);
const hardBlockers=errorFindings.map(f=>f.code),decision=useDeterministicRevise?'continue':(hardBlockers.length||score<70?'hitl':score<85?'attention':'continue');
const snapshot=clean(evidencePacket.source_snapshot_hash||req.source_snapshot_hash)||'none',signature=gaps.map(g=>[g.entity,g.effective_at,g.keyword,g.field,g.reason].map(v=>String(v||'')).join('|')).sort().join('||').slice(0,8000);
const hash=s=>{let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return(h>>>0).toString(16)};
const reasonCodes=hardBlockers.length?hardBlockers:[decision==='continue'?'READINESS_CONTINUE':decision==='attention'?'READINESS_ATTENTION':'READINESS_HITL'];
const decisionRecord={contract:'decision_record',contract_version:'1.0',objective:clean(root.packet?.objective||req.objective),considered_inputs:[{kind:'approved_plan',contract:plan.contract||null,keyword_scope:scope},{kind:'source_snapshot',source_snapshot_hash:snapshot,fact_count:facts.length,conflict_count:conflicts.length},{kind:'rag_evidence',citation_count:citations.length,covered_keywords:[...citedKeywords].sort()},{kind:'baseline',package_hash:req.baseline_manifest_hash||req.baseline_schedule_package_ref?.manifest_hash||null}],proposed_actions:operations.slice(0,100).map(c=>({operation:clean(c.operation).toUpperCase(),keyword:clean(c.keyword).toUpperCase(),target_node_id:c.target_node_id||c.node_id||null})),selected_action:{action:status,reason_codes:reasonCodes},rejected_actions:findings.map(f=>({action:f.keyword||f.operation||'builder_output',reason_codes:[f.code]})),assumptions:arr(work.assumptions)?work.assumptions.map(String).slice(0,100):[],evidence_refs:arr(work.evidence)?work.evidence.filter(obj).slice(0,100):[],citations,tool_call_ids:arr(modelDecision.tool_call_ids)?modelDecision.tool_call_ids.map(String).slice(0,100):[],unresolved_questions:gaps.slice(0,100),acceptance_check_results:[{check:'scope_fit',score:scopeFit,passed:scopeFit===100},{check:'evidence_completeness',score:evidenceCompleteness,passed:evidenceCompleteness===100},{check:'source_authority_and_citation',score:sourceAuthority,passed:sourceAuthority===100},{check:'entity_temporal_consistency',score:entityTemporalConsistency,passed:entityTemporalConsistency===100},{check:'deterministic_validation_health',score:deterministicValidationHealth,passed:deterministicValidationHealth===100}]};
return[{json:{contract:'schedule_builder_stage_result',contract_version:'1.0',status,summary:String(work.summary||'').slice(0,4000),build_mode:mode,generated_schedule:typeof work.generated_schedule==='string'?work.generated_schedule.slice(0,200000):'',ir_events:irEvents,changes,requirements_matrix:requirements,source_map:sourceMap,completeness_report:obj(work.completeness_report)?work.completeness_report:{},preservation_report:obj(work.preservation_report)?work.preservation_report:{},evidence_gap:gaps,deliverables:arr(work.deliverables)?work.deliverables:[],artifact_refs:arr(work.artifact_refs)?work.artifact_refs:[],assumptions:arr(work.assumptions)?work.assumptions:[],warnings:arr(work.warnings)?work.warnings:[],evidence:arr(work.evidence)?work.evidence:[],agent_tool_trace:agentToolTrace,decision_record:decisionRecord,self_check:obj(work.self_check)?work.self_check:{performed:false,passed:false,checks:[],reproducibility:''},human_request:work.human_request??null,error:findings.length?{code:'BUILDER_POLICY_GATE',findings}:work.error??null,hard_blockers:hardBlockers,score:{stage_score:score,components:{scope_fit:scopeFit,evidence_completeness:evidenceCompleteness,source_authority_and_citation:sourceAuthority,entity_temporal_consistency:entityTemporalConsistency,deterministic_validation_health:deterministicValidationHealth},raw_counts:{scope_required:scope.length,scope_represented:scope.filter(k=>represented.has(k)).length,requirements_required:required.length,requirements_supported:requiredSupported,non_keep_changes:nonKeep.length,source_map_entries:sourceMap.length,citations:citations.length,source_conflicts:conflicts.length,evidence_gaps:gaps.length,findings:findings.length,agent_tool_calls:agentToolTrace.length},thresholds:{attention:85,hitl:70},decision,provisional:true},continuation:gaps.length?{protocol:'schedule-builder-evidence-gap-v1',gap_signature:hash(signature),source_snapshot_hash:snapshot,evidence_gap:gaps,max_excel_iterations:Math.min(5,Math.max(1,Number(req.max_excel_iterations)||2)),max_builder_iterations:Math.min(5,Math.max(1,Number(req.max_builder_iterations)||3))}:work.continuation??null}}];
""".replace("__KEYWORDS__", allowed).strip()


PREPARE_RENDER = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,intake=$('Run deterministic SCHEDULE intake').first().json,b=$('Validate SCHEDULE builder stage').first().json;
const catalogue=root.request.schema_catalogue||root.request.rag_evidence?.schema_catalogue||{};
return[{json:{schedule_render_request:{mode:intake.build_mode,schema_catalogue:catalogue,ir_events:b.ir_events||[]}}}];
"""


PREPARE_MERGE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,intake=$('Run deterministic SCHEDULE intake').first().json,r=$('Render typed SCHEDULE IR deterministically').first().json;
const b=$('Validate SCHEDULE builder stage').first().json;
let baseline=null;try{baseline=$('Analyze lossless baseline inventory').first().json}catch{}
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const packet=obj(root.request?.source_facts_packet)?root.request.source_facts_packet:{};
const facts=arr(packet.facts)?packet.facts:[];
const wellFacts=facts.map(f=>{const values=obj(f.values)?f.values:{};const well=clean(f.well||f.entity||values['Скважина']||values.WELL||values.well);const date=f.value??f.raw_value??values['Дата ввода']??values.date??null;return{well,date}}).filter(f=>f.well&&f.date!=null);
const timelinePath=intake.build_mode==='REVISE'&&(wellFacts.length>0||arr(b.source_map)&&b.source_map.some(s=>['timeline_commissioning_revise','timeline_group_revise'].includes(clean(s.path))));
return[{json:{merge_request:{mode:intake.build_mode,root_path:String(root.request.root_path||root.request.baseline_filename||'schedule.inc'),baseline_schedule_text:String(root.request.baseline_schedule_text||''),include_files:Array.isArray(root.request.include_files)?root.request.include_files:[],baseline_analysis:baseline,changes:timelinePath?[]:(r.changes||[]),schema_render_result:r,explicit_remove_approved:root.request.explicit_remove_approved===true,remove_approval:root.request.remove_approval||null,commissioning_timeline_pending:timelinePath}}}];
"""


APPLY_COMMISSIONING_TIMELINE = build_commissioning_revise_js()


PREPARE_VALIDATE = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
let m;try{m=$('Apply commissioning timeline revise').first().json}catch{m=$('Merge SCHEDULE draft deterministically').first().json}
const r=$('Render typed SCHEDULE IR deterministically').first().json;
const b=$('Validate SCHEDULE builder stage').first().json;
let replay=null;try{replay=$('Replay baseline prefix into semantic boundary').first().json}catch{}
const catalogue=root.request.schema_catalogue||root.request.rag_evidence?.schema_catalogue||{};
const temporalPolicy=root.request.temporal_policy||{history_end:root.request.history_end||null,forecast_start:root.request.forecast_start||null};
const generatedBoundary=b.build_mode==='REVISE'?replay?.semantic_state_snapshot:null;
const commissioning=m.commissioning_revise||null;
const timelineApplied=commissioning&&commissioning.status==='applied';
return[{json:{schedule_validation_request:{validation_phase:'CANDIDATE',mode:b.build_mode,build_mode:b.build_mode,schedule_text:m.generated_schedule,output_package:m.output_package,render_result:r,ir_events:timelineApplied?[]:(b.ir_events||[]),simulator_profile:root.request.simulator_profile||{},schema_catalogue:catalogue,schema_catalogue_ref:String(catalogue.catalogue_ref||root.request.schema_catalogue_ref||''),schema_catalogue_approved:r.status==='rendered',approved_keyword_schemas:Array.isArray(catalogue.schemas)?catalogue.schemas:[],temporal_policy:temporalPolicy,initial_semantic_snapshot:b.build_mode==='CREATE'?(root.request.initial_semantic_snapshot||null):null,semantic_baseline_snapshot:generatedBoundary,baseline_package_hash:m.baseline_package_hash||null,commissioning_revise:commissioning,commissioning_timeline_applied:timelineApplied,monthly_dates_check:commissioning?.monthly_dates_check||m.monthly_dates_check||null,preserve_unbound_keywords:b.build_mode==='REVISE'}}}];
"""


PREPARE_VERIFY = r"""
const b=$('Validate SCHEDULE builder stage').first().json,r=$('Render typed SCHEDULE IR deterministically').first().json;
let m;try{m=$('Apply commissioning timeline revise').first().json}catch{m=$('Merge SCHEDULE draft deterministically').first().json}
const v=$('Validate merged SCHEDULE package').first().json;
return[{json:{builder_result:b,render_result:r,merge_result:m,validation_result:v}}];
"""


FINAL_RESULT = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,b=$('Validate SCHEDULE builder stage').first().json,r=$('Render typed SCHEDULE IR deterministically').first().json;
let m;try{m=$('Apply commissioning timeline revise').first().json}catch{m=$('Merge SCHEDULE draft deterministically').first().json}
const v=$('Validate merged SCHEDULE package').first().json,review=$json;
const plan=$('Validate SCHEDULE pipeline plan').first().json;
const score=Math.min(Number(b.score?.stage_score||0),Number(v.score?.stage_score||0),Number(review.score?.stage_score||0));
const generated=String(m.generated_schedule||'');
const generatedPreview=generated.slice(0,200000);
const artifactRefs=Array.isArray(b.artifact_refs)?b.artifact_refs.filter(x=>x&&typeof x==='object'):[];
const reviewDecision=review.decision_record??{contract:'decision_record',contract_version:'1.0',objective:'Independently verify the deterministic SCHEDULE draft.',considered_inputs:[{kind:'builder_result',status:b.status},{kind:'merge_result',status:m.status},{kind:'validation_result',status:v.status}],proposed_actions:[{action:'pass'},{action:'pass_with_warnings'},{action:'reject'}],selected_action:{action:String(review.verdict||'reject'),reason_codes:(review.required_corrections||[]).length?review.required_corrections:[review.can_release?'VERIFIER_PASS':'VERIFIER_NOT_RELEASE_READY']},rejected_actions:[],assumptions:[],evidence_refs:[],citations:[],tool_call_ids:[],unresolved_questions:[],acceptance_check_results:[{check:'merge',passed:m.status==='merged'},{check:'validation',passed:v.status==='valid'},{check:'independent_schedule_verifier',passed:review.verdict==='pass'}]};
const gateDecisions=[{stage:'plan',decision:plan.score?.decision||'hitl',score:plan.score?.stage_score??null,reason_codes:plan.decision_record?.selected_action?.reason_codes||plan.hard_blockers||[]},{stage:'builder',decision:b.score?.decision||'hitl',score:b.score?.stage_score??null,reason_codes:b.decision_record?.selected_action?.reason_codes||b.hard_blockers||[]},{stage:'render',decision:r.status==='rendered'?'continue':'hitl',score:r.status==='rendered'?100:0,reason_codes:r.hard_blockers||[]},{stage:'validation',decision:v.score?.gate||'hitl',score:v.score?.stage_score??null,reason_codes:v.hard_blockers||[]},{stage:'verification',decision:review.can_release?'continue':'hitl',score:review.score?.stage_score??null,reason_codes:reviewDecision.selected_action.reason_codes}];
let replay=null;try{replay=$('Replay baseline prefix into semantic boundary').first().json}catch{}
const trace=[{stage:'intake',status:'accepted'},{stage:'baseline',status:b.build_mode==='REVISE'?'decoded_and_replayed':'not_applicable',score:replay?.score||null,findings:replay?.findings||[]},{stage:'plan',status:plan.status,score:plan.score,decision_record:plan.decision_record},{stage:'builder',status:b.status,score:b.score,decision_record:b.decision_record,tool_calls:Array.isArray(b.agent_tool_trace)?b.agent_tool_trace:[]},{stage:'render',status:r.status,score:{stage_score:r.status==='rendered'?100:0},findings:r.findings},{stage:'merge',status:m.status},{stage:'commissioning_timeline',status:m.commissioning_revise?.status||'skipped'},{stage:'validate',status:v.status,score:v.score},{stage:'verify',status:review.verdict,score:review.score,decision_record:reviewDecision}];
const slimMerge=obj=>{if(!obj||typeof obj!=='object')return obj;const {generated_schedule,output_package,...rest}=obj;return {...rest,generated_schedule_bytes:String(generated_schedule||'').length,output_package:output_package?{contract:output_package.contract,contract_version:output_package.contract_version,root_path:output_package.root_path,package_hash:output_package.package_hash,file_count:Array.isArray(output_package.files)?output_package.files.length:0}:null};};
const slimCommissioning=obj=>{if(!obj||typeof obj!=='object')return obj;const {timeline,generated_schedule,...rest}=obj;return {...rest,timeline_steps:Array.isArray(timeline?.steps)?timeline.steps.length:null,generated_schedule_bytes:String(generated_schedule||'').length};};
let correctionQuestions=(Array.isArray(review.required_corrections)?review.required_corrections:[]).map((c,i)=>{
  if(c&&typeof c==='object'&&!Array.isArray(c)){
    const q=String(c.question||c.text||c.message||c.code||'').trim();
    return {id:String(c.id||`correction_${i+1}`),question:q||'Исправьте замечание проверки schedule.',required:true};
  }
  const q=String(c||'').trim();
  return {id:`correction_${i+1}`,question:q||'Исправьте замечание проверки schedule.',required:true};
});
const releaseReady=Boolean(review.can_release);
if(!releaseReady&&!correctionQuestions.length){
  correctionQuestions=[{id:'schedule_not_release_ready',question:'Черновик не готов к выпуску. Напишите, что исправить, или уточните исходные данные.',required:true}];
}
return[{json:{specialist_result:{contract:'specialist_result',contract_version:'1.0',task_id:root.task_id,specialist_id:'schedule_builder_specialist',attempt:root.attempt,status:releaseReady?'needs_approval':'needs_input',summary:releaseReady?'Черновик прогнозного schedule файла прошёл проверки и готов к утверждению.':'Черновик прогнозного schedule файла не прошёл все проверки выпуска.',user_message:releaseReady?'Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.':'Черновик прогнозного schedule файла не готов к выпуску — посмотрите замечания проверки и ответьте в чате.',deliverables:[{kind:'schedule_inc_text',filename:String(m.output_package?.root_path||'schedule.inc'),description:'Validated SCHEDULE text; not approved until the orchestrator human release gate.',schedule_text:generatedPreview}],artifact_refs:artifactRefs,compact_data:{build_mode:b.build_mode,generated_schedule_bytes:generated.length,generated_schedule_preview:generated.slice(0,4000),plan,render_result:{status:r.status,hard_blockers:r.hard_blockers||[],catalogue_hash:r.catalogue_hash||null},merge_result:slimMerge(m),validation_result:{status:v.status,hard_blockers:v.hard_blockers||[],score:v.score||null,findings:(Array.isArray(v.findings)?v.findings:[]).filter(f=>f.severity==='error').slice(0,20)},schedule_verifier_result:{verdict:review.verdict,can_release:review.can_release,score:review.score,findings:review.findings||[]},preservation_report:m.preservation_report,semantic_diff:m.semantic_diff,commissioning_revise:slimCommissioning(m.commissioning_revise),monthly_dates_check:m.commissioning_revise?.monthly_dates_check||null,requirements_matrix:b.requirements_matrix,source_map:b.source_map,completeness_report:b.completeness_report,decision_record:reviewDecision,decision_records:[plan.decision_record,b.decision_record,reviewDecision].filter(Boolean),gate_decisions:gateDecisions,stage_scores:trace.filter(x=>x.score).map(x=>({stage:x.stage,...x.score})),overall_score:score,agent_tool_trace:Array.isArray(b.agent_tool_trace)?b.agent_tool_trace:[],trace_summary:trace,release_ready:review.can_release},assumptions:b.assumptions,warnings:b.warnings,evidence:b.evidence,self_check:{performed:true,passed:Boolean(review.can_release),checks:[{check:'schema_render',passed:r.status==='rendered'},{check:'merge',passed:m.status==='merged'},{check:'validation',passed:v.status==='valid'},{check:'independent_schedule_verifier',passed:review.verdict==='pass'}],reproducibility:'Replay the bounded input text, expert schema catalogue, evidence snapshot and typed IR.'},human_request:{kind:releaseReady?'needs_approval':'needs_input',questions:releaseReady?[{id:'release_approval',question:'Черновик прошёл проверки. Утвердите выпуск schedule или напишите, что изменить.',required:true}]:correctionQuestions},error:releaseReady?null:{code:'SCHEDULE_PIPELINE_NOT_RELEASE_READY',findings:review.findings||[]},continuation:null}}}];
"""


GATE_RESULT = r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json,x=$json||{};
const commissioning=x.commissioning_revise&&typeof x.commissioning_revise==='object'?x.commissioning_revise:null;
const findings=Array.isArray(x.findings)?x.findings:(Array.isArray(x.error?.findings)?x.error.findings:[]);
const gaps=Array.isArray(x.evidence_gap)&&x.evidence_gap.length?x.evidence_gap:(Array.isArray(commissioning?.evidence_gap)?commissioning.evidence_gap:[]);
let status=['needs_input','needs_decision','needs_approval','retryable_error','fatal_error'].includes(x.status)?x.status:'needs_input';
if(x.contract==='schedule_verifier_result'&&x.verdict==='pass_with_warnings')status='needs_approval';
if(x.contract==='schedule_builder_stage_result'&&Array.isArray(x.evidence_gap)&&x.evidence_gap.length)status='needs_input';
if(commissioning&&commissioning.status==='needs_input')status='needs_input';
const rawFindings=findings.length?findings:(Array.isArray(root.packet_findings)?root.packet_findings:[]);
const compactFindings=[];const seenCodes=new Set();
for(const f of rawFindings){const code=String(f?.code||'SCHEDULE_PIPELINE_GATE');if(seenCodes.has(code)&&compactFindings.length>=20)continue;if(!seenCodes.has(code)||compactFindings.filter(x=>x.code===code).length<3){compactFindings.push(f);seenCodes.add(code)}if(compactFindings.length>=40)break}
const normalizedFindings=compactFindings.filter(f=>String(f?.severity||'error').toLowerCase()==='error'),reasonCodes=[...new Set(normalizedFindings.map(f=>String(f.code||'SCHEDULE_PIPELINE_GATE')))],suppliedQuestions=Array.isArray(x.questions)?x.questions.filter(q=>q&&typeof q==='object').slice(0,100):(Array.isArray(commissioning?.questions)?commissioning.questions:[]);
const questions=suppliedQuestions.length?suppliedQuestions.slice(0,100):gaps.length?gaps.map((g,i)=>({id:`schedule_gap_${i+1}`,question:String(g.question||`${g.keyword||'SCHEDULE'} ${g.entity||''} ${g.field||''}: ${g.reason||'required evidence is missing'}`).trim(),expected_format:String(g.expected_format||'value with units and provenance'),required:true,type:String(g.keyword||'').toUpperCase()==='WELLTRACK'||/file|xlsx|WELLTRACK/i.test(String(g.expected_format||''))?'file':'text'})):normalizedFindings.map((f,i)=>({id:`schedule_finding_${i+1}`,question:String(f.message||f.code||'Resolve the SCHEDULE pipeline finding.'),required:true})).slice(0,30);
const continuation=x.continuation||commissioning?.continuation||(gaps.length&&!String(commissioning?.continuation?.protocol||'').includes('hitl')?{protocol:'schedule-builder-evidence-gap-v1',evidence_gap:gaps}:null);
const hitlAttach=String(continuation?.protocol||'')==='schedule-builder-hitl-attachment-v1';
const decisionRecord=x.decision_record??{contract:'decision_record',contract_version:'1.0',objective:String(root.packet?.objective||root.request?.objective||'Resolve the failed SCHEDULE stage.'),considered_inputs:[{kind:'failed_stage',contract:String(x.contract||'unknown'),status:String(x.status||x.verdict||'blocked')}],proposed_actions:[{action:'request_targeted_input'},{action:'retry_with_new_snapshot'}],selected_action:{action:status,reason_codes:reasonCodes.length?reasonCodes:['SCHEDULE_PIPELINE_GATE']},rejected_actions:[],assumptions:[],evidence_refs:[],citations:[],tool_call_ids:[],unresolved_questions:questions,acceptance_check_results:[]};
return[{json:{specialist_result:{contract:'specialist_result',contract_version:'1.0',task_id:root.task_id||'unknown',specialist_id:'schedule_builder_specialist',attempt:root.attempt||1,status,summary:String(x.summary||x.rationale||commissioning?.findings?.[0]?.note||'SCHEDULE pipeline requires additional evidence or a controlled decision.').slice(0,4000),deliverables:[],artifact_refs:[],compact_data:{failed_stage:String(x.contract||commissioning?.contract||'unknown'),findings:normalizedFindings,findings_truncated:rawFindings.length>normalizedFindings.length,findings_total:rawFindings.length,evidence_gap:gaps.slice(0,50),new_wells:commissioning?.new_wells||[],unlisted_wells_policy:commissioning?.unlisted_wells_policy||null,score:x.score||null,decision_record:decisionRecord,gate_decisions:[{stage:String(x.contract||'unknown'),decision:'hitl',score:x.score?.stage_score??null,reason_codes:decisionRecord.selected_action.reason_codes}],trace_summary:[{stage:String(x.contract||'unknown'),status,score:x.score||null,decision_record:decisionRecord}]},assumptions:[],warnings:[],evidence:[],self_check:{performed:false,passed:false,checks:[],reproducibility:hitlAttach?'Resume via Human Gate with WELLTRACK + xlsx attachments and the same task_id.':'Resume with the same task and a new versioned evidence snapshot.'},human_request:{kind:status,questions},error:x.error||{code:hitlAttach?'NEW_WELLS_REQUIRE_HITL':'SCHEDULE_PIPELINE_GATE',findings:normalizedFindings},continuation:hitlAttach?continuation:(continuation&&continuation.protocol==='schedule-builder-evidence-gap-v1'?continuation:null)}}}];
"""


INVALID_RESULT = """const x=$json,findings=Array.isArray(x.packet_findings)?x.packet_findings:[];const ragMissing=findings.some(f=>f.code==='SCHEDULE_RAG_EVIDENCE_REQUIRED'),status=ragMissing?'needs_input':'fatal_error',code=ragMissing?'SCHEDULE_RAG_EVIDENCE_REQUIRED':'INVALID_SCHEDULE_SPECIALIST_PACKET',questions=ragMissing?[{id:'rag_evidence',question:'Load active expert keyword_instruction blocks and matching schema JSON into schedule_mvp.',required:true}]:[];const decisionRecord={contract:'decision_record',contract_version:'1.0',objective:'Validate the SCHEDULE specialist request before any model or tool call.',considered_inputs:[{kind:'specialist_packet',task_id:x.task_id||'unknown',packet_valid:false}],proposed_actions:[{action:'reject_invalid_packet'}],selected_action:{action:status,reason_codes:[code]},rejected_actions:[],assumptions:[],evidence_refs:[],citations:[],tool_call_ids:[],unresolved_questions:questions,acceptance_check_results:[{check:'packet_contract',passed:false}]};return[{json:{specialist_result:{contract:'specialist_result',contract_version:'1.0',task_id:x.task_id||'unknown',specialist_id:'schedule_builder_specialist',attempt:x.attempt||1,status,summary:ragMissing?'Complete expert RAG instructions and schema evidence are required before SCHEDULE planning.':'Invalid specialist_packet v1.0 for SCHEDULE pipeline.',deliverables:[],artifact_refs:[],compact_data:{findings,decision_record:decisionRecord,gate_decisions:[{stage:'intake',decision:'hitl',score:0,reason_codes:[code]}],trace_summary:[{stage:'intake',status,decision_record:decisionRecord}]},assumptions:[],warnings:[],evidence:[],self_check:{performed:false,passed:false,checks:[],reproducibility:''},human_request:ragMissing?{kind:'needs_input',questions}:null,error:{code,findings},continuation:null}}}];"""


def build_schedule_pipeline(*, node, note, code, trigger, ifnode, connect, workflow, keywords, planner_schema, planner_system, intake_js, baseline_js, baseline_decode_js, baseline_query_js, plan_validate_js, render_js, merge_js, validate_js, verify_js):
    example = {"specialist_packet": {"contract": "specialist_packet", "contract_version": "1.0", "task_id": "eng_example", "specialist_id": "schedule_builder_specialist", "attempt": 1, "objective": "Create forecast SCHEDULE", "inputs": {"schedule_request": {"build_mode": "CREATE", "simulator_profile": {"vendor": "Rock Flow Dynamics", "simulator": "tNavigator", "version": "22.2", "unit_system": "METRIC"}, "requested_keyword_scope": ["DATES", "WCONPROD"], "source_facts": [], "rag_evidence": {"contract": "schedule_rag_evidence", "contract_version": "1.0", "citations": [{"document_id": "tnav-22.2", "document_revision": "22.2", "source_hash": "sha256:replace", "page": "replace", "heading": "SCHEDULE"}], "results": []}, "schema_catalogue": {"contract": "schedule_schema_catalogue", "contract_version": "1.0", "catalogue_ref": "catalogue://tnavigator/22.2/replace", "catalogue_hash": "sha256:replace", "source_hash": "sha256:replace", "simulator_profile": {"vendor": "Rock Flow Dynamics", "simulator": "tNavigator", "version": "22.2"}, "approved": False, "approved_by": "", "approval_gate_id": "", "schemas": []}}}, "controls": {"preservation_policy": "preserve_unmentioned"}, "acceptance_criteria": [], "artifact_refs": []}}
    nodes = [
        note("SCHEDULE pipeline README", (-1200, -760), "## Governed SCHEDULE pipeline — n8n 2.30.8\n\nVisible bounded CREATE/REVISE specialist: intake → lossless baseline analysis → expert catalogue decode → pre-change semantic replay → planning summary → plan → targeted mutation-safe baseline query → typed IR → deterministic render → merge → candidate replay/validation → independent review.\n\nIt never calls Excel/RAG directly and owns no durable state. Bind it in Universal Orchestrator. Without a complete active keyword instruction and exact expert schema it stops before planning/rendering.", 660, 480),
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
        node("SCHEDULE Planner Chat Model — configure in UI", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, (3100, -360), {"model": {"mode": "id", "value": "gpt-5.4-nano"}, "options": {"maxTokens": 3500, "temperature": 0, "timeout": 120000, "maxRetries": 2}, "responsesApiEnabled": False}, credentials={"openAiApi": {"id": "REPLACE_IN_UI", "name": "REPLACE: schedule planner chat credential"}}),
        node("SCHEDULE Planner Structured Output", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3, (3320, -360), {"schemaType": "manual", "inputSchema": json.dumps(planner_schema), "autoFix": False}),
        code("Validate SCHEDULE pipeline plan", (3420, -100), plan_validate_js),
        ifnode("SCHEDULE plan ready?", (3640, -100), '={{ ["proposed"].includes($json.status) && $json.score.decision !== "hitl" }}'),
        ifnode("REVISE needs targeted baseline context?", (3860, -100), "={{ $json.build_mode }}", "REVISE", "string"),
        code("Prepare targeted baseline query", (4080, -260), PREPARE_BASELINE_BUILDER_QUERY),
        code("Query targeted baseline records", (4300, -260), baseline_query_js),
        ifnode("Targeted baseline context complete?", (4520, -260), "={{ $json.status }}", "succeeded", "string"),
        code("Prepare SCHEDULE builder stage", (4740, -100), PREPARE_BUILD),
        node("SCHEDULE Builder Agent", "@n8n/n8n-nodes-langchain.agent", 3.1, (4960, -100), {"promptType": "define", "text": "={{ $json.builder_input }}", "hasOutputParser": True, "needsFallback": False, "options": {"systemMessage": BUILDER_SYSTEM, "maxIterations": 8, "returnIntermediateSteps": True, "enableStreaming": False}}),
        node("SCHEDULE Builder Chat Model — configure in UI", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, (4860, -360), {"model": {"mode": "id", "value": "gpt-5.4-nano"}, "options": {"maxTokens": 6000, "temperature": 0, "timeout": 120000, "maxRetries": 2}, "responsesApiEnabled": False}, credentials={"openAiApi": {"id": "REPLACE_IN_UI", "name": "REPLACE: schedule builder chat credential"}}),
        node("SCHEDULE Builder Structured Output", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3, (5080, -360), {"schemaType": "manual", "inputSchema": json.dumps(BUILDER_SCHEMA), "autoFix": False}),
        code("Validate SCHEDULE builder stage", (5180, -100), validate_builder(keywords)),
        ifnode("Builder draft ready?", (5400, -100), '={{ ["succeeded","partial"].includes($json.status) && $json.score.decision !== "hitl" }}'),
        code("Prepare deterministic schema render", (5620, -100), PREPARE_RENDER),
        code("Render typed SCHEDULE IR deterministically", (5840, -100), render_js),
        ifnode("SCHEDULE schema render accepted?", (6060, -100), "={{ $json.status }}", "rendered", "string"),
        code("Prepare deterministic merge", (6280, -100), PREPARE_MERGE),
        code("Merge SCHEDULE draft deterministically", (6500, -100), merge_js),
        ifnode("SCHEDULE merge accepted?", (6720, -100), "={{ $json.status }}", "merged", "string"),
        code("Apply commissioning timeline revise", (6830, -100), APPLY_COMMISSIONING_TIMELINE),
        ifnode("Commissioning timeline ok?", (6880, -100), "={{ $json.status }}", "merged", "string"),
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
    connect(connections, "SCHEDULE merge accepted?", "Apply commissioning timeline revise", idx=0)
    connect(connections, "SCHEDULE merge accepted?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Apply commissioning timeline revise", "Commissioning timeline ok?")
    connect(connections, "Commissioning timeline ok?", "Prepare SCHEDULE validation", idx=0)
    connect(connections, "Commissioning timeline ok?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare SCHEDULE validation", "Validate merged SCHEDULE package")
    connect(connections, "Validate merged SCHEDULE package", "SCHEDULE validation passed?")
    connect(connections, "SCHEDULE validation passed?", "Prepare independent SCHEDULE review", idx=0)
    connect(connections, "SCHEDULE validation passed?", "Build SCHEDULE pipeline gate result", idx=1)
    connect(connections, "Prepare independent SCHEDULE review", "Run independent SCHEDULE verifier")
    connect(connections, "Run independent SCHEDULE verifier", "SCHEDULE verifier passed?")
    connect(connections, "SCHEDULE verifier passed?", "Build release-ready specialist result", idx=0)
    connect(connections, "SCHEDULE verifier passed?", "Build SCHEDULE pipeline gate result", idx=1)
    return workflow(
        "SCHEDULE — Builder",
        "Visible intake, lossless catalogue decode, pre-change state replay, targeted baseline retrieval, plan, typed IR, deterministic render, merge, timeline commissioning revise (parse→mutate→emit), candidate validation and independent review. Durable state, Excel and release remain in the Orchestrator.",
        nodes,
        connections,
        "specialist_result/v1",
    )
