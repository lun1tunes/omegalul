#!/usr/bin/env python3
"""Generate Agent — Schedule Builder: one LLM + FastAPI keyword tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options
from generate_mas_runtime_config import runtime_config_execute_params
from mas_retrieval_client import (
    SELECTORS,
    attach_schedule_rag_js,
    knowledge_retrieval_execute_params,
)
from mas_tool_nodes import HTTP_REQUEST_TOOL_TYPE, HTTP_REQUEST_TOOL_VERSION, http_request_tool_params
from schedule_rag_workflows import KEYWORDS as SCHEDULE_KEYWORDS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/schedule-builder-agent.workflow.json"
WF_ID = "c8d5f3b2-6e91-5d22-8a7b-1f0c4e9a2d55"
WF_NAME = "Agent — Schedule Builder"
OA = {
    "openAiApi": {
        "id": "REPLACE_IN_UI",
        "name": "REPLACE: Qwen OpenAI-compatible schedule builder credential",
    }
}

SYSTEM = """Ты — инженер-решатель агента Schedule Builder: читаешь задачу инженера, выбираешь инструмент и вызываешь его со структурированными аргументами. Текст SCHEDULE (.INC) пишут инструменты, не ты.

Исходный .INC тебе не показывают — он лежит в сессии FastAPI. session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Какой инструмент когда:
- Задача про НОВЫЕ ДАТЫ ВВОДА скважин (Excel «скважина — дата», fact_count > 0) → apply_commissioning. Факты, решение по скважинам вне Excel и параметры новых скважин уже в сессии — аргументов не нужно. Другие apply_* для такой задачи не вызывай.
- Задача про ГРУППЫ (поместить скважины в группу, групповой контроль GCONPROD) → inspect_schedule (имена скважин, дерево GRUPTREE), затем apply_group_rebind с полным spec: wells, parent_group, parent_of_parent, control (ORAT/WRAT/GRAT/LRAT/RESV), gas_rate числом в м3/сут («200 тыс. м3 газа в сут.» → 200000). Родителя новой группы бери из дерева baseline (корень — FIELD или как в inspect), если инженер не сказал иначе.
- Точечные правки режимов/keywords → search_keywords → get_keyword (details.parameters) → apply_operations или render_ir.
- inspect_well / analyze_forecast_controls / list_records — чтобы посмотреть скважину перед правкой. Не вызывай их «на всякий случай» и не больше трёх раз подряд.
- build_schedule — только если apply уже менял сессию; validate_result — проверки emit.
- ask_engineer — единственный способ спросить инженера. Только когда данных нет ни в задаче, ни в baseline, ни в ответах инженера (engineer_answers). Один вопрос обычной русской фразой: что нужно и зачем; варианты (options) — как их называет инженер: имена групп из baseline, «оставить»/«убрать». Никаких имён полей, JSON, enum, кодов. Инженер отвечает фактами, таблицами и файлами — не строками .INC.

Ответы инструментов:
- ok:false, error:spec_incomplete — это тебе, не инженеру: заполни missing из текста задачи и inspect_schedule (where_to_find подсказывает откуда) и вызови инструмент снова. Спрашивай инженера, только если данных действительно нет.
- ok:false, error:question_not_human — переформулируй вопрос прозой и вызови ask_engineer снова.
- ok:false, error:well_not_in_schedule / operations_required — ошибка твоего вызова; исправь аргументы.
- ok:false, error:result_already_stored — результат этого запуска уже зафиксирован (apply или вопрос инженеру). Больше инструменты не вызывай, заверши ответ.
- status completed или needs_input от apply_* / ask_engineer — результат зафиксирован. STOP: не вызывай build и другие apply.

