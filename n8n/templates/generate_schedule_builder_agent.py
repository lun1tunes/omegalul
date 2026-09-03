#!/usr/bin/env python3
"""Generate Agent — Schedule Builder: one LLM + FastAPI keyword tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import chat_model_options
from generate_mas_runtime_config import runtime_config_execute_params

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
- get_keyword — объектная модель полей и методов
- list_records — компактные записи keyword/well
- apply_commissioning — сдвиг дат ввода по фактам Excel из сессии. Якорь commissioning — первый нефактический WCONPROD; последующие WCONPROD являются прогнозными режимами и сохраняются.
- apply_group_rebind — перепривязка групп по тексту задачи и baseline. Скважины только из inspect.
- apply_operations — точечные MODIFY/ADD по схеме keyword. wells только из inspect.
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
- Иначе search_keywords → get_keyword → apply_operations / needs_input.
- После apply_* со status completed или needs_input — STOP. Не вызывай build, если apply уже вернул результат.
- Если данных нет — не вызывай apply с пустыми выдуманными полями.

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
        "Объектная модель keyword: fields + methods. Бери имена полей отсюда, не выдумывай.",
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
        "Применить operations. JSON-объект с числовыми ключами: {\"0\":{keyword,operation,fields}}.",
        [("operations", "json", True, "Числовые ключи → {keyword, operation, fields}")],
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


def http_json(name, pos, method, url, body=None, timeout=180000):
    params = {
        "method": method,
        "url": url,
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
        "options": {"timeout": timeout, "response": {"response": {"fullResponse": False, "responseFormat": "json"}}},
    }
    if body is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = body
    return node(name, "n8n-nodes-base.httpRequest", 4.4, pos, params, onError="continueRegularOutput", alwaysOutputData=True)


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
        timeout=30000,
    )


def activity_result_event(name, pos):
    body = (
        "={{ ({"
        "kind: 'agent.result', actor: 'schedule_builder', agent_id: 'schedule_builder', "
        "task_id: $('Normalize schedule task').first().json.agent_task.task_id, "
        "status: String($('Format schedule result').first().json.status || 'failed'), "
        "status_message: String($('Format schedule result').first().json.message || 'Schedule Builder завершил обработку schedule.'), "
        "payload: {source: 'schedule-builder-agent-workflow'}"
        "}) }}"
    )
    return http_json(
        name,
        pos,
        "POST",
        "={{ $('Runtime configuration').first().json.activity_base_url + '/cases/' + $('Normalize schedule task').first().json.agent_task.case_id + '/events' }}",
        body,
        timeout=30000,
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
    agent_id:'schedule_builder',
    objective:String(packet.objective||task.objective||''),
    handoff_message:String(packet.handoff_message||task.handoff_message||packet.objective||''),
    inputs,
    context:packet.context&&typeof packet.context==='object'?packet.context:(task.context||{}),
    constraints:packet.controls&&typeof packet.controls==='object'?packet.controls:(task.constraints||{})
  };
}
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

PREPARE = r"""
const opened=$json||{};
const task=$('Normalize schedule task').first().json.agent_task||{};
const agent_input=JSON.stringify({
  objective:opened.objective||task.objective||'',
  handoff_message:opened.handoff_message||task.handoff_message||'',
  inspect:opened.inspect||{},
  fact_count:opened.fact_count||0,
  facts_preview:opened.facts_preview||[],
  suggested_capability:opened.suggested_capability||''
});
return [{json:{...opened, session_id:opened.session_id, agent_input}}];
"""

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
if(fetched.status){
  return [{json:{
    task_id:fetched.task_id||opened.task_id||'',
    agent_id:'schedule_builder',
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
                    "3. Orchestrator — MAS вызывает этот workflow через "
                    "`executeWorkflow` (`Call Schedule Builder`), как Excel Extractor. "
                    "Webhook не нужен.\n\n"
                    "LLM не пишет .INC. parse/apply/emit остаются в сервисе.\n"
                    "`suggested_capability` commissioning/group_rebind идут "
                    "обычным HTTP `apply_*` (external n8n runners не execute'ят "
                    "toolHttpRequest). Скважины/даты не хардкодятся — только "
                    "факты сессии и текст задачи."
                ),
                "height": 320,
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
            "Capability router",
            "n8n-nodes-base.switch",
            3.4,
            (1220, 0),
            {
                "mode": "expression",
                "numberOutputs": 3,
                "output": "={{ ({commissioning:0, group_rebind:1, operations:2})[$json.suggested_capability] ?? 2 }}",
            },
        ),
        http_json(
            "Apply commissioning",
            (1480, -160),
            "POST",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/agent-tools/apply_commissioning' }}",
            "={{ ({session_id: $('Open schedule session').first().json.session_id}) }}",
            timeout=180000,
        ),
        http_json(
            "Apply group rebind",
            (1480, 0),
            "POST",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/agent-tools/apply_group_rebind' }}",
            "={{ ({session_id: $('Open schedule session').first().json.session_id}) }}",
            timeout=180000,
        ),
        activity_result_event(
            "Activity — Schedule Builder completed",
            (2200, -180),
        ),
        node(
            "Restore after Schedule Builder activity",
            "n8n-nodes-base.code",
            2,
            (2440, -180),
            {"jsCode": "const x=$('Format schedule result').first().json||{}; return [{json:x}];"},
        ),
        node("Prepare AI Agent input", "n8n-nodes-base.code", 2, (1480, 160), {"jsCode": PREPARE}),
        node(
            "Schedule Builder AI Agent",
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
        http_json(
            "Fetch schedule result",
            (1960, 0),
            "GET",
            "={{ $('Runtime configuration').first().json.schedule_service_url + '/sessions/' + $('Open schedule session').first().json.session_id + '/result' }}",
            None,
            timeout=120000,
        ),
        node("Format schedule result", "n8n-nodes-base.code", 2, (2200, 0), {"jsCode": FORMAT_RESULT}),
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
    connect(connections, "Apply commissioning", "Fetch schedule result")
    connect(connections, "Apply group rebind", "Fetch schedule result")
    connect(connections, "Prepare AI Agent input", "Schedule Builder AI Agent")
    connect(connections, "Schedule Builder Chat Model — Qwen", "Schedule Builder AI Agent", out="ai_languageModel", tin="ai_languageModel")
    for name, _desc, _fields in TOOLS:
        connect(connections, name, "Schedule Builder AI Agent", out="ai_tool", tin="ai_tool")
    connect(connections, "Schedule Builder AI Agent", "Fetch schedule result")
    connect(connections, "Fetch schedule result", "Format schedule result")
    connect(connections, "Format schedule result", "Activity — Schedule Builder completed")
    connect(connections, "Activity — Schedule Builder completed", "Restore after Schedule Builder activity")

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
