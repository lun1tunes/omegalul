#!/usr/bin/env python3
"""Generate Error — MAS Case Handler workflow (critical failure path)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "workflows/retired/mas-error-handler.workflow.json"

WF_ID = "e1f0a7c2-9b4d-5e8f-a123-4567890abcde"
WF_NAME = "Error — MAS Case Handler"
# Do not attach n8n Error workflow to the handler or its callees — that loops
# when CAS/Trace fail while handling an uncaught error.
ERROR_WORKFLOW_SKIP = frozenset(
    {
        WF_NAME,
        "Writer — MAS Trace",
        "CAS — Persist Task State",
    }
)


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-error:{name}"))


def node(name, ntype, ver, pos, params, **extra):
    out = {
        "parameters": params,
        "id": nid(name),
        "name": name,
        "type": ntype,
        "typeVersion": ver,
        "position": list(pos),
    }
    out.update(extra)
    return out


def note(name, pos, content, w=520, h=280, color=5):
    return node(name, "n8n-nodes-base.stickyNote", 1, pos, {"content": content, "width": w, "height": h, "color": color})


def code(name, pos, js):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})


def set_fields(name, pos, fields):
    assignments = [
        {
            "id": nid(f"{name}/field/{i}"),
            "name": field,
            "value": value,
            "type": type_,
        }
        for i, (field, value, type_) in enumerate(fields, 1)
    ]
    return node(
        name,
        "n8n-nodes-base.set",
        3.4,
        pos,
        {"assignments": {"assignments": assignments}, "options": {}, "includeOtherFields": True},
    )


def ifnode(name, pos, left, right=True, op="boolean"):
    return node(
        name,
        "n8n-nodes-base.if",
        2.2,
        pos,
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [
                    {
                        "id": nid(f"{name}-cond"),
                        "leftValue": left,
                        "rightValue": right,
                        "operator": {"type": op, "operation": "equals" if op == "boolean" else "equals"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
    )


def connect(c, src, dst, out="main", si=0, tin="main", ti=0):
    groups = c.setdefault(src, {})
    outputs = groups.setdefault(out, [])
    while len(outputs) <= si:
        outputs.append([])
    outputs[si].append({"node": dst, "type": tin, "index": ti})


CLASSIFY = r"""
const root=$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const clean=v=>typeof v==='string'?v.trim():'';
const arr=Array.isArray;
const event=obj(root.mas_error_event)?root.mas_error_event:(obj(root)&&root.contract==='mas_error_event'?root:{});
const taskId=clean(event.task_id||root.task_id);
const scenarioRaw=clean(event.scenario||event.error_scenario||'').toLowerCase();
const codeIn=clean(event.code||event.error_code||'').toUpperCase();
const stage=clean(event.stage||'error')||'error';
const findings=arr(event.findings)?event.findings.filter(obj).slice(0,40):[];
const executionId=clean(String(event.execution_id||event.executionId||''))||null;
const snap=obj(event.cas_snapshot)?event.cas_snapshot:(obj(root.cas_snapshot)?root.cas_snapshot:null);
const passthrough=obj(root.passthrough)?root.passthrough:{};

const TAXONOMY={
  llm_error:{code:'LLM_CALL_FAILED',cas_status:'retryable_error',restartable:true,label:'Ошибка LLM'},
  invalid_json:{code:'INVALID_STRUCTURED_OUTPUT',cas_status:'retryable_error',restartable:true,label:'Невалидный JSON / structured output'},
  calc_timeout:{code:'CALC_SERVICE_TIMEOUT',cas_status:'retryable_error',restartable:true,label:'Timeout расчётного сервиса'},
  missing_data:{code:'MISSING_MANDATORY_DATA',cas_status:'awaiting_human',restartable:false,label:'Нет обязательных данных'},
  validator_reject:{code:'VALIDATOR_REJECTED',cas_status:'awaiting_human',restartable:false,label:'Отказ валидатора'},
  rag_error:{code:'RAG_UNAVAILABLE',cas_status:'awaiting_human',restartable:false,label:'Ошибка RAG'},
  document_access:{code:'DOCUMENT_ACCESS_DENIED',cas_status:'awaiting_human',restartable:false,label:'Нет доступа к документу'},
  approval_error:{code:'APPROVAL_GATE_FAILED',cas_status:'awaiting_human',restartable:false,label:'Ошибка согласования'},
  uncaught:{code:'UNCAUGHT_NODE_FAILURE',cas_status:'retryable_error',restartable:true,label:'Неперехваченный сбой n8n'},
};