Инварианты:
- Не придумывай скважины, даты, группы, дебиты. Имена скважин — только из inspect_schedule.
- Если комментарий WCONPROD содержит «факт»/«fact», запись фактическая: её нельзя удалять, переносить или считать прогнозным якорем ввода. Якорь commissioning — первый нефактический WCONPROD; следующие WCONPROD — прогнозные режимы, они сохраняются.
- Один параметр после существующего контроля — WELTARG, не переписывание WCONPROD. Не путай WECON (экономика), WTEST (переоткрытие), WELOPEN (статус), WEFAC (uptime), WPIMULT (CF), GCONPROD (группа).
- Имена полей — из get_keyword.details (WELL, DATE, CHILD). WCONPROD variant = CONTROL в нижнем регистре (orat, wrat, grat, lrat, bhp, thp, resv, grup); если variant не указан — положи CONTROL в fields.
- apply_operations принимает JSON-массив [{keyword, operation, fields}], не объект с ключами "0","1".
- Если analyze_forecast_controls вернул needs_input по границе history/forecast — не применяй операцию, спроси инженера.
- rework_reason в задаче — замечание оркестратора к прошлому результату: устрани именно его.
- Retrieved knowledge — только срез schedule_mvp (keyword_instruction / worked_example): when-to-use и pitfalls. Расклад полей — из get_keyword.details / render_ir. Пустой или unavailable срез — работай инструментами, не спрашивай про базу знаний.

