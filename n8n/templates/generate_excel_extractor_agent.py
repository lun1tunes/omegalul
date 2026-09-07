#!/usr/bin/env python3
"""Generate Agent — Excel Extractor: one LLM + excel-tools FastAPI tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options
from generate_mas_runtime_config import EXCEL_KEY_CRED, runtime_config_execute_params
from mas_tool_nodes import HTTP_REQUEST_TOOL_TYPE, HTTP_REQUEST_TOOL_VERSION, http_request_tool_params
from mas_retrieval_client import (
    SELECTORS,
    attach_excel_rag_js,
    knowledge_retrieval_execute_params,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/excel-extractor-agent.workflow.json"
WF_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "mas-excel-extractor-agent"))
WF_NAME = "Agent — Excel Extractor"
OA = {
    "openAiApi": {
        "id": "REPLACE_IN_UI",
        "name": "REPLACE: Qwen OpenAI-compatible excel extractor credential",
    }
}

SYSTEM = """Ты — решатель агента Excel Extractor: читаешь задачу инженера и инвентарь приложенных Excel-книг (файлы, листы, таблицы с колонками и первыми строками), сам выбираешь таблицу и колонки и вызываешь инструмент извлечения. Данные извлекают инструменты — ты их не переписываешь и SCHEDULE / .INC не пишешь.

Книгу целиком тебе не показывают — она лежит в сессии FastAPI. session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Какой инструмент когда:
- Задача про даты ввода / запуска скважин (таблица «скважина — дата») → extract_commissioning с table_id, well_column и date_column из инвентаря (inspect.tables: columns и sample). Если в таблице несколько колонок с датами — бери новую (плановую) дату ввода, а не baseline / старую / дату из .INC. Один вызов на таблицу.
- В инвентаре есть таблица параметров скважин (группа, интервал MD, диаметр, режим, дебит, BHP, VFP, файл траектории) и задача упоминает новые скважины или их добавление → extract_well_parameters с table_id, well_column и mapping колонок на поля: date, group, phase, i, j, md_top, md_bot, diameter, control, rate, bhp, thp, vfp_table, welltrack_include. Немаппированные колонки сохраняются под своими заголовками.
- Обе таблицы есть → оба инструмента, по одному вызову каждый.
- Инвентаря не хватает (заголовки непонятны, таблица не найдена) → describe_table / sheet_preview / list_column_values / detect_tables / match_tables — не больше трёх вызовов подряд.
- Другие данные (дебиты, история, режимы, PVT) → detect_tables / describe_table / query_table. select и filters в query_table — JSON-массивы, не объект с ключами "0","1". Не выгружай всю книгу.
- ask_engineer — единственный способ спросить инженера: только когда по инвентарю и ответам инженера (engineer_answers) нельзя выбрать таблицу или колонку (например две колонки дат без пояснений). Один вопрос обычной русской фразой; варианты — как их видит инженер (названия листов и колонок). Никаких имён полей, JSON, enum.

Ответы инструментов:
- ok:false с error.code spec_incomplete / table_not_found / column_not_found / column_not_dates / no_rows — это тебе, не инженеру: исправь аргументы по инвентарю (details.available_tables / available_columns подсказывают) и вызови инструмент снова.
- ok:false с error.code question_not_human — переформулируй вопрос прозой и вызови ask_engineer снова.
- ok:false с error.code too_many_attempts — больше этот инструмент не вызывай: спроси инженера или заверши ответ.
- status completed от extract_* — результат зафиксирован. Если извлекать больше нечего — STOP. status needs_input от ask_engineer — STOP.

Инварианты:
- Не придумывай скважины, даты, дебиты, имена листов, table_id и колонок — только из инвентаря и ответов инструментов.
- rework_reason в задаче — замечание оркестратора к прошлому результату: устрани именно его.
- Retrieved knowledge — только срез excel_protocol / protocol_instruction (протокол инструментов). Пустой или unavailable срез — работай по инвентарю, инженера про базу знаний не спрашивай.