const CODE_TO_SCENARIO={
  LLM_CALL_FAILED:'llm_error',
  INVALID_STRUCTURED_OUTPUT:'invalid_json',
  INVALID_SPECIALIST_CONTRACT:'invalid_json',
  DECISION_RECORD_REQUIRED:'invalid_json',
  CALC_SERVICE_TIMEOUT:'calc_timeout',
  MISSING_MANDATORY_DATA:'missing_data',
  VALIDATOR_REJECTED:'validator_reject',
  RAG_UNAVAILABLE:'rag_error',
  SCHEDULE_RAG_UNAVAILABLE:'rag_error',
  ORCHESTRATOR_ROUTING_RAG_REQUIRED:'rag_error',
  DOCUMENT_ACCESS_DENIED:'document_access',
  APPROVAL_GATE_FAILED:'approval_error',
  CAS_CONFLICT:'approval_error',
  UNCAUGHT_NODE_FAILURE:'uncaught',
};

let scenario=scenarioRaw;
if(!TAXONOMY[scenario]&&codeIn&&CODE_TO_SCENARIO[codeIn]) scenario=CODE_TO_SCENARIO[codeIn];
if(!TAXONOMY[scenario]&&clean(snap&&snap.status)==='conflict') scenario='approval_error';
if(!TAXONOMY[scenario]){
  const blob=JSON.stringify({code:codeIn,findings,msg:event.safe_message||event.message||'',snap_status:snap&&snap.status,snap_message:snap&&snap.message}).toLowerCase();
  if(/llm|model|openai|chat/.test(blob)) scenario='llm_error';
  else if(/json|structured|schema|parse/.test(blob)) scenario='invalid_json';
  else if(/timeout|timed out|etimedout|calc|math_service/.test(blob)) scenario='calc_timeout';
  else if(/missing|required data|обязательн/.test(blob)) scenario='missing_data';
  else if(/validat|reject|self_check/.test(blob)) scenario='validator_reject';
  else if(/rag|retrieval|abstain/.test(blob)) scenario='rag_error';
  else if(/access|denied|forbidden|acl|unauthorized/.test(blob)) scenario='document_access';
  else if(/cas[_-]?conflict|stale expected_version|gate_id does not match|optimistic concurrency|concurrent or non-unique|current expected_version/.test(blob)) scenario='approval_error';
  else scenario='llm_error';
}

const tax=TAXONOMY[scenario];
const code=codeIn&&Object.values(TAXONOMY).some(t=>t.code===codeIn)?codeIn:tax.code;
let restartable=event.restartable===true||event.restartable===false?Boolean(event.restartable):tax.restartable;
let casStatus=clean(event.cas_status)||tax.cas_status;
const snapStatus=clean(snap&&snap.status);
// Never override a terminal CAS row into restartable UI state.
if(['failed','rejected','cancelled','completed'].includes(snapStatus)){
  casStatus=snapStatus;
  restartable=false;
} else if(['failed','rejected','cancelled','completed'].includes(casStatus)){
  restartable=false;
} else if(casStatus==='awaiting_human'){
  // keep taxonomy restartable flag (usually false)
} else if(casStatus==='retryable_error' && event.restartable===false){
  restartable=false;
}

// Fail closed: never notify UI without case_id.
if(!taskId){
  return[{json:{
    contract:'mas_error_ack',
    contract_version:'1.0',
    accepted:false,
    code:'CASE_ID_REQUIRED',
    message:'Error workflow refused anonymous failure: task_id/case_id is required.',
    taxonomy_scenario:scenario,
    error_code:code,
    restartable:false,
    cas_persisted:false,
    activity_notified:false,
    passthrough,
  }}];
}

let safeMessage=clean(event.safe_message||event.message||'');
if(!safeMessage) safeMessage=`${tax.label}. Case ${taskId}.`;
if(!safeMessage.includes(taskId)) safeMessage=`${safeMessage} (case_id=${taskId})`;
// Strip secretish fragments from message surface.
safeMessage=safeMessage.replace(/(sk-[A-Za-z0-9]+|Bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+)/gi,'[redacted]').slice(0,800);

const lastError={
  code,
  scenario,
  stage,
  safe_message:safeMessage,
  findings:findings.slice(0,20),
  execution_id:executionId,
  restartable,
  at:new Date().toISOString(),
};

