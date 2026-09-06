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

SYSTEM = """Ты — единственный LLM-решатель агента Schedule Builder.

Исходный .INC тебе не показывают. Он лежит в сессии FastAPI. session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Инструменты:
- inspect_schedule — объектная инвентаризация скважин, keywords, даты, GRUPTREE (компактно)
- inspect_well — подробный объект одной скважины: identity, factual WCONPROD, commissioning anchor и история режимов
- analyze_forecast_controls — детерминированный разбор control timeline: history, forecast, overrides, economics, reopen/status/efficiency events
- search_keywords — по intent сервиса вернёт keywords + methods
- get_keyword — объект keyword: details.parameters (позиция, тип, unit, описание) из schema_catalogue
- list_records — компактные записи keyword/well
- apply_commissioning — сдвиг дат ввода по фактам Excel из сессии. Якорь commissioning — первый нефактический WCONPROD; последующие WCONPROD являются прогнозными режимами и сохраняются.
- apply_group_rebind — перепривязка групп по тексту задачи и baseline. Скважины только из inspect.
- apply_operations — точечные MODIFY/ADD по схеме keyword. wells только из inspect.
- render_ir — fields по каталогу → текст записи .inc (слэши, layout). Не пиши .INC сам.
- build_schedule — собрать текущий working text, если apply уже менял сессию
- validate_result — проверки emit

Правила:
- Не пиши текст SCHEDULE сам. Только инструменты.
- Не придумывай скважины, даты, группы, дебиты.
- Для commissioning сначала проверь объект скважины через inspect_schedule/inspect_well и отличай first_wconprod от forecast control events.
- Для изменения одного параметра после существующего контроля используй WELTARG, а не переписывай весь WCONPROD.
- Не путай WECON (экономические пределы), WTEST (переоткрытие), WELOPEN (статус), WEFAC (uptime), WPIMULT (CF) и GCONPROD (группа).
- Если analyze_forecast_controls вернул needs_input по границе history/forecast — не применяй операцию до уточнения.
- Жёсткое правило: если комментарий WCONPROD содержит «факт» или «fact», запись фактическая. Её нельзя удалять, переносить или использовать как прогнозный commissioning anchor.
- suggested_capability=commissioning → сразу apply_commissioning, затем STOP.
- suggested_capability=group_rebind → inspect при необходимости, затем apply_group_rebind, затем STOP.
- Иначе search_keywords → get_keyword (смотри details.parameters) → apply_operations / render_ir / needs_input.
- Имена полей бери из get_keyword.details (WELL, DATE, CHILD), не выдумывай расклад.
- WCONPROD variant = CONTROL в нижнем регистре (orat, wrat, grat, lrat, bhp, thp, resv, grup). Если variant не указан — положи CONTROL в fields.
- apply_operations принимает JSON-массив [{keyword, operation, fields}], не объект с ключами "0","1".
- После apply_* со status completed или needs_input — STOP. Не вызывай build, если apply уже вернул результат.
- Если данных нет — не вызывай apply с пустыми выдуманными полями.
- Не вызывай один и тот же inspect_* больше трёх раз подряд. Если за 3 шага нет apply_* — STOP и верни вопрос (needs_input).
- Не зацикливайся: max несколько tool-вызовов, затем короткий итог.
- Retrieved knowledge — только срез schedule_mvp (keyword_instruction / worked_example): when-to-use и pitfalls. Расклад полей по-прежнему из get_keyword.details / render_ir, не из schema_catalogue RAG. Пустой или unavailable срез — работай инструментами, не спрашивай HITL про базу знаний и не ходи в excel_protocol / orchestrator_routing.

Заверши одним коротким фактическим предложением по-русски.
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
        "Сдвинуть даты ввода по фактам Excel, уже лежащим в сессии. Даты в аргументах не передавай.",
        [],
    ),
    (
        "apply_group_rebind",
        "Перепривязать скважины в группу. Пустые поля сервис возьмёт из objective/baseline. wells только из inspect.",
        [
            ("parent_group", "string", False, "Родительская группа, если уже известна"),
            ("wells", "string", False, "Имена скважин через пробел, только из inspect"),
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
    placeholders = {
        "values": [
            {"name": key, "description": desc, "type": typ}
            for key, typ, _req, desc in fields
        ]
    }
    body_values = [
        {
            "name": "session_id",
            "valueProvider": "fieldValue",
            "value": "={{ $('Open schedule session').first().json.session_id }}",
        }
    ]
    for key, _typ, required, _desc in fields:
        body_values.append(
            {
                "name": key,
                "valueProvider": "modelRequired" if required else "modelOptional",
                "value": "",
            }
        )
    return node(
        name,
        "@n8n/n8n-nodes-langchain.toolHttpRequest",
        1.1,
        pos,
        {
            "toolDescription": description,
            "method": "POST",
            "url": "={{ $('Runtime configuration').first().json.schedule_service_url + '/agent-tools/' + "
            + json.dumps(name)
            + " }}",
            "authentication": "none",
            "sendQuery": False,
            "sendHeaders": True,
            "specifyHeaders": "keypair",
            "sendBody": True,
            "specifyBody": "keypair",
            "placeholderDefinitions": placeholders,
            "optimizeResponse": False,
            "parametersHeaders": {"values": [{"name": "Content-Type", "valueProvider": "fieldValue", "value": "application/json"}]},
            "parametersBody": {"values": body_values},
        },
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

RESTORE_AFTER_PROGRESS = r"""
const x=$('Restore after Schedule Builder accepted').first().json||{};
const cap=String(x.suggested_capability||'').trim();
const known=cap==='commissioning'||cap==='group_rebind'?cap:'operations';
return [{json:{...x, suggested_capability:known}}];
"""

DESCRIBE_APPLY = r"""
const raw=$json||{};
const data=raw.data&&typeof raw.data==='object'?raw.data:{};
const shifts=Array.isArray(data.shifts)?data.shifts:[];
const rebind=data.group_rebind&&typeof data.group_rebind==='object'?data.group_rebind:{};
const httpError=raw.error&&typeof raw.error==='object'?raw.error:null;
const status=String(raw.status||(httpError?'failed':'')).trim();
const operation=rebind.parent_group||Array.isArray(rebind.wells)?'group_rebind':(shifts.length||(Array.isArray(data.changed_keywords)&&data.changed_keywords.indexOf('DATES')>=0)?'commissioning':'apply');
const dates=shifts.slice(0,12).map(s=>{
  if(!s||typeof s!=='object') return '';
  const well=String(s.well||s.name||'').trim();
  const date=String(s.date||s.new_date||s.to||'').trim();
  return well?(date?well+': '+date:well):'';
}).filter(Boolean);
const wellsAffected=operation==='group_rebind'
  ? (Array.isArray(rebind.wells)?rebind.wells.length:Number(data.records_applied||0))
  : Number(shifts.length||data.records_applied||0);