Заверши одним коротким фактическим предложением по-русски: что извлечено или чего не хватило.
"""

TOOLS = [
    ("workbook_introspect", "Компактные листы и размеры книги. Без файла.", []),
    (
        "sheet_preview",
        "Небольшой preview одного листа. sheet только из инвентаря / introspect.",
        [("sheet", "string", True, "Точное имя листа из инвентаря")],
    ),
    (
        "detect_tables",
        "Найти таблицы (если инвентарь пуст или неполный). sheet опционален. Возвращает table_id, range, columns — не книгу.",
        [("sheet", "string", False, "Ограничить поиск одним листом")],
    ),
    (
        "match_tables",
        "Ранжировать найденные таблицы по фразе запроса.",
        [("query", "string", True, "Что искать: даты ввода, параметры скважин, дебиты, ...")],
    ),
    (
        "describe_table",
        "Колонки, число строк и sample одной таблицы. table_id только из инвентаря / detect.",
        [("table_id", "string", True, "table_id из инвентаря")],
    ),
    (
        "list_column_values",
        "Ограниченные distinct-значения одной колонки — чтобы понять, что в ней лежит.",
        [
            ("table_id", "string", True, "table_id из инвентаря"),
            ("column", "string", True, "Точное имя колонки из инвентаря"),
        ],
    ),
    (
        "query_table",
        "Строки таблицы для прочих данных (дебиты, история). select и filters — JSON-массивы: [\"well\",\"date\"] и [{\"field\":\"well\",\"operator\":\"eq\",\"value\":\"101\"}].",
        [
            ("table_id", "string", True, "table_id из инвентаря"),
            ("select", "json", False, "Массив имён колонок, например [\"well\",\"date\"]"),
            ("filters", "json", False, "Массив {field, operator, value}"),
            ("limit", "string", False, "Лимит строк, по умолчанию 200"),
        ],
    ),
    (
        "extract_commissioning",
        "Извлечь факты «скважина — дата ввода» из выбранной тобой таблицы. Ты указываешь table_id и точные имена колонок из инвентаря; извлечение детерминированное, колонки проверяются (column_not_found / column_not_dates вернутся тебе).",
        [
            ("table_id", "string", True, "table_id таблицы со скважинами и датами из инвентаря"),
            ("well_column", "string", True, "Точное имя колонки со скважинами"),
            ("date_column", "string", True, "Точное имя колонки с новой датой ввода (не baseline)"),
        ],
    ),
    (
        "extract_well_parameters",
        "Извлечь таблицу параметров новых скважин (группа, интервал MD, диаметр, режим, дебит, BHP, VFP, файл траектории). Ты указываешь table_id, колонку скважин и mapping колонок на поля; извлечение детерминированное.",
        [
            ("table_id", "string", True, "table_id таблицы параметров из инвентаря"),
            ("well_column", "string", True, "Точное имя колонки со скважинами"),
            (
                "mapping",
                "json",
                False,
                "JSON-объект {поле: колонка}: date, group, phase, i, j, md_top, md_bot, diameter, control, rate, bhp, thp, vfp_table, welltrack_include → точные имена колонок",
            ),
        ],
    ),
    (
        "ask_engineer",
        "Спросить инженера одним вопросом по-русски, когда по инвентарю нельзя выбрать таблицу или колонку. Варианты показываются кнопками. Не для ошибок вызова инструментов.",
        [
            ("question", "string", True, "Обычная русская фраза: что нужно уточнить и зачем, без имён полей и JSON"),
            ("options", "string", False, "Варианты через точку с запятой, как их видит инженер (названия листов/колонок); пусто — свободный ответ"),
            ("topic", "string", False, "Короткая латинская тема вопроса, например date_column"),
        ],
    ),
]


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-excel-agent:{name}"))


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
    if not activity:
        params["authentication"] = "genericCredentialType"
        params["genericAuthType"] = "httpHeaderAuth"
    if body is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = body
    extra = {"onError": "continueRegularOutput", "alwaysOutputData": True}
    if retry:
        extra.update({"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000})
    if not activity:
        extra["credentials"] = EXCEL_KEY_CRED
    return node(name, "n8n-nodes-base.httpRequest", 4.4, pos, params, **extra)


def activity_event(name, pos, kind, message, *, status="running"):
    body = (
        "={{ ({"
        f"kind: {json.dumps(kind)}, "
        "actor: 'excel_extractor', agent_id: 'excel_extractor', "
        "task_id: $('Normalize excel task').first().json.agent_task.task_id, "
        f"status: {json.dumps(status)}, "
        f"status_message: {json.dumps(message)}, "
        "payload: {source: 'excel-extractor-agent-workflow'}"
        "}) }}"
    )
    return http_json(
        name,
        pos,
        "POST",
        "={{ $('Runtime configuration').first().json.activity_base_url + '/cases/' + $('Normalize excel task').first().json.agent_task.case_id + '/events' }}",
        body,
        timeout=2000,
        activity=True,
    )



def activity_event_dynamic(name, pos):
    body = (
        "={{ ({"
        "kind: $json.activity_kind || 'agent.progress', "
        "actor: 'excel_extractor', agent_id: 'excel_extractor', "
        "task_id: $('Normalize excel task').first().json.agent_task.task_id, "
        "status: $json.status || 'running', "
        "status_message: $json.status_message || '', "
        "payload: $json.activity_payload || {source: 'excel-extractor-agent-workflow'}"
        "}) }}"
    )
    return http_json(
        name,
        pos,
        "POST",
        "={{ $('Runtime configuration').first().json.activity_base_url + '/cases/' + $('Normalize excel task').first().json.agent_task.case_id + '/events' }}",
        body,
        timeout=2000,
        activity=True,
    )


def tool_http(name, pos, description, fields):
    # n8n 2.30.8 + AI Agent v3: tools must be executable nodes → HTTP Request (as tool) with $fromAI.
    params = http_request_tool_params(
        name,
        description,
        fields,
        url_expr="={{ $('Runtime configuration').first().json.excel_tools_url + '/agent-tools/' + "
        + json.dumps(name)
        + " }}",
        session_expr="$('Open excel session').first().json.session_id",
    )
    params.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
    return node(
        name,
        HTTP_REQUEST_TOOL_TYPE,
        HTTP_REQUEST_TOOL_VERSION,
        pos,
        params,
        credentials=EXCEL_KEY_CRED,
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
task.agent_id='excel_extractor';
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
const opened=$('Open excel session').first().json||{};
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
// A stored result exists after extract_* (completed) or ask_engineer (needs_input); the session is authoritative.
const hasResult=tools.some(n=>n.indexOf('extract_')===0||n==='ask_engineer');
const skipFetch=!hasResult;
const finalText=String(agent.output||agent.text||'').trim();
// Engineer-facing fallback when the LLM ended without fixing a result: plain Russian, no tool names.
const question=repeated
  ? 'Excel Extractor несколько раз просматривал книгу, но не смог выбрать таблицу и колонки. Уточните, на каком листе и в каких колонках находятся скважины и нужные значения (даты ввода, режимы, дебиты).'
  : 'Excel Extractor не смог понять, какие данные взять из приложенной книги. Уточните, на каком листе и в каких колонках находятся скважины и нужные значения (даты ввода, режимы, дебиты).';
const message=hasResult
  ? (finalText||'Excel Extractor извлёк данные из книги.')
  : (finalText?finalText+' ':'')+question;
return [{json:{
  ...(skipFetch?{
    task_id:opened.task_id||'',
    agent_id:'excel_extractor',
    status:'needs_input',
    message:question,
    data:{tools_used:unique,total_calls:steps.length,llm_final_text:finalText.slice(0,600)},
    artifacts:{},
    issues:[{type:repeated?'repeated_tools':'no_extract'}],
    assumptions:[],
    requests:[{question_id:'Q-clarify',question,options:[],accepts:{free_text:true,files:['xlsx']}}]
  }:agent),
  skip_fetch:skipFetch,
  has_extraction:hasResult,
  activity_kind:'agent.progress',
  status_message:message.slice(0,400),
  activity_payload:{source:'excel-extractor-agent-workflow',total_calls:steps.length,tools_used:unique,iterations:steps.length}
}}];
"""

