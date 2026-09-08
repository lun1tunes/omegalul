#!/usr/bin/env python3
"""Generate the thin MAS orchestrator (one n8n execution = one step)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options, structured_parser_params
from generate_mas_runtime_config import runtime_config_execute_params
from mas_agent_registry import PLANNER_COLUMNS, list_agents_sql
from mas_retrieval_client import (
    SELECTORS,
    attach_orchestrator_rag_js,
    knowledge_retrieval_execute_params,
)
from mas_state_utils import STATE_SHAPE_JS

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
    "required": ["progress", "status_message", "action"],
    "properties": {
        # Progress ledger (Magentic-One style): the LLM must first judge completion from the journal.
        "progress": {
            "type": "object",
            "required": ["goal_satisfied", "evidence"],
            "properties": {
                "goal_satisfied": {"type": "boolean"},
                "evidence": {"type": "string"},
                "missing": {"type": "string"},
                "is_repeating": {"type": "boolean"},
            },
        },
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
                "rework_reason": {"type": "string"},
                "task": {"type": "object"},
                "question_id": {"type": "string"},
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "result": {
                    "type": "object",
                    "properties": {"summary_for_human": {"type": "string"}},
                },
            },
        },
    },
}

SYSTEM = """Ты оркестратор инженерной задачи. Предметной области ты не знаешь заранее: что умеют агенты, написано в реестре («Доступные агенты» в planner_input), а политика декомпозиции — в карточках знаний (срез target_base=orchestrator_routing, knowledge_type=routing_card). Другие срезы базы знаний сюда не подмешивают.

Как читать реестр:
- agent_id — значение для action.agent_id; title — так агента называют инженеру.
- when_to_use — что агент делает и когда его звать.
- input_required — роли файлов, которые ему нужны; сравнивай с ролями приложенных файлов (inputs[].role, files) в текущем состоянии.
- output_provides — какие данные и файлы он возвращает; в журнале они видны как «данные: …» и «артефакты: …».
- input_schema.data — данные, которые агент берёт из результатов других агентов; output_schema — что он отдаёт.
- hitl_policy=agent_asks — агент сам спросит инженера, если ему не хватит данных; не спрашивай за него.

Правила выбора агента:
- Вызывай только агентов из реестра и только тех, чьи input_required есть среди приложенных файлов.
- Если агенту нужны данные (input_schema.data), которые даёт другой агент (output_provides), а в журнале их ещё нет — сначала вызови того агента.
- Если карточки знаний недоступны (status=empty или unavailable) — решай по реестру и состоянию. Не спрашивай инженера про базу знаний.

Сначала заполни progress — оценку хода задачи по журналу (compact.journal.history: результаты агентов с summary, данными и добавленными артефактами, ответы человека):
- goal_satisfied: true, только если результаты в журнале покрывают цель целиком. Если ты выбираешь call_agent — цель ещё не достигнута: goal_satisfied=false, в missing напиши, чего не хватает.
- evidence: что именно в журнале это подтверждает (или чего не хватает).
- missing: что ещё нужно сделать (пусто, если цель достигнута).
- is_repeating: true, если ты собираешься повторить агенту задание, которое он уже выполнил (status completed) без новых данных.

Затем выбери одно действие:
1. call_agent
2. ask_user
3. finish

Завершение:
- Если goal_satisfied — только finish. В action.result.summary_for_human напиши по-русски, что фактически сделано, опираясь на summary агентов из журнала (не на шаблон «вызвал агента»).
- summary_for_human и question читает инженер, не программа: обычные русские фразы без технических идентификаторов — ни agent_id, ни имён артефактов из журнала, ни ключей JSON. Агентов называй по title из реестра, результат — файлом («новый schedule.inc»), скважины и даты — как в summary агентов.
- Агент, который вернул completed, свою часть сделал: его результат уже в артефактах. Не вызывай его снова «для проверки» или «чтобы применить ещё раз».
- Повторный call_agent того же агента допустим только если появились новые данные (ответ человека, новые файлы) или ты нашёл конкретный недостаток в его результате — тогда обязательно заполни action.rework_reason и опиши недостаток в handoff_message.
- Если человек в журнале принял результат (review_accept) — finish.
- Если агент вернул needs_input (задал вопрос) и человек ответил — верни задачу этому же агенту (call_agent) и передай ответ в handoff_message. Пока этот агент не вернул completed, цель не достигнута и finish невозможен.
- finish проверяется отдельно: каждая часть цели должна быть покрыта записью журнала со статусом completed. Если в журнале есть «проверка завершения отклонила finish — не покрыто: …», цель не достигнута: закрой именно эти части через call_agent. Не повторяй finish без новых результатов агентов.

Правила:
- Если не хватает данных, которых нет ни в приложенных файлах, ни в результатах агентов и ни один агент их не даст — выбери ask_user. Не придумывай факты (даты, имена объектов, параметры) и не пиши содержимое файлов сам.
- Имена приложенных файлов — это состав пакета, а не постановка задачи: делай только то, о чём просит цель, и не дописывай в handoff_message работу, которую инженер не просил.
- Если следующий шаг очевиден, выбери call_agent.
- Не разбирай содержимое файлов сам. Retrieval уже выполнен до тебя (только срез orchestrator_routing).
- Возвращай только JSON.
- status_message пиши по-русски, коротко, в стиле текущего шага.
- handoff_message — человекопонятное обращение к агенту (по title): что сделать, на основании каких файлов и данных, что уже известно из журнала (ответы инженера, результаты других агентов).
"""

# Completion check: a second, sceptical LLM pass that runs only when the Decision LLM proposes
# `finish`. It decomposes the goal into deliverables and demands a completed journal entry for
# each one. Domain-free: it knows nothing about Excel or SCHEDULE, only goal vs. journal.
VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["goal_parts", "all_covered", "verdict_for_human"],
    "properties": {
        "goal_parts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["part", "covered", "evidence"],
                "properties": {
                    "part": {"type": "string"},
                    "covered": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "all_covered": {"type": "boolean"},
        "verdict_for_human": {"type": "string"},
    },
}

VERIFY_SYSTEM = """Ты проверяющий завершения инженерной задачи. Оркестратор предлагает завершить задачу; твоя работа — проверить это по журналу, а не поверить на слово.

Тебе даны: цель (текст инженера), журнал (что агенты фактически сделали — их итоги со статусом и добавленные артефакты, ответы инженера) и предложенный итог.

1. Разбей цель на части — результаты, которые инженер должен получить на выходе (обычно 1–3). Часть — это результат («получен обновлённый файл X»), не действие («вызвать агента»). Файлы, которые инженер приложил сам (шаг 0 журнала), — исходные данные, а не результат: «получить/использовать исходный файл» не выделяй в отдельную часть.
2. Для каждой части решай, покрыта ли она записью журнала со статусом completed, чей итог по смыслу говорит, что это сделано. Промежуточный шаг не покрывает конечный результат: извлечь данные ≠ построить на их основе итог; «исходный файл есть во вложении» ≠ «получен новый файл». Но не придирайся к формулировкам: если агент отчитался о сделанных изменениях (сдвинул даты, добавил/убрал скважины, перепривязал группы) и/или в записи есть добавленные артефакты (новый файл, diff), результат получен. evidence — пересказ записи журнала (шаг N) либо «в журнале нет».
3. unsupported_claims — утверждения предложенного итога, которых нет в журнале (например «файл собран», когда ни один агент об этом не отчитался).
4. all_covered = true только если покрыты все части — и обязательно true, если все части covered=true. Не выдумывай записей журнала и не додумывай, что «наверняка сделано».
5. verdict_for_human — одна русская фраза для инженера без идентификаторов и имён полей: что сделано и чего не хватает.