Заверши одним коротким фактическим предложением по-русски о том, что сделано или чего не хватило.
"""

TOOLS = [
    ("inspect_schedule", "Объектная инвентаризация baseline: wells, factual/forecast WCONPROD, commissioning anchors, история режимов, keywords, даты и GRUPTREE. Без полного .INC.", []),
    (
        "inspect_well",
        "Подробно осмотреть одну скважину: identity, factual WCONPROD, commissioning anchor (первый нефактический WCONPROD), последующие forecast control events и связанные records.",
        [("well", "string", True, "Точное имя скважины из inspect_schedule")],
    ),
    (
        "analyze_forecast_controls",
        "Детерминированно разобрать timeline одной скважины: history/forecast controls, commissioning, WELTARG, WECON, WTEST, WELOPEN, WEFAC, WPIMULT и правила выбора keyword.",
        [("well", "string", True, "Точное имя скважины из inspect_schedule")],
    ),
    (
        "search_keywords",
        "Найти keywords и methods по intent (даты ввода, группы, дебиты, перфорация, ГРП).",
        [("intent", "string", True, "Фраза задачи: даты ввода, перепривязка групп, ORAT, ...")],
    ),
    (
        "get_keyword",
        "Объект keyword: details.kind=schedule_keyword, parameters[{name,position,type,required,unit,description,enum}]. Имена полей только отсюда.",
        [("keyword", "string", True, "DATES / WCONPROD / GRUPTREE / ...")],
    ),
    (
        "list_records",
        "Компактные records keyword. well опционален. Не больше 40 строк.",
        [
            ("keyword", "string", True, "Имя keyword"),
            ("well", "string", False, "Точное имя скважины из inspect"),
        ],
    ),
    (
        "apply_commissioning",
        "Сдвинуть даты ввода скважин по фактам «скважина — дата» из Excel, уже лежащим в сессии (fact_count). Решение по скважинам вне Excel и параметры новых скважин тоже берутся из сессии. Аргументов нет. Для задач про даты ввода — это единственный нужный apply.",
        [],
    ),
    (
        "apply_group_rebind",
        "Поместить скважины в группу с групповым контролем (WELSPECS + GRUPTREE + GCONPROD). Spec заполняешь ты из задачи и inspect_schedule; ничего не выводится из текста автоматически. Неполный spec вернётся как ok:false spec_incomplete с missing и where_to_find — дополни и вызови снова.",
        [
            ("wells", "string", True, "Имена скважин через пробел или запятую, только из inspect_schedule.wells"),
            ("parent_group", "string", True, "Имя целевой группы из задачи (например DKS)"),
            ("parent_of_parent", "string", False, "Родитель целевой группы в GRUPTREE: корень baseline (FIELD) или группа из задачи. Пусто — возьмётся единственный корень baseline"),
            ("control", "string", True, "Тип группового контроля: GRAT (газ), ORAT (нефть), WRAT (вода), LRAT (жидкость), RESV"),
            ("gas_rate", "number", True, "Целевой дебит числом в м3/сут: «200 тыс. м3 в сут.» → 200000"),
            ("effective_at", "string", False, "Дата начала контроля вида 1 JAN 2026; пусто — с даты ввода этих скважин"),
        ],
    ),
    (
        "ask_engineer",
        "Задать инженеру ОДИН вопрос обычной русской фразой, когда данных нет ни в задаче, ни в baseline, ни в engineer_answers. options — варианты словами инженера (имена групп из baseline, «оставить»/«убрать»). Без имён полей, JSON, enum. После вызова — STOP.",
        [
            ("question", "string", True, "Вопрос по-русски: что нужно и зачем, с именами скважин/групп"),
            ("options", "string", False, "Варианты ответа через точку с запятой, например «Оставить как в baseline; Убрать из прогноза». Пусто — свободный ответ"),
            ("topic", "string", False, "Короткий латинский идентификатор темы вопроса, например target_group"),
            ("accepts_files", "string", False, "Какие файлы принимаются, через запятую: xlsx, .inc"),
        ],
    ),
    (
        "apply_operations",
        "Применить operations. JSON-массив: [{\"keyword\":\"WCONPROD\",\"operation\":\"MODIFY\",\"fields\":{...}}].",
        [("operations", "json", True, "Массив {keyword, operation, fields}. Не объект с ключами 0,1,2.")],
    ),
    (
        "render_ir",
        "Собрать текст keyword по schema_catalogue: ir_events[{event_id,operation,keyword,variant,fields,provenance}]. Не пиши .INC руками.",
        [
            ("mode", "string", False, "CREATE или REVISE"),
            ("ir_events", "json", True, "Массив IR-событий с fields по get_keyword.details.parameters"),
        ],
    ),
    (
        "build_schedule",
        "Собрать текущий working SCHEDULE, если apply уже отработал. Не вызывай вместо commissioning/group_rebind.",
        [],
    ),
    (
        "validate_result",
        "Проверить текущий working text: findings без полного файла.",
        [],
    ),
]


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-sched-agent:{name}"))


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


def connect(c, src, dst, out="main", si=0, tin="main", ti=0):
    groups = c.setdefault(src, {})
    outputs = groups.setdefault(out, [])
    while len(outputs) <= si:
        outputs.append([])
    outputs[si].append({"node": dst, "type": tin, "index": ti})


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


def http_json(name, pos, method, url, body=None, timeout=180000, *, activity=False, retry=False):
    response = {"fullResponse": False, "responseFormat": "json"}
    if activity:
        response["neverError"] = True
        timeout = min(timeout, 2000)
    params = {
        "method": method,
        "url": url,
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
        "options": {"timeout": timeout, "response": {"response": response}},
    }
    if body is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = body
    extra = {"onError": "continueRegularOutput", "alwaysOutputData": True}
    if retry:
        extra.update({"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000})
    return node(name, "n8n-nodes-base.httpRequest", 4.4, pos, params, **extra)


def activity_event(name, pos, kind, message, *, status="running"):
    body = (
        "={{ ({"
        f"kind: {json.dumps(kind)}, "
        "actor: 'schedule_builder', agent_id: 'schedule_builder', "
        "task_id: $('Normalize schedule task').first().json.agent_task.task_id, "
        f"status: {json.dumps(status)}, "
        f"status_message: {json.dumps(message)}, "
        "payload: {source: 'schedule-builder-agent-workflow'}"
        "}) }}"
    )
    return http_json(
        name,
        pos,
        "POST",
        "={{ $('Runtime configuration').first().json.activity_base_url + '/cases/' + $('Normalize schedule task').first().json.agent_task.case_id + '/events' }}",
        body,
        timeout=2000,
        activity=True,
    )



def activity_event_dynamic(name, pos):
    body = (
        "={{ ({"
        "kind: $json.activity_kind || 'agent.progress', "
        "actor: 'schedule_builder', agent_id: 'schedule_builder', "
        "task_id: $('Normalize schedule task').first().json.agent_task.task_id, "
        "status: $json.status || 'running', "
        "status_message: $json.status_message || '', "
        "payload: $json.activity_payload || {source: 'schedule-builder-agent-workflow'}"
        "}) }}"
    )
    return http_json(
        name,
        pos,
        "POST",
        "={{ $('Runtime configuration').first().json.activity_base_url + '/cases/' + $('Normalize schedule task').first().json.agent_task.case_id + '/events' }}",
        body,
        timeout=2000,
        activity=True,
    )


def tool_http(name, pos, description, fields):
    # n8n 2.30.8 + AI Agent v3: tools must be executable nodes → HTTP Request (as tool) with $fromAI.
    return node(
        name,
        HTTP_REQUEST_TOOL_TYPE,
        HTTP_REQUEST_TOOL_VERSION,
        pos,
        http_request_tool_params(
            name,
            description,
            fields,
            url_expr="={{ $('Runtime configuration').first().json.schedule_service_url + '/agent-tools/' + "
            + json.dumps(name)
            + " }}",
            session_expr="$('Open schedule session').first().json.session_id",
        ),
        retryOnFail=True,
        maxTries=3,
        waitBetweenTries=2000,
    )


NORMALIZE = r"""
const incoming=(()=>{try{return $('When executed by another workflow').first().json||{}}catch{return $json||{}}})();
const root=incoming&&typeof incoming==='object'?incoming:{};
const body=root.body&&typeof root.body==='object'?root.body:root;
let task=body.agent_task&&typeof body.agent_task==='object'?body.agent_task:(body.case_id||body.task_id||body.objective?body:null);
if(!task||typeof task!=='object') task={};
// Contract: agent_task only (retired specialist_packet is not accepted).
task.agent_id='schedule_builder';
task.case_id=String(task.case_id||'');
task.task_id=String(task.task_id||'');
task.objective=String(task.objective||'');
task.handoff_message=String(task.handoff_message||'');
task.inputs=task.inputs&&typeof task.inputs==='object'?task.inputs:{};
task.context=task.context&&typeof task.context==='object'?task.context:{};
const cfg=$('Runtime configuration').first().json||{};
if(!task.inputs.activity_base_url&&cfg.activity_base_url) task.inputs={...task.inputs, activity_base_url:cfg.activity_base_url};
return [{json:{...cfg, agent_task:task, case_id:task.case_id, task_id:task.task_id}}];
"""

SUMMARIZE_AI = r"""
const agent=$json||{};
const opened=$('Open schedule session').first().json||{};
const steps=Array.isArray(agent.intermediateSteps)?agent.intermediateSteps:(Array.isArray(agent.intermediate_steps)?agent.intermediate_steps:[]);
const toolName=step=>{
  if(!step||typeof step!=='object') return '';
  const a=step.action&&typeof step.action==='object'?step.action:step;
  return String(a.tool||a.toolName||a.name||step.tool||'');
};
const tools=steps.map(toolName).filter(Boolean);
const unique=[...new Set(tools)];
const counts={};
for(const name of tools) counts[name]=(counts[name]||0)+1;
const repeated=Object.keys(counts).some(name=>counts[name]>3);
// A stored result exists after apply_* / build_schedule (completed) or ask_engineer (needs_input).
const hasResult=tools.some(n=>n.indexOf('apply_')===0||n.indexOf('build_')===0||n==='ask_engineer');
const skipFetch=!hasResult;
const finalText=String(agent.output||agent.text||'').trim();
// Engineer-facing fallback when the LLM ended without fixing a result: plain Russian, no tool names.
const question=repeated
  ? 'Schedule Builder не смог продвинуться по задаче: несколько раз проверял SCHEDULE, но не понял, что именно изменить. Опишите задачу подробнее: какие скважины, какие даты или режимы работы и откуда взять значения.'
  : 'Schedule Builder не внёс изменений в SCHEDULE: не хватило данных, чтобы понять задачу. Опишите, что именно нужно изменить (скважины, даты, режимы) и откуда взять значения (Excel, текст, baseline).';