return[{json:{
  contract:'mas_error_event',
  contract_version:'1.0',
  task_id:taskId,
  taxonomy_scenario:scenario,
  error_code:code,
  stage,
  cas_status:casStatus,
  restartable,
  safe_message:safeMessage,
  last_error:lastError,
  findings,
  execution_id:executionId,
  cas_snapshot:snap,
  cas_already_persisted:event.cas_already_persisted===true||root.cas_already_persisted===true,
  skip_cas:event.skip_cas===true||root.skip_cas===true,
  passthrough,
  has_task_id:true,
}}];
""".strip()


BUILD_CAS = r"""
const x=$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const clean=v=>typeof v==='string'?v.trim():'';
const parse=(v,f)=>{try{const p=typeof v==='string'?JSON.parse(v):v;return obj(p)?p:f}catch{return f}};
if(!x.has_task_id) return[{json:x}];
if(x.skip_cas||x.cas_already_persisted){
  return[{json:{...x,cas_operation:null,should_persist_cas:false,attempted:null}}];
}
const snap=obj(x.cas_snapshot)?x.cas_snapshot:{};
const now=new Date().toISOString();
const storedVersion=Number.isInteger(Number(snap.version))?Number(snap.version):1;
const previousVersion=Number.isInteger(Number(snap.previous_version))?Number(snap.previous_version):(Number.isInteger(Number(snap.version))?Number(snap.version):0);
const nextVersion=x.cas_already_persisted?storedVersion:storedVersion+1;
const runtime=parse(snap.runtime_json,{});
const preservedRequest=typeof snap.request_json==='string'?snap.request_json:JSON.stringify(obj(snap.request)?snap.request:{});
const preservedPlan=typeof snap.plan_json==='string'?snap.plan_json:(snap.plan_json!=null?JSON.stringify(snap.plan_json):'{}');
const preservedPacket=typeof snap.packet_json==='string'?snap.packet_json:(snap.packet_json!=null?JSON.stringify(snap.packet_json):'{}');
const preservedResult=typeof snap.result_json==='string'?snap.result_json:(snap.result_json!=null?JSON.stringify(snap.result_json):'{}');
const preservedVerification=typeof snap.verification_json==='string'?snap.verification_json:(snap.verification_json!=null?JSON.stringify(snap.verification_json):'{}');

const gateNeeded=x.cas_status==='awaiting_human'||(x.cas_status==='retryable_error'&&x.restartable);
const gate=gateNeeded?{
  gate_id:`gate_${x.task_id}_${nextVersion}_error_${String(x.error_code||'ERR').toLowerCase()}`,
  kind:x.cas_status==='retryable_error'?'needs_decision':'needs_input',
  reason:x.safe_message,
  questions:x.restartable?[{id:'error_restart',text:`Перезапустить case ${x.task_id} с сохранёнными входными данными?`,required:true,type:'enum',enum:['restart','cancel']}]:[{id:'error_input',text:x.safe_message,required:true,type:'text'}],
  expected_version:nextVersion,
  restartable:Boolean(x.restartable),
  error_code:x.error_code,
  taxonomy_scenario:x.taxonomy_scenario,
}:{};

const attempted={
  task_id:x.task_id,
  version:nextVersion,
  previous_version:previousVersion||storedVersion,
  status:x.cas_status,
  risk_class:clean(snap.risk_class)||'high',
  request_json:preservedRequest||'{}',
  runtime_json:JSON.stringify({...runtime,last_error:x.last_error,error_preserved_inputs:true}),
  plan_json:preservedPlan||'{}',
  packet_json:preservedPacket||'{}',
  result_json:preservedResult||'{}',
  verification_json:preservedVerification||'{}',
  gate_json:JSON.stringify(gate),
  retry_count:Number.isInteger(Number(snap.retry_count))?Number(snap.retry_count):0,
  max_retries:Number.isInteger(Number(snap.max_retries))?Number(snap.max_retries):2,
  created_at:clean(snap.created_at)||now,
  updated_at:now,
};

