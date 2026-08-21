#!/usr/bin/env python3
"""Generate Agent — Excel Extractor: one LLM + excel-tools FastAPI tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options

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
- query_table — строки по table_id (limit, фильтры). Не выгружай всю книгу
- extract_commissioning — скважина+дата ввода из уже открытой сессии. Даты и скважины сам не выдумывай

Правила:
- Не пиши SCHEDULE / .INC. Только факты из Excel.
- Не придумывай скважины, даты, дебиты, имена листов и table_id.
- suggested_capability=commissioning → сразу extract_commissioning, затем STOP.
- Иначе introspect/detect → query нужных таблиц. Если нужны даты ввода — extract_commissioning.
- После extract_commissioning со status completed или needs_input — STOP.
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
        "Строки таблицы. select/filters — JSON с числовыми ключами 0,1,2,...",
        [
            ("table_id", "string", True, "table_id из detect_tables"),
            ("select", "json", False, "Числовые ключи → имена колонок"),
            ("filters", "json", False, "Числовые ключи → {field, operator, value}"),
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


def http_json(name, pos, method, url, body=None, timeout=180000):
    params = {
        "method": method,
        "url": url,
        "sendHeaders": True,
        "headerParameters": {
            "parameters": [
                {"name": "Content-Type", "value": "application/json"},
                {
                    "name": "X-API-Key",
                    "value": "={{ $('Runtime configuration').first().json.excel_tools_api_key || '' }}",
                },
            ]
        },
        "options": {"timeout": timeout, "response": {"response": {"fullResponse": False, "responseFormat": "json"}}},
    }
    if body is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = body
    return node(name, "n8n-nodes-base.httpRequest", 4.4, pos, params, onError="continueRegularOutput", alwaysOutputData=True)


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
            "authentication": "none",
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
                    {
                        "name": "X-API-Key",
                        "valueProvider": "fieldValue",
                        "value": "={{ $('Runtime configuration').first().json.excel_tools_api_key || '' }}",
                    },
                ]
            },
            "parametersBody": {"values": body_values},
        },
    )


NORMALIZE = r"""
const root=$json||{};
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
                    "2. Runtime configuration: `excel_tools_url` = "
                    "`http://excel-tools:18000`, API key, `activity_base_url`\n"
                    "3. Orchestrator — MAS вызывает этот workflow через "
                    "`executeWorkflow` (`Call Excel Extractor`). Webhook не нужен.\n\n"
                    "Файлы не грузятся в n8n. Сервис сам GET "
                    "`/cases/{id}/artifacts/excel`.\n"
                    "`suggested_capability=commissioning` идёт обычным HTTP "
                    "`extract_commissioning` (external n8n runners не execute'ят "
                    "toolHttpRequest). Скважины/даты не хардкодятся."
                ),
                "height": 340,
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
            "n8n-nodes-base.set",
            3.4,
            (260, 0),
            {
                "assignments": {
                    "assignments": [
                        {
                            "id": nid("cfg-svc"),
                            "name": "excel_tools_url",
                            "value": "http://excel-tools:18000",
                            "type": "string",
                        },
                        {
                            "id": nid("cfg-key"),
                            "name": "excel_tools_api_key",
                            "value": "",
                            "type": "string",
                        },
                        {
                            "id": nid("cfg-act"),
                            "name": "activity_base_url",
                            "value": "http://mas-activity:8200",
                            "type": "string",
                        },
                    ]
                },
                "options": {},
                "includeOtherFields": True,
            },
        ),
        node("Normalize excel task", "n8n-nodes-base.code", 2, (500, 0), {"jsCode": NORMALIZE}),
        http_json(
            "Open excel session",
            (740, 0),
            "POST",
            "={{ $json.excel_tools_url }}/agent-tools/open_session",
            "={{ $json.agent_task }}",
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
        node(
            "Capability router",
            "n8n-nodes-base.switch",
            3.4,
            (1220, 0),
            {
                "mode": "expression",
                "numberOutputs": 2,
                "output": "={{ ({commissioning:0, operations:1})[$json.suggested_capability] ?? 1 }}",
            },
        ),
        http_json(
            "Extract commissioning",
            (1480, -160),
            "POST",
            "={{ $('Runtime configuration').first().json.excel_tools_url + '/agent-tools/extract_commissioning' }}",
            "={{ ({session_id: $('Open excel session').first().json.session_id}) }}",
            timeout=180000,
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
                    "maxIterations": 12,
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
        http_json(
            "Fetch excel result",
            (1960, 0),
            "GET",
            "={{ $('Runtime configuration').first().json.excel_tools_url + '/sessions/' + $('Open excel session').first().json.session_id + '/result' }}",
            None,
            timeout=120000,
        ),
        node("Format excel result", "n8n-nodes-base.code", 2, (2200, 0), {"jsCode": FORMAT_RESULT}),
        *tools,
    ]

    connections = {}
    connect(connections, "When executed by another workflow", "Runtime configuration")
    connect(connections, "Runtime configuration", "Normalize excel task")
    connect(connections, "Normalize excel task", "Open excel session")
    connect(connections, "Open excel session", "Session ready?")
    connect(connections, "Session ready?", "Capability router", si=0)
    connect(connections, "Session ready?", "Format missing excel", si=1)
    connect(connections, "Capability router", "Extract commissioning", si=0)
    connect(connections, "Capability router", "Prepare AI Agent input", si=1)
    connect(connections, "Extract commissioning", "Fetch excel result")
    connect(connections, "Prepare AI Agent input", "Excel Extractor AI Agent")
    connect(connections, "Excel Extractor Chat Model — Qwen", "Excel Extractor AI Agent", out="ai_languageModel", tin="ai_languageModel")
    for name, _desc, _fields in TOOLS:
        connect(connections, name, "Excel Extractor AI Agent", out="ai_tool", tin="ai_tool")
    connect(connections, "Excel Extractor AI Agent", "Fetch excel result")
    connect(connections, "Fetch excel result", "Format excel result")

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