const skipFetch=status!=='completed';
const fallback=operation==='group_rebind'
  ? (status==='completed'?'Перепривязка групп завершена':'Перепривязка групп требует уточнения')
  : (status==='completed'?('Commissioning: сдвинуты даты ввода для '+wellsAffected+' скважин'):'Commissioning требует уточнения');
const message=String(raw.message||(httpError&&(httpError.message||httpError.description))||fallback);
return [{json:{
  ...raw,
  status:status||'failed',
  message,
  skip_fetch:skipFetch,
  activity_kind:status==='completed'?'agent.progress':(status==='needs_input'?'agent.progress':'agent.failed'),
  status_message:message,
  activity_payload:{
    source:'schedule-builder-agent-workflow',
    operation,
    wells_affected:wellsAffected,
    dates_changed:dates,
    status:status||'failed'
  }
}}];
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
const hasApply=tools.some(n=>n.indexOf('apply_')===0||n.indexOf('build_')===0);
const skipFetch=!hasApply||repeated;
const message=hasApply&&!repeated
  ? ('Schedule Builder вызвал '+String(tools.length)+' tools: '+unique.join(', '))
  : (repeated
    ? 'Агент повторял один и тот же tool без прогресса. Нужно уточнение.'
    : 'Агент не применил изменений. Уточните задачу.');
