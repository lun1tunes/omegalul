#!/usr/bin/env python3
"""Generate Agent — Excel Extractor: one LLM + excel-tools FastAPI tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options
from generate_mas_runtime_config import EXCEL_KEY_CRED, runtime_config_execute_params

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

SYSTEM = """Ты — единственный LLM-решатель агента Excel Extractor.

Workbook тебе не показывают. Он лежит в сессии FastAPI. session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Инструменты:
- workbook_introspect — листы и размеры (компактно)
- sheet_preview — небольшой прямоугольник одного листа, не весь лист
- detect_tables — найти таблицы, вернуть table_id / range / columns
- match_tables — ранжировать таблицы по запросу
- describe_table — колонки и sample по table_id
- list_column_values — ограниченные distinct-значения колонки
- query_table — строки по table_id (limit, фильтры). select и filters — JSON-массивы, не объект с ключами "0","1". Не выгружай всю книгу
- extract_commissioning — скважина+дата ввода из уже открытой сессии. Даты и скважины сам не выдумывай

Правила:
- Не пиши SCHEDULE / .INC. Только факты из Excel.
- Не придумывай скважины, даты, дебиты, имена листов и table_id.
- Проанализируй objective и handoff_message.
- Если задача про даты ввода скважин → extract_commissioning один раз, затем STOP.
- Если задача про дебиты, управления, историю, PVT, SCAL → introspect/detect/query. Не вызывай extract_commissioning, если даты ввода не нужны.
- После извлечения данных (extract_commissioning или query_table) — STOP.
- Если за 3 шага нет прогресса — STOP и верни вопрос. Не вызывай один tool больше трёх раз подряд.
- Если файла/таблиц нет — не выдумывай строки.

Заверши одним коротким фактическим предложением по-русски.
"""

TOOLS = [
    ("workbook_introspect", "Компактные листы и размеры workbook. Без файла.", []),
    (
        "sheet_preview",
        "Небольшой preview одного листа. sheet только из introspect.",
        [("sheet", "string", True, "Точное имя листа из introspect")],
    ),
    (
        "detect_tables",
        "Найти таблицы. sheet опционален. Возвращает table_id, range, columns — не workbook.",
        [("sheet", "string", False, "Ограничить поиск одним листом")],
    ),
    (
        "match_tables",
        "Ранжировать уже найденные или подходящие таблицы по фразе запроса.",
        [("query", "string", True, "Что искать: даты ввода, дебиты, ...")],
    ),
    (
        "describe_table",
        "Колонки, число строк и sample. table_id только из detect/match.",
        [("table_id", "string", True, "table_id из detect_tables")],
    ),
    (
        "list_column_values",
        "Ограниченные distinct-значения одной колонки для точных фильтров.",
        [
            ("table_id", "string", True, "table_id из detect_tables"),
            ("column", "string", True, "Точное имя колонки из describe/detect"),
        ],
    ),
    (
        "query_table",
        "Строки таблицы. select и filters — JSON-массивы: [\"well\",\"date\"] и [{\"field\":\"well\",\"operator\":\"eq\",\"value\":\"101\"}].",
        [
            ("table_id", "string", True, "table_id из detect_tables"),
            ("select", "json", False, "Массив имён колонок, например [\"well\",\"date\"]"),
            ("filters", "json", False, "Массив {field, operator, value}"),
            ("limit", "string", False, "Лимит строк, по умолчанию 200"),
        ],
    ),
    (
        "extract_commissioning",
        "Извлечь факты скважина+дата ввода из сессии. Аргументы не передавай.",
        [],
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


def activity_result_event(name, pos):
    body = (
        "={{ ({"
        "kind: 'agent.result', actor: 'excel_extractor', agent_id: 'excel_extractor', "
        "task_id: $('Normalize excel task').first().json.agent_task.task_id, "
        "status: String($('Format excel result').first().json.status || 'failed'), "
        "status_message: String($('Format excel result').first().json.message || 'Excel Extractor завершил обработку workbook.'), "
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
    placeholders = {
        "values": [{"name": key, "description": desc, "type": typ} for key, typ, _req, desc in fields]
    }
    body_values = [
        {
            "name": "session_id",
            "valueProvider": "fieldValue",
            "value": "={{ $('Open excel session').first().json.session_id }}",
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
            "url": "={{ $('Runtime configuration').first().json.excel_tools_url + '/agent-tools/' + "
            + json.dumps(name)
            + " }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendQuery": False,
            "sendHeaders": True,
            "specifyHeaders": "keypair",
            "sendBody": True,
            "specifyBody": "keypair",
            "placeholderDefinitions": placeholders,
            "optimizeResponse": False,
            "parametersHeaders": {
                "values": [
                    {"name": "Content-Type", "valueProvider": "fieldValue", "value": "application/json"},
                ]
            },
            "parametersBody": {"values": body_values},
        },
        credentials=EXCEL_KEY_CRED,
        retryOnFail=True,
        maxTries=3,
        waitBetweenTries=2000,
    )


NORMALIZE = r"""
const incoming=(()=>{try{return $('When executed by another workflow').first().json||{}}catch{return $json||{}}})();
const root=incoming&&typeof incoming==='object'?incoming:{};
const body=root.body&&typeof root.body==='object'?root.body:root;
const packet=body.specialist_packet&&typeof body.specialist_packet==='object'?body.specialist_packet:null;
let task=body.agent_task&&typeof body.agent_task==='object'?body.agent_task:(body.case_id||body.task_id||body.objective?body:null);
if(!task||typeof task!=='object') task={};
if(packet){
  const inputs=packet.inputs&&typeof packet.inputs==='object'?packet.inputs:{};
  task={
    case_id:String(packet.case_id||body.case_id||task.case_id||''),
    task_id:String(packet.task_id||task.task_id||''),
    agent_id:'excel_extractor',
    objective:String(packet.objective||task.objective||''),
    handoff_message:String(packet.handoff_message||task.handoff_message||packet.objective||''),
    inputs,
    context:packet.context&&typeof packet.context==='object'?packet.context:(task.context||{}),
    constraints:packet.controls&&typeof packet.controls==='object'?packet.controls:(task.constraints||{})
  };
}
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

