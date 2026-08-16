#!/usr/bin/env python3
"""Generate UI-importable Activity ↔ Data Table hydrate workflows (n8n 2.30.8)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows" / "core"


def _wf(payload: dict) -> None:
    name = payload["id"].replace("-", "_")  # unused
    path = OUT / f"{payload['file']}"
    body = {k: v for k, v in payload.items() if k != "file"}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(ROOT.parent))


def _relayout_written() -> None:
    """Keep core canvas compact + yellow edit-after-import notes after regen."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parent / "relayout_core_workflows.py"
    subprocess.check_call([sys.executable, str(script)], cwd=str(ROOT.parent))


LIST_CODE = r"""const rows=$input.all().map(i=>i.json||{}).filter(r=>r&&r.task_id);
const parse=(v,f)=>{try{const p=typeof v==='string'?JSON.parse(v):v;return p&&typeof p==='object'&&!Array.isArray(p)?p:f}catch{return f}};
const awaitingStatus=s=>String(s||'').trim().toLowerCase()==='awaiting_human';
const tasks=rows.map(row=>{
  const request=parse(row.request_json,{});
  const gateRaw=parse(row.gate_json,null);
  const gate=gateRaw&&typeof gateRaw==='object'&&!Array.isArray(gateRaw)?gateRaw:null;
  const objective=String(request.objective||request.problem_statement||request.task_description||'').trim();
  const status=String(row.status||'').trim()||null;
  const awaiting=awaitingStatus(status)&&gate&&gate.gate_id;
  return{
    task_id:String(row.task_id).trim(),
    title:objective?objective.slice(0,120):String(row.task_id).trim(),
    objective:objective?objective.slice(0,8000):null,
    updated_at:String(row.updated_at||row.created_at||new Date().toISOString()),
    status,
    version:Number.isFinite(Number(row.version))?Number(row.version):null,
    awaiting_human:Boolean(awaiting),
    human_gate:awaiting?gate:null,
    turn_count:0,
    last_status:status,
    last_at_abs:null,
  };
}).filter(t=>t.task_id);
tasks.sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at)));
// count = full CAS total before the 200-row page cap (Activity prunes ghosts only when count ≤ returned).
return[{json:{
  contract:'mas_activity_task_list',
  contract_version:'1.0',
  source:'engineering_orchestrator_tasks_v1',
  count:tasks.length,
  tasks:tasks.slice(0,200),
}}];"""

FEED_CODE = r"""const req=$('Normalize feed request').first().json||{};
const taskId=String(req.task_id||'').trim();
if(!taskId) return[{json:{contract:'mas_activity_feed_hydrate',contract_version:'1.0',ok:false,error:'task_id missing'}}];
const parse=(v,f)=>{try{const p=typeof v==='string'?JSON.parse(v):v;return p&&typeof p==='object'?p:f}catch{return f}};
const awaitingStatus=s=>String(s||'').trim().toLowerCase()==='awaiting_human';
const taskRows=$('Load task row').all().map(i=>i.json||{}).filter(r=>r&&String(r.task_id||'').trim()===taskId);
if(!taskRows.length) return[{json:{contract:'mas_activity_feed_hydrate',contract_version:'1.0',ok:false,task_id:taskId,error:'task not found in Data Table'}}];
const taskItem=taskRows[0];
const gateRaw=parse(taskItem.gate_json,null);
const gate=gateRaw&&typeof gateRaw==='object'&&!Array.isArray(gateRaw)?gateRaw:null;
const request=parse(taskItem.request_json,{});
const objective=String(request.objective||request.problem_statement||request.task_description||'').trim();
const status=String(taskItem.status||'').trim()||null;
const version=Number.isFinite(Number(taskItem.version))?Number(taskItem.version):null;
const traceRows=$('Load trace rows').all().map(i=>i.json||{}).filter(r=>r&&String(r.task_id||'').trim()===taskId);
const events=[];
for(const row of traceRows){
  const et=String(row.event_type||'').trim().toLowerCase();
  if(et&&et!=='handoff'&&et!=='mas_activity_turn') continue;
  const details=parse(row.details_json,{});
  const handoff=details&&typeof details.handoff==='object'?details.handoff:details;
  events.push({
    event_type:'handoff',
    task_id:taskId,
    event_id:row.event_id||null,
    trace_id:row.trace_id||null,
    at:row.at||null,
    stage:row.stage||null,
    status:row.status||null,
    summary:row.summary||null,
    brief:handoff?.brief||details?.brief||null,
    duration_ms:handoff?.duration_ms??details?.duration_ms??null,
    actor:row.actor||null,
    handoff:{
      from_role:handoff?.from_role||row.actor||null,
      to_role:handoff?.to_role||null,
      from_specialist:handoff?.from_specialist||null,
      to_specialist:handoff?.to_specialist||null,
      brief:handoff?.brief||details?.brief||null,
      details:Object.assign({}, (handoff?.details&&typeof handoff.details==='object'?handoff.details:(details||{})), row.event_id?{event_id:row.event_id}:{}),
    },
  });
}
events.sort((a,b)=>String(a.at||'').localeCompare(String(b.at||'')));
const handoffCount=events.length;
// DT node loads newest 500 by `at` DESC; Code keeps chronological last 500.
const truncated=Boolean(traceRows.length>=500||handoffCount>500);
return[{json:{
  contract:'mas_activity_feed_hydrate',
  contract_version:'1.0',
  ok:true,
  task_id:taskId,
  title:objective?objective.slice(0,120):taskId,
  objective:objective?objective.slice(0,8000):null,
  status,
  version,
  human_gate:gate&&gate.gate_id?gate:null,
  awaiting_human:Boolean(awaitingStatus(status)&&gate&&gate.gate_id),
  events:events.slice(-500),
  source:{task_table:'engineering_orchestrator_tasks_v1',trace_table:'mas_trace_events_v1',trace_rows:traceRows.length,handoff_events:Math.min(handoffCount,500),truncated},
}}];"""