return[{json:{
  ...x,
  should_persist_cas:true,
  cas_operation:'update',
  attempted,
  human_gate:Object.keys(gate).length?gate:null,
}}];
""".strip()


PREPARE_TRACE = r"""
const built=$('Build CAS error patch').first().json||{};
const incoming=$json||{};
const x={...built,...incoming,has_task_id:built.has_task_id!==false,task_id:built.task_id||incoming.task_id,safe_message:built.safe_message||incoming.safe_message,error_code:built.error_code||incoming.error_code,taxonomy_scenario:built.taxonomy_scenario||incoming.taxonomy_scenario,cas_status:built.cas_status||incoming.status||incoming.cas_status,restartable:built.restartable,human_gate:built.human_gate||null,last_error:built.last_error||null,findings:built.findings||[],stage:built.stage||'error',execution_id:built.execution_id||null,attempted:built.attempted||null,passthrough:built.passthrough||{}};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const clean=v=>typeof v==='string'?v.trim():'';
if(!x.has_task_id||!x.task_id){
  return[{json:{...x,activity_sync_ready:false,skip_trace:true}}];
}
const cas=obj(x.attempted)?x.attempted:(obj(x.cas_snapshot)?x.cas_snapshot:{});
const version=Number(cas.version||x.human_gate?.expected_version||0)||null;
// awaiting_human from error path is HITL (wait), not a red CASE_ERROR block.
const statusLabel=x.cas_status==='retryable_error'?'RETRYABLE_ERROR'
  :x.cas_status==='failed'?'FATAL_ERROR'
  :x.cas_status==='awaiting_human'?(clean(x.error_code)||'NEEDS_DECISION')
  :'CASE_ERROR';
const summary=x.safe_message;
const details={
  error_code:x.error_code,
  taxonomy_scenario:x.taxonomy_scenario,
  restartable:Boolean(x.restartable),
  stage:x.stage,
  execution_id:x.execution_id||null,
  case_id:x.task_id,
  version,
  findings_count:Array.isArray(x.findings)?x.findings.length:0,
};
const handoff={
  event_type:'handoff',
  stage:'error',
  status:statusLabel,
  summary,
  brief:summary,
  actor:'error_handler',
  handoff:{
    from_specialist:'universal_orchestrator',
    to_specialist:'human_operator',
    from_role:'Orchestrator',
    to_role:'User',
    details,
  },
  human_gate:x.human_gate||null,
};
const mas_trace_event={
  trace_id:`trace_${x.task_id}_${Date.now().toString(36)}`,
  task_id:x.task_id,
  stage:'error',
  event_type:'handoff',
  actor:'error_handler',
  status:statusLabel,
  summary,
  error_code:x.error_code,
  safe_message:summary,
  redacted_args:details,
};
return[{json:{
  ...x,
  skip_trace:false,
  mas_trace_event,
  passthrough:{...(obj(x.passthrough)?x.passthrough:{}),skip_activity_sync:true,task_id:x.task_id},
  activity_status:x.cas_status,
  activity_version:version,
  activity_human_gate:x.human_gate||null,
  activity_status_message:summary,
  activity_event:handoff,
}}];
""".strip()


FORMAT_ACK = r"""
const built=(()=>{try{return $('Build CAS error patch').first().json||{}}catch{return {}}})();
const classified=(()=>{try{return $('Classify MAS error event').first().json||{}}catch{return {}}})();
const x={...classified,...built,...($json||{})};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
if(x.accepted===false||x.code==='CASE_ID_REQUIRED'||!x.task_id){
  return[{json:{
    contract:'mas_error_ack',
    contract_version:'1.0',
    accepted:false,
    code:x.code||'CASE_ID_REQUIRED',
    message:x.message||'Error workflow refused anonymous failure: task_id/case_id is required.',
    task_id:null,
    restartable:false,
    cas_persisted:false,
    activity_notified:false,
    passthrough:x.passthrough||null,
  }}];
}
let activity={attempted:false,stored:false};
try{
  const http=$('POST error handoff to MAS Activity').first().json||{};
  activity={attempted:true,stored:Boolean(http.stored||http.ok),count:Number(http.count||0),task_id:http.task_id||x.task_id};
}catch(_){
  activity={attempted:false,stored:false,note:'activity_post_skipped_or_failed'};
}
let casOk=Boolean(x.cas_already_persisted||x.skip_cas||!x.should_persist_cas);
try{
  if(x.should_persist_cas){
    const persisted=$('Call CAS persist — error case').first().json||{};
    casOk=persisted.cas_succeeded===true||String(persisted.status||'')===String(x.cas_status||'');
  }
}catch(_){ /* keep casOk */ }

