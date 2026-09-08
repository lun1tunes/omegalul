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
                 → Call Knowledge Retrieval → Attach <slug> RAG evidence → <Title> AI Agent (+ tools)
                 → Summarize AI steps → Activity — <Title> tools → Result stored?
                     ├─ no result → Format <slug> result (prose question to the engineer)
                     └─ result    → Fetch <slug> result → Format <slug> result
                 → Close <slug> session

Tools are ``n8n-nodes-base.httpRequestTool`` nodes (``mas_tool_nodes.tool_http``): in n8n 2.30.8 the
langchain ``toolHttpRequest`` is not executable. The service URL comes from ``MAS — Runtime Config``
(``spec.service_url_key``), so the field engineer changes addresses in the UI, never in JSON.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from generate_mas_error_traces import WF_ID as ERROR_WF_ID
from generate_mas_runtime_config import runtime_config_execute_params
from llm_runtime_options import chat_model_options
from mas_agent_spec import AgentSpec
from mas_retrieval_client import SELECTORS, attach_retrieval_js, knowledge_retrieval_execute_params
from mas_tool_nodes import HTTP_REQUEST_TOOL_TYPE, HTTP_REQUEST_TOOL_VERSION, http_request_tool_params

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "workflows/core"
CHAT_MODEL_CREDENTIAL = {"openAiApi": {"id": "REPLACE_IN_UI", "name": "REPLACE: Qwen OpenAI-compatible agent credential"}}


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

    def http_json(self, name: str, pos: tuple[int, int], method: str, url: str, body: str | None = None, *, timeout: int = 180000, activity: bool = False, retry: bool = False) -> dict[str, Any]:
        """HTTP Request 4.4 with JSON in/out. ``activity=True``: feed line, never fails, 2 s budget, no service auth."""
        response: dict[str, Any] = {"fullResponse": False, "responseFormat": "json"}
        if activity:
            response["neverError"] = True
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
            "// Retrieval filters. Hints below only narrow the RAG slice — they never pick a tool.\n"
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
        return (
            "const opened=$json||{};\n"
            "const result=opened.result&&typeof opened.result==='object'?opened.result:{};\n"
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
            "const fetched=$json||{};\n"
            f"const opened={self.ref(self.n['open'])}.first().json||{{}};\n"
            "const arts=fetched.artifacts&&typeof fetched.artifacts==='object'?fetched.artifacts:{};\n"
            "// n8n may coerce an empty artifact text to a boolean; a boolean card is no card.\n"
            "const artifacts=Object.fromEntries(Object.entries(arts).filter(([,v])=>typeof v!=='boolean'));\n"
            "if(fetched.status){\n"
            "  return [{json:{\n"
            "    task_id:fetched.task_id||opened.task_id||'',\n"
            f"    agent_id:{json.dumps(self.spec.agent_id)},\n"
            "    status:fetched.status,\n"
            "    message:fetched.message||'',\n"
            "    data:fetched.data||{},\n"
            "    artifacts,\n"
            "    issues:fetched.issues||[],\n"
            "    assumptions:fetched.assumptions||[],\n"
            "    requests:fetched.requests||[],\n"
            "    // in_progress (long job): what the feed/monitor may show or poll (CASE-6a9f3bc9-10cb9f: was dropped here).\n"
            "    ...(fetched.watch&&typeof fetched.watch==='object'?{watch:fetched.watch}:{})\n"
            "  }}];\n"
            "}\n"
            "return [{json:{\n"
            "  task_id:opened.task_id||'',\n"
            f"  agent_id:{json.dumps(self.spec.agent_id)},\n"
            "  status:'failed',\n"
            f"  message:String(fetched.message||fetched.error||{json.dumps(t.no_result_failed_message, ensure_ascii=False)}),\n"
            "  data:{},\n"
            "  artifacts:{},\n"
            f"  issues:[{{type:{json.dumps(t.no_result_failed_issue)}}}],\n"
            "  assumptions:[],\n"
            "  requests:[]\n"
            "}}];\n"
        )

    def js_attach_rag(self) -> str:
        return attach_retrieval_js(
            prepare_node=self.n["prepare"],
            selector_key=self.spec.rag_selector,
            fail_open=True,
            ready_note=self.spec.rag_ready_note,
            empty_note=self.spec.rag_empty_note,
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
            {"model": {"mode": "id", "value": spec.model}, "options": chat_model_options(max_tokens=2048, temperature=0), "responsesApiEnabled": False},
            credentials=CHAT_MODEL_CREDENTIAL,
        )
        self.code(n["summarize"], (1960, 160), self.js_summarize())
        self.activity_event_dynamic(n["tools_event"], (1960, 300))
        self.restore(n["restore_tools"], (2160, 300), n["summarize"])
        self.if_true(n["stored"], (2160, 160), "={{ Boolean($json.skip_fetch) }}")
        self.http_json(n["fetch"], (2100, 0), "GET", f"={{{{ {self.service_url} + '/sessions/' + {self.session_id_expr} + '/result' }}}}", None, timeout=120000, retry=True)
        self.code(n["format"], (2320, 0), self.js_format_result())
        self.http_json(n["close"], (2480, 0), "POST", f"={{{{ {self.service_url} + '/sessions/' + {self.session_id_expr} + '/close' }}}}", "={{ ({}) }}", timeout=5000)
        cols = max(1, spec.tool_grid_columns)
        for i, (name, desc, fields) in enumerate(spec.tools):
            self.tool_http(name, (760 + (i % cols) * 220, -420 + (i // cols) * 160), desc, fields)

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
        # No capability router: every task goes through the LLM, which picks the tool.
        c(n["restore_progress"], n["prepare"])
        c(n["prepare"], n["rag"])
        c(n["rag"], n["attach"])
        c(n["attach"], n["agent"])
        c(n["model"], n["agent"], out="ai_languageModel", tin="ai_languageModel")
        for name, _desc, _fields in spec.tools:
            c(name, n["agent"], out="ai_tool", tin="ai_tool")
        c(n["agent"], n["summarize"])
        c(n["summarize"], n["tools_event"])
        c(n["tools_event"], n["restore_tools"])
        c(n["restore_tools"], n["stored"])
        c(n["stored"], n["format"], si=0)
        c(n["stored"], n["fetch"], si=1)
        c(n["fetch"], n["format"])
        c(n["format"], n["close"])
        # agent.result is emitted once, by the orchestrator when it merges this result into case state.
        c(n["close"], n["restore_final"])

        return {
            "id": spec.resolved_workflow_id,
            "name": spec.workflow_name,
            "active": False,
            "isArchived": False,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {"executionOrder": "v1", "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner", "errorWorkflow": ERROR_WF_ID, "executionTimeout": 900},
            "meta": {"templateCredsSetupCompleted": True, "targetN8nVersion": "2.30.8"},
            "tags": [],
            "pinData": {},
            "versionId": str(uuid.uuid4()),
        }

    def default_sticky(self) -> str:
        spec = self.spec
        auth = (
            " Ключ сервиса — credential Header Auth на HTTP-нодах, не Set.\n" if spec.service_credentials else "\n"
        )
        return (
            "## edit after import\n\n"
            f"**{spec.workflow_name}** — один LLM + FastAPI tools (сгенерировано из `agents/{spec.agent_id}.py`).\n\n"
            f"1. Bind **Qwen** credential on {self.n['model']}\n"
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
