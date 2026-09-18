#!/usr/bin/env python3
"""Build the n8n workflow of an LLM agent from its ``AgentSpec``.

    from agents.excel_extractor import SPEC
    from mas_agent_workflow import write_workflow
    write_workflow(SPEC)          # → n8n/workflows/core/<agent-id>-agent.workflow.json

Every agent workflow has the same nodes (names differ only by ``spec.title`` / ``spec.slug``):

    When executed by another workflow → Runtime configuration → Normalize <slug> task
    → Open <slug> session → Session ready?
        ├─ no  → Format missing <slug>
        └─ yes → Activity — <Title> accepted → Activity — <Title> progress → Prepare AI Agent input
                 → Call Knowledge Retrieval → Attach <slug> RAG evidence → Activity — <Title> RAG
                 → Restore after <Title> RAG → Build chat request → <Title> Agent chat
                 → Parse agent chat → loop (tool / retrieve_knowledge / next chat)
                 → Summarize AI steps → Fetch <slug> result → Format <slug> result
                 → Close <slug> session

``llm_transport=n8n_agent`` keeps the legacy LangChain Agent + httpRequestTool graph for one-revision rollback.

The service URL comes from ``MAS — Runtime Config`` (``spec.service_url_key``).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from generate_mas_error_traces import WF_ID as ERROR_WF_ID
from generate_mas_runtime_config import runtime_config_execute_params
from llm_runtime_options import (
    CHAT_THINKING_OFF,
    LLM_HTTP_TIMEOUT_MS,
    PARSE_CHAT_EXTRA_JS,
    PREVIEW_CHAT_MESSAGES_JS,
    SAMPLING,
    chat_model_options,
)
from mas_agent_spec import AgentSpec, ToolField
from mas_retrieval_client import SELECTORS, RAG_HELPERS_JS, attach_retrieval_js, knowledge_retrieval_execute_params
from mas_tool_nodes import HTTP_REQUEST_TOOL_TYPE, HTTP_REQUEST_TOOL_VERSION, http_request_tool_params

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "workflows/core"
CHAT_MODEL_CREDENTIAL = {"openAiApi": {"id": "REPLACE_IN_UI", "name": "REPLACE: Qwen OpenAI-compatible agent credential"}}

RETRIEVE_KNOWLEDGE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "retrieve_knowledge",
        "description": (
            "Найти карточки в базе знаний по запросу. Вызови, когда в текущем срезе нет инструкции "
            "по нужному keyword или протоколу — особенно перед первым apply_dataset / apply_operations "
            "/ extract_table по ним. keywords — имена keyword через запятую."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос (русская фраза или имена keyword)"},
                "keywords": {"type": "string", "description": "Необязательно: семейства keyword через запятую"},
            },
            "required": ["query"],
        },
    },
}

LOOP_HELPERS_JS = r"""
function fromNode(name){
  try{
    const n=$(name);
    if(n&&n.item&&n.item.json) return n.item.json;
    if(n&&typeof n.last==='function'){const x=n.last(); if(x&&x.json) return x.json;}
    if(n&&typeof n.first==='function'){const x=n.first(); if(x&&x.json) return x.json;}
  }catch(e){}
  return {};
}
function obj(v){return v&&typeof v==='object'&&!Array.isArray(v);}
function stripThink(text){return String(text||'').replace(/<think>[\s\S]*?<\/think>/gi,' ').replace(/\s+/g,' ').trim();}
function clip(text,n){const s=String(text||''); return s.length<=n?s:s.slice(0,n-1)+'…';}
function parseArgs(raw){
  if(obj(raw)) return raw;
  const s=String(raw||'').trim();
  if(!s) return {};
  try{const p=JSON.parse(s); return obj(p)?p:{};}catch(e){return {};}
}
function looksMachine(text){
  const s=String(text||'');
  if(!s.trim()) return false;
  return /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/.test(s)||/\bbaseline\b/i.test(s)||!/[А-Яа-яЁё]{3,}/.test(s);
}
function loopRouteForPending(pending, over){
  const list=Array.isArray(pending)?pending:[];
  if(list.length){
    const name=String((list[0]&&list[0].name)||'');
    return name==='retrieve_knowledge'?'retrieve':'tool';
  }
  return over?'done':'chat';
}
function toolTransportCode(http){
  if(!http||typeof http.ok==='boolean') return '';
  const err=obj(http.error)?http.error:(typeof http.error==='string'?{message:http.error}:null);
  const status=Number(http.statusCode||http.status||(err&&err.status)||0);
  const blob=String((err&&(err.httpCode||err.code||err.message||err.description))||'').toLowerCase();
  if(/econnrefused|enotfound|etimedout|econnreset|ehostunreach|socket hang|connection|offline|unreachable/.test(blob)) return 'service_unreachable';
  if(status>=500) return 'service_unreachable';
  if(err&&(status===0||!status)&&!http.choices) return 'service_unreachable';
  if(err||status>=400) return 'tool_http_failed';
  return '';
}
""" + PARSE_CHAT_EXTRA_JS + "\n" + PREVIEW_CHAT_MESSAGES_JS


def openai_function_tool(name: str, description: str, fields: list[ToolField]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, typ, req, desc in fields:
        json_type = "number" if typ == "number" else "string"
        extra = " Передай JSON-строкой." if typ == "json" else ""
        properties[key] = {"type": json_type, "description": f"{desc}{extra}"}
        if req:
            required.append(key)
    parameters: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": True}
    if required:
        parameters["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def openai_tools_for_spec(spec: AgentSpec) -> list[dict[str, Any]]:
    tools = [openai_function_tool(name, desc, fields) for name, desc, fields in spec.tools]
    tools.append(dict(RETRIEVE_KNOWLEDGE_TOOL))
    return tools


class AgentWorkflow:
    """Nodes + connections for one spec. ``build()`` returns the importable workflow JSON."""

    def __init__(self, spec: AgentSpec):
        spec.validate()
        if not spec.has_workflow:
            raise ValueError(f"{spec.agent_id} is an HTTP-only agent: nothing to generate")
        self.spec = spec
        self.ns = spec.node_id_namespace or f"mas-agent:{spec.agent_id}"
        self.nodes: list[dict[str, Any]] = []
        self.connections: dict[str, Any] = {}

    # -- names ---------------------------------------------------------------------------------

    @property
    def n(self) -> dict[str, str]:
        t, s = self.spec.title, self.spec.slug
        return {
            "trigger": "When executed by another workflow",
            "config": "Runtime configuration",
            "normalize": f"Normalize {s} task",
            "open": f"Open {s} session",
            "ready": "Session ready?",
            "missing": f"Format missing {s}",
            "accepted": f"Activity — {t} accepted",
            "restore_accepted": f"Restore after {t} accepted",
            "progress": f"Activity — {t} progress",
            "restore_progress": f"Restore after {t} progress",
            "prepare": "Prepare AI Agent input",
            "rag": "Call Knowledge Retrieval",
            "attach": f"Attach {s} RAG evidence",
            "rag_event": f"Activity — {t} RAG",
            "restore_rag": f"Restore after {t} RAG",
            "build": "Build chat request",
            "chat": f"{t} Agent chat",
            "parse": "Parse agent chat",
            "llm_event": f"Activity — {t} LLM",
            "restore_llm": f"Restore after {t} LLM",
            "router": "Agent loop router",
            "prep_tool": "Prepare tool call",
            "skip_tool": "Skip unknown tool?",
            "call_tool": "Call agent tool",
            "append_tool": "Append tool result",
            "prep_retrieve": "Prepare retrieve request",
            "retrieve": "Retrieve knowledge",
            "attach_retrieve": "Attach retrieve evidence",
            "retrieve_event": f"Activity — {t} retrieve",
            "restore_retrieve": f"Restore after {t} retrieve",
            "agent": f"{t} AI Agent",
            "model": f"{t} Chat Model — Qwen",
            "summarize": "Summarize AI steps",
            "tools_event": f"Activity — {t} tools",
            "restore_tools": "Restore after AI tools",
            "stored": "Result stored?",
            "fetch": f"Fetch {s} result",
            "format": f"Format {s} result",
            "close": f"Close {s} session",
            "restore_final": f"Restore after {t} activity",
        }

    # -- node helpers --------------------------------------------------------------------------

    def nid(self, name: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.ns}:{name}"))

    @staticmethod
    def ref(name: str) -> str:
        """``$('Node name')`` — how n8n expressions and Code nodes read another node's output."""
        return "$('" + name.replace("'", "\\'") + "')"

    def node(self, name: str, ntype: str, ver: float | int, pos: tuple[int, int], params: dict[str, Any], **extra: Any) -> dict[str, Any]:
        out = {"parameters": params, "id": self.nid(name), "name": name, "type": ntype, "typeVersion": ver, "position": list(pos)}
        out.update(extra)
        self.nodes.append(out)
        return out

    def code(self, name: str, pos: tuple[int, int], js: str) -> dict[str, Any]:
        return self.node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})

    def restore(self, name: str, pos: tuple[int, int], source: str) -> dict[str, Any]:
        """Activity HTTP nodes replace the item; a Restore node puts the previous item back."""
        return self.code(name, pos, f"const x={self.ref(source)}.first().json||{{}}; return [{{json:x}}];")

    def connect(self, src: str, dst: str, *, out: str = "main", si: int = 0, tin: str = "main") -> None:
        outputs = self.connections.setdefault(src, {}).setdefault(out, [])
        while len(outputs) <= si:
            outputs.append([])
        outputs[si].append({"node": dst, "type": tin, "index": 0})

    def if_true(self, name: str, pos: tuple[int, int], left: str) -> dict[str, Any]:
        return self.node(
            name,
            "n8n-nodes-base.if",
            2.3,
            pos,
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                    "conditions": [{"id": self.nid(f"{name}-cond"), "leftValue": left, "rightValue": True, "operator": {"type": "boolean", "operation": "true"}}],
                    "combinator": "and",
                },
                "options": {},
            },
        )

    def http_json(self, name: str, pos: tuple[int, int], method: str, url: str, body: str | None = None, *, timeout: int = 180000, activity: bool = False, retry: bool = False, never_error: bool = False) -> dict[str, Any]:
        """HTTP Request 4.4 with JSON in/out. ``activity=True``: feed line, never fails, 2 s budget, no service auth."""
        response: dict[str, Any] = {"fullResponse": False, "responseFormat": "json"}
        if activity or never_error:
            response["neverError"] = True
            if activity:
                timeout = min(timeout, 2000)
        params: dict[str, Any] = {
            "method": method,
            "url": url,
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
            "options": {"timeout": timeout, "response": {"response": response}},
        }
        extra: dict[str, Any] = {"onError": "continueRegularOutput", "alwaysOutputData": True}
        if not activity and self.spec.service_credentials:
            params["authentication"] = "genericCredentialType"
            params["genericAuthType"] = "httpHeaderAuth"
            extra["credentials"] = self.spec.service_credentials
        if body is not None:
            params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        if retry:
            extra.update({"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000})
        return self.node(name, "n8n-nodes-base.httpRequest", 4.4, pos, params, **extra)

    def openai_chat_http(self, name: str, pos: tuple[int, int]) -> dict[str, Any]:
        """OpenAI-compatible /chat/completions — same credential and URL pattern as orchestrator Decision chat."""
        return self.node(
            name,
            "n8n-nodes-base.httpRequest",
            4.4,
            pos,
            {
                "method": "POST",
                "url": "={{ $json.chat_url }}",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "openAiApi",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ $json.chat_request }}",
                "options": {
                    "timeout": LLM_HTTP_TIMEOUT_MS,
                    "response": {"response": {"fullResponse": False, "neverError": True}},
                },
            },
            credentials=CHAT_MODEL_CREDENTIAL,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=2000,
            onError="continueRegularOutput",
            alwaysOutputData=True,
        )

    # -- expressions ---------------------------------------------------------------------------

    @property
    def service_url(self) -> str:
        return f"$('Runtime configuration').first().json.{self.spec.service_url_key}"

    @property
    def session_id_expr(self) -> str:
        return f"{self.ref(self.n['open'])}.first().json.session_id"

    @property
    def events_url(self) -> str:
        return (
            "={{ $('Runtime configuration').first().json.activity_base_url + '/cases/' + "
            f"{self.ref(self.n['normalize'])}.first().json.agent_task.case_id + '/events' }}}}"
        )

    def _event_body(self, kind_js: str, status_js: str, message_js: str, payload_js: str) -> str:
        aid = json.dumps(self.spec.agent_id)
        return (
            "={{ ({"
            f"kind: {kind_js}, actor: {aid}, agent_id: {aid}, "
            f"task_id: {self.ref(self.n['normalize'])}.first().json.agent_task.task_id, "
            f"status: {status_js}, status_message: {message_js}, payload: {payload_js}"
            "}) }}"
        )

    @property
    def source_tag(self) -> str:
        return f"{self.spec.agent_id.replace('_', '-')}-agent-workflow"

    @property
    def exec_ref_js(self) -> str:
        """Developer log: Activity maps ``execution_id`` → case, so the Error Trigger of this workflow
        can attribute a failed node to the case (``GET /cases/{id}/log``)."""
        return "execution_id: String($execution.id || ''), workflow_id: String($workflow.id || ''), workflow_name: String($workflow.name || '')"

    def activity_event(self, name: str, pos: tuple[int, int], kind: str, message: str) -> dict[str, Any]:
        payload = f"{{source: {json.dumps(self.source_tag)}, {self.exec_ref_js}}}"
        body = self._event_body(json.dumps(kind), "'running'", json.dumps(message, ensure_ascii=False), payload)
        return self.http_json(name, pos, "POST", self.events_url, body, timeout=2000, activity=True)

    def activity_event_dynamic(self, name: str, pos: tuple[int, int]) -> dict[str, Any]:
        body = self._event_body(
            "$json.activity_kind || 'agent.progress'",
            "$json.status || 'running'",
            "$json.status_message || ''",
            f"Object.assign({{source: {json.dumps(self.source_tag)}}}, $json.activity_payload || {{}}, {{{self.exec_ref_js}}})",
        )
        return self.http_json(name, pos, "POST", self.events_url, body, timeout=2000, activity=True)

    def tool_http(self, name: str, pos: tuple[int, int], description: str, fields: list[tuple[str, str, bool, str]]) -> dict[str, Any]:
        params = http_request_tool_params(
            name,
            description,
            fields,
            url_expr=f"={{{{ {self.service_url} + '/agent-tools/' + {json.dumps(name)} }}}}",
            session_expr=self.session_id_expr,
        )
        extra: dict[str, Any] = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000}
        if self.spec.service_credentials:
            params.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
            extra["credentials"] = self.spec.service_credentials
        return self.node(name, HTTP_REQUEST_TOOL_TYPE, HTTP_REQUEST_TOOL_VERSION, pos, params, **extra)

    # -- Code node sources ---------------------------------------------------------------------

    def js_normalize(self) -> str:
        return (
            "const incoming=(()=>{try{return $('When executed by another workflow').first().json||{}}catch{return $json||{}}})();\n"
            "const root=incoming&&typeof incoming==='object'?incoming:{};\n"
            "const body=root.body&&typeof root.body==='object'?root.body:root;\n"
            "let task=body.agent_task&&typeof body.agent_task==='object'?body.agent_task:(body.case_id||body.task_id||body.objective?body:null);\n"
            "if(!task||typeof task!=='object') task={};\n"
            "// Contract: agent_task only (retired specialist_packet is not accepted).\n"
            f"task.agent_id={json.dumps(self.spec.agent_id)};\n"
            "task.case_id=String(task.case_id||'');\n"
            "task.task_id=String(task.task_id||'');\n"
            "task.objective=String(task.objective||'');\n"
            "task.handoff_message=String(task.handoff_message||'');\n"
            "task.inputs=task.inputs&&typeof task.inputs==='object'?task.inputs:{};\n"
            "task.context=task.context&&typeof task.context==='object'?task.context:{};\n"
            "const cfg=$('Runtime configuration').first().json||{};\n"
            "if(!task.inputs.activity_base_url&&cfg.activity_base_url) task.inputs={...task.inputs, activity_base_url:cfg.activity_base_url};\n"
            "return [{json:{...cfg, agent_task:task, case_id:task.case_id, task_id:task.task_id}}];\n"
        )

    def js_prepare(self) -> str:
        sel = SELECTORS[self.spec.rag_selector]
        extra = self.spec.planner_extra_js.strip()
        return (
            "const RAG_TARGET_BASE=" + json.dumps(sel["target_base"]) + ";\n"
            "const RAG_ACCESS_SCOPE=" + json.dumps(sel["access_scope"]) + ";\n"
            "const RAG_KNOWLEDGE_TYPES=" + json.dumps(sel["knowledge_types"]) + ";\n"
            "const RAG_TOP_K=" + str(int(sel["top_k"])) + ";\n"
            "const RAG_TOPICS=" + json.dumps(sel.get("topics") or [], ensure_ascii=False) + ";\n"
            "const opened=$json||{};\n"
            f"const task={self.ref(self.n['normalize'])}.first().json.agent_task||{{}};\n"
            "const objective=String(opened.objective||task.objective||'');\n"
            "const handoff=String(opened.handoff_message||task.handoff_message||'');\n"
            "const blob=[objective,handoff].join('\\n');\n"
            "const low=blob.toLowerCase();\n"
            "// Retrieval filters from inspect / expected_output (spec JS). Task text is query only.\n"
            "const keyword_families=[];\n"
            "const topics=RAG_TOPICS.slice();\n"
            "const task_patterns=[];\n"
            + (self.spec.retrieval_filters_js.strip() + "\n" if self.spec.retrieval_filters_js.strip() else "")
            + "const query=blob.trim().replace(/\\b[\\w.-]+\\.(xlsx|xls|xlsm|inc|dev|txt|csv)\\b/gi,' ').replace(/\\s+/g,' ').trim().slice(0,800)||"
            + json.dumps(f"{sel['target_base']} instruction")
            + ";\n"
            "const retrieval_selector={target_base:RAG_TARGET_BASE,knowledge_types:RAG_KNOWLEDGE_TYPES};\n"
            "const schedule_retrieval_request={\n"
            "  query,\n"
            "  filters:{target_base:RAG_TARGET_BASE,access_scope:RAG_ACCESS_SCOPE,knowledge_types:RAG_KNOWLEDGE_TYPES,"
            "keyword_families:[...new Set(keyword_families)].slice(0,6),topics:[...new Set(topics)],task_patterns:[...new Set(task_patterns)]},\n"
            "  top_k:RAG_TOP_K\n"
            "};\n"
            "// The LLM decides which tool fits: task text + what open_session found + what the engineer already answered.\n"
            "const engineerAnswers=Array.isArray(opened.engineer_answers)?opened.engineer_answers:[];\n"
            "const reworkReason=String(opened.rework_reason||(task.inputs&&task.inputs.rework_reason)||'').trim();\n"
            "const planner_input=JSON.stringify({\n"
            "  objective,\n"
            "  handoff_message:handoff,\n"
            "  inspect:opened.inspect||{},\n"
            + (f"  {extra},\n" if extra else "")
            + "  engineer_answers:engineerAnswers,\n"
            "  ...(reworkReason?{rework_reason:reworkReason}:{}),\n"
            "  ...(opened.expected_output&&typeof opened.expected_output==='object'&&Object.keys(opened.expected_output).length?{expected_output:opened.expected_output}:{})\n"
            "});\n"
            "return [{json:{...opened,session_id:opened.session_id,agent_input:planner_input,planner_input,schedule_retrieval_request,retrieval_selector}}];\n"
        )

    def js_summarize(self) -> str:
        t = self.spec.texts
        assert t is not None
        prefixes = [name for name in self.spec.result_tools if name.endswith("_")]
        exact = [name for name in self.spec.result_tools if not name.endswith("_")]
        checks = [f"n.indexOf('{p}')===0" for p in prefixes] + [f"n==='{e}'" for e in exact]  # tool names are identifiers
        return (
            "const agent=$json||{};\n"
            f"const opened={self.ref(self.n['open'])}.first().json||{{}};\n"
            "const steps=Array.isArray(agent.intermediateSteps)?agent.intermediateSteps:(Array.isArray(agent.intermediate_steps)?agent.intermediate_steps:[]);\n"
            "const toolName=step=>{\n"
            "  if(!step||typeof step!=='object') return '';\n"
            "  const a=step.action&&typeof step.action==='object'?step.action:step;\n"
            "  return String(a.tool||a.toolName||a.name||step.tool||'');\n"
            "};\n"
            "const tools=steps.map(toolName).filter(Boolean);\n"
            "const unique=[...new Set(tools)];\n"
            "const counts={};\n"
            "for(const name of tools) counts[name]=(counts[name]||0)+1;\n"
            "const repeated=Object.keys(counts).some(name=>counts[name]>3);\n"
            "// A stored result exists after one of the result tools (completed) or ask_engineer (needs_input); the session is authoritative.\n"
            f"const hasResult=tools.some(n=>{' || '.join(checks) or 'false'});\n"
            "const skipFetch=!hasResult;\n"
            "const finalText=String(agent.output||agent.text||'').trim();\n"
            "// Engineer-facing fallback when the LLM ended without fixing a result: plain Russian, no tool names.\n"
            f"const question=repeated?{json.dumps(t.repeated_question, ensure_ascii=False)}:{json.dumps(t.no_result_question, ensure_ascii=False)};\n"
            f"const message=hasResult?(finalText||{json.dumps(t.done_message, ensure_ascii=False)}):(finalText?finalText+' ':'')+question;\n"
            "return [{json:{\n"
            "  ...(skipFetch?{\n"
            "    task_id:opened.task_id||'',\n"
            f"    agent_id:{json.dumps(self.spec.agent_id)},\n"
            "    status:'needs_input',\n"
            "    message:question,\n"
            "    data:{tools_used:unique,total_calls:steps.length,llm_final_text:finalText.slice(0,600)},\n"
            "    artifacts:{},\n"
            f"    issues:[{{type:repeated?'repeated_tools':{json.dumps(t.no_result_issue)}}}],\n"
            "    assumptions:[],\n"
            f"    requests:[{{question_id:{json.dumps(t.question_id)},question,options:[],accepts:{{free_text:true,files:{json.dumps(t.accepts_files)}}}}}]\n"
            "  }:agent),\n"
            "  skip_fetch:skipFetch,\n"
            "  has_result:hasResult,\n"
            "  activity_kind:'agent.progress',\n"
            "  status_message:message.slice(0,400),\n"
            f"  activity_payload:{{source:{json.dumps(self.source_tag)},total_calls:steps.length,tools_used:unique,iterations:steps.length}}\n"
            "}}];\n"
        )

    def js_format_missing(self) -> str:
        t = self.spec.texts
        assert t is not None
        down = (
            f"Сервис агента „{self.spec.title}“ не отвечает по адресу из настроек среды. "
            "Проверьте, что он запущен, и перезапустите задачу."
        )
        return (
            "const opened=$json||{};\n"
            "const runtime=(()=>{try{return $('Runtime configuration').first().json||{}}catch{return {}}})();\n"
            "const result=opened.result&&typeof opened.result==='object'?opened.result:{};\n"
            "if(typeof opened.ok!=='boolean'){\n"
            "  return [{json:{\n"
            "    task_id:result.task_id||opened.task_id||'',\n"
            f"    agent_id:{json.dumps(self.spec.agent_id)},\n"
            "    status:'failed',\n"
            f"    message:{json.dumps(down, ensure_ascii=False)},\n"
            "    data:{},\n"
            "    artifacts:{},\n"
            f"    issues:[{{code:'service_unreachable',url:String(runtime[{json.dumps(self.spec.service_url_key)}]||'')}}],\n"
            "    assumptions:[],\n"
            "    requests:[]\n"
            "  }}];\n"
            "}\n"
            "return [{json:{\n"
            "  task_id:result.task_id||opened.task_id||'',\n"
            f"  agent_id:{json.dumps(self.spec.agent_id)},\n"
            "  status:result.status||'needs_input',\n"
            f"  message:result.message||opened.message||{json.dumps(t.missing_input_message, ensure_ascii=False)},\n"
            "  data:result.data||{},\n"
            "  artifacts:result.artifacts||{},\n"
            f"  issues:result.issues||[{{type:{json.dumps(t.missing_input_issue)}}}],\n"
            "  assumptions:result.assumptions||[],\n"
            f"  requests:result.requests||[{{question_id:{json.dumps(t.question_id)},question:{json.dumps(t.missing_input_question, ensure_ascii=False)},options:[],accepts:{{free_text:true,files:{json.dumps(t.accepts_files)}}}}}]\n"
            "}}];\n"
        )

    def js_format_result(self) -> str:
        t = self.spec.texts
        assert t is not None
        return (
            LOOP_HELPERS_JS
            + f"\nconst AGENT_ID={json.dumps(self.spec.agent_id)};\n"
            + f"const NO_RESULT_ISSUE={json.dumps(t.no_result_issue)};\n"
            + f"const QUESTION_ID={json.dumps(t.question_id)};\n"
            + f"const ACCEPTS_FILES={json.dumps(t.accepts_files)};\n"
            + f"const FAIL_MSG={json.dumps(t.no_result_failed_message, ensure_ascii=False)};\n"
            + f"const FAIL_ISSUE={json.dumps(t.no_result_failed_issue)};\n"
            + f"const OPEN_NAME={json.dumps(self.n['open'])};\n"
            + f"const DOWN_MSG={json.dumps('Сервис агента „' + self.spec.title + '“ не отвечает по адресу из настроек среды. Проверьте, что он запущен, и перезапустите задачу.', ensure_ascii=False)};\n"
            + f"const LLM_DOWN_MSG={json.dumps('Модель чата не ответила. Проверьте доступ к модели в настройках среды и перезапустите задачу.', ensure_ascii=False)};\n"
            + """
const fetched=$json||{};
const opened=fromNode(OPEN_NAME);
const summarized=fromNode('Summarize AI steps');
const finalText=stripThink(String(summarized.llm_final_text||fetched.llm_final_text||''));
const arts=fetched.artifacts&&typeof fetched.artifacts==='object'?fetched.artifacts:{};
const artifacts=Object.fromEntries(Object.entries(arts).filter(([,v])=>typeof v!=='boolean'));
function pack(src){
  return {json:{
    task_id:src.task_id||opened.task_id||'',
    agent_id:AGENT_ID,
    status:src.status,
    message:src.message||'',
    data:src.data||{},
    artifacts:src.artifacts&&typeof src.artifacts==='object'?src.artifacts:artifacts,
    issues:src.issues||[],
    assumptions:src.assumptions||[],
    requests:src.requests||[],
    ...(src.watch&&typeof src.watch==='object'?{watch:src.watch}:{})
  }};
}
if(summarized.service_unreachable||fetched.service_unreachable){
  return [pack({status:'failed',message:DOWN_MSG,data:{},artifacts:{},issues:[{code:'service_unreachable'}],requests:[]})];
}
if(summarized.llm_unavailable||fetched.llm_unavailable){
  return [pack({status:'failed',message:LLM_DOWN_MSG,data:{},artifacts:{},issues:[{code:'llm_unavailable'}],requests:[]})];
}
if(fetched.status){
  const issues=Array.isArray(fetched.issues)?fetched.issues:[];
  const generic=issues.some(i=>i&&i.type===NO_RESULT_ISSUE);
  if(generic&&fetched.status==='needs_input'&&finalText&&!looksMachine(finalText)){
    const reqs=Array.isArray(fetched.requests)?fetched.requests:[];
    const requests=reqs.length?reqs.map((r,i)=>i===0?{...r,question:finalText}:r):[{question_id:QUESTION_ID,question:finalText,options:[],accepts:{free_text:true,files:ACCEPTS_FILES}}];
    return [pack({...fetched,message:finalText,requests,artifacts})];
  }
  return [pack({...fetched,artifacts})];
}
if(finalText&&!looksMachine(finalText)){
  return [pack({
    status:'needs_input',
    message:finalText,
    data:{llm_final_text:finalText.slice(0,600)},
    artifacts:{},
    issues:[{type:NO_RESULT_ISSUE}],
    requests:[{question_id:QUESTION_ID,question:finalText,options:[],accepts:{free_text:true,files:ACCEPTS_FILES}}]
  })];
}
return [{json:{
  task_id:opened.task_id||'',
  agent_id:AGENT_ID,
  status:'failed',
  message:String(fetched.message||FAIL_MSG),
  data:{},
  artifacts:{},
  issues:[{type:FAIL_ISSUE}],
  assumptions:[],
  requests:[]
}}];
"""
        )

    def js_attach_rag(self) -> str:
        return attach_retrieval_js(
            prepare_node=self.n["prepare"],
            selector_key=self.spec.rag_selector,
            fail_open=True,
            ready_note=self.spec.rag_ready_note,
            empty_note=self.spec.rag_empty_note,
            activity_caller=self.spec.agent_id,
            phase="initial",
        )

    def js_build_chat(self) -> str:
        return (
            LOOP_HELPERS_JS
            + "\nconst SYSTEM="
            + json.dumps(self.spec.system_prompt, ensure_ascii=False)
            + ";\nconst TOOLS="
            + json.dumps(openai_tools_for_spec(self.spec), ensure_ascii=False)
            + ";\nconst THINKING_OFF="
            + json.dumps(CHAT_THINKING_OFF)
            + ";\nconst SAMPLING="
            + json.dumps(SAMPLING["agent"])
            + ";\nconst DEFAULT_CHAT_MODEL="
            + json.dumps(self.spec.model)
            + ";\nconst MAX_ITER="
            + str(int(self.spec.max_iterations))
            + """;
const prev=$json||{};
let cfg={};
try{cfg=$('Runtime configuration').first().json||{};}catch(e){cfg={}}
const model=String(cfg.chat_model||'').trim()||DEFAULT_CHAT_MODEL;
const base=String(cfg.chat_base_url||'').replace(/[/]+$/,'');
const chat_url=base?base+'/chat/completions':'';
let messages=Array.isArray(prev.messages)?prev.messages:[];
if(!messages.length){
  messages=[{role:'system',content:SYSTEM},{role:'user',content:String(prev.planner_input||prev.agent_input||'')}];
}
const extra=parseChatExtra(cfg);
const chat_request={model,...SAMPLING,...THINKING_OFF,messages,tools:TOOLS,tool_choice:'auto',...extra};
return [{json:{...prev,messages,chat_request,chat_url,max_iterations:MAX_ITER,iteration:Number(prev.iteration||0)}}];
"""
        )

    def js_parse_chat(self) -> str:
        return (
            LOOP_HELPERS_JS
            + """
const http=$json||{};
const prev=fromNode('Build chat request');
const choice=obj((http.choices||[])[0])?(http.choices||[])[0]:{};
const msg=obj(choice.message)?choice.message:{};
const content=stripThink(typeof msg.content==='string'?msg.content:'');
const usage=obj(http.usage)?http.usage:{};
const details=obj(usage.completion_tokens_details)?usage.completion_tokens_details:{};
const finish=String(choice.finish_reason||http.finish_reason||'');
const errObj=obj(http.error)?http.error:null;
const errText=typeof http.error==='string'?http.error:String((errObj&&(errObj.message||errObj.code))||'');
const statusCode=Number(http.statusCode||http.status||(errObj&&errObj.status)||0);
const toolCalls=Array.isArray(msg.tool_calls)?msg.tool_calls:[];
const pending=toolCalls.map(tc=>{
  const fn=obj(tc.function)?tc.function:{};
  return {id:String(tc.id||''),name:String(fn.name||tc.name||''),arguments:fn.arguments!=null?fn.arguments:tc.arguments};
}).filter(t=>t.name);
const iteration=Number(prev.iteration||0)+1;
const maxIter=Number(prev.max_iterations||8);
const over=iteration>=maxIter;
let messages=Array.isArray(prev.messages)?prev.messages.slice():[];
const assistant={role:'assistant',content:content||null};
if(toolCalls.length) assistant.tool_calls=toolCalls;
messages.push(assistant);
const tool_log=Array.isArray(prev.tool_log)?prev.tool_log.slice():[];
const llm_unavailable=(!pending.length)&&(Boolean(errText)||statusCode>=400||!Array.isArray(http.choices)||finish==='error');
const loop_route=llm_unavailable?'done':(pending.length?loopRouteForPending(pending, over):'done');
const promptMsgs=obj(prev.chat_request)&&Array.isArray(prev.chat_request.messages)?prev.chat_request.messages:[];
const tokens=Number(usage.prompt_tokens||0)+Number(usage.completion_tokens||0);
const payload={
  role:'agent',
  model:String((obj(prev.chat_request)&&prev.chat_request.model)||''),
  prompt_tokens:Number(usage.prompt_tokens||0),
  completion_tokens:Number(usage.completion_tokens||0),
  reasoning_tokens:Number(details.reasoning_tokens||0),
  finish_reason:llm_unavailable?(finish||'error'):(finish||(errText?'error':'stop')),
  tool_calls:pending.map(t=>({name:t.name,args_preview:clip(String(t.arguments||''),400)})),
  prompt_preview:previewChatMessages(promptMsgs, Number(prev.prompt_len||0)),
  content_preview:clip(content,2000)
};
if(errText) payload.error=errText.slice(0,200);
return [{json:{
  ...prev,
  messages,
  pending_tools:llm_unavailable?[]:pending,
  iteration,
  over,
  loop_route,
  llm_final_text:content,
  llm_unavailable,
  prompt_len:promptMsgs.length,
  tool_log,
  finish_reason:payload.finish_reason,
  activity_kind:'trace.llm',
  status_message:`agent: ${payload.finish_reason} · ${tokens} tok`,
  activity_payload:payload
}}];
"""
        )

    def js_prepare_tool(self) -> str:
        allowed = json.dumps([name for name, _d, _f in self.spec.tools])
        key = json.dumps(self.spec.service_url_key)
        open_name = json.dumps(self.n["open"])
        return (
            LOOP_HELPERS_JS
            + f"\nconst ALLOWED=new Set({allowed});\nconst SERVICE_KEY={key};\nconst OPEN_NAME={open_name};\n"
            + """
const prev=$json||{};
const pending=Array.isArray(prev.pending_tools)?prev.pending_tools:[];
const call=pending[0]||{};
const name=String(call.name||'');
const args=parseArgs(call.arguments);
const opened=fromNode(OPEN_NAME);
const sessionId=String(opened.session_id||prev.session_id||'');
let cfg={};
try{cfg=$('Runtime configuration').first().json||{};}catch(e){cfg={}}
const base=String(cfg[SERVICE_KEY]||prev[SERVICE_KEY]||'').replace(/[/]+$/,'');
const log=Array.isArray(prev.tool_log)?prev.tool_log:[];
const needKb=name==='apply_dataset'&&ALLOWED.has(name)&&!log.includes('retrieve_knowledge');
const skip_http=!name||!ALLOWED.has(name)||needKb;
const skip_result=needKb?{ok:false,code:'knowledge_required',message:'Сначала вызови retrieve_knowledge с query (смысл набора) и keywords (имя keyword), затем get_keyword, затем apply_dataset. Стартовый срез — краткие summary; полный текст — только из retrieve_knowledge.'}:(skip_http?{ok:false,code:'unknown_tool',message:'Нет такого инструмента',available:[...ALLOWED]}:null);
const tool_url=skip_http?'':(base+'/agent-tools/'+name);
const tool_body={session_id:sessionId,...args};
return [{json:{...prev,pending_tool:call,tool_url,tool_body,skip_http,skip_result}}];
"""
        )

    def js_append_tool(self) -> str:
        return (
            LOOP_HELPERS_JS
            + """
const http=$json||{};
const prev=fromNode('Prepare tool call');
const call=obj(prev.pending_tool)?prev.pending_tool:{};
let pending=Array.isArray(prev.pending_tools)?prev.pending_tools.slice(1):[];
let messages=Array.isArray(prev.messages)?prev.messages.slice():[];
const transport=prev.skip_http?'':toolTransportCode(http);
const result=prev.skip_http?prev.skip_result:(transport?{ok:false,code:transport,message:transport==='service_unreachable'?'Сервис агента не отвечает':'Инструмент не ответил'}:http);
const body=typeof result==='string'?result:JSON.stringify(result==null?{}:result);
messages.push({role:'tool',tool_call_id:String(call.id||''),content:body});
const tool_log=Array.isArray(prev.tool_log)?prev.tool_log.slice():[];
if(call.name) tool_log.push(String(call.name));
const down=transport==='service_unreachable';
const loop_route=down?'done':loopRouteForPending(pending, Boolean(prev.over));
return [{json:{...prev,messages,pending_tools:down?[]:pending,tool_log,loop_route,skip_http:false,skip_result:null,service_unreachable:Boolean(prev.service_unreachable||down)}}];
"""
        )

    def js_prepare_retrieve(self) -> str:
        return (
            LOOP_HELPERS_JS
            + """
const prev=$json||{};
const pending=Array.isArray(prev.pending_tools)?prev.pending_tools:[];
const call=pending[0]||{};
const args=parseArgs(call.arguments);
const req=obj(prev.schedule_retrieval_request)?{...prev.schedule_retrieval_request}:{};
const filters=obj(req.filters)?{...req.filters}:{};
const keywords=String(args.keywords||'').split(/[,;]/).map(s=>s.trim().toUpperCase()).filter(Boolean);
if(keywords.length) filters.keyword_families=[...new Set(keywords)].slice(0,6);
else filters.keyword_families=[];
const query=clip(String(args.query||'').trim()||String(req.query||''),800);
return [{json:{...prev,pending_tool:call,schedule_retrieval_request:{...req,query,filters}}}];
"""
        )

    def js_attach_retrieve(self) -> str:
        sel = SELECTORS[self.spec.rag_selector]
        selector_json = json.dumps(
            {"target_base": sel["target_base"], "knowledge_types": sel["knowledge_types"]},
            ensure_ascii=False,
        )
        caller = json.dumps(self.spec.agent_id)
        return (
            LOOP_HELPERS_JS
            + "\n"
            + RAG_HELPERS_JS
            + "\n"
            + f"const fallbackSelector={selector_json};\n"
            + f"const caller={caller};\n"
            + f"const maxCards={int(sel['max_cards'])};\n"
            + f"const textLimit={int(sel.get('on_demand_text_limit') or max(int(sel['text_limit']), 4000))};\n"
            + """
const prev=fromNode('Prepare retrieve request');
const raw=$json||{};
const result=unwrapRetrieval(raw);
const fromPrev=prev.retrieval_selector;
const selector=(fromPrev&&fromPrev.target_base)?{target_base:String(fromPrev.target_base),knowledge_types:Array.isArray(fromPrev.knowledge_types)&&fromPrev.knowledge_types.length?fromPrev.knowledge_types:fallbackSelector.knowledge_types}:fallbackSelector;
const outage=retrievalOutage(result,raw);
const cards=(outage==='failed'||outage==='needs_input')?[]:compactRetrievalCards(result,selector,maxCards,textLimit,true);
const rag={contract:'mas_rag_evidence',contract_version:'1.0',target_base:selector.target_base,knowledge_types:selector.knowledge_types,status:outage||(cards.length?'ready':'empty'),phase:'on_demand',cards,findings:(Array.isArray(result.findings)?result.findings:[]).slice(0,6).map(f=>f&&f.code).filter(Boolean)};
const call=obj(prev.pending_tool)?prev.pending_tool:{};
let pending=Array.isArray(prev.pending_tools)?prev.pending_tools.slice(1):[];
let messages=Array.isArray(prev.messages)?prev.messages.slice():[];
messages.push({role:'tool',tool_call_id:String(call.id||''),content:JSON.stringify({status:rag.status,findings:rag.findings,cards:rag.cards})});
const tool_log=Array.isArray(prev.tool_log)?prev.tool_log.slice():[];
tool_log.push('retrieve_knowledge');
const loop_route=loopRouteForPending(pending, Boolean(prev.over));
const req=obj(prev.schedule_retrieval_request)?prev.schedule_retrieval_request:{};
const logCards=(Array.isArray(cards)?cards:[]).map(c=>({knowledge_id:String((c&&c.knowledge_id)||''),revision:(c&&c.revision)!=null?c.revision:null,rrf_score:Number.isFinite(Number(c&&c.rrf_score))?Number(c.rrf_score):null,branches:Array.isArray(c&&c.branches)?c.branches.slice(0,6):[]}));
const logFindings=(Array.isArray(result.findings)?result.findings:[]).slice(0,8).map(f=>{if(obj(f)&&f.code)return{code:String(f.code)};const code=String(f||'').trim();return code?{code}:null}).filter(Boolean);
return [{json:{
  ...prev,
  messages,
  pending_tools:pending,
  tool_log,
  loop_route,
  rag,
  activity_kind:'trace.rag',
  status_message:`База знаний: ${rag.status} · ${logCards.length} карточек`,
  activity_payload:{caller,query:String(req.query||result.query||'').slice(0,800),filters:obj(req.filters)?req.filters:{},status:rag.status,phase:rag.phase,findings:logFindings,cards:logCards}
}}];
"""
        )

    def js_summarize_loop(self) -> str:
        return (
            LOOP_HELPERS_JS
            + f"\nconst OPEN_NAME={json.dumps(self.n['open'])};\n"
            + """
const prev=$json||{};
const opened=fromNode(OPEN_NAME);
const tools=Array.isArray(prev.tool_log)?prev.tool_log.filter(Boolean):[];
const unique=[...new Set(tools)];
const finalText=stripThink(String(prev.llm_final_text||''));
const counts={};
for(const name of tools) counts[name]=(counts[name]||0)+1;
const repeated=Object.keys(counts).some(name=>name!=='retrieve_knowledge'&&counts[name]>3);
const total=tools.filter(n=>n!=='retrieve_knowledge').length;
const human=finalText.trim();
const status_message=(human&&!looksMachine(human))?clip(human,400):'Агент завершил шаг.';
return [{json:{
  ...prev,
  llm_final_text:finalText,
  tools_used:unique,
  total_calls:total,
  repeated,
  activity_kind:'agent.progress',
  status_message,
  activity_payload:{source:"""
            + json.dumps(self.source_tag)
            + """,total_calls:total,tools_used:unique,iterations:Number(prev.iteration||0)}
}}];
"""
        )

    # -- assembly ------------------------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        spec, n = self.spec, self.n
        self.nodes, self.connections = [], {}
        self.node(
            "edit after import",
            "n8n-nodes-base.stickyNote",
            1,
            (-220, -360),
            {"content": spec.sticky_note or self.default_sticky(), "height": spec.sticky_height, "width": 480, "color": 1},
        )
        example = {"agent_task": {"case_id": "CASE-example", "task_id": "TASK-001", "agent_id": spec.agent_id, "objective": spec.example_objective, "inputs": {}, "context": {}}}
        self.node(n["trigger"], "n8n-nodes-base.executeWorkflowTrigger", 1.2, (0, 0), {"inputSource": "jsonExample", "jsonExample": json.dumps(example, ensure_ascii=False)})
        self.node(n["config"], "n8n-nodes-base.executeWorkflow", 1.3, (260, 0), runtime_config_execute_params())
        self.code(n["normalize"], (500, 0), self.js_normalize())
        self.http_json(n["open"], (740, 0), "POST", f"={{{{ $json.{spec.service_url_key} }}}}/agent-tools/open_session", "={{ $json.agent_task }}", retry=True)
        self.if_true(n["ready"], (980, 0), "={{ $json.ok === true }}")
        self.code(n["missing"], (1220, 240), self.js_format_missing())
        self.activity_event(n["accepted"], (1220, -220), "agent.accepted", spec.accepted_message)
        self.restore(n["restore_accepted"], (1440, -220), n["ready"])
        self.activity_event(n["progress"], (1660, -220), "agent.progress", spec.progress_message)
        self.restore(n["restore_progress"], (1880, -220), n["restore_accepted"])
        self.restore(n["restore_final"], (2760, -180), n["format"])
        self.code(n["prepare"], (1480, 160), self.js_prepare())
        self.node(n["rag"], "n8n-nodes-base.executeWorkflow", 1.3, (1480, 300), knowledge_retrieval_execute_params(), onError="continueRegularOutput")
        self.code(n["attach"], (1680, 300), self.js_attach_rag())
        self.activity_event_dynamic(n["rag_event"], (1880, 300))
        self.restore(n["restore_rag"], (2040, 300), n["attach"])
        if spec.llm_transport == "n8n_agent":
            self._add_n8n_agent_nodes(spec, n)
        else:
            self._add_http_loop_nodes(spec, n)
        self.http_json(n["fetch"], (2100, 0), "GET", f"={{{{ {self.service_url} + '/sessions/' + {self.session_id_expr} + '/result' }}}}", None, timeout=120000, retry=True)
        self.code(n["format"], (2320, 0), self.js_format_result())
        self.http_json(n["close"], (2480, 0), "POST", f"={{{{ {self.service_url} + '/sessions/' + {self.session_id_expr} + '/close' }}}}", "={{ ({}) }}", timeout=5000)
        if spec.llm_transport == "n8n_agent":
            self._connect_n8n_agent(n)
        else:
            self._connect_http_loop(n)

        return {
            "id": spec.resolved_workflow_id,
            "name": spec.workflow_name,
            "active": False,
            "isArchived": False,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {"executionOrder": "v1", "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner", "errorWorkflow": ERROR_WF_ID, "executionTimeout": 1800},
            "meta": {"templateCredsSetupCompleted": True, "targetN8nVersion": "2.30.8"},
            "tags": [],
            "pinData": {},
            "versionId": str(uuid.uuid4()),
        }

    def _shared_head_connections(self, n: dict[str, str]) -> None:
        c = self.connect
        c(n["trigger"], n["config"])
        c(n["config"], n["normalize"])
        c(n["normalize"], n["open"])
        c(n["open"], n["ready"])
        c(n["ready"], n["accepted"], si=0)
        c(n["ready"], n["missing"], si=1)
        c(n["accepted"], n["restore_accepted"])
        c(n["restore_accepted"], n["progress"])
        c(n["progress"], n["restore_progress"])
        c(n["restore_progress"], n["prepare"])
        c(n["prepare"], n["rag"])
        c(n["rag"], n["attach"])
        c(n["attach"], n["rag_event"])
        c(n["rag_event"], n["restore_rag"])
        c(n["format"], n["close"])
        c(n["close"], n["restore_final"])

    def _add_n8n_agent_nodes(self, spec: AgentSpec, n: dict[str, str]) -> None:
        self.node(
            n["agent"],
            "@n8n/n8n-nodes-langchain.agent",
            3.1,
            (1720, 160),
            {
                "promptType": "define",
                "text": "={{ $json.planner_input }}",
                "hasOutputParser": False,
                "options": {
                    "systemMessage": spec.system_prompt,
                    "maxIterations": spec.max_iterations,
                    "returnIntermediateSteps": True,
                    "passthroughBinaryImages": False,
                    "passthroughBinaryPdfs": False,
                    "enableStreaming": False,
                },
                "needsFallback": False,
            },
        )
        self.node(
            n["model"],
            "@n8n/n8n-nodes-langchain.lmChatOpenAi",
            1.3,
            (1720, 40),
            {"model": {"mode": "id", "value": spec.model}, "options": chat_model_options(temperature=0), "responsesApiEnabled": False},
            credentials=CHAT_MODEL_CREDENTIAL,
        )
        self.code(n["summarize"], (1960, 160), self.js_summarize())
        self.activity_event_dynamic(n["tools_event"], (1960, 300))
        self.restore(n["restore_tools"], (2160, 300), n["summarize"])
        self.if_true(n["stored"], (2160, 160), "={{ Boolean($json.skip_fetch) }}")
        cols = max(1, spec.tool_grid_columns)
        for i, (name, desc, fields) in enumerate(spec.tools):
            self.tool_http(name, (760 + (i % cols) * 220, -420 + (i // cols) * 160), desc, fields)

    def _connect_n8n_agent(self, n: dict[str, str]) -> None:
        self._shared_head_connections(n)
        c = self.connect
        c(n["restore_rag"], n["agent"])
        c(n["model"], n["agent"], out="ai_languageModel", tin="ai_languageModel")
        for name, _desc, _fields in self.spec.tools:
            c(name, n["agent"], out="ai_tool", tin="ai_tool")
        c(n["agent"], n["summarize"])
        c(n["summarize"], n["tools_event"])
        c(n["tools_event"], n["restore_tools"])
        c(n["restore_tools"], n["stored"])
        c(n["stored"], n["format"], si=0)
        c(n["stored"], n["fetch"], si=1)
        c(n["fetch"], n["format"])

    def _add_http_loop_nodes(self, spec: AgentSpec, n: dict[str, str]) -> None:
        self.code(n["build"], (2240, 160), self.js_build_chat())
        self.openai_chat_http(n["chat"], (2480, 160))
        self.code(n["parse"], (2720, 160), self.js_parse_chat())
        self.activity_event_dynamic(n["llm_event"], (2720, 320))
        self.restore(n["restore_llm"], (2920, 320), n["parse"])
        self.node(
            n["router"],
            "n8n-nodes-base.switch",
            3.4,
            (3120, 160),
            {"mode": "expression", "numberOutputs": 4, "output": "={{ ({chat:0,tool:1,retrieve:2,done:3})[$json.loop_route] ?? 3 }}"},
        )
        self.code(n["prep_tool"], (3360, -80), self.js_prepare_tool())
        self.if_true(n["skip_tool"], (3560, -80), "={{ Boolean($json.skip_http) }}")
        self.http_json(
            n["call_tool"],
            (3760, 40),
            "POST",
            "={{ $json.tool_url }}",
            "={{ $json.tool_body }}",
            timeout=120000,
            retry=True,
            never_error=True,
        )
        self.code(n["append_tool"], (3960, -80), self.js_append_tool())
        self.code(n["prep_retrieve"], (3360, 320), self.js_prepare_retrieve())
        self.node(n["retrieve"], "n8n-nodes-base.executeWorkflow", 1.3, (3560, 320), knowledge_retrieval_execute_params(), onError="continueRegularOutput")
        self.code(n["attach_retrieve"], (3760, 320), self.js_attach_retrieve())
        self.activity_event_dynamic(n["retrieve_event"], (3960, 320))
        self.restore(n["restore_retrieve"], (4160, 320), n["attach_retrieve"])
        self.code(n["summarize"], (3360, 520), self.js_summarize_loop())
        self.activity_event_dynamic(n["tools_event"], (3560, 520))
        self.restore(n["restore_tools"], (3760, 520), n["summarize"])

    def _connect_http_loop(self, n: dict[str, str]) -> None:
        self._shared_head_connections(n)
        c = self.connect
        c(n["restore_rag"], n["build"])
        c(n["build"], n["chat"])
        c(n["chat"], n["parse"])
        c(n["parse"], n["llm_event"])
        c(n["llm_event"], n["restore_llm"])
        c(n["restore_llm"], n["router"])
        c(n["router"], n["build"], si=0)
        c(n["router"], n["prep_tool"], si=1)
        c(n["router"], n["prep_retrieve"], si=2)
        c(n["router"], n["summarize"], si=3)
        c(n["prep_tool"], n["skip_tool"])
        c(n["skip_tool"], n["append_tool"], si=0)
        c(n["skip_tool"], n["call_tool"], si=1)
        c(n["call_tool"], n["append_tool"])
        c(n["append_tool"], n["router"])
        c(n["prep_retrieve"], n["retrieve"])
        c(n["retrieve"], n["attach_retrieve"])
        c(n["attach_retrieve"], n["retrieve_event"])
        c(n["retrieve_event"], n["restore_retrieve"])
        c(n["restore_retrieve"], n["router"])
        c(n["summarize"], n["tools_event"])
        c(n["tools_event"], n["restore_tools"])
        c(n["restore_tools"], n["fetch"])
        c(n["fetch"], n["format"])

    def default_sticky(self) -> str:
        spec = self.spec
        auth = (
            " Ключ сервиса — credential Header Auth на HTTP-нодах, не Set.\n" if spec.service_credentials else "\n"
        )
        return (
            "## edit after import\n\n"
            f"**{spec.workflow_name}** — один LLM + FastAPI tools (сгенерировано из `agents/{spec.agent_id}.py`).\n\n"
            f"1. Bind **Qwen** credential on **{self.n['chat'] if spec.llm_transport != 'n8n_agent' else self.n['model']}** "
            "(тот же OpenAI-compatible, что у Decision chat оркестратора).\n"
            f"2. Bind **Runtime configuration** → `MAS — Runtime Config` (поле `{spec.service_url_key}` = URL сервиса)." + auth
            + f"3. Bind **Call Knowledge Retrieval** → `MAS — Knowledge Retrieval` (срез `{SELECTORS[spec.rag_selector]['target_base']}`).\n"
            "4. Оркестратор вызывает этот workflow через универсальный `Call agent (n8n)` по id из `agent_registry.invoke`. "
            "После импорта через UI id меняется — впишите новый id (из URL этого workflow) в `MAS — Runtime Config` → `agent_workflow_ids`.\n"
            "5. Settings → **Error workflow** = `Error — MAS Node Traces` (после импорта через UI ссылка по id теряется): "
            "упавший узел попадёт в лог кейса (Activity → Режим разработчика → Лог).\n\n"
            "Инструмент выбирает LLM по задаче (нет regex-роутера). Вопрос инженеру — только `ask_engineer` прозой с вариантами; "
            "ошибки аргументов возвращаются LLM, не человеку. Результат читается из GET /sessions/{id}/result, "
            "сессия закрывается POST /sessions/{id}/close."
        )


def build_workflow(spec: AgentSpec) -> dict[str, Any]:
    return AgentWorkflow(spec).build()


def write_workflow(spec: AgentSpec, out_dir: Path = OUT_DIR) -> Path:
    wf = build_workflow(spec)
    out = out_dir / spec.output_filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(wf['nodes'])} nodes)")
    return out


if __name__ == "__main__":
    import sys

    from agents import ALL

    wanted = set(sys.argv[1:])
    for agent_spec in ALL:
        if agent_spec.has_workflow and (not wanted or agent_spec.agent_id in wanted):
            write_workflow(agent_spec)
