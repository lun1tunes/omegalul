#!/usr/bin/env python3
"""Generate the thin MAS orchestrator (one n8n execution = one step)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options, structured_parser_params

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/mas-orchestrator.workflow.json"
WF_ID = "e9bbdb6e-3b7c-5dc0-851a-30bd9f2eb0d6"
WF_NAME = "Orchestrator — MAS"
ERROR_WF_ID = "63116836-8724-595e-bc5e-dd6e743e2586"
PG = {"postgres": {"id": "REPLACE_IN_UI", "name": "REPLACE: SCHEDULE PostgreSQL / PGVector credential"}}
OA = {
    "openAiApi": {
        "id": "REPLACE_IN_UI",
        "name": "REPLACE: Qwen OpenAI-compatible planner chat credential",
    }
}
HDR = {"httpHeaderAuth": {"id": "REPLACE_IN_UI", "name": "REPLACE: engineering orchestrator inbound key"}}

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["status_message", "action"],
    "properties": {
        "status_message": {"type": "string"},
        "plan_update": {"type": "array", "items": {"type": "object"}},
        "action": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {"enum": ["call_agent", "ask_user", "finish"]},
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "handoff_message": {"type": "string"},
                "task": {"type": "object"},
                "question_id": {"type": "string"},
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "result": {"type": "object"},
            },
        },
    },
}

SYSTEM = """Ты оркестратор инженерной задачи.

Ты должен выбрать одно действие:
1. call_agent
2. ask_user
3. finish

Агенты в реестре:
- excel_extractor — Excel: скважины, даты ввода, дебиты. Не пишет SCHEDULE. Вызывай только если в artifacts есть excel.
- calculation_agent — поверхность + траектория → top_perforation_md.
- schedule_builder — исходный SCHEDULE (.inc) → новый .INC через свой LLM + FastAPI tools (даты ввода, перепривязка групп). Не пишет .INC сам.