const message=hasResult
  ? (finalText||'Schedule Builder завершил работу с инструментами.')
  : (finalText?finalText+' ':'')+question;
return [{json:{
  ...(skipFetch?{
    task_id:opened.task_id||'',
    agent_id:'schedule_builder',
    status:'needs_input',
    message:question,
    data:{tools_used:unique,total_calls:steps.length,llm_final_text:finalText.slice(0,600)},
    artifacts:{},
    issues:[{type:repeated?'repeated_tools':'no_apply'}],
    assumptions:[],
    requests:[{question_id:'Q-apply',question,options:[],accepts:{free_text:true,files:['xlsx','.inc']}}]
  }:agent),
  skip_fetch:skipFetch,
  has_apply:hasResult,
  activity_kind:'agent.progress',
  status_message:message.slice(0,400),
  activity_payload:{source:'schedule-builder-agent-workflow',total_calls:steps.length,tools_used:unique,iterations:steps.length}
}}];
"""

_SCHED_SEL = SELECTORS["schedule"]
PREPARE = (
    "const RAG_TARGET_BASE=" + json.dumps(_SCHED_SEL["target_base"]) + ";\n"
    "const RAG_ACCESS_SCOPE=" + json.dumps(_SCHED_SEL["access_scope"]) + ";\n"
    "const RAG_KNOWLEDGE_TYPES=" + json.dumps(_SCHED_SEL["knowledge_types"]) + ";\n"
    "const RAG_TOP_K=" + str(int(_SCHED_SEL["top_k"])) + ";\n"
    "const ALLOWED_KEYWORDS=" + json.dumps(SCHEDULE_KEYWORDS) + ";\n"
    + r"""