return[{json:{
  contract:'mas_error_ack',
  contract_version:'1.0',
  accepted:true,
  task_id:x.task_id,
  version:x.activity_version||x.attempted?.version||null,
  status:x.cas_status,
  error_code:x.error_code,
  taxonomy_scenario:x.taxonomy_scenario,
  safe_message:x.safe_message,
  restartable:Boolean(x.restartable),
  human_gate:x.human_gate||x.activity_human_gate||null,
  cas_persisted:casOk,
  activity_notified:Boolean(activity.stored||activity.attempted),
  activity,
  last_error:x.last_error||null,
  passthrough:x.passthrough||null,
}}];
""".strip()


NORMALIZE_N8N_ERROR = r"""
const root=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const clean=v=>typeof v==='string'?v.trim():'';
const exec=obj(root.execution)?root.execution:{};
const wf=obj(root.workflow)?root.workflow:{};
const err=obj(exec.error)?exec.error:(obj(root.error)?root.error:{});
const msg=clean(err.message||err.description||root.message||'');
const lastNode=clean(exec.lastNodeExecuted||(obj(err.node)?err.node.name:'')||'');
const executionId=clean(String(exec.id||root.executionId||''))||null;
const TASK_RE=/\b((?:eng|act)_[A-Za-z0-9._-]{4,80})\b/;
function findTaskId(v, depth){
  if(depth>8||v==null) return '';
  if(typeof v==='string'){
    const m=v.match(TASK_RE);
    return m?m[1]:'';
  }
  if(Array.isArray(v)){
    for(const x of v){const t=findTaskId(x,depth+1); if(t) return t;}
    return '';
  }
  if(typeof v==='object'){
    for(const k of ['task_id','taskId','case_id','caseId']){
      const t=clean(v[k]); if(t) return t;
    }
    for(const x of Object.values(v)){const t=findTaskId(x,depth+1); if(t) return t;}
  }
  return '';
}
const taskId=findTaskId(root,0);
const blob=`${msg} ${lastNode} ${clean(wf.name)}`.toLowerCase();
let scenario='uncaught';
if(/timeout|timed out|etimedout/.test(blob)) scenario='calc_timeout';
else if(/json|parse|structured/.test(blob)) scenario='invalid_json';
else if(/llm|openai|model/.test(blob)) scenario='llm_error';
const safe=[
  lastNode?`Узел «${lastNode}» упал.`:'Неперехваченный сбой n8n.',
  msg?msg.slice(0,400):'',
  wf.name?`Workflow: ${wf.name}.`:'',
  executionId?`execution ${executionId}.`:'',
].filter(Boolean).join(' ');
return[{json:{
  mas_error_event:{
    contract:'mas_error_event',
    contract_version:'1.0',
    task_id:taskId||null,
    scenario,
    code:scenario==='uncaught'?'UNCAUGHT_NODE_FAILURE':undefined,
    stage:'uncaught',
    safe_message:safe,
    execution_id:executionId,
    findings:[{code:'UNCAUGHT_NODE_FAILURE',node:lastNode||null,workflow:wf.name||null,message:msg.slice(0,400)||null}],
  },
  passthrough:{source:'n8n_error_trigger',workflow_id:wf.id||null,workflow_name:wf.name||null,last_node:lastNode||null,execution_id:executionId},
}}];
""".strip()


PREPARE_ACTIVITY = r"""
const x=$json;
if(!x.has_task_id||x.skip_trace){
  return[{json:{...x,activity_sync_ready:false}}];
}
const ACTIVITY_BASE_URL=String($('Activity connection').first().json.activity_base_url||'').trim();
const activityBase=ACTIVITY_BASE_URL.endsWith('/')?ACTIVITY_BASE_URL.slice(0,-1):ACTIVITY_BASE_URL;
const body={
  task_id:x.task_id,
  trace_id:x.mas_trace_event?.trace_id||null,
  status:x.activity_status||x.cas_status||null,
  version:x.activity_version||null,
  human_gate:x.activity_human_gate||x.human_gate||null,
  events:[x.activity_event].filter(Boolean),
};
return[{json:{
  ...x,
  activity_sync_ready:Boolean(ACTIVITY_BASE_URL),
  activity_url:`${activityBase}/v1/sync`,
  activity_body:body,
}}];
""".strip()


def bind_error_workflow_settings() -> None:
    """Stamp Settings.errorWorkflow on portable JSON (skip handler + its callees)."""
    # Retired contour: never touch live core/ workflows (they use Error — MAS Traces).
    root = ROOT / "workflows"
    paths = sorted((root / "retired").glob("*.workflow.json")) + sorted((root / "support").glob("*.workflow.json"))
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name") or "")
        settings = data.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            data["settings"] = settings
        wanted = "" if name in ERROR_WORKFLOW_SKIP else WF_ID
        if str(settings.get("errorWorkflow") or "") == wanted:
            continue
        settings["errorWorkflow"] = wanted
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"errorWorkflow {path.relative_to(ROOT)} → {wanted or '(empty)'}")


def _relayout_core() -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "relayout_core_workflows.py"
    subprocess.check_call([sys.executable, str(script)], cwd=str(ROOT.parent))


def main() -> None:
    nodes = [
        note(
            "edit after import",
            (-40, -320),
            "## edit after import\n\n**Error — MAS Case Handler** — after UI import:\n\n"
            "- Bind **Call CAS persist — error case** → `CAS — Persist Task State`\n"
            "- Bind **Call Writer — MAS Trace (error)** → `Writer — MAS Trace`\n"
            "- Set Activity URL in **Activity connection** (`activity_base_url`)\n"
            "- On other workflows: Settings → **Error workflow** → this handler (uncaught node crashes).\n\n"
            "Never notify Activity without `task_id`/`case_id`. Do not set this workflow's own Error workflow field.",
            520,
            260,
            1,
        ),
        note(
            "Error handler README",
            (-700, -420),
            "## Error — MAS Case Handler\n\n"
            "Two entries, one CAS/Trace/Activity path:\n"
            "1. **Execute Workflow** — Orchestrator branch for classified domain errors (LLM, missing data, validator). Returns `mas_error_ack` in the same run.\n"
            "2. **Error Trigger** — n8n Settings → Error workflow. Fires on uncaught node crash/timeout. Parent execution is already dead; we persist if `task_id` can be recovered.\n\n"
            "Taxonomy: LLM / invalid JSON / calc timeout / missing data / validator / RAG / document access / approval / uncaught.\n"
            "Fail-closed without case_id. Never attach this handler to CAS Persist or Trace Writer (loop).",
            620,
            360,
            5,
        ),
        node(
            "Error Trigger",
            "n8n-nodes-base.errorTrigger",
            1,
            (0, -220),
            {},
        ),
        code("Normalize n8n error trigger", (280, -220), NORMALIZE_N8N_ERROR),
        node(
            "Receive MAS error event",
            "n8n-nodes-base.executeWorkflowTrigger",
            1.2,
            (0, 0),
            {
                "inputSource": "jsonExample",
                "jsonExample": json.dumps(
                    {
                        "mas_error_event": {
                            "contract": "mas_error_event",
                            "contract_version": "1.0",
                            "task_id": "eng_example",
                            "scenario": "llm_error",
                            "stage": "plan",
                            "safe_message": "Модель не вернула ответ. Case eng_example.",
                            "findings": [{"code": "LLM_CALL_FAILED"}],
                            "cas_snapshot": {
                                "task_id": "eng_example",
                                "version": 2,
                                "status": "planning",
                                "risk_class": "high",
                                "request_json": "{\"objective\":\"example\"}",
                                "runtime_json": "{}",
                                "plan_json": "{}",
                                "packet_json": "{}",
                                "result_json": "{}",
                                "verification_json": "{}",
                                "gate_json": "{}",
                                "retry_count": 0,
                                "max_retries": 2,
                                "created_at": "2026-01-01T00:00:00.000Z",
                                "updated_at": "2026-01-01T00:00:00.000Z",
                            },
                        }
                    },
                    ensure_ascii=False,
                ),
            },
        ),
        code("Classify MAS error event", (280, 0), CLASSIFY),
        ifnode("Has case_id?", (560, 0), "={{ $json.has_task_id }}", True, "boolean"),
        code("Build CAS error patch", (840, -120), BUILD_CAS),
        ifnode("Should persist CAS?", (1120, -120), "={{ $json.should_persist_cas }}", True, "boolean"),
        node(
            "Call CAS persist — error case",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (1400, -220),
            {
                "source": "database",
                "workflowId": {
                    "__rl": True,
                    "value": "REPLACE_CAS_PERSIST_IN_UI",
                    "mode": "list",
                    "cachedResultName": "CAS — Persist Task State",
                },
                "workflowInputs": {
                    "mappingMode": "defineBelow",
                    "value": {
                        "cas_operation": "={{ $json.cas_operation }}",
                        "attempted": "={{ $json.attempted }}",
                    },
                    "matchingColumns": [],
                    "schema": [],
                    "attemptToConvertTypes": False,
                    "convertFieldsToString": False,
                },
                "mode": "once",
                "options": {"waitForSubWorkflow": True},
            },
            onError="continueRegularOutput",
        ),
        code("Prepare structured error trace", (1680, -120), PREPARE_TRACE),
        node(
            "Call Writer — MAS Trace (error)",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (1960, -120),
            {
                "source": "database",
                "workflowId": {
                    "__rl": True,
                    "value": "REPLACE_MAS_TRACE_WRITER_IN_UI",
                    "mode": "list",
                    "cachedResultName": "Writer — MAS Trace",
                },
                "workflowInputs": {
                    "mappingMode": "defineBelow",
                    "value": {
                        "mas_trace_event": "={{ $json.mas_trace_event }}",
                        "passthrough": "={{ $json.passthrough || { skip_activity_sync: true } }}",
                    },
                    "matchingColumns": [],
                    "schema": [],
                    "attemptToConvertTypes": False,
                    "convertFieldsToString": False,
                },
                "mode": "once",
                "options": {"waitForSubWorkflow": True},
            },
            onError="continueRegularOutput",
        ),
        set_fields(
            "Activity connection",
            (2100, -120),
            [("activity_base_url", "http://127.0.0.1:8200", "string")],
        ),
        code("Prepare Activity error sync", (2240, -120), PREPARE_ACTIVITY),
        ifnode("Activity sync ready?", (2520, -120), "={{ $json.activity_sync_ready }}", True, "boolean"),
        node(
            "POST error handoff to MAS Activity",
            "n8n-nodes-base.httpRequest",
            4.4,
            (2800, -220),
            {
                "method": "POST",
                "url": "={{ $json.activity_url }}",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "Content-Type", "value": "application/json"},
                    ]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ $json.activity_body }}",
                "options": {"timeout": 15000},
            },
            onError="continueRegularOutput",
            continueOnFail=True,
        ),
        code("Format MAS error ack", (3080, -120), FORMAT_ACK),
        code(
            "Reject without case_id",
            (840, 160),
            "const x=$json;return[{json:{contract:'mas_error_ack',contract_version:'1.0',accepted:false,code:'CASE_ID_REQUIRED',message:x.message||'Error workflow refused anonymous failure: task_id/case_id is required.',task_id:null,restartable:false,cas_persisted:false,activity_notified:false,passthrough:x.passthrough||null}}];",
        ),
    ]

    connections = {}
    connect(connections, "Error Trigger", "Normalize n8n error trigger")
    connect(connections, "Normalize n8n error trigger", "Classify MAS error event")
    connect(connections, "Receive MAS error event", "Classify MAS error event")
    connect(connections, "Classify MAS error event", "Has case_id?")
    connect(connections, "Has case_id?", "Build CAS error patch", si=0)
    connect(connections, "Has case_id?", "Reject without case_id", si=1)
    connect(connections, "Build CAS error patch", "Should persist CAS?")
    connect(connections, "Should persist CAS?", "Call CAS persist — error case", si=0)
    connect(connections, "Should persist CAS?", "Prepare structured error trace", si=1)
    connect(connections, "Call CAS persist — error case", "Prepare structured error trace")
    connect(connections, "Prepare structured error trace", "Call Writer — MAS Trace (error)")
    connect(connections, "Call Writer — MAS Trace (error)", "Activity connection")
    connect(connections, "Activity connection", "Prepare Activity error sync")
    connect(connections, "Prepare Activity error sync", "Activity sync ready?")
    connect(connections, "Activity sync ready?", "POST error handoff to MAS Activity", si=0)
    connect(connections, "Activity sync ready?", "Format MAS error ack", si=1)
    connect(connections, "POST error handoff to MAS Activity", "Format MAS error ack")
    connect(connections, "Reject without case_id", "Format MAS error ack")

    wf = {
        "id": WF_ID,
        "name": WF_NAME,
        "active": False,
        "isArchived": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner", "errorWorkflow": ""},
        "meta": {"templateCredsSetupCompleted": True},
        "tags": [],
        "pinData": {},
        "versionId": str(uuid.uuid4()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(nodes)} nodes)")
    bind_error_workflow_settings()
    _relayout_core()


if __name__ == "__main__":
    main()