RESTORE_AFTER_PROGRESS = r"""
const x=$('Restore after Excel Extractor accepted').first().json||{};
const cap=String(x.suggested_capability||'').trim();
const known=cap==='commissioning'?cap:'operations';
return [{json:{...x, suggested_capability:known}}];
"""

DESCRIBE_EXTRACT = r"""
const raw=$json||{};
const data=raw.data&&typeof raw.data==='object'?raw.data:{};
const facts=Array.isArray(data.facts)?data.facts:[];
const n=Number(data.total_count!=null?data.total_count:facts.length);
const httpError=raw.error&&typeof raw.error==='object'?raw.error:null;
const status=String(raw.status||(httpError?'failed':'')).trim();
const skipFetch=status!=='completed';
const fallback=status==='completed'
  ? ('Commissioning: извлечено '+n+' скважин с датами ввода')
  : (status==='needs_input'?'Commissioning требует уточнения':'Извлечение commissioning не удалось');
const message=String(raw.message||(httpError&&(httpError.message||httpError.description))||fallback);
return [{json:{
  ...raw,
  status:status||'failed',
  message,
  skip_fetch:skipFetch,
  activity_kind:status==='completed'?'agent.progress':(status==='needs_input'?'agent.progress':'agent.failed'),
  status_message:message,
  activity_payload:{
    source:'excel-extractor-agent-workflow',
    operation:'extract_commissioning',
    wells_extracted:n,
    status:status||'failed'
  }
}}];
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
const hasExtraction=tools.some(n=>n==='extract_commissioning'||n==='query_table');
const skipFetch=!hasExtraction||repeated;
const message=hasExtraction&&!repeated
  ? ('Excel Extractor вызвал '+String(tools.length)+' tools: '+unique.join(', '))
  : (repeated
    ? 'Агент повторял один и тот же tool без прогресса. Нужно уточнение.'
    : 'Агент не извлёк данных. Уточните, какие данные нужны.');
return [{json:{
  ...(skipFetch?{
    task_id:opened.task_id||'',
    agent_id:'excel_extractor',
    status:'needs_input',
    message,
    data:{tools_used:unique,total_calls:steps.length},
    artifacts:{},
    issues:[{type:repeated?'repeated_tools':'no_extract'}],
    assumptions:[],
    requests:[{question_id:'Q-clarify',question:message,options:['Даты ввода','Дебиты','Управления','Другое']}]
  }:agent),
  skip_fetch:skipFetch,
  has_extraction:hasExtraction,
  activity_kind:'agent.progress',
  status_message:message,
  activity_payload:{source:'excel-extractor-agent-workflow',total_calls:steps.length,tools_used:unique,iterations:steps.length}
}}];
"""