_EXCEL_SEL = SELECTORS["excel"]
PREPARE = (
    "const RAG_TARGET_BASE=" + json.dumps(_EXCEL_SEL["target_base"]) + ";\n"
    "const RAG_ACCESS_SCOPE=" + json.dumps(_EXCEL_SEL["access_scope"]) + ";\n"
    "const RAG_KNOWLEDGE_TYPES=" + json.dumps(_EXCEL_SEL["knowledge_types"]) + ";\n"
    "const RAG_TOP_K=" + str(int(_EXCEL_SEL["top_k"])) + ";\n"
    "const RAG_TOPICS=" + json.dumps(_EXCEL_SEL.get("topics") or [], ensure_ascii=False) + ";\n"
    + r"""
const opened=$json||{};
const task=$('Normalize excel task').first().json.agent_task||{};
const objective=String(opened.objective||task.objective||'');
const handoff=String(opened.handoff_message||task.handoff_message||'');
const blob=[objective,handoff].join('\n');
const low=blob.toLowerCase();
const topics=RAG_TOPICS.slice();
const task_patterns=[];
if(/дат|ввод|commission/.test(low)) task_patterns.push('даты ввода');
if(/таблиц|query|дебит|истори|управлен/.test(low)) task_patterns.push('извлечь таблицу');
if(/уточн|ambigu|clarif/.test(low)) task_patterns.push('clarification_needed');
const query=blob.trim().replace(/\b[\w.-]+\.(xlsx|xls|xlsm|inc|dev|txt|csv)\b/gi,' ').replace(/\s+/g,' ').trim().slice(0,800)||'excel protocol instruction';
const retrieval_selector={target_base:RAG_TARGET_BASE,knowledge_types:RAG_KNOWLEDGE_TYPES};
const schedule_retrieval_request={
  query,
  filters:{
    target_base:RAG_TARGET_BASE,
    access_scope:RAG_ACCESS_SCOPE,
    knowledge_types:RAG_KNOWLEDGE_TYPES,
    keyword_families:[],
    topics:[...new Set(topics)],
    task_patterns:[...new Set(task_patterns)]
  },
  top_k:RAG_TOP_K
};
// The LLM decides which table/columns fit: task text + workbook inventory + what the engineer already answered.
const engineerAnswers=Array.isArray(opened.engineer_answers)?opened.engineer_answers:[];
const reworkReason=String(opened.rework_reason||(task.inputs&&task.inputs.rework_reason)||'').trim();
const planner_input=JSON.stringify({
  objective,
  handoff_message:handoff,
  files:Array.isArray(opened.files)?opened.files:[],
  inspect:opened.inspect||{},
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
  agent_id:'excel_extractor',
  status:result.status||'needs_input',
  message:result.message||opened.message||'Нет Excel-файла для извлечения',
  data:result.data||{},
  artifacts:result.artifacts||{},
  issues:result.issues||[{type:'missing_excel'}],
  assumptions:result.assumptions||[],
  requests:result.requests||[{question_id:'Q-excel',question:'К задаче не приложен Excel-файл с данными. Приложите книгу .xlsx, из которой нужно взять скважины и даты.',options:[],accepts:{free_text:true,files:['xlsx']}}]
}}];
"""

FORMAT_RESULT = r"""
const fetched=$json||{};
const opened=$('Open excel session').first().json||{};
if(fetched.status){
  return [{json:{
    task_id:fetched.task_id||opened.task_id||'',
    agent_id:'excel_extractor',
    status:fetched.status,
    message:fetched.message||'',
    data:fetched.data||{},
    artifacts:fetched.artifacts||{},
    issues:fetched.issues||[],
    assumptions:fetched.assumptions||[],
    requests:fetched.requests||[]
  }}];
}
return [{json:{
  task_id:opened.task_id||'',
  agent_id:'excel_extractor',
  status:'failed',
  message:String(fetched.message||fetched.error||'Excel Extractor не вернул факты'),
  data:{},
  artifacts:{},
  issues:[{type:'excel_agent_no_result'}],
  assumptions:[],
  requests:[]
}}];
"""


def main() -> None:
    tools = []
    x = 760
    y = -420
    for i, (name, desc, fields) in enumerate(TOOLS):
        tools.append(tool_http(name, (x + (i % 4) * 220, y + (i // 4) * 160), desc, fields))

    nodes = [
        node(
            "edit after import",
            "n8n-nodes-base.stickyNote",
            1,
            (-220, -360),
            {
                "content": (
                    "## edit after import\n\n"
                    "**Agent — Excel Extractor** — один LLM + excel-tools FastAPI.\n\n"
                    "1. Bind **Qwen** credential on Excel Extractor Chat Model\n"
                    "2. Bind **Runtime configuration** → `MAS — Runtime Config` "
                    "(URL сервисов). Ключ Excel: credential Header Auth "
                    "**Excel Tools X-API-Key** (`X-API-Key`), не Set. "
                    "Activity events ключ Excel не используют.\n"
                    "3. Bind **Call Knowledge Retrieval** → `MAS — Knowledge Retrieval` "
                    "(срез `excel_protocol` / `protocol_instruction`).\n"
                    "4. Orchestrator — MAS вызывает этот workflow через "
                    "`executeWorkflow` (`Call Excel Extractor`). Webhook не нужен.\n\n"
                    "Файлы не грузятся в n8n. `open_session` сам забирает все Excel-вложения "
                    "кейса (`/cases/{id}/artifacts/excel`, `excel_1`, …) в одну сессию и "
                    "возвращает инвентарь (листы, таблицы, колонки, первые строки). "
                    "Никакого regex-роутера: LLM сам выбирает таблицу и колонки и вызывает "
                    "`extract_commissioning` / `extract_well_parameters` (детерминированное "
                    "извлечение, проверка колонок) или `ask_engineer` (вопрос прозой). "
                    "Инструменты — HTTP Request (as tool) с `$fromAI`. Результат читается "
                    "GET /sessions/{id}/result, сессия закрывается POST /sessions/{id}/close. "
                    "Скважины/даты не хардкодятся."
                ),
                "height": 380,
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
                            "task_id": "TASK-001",
                            "agent_id": "excel_extractor",
                            "objective": "Достань даты ввода скважин",
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
        node("Normalize excel task", "n8n-nodes-base.code", 2, (500, 0), {"jsCode": NORMALIZE}),
        http_json(
            "Open excel session",
            (740, 0),
            "POST",
            "={{ $json.excel_tools_url }}/agent-tools/open_session",
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
        node("Format missing excel", "n8n-nodes-base.code", 2, (1220, 240), {"jsCode": FORMAT_OPEN}),
        activity_event(
            "Activity — Excel Extractor accepted",
            (1220, -220),
            "agent.accepted",
            "Excel Extractor принял задачу и анализирует workbook",
        ),
        node(
            "Restore after Excel Extractor accepted",
            "n8n-nodes-base.code",
            2,
            (1440, -220),
            {"jsCode": "const x=$('Session ready?').first().json||{}; return [{json:x}];"},
        ),
        activity_event(
            "Activity — Excel Extractor progress",
            (1660, -220),
            "agent.progress",
            "Excel Extractor читает листы и таблицы приложенных книг.",
        ),
        node(
            "Restore after Excel Extractor progress",
            "n8n-nodes-base.code",
            2,
            (1880, -220),
            {"jsCode": "const x=$('Restore after Excel Extractor accepted').first().json||{}; return [{json:x}];"},
        ),
        node(
            "Restore after Excel Extractor activity",
            "n8n-nodes-base.code",
            2,
            (2760, -180),
            {"jsCode": "const x=$('Format excel result').first().json||{}; return [{json:x}];"},
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
            "Attach excel RAG evidence",
            "n8n-nodes-base.code",
            2,
            (1680, 300),
            {"jsCode": attach_excel_rag_js()},
        ),
        node(
            "Excel Extractor AI Agent",
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
            "Excel Extractor Chat Model — Qwen",
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
        activity_event_dynamic("Activity — Excel Extractor tools", (1960, 300)),
        node(
            "Restore after AI tools",
            "n8n-nodes-base.code",
            2,
            (2160, 300),
            {"jsCode": "const x=$('Summarize AI steps').first().json||{}; return [{json:x}];"},
        ),
        if_true("AI extracted?", (2160, 160), "={{ Boolean($json.skip_fetch) }}"),
        http_json(
            "Fetch excel result",
            (2100, 0),
            "GET",
            "={{ $('Runtime configuration').first().json.excel_tools_url + '/sessions/' + $('Open excel session').first().json.session_id + '/result' }}",
            None,
            timeout=120000,
            retry=True,
        ),
        node("Format excel result", "n8n-nodes-base.code", 2, (2320, 0), {"jsCode": FORMAT_RESULT}),
        http_json(
            "Close excel session",
            (2480, 0),
            "POST",
            "={{ $('Runtime configuration').first().json.excel_tools_url + '/sessions/' + $('Open excel session').first().json.session_id + '/close' }}",
            "={{ ({}) }}",
            timeout=5000,
        ),
        *tools,
    ]

    connections = {}
    connect(connections, "When executed by another workflow", "Runtime configuration")
    connect(connections, "Runtime configuration", "Normalize excel task")
    connect(connections, "Normalize excel task", "Open excel session")
    connect(connections, "Open excel session", "Session ready?")
    connect(connections, "Session ready?", "Activity — Excel Extractor accepted", si=0)
    connect(connections, "Activity — Excel Extractor accepted", "Restore after Excel Extractor accepted")
    connect(connections, "Restore after Excel Extractor accepted", "Activity — Excel Extractor progress")
    connect(connections, "Activity — Excel Extractor progress", "Restore after Excel Extractor progress")
    connect(connections, "Restore after Excel Extractor progress", "Prepare AI Agent input")
    connect(connections, "Session ready?", "Format missing excel", si=1)
    connect(connections, "Prepare AI Agent input", "Call Knowledge Retrieval")
    connect(connections, "Call Knowledge Retrieval", "Attach excel RAG evidence")
    connect(connections, "Attach excel RAG evidence", "Excel Extractor AI Agent")
    connect(connections, "Excel Extractor Chat Model — Qwen", "Excel Extractor AI Agent", out="ai_languageModel", tin="ai_languageModel")
    for name, _desc, _fields in TOOLS:
        connect(connections, name, "Excel Extractor AI Agent", out="ai_tool", tin="ai_tool")
    connect(connections, "Excel Extractor AI Agent", "Summarize AI steps")
    connect(connections, "Summarize AI steps", "Activity — Excel Extractor tools")
    connect(connections, "Activity — Excel Extractor tools", "Restore after AI tools")
    connect(connections, "Restore after AI tools", "AI extracted?")
    connect(connections, "AI extracted?", "Format excel result", si=0)
    connect(connections, "AI extracted?", "Fetch excel result", si=1)
    connect(connections, "Fetch excel result", "Format excel result")
    connect(connections, "Format excel result", "Close excel session")
    # agent.result is emitted once, by the orchestrator when it merges this result into case state.
    connect(connections, "Close excel session", "Restore after Excel Extractor activity")

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