def main() -> None:
    _wf(
        {
            "file": "mas-activity-list-tasks.workflow.json",
            "id": "mas-activity-list-tasks-v1",
            "name": "Activity — List Tasks (Data Table)",
            "description": "UI-importable hydrate: read engineering_orchestrator_tasks_v1 and return Activity rail catalog. Bind Data Table in UI. Webhook path mas-activity-list-tasks.",
            "active": False,
            "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner"},
            "nodes": [
                {
                    "parameters": {
                        "content": "## Activity — List Tasks\n1. Select Data Table `engineering_orchestrator_tasks_v1` in **Load recent tasks**.\n2. Activate webhook (or Test) after import.\n3. In mas-activity `.env` / Compose set `ACTIVITY_LIST_URL=http://<n8n>/webhook/mas-activity-list-tasks`.\n\nNo n8n env()/Globals. Corporate UI import only.",
                        "height": 280,
                        "width": 460,
                        "color": 4,
                    },
                    "id": "a1000001-list-note-0001-8000-000000000001",
                    "name": "Setup note",
                    "type": "n8n-nodes-base.stickyNote",
                    "typeVersion": 1,
                    "position": [-520, -220],
                },
                {
                    "parameters": {
                        "httpMethod": "POST",
                        "path": "mas-activity-list-tasks",
                        "responseMode": "lastNode",
                        "options": {},
                    },
                    "id": "a1000001-list-wh-0001-8000-000000000002",
                    "name": "Webhook list tasks",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2.1,
                    "position": [-200, 0],
                    "webhookId": "a1000001-list-wh-0001-8000-000000000002",
                },
                {
                    "parameters": {
                        "operation": "get",
                        "dataTableId": {
                            "__rl": True,
                            "mode": "list",
                            "value": "REPLACE_IN_UI",
                            "cachedResultName": "Engineering orchestrator task state",
                        },
                        "returnAll": True,
                        "limit": 200,
                    },
                    "id": "a1000001-list-dt-0001-8000-000000000003",
                    "name": "Load recent tasks",
                    "type": "n8n-nodes-base.dataTable",
                    "typeVersion": 1.1,
                    "position": [80, 0],
                    "alwaysOutputData": True,
                },
                {
                    "parameters": {"jsCode": LIST_CODE},
                    "id": "a1000001-list-code-0001-8000-000000000004",
                    "name": "Format Activity task list",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [360, 0],
                },
            ],
            "connections": {
                "Webhook list tasks": {
                    "main": [[{"node": "Load recent tasks", "type": "main", "index": 0}]]
                },
                "Load recent tasks": {
                    "main": [[{"node": "Format Activity task list", "type": "main", "index": 0}]]
                },
            },
            "pinData": {},
            "meta": {"templateCredsSetupCompleted": False},
            "tags": [],
        }
    )

    _wf(
        {
            "file": "mas-activity-load-feed.workflow.json",
            "id": "mas-activity-load-feed-v1",
            "name": "Activity — Load Feed (Data Tables)",
            "description": "UI-importable hydrate: load one task CAS row + handoff traces into Activity feed payload. Bind both Data Tables in UI. Webhook path mas-activity-load-feed.",
            "active": False,
            "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner"},
            "nodes": [
                {
                    "parameters": {
                        "content": "## Activity — Load Feed\n1. Bind **Load task row** → `engineering_orchestrator_tasks_v1`.\n2. Bind **Load trace rows** → `mas_trace_events_v1`.\n3. Activate webhook. Body: `{ \"task_id\": \"eng_…\" }`.\n4. Activity: `ACTIVITY_FEED_URL=http://<n8n>/webhook/mas-activity-load-feed`.\n\nReturns status/version/human_gate + handoff events for `/v1/hydrate`.",
                        "height": 300,
                        "width": 480,
                        "color": 4,
                    },
                    "id": "a1000002-feed-note-0001-8000-000000000001",
                    "name": "Setup note",
                    "type": "n8n-nodes-base.stickyNote",
                    "typeVersion": 1,
                    "position": [-640, -240],
                },
                {
                    "parameters": {
                        "httpMethod": "POST",
                        "path": "mas-activity-load-feed",
                        "responseMode": "lastNode",
                        "options": {},
                    },
                    "id": "a1000002-feed-wh-0001-8000-000000000002",
                    "name": "Webhook load feed",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2.1,
                    "position": [-280, 0],
                    "webhookId": "a1000002-feed-wh-0001-8000-000000000002",
                },
                {
                    "parameters": {
                        "jsCode": "const raw=$input.first().json||{};\nconst body=raw.body&&typeof raw.body==='object'?raw.body:raw;\nconst taskId=String(body.task_id||body.taskId||'').trim();\nif(!taskId) return[{json:{task_id:'',input_valid:false,error:'task_id required'}}];\nreturn[{json:{task_id:taskId,input_valid:true}}];"
                    },
                    "id": "a1000002-feed-norm-0001-8000-000000000003",
                    "name": "Normalize feed request",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [0, 0],
                },
                {
                    "parameters": {
                        "operation": "get",
                        "dataTableId": {
                            "__rl": True,
                            "mode": "list",
                            "value": "REPLACE_IN_UI",
                            "cachedResultName": "Engineering orchestrator task state",
                        },
                        "matchType": "allConditions",
                        "filters": {
                            "conditions": [
                                {
                                    "keyName": "task_id",
                                    "condition": "eq",
                                    "keyValue": "={{ $json.task_id }}",
                                }
                            ]
                        },
                        "returnAll": False,
                        "limit": 1,
                    },
                    "id": "a1000002-feed-task-0001-8000-000000000004",
                    "name": "Load task row",
                    "type": "n8n-nodes-base.dataTable",
                    "typeVersion": 1.1,
                    "position": [280, 0],
                    "alwaysOutputData": True,
                },
                {
                    "parameters": {
                        "jsCode": "const req=$('Normalize feed request').first().json||{};\nreturn[{json:{task_id:req.task_id}}];"
                    },
                    "id": "a1000002-feed-pass-0001-8000-000000000005",
                    "name": "Pass task_id to traces",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [560, 0],
                },
                {
                    "parameters": {
                        "operation": "get",
                        "dataTableId": {
                            "__rl": True,
                            "mode": "list",
                            "value": "REPLACE_IN_UI",
                            "cachedResultName": "MAS trace events v1",
                        },
                        "matchType": "allConditions",
                        "filters": {
                            "conditions": [
                                {
                                    "keyName": "task_id",
                                    "condition": "eq",
                                    "keyValue": "={{ $json.task_id }}",
                                }
                            ]
                        },
                        "returnAll": False,
                        "limit": 500,
                        "orderBy": True,
                        "orderByColumn": "at",
                        "orderByDirection": "DESC",
                    },
                    "id": "a1000002-feed-tr-0001-8000-000000000006",
                    "name": "Load trace rows",
                    "type": "n8n-nodes-base.dataTable",
                    "typeVersion": 1.1,
                    "position": [840, 0],
                    "alwaysOutputData": True,
                },
                {
                    "parameters": {"jsCode": FEED_CODE},
                    "id": "a1000002-feed-fmt-0001-8000-000000000007",
                    "name": "Format Activity feed hydrate",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [1120, 0],
                },
            ],
            "connections": {
                "Webhook load feed": {
                    "main": [[{"node": "Normalize feed request", "type": "main", "index": 0}]]
                },
                "Normalize feed request": {
                    "main": [[{"node": "Load task row", "type": "main", "index": 0}]]
                },
                "Load task row": {
                    "main": [[{"node": "Pass task_id to traces", "type": "main", "index": 0}]]
                },
                "Pass task_id to traces": {
                    "main": [[{"node": "Load trace rows", "type": "main", "index": 0}]]
                },
                "Load trace rows": {
                    "main": [[{"node": "Format Activity feed hydrate", "type": "main", "index": 0}]]
                },
            },
            "pinData": {},
            "meta": {"templateCredsSetupCompleted": False},
            "tags": [],
        }
    )


if __name__ == "__main__":
    main()
    _relayout_written()