Возвращай только JSON.
"""

PREPARE_VERIFY = r"""
const ctx=$('Prepare decision context').first().json||{};
const llm=$('Decision LLM').first().json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
const decision=parse(llm.output||llm.text||llm);
const action=obj(decision.action)?decision.action:{};
const proposed=String((obj(action.result)&&action.result.summary_for_human)||'').trim();
const progress=obj(decision.progress)?decision.progress:{};
/* Same goal + journal the Decision LLM saw (planner_input starts with them), plus its proposal. */
const planner=String(ctx.planner_input||'');
const cut=planner.indexOf('\nТекущее состояние:');
const goalAndJournal=cut>0?planner.slice(0,cut):planner;
const verify_input=`${goalAndJournal}\n\nПредложенный итог оркестратора:\n${proposed||'(итог не написан)'}\n\nОбоснование оркестратора:\n${String(progress.evidence||'').slice(0,600)||'(нет)'}\n`;
return [{json:{...ctx, verify_input, proposed_summary:proposed}}];
"""

INTERPRET_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["decision", "confidence", "paraphrase"],
    "properties": {
        "decision": {"type": "string"},
        "confidence": {"type": "number"},
        "paraphrase": {"type": "string"},
    },
}

INTERPRET_SYSTEM = """Ты понимаешь свободный ответ инженера на вопрос, у которого уже есть варианты.

Тебе даны: текст вопроса, варианты (как их видит инженер) и его слова.

Верни JSON:
- decision — значение выбранного варианта (то, что стоит за кнопкой: keep, remove, accept, rework или иное value из списка). Не придумывай вариант, которого нет.
- confidence — число от 0 до 1. Если ответ двусмысленный, двусмысленно-вежливый или не про этот вопрос — ниже 0.8.
- paraphrase — одна русская фраза, что инженер имел в виду, без идентификаторов и без JSON.

Если не уверен — decision оставь пустым или unclear, confidence ниже 0.8. Не угадывай деструктив (убрать, удалить), если инженер этого явно не сказал.
"""

APPLY_INTERPRETED = STATE_SHAPE_JS + r"""
const prev=$('Apply request extras').first().json||{};
const llm=$('Interpret free-text answer').first().json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
const decision=parse(llm.output||llm.text||llm);
let state=sanitizeState(prev.state);
const qid=String(prev.interpret_qid||'Q-1');
const q0=obj(prev.interpret_question)?prev.interpret_question:{};
const raw=prev.interpret_raw;
const applied=applyInterpretedDecision(qid,q0,raw,decision);
const hitl=state.hitl&&typeof state.hitl==='object'?{...state.hitl}:{pending:false,questions:[],answers:{}};
const questions=Array.isArray(hitl.questions)?hitl.questions:[];
const answers={...(hitl.answers&&typeof hitl.answers==='object'?hitl.answers:{})};
if(!applied.accepted){
  const q=buildClarifyQuestion(q0);
  state.hitl={pending:true,questions:[q],answers};
  state.status='waiting_user';
  state.version=Number(state.version||0)+1;
  const persistEvents=[[prev.case_id,'','hitl.request','orchestrator','','waiting_user',q.question,'',JSON.stringify({...q,...execRef()})]];
  return [{json:{
    ...prev,
    state,
    did_resume:true,
    needs_interpret:false,
    next_status:'waiting_user',
    should_continue:false,
    update_sql_parameters:[JSON.stringify(state),'waiting_user',prev.case_id],
    persist_events:persistEvents,
    event_sql_parameters:persistEvents[0],
    human_gate:{gate_id:q.question_id,kind:q.kind,reason:q.question,expected_version:state.version,questions:[q]}
  }}];
}
answers[qid]=applied.answer;
state.hitl={pending:false,questions,answers};
state.status='running';
state.version=Number(state.version||0)+1;
const reviewAccept=isResultReviewGate(qid)&&humanAnswerChoice(applied.answer)==='accept';
ledgerPush(state,{kind:'human',step:Number(state.step_count||0),question_id:qid,question:String(q0.question||'').slice(0,200),answer:humanAnswerText(applied.answer).slice(0,300),...(reviewAccept?{review_accept:true}:{})});
state.ledger.last_human_step=Number(state.step_count||0);
state.ledger.stall_count=0;
const said=humanAnswerText(applied.answer);
const persistEvents=[[prev.case_id,'','hitl.answered','user','','answered',said?('Пользователь ответил: '+said):'Пользователь ответил','',JSON.stringify({question_id:qid,answer:applied.answer,...execRef()})]];
return [{json:{
  ...prev,
  state,
  did_resume:true,
  needs_interpret:false,
  next_status:'running',
  update_sql_parameters:[JSON.stringify(state),'running',prev.case_id],
  persist_events:persistEvents,
  event_sql_parameters:persistEvents[0]
}}];
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
                ]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body,
            "options": {"timeout": timeout, "response": {"response": {"fullResponse": False}}},
        },
        onError="continueRegularOutput",
    )