Правила:
- Если не хватает данных, выбери ask_user. Не придумывай даты, скважины и строки .INC.
- Типичный путь «новые даты ввода»: excel_extractor, затем schedule_builder, затем finish.
- Типичный путь «перепривязка в группу / групповой контроль»: сразу schedule_builder (Excel не нужен), затем finish. Даты ввода этих скважин брать из baseline SCHEDULE.
- Не вызывай excel_extractor, если Excel нет в artifacts.
- Если в data/artifacts уже есть schedule_out — finish.
- Имена INCLUDE-файлов (GRUPTREE.GRDECL и т.п.) — это состав пакета, не просьба менять группы. Не пиши в handoff про перепривязку групп, если пользователь об этом не просил.
- Если следующий шаг очевиден, выбери call_agent.
- Не вызывай агента, которого нет в реестре.
- Не разбирай SCHEDULE/.INC сам и не ходи в RAG.
- Возвращай только JSON.
- status_message пиши по-русски, коротко, в стиле текущего шага.
- handoff_message — человекопонятное обращение к агенту, например: «Агент Excel, достань из файла данные по датам ввода скважин.»
"""


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-orch:{name}"))


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


def note(name, pos, content, w=440, h=280, color=5):
    return node(name, "n8n-nodes-base.stickyNote", 1, pos, {"content": content, "width": w, "height": h, "color": color})


def code(name, pos, js, **extra):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js}, **extra)


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


def postgres(name, pos, query, params_expr, batching="single"):
    return node(
        name,
        "n8n-nodes-base.postgres",
        2.6,
        pos,
        {
            "operation": "executeQuery",
            "query": query,
            "options": {
                "queryReplacement": params_expr,
                "queryBatching": batching,
                "largeNumbersOutput": "text",
                "replaceEmptyStrings": False,
            },
        },
        credentials=PG,
        alwaysOutputData=True,
    )


def http_json(name, pos, url, body, timeout=180000):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.4,
        pos,
        {
            "method": "POST",
            "url": url,
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Content-Type", "value": "application/json"},
                    {"name": "X-API-Key", "value": "={{ $json.excel_tools_api_key || '' }}"},
                ]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body,
            "options": {"timeout": timeout, "response": {"response": {"fullResponse": False}}},
        },
        onError="continueRegularOutput",
    )


def execute_workflow(name, pos, placeholder, cached_name, inputs):
    return node(
        name,
        "n8n-nodes-base.executeWorkflow",
        1.3,
        pos,
        {
            "source": "database",
            "workflowId": {
                "__rl": True,
                "value": placeholder,
                "mode": "list",
                "cachedResultName": cached_name,
            },
            "workflowInputs": {
                "mappingMode": "defineBelow",
                "value": inputs,
                "matchingColumns": [],
                "schema": [],
                "attemptToConvertTypes": False,
                "convertFieldsToString": False,
            },
            "mode": "once",
            "options": {"waitForSubWorkflow": True},
        },
        onError="continueRegularOutput",
    )


def if_true(name, pos, left):
    return node(
        name,
        "n8n-nodes-base.if",
        2.3,
        pos,
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [
                    {
                        "id": nid(f"{name}-cond"),
                        "leftValue": left,
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true"},
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


NORMALIZE = r"""
const raw=$input.first().json||{};
const body=raw.body&&typeof raw.body==='object'?raw.body:raw;
const action=String(body.action||raw.action||'step').trim().toLowerCase();
let caseId=String(body.case_id||raw.case_id||'').trim();
const goal=String(body.task_description||body.goal||raw.task_description||raw.goal||'').trim();
const taskName=String(body.task_name||raw.task_name||'').trim();
const humanResponse=String(body.human_response||raw.human_response||'');
const gateId=String(body.gate_id||raw.gate_id||'');
const requestedBy=String(body.requested_by||raw.requested_by||'');
const activityBaseUrl=String(raw.activity_base_url||'').trim();
const orchestratorStepUrl=String(raw.orchestrator_step_url||'').trim();
const executionId=String($execution.id||'');
const readinessId=!caseId||caseId==='CASE-readiness-probe';
if(action==='probe'||(action==='status'&&readinessId)){
  return [{json:{
    case_id:caseId||'CASE-readiness-probe',
    action:'probe',
    is_probe:true,
    is_status:false,
    is_resume:false,
    needs_create:false,
    next_status:'probe',
    status:'probe',
    action_type:'probe',
    should_continue:false
  }}];
}
let needsCreate=false;
if(action==='create'){
  if(!caseId) caseId='CASE-'+Date.now().toString(16)+'-'+Math.random().toString(16).slice(2,8);
  if(!goal) throw new Error('task_description is required for action=create');
  needsCreate=true;
} else if(!caseId){
  if(action!=='start') throw new Error('case_id is required');
  if(!goal) throw new Error('task_description is required for action=start');
  caseId='CASE-'+Date.now().toString(16)+'-'+Math.random().toString(16).slice(2,8);
  needsCreate=true;
}
return [{json:{
  case_id:caseId,
  action,
  goal,
  task_name:taskName,
  human_response:humanResponse,
  gate_id:gateId,
  requested_by:requestedBy,
  activity_base_url:activityBaseUrl,
  orchestrator_step_url:orchestratorStepUrl,
  is_probe:false,
  is_status:action==='status',
  is_resume:action==='resume',
  needs_create:needsCreate,
  execution_id:executionId,
  workflow_name:'orchestrator',
  sql_parameters:[caseId],
  exec_sql_parameters:[executionId, caseId, 'orchestrator'],
}}];
"""

CREATE_CASE = r"""
const req=$json||{};
const goal=String(req.goal||'').trim();
const state={
  case_id:req.case_id,
  goal,
  task_name:req.task_name||'',
  status:'running',
  plan:[],
  artifacts:{},
  data:{},
  current_task:null,
  hitl:{pending:false,questions:[],answers:{}},
  last_error:null,
  step_count:0
};
const persistEvents=[[req.case_id,'','case.created','user','','new',('Принял задачу: '+goal).slice(0,200),'',JSON.stringify({requested_by:req.requested_by||'',task_name:req.task_name||'',note:'orchestrator start; files stay in Activity /cases/{id}/artifacts'})]];
return [{json:{
  ...req,
  state,
  create_sql_parameters:[req.case_id, JSON.stringify(state), 'running'],
  persist_events:persistEvents,
  event_sql_parameters:persistEvents[0]
}}];
"""

APPLY_EXTRAS = r"""
const req=$('Normalize step request').first().json||{};
const load=$json||{};
const parse=v=>{if(v&&typeof v==='object'&&!Array.isArray(v))return v;try{const p=JSON.parse(String(v||'{}'));return p&&typeof p==='object'&&!Array.isArray(p)?p:{}}catch{return {}}};
const state=parse(load.state||load.State||{});
if(req.is_status===true){
  const hitl=parse(state.hitl);
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const status=String(load.status||state.status||'');
  const version=Number(state.version||state.step_count||0);
  let human_gate=null;
  if(status==='waiting_user'&&(hitl.pending===true||questions.length)){
    const q0=questions[0]&&typeof questions[0]==='object'?questions[0]:{};
    human_gate={
      gate_id:q0.question_id||'hitl',
      kind:'needs_input',
      reason:q0.question||state.goal||'Нужен ответ',
      expected_version:version,
      questions
    };
  }
  return [{json:{
    ...req,
    case_id:req.case_id,
    is_status:true,
    did_resume:false,
    next_status:status,
    status,
    action_type:'status',
    should_continue:false,
    human_gate,
    version,
    restartable:status==='done'||status==='failed',
    has_schedule_out:Boolean(state.artifacts&&state.artifacts.schedule_out)
  }}];
}
if(req.is_resume===true){
  const answer=String(req.human_response||'').trim();
  const hitl=parse(state.hitl);
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const qid=String(req.gate_id||(questions[0]&&questions[0].question_id)||'Q-1');
  const answers={...(hitl.answers&&typeof hitl.answers==='object'?hitl.answers:{})};
  answers[qid]=answer;
  state.hitl={pending:false,questions,answers};
  state.status='running';
  const persistEvents=[[req.case_id,'','hitl.answered','user','','answered',answer?('Пользователь ответил: '+answer):'Пользователь ответил','',JSON.stringify({question_id:qid,answer})]];
  return [{json:{
    ...req,
    state,
    did_resume:true,
    is_status:false,
    next_status:'running',
    update_sql_parameters:[JSON.stringify(state),'running',req.case_id],
    persist_events:persistEvents,
    event_sql_parameters:persistEvents[0]
  }}];
}
return [{json:{...req,...load,state,did_resume:false,is_status:false}}];
"""

VALIDATE_CASE = r"""
const req=$('Normalize step request').first().json||{};
const row=$json&&typeof $json==='object'?$json:{};
const requested=String(req.case_id||'').trim();
const loaded=String(row.case_id||row.Case_id||'').trim();
if(!requested||loaded!==requested){
  return [{json:{
    ...req,
    status:'not_found',
    next_status:'not_found',
    action_type:'not_found',
    should_continue:false,
    case_loaded:false,
    message:`Кейс ${requested||'(без case_id)'} не найден в control plane`
  }}];
}
return [{json:{...req,...row,case_loaded:true}}];
"""

PREPARE_DECISION = r"""
const req=$('Normalize step request').first().json||{};
const extras=(()=>{try{return $('Apply request extras').first().json}catch{return null}})();
const load=$('Load case').first().json||{};
const parse=v=>{if(v&&typeof v==='object')return v;try{return JSON.parse(String(v||'{}'))}catch{return {}}};
const state=(extras&&extras.state&&typeof extras.state==='object')?extras.state:parse(load.state||load.State||{});
if(!state.artifacts||typeof state.artifacts!=='object') state.artifacts={};
if(state.artifacts.file&&!state.artifacts.excel) state.artifacts.excel=state.artifacts.file;
if(state.artifacts.schedule_files&&!state.artifacts.schedule_source) state.artifacts.schedule_source=state.artifacts.schedule_files;
const status=String((extras&&extras.next_status)||load.status||state.status||'running');
const registry=$('Load agent registry').all().map(i=>i.json||{}).filter(r=>r&&r.agent_id);
const artifacts=state.artifacts&&typeof state.artifacts==='object'?state.artifacts:{};
const data=state.data&&typeof state.data==='object'?state.data:{};
const plan=Array.isArray(state.plan)?state.plan:[];
const hitl=state.hitl&&typeof state.hitl==='object'?state.hitl:{};
const compact={
  goal:state.goal||'',
  artifacts_present:Object.keys(artifacts).filter(k=>artifacts[k]!=null&&artifacts[k]!==''),
  artifact_files:Object.fromEntries(Object.entries(artifacts).map(([k,v])=>[k,(v&&typeof v==='object'&&v.filename)?v.filename:k])),
  has_excel:Boolean(artifacts.excel),
  has_schedule_source:Boolean(artifacts.schedule_source),
    has_schedule_out:Boolean(artifacts.schedule_out&&String(artifacts.schedule_out).trim()),
  excel_facts:(data.excel&&Array.isArray(data.excel.facts))?data.excel.facts.length:(Array.isArray(data.facts)?data.facts.length:0),
  schedule_root:state.schedule_root||'',
  data_present:Object.fromEntries(Object.keys(data).map(k=>[k,!(data[k]==null||data[k]==={}||data[k]===[])]) ),
  plan:plan.map(p=>({id:p.id,status:p.status})),
  current_task:state.current_task||null,
  hitl_pending:hitl.pending===true,
  hitl_answers:hitl.answers||{},
  step_count:Number(state.step_count||0),
  last_error:state.last_error||null
};
const hint=[];
if(compact.has_excel&&!data.excel) hint.push('Сначала excel_extractor — в artifacts есть Excel, фактов ещё нет.');
else if((compact.excel_facts>0||(data.excel&&data.excel.facts))&&compact.has_schedule_source&&!compact.has_schedule_out) hint.push('Дальше schedule_builder — факты Excel и исходный .inc уже есть.');
else if(!compact.has_excel&&compact.has_schedule_source&&!compact.has_schedule_out) hint.push('Excel нет: сразу schedule_builder по тексту задачи и baseline .inc.');
else if(compact.has_schedule_out) hint.push('schedule_out уже есть — finish.');
const prompt=`Цель:\n${compact.goal}\n\nТекущее состояние:\n${JSON.stringify(compact,null,2)}\n\nПодсказка следующего шага:\n${hint.join(' ')||'выбери по реестру агентов'}\n\nДоступные агенты:\n${JSON.stringify(registry,null,2)}\n`;
const endpoints=$('Runtime endpoints').first().json||{};
return [{json:{
  ...req,
  ...endpoints,
  case_id:req.case_id,
  state,
  status,
  registry,
  compact,
  planner_input:prompt,
  step_count:compact.step_count
}}];
"""

PARSE_DECISION = r"""
const prev=$('Prepare decision context').first().json||{};
const raw=$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
const out=parse(raw.output||raw.text||raw);
const decision=obj(out.action)?out:(obj(raw)?raw:{});
const action=obj(decision.action)?decision.action:{type:'ask_user',question_id:'Q-parse',question:'Не удалось разобрать решение оркестратора',options:[]};
const type=String(action.type||'').trim();
const state=obj(prev.state)?{...prev.state}:{};
state.step_count=Number(state.step_count||0)+1;
if(Array.isArray(decision.plan_update)){
  const by=new Map((Array.isArray(state.plan)?state.plan:[]).map(p=>[p.id,p]));
  for(const item of decision.plan_update){if(item&&item.id) by.set(item.id,{...(by.get(item.id)||{}),...item});}
  state.plan=[...by.values()];
}
const statusMessage=String(decision.status_message||'').trim()||'Шаг оркестратора';
const decisionEvent={
  kind:'orchestrator.decision',
  actor:'orchestrator',
  status_message:statusMessage,
  payload:{action_type:type,agent_id:action.agent_id||null,step_count:state.step_count}
};
const events=[];
let nextStatus='running';
let agentTask=null;
if(type==='call_agent'){
  const taskId=String(action.task_id||`TASK-${state.step_count}`).trim();
  agentTask={
    case_id:prev.case_id,
    task_id:taskId,
    agent_id:String(action.agent_id||'').trim(),
    objective:String(action.task&&action.task.objective||state.goal||''),
    handoff_message:String(action.handoff_message||''),
    inputs:{...(obj(action.task)?action.task:{}),artifacts:state.artifacts||{},activity_base_url:String(prev.activity_base_url||'http://mas-activity:8200').replace(/\/$/,''),schedule_root:state.schedule_root||(obj(action.task)?action.task.schedule_root:'')},
    context:{data:state.data||{},hitl:state.hitl||{}},
    constraints:{units:'METRIC'}
  };
  state.current_task=agentTask;
  events.push(decisionEvent, {
    kind:'agent.handoff',
    actor:'orchestrator',
    agent_id:agentTask.agent_id,
    task_id:taskId,
    status_message:statusMessage,
    handoff_message:agentTask.handoff_message,
    payload:{task_id:taskId}
  });
} else if(type==='ask_user'){
  nextStatus='waiting_user';
  const q={question_id:String(action.question_id||`Q-${state.step_count}`),question:String(action.question||'Нужно уточнение'),options:Array.isArray(action.options)?action.options:[]};
  state.hitl={pending:true,questions:[q],answers:(state.hitl&&state.hitl.answers)||{}};
  events.push(decisionEvent, {kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:statusMessage,payload:q});
} else if(type==='finish'){
  nextStatus='done';
  state.current_task=null;
  state.data={...(state.data||{}),result:action.result||{}};
  events.push({kind:'case.finished',actor:'orchestrator',status:'done',status_message:statusMessage,payload:{...(obj(action.result)?action.result:{}),action_type:'finish'}});
} else {
  nextStatus='waiting_user';
  const q={question_id:'Q-unknown',question:'Оркестратор вернул неизвестное действие',options:[]};
  state.hitl={pending:true,questions:[q],answers:{}};
  events.push({kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:statusMessage,payload:q});
}
state.status=nextStatus;
const persistEvents=events.map(e=>[
  prev.case_id,
  e.task_id||'',
  e.kind,
  e.actor,
  e.agent_id||'',
  e.status||'',
  e.status_message||'',
  e.handoff_message||'',
  JSON.stringify(e.payload||{})
]);
return [{json:{
  ...prev,
  decision,
  action_type:type,
  agent_id:agentTask?agentTask.agent_id:null,
  agent_task:agentTask,
  state,
  next_status:nextStatus,
  should_call_agent:type==='call_agent'&&Boolean(agentTask&&agentTask.agent_id),
  should_continue:false,
  events,
  update_sql_parameters:[JSON.stringify(state), nextStatus, prev.case_id],
  event_sql_parameters:persistEvents[0],
  persist_events:persistEvents
}}];
"""

WRITE_EVENTS = r"""
const fromParse=(()=>{try{return $('Parse decision').first().json}catch{return null}})();
const fromMerge=(()=>{try{return $('Merge agent result').first().json}catch{return null}})();
const prev=fromMerge||fromParse||$json||{};
const rows=Array.isArray(prev.persist_events)?prev.persist_events:[];
if(!rows.length) return [{json:{...prev,p1:'',p2:'',p3:'',p4:'',p5:'',p6:'',p7:'',p8:'',p9:'{}'}}];
return rows.map(params=>{
  const vals=(Array.isArray(params)?params:[]).map(v=>v===null||v===undefined?'':String(v));
  return {json:{
    ...prev,
    p1:vals[0]||'',
    p2:vals[1]||'',
    p3:vals[2]||'',
    p4:vals[3]||'',
    p5:vals[4]||'',
    p6:vals[5]||'',
    p7:vals[6]||'',
    p8:vals[7]||'',
    p9:vals[8]||'{}',
    event_sql_parameters:vals
  }};
});
"""

ROUTE = r"""
const x=$json;
const id=String(x.agent_id||'').trim();
let route='none';
if(x.should_call_agent!==true) route='none';
else if(id==='excel_extractor') route='excel';
else if(id==='calculation_agent') route='calc';
else if(id==='schedule_builder') route='schedule';
else route='unknown';
return [{json:{...x,route}}];
"""

MERGE = r"""
const prev=$('Prepare agent call').first().json||$('Parse decision').first().json||{};
const http=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const result=obj(http.body)?http.body:(obj(http)?http:{});
const status=String(result.status||(http.error?'failed':'completed'));
const state=obj(prev.state)?{...prev.state}:{};
const data=obj(state.data)?{...state.data}:{};
const artifacts=obj(state.artifacts)?{...state.artifacts}:{};
const agentId=String(prev.agent_id||result.agent_id||'');
const bucket=agentId==='excel_extractor'?'excel':(agentId==='calculation_agent'?'calc':(agentId==='schedule_builder'?'schedule':'agent'));
if(status==='completed'){
  data[bucket]=result.data||result;
  if(agentId==='excel_extractor'&&result.data&&Array.isArray(result.data.facts)) data.facts=result.data.facts;
  Object.assign(artifacts, result.artifacts||{});
  state.current_task=null;
  state.last_error=null;
}
if(Array.isArray(state.plan)){
  state.plan=state.plan.map(p=>{
    if(!p||typeof p!=='object') return p;
    if(status==='completed'&&String(p.status)==='running') return {...p,status:'done'};
    return p;
  });
}
let nextStatus='running';
let shouldContinue=true;
const events=[];
if(status==='needs_input'){
  nextStatus='waiting_user';
  shouldContinue=false;
  const reqs=Array.isArray(result.requests)?result.requests:[];
  const q=reqs[0]||{question_id:'Q-agent',question:String(result.message||'Агенту нужны данные'),options:[]};
  state.hitl={pending:true,questions:reqs.length?reqs:[q],answers:(state.hitl&&state.hitl.answers)||{}};
  events.push({kind:'hitl.request',actor:'orchestrator',agent_id:agentId,status:'waiting_user',status_message:q.question,payload:q});
} else if(status==='completed'){
  events.push({
    kind:'agent.result',
    actor:agentId||'agent',
    agent_id:agentId,
    task_id:(prev.agent_task&&prev.agent_task.task_id)||'',
    status:'completed',
    status_message:String(result.message||'Агент завершил работу'),
    payload:{data_keys:Object.keys((result.data&&typeof result.data==='object')?result.data:{}),artifacts:Object.keys((result.artifacts&&typeof result.artifacts==='object')?result.artifacts:{})}
  });
} else if(status==='failed'||http.error){
  nextStatus='running';
  shouldContinue=true;
  state.last_error={message:String(result.message||http.message||'agent failed'),agent_id:agentId};
  data[bucket]=result.data||result;
  events.push({kind:'agent.failed',actor:agentId||'agent',agent_id:agentId,status:'failed',status_message:String(result.message||http.message||'Агент вернул ошибку'),payload:{message:state.last_error.message,issues:result.issues||[]}});
}
if(Number(state.step_count||0)>=24){
  nextStatus='failed';
  shouldContinue=false;
  events.push({kind:'case.failed',actor:'orchestrator',status:'failed',status_message:'Превышен лимит шагов оркестратора'});
}
state.data=data;
state.artifacts=artifacts;
state.status=nextStatus;
const persistEvents=events.map(e=>[
  prev.case_id, e.task_id||'', e.kind, e.actor, e.agent_id||'', e.status||'', e.status_message||'', e.handoff_message||'', JSON.stringify(e.payload||{})
]);
return [{json:{
  ...prev,
  agent_result:result,
  state,
  next_status:nextStatus,
  should_continue:shouldContinue&&nextStatus==='running',
  update_sql_parameters:[JSON.stringify(state), nextStatus, prev.case_id],
  persist_events:persistEvents,
  events,
  event_sql_parameters:persistEvents[0],
  continue_url:`${String(prev.activity_base_url||'http://mas-activity:8200').replace(/\/$/,'')}/cases/${encodeURIComponent(String(prev.case_id||''))}/run`
}}];
"""

FINISH_NONE = r"""
const x=$json;
const cont=x.action_type==='finish'?false:false;
return [{json:{...x, should_continue:false, continue_url:`${String(x.activity_base_url||'http://mas-activity:8200').replace(/\/$/,'')}/cases/${encodeURIComponent(String(x.case_id||''))}/run`}}];
"""


def main() -> None:
    nodes = [
        note(
            "edit after import",
            (-200, -420),
            "## edit after import\n\n**Orchestrator — MAS** — thin loop:\n- Bind Postgres on load/insert/update\n- Bind **Qwen** OpenAI-compatible credential on Decision Chat Model\n- Bind inbound header auth on webhook\n- Bind **Call Excel Extractor** → `Agent — Excel Extractor` (executeWorkflow)\n- Bind **Call Schedule Builder** → `Agent — Schedule Builder` (executeWorkflow)\n- Runtime endpoints patched by lab_soft_redeploy\n\n`action`: probe | status | start | create | step | resume\n- Activity `/cases` stores files; specialists fetch `/cases/{id}/artifacts/{id}` from their FastAPI tools. n8n never carries binaries.\n- **status** loads case and returns `human_gate` without LLM\n- Decision is Basic LLM Chain + Structured Output (no Agent tools)\n\nOne execution = one step.",
            480,
            420,
            1,
        ),
        node(
            "Authenticated MAS webhook",
            "n8n-nodes-base.webhook",
            2.1,
            (0, 0),
            {
                "httpMethod": "POST",
                "path": "mas-orchestrator-step",
                "authentication": "headerAuth",
                "responseMode": "lastNode",
                "options": {},
            },
            credentials=HDR,
            webhookId="a1000003-mas-orch-wh-0001-800000000001",
        ),
        set_fields(
            "Runtime endpoints",
            (240, 0),
            [
                ("calculation_agent_url", "http://math-service:8100/agent/run", "string"),
                ("orchestrator_step_url", "http://n8n:5678/webhook/mas-orchestrator-step", "string"),
                ("activity_base_url", "http://mas-activity:8200", "string"),
                ("excel_tools_api_key", "", "string"),
            ],
        ),
        code("Normalize step request", (480, 0), NORMALIZE),
        if_true("Probe ping?", (640, 0), "={{ Boolean($json.is_probe) }}"),
        if_true("Needs create?", (640, 160), "={{ Boolean($json.needs_create) }}"),
        code("Prepare start case", (800, 280), CREATE_CASE),
        postgres(
            "Insert new case",
            (1000, 280),
            "INSERT INTO cases (case_id, state, status, updated_at) VALUES ($1, $2::jsonb, $3, now()) ON CONFLICT (case_id) DO NOTHING",
            "={{ $json.create_sql_parameters }}",
        ),
        code("Expand start events", (1180, 280), WRITE_EVENTS),
        postgres(
            "Insert start events",
            (1360, 280),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore after start",
            (1540, 280),
            "const prev=$('Prepare start case').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        postgres(
            "Insert execution map",
            (720, -120),
            "INSERT INTO executions(execution_id, case_id, workflow_name) VALUES ($1, $2, $3) ON CONFLICT (execution_id) DO UPDATE SET case_id = EXCLUDED.case_id",
            "={{ $json.exec_sql_parameters }}",
        ),
        postgres(
            "Load case",
            (960, -120),
            "SELECT case_id, state, status, updated_at FROM cases WHERE case_id = $1",
            "={{ $('Normalize step request').first().json.sql_parameters }}",
        ),
        code("Validate loaded case", (1120, -240), VALIDATE_CASE),
        if_true("Case found?", (1280, -240), "={{ Boolean($json.case_loaded) }}"),
        code("Apply request extras", (1120, -120), APPLY_EXTRAS),
        if_true("Status only?", (1280, -120), "={{ Boolean($json.is_status) }}"),
        if_true("Resume persist?", (1280, 40), "={{ Boolean($json.did_resume) }}"),
        postgres(
            "Update case after resume",
            (1480, 40),
            "UPDATE cases SET state = $1::jsonb, status = $2, updated_at = now() WHERE case_id = $3",
            "={{ $json.update_sql_parameters }}",
        ),
        code("Expand resume events", (1660, 40), WRITE_EVENTS),
        postgres(
            "Insert resume events",
            (1840, 40),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore after resume",
            (2020, 40),
            "const prev=$('Apply request extras').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        postgres(
            "Load agent registry",
            (960, 80),
            "SELECT agent_id, title, when_to_use, input_required, output_provides FROM agent_registry ORDER BY agent_id",
            "={{ [] }}",
        ),
        code("Prepare decision context", (1200, 0), PREPARE_DECISION),
        node(
            "Decision LLM",
            "@n8n/n8n-nodes-langchain.chainLlm",
            1.9,
            (1440, 0),
            {
                "promptType": "define",
                "text": "={{ $json.planner_input }}",
                "hasOutputParser": True,
                "needsFallback": False,
                "messages": {
                    "messageValues": [
                        {
                            "type": "SystemMessagePromptTemplate",
                            "message": SYSTEM,
                        }
                    ]
                },
            },
        ),
        node(
            "Decision Chat Model — configure in UI",
            "@n8n/n8n-nodes-langchain.lmChatOpenAi",
            1.3,
            (1440, -180),
            {
                "model": {"mode": "id", "value": "qwen3.6-plus"},
                "options": chat_model_options(max_tokens=1024),
                "responsesApiEnabled": False,
            },
            credentials=OA,
        ),
        node(
            "Decision Structured Output",
            "@n8n/n8n-nodes-langchain.outputParserStructured",
            1.3,
            (1680, -180),
            structured_parser_params(json.dumps(DECISION_SCHEMA, ensure_ascii=False)),
        ),
        code("Parse decision", (1920, 0), PARSE_DECISION),
        postgres(
            "Update case after decision",
            (2160, -120),
            "UPDATE cases SET state = $1::jsonb, status = $2, updated_at = now() WHERE case_id = $3",
            "={{ $json.update_sql_parameters }}",
        ),
        code("Expand decision events", (2160, 80), WRITE_EVENTS),
        postgres(
            "Insert decision events",
            (2400, 80),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore parsed decision",
            (2640, 0),
            "const prev=$('Parse decision').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        code("Prepare agent call", (2760, 0), ROUTE),
        node(
            "Action router",
            "n8n-nodes-base.switch",
            3.4,
            (2880, 0),
            {"mode": "expression", "numberOutputs": 4, "output": "={{ ({excel:0,calc:1,schedule:2,none:3,unknown:3})[$json.route] ?? 3 }}"},
        ),
        execute_workflow(
            "Call Excel Extractor",
            (3120, -240),
            "REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI",
            "Agent — Excel Extractor",
            {"agent_task": "={{ $json.agent_task }}"},
        ),
        http_json("Call Calculation Agent", (3120, -40), "={{ $json.calculation_agent_url }}", "={{ $json.agent_task }}"),
        execute_workflow(
            "Call Schedule Builder",
            (3120, 160),
            "REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI",
            "Agent — Schedule Builder",
            {"agent_task": "={{ $json.agent_task }}"},
        ),
        code("No agent this step", (3120, 360), FINISH_NONE),
        code("Merge agent result", (3360, -40), MERGE),
        postgres(
            "Update case after agent",
            (3600, -160),
            "UPDATE cases SET state = $1::jsonb, status = $2, updated_at = now() WHERE case_id = $3",
            "={{ $json.update_sql_parameters }}",
        ),
        code("Expand agent events", (3600, 40), WRITE_EVENTS),
        postgres(
            "Insert agent events",
            (3840, 40),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore after agent events",
            (3960, 0),
            "const prev=$('Merge agent result').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        if_true("Continue loop?", (4080, 0), "={{ $json.should_continue }}"),
        node(
            "POST continue run",
            "n8n-nodes-base.httpRequest",
            4.4,
            (4320, -80),
            {
                "method": "POST",
                "url": "={{ $json.continue_url }}",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ ({action: 'step', case_id: $json.case_id, source: 'orchestrator'}) }}",
                "options": {"timeout": 5000},
            },
            onError="continueRegularOutput",
        ),
        code(
            "Prepare Activity ack",
            (4320, 120),
            r"""const x=$json||{};
