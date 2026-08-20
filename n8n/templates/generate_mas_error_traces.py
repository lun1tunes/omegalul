#!/usr/bin/env python3
"""Generate native n8n Error Trigger → error_traces + system.node_error."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/mas-error-traces.workflow.json"
WF_ID = "63116836-8724-595e-bc5e-dd6e743e2586"
WF_NAME = "Error — MAS Node Traces"
PG = {"postgres": {"id": "REPLACE_IN_UI", "name": "REPLACE: SCHEDULE PostgreSQL / PGVector credential"}}


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-error-traces:{name}"))


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


def code(name, pos, js):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})


def postgres(name, pos, query, params_expr):
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
                "queryBatching": "single",
                "largeNumbersOutput": "text",
                "replaceEmptyStrings": False,
            },
        },
        credentials=PG,
        alwaysOutputData=True,
    )


def connect(c, src, dst, out="main", si=0, tin="main", ti=0):
    groups = c.setdefault(src, {})
    outputs = groups.setdefault(out, [])
    while len(outputs) <= si:
        outputs.append([])
    outputs[si].append({"node": dst, "type": tin, "index": ti})


NORMALIZE = r"""
const root=$json||{};
const exec=root.execution&&typeof root.execution==='object'?root.execution:(root);
const error=exec.error&&typeof exec.error==='object'?exec.error:(root.error||{});
const executionId=String(exec.id||exec.executionId||root.execution_id||'');
const workflowName=String((exec.workflow&&exec.workflow.name)||exec.workflowName||root.workflow_name||'');
const nodeName=String(exec.lastNodeExecuted||error.node||root.node_name||'');
const message=String(error.message||root.message||'n8n node failed');
const errorType=String(error.name||error.type||'Error');
const stack=String(error.stack||'');
return [{json:{
  execution_id:executionId,
  workflow_name:workflowName,
  node_name:nodeName,
  error_message:message.slice(0,4000),
  error_type:errorType.slice(0,200),
  stack:stack.slice(0,8000),
  lookup_sql_parameters:[executionId],
}}];
"""

ATTACH = r"""
const n=$('Normalize n8n error trigger').first().json||{};
const look=$json||{};
const caseId=String(look.case_id||n.case_id||'').trim()||null;
return [{json:{
  ...n,
  case_id:caseId,
  trace_sql_parameters:[caseId, n.execution_id, n.workflow_name, n.node_name, n.error_message, n.error_type, n.stack, JSON.stringify({execution_id:n.execution_id})],
}}];
"""

EVENT = r"""
const x=$json;
const errorId=x.error_id||x.errorId||null;
if(!x.case_id) return [{json:{...x, skip_event:true, event_sql_parameters:null}}];
return [{json:{
  ...x,
  skip_event:false,
  event_sql_parameters:[
    x.case_id,
    null,
    'system.node_error',
    'n8n',
    null,
    'error',
    `Упал узел ${x.node_name||'unknown'}`,
    null,
    JSON.stringify({error_id:errorId, execution_id:x.execution_id, node_name:x.node_name, error_type:x.error_type})
  ]
}}];
"""


def main() -> None:
    nodes = [
        node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, (0, 0), {}),
        code("Normalize n8n error trigger", (240, 0), NORMALIZE),
        postgres(
            "Lookup case by execution",
            (480, 0),
            "SELECT case_id FROM executions WHERE execution_id = $1",
            "={{ $json.lookup_sql_parameters }}",
        ),
        code("Attach case_id", (720, 0), ATTACH),
        postgres(
            "Insert error_traces",
            (960, 0),
            "INSERT INTO error_traces (case_id, execution_id, workflow_name, node_name, error_message, error_type, stack, input_snapshot) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING error_id, case_id",
            "={{ $json.trace_sql_parameters }}",
        ),
        code("Prepare system.node_error", (1200, 0), EVENT),
        postgres(
            "Insert system.node_error event",
            (1440, 0),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE $1 IS NOT NULL",
            "={{ $json.event_sql_parameters }}",
        ),
        code(
            "Format error trace ack",
            (1680, 0),
            "const x=$('Attach case_id').first().json||{};return[{json:{contract:'mas_error_trace_ack',case_id:x.case_id,execution_id:x.execution_id,node_name:x.node_name}}];",
        ),
    ]
    connections = {}
    connect(connections, "Error Trigger", "Normalize n8n error trigger")
    connect(connections, "Normalize n8n error trigger", "Lookup case by execution")
    connect(connections, "Lookup case by execution", "Attach case_id")
    connect(connections, "Attach case_id", "Insert error_traces")
    connect(connections, "Insert error_traces", "Prepare system.node_error")
    connect(connections, "Prepare system.node_error", "Insert system.node_error event")
    connect(connections, "Insert system.node_error event", "Format error trace ack")

    wf = {
        "id": WF_ID,
        "name": WF_NAME,
        "active": False,
        "isArchived": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner", "errorWorkflow": ""},
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