def execute_workflow_by_expression(name, pos, workflow_id_expr, inputs):
    """executeWorkflow whose target is decided at runtime (Phase 2: id comes from ``agent_registry.invoke``).

    Verified on n8n 2.30.8: ``workflowId`` accepts ``{"__rl": True, "mode": "id", "value": "={{ … }}"}``;
    an unknown id fails the node cleanly (caught by ``onError=continueRegularOutput`` → Merge records a
    failed agent result). No ``cachedResultName`` — nothing to bind in the UI.
    """
    return node(
        name,
        "n8n-nodes-base.executeWorkflow",
        1.3,
        pos,
        {
            "source": "database",
            "workflowId": {"__rl": True, "value": workflow_id_expr, "mode": "id"},
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
const cfg=(()=>{try{return $('Runtime endpoints').first().json||{}}catch{return {}}})();
const raw=(()=>{try{return $('Authenticated MAS webhook').first().json||{}}catch{return $input.first().json||{}}})();
const body=raw.body&&typeof raw.body==='object'?raw.body:raw;
const action=String(body.action||raw.action||'step').trim().toLowerCase();
let caseId=String(body.case_id||raw.case_id||'').trim();
const goal=String(body.task_description||body.goal||raw.task_description||raw.goal||'').trim();
const taskName=String(body.task_name||raw.task_name||'').trim();
const hrRaw=body.human_response!=null?body.human_response:(raw.human_response!=null?raw.human_response:'');
const humanResponse=typeof hrRaw==='string'?hrRaw:(hrRaw==null?'':JSON.stringify(hrRaw));
const gateId=String(body.gate_id||raw.gate_id||'');
const expectedVersion=body.expected_version!=null?Number(body.expected_version):(raw.expected_version!=null?Number(raw.expected_version):null);
const requestedBy=String(body.requested_by||raw.requested_by||'');
const resumeSource=String(body.source||raw.source||(action==='resume'?'human':'')).trim().toLowerCase();
const resumeTaskId=String(body.task_id||raw.task_id||'');
const resumeAgentId=String(body.agent_id||raw.agent_id||'');
const activityBaseUrl=String(cfg.activity_base_url||raw.activity_base_url||'').trim();
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
  expected_version:Number.isFinite(expectedVersion)?expectedVersion:null,
  requested_by:requestedBy,
  source:resumeSource,
  task_id:resumeTaskId,
  agent_id:resumeAgentId,
  activity_base_url:activityBaseUrl,
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
  error_count:0,
  step_count:0,
  version:1
};
/* `case.created` is written by Activity (`POST /cases` initial_event) before it calls action=create;
   the orchestrator must not add a second one (CASE-6a9ee318: two «Принял задачу» rows). */
return [{json:{
  ...req,
  state,
  create_sql_parameters:[req.case_id, JSON.stringify(state), 'running'],
  persist_events:[],
  event_sql_parameters:[]
}}];
"""

APPLY_EXTRAS = STATE_SHAPE_JS + r"""
const req=$('Normalize step request').first().json||{};
const load=$json||{};
const parse=v=>{if(v&&typeof v==='object'&&!Array.isArray(v))return v;try{const p=JSON.parse(String(v||'{}'));return p&&typeof p==='object'&&!Array.isArray(p)?p:{}}catch{return {}}};
let state=parse(load.state||load.State||{});
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
      kind:String(q0.kind||'needs_input'),
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
    deliverables:deliverables(state.artifacts)
  }}];
}
if(req.is_resume===true){
  state=sanitizeState(state);
  const expected=Number(req.expected_version);
  const current=Number(state.version||0);
  if(Number.isFinite(expected)&&expected>0&&current>0&&expected!==current){
    const hitl=state.hitl&&typeof state.hitl==='object'?state.hitl:{};
    const questions=Array.isArray(hitl.questions)?hitl.questions:[];
    return [{json:{
      ...req,
      state,
      did_resume:false,
      is_status:false,
      next_status:String(state.status||'waiting_user'),
      status:String(state.status||'waiting_user'),
      action_type:'version_mismatch',
      should_continue:false,
      message:`Version mismatch: expected ${expected}, got ${current}. Reload status.`,
      version:current,
      human_gate:questions.length?{
        gate_id:(questions[0]&&questions[0].question_id)||'hitl',
        kind:String((questions[0]&&questions[0].kind)||'needs_input'),
        reason:questions[0]&&questions[0].question||state.goal||'Нужен ответ',
        expected_version:current,
        questions
      }:null
    }}];
  }
  const source=String(req.source||'human').toLowerCase();
  if(source==='agent'||source==='system'){
    const taskId=String(req.task_id||'');
    const slot=state.agents&&state.agents[String(req.agent_id||'')];
    const waiting=state.current_task&&typeof state.current_task==='object'?state.current_task:{};
    const agentId=String(req.agent_id||waiting.agent_id||'');
    const payload=parseJsonish(String(req.human_response||'').trim(),null);
    const events=[];
    let nextStatus='running';
    if(source==='agent'&&payload&&typeof payload.status==='string'){
      /* A long agent finished (or asked / failed) after returning in_progress: same merge as a synchronous result. */
      const applied=applyAgentResult(state, agentId, taskId||String(waiting.task_id||(slot&&slot.task_id)||''), payload, Array.isArray(req.registry)?req.registry:[]);
      nextStatus=applied.next_status;
      events.push(...applied.events);
    } else {
      const summary=humanAnswerText(decodeHitlAnswer(String(req.human_response||'').trim()))||'событие';
      ledgerPush(state,{kind:'external',source,task_id:taskId,agent_id:agentId,step:Number(state.step_count||0),summary:String(summary).slice(0,300)});
    }
    state.status=nextStatus;
    state.version=Number(state.version||0)+1;
    state.ledger.last_human_step=Number(state.step_count||0);
    state.ledger.stall_count=0;
    const persistEvents=[[req.case_id,taskId,'orchestrator.resume',source,agentId,nextStatus,'Продолжение по событию агента или системы','',JSON.stringify({source,task_id:taskId})],
      ...events.map(e=>[req.case_id,e.task_id||'',e.kind,e.actor,e.agent_id||'',e.status||'',e.status_message||'',e.handoff_message||'',JSON.stringify(e.payload||{})])];
    return [{json:{
      ...req,
      state,
      did_resume:true,
      needs_interpret:false,
      is_status:false,
      next_status:nextStatus,
      update_sql_parameters:[JSON.stringify(state),nextStatus,req.case_id],
      persist_events:persistEvents,
      event_sql_parameters:persistEvents[0]
    }}];
  }
  const answer=decodeHitlAnswer(String(req.human_response||'').trim());
  const hitl=state.hitl&&typeof state.hitl==='object'?state.hitl:{pending:false,questions:[],answers:{}};
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const qid=String(req.gate_id||(questions[0]&&questions[0].question_id)||'Q-1');
  const q0=questions.find(q=>q&&String(q.question_id||'')===qid)||questions[0]||{};
  const stored=normalizeHitlAnswer(qid, answer, q0.question);
  const choice=humanAnswerChoice(stored);
  const opts=optionValues(q0);
  const needsInterpret=!choice&&opts.length>0&&Boolean(humanAnswerText(stored).trim());
  if(needsInterpret){
    const optionLines=(Array.isArray(q0.options)?q0.options:[]).map(o=>{
      if(o&&typeof o==='object'&&!Array.isArray(o)) return `- ${o.label||o.value||''}`;
      return `- ${o}`;
    }).join('\n');
    const interpret_input=`Вопрос инженеру:\n${String(q0.question||'')}\n\nВарианты:\n${optionLines}\n\nОтвет инженера:\n${humanAnswerText(stored)}\n`;
    return [{json:{
      ...req,
      state,
      did_resume:true,
      needs_interpret:true,
      is_status:false,
      next_status:String(state.status||'waiting_user'),
      interpret_qid:qid,
      interpret_raw:stored,
      interpret_question:q0,
      interpret_input,
      should_continue:false
    }}];
  }
  const answers={...(hitl.answers&&typeof hitl.answers==='object'?hitl.answers:{})};
  answers[qid]=stored;
  state.hitl={pending:false,questions,answers};
  state.status='running';
  state.version=Number(state.version||0)+1;
  const reviewAccept=isResultReviewGate(qid)&&humanAnswerChoice(stored)==='accept';
  ledgerPush(state,{kind:'human',step:Number(state.step_count||0),question_id:qid,question:String(q0.question||'').slice(0,200),answer:humanAnswerText(stored).slice(0,300),...(reviewAccept?{review_accept:true}:{})});
  state.ledger.last_human_step=Number(state.step_count||0);
  state.ledger.stall_count=0;
  const said=humanAnswerText(stored);
  const persistEvents=[[req.case_id,'','hitl.answered','user','','answered',said?('Пользователь ответил: '+said):'Пользователь ответил','',JSON.stringify({question_id:qid,answer:stored,...execRef()})]];
  return [{json:{
    ...req,
    state,
    did_resume:true,
    needs_interpret:false,
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

_ORCH_SEL = SELECTORS["orchestrator"]
PREPARE_DECISION = STATE_SHAPE_JS + (
    "const ORCH_RAG_TARGET_BASE=" + json.dumps(_ORCH_SEL["target_base"]) + ";\n"
    "const ORCH_RAG_ACCESS_SCOPE=" + json.dumps(_ORCH_SEL["access_scope"]) + ";\n"
    "const ORCH_RAG_KNOWLEDGE_TYPES=" + json.dumps(_ORCH_SEL["knowledge_types"]) + ";\n"
    "const ORCH_RAG_TOP_K=" + str(int(_ORCH_SEL["top_k"])) + ";\n"
    "const PLANNER_COLUMNS=" + json.dumps(list(PLANNER_COLUMNS)) + ";\n"
    + r"""
const req=$('Normalize step request').first().json||{};
const interpreted=(()=>{try{return $('Apply interpreted answer').first().json}catch{return null}})();
const extras=(()=>{try{return $('Apply request extras').first().json}catch{return null}})();
const load=$('Load case').first().json||{};
const parse=v=>{if(v&&typeof v==='object')return v;try{return JSON.parse(String(v||'{}'))}catch{return {}}};
const fromResume=(interpreted&&interpreted.state)?interpreted:extras;
const rawState=(fromResume&&fromResume.state&&typeof fromResume.state==='object')?fromResume.state:parse(load.state||load.State||{});
const state=sanitizeState(rawState);
const status=String((fromResume&&fromResume.next_status)||load.status||state.status||'running');
/* Phase 2: the registry is the only source of "who can do what". Rows come from Postgres (enabled=true);
   the Decision LLM sees PLANNER_COLUMNS only — `invoke` is a deployment detail resolved in Prepare agent call. */
const registry=$('Load agent registry').all().map(i=>i.json||{}).filter(r=>r&&r.agent_id);
if(!registry.length) throw new Error('agent_registry has no enabled agents — seed it via Control Plane Proxy (upsert_agent) before running cases');
const plannerRegistry=registry.map(r=>{
  const o={};
  for(const c of PLANNER_COLUMNS){ if(r[c]!==undefined&&r[c]!==null) o[c]=parseJsonish(r[c],r[c]); }
  return o;
});
const compact=buildCompact(state);
/* No rule-based routing hint here: the Decision LLM reasons from goal + journal + state + registry + RAG policy cards. */
const journalLines=(compact.journal&&Array.isArray(compact.journal.history)?compact.journal.history:[]).map(e=>{
  if(e.kind==='human') return `- шаг ${e.step}: человек ответил${e.review_accept?' (принял результат)':''}: «${e.answer}»${e.question?` — на вопрос «${e.question}»`:''}`;
  if(e.kind==='verification') return `- шаг ${e.step}: проверка завершения отклонила finish — не покрыто: ${(e.uncovered||[]).join('; ')||'(не указано)'}`;
  const data=(e.data_keys||[]).length?`; данные: ${e.data_keys.join(', ')}`:'';
  const arts=(e.artifacts_added||[]).length?`; артефакты: ${e.artifacts_added.join(', ')}`:'';
  return `- шаг ${e.step}: ${e.agent_id} → ${e.status}: ${e.summary||'(без описания)'}${data}${arts}${e.rework_reason?` (доработка: ${e.rework_reason})`:''}`;
});
/* Step 0 of the journal is what the engineer handed in (artifact cards with kind=input). Without it the completion
   check has no evidence that the source files exist and may invent an uncoverable "get the source file" part
   (CASE-6a9ec5ef-905bb0). Filenames only — no roles, no domain words. */
const inputCards=artifactCards(state.artifacts||{}).filter(c=>c.kind==='input');
const inputNames=inputCards.map(c=>String(c.filename||c.artifact_id||'').trim()).filter(Boolean);
const inputsLine=inputNames.length?`- шаг 0: инженер приложил ${inputNames.length} файл(ов): ${inputNames.slice(0,6).join(', ')}${inputNames.length>6?` и ещё ${inputNames.length-6}`:''}`:'';
const journalText=[inputsLine,...journalLines].filter(Boolean).join('\n')||'- пока ничего не сделано';
const prompt=`Цель:\n${compact.goal}\n\nЖурнал задачи (что уже сделано, по шагам):\n${journalText}\n\nТекущее состояние:\n${JSON.stringify(compact,null,2)}\n\nДоступные агенты (реестр):\n${JSON.stringify(plannerRegistry,null,2)}\n`;
const retrieval_selector={target_base:ORCH_RAG_TARGET_BASE,knowledge_types:ORCH_RAG_KNOWLEDGE_TYPES};
/* Plain-text query: goal + inputs + agent summaries. Selectors (target_base / knowledge_types) do the filtering;
   no regex-derived topics or keyword families (Phase 2). */
const schedule_retrieval_request={
  query:buildRetrievalQuery(compact),
  filters:{
    target_base:ORCH_RAG_TARGET_BASE,
    access_scope:ORCH_RAG_ACCESS_SCOPE,
    knowledge_types:ORCH_RAG_KNOWLEDGE_TYPES
  },
  top_k:ORCH_RAG_TOP_K
};
const endpoints=$('Runtime endpoints').first().json||{};
return [{json:{
  ...req,
  ...endpoints,
  activity_base_url:String(endpoints.activity_base_url||req.activity_base_url||'http://mas-activity:8200').replace(/\/$/,''),
  orchestrator_step_url:String(endpoints.orchestrator_step_url||req.orchestrator_step_url||'http://127.0.0.1:5678/webhook/mas-orchestrator-step').replace(/\/$/,''),
  case_id:req.case_id,
  state,
  status,
  registry,
  compact,
  planner_input:prompt,
  schedule_retrieval_request,
  retrieval_selector,
  step_count:compact.step_count
}}];
"""
)

PARSE_DECISION = STATE_SHAPE_JS + r"""
const prev=$('Prepare decision context').first().json||{};
/* Input arrives either straight from Decision LLM or via the completion check (Verify completion). */
const raw=(()=>{try{return $('Decision LLM').first().json||$json}catch{return $json}})();
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
const out=parse(raw.output||raw.text||raw);
const decision=obj(out.action)?out:(obj(raw)?raw:{});
const verification=(()=>{
  try{
    const v=$('Verify completion').first().json||{};
    const p=parse(v.output||v.text||v);
    /* The per-part judgements are the reasoning; the summary flag is derived from them (the model
       can contradict itself: every part covered:true yet all_covered:false). No parts listed → flag.
       Neither a usable part nor a boolean flag → no verification (null), never a verdict from nothing.
       Deliberately lenient (either field suffices): a partial reply with an uncovered part must still
       reject — discarding it would let the finish through unchecked. */
    const parts=(Array.isArray(p.goal_parts)?p.goal_parts:[]).filter(x=>obj(x)&&String(x.part||'').trim());
    const hasFlag=typeof p.all_covered==='boolean';
    if(!hasFlag&&!parts.length) return null;
    const uncovered=parts.filter(x=>x.covered!==true).map(x=>String(x.part||'').trim()).slice(0,6);
    const allCovered=parts.length?uncovered.length===0:p.all_covered===true;
    return {...p,goal_parts:parts,uncovered,all_covered:allCovered,
      unsupported_claims:(Array.isArray(p.unsupported_claims)?p.unsupported_claims:[]).map(s=>String(s||'').trim()).filter(Boolean).slice(0,6)};
  }catch{return null}
})();
let action=obj(decision.action)?{...decision.action}:{type:'ask_user',question_id:'Q-parse',question:'Не удалось разобрать решение оркестратора',options:[]};
let type=String(action.type||'').trim();
const progress=obj(decision.progress)?decision.progress:{};
const registry=Array.isArray(prev.registry)?prev.registry:[];
const state=sanitizeState(obj(prev.state)?{...prev.state}:{});
state.step_count=Number(state.step_count||0)+1;
state.version=Number(state.version||0)+1;
/* --- Completion guards (deterministic safety around the LLM decision; no domain knowledge) ---
   1. The human accepted the result in a review gate → the case is finished, whatever the LLM picked.
   2. The LLM says goal_satisfied yet re-delegates to an agent that already completed (habit) → finish.
      A first delegation with a wrong goal_satisfied flag is the opposite case: the action is the
      intent, the flag is the slip — the flag is ignored and the agent is called.
   3. Re-delegating to an agent that already returned completed, with no new human input since:
      allowed once with an explicit rework_reason; otherwise → result review with the human. */
let guard=null;
let reworkReason='';
const answered=ledgerAnsweredAgentQuestion(state);
const repeatDelegation=(agentId)=>{
  const last=ledgerLastAgentEntry(state, String(agentId||'').trim());
  return Boolean(last)&&String(last.status||'')==='completed'&&!ledgerHasNewInputsSince(state, last);
};
if(type==='finish'&&answered){
  /* 0. An agent asked, the human answered, the agent has not run since → the answer must reach the
        agent that asked. Finishing here would silently drop the engineer's decision. */
  guard='answer_not_applied';
  type='call_agent';
  action={
    type:'call_agent',
    agent_id:answered.agent_id,
    handoff_message:`Инженер ответил на ваш вопрос («${answered.question.slice(0,160)}»): «${answered.answer.slice(0,200)}». Продолжите задачу с учётом этого ответа.`,
    task:{objective:String(state.goal||'')}
  };
} else if(type!=='finish'&&ledgerHumanAccepted(state)){
  guard='human_accepted';
  type='finish';
  action={type:'finish',result:{summary_for_human:String((obj(action.result)&&action.result.summary_for_human)||'')}};
} else if(type==='finish'&&verification&&verification.all_covered===false&&!ledgerHumanAccepted(state)){
  /* 4. Verified completion: the LLM proposed finish, the completion check found parts of the goal
        without a completed journal entry. First time — one more step with the gaps in the journal
        (the Decision LLM must close them); second time — the engineer decides. */
  const uncovered=verification.uncovered||[];
  const unsupported=verification.unsupported_claims||[];
  const verdict=String(verification.verdict_for_human||'').trim();
  const rejections=Number(state.ledger.verify_rejections||0);
  ledgerPush(state,{kind:'verification',verdict:'rejected',uncovered,unsupported,proposed:String((obj(action.result)&&action.result.summary_for_human)||'').slice(0,300)});
  state.ledger.verify_rejections=rejections+1;
  if(rejections<1){
    guard='completion_unverified';
    type='continue';
    action={type:'continue',uncovered,verdict};
  } else {
    guard='completion_review';
    state.ledger.reviews=Number(state.ledger.reviews||0)+1;
    const review=buildCompletionReviewQuestion(state, registry, uncovered, verdict);
    type='ask_user';
    action={type:'ask_user',...review};
  }
} else if(type==='call_agent'&&progress.goal_satisfied===true&&!answered&&repeatDelegation(action.agent_id)){
  guard='goal_satisfied';
  type='finish';
  action={type:'finish',result:{summary_for_human:String(progress.evidence||'')}};
} else if(type==='call_agent'){
  const agentId=String(action.agent_id||'').trim();
  const repeat=repeatDelegation(agentId);
  if(progress.goal_satisfied===true&&!repeat) guard='goal_flag_ignored';
  if(repeat){
    reworkReason=String(action.rework_reason||'').trim();
    const stall=Number(state.ledger.stall_count||0);
    if(reworkReason&&stall<1){
      guard='rework_allowed';
      state.ledger.stall_count=stall+1;
    } else {
      guard=reworkReason?'stall_review':'repeat_review';
      state.ledger.stall_count=stall+1;
      state.ledger.reviews=Number(state.ledger.reviews||0)+1;
      const review=buildResultReviewQuestion(state, agentId, registry, 'repeat');
      type='ask_user';
      action={type:'ask_user',...review};
    }
  }
}
if(Array.isArray(decision.plan_update)){
  const by=new Map((Array.isArray(state.plan)?state.plan:[]).map(p=>[p.id,p]));
  for(const item of decision.plan_update){if(item&&item.id) by.set(item.id,{...(by.get(item.id)||{}),...item});}
  state.plan=[...by.values()];
}
let statusMessage=String(decision.status_message||'').trim()||'Шаг оркестратора';
if(guard==='repeat_review'||guard==='stall_review') statusMessage='Результат уже получен — прошу инженера принять его или описать доработку.';
else if(guard==='answer_not_applied') statusMessage=`Передаю ответ инженера агенту ${agentTitle(action.agent_id, registry)}.`;
else if(guard==='human_accepted') statusMessage='Инженер принял результат — завершаю задачу.';
else if(guard==='completion_unverified'){
  const gaps=(action.uncovered||[]).join('; ');
  const verdict=String(action.verdict||'').trim();
  statusMessage=(verdict&&!looksMachineText(verdict)?verdict.replace(/\.?$/,'. '):'Проверка завершения: задача ещё не выполнена целиком. ')+(gaps?`Не хватает: ${gaps}. `:'')+'Продолжаю работу.';
}
else if(guard==='completion_review') statusMessage='Проверка завершения снова нашла пробел — прошу инженера принять результат или уточнить, что нужно получить.';
/* Developer log: the LLM's decision as it came (before guards), bounded — this is what a developer
   compares with the guard/verification fields when a step went wrong. */
const decisionRaw=boundedForLog({...decision,action:obj(decision.action)?decision.action:null},4000);
const decisionEvent={
  kind:'orchestrator.decision',
  actor:'orchestrator',
  status_message:statusMessage,
  payload:{action_type:type,agent_id:action.agent_id||null,step_count:state.step_count,version:state.version,progress:{goal_satisfied:progress.goal_satisfied===true,evidence:String(progress.evidence||'').slice(0,300),missing:String(progress.missing||'').slice(0,300)},...(guard?{guard}:{}),...(verification?{verification:{all_covered:verification.all_covered===true,goal_parts:(Array.isArray(verification.goal_parts)?verification.goal_parts:[]).slice(0,6).map(p=>({part:String((p&&p.part)||'').slice(0,160),covered:Boolean(p&&p.covered)}))}}:{}),decision:decisionRaw,...execRef()}
};
const events=[];
let nextStatus='running';
let agentTask=null;
if(type==='call_agent'){
  const taskId=String(action.task_id||`TASK-${state.step_count}`).trim();
  const flatArts=flattenArtifacts(state.artifacts||{});
  const artifactIds=Object.keys(flatArts).filter(k=>k!=='diff');
  const hitlState=state.hitl&&typeof state.hitl==='object'?state.hitl:{};
  const hitlAnswers=hitlState.answers&&typeof hitlState.answers==='object'?hitlState.answers:{};
  const unlistedPolicy=readUnlistedWellsPolicy(hitlAnswers)||'';
  /* inputs are references owned by the orchestrator (artifact ids, data buckets, HITL policy). The LLM talks to
     the agent through handoff_message only: anything it puts into action.task (e.g. its own `facts` with dates)
     is a paraphrase, and an agent that trusted it would override the deterministic result of a previous agent
     (CASE-6a9ec6b3-74e34e: invented per-well dates replaced data.excel.facts). */
  agentTask={
    case_id:prev.case_id,
    task_id:taskId,
    agent_id:String(action.agent_id||'').trim(),
    objective:String(action.task&&action.task.objective||state.goal||''),
    handoff_message:String(action.handoff_message||''),
    inputs:{
      activity_base_url:String(prev.activity_base_url||'http://mas-activity:8200').replace(/\/$/,''),
      schedule_root:state.schedule_root||'',
      artifact_ids:artifactIds,
      /* Phase 2: results of earlier agents live in state.agents[<agent_id>].data; the callee reads them from
         GET /cases/{id}/state. data_refs lists which agents already produced data (not domain buckets). */
      data_refs:Object.keys(state.agents||{}),
      ...(unlistedPolicy?{unlisted_wells_policy:unlistedPolicy}:{}),
      ...(reworkReason?{rework_reason:reworkReason}:{})
    },
    context:{hitl:{pending:Boolean(hitlState.pending),answer_ids:Object.keys(hitlAnswers),answers:hitlAnswers}},
    constraints:{units:'METRIC'}
  };
  state.current_task=slimCurrentTask({task_id:taskId,agent_id:agentTask.agent_id},state.artifacts,state.agents);
  events.push(decisionEvent, {
    kind:'agent.handoff',
    actor:'orchestrator',
    agent_id:agentTask.agent_id,
    task_id:taskId,
    status_message:statusMessage,
    handoff_message:agentTask.handoff_message,
    /* Developer log: the exact agent_task the agent received (inputs are references, so it is small). */
    payload:{task_id:taskId,agent_task:boundedForLog(agentTask,6000),...execRef()}
  });
} else if(type==='ask_user'){
  nextStatus='waiting_user';
  const q={question_id:String(action.question_id||`Q-${state.step_count}`),question:String(action.question||'Нужно уточнение'),options:Array.isArray(action.options)?action.options:[],...(action.kind?{kind:action.kind}:{})};
  state.hitl={pending:true,questions:[q],answers:(state.hitl&&state.hitl.answers)||{}};
  state.current_task=null;
  events.push(decisionEvent, {kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:statusMessage,payload:{...q,...execRef()}});
} else if(type==='continue'){
  /* Completion check rejected the finish: no agent, no question — the next step re-decides with the
     gaps written into the journal. The loop is continued by "No agent this step" → "Continue loop?". */
  nextStatus='running';
  state.current_task=null;
  events.push(decisionEvent, {kind:'orchestrator.status',actor:'orchestrator',status:'running',status_message:statusMessage,payload:{action_type:'continue',uncovered:action.uncovered||[],...execRef()}});
} else if(type==='finish'){
  nextStatus='done';
  state.current_task=null;
  /* Honest completion: what agents actually did (their summaries), not "called N agents". */
  const llmSummary=String((obj(action.result)&&action.result.summary_for_human)||'').trim();
  const journalSummary=composeDoneSummary(state, registry);
  /* The engineer reads this line: if the LLM leaked ids (agent ids, artifact ids, JSON) or the
     completion check found claims the journal does not support, prefer the journal. */
  const unsupported=verification&&Array.isArray(verification.unsupported_claims)&&verification.unsupported_claims.length>0;
  const summary=(llmSummary&&!looksMachineText(llmSummary)&&!(unsupported&&journalSummary))?llmSummary:(journalSummary||llmSummary||statusMessage);
  /* The case result is the set of deliverables agents produced (cards with producer), not one file. */
  const result={...(obj(action.result)?action.result:{}),summary_for_human:summary,done_by_agents:journalSummary,deliverables:deliverables(state.artifacts),...(guard?{guard}:{}),...(verification?{completion_verified:verification.all_covered===true}:{})};
  state.data={...(state.data||{}),result};
  statusMessage=summary;
  events.push({kind:'case.finished',actor:'orchestrator',status:'done',status_message:summary,payload:{...result,action_type:'finish',...execRef()}});
} else {
  nextStatus='waiting_user';
  const q={question_id:'Q-unknown',question:'Оркестратор вернул неизвестное действие',options:[]};
  state.hitl={pending:true,questions:[q],answers:{}};
  events.push({kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:statusMessage,payload:{...q,...execRef()}});
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
const fromInterp=(()=>{try{return $('Apply interpreted answer').first().json}catch{return null}})();
const fromExtras=(()=>{try{return $('Apply request extras').first().json}catch{return null}})();
const withEvents=v=>v&&Array.isArray(v.persist_events)&&v.persist_events.length?v:null;
/* Resume events (hitl.answered / hitl.request re-ask / orchestrator.resume) come from Apply interpreted answer or
   Apply request extras. `case.created` is Activity's — Prepare start case is deliberately not a source here. */
const prev=fromMerge||fromParse||withEvents(fromInterp)||withEvents(fromExtras)||$json||{};
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

ROUTE = STATE_SHAPE_JS + r"""
/* Phase 2: no agent names here. The registry row (`invoke`) says how the agent is reached:
   n8n sub-workflow by id (executeWorkflow with expression workflowId) or HTTP POST. Runtime Config may
   override workflow ids per agent (agent_workflow_ids JSON) — field engineers re-point without regen. */
const x=$json;
const id=String(x.agent_id||'').trim();
if(x.should_call_agent!==true||!id) return [{json:{...x,route:'none'}}];
const endpoints=(()=>{try{return $('Runtime endpoints').first().json||{}}catch{return {}}})();
const registry=Array.isArray(x.registry)?x.registry:[];
const inv=resolveInvoke(id, registry, endpoints);
return [{json:{
  ...x,
  route:inv.route,
  invoke_workflow_id:inv.workflow_id||'',
  invoke_workflow_name:inv.workflow_name||'',
  invoke_url:inv.url||'',
  invoke_reason:inv.reason||'',
  invoke_message:inv.route==='unbound'?unboundAgentMessage(inv,registry):''
}}];
"""

AGENT_UNBOUND = r"""
/* An agent the Decision LLM picked cannot be reached (not in registry, disabled, no invoke). Shaped as a
   failed agent_result so Merge agent result records it in the journal and the loop re-decides or fails. */
const x=$json||{};
return [{json:{
  status:'failed',
  agent_id:String(x.agent_id||''),
  message:String(x.invoke_message||'Агент недоступен для вызова.'),
  data:{},
  artifacts:{},
  issues:[{code:'agent_unbound',reason:String(x.invoke_reason||'')}],
  assumptions:[],
  requests:[]
}}];
"""

MERGE = STATE_SHAPE_JS + r"""
const prev=$('Prepare agent call').first().json||$('Parse decision').first().json||{};
const http=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const clean=v=>{
  const s=String(v??'').trim();
  return /^(none|null|undefined)$/i.test(s)?'':s;
};
const unwrap=v=>{
  if(obj(v)) return v;
  if(Array.isArray(v)&&v.length&&obj(v[0])) return v[0];
  return {};
};
/* 1. Unwrap what Execute Workflow / HTTP Request returned into one agent_result object. */
const execError=http.error||http.executionError||null;
const result=unwrap(
  (http.body&&typeof http.body==='object')?http.body
  :(http.result&&typeof http.result==='object')?http.result
  :(http.output&&typeof http.output==='object')?http.output
  :http
);
const rawStatus=clean(result.status);
const errorMessage=clean((execError&&(execError.message||execError.description))||(result.error&&(result.error.message||result.error.description))||http.message);
const failed=!rawStatus&&Boolean(execError||result.error||errorMessage&&!result.task_id);
const agentResult={...result,status:rawStatus||(failed?'failed':'completed'),message:clean(result.message)||errorMessage};
/* 2. Apply it to the case (slot, artifacts, journal, events) — shared with the resume source=agent path. */
const state=sanitizeState(obj(prev.state)?{...prev.state}:{});
const data=obj(state.data)?{...state.data}:{};
delete data.facts;
const agentId=String(prev.agent_id||result.agent_id||'');
const registry=Array.isArray(prev.registry)?prev.registry:[];
const agentTaskIn=obj(prev.agent_task)?prev.agent_task:{};
const taskId=String(agentTaskIn.task_id||'');
const applied=applyAgentResult(state, agentId, taskId, agentResult, registry);
if(agentTaskIn.inputs&&agentTaskIn.inputs.rework_reason){
  const last=state.ledger.history[state.ledger.history.length-1];
  if(last&&last.kind==='agent') last.rework_reason=String(agentTaskIn.inputs.rework_reason);
}
let nextStatus=applied.next_status;
let shouldContinue=applied.should_continue;
const events=applied.events;
/* 3. Step budget comes from MAS — Runtime Config (max_steps, UI-editable). Hitting it is not a silent
   failure: if there is a completed result, the engineer reviews it; only with nothing to show we fail. */
const maxSteps=Math.max(3,Number(prev.max_steps)||12);
if(Number(state.step_count||0)>=maxSteps&&nextStatus==='running'){
  shouldContinue=false;
  const done=composeDoneSummary(state, registry);
  if(done){
    nextStatus='waiting_user';
    const review=buildResultReviewQuestion(state, agentId, registry, 'step_limit');
    state.hitl={pending:true,questions:[review],answers:(state.hitl&&state.hitl.answers)||{}};
    state.ledger.reviews=Number(state.ledger.reviews||0)+1;
    events.push({kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:`Лимит шагов (${maxSteps}) исчерпан — прошу инженера принять результат или описать доработку.`,payload:{...review,...execRef()}});
  } else {
    nextStatus='failed';
    events.push({kind:'case.failed',actor:'orchestrator',status:'failed',status_message:`Превышен лимит шагов оркестратора (${maxSteps}), результата нет.`,payload:{reason:'step_limit',max_steps:maxSteps,...execRef()}});
  }
}
state.data=data;
state.status=nextStatus;
state.version=Number(state.version||0)+1;
const persistEvents=events.map(e=>[
  prev.case_id, e.task_id||'', e.kind, e.actor, e.agent_id||'', e.status||'', e.status_message||'', e.handoff_message||'', JSON.stringify(e.payload||{})
]);
return [{json:{
  ...prev,
  agent_result:agentResult,
  state,
  next_status:nextStatus,
  should_continue:shouldContinue&&nextStatus==='running',
  update_sql_parameters:[JSON.stringify(state), nextStatus, prev.case_id],
  persist_events:persistEvents,
  events,
  event_sql_parameters:persistEvents[0],
  continue_url:String(prev.orchestrator_step_url||'http://127.0.0.1:5678/webhook/mas-orchestrator-step').replace(/\/$/,'')
}}];
"""

FINISH_NONE = r"""
const x=$json;
/* Only a `continue` decision (completion check rejected a finish) triggers the next step here;
   ask_user / finish end the run. */
const shouldContinue=String(x.action_type||'')==='continue'&&String(x.next_status||'')==='running';
return [{json:{...x, should_continue:shouldContinue, continue_url:String(x.orchestrator_step_url||'http://127.0.0.1:5678/webhook/mas-orchestrator-step').replace(/\/$/,'')}}];
"""

# Compact board (not a 12k-wide Kahn line). Grid 250×180; node ~200×88.
CX, CY = 250, 180
ORCH_LAYOUT: dict[str, tuple[int, int]] = {
    "edit after import": (-520, -380),
    "lane intake": (0, -110),
    "lane start": (1750, 180),
    "lane decide": (0, 790),
    "lane agents": (0, 1150),
    "Authenticated MAS webhook": (0, 0),
    "Runtime endpoints": (CX, 0),
    "Normalize step request": (2 * CX, 0),
    "Probe ping?": (3 * CX, 0),
    "Needs create?": (4 * CX, 0),
    "Insert execution map": (5 * CX, 0),
    "Load case": (6 * CX, 0),
    "Prepare start case": (4 * CX, CY),
    "Insert new case": (5 * CX, CY),
    "Expand start events": (6 * CX, CY),
    "Insert start events": (4 * CX, 2 * CY),
    "Restore after start": (5 * CX, 2 * CY),
    "Validate loaded case": (0, 3 * CY),
    "Case found?": (CX, 3 * CY),
    "Apply request extras": (2 * CX, 3 * CY),
    "Status only?": (3 * CX, 3 * CY),
    "Resume persist?": (4 * CX, 3 * CY),
    "Needs interpret?": (7 * CX, 3 * CY),
    "Interpret free-text answer": (8 * CX, 3 * CY),
    "Answer interpretation Structured Output": (8 * CX, 2 * CY),
    "Apply interpreted answer": (7 * CX, 2 * CY),
    "Update case after resume": (5 * CX, 3 * CY),
    "Expand resume events": (6 * CX, 3 * CY),
    "Insert resume events": (5 * CX, 4 * CY),
    "Restore after resume": (6 * CX, 4 * CY),
    "Resume to decision?": (6 * CX, 2 * CY),
    "Not a resume?": (3 * CX, 4 * CY),
    "Decision Chat Model — configure in UI": (2 * CX, 4 * CY),
    "Decision Structured Output": (3 * CX, 4 * CY),
    "Load agent registry": (0, 5 * CY),
    "Prepare decision context": (CX, 5 * CY),
    "Call Knowledge Retrieval": (CX, 6 * CY),
    "Attach orchestrator RAG evidence": (2 * CX, 6 * CY),
    "Decision LLM": (2 * CX, 5 * CY),
    "Finish proposed?": (3 * CX, 5 * CY),
    "Prepare completion check": (3 * CX, 6 * CY),
    "Verify completion": (4 * CX, 6 * CY),
    "Verification Structured Output": (4 * CX, 4 * CY),
    "Parse decision": (4 * CX, 5 * CY),
    "Update case after decision": (5 * CX, 5 * CY),
    "Expand decision events": (6 * CX, 5 * CY),
    "Insert decision events": (5 * CX, 6 * CY),
    "Restore parsed decision": (6 * CX, 6 * CY),
    "Prepare agent call": (7 * CX, 5 * CY),
    "Action router": (7 * CX, 6 * CY),
    "Call agent (n8n)": (0, 7 * CY),
    "Call agent (HTTP)": (0, 8 * CY),
    "Agent not bound": (0, 9 * CY),
    "No agent this step": (0, 10 * CY),
    "Merge agent result": (CX, 8 * CY),
    "Update case after agent": (2 * CX, 8 * CY),
    "Expand agent events": (3 * CX, 8 * CY),
    "Insert agent events": (2 * CX, 9 * CY),
    "Restore after agent events": (3 * CX, 9 * CY),
    "Continue loop?": (4 * CX, 8 * CY),
    "POST continue run": (5 * CX, 8 * CY),
    "Prepare Activity ack": (4 * CX, 10 * CY),
    "Activity sync?": (5 * CX, 10 * CY),
    "POST step ack to MAS Activity": (6 * CX, 9 * CY),
    "Format step ack": (6 * CX, 10 * CY),
}


def apply_orchestrator_layout(wf: dict) -> None:
    """Keep the board layout; generic layered relayout must not flatten this graph."""
    for node in wf.get("nodes") or []:
        name = node.get("name")
        if name in ORCH_LAYOUT:
            x, y = ORCH_LAYOUT[name]
            node["position"] = [int(x), int(y)]


def main() -> None:
    nodes = [
        note(
            "edit after import",
            (-200, -420),
            "## edit after import\n\n**Orchestrator — MAS** — thin loop:\n- Bind Postgres on load/insert/update\n- Bind **Qwen** OpenAI-compatible credential on Decision Chat Model (also Interpret free-text answer + Verify completion)\n- Bind inbound header auth on webhook **and** POST continue run\n- Bind **Runtime endpoints** → `MAS — Runtime Config` (один Set URL на весь контур)\n- **Call agent (n8n)** — universal: workflowId is an expression from `agent_registry.invoke.workflow_id` (or Runtime Config `agent_workflow_ids` JSON `{\"<agent_id>\":\"<id>\"}` when the field import assigned new ids). No per-agent nodes; adding an agent = `upsert_agent` in Control Plane Proxy + RAG routing card.\n- **Call agent (HTTP)** — `invoke.kind=http`, url with `{math_url}`-style placeholders from Runtime Config\n- Bind **Call Knowledge Retrieval** → `MAS — Knowledge Retrieval` (срез `orchestrator_routing` / `routing_card`; не excel_protocol и не schedule_mvp)\n- Excel `X-API-Key` — credential на Agent — Excel Extractor, не этот workflow\n\n`action`: probe | status | start | create | step | resume\n- **resume** `source`: human (Activity `/answer`) | agent | system (`POST /cases/{id}/run`). Кнопка HITL = `choice`; свободный текст при вариантах — Interpret free-text answer (тот же Qwen).\n- Activity `/cases` stores files; specialists fetch `/cases/{id}/artifacts/{id}` from their FastAPI tools. n8n never carries binaries.\n- **status** loads case and returns `human_gate` without LLM\n- Decision is Basic LLM Chain + Structured Output (no Agent tools)\n- Loop: **POST continue run** → own webhook (`orchestrator_step_url` in Runtime Config). Activity starts/resumes cases; it does not drive the step loop.\n\nOne execution = one step.",
            480,
            420,
            1,
        ),
        note("lane intake", (0, -110), "### 1. Вход и загрузка кейса", 280, 72, 6),
        note("lane start", (1750, 180), "### 2. Новый кейс", 220, 72, 4),
        note("lane decide", (0, 790), "### 3. Решение LLM", 240, 72, 5),
        note("lane agents", (0, 1150), "### 4. Агенты и цикл", 260, 72, 7),
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
        node(
            "Runtime endpoints",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (240, 0),
            runtime_config_execute_params(),
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
        if_true("Needs interpret?", (1480, -80), "={{ Boolean($json.needs_interpret) }}"),
        node(
            "Interpret free-text answer",
            "@n8n/n8n-nodes-langchain.chainLlm",
            1.9,
            (1680, -80),
            {
                "promptType": "define",
                "text": "={{ $json.interpret_input }}",
                "hasOutputParser": True,
                "needsFallback": False,
                "messages": {
                    "messageValues": [
                        {
                            "type": "SystemMessagePromptTemplate",
                            "message": INTERPRET_SYSTEM,
                        }
                    ]
                },
            },
        ),
        node(
            "Answer interpretation Structured Output",
            "@n8n/n8n-nodes-langchain.outputParserStructured",
            1.3,
            (1680, -240),
            structured_parser_params(json.dumps(INTERPRET_SCHEMA, ensure_ascii=False)),
        ),
        code("Apply interpreted answer", (1880, -80), APPLY_INTERPRETED),
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
            "let prev=null;\ntry{const item=$('Apply interpreted answer').first(); if(item&&item.json&&item.json.did_resume) prev=item.json;}catch(e){}\nif(!prev){try{prev=$('Apply request extras').first().json||{}}catch(e){prev={}}}\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        if_true("Resume to decision?", (1840, 200), "={{ String($json.next_status || '') === 'running' }}"),
        if_true("Not a resume?", (1120, 200), "={{ Boolean($json.is_resume) !== true }}"),
        postgres(
            "Load agent registry",
            (960, 80),
            list_agents_sql(enabled_only=True),
            "={{ [] }}",
        ),
        code("Prepare decision context", (1200, 0), PREPARE_DECISION),
        node(
            "Call Knowledge Retrieval",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (1200, 180),
            knowledge_retrieval_execute_params(),
            onError="continueRegularOutput",
        ),
        code("Attach orchestrator RAG evidence", (1440, 180), attach_orchestrator_rag_js()),
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
        # Verified completion: only a proposed `finish` takes the detour through the completion check.
        if_true(
            "Finish proposed?",
            (1680, 0),
            "={{ String(((($json.output || {}).action) || {}).type || '') === 'finish' }}",
        ),
        code("Prepare completion check", (1800, 120), PREPARE_VERIFY),
        node(
            "Verify completion",
            "@n8n/n8n-nodes-langchain.chainLlm",
            1.9,
            (1920, 120),
            {
                "promptType": "define",
                "text": "={{ $json.verify_input }}",
                "hasOutputParser": True,
                "needsFallback": False,
                "messages": {
                    "messageValues": [
                        {
                            "type": "SystemMessagePromptTemplate",
                            "message": VERIFY_SYSTEM,
                        }
                    ]
                },
            },
        ),
        node(
            "Verification Structured Output",
            "@n8n/n8n-nodes-langchain.outputParserStructured",
            1.3,
            (1920, 300),
            structured_parser_params(json.dumps(VERIFY_SCHEMA, ensure_ascii=False)),
        ),
        code("Parse decision", (2040, 0), PARSE_DECISION),
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
            {"mode": "expression", "numberOutputs": 4, "output": "={{ ({workflow:0,http:1,unbound:2,none:3})[$json.route] ?? 3 }}"},
        ),
        # Phase 2: one call node per invocation kind, target resolved from the registry row at runtime.
        execute_workflow_by_expression(
            "Call agent (n8n)",
            (3120, -240),
            "={{ $json.invoke_workflow_id }}",
            {"agent_task": "={{ $json.agent_task }}"},
        ),
        http_json("Call agent (HTTP)", (3120, -40), "={{ $json.invoke_url }}", "={{ $json.agent_task }}"),
        code("Agent not bound", (3120, 160), AGENT_UNBOUND),
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
                "authentication": "genericCredentialType",
                "genericAuthType": "httpHeaderAuth",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ ({action: 'step', case_id: $json.case_id, source: 'orchestrator-self'}) }}",
                "options": {"timeout": 8000, "response": {"response": {"fullResponse": False, "neverError": True}}},
            },
            credentials=HDR,
            onError="continueRegularOutput",
            alwaysOutputData=True,
        ),
        code(
            "Prepare Activity ack",
            (4320, 120),
            r"""const pick=()=>{
  try{const m=$('Merge agent result').first().json; if(m&&m.case_id) return m;}catch{}
  try{const n=$('No agent this step').first().json; if(n&&n.case_id) return n;}catch{}
  /* Interpret path: Apply interpreted answer owns the persisted events (hitl.answered / re-ask). Reading
     Apply request extras here (needs_interpret, no events) posted an empty hitl.request ack (CASE-6a9ee76b). */
  try{const i=$('Apply interpreted answer').first().json; if(i&&i.case_id&&i.did_resume) return i;}catch{}
  try{const a=$('Apply request extras').first().json; if(a&&a.case_id) return a;}catch{}
  try{const r=$('Normalize step request').first().json; if(r&&r.case_id) return r;}catch{}
  return $json||{};
};
const x=pick();
const persisted=Array.isArray(x.persist_events)&&x.persist_events.length>0;
const kind=x.action_type==='finish'?'case.finished':(x.action_type==='ask_user'||x.next_status==='waiting_user'?'hitl.request':'orchestrator.status');
const payload={contract:'mas_orchestrator_ack',case_id:x.case_id,status:x.next_status||x.status,action_type:x.action_type||null,message:x.message||null,should_continue:x.should_continue===true,human_gate:x.human_gate||null,version:x.version??null,restartable:x.restartable===true};
const statusMessage=String(payload.message||'').trim()||String(x.status_message||'').trim();
return [{json:{...x,activity_sync:Boolean(x.case_id&&!x.is_probe&&x.activity_base_url&&!persisted),activity_url:`${String(x.activity_base_url||'').replace(/\/$/,'')}/cases/${encodeURIComponent(String(x.case_id||''))}/events`,activity_event:{kind,actor:'orchestrator',status:payload.status,status_message:statusMessage,payload}}}];""",
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
                "options": {"timeout": 2000, "response": {"response": {"fullResponse": False, "neverError": True}}},
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
    connect(connections, "Resume persist?", "Needs interpret?", si=0)
    connect(connections, "Resume persist?", "Not a resume?", si=1)
    connect(connections, "Not a resume?", "Load agent registry", si=0)
    connect(connections, "Not a resume?", "Prepare Activity ack", si=1)
    connect(connections, "Needs interpret?", "Interpret free-text answer", si=0)
    connect(connections, "Needs interpret?", "Update case after resume", si=1)
    connect(connections, "Interpret free-text answer", "Apply interpreted answer")
    connect(connections, "Apply interpreted answer", "Update case after resume")
    connect(connections, "Decision Chat Model — configure in UI", "Interpret free-text answer", out="ai_languageModel", si=0, tin="ai_languageModel")
    connect(connections, "Decision Chat Model — configure in UI", "Answer interpretation Structured Output", out="ai_languageModel", si=0, tin="ai_languageModel")
    connect(connections, "Answer interpretation Structured Output", "Interpret free-text answer", out="ai_outputParser", tin="ai_outputParser")
    connect(connections, "Update case after resume", "Expand resume events")
    connect(connections, "Expand resume events", "Insert resume events")
    connect(connections, "Insert resume events", "Restore after resume")
    connect(connections, "Restore after resume", "Resume to decision?")
    connect(connections, "Resume to decision?", "Load agent registry", si=0)
    connect(connections, "Resume to decision?", "Prepare Activity ack", si=1)
    connect(connections, "Load agent registry", "Prepare decision context")
    connect(connections, "Prepare decision context", "Call Knowledge Retrieval")
    connect(connections, "Call Knowledge Retrieval", "Attach orchestrator RAG evidence")
    connect(connections, "Attach orchestrator RAG evidence", "Decision LLM")
    connect(connections, "Decision Chat Model — configure in UI", "Decision LLM", out="ai_languageModel", tin="ai_languageModel")
    connect(connections, "Decision Chat Model — configure in UI", "Decision Structured Output", out="ai_languageModel", si=0, tin="ai_languageModel")
    connect(connections, "Decision Structured Output", "Decision LLM", out="ai_outputParser", tin="ai_outputParser")
    # Verified completion: finish → completion check (second LLM pass) → Parse decision; anything else → Parse decision.
    connect(connections, "Decision LLM", "Finish proposed?")
    connect(connections, "Finish proposed?", "Prepare completion check", si=0)
    connect(connections, "Finish proposed?", "Parse decision", si=1)
    connect(connections, "Prepare completion check", "Verify completion")
    connect(connections, "Verify completion", "Parse decision")
    connect(connections, "Decision Chat Model — configure in UI", "Verify completion", out="ai_languageModel", si=0, tin="ai_languageModel")
    connect(connections, "Decision Chat Model — configure in UI", "Verification Structured Output", out="ai_languageModel", si=0, tin="ai_languageModel")
    connect(connections, "Verification Structured Output", "Verify completion", out="ai_outputParser", tin="ai_outputParser")
    connect(connections, "Parse decision", "Update case after decision")
    connect(connections, "Update case after decision", "Expand decision events")
    connect(connections, "Expand decision events", "Insert decision events")
    connect(connections, "Insert decision events", "Restore parsed decision")
    connect(connections, "Restore parsed decision", "Prepare agent call")
    connect(connections, "Prepare agent call", "Action router")
    connect(connections, "Action router", "Call agent (n8n)", si=0)
    connect(connections, "Action router", "Call agent (HTTP)", si=1)
    connect(connections, "Action router", "Agent not bound", si=2)
    connect(connections, "Action router", "No agent this step", si=3)
    connect(connections, "Call agent (n8n)", "Merge agent result")
    connect(connections, "Call agent (HTTP)", "Merge agent result")
    connect(connections, "Agent not bound", "Merge agent result")
    # A `continue` step (completion check rejected a finish) re-enters the loop like an agent step would.
    connect(connections, "No agent this step", "Continue loop?")
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
    apply_orchestrator_layout(wf)
    missing = [n["name"] for n in wf["nodes"] if n.get("name") not in ORCH_LAYOUT]
    if missing:
        raise SystemExit(f"orchestrator layout missing nodes: {missing}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(nodes)} nodes)")


if __name__ == "__main__":
    main()