const opened=$json||{};
const task=$('Normalize schedule task').first().json.agent_task||{};
const objective=String(opened.objective||task.objective||'');
const handoff=String(opened.handoff_message||task.handoff_message||'');
const blob=[objective,handoff].join('\n');
const allowed=new Set(ALLOWED_KEYWORDS);
const fromText=[...new Set((blob.match(/\b[A-Z][A-Z0-9_]{2,}\b/g)||[]).filter(k=>allowed.has(k)))];
const low=blob.toLowerCase();
const mapped=[];
if(/дат[аые].{0,24}ввод|ввод.{0,16}скважин|commission/.test(low)) mapped.push('DATES','WCONPROD');
if(/групп|перепривяз|gruptree/.test(low)) mapped.push('GRUPTREE','GCONPROD','WELSPECS');
if(/\borat\b|\bwrat\b|\bgrat\b|дебит|лимит.{0,24}нефт|wconprod|weltarg/.test(low)) mapped.push('WCONPROD','WELTARG');
if(/грп|гидроразрыв|fracture/.test(low)) mapped.push('FRACTURE_SPECS','FRACTURE_STAGE');
if(/vfp/.test(low)) mapped.push('VFPPROD','WVFPDP');
if(/перфорац|compdat/.test(low)) mapped.push('COMPDATMD');
if(/закачк|инъект|wconinje/.test(low)) mapped.push('WCONINJE');
const keyword_families=[...new Set([...fromText,...mapped])].filter(k=>allowed.has(k)).slice(0,6);
const topics=[];
const task_patterns=[];
if(/дат|ввод/.test(low)){topics.push('календарь');task_patterns.push('даты ввода');}
if(/групп|перепривяз/.test(low)){topics.push('группы');task_patterns.push('перепривязка групп');}
if(/дебит|orat|лимит/.test(low)){topics.push('контроль');task_patterns.push('прогнозный режим');}
if(/грп|fracture/.test(low)){topics.push('ГРП');task_patterns.push('гидроразрыв');}
const query=blob.trim().replace(/\b[\w.-]+\.(xlsx|xls|xlsm|inc|dev|txt|csv)\b/gi,' ').replace(/\s+/g,' ').trim().slice(0,800)||'schedule keyword instruction';
const retrieval_selector={target_base:RAG_TARGET_BASE,knowledge_types:RAG_KNOWLEDGE_TYPES};
const schedule_retrieval_request={
  query,
  filters:{
    target_base:RAG_TARGET_BASE,
    access_scope:RAG_ACCESS_SCOPE,
    knowledge_types:RAG_KNOWLEDGE_TYPES,
    keyword_families,
    topics:[...new Set(topics)],
    task_patterns:[...new Set(task_patterns)]
  },
  top_k:RAG_TOP_K
};
// The LLM decides which tool fits: task text + baseline inventory + what the engineer already answered.
const engineerAnswers=Array.isArray(opened.engineer_answers)?opened.engineer_answers:[];
const reworkReason=String(opened.rework_reason||(task.inputs&&task.inputs.rework_reason)||'').trim();
const planner_input=JSON.stringify({
  objective,
  handoff_message:handoff,
  inspect:opened.inspect||{},
  fact_count:opened.fact_count||0,
  facts_preview:opened.facts_preview||[],
  engineer_answers:engineerAnswers,
  ...(reworkReason?{rework_reason:reworkReason}:{})
});
return [{json:{...opened,session_id:opened.session_id,agent_input:planner_input,planner_input,schedule_retrieval_request,retrieval_selector}}];
"""
)

FORMAT_OPEN = r"""
const opened=$json||{};
const result=opened.result&&typeof opened.result==='object'?opened.result:{};
return [{json:{
  task_id:result.task_id||opened.task_id||'',
  agent_id:'schedule_builder',
  status:result.status||'needs_input',
  message:result.message||opened.message||'Нет исходного SCHEDULE',
  data:result.data||{},
  artifacts:result.artifacts||{},
  issues:result.issues||[{type:'missing_schedule_source'}],
  assumptions:result.assumptions||[],
  requests:result.requests||[{question_id:'Q-sched',question:'Приложите baseline .inc',options:[]}]
}}];
"""

FORMAT_RESULT = r"""
const fetched=$json||{};
const opened=$('Open schedule session').first().json||{};
const arts=fetched.artifacts&&typeof fetched.artifacts==='object'?fetched.artifacts:{};
const artifacts=(typeof arts.schedule_out==='boolean')?{}:arts;
if(fetched.status){
  return [{json:{
    task_id:fetched.task_id||opened.task_id||'',
    agent_id:'schedule_builder',
    status:fetched.status,
    message:fetched.message||'',
    data:fetched.data||{},
    artifacts,
    issues:fetched.issues||[],
    assumptions:fetched.assumptions||[],
    requests:fetched.requests||[]
  }}];
}
return [{json:{
  task_id:opened.task_id||'',
  agent_id:'schedule_builder',
  status:'failed',
  message:String(fetched.message||fetched.error||'Schedule Builder не вернул SCHEDULE'),
  data:{},
  artifacts:{},
  issues:[{type:'schedule_agent_no_result'}],
  assumptions:[],
  requests:[]
}}];
"""


def main() -> None:
    tools = []
    x = 760
    y = -420
    for i, (name, desc, fields) in enumerate(TOOLS):
        tools.append(tool_http(name, (x + (i % 3) * 220, y + (i // 3) * 160), desc, fields))

    nodes = [
        node(
            "edit after import",
            "n8n-nodes-base.stickyNote",
            1,
            (-220, -360),
            {
                "content": (
                    "## edit after import\n\n"
                    "**Agent — Schedule Builder** — один LLM + FastAPI tools.\n\n"
                    "1. Bind **Qwen** credential on Schedule Builder Chat Model\n"
                    "2. Bind **Runtime configuration** → `MAS — Runtime Config` "
                    "(URL FastAPI). Field: Windows/host URL.\n"
                    "3. Bind **Call Knowledge Retrieval** → `MAS — Knowledge Retrieval` "
                    "(срез `schedule_mvp` / `keyword_instruction`; LLM-ветка operations). "
                    "Commissioning / group_rebind RAG не вызывают.\n"
                    "4. Orchestrator — MAS вызывает этот workflow через "
                    "`executeWorkflow` (`Call Schedule Builder`), как Excel Extractor. "
                    "Webhook не нужен.\n\n"
                    "LLM не пишет .INC. parse/apply/emit остаются в сервисе.\n"
                    "Инструмент выбирает LLM по задаче (нет regex-роутера): "
                    "`apply_commissioning` для дат ввода, `apply_group_rebind` "
                    "со spec от LLM, `apply_operations`/`render_ir` для точечных правок. "
                    "Вопрос инженеру — только `ask_engineer` прозой с вариантами; "
                    "неполный spec возвращается LLM, не человеку. Результат читается "
                    "из GET /sessions/{id}/result, сессия закрывается "
                    "POST /sessions/{id}/close. Скважины/даты не хардкодятся."
                ),
                "height": 360,
                "width": 480,
                "color": 1,
            },
        ),
        node(
            "When executed by another workflow",
            "n8n-nodes-base.executeWorkflowTrigger",
            1.2,
            (0, 0),
            {
                "inputSource": "jsonExample",
                "jsonExample": json.dumps(
                    {
                        "agent_task": {
                            "case_id": "CASE-example",
                            "task_id": "TASK-002",
                            "agent_id": "schedule_builder",
                            "objective": "Сдвинь даты ввода по Excel",
                            "inputs": {},
                            "context": {},
                        }
                    },
                    ensure_ascii=False,
                ),
            },
        ),
        node(
            "Runtime configuration",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (260, 0),
            runtime_config_execute_params(),
        ),
        node("Normalize schedule task", "n8n-nodes-base.code", 2, (500, 0), {"jsCode": NORMALIZE}),
        http_json(
            "Open schedule session",
            (740, 0),
            "POST",
            "={{ $json.schedule_service_url }}/agent-tools/open_session",
            "={{ $json.agent_task }}",
            retry=True,
        ),
        node(
            "Session ready?",
            "n8n-nodes-base.if",
            2.3,
            (980, 0),
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                    "conditions": [
                        {
                            "id": nid("sess-ok"),
                            "leftValue": "={{ $json.ok === true }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        ),
        node("Format missing schedule", "n8n-nodes-base.code", 2, (1220, 240), {"jsCode": FORMAT_OPEN}),
        activity_event(
            "Activity — Schedule Builder accepted",
            (1220, -220),
            "agent.accepted",
            "Schedule Builder принял задачу и анализирует исходный schedule.",
        ),
        node(
            "Restore after Schedule Builder accepted",
            "n8n-nodes-base.code",
            2,
            (1440, -220),
            {"jsCode": "const x=$('Session ready?').first().json||{}; return [{json:x}];"},
        ),
        activity_event(
            "Activity — Schedule Builder progress",
            (1660, -220),
            "agent.progress",
            "Schedule Builder проверяет структуру скважин, даты и прогнозные controls.",
        ),
        node(
            "Restore after Schedule Builder progress",
            "n8n-nodes-base.code",
            2,
            (1880, -220),
            {"jsCode": "const x=$('Restore after Schedule Builder accepted').first().json||{}; return [{json:x}];"},
        ),
        node(
            "Restore after Schedule Builder activity",
            "n8n-nodes-base.code",
            2,
            (2760, -180),
            {"jsCode": "const x=$('Format schedule result').first().json||{}; return [{json:x}];"},
        ),
        node("Prepare AI Agent input", "n8n-nodes-base.code", 2, (1480, 160), {"jsCode": PREPARE}),
        node(
            "Call Knowledge Retrieval",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (1480, 300),
            knowledge_retrieval_execute_params(),
            onError="continueRegularOutput",
        ),
        node(
            "Attach schedule RAG evidence",
            "n8n-nodes-base.code",
            2,
            (1680, 300),
            {"jsCode": attach_schedule_rag_js()},
        ),
        node(
            "Schedule Builder AI Agent",
            "@n8n/n8n-nodes-langchain.agent",
            3.1,
            (1720, 160),
            {
                "promptType": "define",
                "text": "={{ $json.planner_input }}",
                "hasOutputParser": False,
                "options": {
                    "systemMessage": SYSTEM,
                    "maxIterations": 8,
                    "returnIntermediateSteps": True,
                    "passthroughBinaryImages": False,
                    "passthroughBinaryPdfs": False,
                    "enableStreaming": False,
                },
                "needsFallback": False,
            },
        ),
        node(
            "Schedule Builder Chat Model — Qwen",
            "@n8n/n8n-nodes-langchain.lmChatOpenAi",
            1.3,
            (1720, 40),
            {
                "model": {"mode": "id", "value": "qwen3.6-plus"},
                "options": chat_model_options(max_tokens=2048, temperature=0),
                "responsesApiEnabled": False,
            },
            credentials=OA,
        ),
        node("Summarize AI steps", "n8n-nodes-base.code", 2, (1960, 160), {"jsCode": SUMMARIZE_AI}),
        activity_event_dynamic("Activity — Schedule Builder tools", (1960, 300)),
        node(
            "Restore after AI tools",
            "n8n-nodes-base.code",
            2,
            (2160, 300),
            {"jsCode": "const x=$('Summarize AI steps').first().json||{}; return [{json:x}];"},
        ),
        if_true("AI applied?", (2160, 160), "={{ Boolean($json.skip_fetch) }}"),
        http_json(
            "Fetch schedule result",
            (2100, 0),
            "GET",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/sessions/' + $('Open schedule session').first().json.session_id + '/result' }}",
            None,
            timeout=120000,
            retry=True,
        ),
        node("Format schedule result", "n8n-nodes-base.code", 2, (2320, 0), {"jsCode": FORMAT_RESULT}),
        http_json(
            "Close schedule session",
            (2480, 0),
            "POST",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/sessions/' + $('Open schedule session').first().json.session_id + '/close' }}",
            "={{ ({}) }}",
            timeout=2000,
            activity=True,
        ),
        *tools,
    ]

    connections = {}
    connect(connections, "When executed by another workflow", "Runtime configuration")
    connect(connections, "Runtime configuration", "Normalize schedule task")
    connect(connections, "Normalize schedule task", "Open schedule session")
    connect(connections, "Open schedule session", "Session ready?")
    connect(connections, "Session ready?", "Activity — Schedule Builder accepted", si=0)
    connect(connections, "Activity — Schedule Builder accepted", "Restore after Schedule Builder accepted")
    connect(connections, "Restore after Schedule Builder accepted", "Activity — Schedule Builder progress")
    connect(connections, "Activity — Schedule Builder progress", "Restore after Schedule Builder progress")
    # No capability router: every task goes through the LLM, which picks the tool.
    connect(connections, "Restore after Schedule Builder progress", "Prepare AI Agent input")
    connect(connections, "Session ready?", "Format missing schedule", si=1)
    connect(connections, "Prepare AI Agent input", "Call Knowledge Retrieval")
    connect(connections, "Call Knowledge Retrieval", "Attach schedule RAG evidence")
    connect(connections, "Attach schedule RAG evidence", "Schedule Builder AI Agent")
    connect(connections, "Schedule Builder Chat Model — Qwen", "Schedule Builder AI Agent", out="ai_languageModel", tin="ai_languageModel")
    for name, _desc, _fields in TOOLS:
        connect(connections, name, "Schedule Builder AI Agent", out="ai_tool", tin="ai_tool")
    connect(connections, "Schedule Builder AI Agent", "Summarize AI steps")
    connect(connections, "Summarize AI steps", "Activity — Schedule Builder tools")
    connect(connections, "Activity — Schedule Builder tools", "Restore after AI tools")
    connect(connections, "Restore after AI tools", "AI applied?")
    connect(connections, "AI applied?", "Format schedule result", si=0)
    connect(connections, "AI applied?", "Fetch schedule result", si=1)
    connect(connections, "Fetch schedule result", "Format schedule result")
    connect(connections, "Format schedule result", "Close schedule session")
    # agent.result is emitted once, by the orchestrator when it merges this result into case state.
    connect(connections, "Close schedule session", "Restore after Schedule Builder activity")

    wf = {
        "id": WF_ID,
        "name": WF_NAME,
        "active": False,
        "isArchived": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "saveManualExecutions": True,
            "callerPolicy": "workflowsFromSameOwner",
            "errorWorkflow": "",
            "executionTimeout": 900,
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