const kind=x.action_type==='finish'?'case.finished':(x.action_type==='ask_user'||x.next_status==='waiting_user'?'hitl.request':'orchestrator.status');
const payload={contract:'mas_orchestrator_ack',case_id:x.case_id,status:x.next_status||x.status,action_type:x.action_type||null,message:x.message||null,should_continue:x.should_continue===true,human_gate:x.human_gate||null,version:x.version??null,restartable:x.restartable===true};
return [{json:{...x,activity_sync:Boolean(x.case_id&&!x.is_probe&&x.activity_base_url),activity_url:`${String(x.activity_base_url||'').replace(/\/$/,'')}/cases/${encodeURIComponent(String(x.case_id||''))}/events`,activity_event:{kind,actor:'orchestrator',status:payload.status,status_message:payload.message||kind,payload}}}];""",
        ),
        if_true("Activity sync?", (4480, 220), "={{ Boolean($json.activity_sync) }}"),
        node(
            "POST step ack to MAS Activity",
            "n8n-nodes-base.httpRequest",
            4.4,
            (4640, 120),
            {
                "method": "POST",
                "url": "={{ $json.activity_url }}",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ $json.activity_event }}",
                "options": {"timeout": 10000},
            },
            onError="continueRegularOutput",
        ),
        code(
            "Format step ack",
            (4800, 220),
            "const source=(()=>{try{return $('Prepare Activity ack').first().json}catch{return $json}})();\nconst x=source||$json;return[{json:{contract:'mas_orchestrator_ack',case_id:x.case_id,status:x.next_status||x.status,action_type:x.action_type||null,message:x.message||null,should_continue:x.should_continue===true,human_gate:x.human_gate||null,version:x.version??null,restartable:x.restartable===true}}];",
        ),
    ]

    connections = {}
    connect(connections, "Authenticated MAS webhook", "Runtime endpoints")
    connect(connections, "Runtime endpoints", "Normalize step request")
    connect(connections, "Normalize step request", "Probe ping?")
    connect(connections, "Probe ping?", "Prepare Activity ack", si=0)
    connect(connections, "Probe ping?", "Needs create?", si=1)
    connect(connections, "Needs create?", "Prepare start case", si=0)
    connect(connections, "Needs create?", "Insert execution map", si=1)
    connect(connections, "Prepare start case", "Insert new case")
    connect(connections, "Insert new case", "Expand start events")
    connect(connections, "Expand start events", "Insert start events")
    connect(connections, "Insert start events", "Restore after start")
    connect(connections, "Restore after start", "Insert execution map")
    connect(connections, "Insert execution map", "Load case")
    connect(connections, "Load case", "Validate loaded case")
    connect(connections, "Validate loaded case", "Case found?")
    connect(connections, "Case found?", "Apply request extras", si=0)
    connect(connections, "Case found?", "Prepare Activity ack", si=1)
    connect(connections, "Apply request extras", "Status only?")
    connect(connections, "Status only?", "Prepare Activity ack", si=0)
    connect(connections, "Status only?", "Resume persist?", si=1)
    connect(connections, "Resume persist?", "Update case after resume", si=0)
    connect(connections, "Resume persist?", "Load agent registry", si=1)
    connect(connections, "Update case after resume", "Expand resume events")
    connect(connections, "Expand resume events", "Insert resume events")
    connect(connections, "Insert resume events", "Restore after resume")
    connect(connections, "Restore after resume", "Load agent registry")
    connect(connections, "Load agent registry", "Prepare decision context")
    connect(connections, "Prepare decision context", "Decision LLM")
    connect(connections, "Decision Chat Model — configure in UI", "Decision LLM", out="ai_languageModel", tin="ai_languageModel")
    connect(connections, "Decision Chat Model — configure in UI", "Decision Structured Output", out="ai_languageModel", si=0, tin="ai_languageModel")
    connect(connections, "Decision Structured Output", "Decision LLM", out="ai_outputParser", tin="ai_outputParser")
    connect(connections, "Decision LLM", "Parse decision")
    connect(connections, "Parse decision", "Update case after decision")
    connect(connections, "Update case after decision", "Expand decision events")
    connect(connections, "Expand decision events", "Insert decision events")
    connect(connections, "Insert decision events", "Restore parsed decision")
    connect(connections, "Restore parsed decision", "Prepare agent call")
    connect(connections, "Prepare agent call", "Action router")
    connect(connections, "Action router", "Call Excel Extractor", si=0)
    connect(connections, "Action router", "Call Calculation Agent", si=1)
    connect(connections, "Action router", "Call Schedule Builder", si=2)
    connect(connections, "Action router", "No agent this step", si=3)
    connect(connections, "Call Excel Extractor", "Merge agent result")
    connect(connections, "Call Calculation Agent", "Merge agent result")
    connect(connections, "Call Schedule Builder", "Merge agent result")
    connect(connections, "No agent this step", "Prepare Activity ack")
    connect(connections, "Merge agent result", "Update case after agent")
    connect(connections, "Update case after agent", "Expand agent events")
    connect(connections, "Expand agent events", "Insert agent events")
    connect(connections, "Insert agent events", "Restore after agent events")
    connect(connections, "Restore after agent events", "Continue loop?")
    connect(connections, "Continue loop?", "POST continue run", si=0)
    connect(connections, "Continue loop?", "Prepare Activity ack", si=1)
    connect(connections, "POST continue run", "Prepare Activity ack")
    connect(connections, "Prepare Activity ack", "Activity sync?", si=0)
    connect(connections, "Activity sync?", "POST step ack to MAS Activity", si=0)
    connect(connections, "Activity sync?", "Format step ack", si=1)
    connect(connections, "POST step ack to MAS Activity", "Format step ack")

    wf = {
        "id": WF_ID,
        "name": WF_NAME,
        "active": False,
        "isArchived": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "callerPolicy": "workflowsFromSameOwner",
            "errorWorkflow": ERROR_WF_ID,
        },
        "meta": {"templateCredsSetupCompleted": True, "targetN8nVersion": "2.30.8"},
        "tags": [],
        "pinData": {},
        "versionId": str(uuid.uuid4()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(nodes)} nodes)")


if __name__ == "__main__":
    main()