return [{json:{
  ...(skipFetch?{
    task_id:opened.task_id||'',
    agent_id:'schedule_builder',
    status:'needs_input',
    message,
    data:{tools_used:unique,total_calls:steps.length},
    artifacts:{},
    issues:[{type:repeated?'repeated_tools':'no_apply'}],
    assumptions:[],
    requests:[{question_id:'Q-apply',question:message,options:[]}]
  }:agent),
  skip_fetch:skipFetch,
  has_apply:hasApply,
  activity_kind:'agent.progress',
  status_message:message,
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
const planner_input=JSON.stringify({
  objective,
  handoff_message:handoff,
  inspect:opened.inspect||{},
  fact_count:opened.fact_count||0,
  facts_preview:opened.facts_preview||[],
  suggested_capability:opened.suggested_capability||''
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
                    "`suggested_capability` commissioning/group_rebind идут "
                    "обычным HTTP `apply_*` (external n8n runners не execute'ят "
                    "toolHttpRequest). После apply проверяется status; "
                    "needs_input не идёт в Fetch result. Сессия закрывается "
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
            {"jsCode": RESTORE_AFTER_PROGRESS},
        ),
        node(
            "Capability router",
            "n8n-nodes-base.switch",
            3.4,
            (1220, 0),
            {
                "mode": "expression",
                "numberOutputs": 3,
                "output": "={{ ({commissioning:0, group_rebind:1, operations:2})[String($json.suggested_capability||'operations')] ?? 2 }}",
            },
        ),
        http_json(
            "Apply commissioning",
            (1480, -160),
            "POST",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/agent-tools/apply_commissioning' }}",
            "={{ ({session_id: $('Open schedule session').first().json.session_id}) }}",
            timeout=180000,
            retry=True,
        ),
        http_json(
            "Apply group rebind",
            (1480, 0),
            "POST",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/agent-tools/apply_group_rebind' }}",
            "={{ ({session_id: $('Open schedule session').first().json.session_id}) }}",
            timeout=180000,
            retry=True,
        ),
        node("Describe apply result", "n8n-nodes-base.code", 2, (1680, -80), {"jsCode": DESCRIBE_APPLY}),
        activity_event_dynamic("Activity — Schedule Builder apply", (1680, -260)),
        node(
            "Restore after apply event",
            "n8n-nodes-base.code",
            2,
            (1880, -260),
            {"jsCode": "const x=$('Describe apply result').first().json||{}; return [{json:x}];"},
        ),
        if_true("Apply finished?", (1880, -80), "={{ Boolean($json.skip_fetch) }}"),
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
                    "maxIterations": 6,
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
    connect(connections, "Restore after Schedule Builder progress", "Capability router")
    connect(connections, "Session ready?", "Format missing schedule", si=1)
    connect(connections, "Capability router", "Apply commissioning", si=0)
    connect(connections, "Capability router", "Apply group rebind", si=1)
    connect(connections, "Capability router", "Prepare AI Agent input", si=2)
    connect(connections, "Apply commissioning", "Describe apply result")
    connect(connections, "Apply group rebind", "Describe apply result")
    connect(connections, "Describe apply result", "Activity — Schedule Builder apply")
    connect(connections, "Activity — Schedule Builder apply", "Restore after apply event")
    connect(connections, "Restore after apply event", "Apply finished?")
    connect(connections, "Apply finished?", "Format schedule result", si=0)
    connect(connections, "Apply finished?", "Fetch schedule result", si=1)
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