PREPARE = r"""
const opened=$json||{};
const task=$('Normalize excel task').first().json.agent_task||{};
const agent_input=JSON.stringify({
  objective:opened.objective||task.objective||'',
  handoff_message:opened.handoff_message||task.handoff_message||'',
  inspect:opened.inspect||{},
  file_name:opened.file_name||'',
  suggested_capability:opened.suggested_capability||''
});
return [{json:{...opened, session_id:opened.session_id, agent_input}}];
"""

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
  requests:result.requests||[{question_id:'Q-excel',question:'Приложите workbook .xlsx',options:[]}]
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
                    "3. Orchestrator — MAS вызывает этот workflow через "
                    "`executeWorkflow` (`Call Excel Extractor`). Webhook не нужен.\n\n"
                    "Файлы не грузятся в n8n. Сервис сам GET "
                    "`/cases/{id}/artifacts/excel`.\n"
                    "`suggested_capability=commissioning` идёт обычным HTTP "
                    "`extract_commissioning` (external n8n runners не execute'ят "
                    "toolHttpRequest). Остальное — LLM + query_table. "
                    "После extract проверяется status; needs_input не идёт в "
                    "Fetch result. Сессия закрывается POST /sessions/{id}/close. "
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
            "Excel Extractor проверяет листы и таблицы workbook.",
        ),
        node(
            "Restore after Excel Extractor progress",
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
                "numberOutputs": 2,
                "output": "={{ ({commissioning:0, operations:1})[String($json.suggested_capability||'operations')] ?? 1 }}",
            },
        ),
        http_json(
            "Extract commissioning",
            (1480, -160),
            "POST",
            "={{ $('Runtime configuration').first().json.excel_tools_url + '/agent-tools/extract_commissioning' }}",
            "={{ ({session_id: $('Open excel session').first().json.session_id}) }}",
            timeout=180000,
            retry=True,
        ),
        node("Describe extract result", "n8n-nodes-base.code", 2, (1680, -80), {"jsCode": DESCRIBE_EXTRACT}),
        activity_event_dynamic("Activity — Excel Extractor extract", (1680, -260)),
        node(
            "Restore after extract event",
            "n8n-nodes-base.code",
            2,
            (1880, -260),
            {"jsCode": "const x=$('Describe extract result').first().json||{}; return [{json:x}];"},
        ),
        if_true("Extract finished?", (1880, -80), "={{ Boolean($json.skip_fetch) }}"),
        activity_result_event(
            "Activity — Excel Extractor completed",
            (2560, -180),
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
            "Excel Extractor AI Agent",
            "@n8n/n8n-nodes-langchain.agent",
            3.1,
            (1720, 160),
            {
                "promptType": "define",
                "text": "={{ $json.agent_input }}",
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
    connect(connections, "Restore after Excel Extractor progress", "Capability router")
    connect(connections, "Session ready?", "Format missing excel", si=1)
    connect(connections, "Capability router", "Extract commissioning", si=0)
    connect(connections, "Capability router", "Prepare AI Agent input", si=1)
    connect(connections, "Extract commissioning", "Describe extract result")
    connect(connections, "Describe extract result", "Activity — Excel Extractor extract")
    connect(connections, "Activity — Excel Extractor extract", "Restore after extract event")
    connect(connections, "Restore after extract event", "Extract finished?")
    connect(connections, "Extract finished?", "Format excel result", si=0)
    connect(connections, "Extract finished?", "Fetch excel result", si=1)
    connect(connections, "Prepare AI Agent input", "Excel Extractor AI Agent")
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
    connect(connections, "Close excel session", "Activity — Excel Extractor completed")
    connect(connections, "Activity — Excel Extractor completed", "Restore after Excel Extractor activity")

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
