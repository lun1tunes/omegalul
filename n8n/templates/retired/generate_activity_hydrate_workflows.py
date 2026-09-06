#!/usr/bin/env python3
"""Generate the retired Activity ↔ Data Table hydrate workflow (n8n 2.30.8).

Not imported on the live MAS path (Activity uses Postgres directly).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "workflows" / "core"
OUT = ROOT / "workflows" / "retired"
STALE = (
    CORE / "mas-activity-list-tasks.workflow.json",
    CORE / "mas-activity-load-feed.workflow.json",
    CORE / "mas-activity-hydrate.workflow.json",
    OUT / "mas-activity-list-tasks.workflow.json",
    OUT / "mas-activity-load-feed.workflow.json",
)


def _wf(payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{payload['file']}"
    body = {k: v for k, v in payload.items() if k != "file"}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(ROOT.parent))


def _relayout_written() -> None:
    """Keep core canvas compact + yellow edit-after-import notes after regen."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "relayout_core_workflows.py"
    subprocess.check_call([sys.executable, str(script)], cwd=str(ROOT.parent))


NORMALIZE_CODE = r"""const raw=$input.first().json||{};
const body=raw.body&&typeof raw.body==='object'?raw.body:raw;
const taskId=String(body.task_id||body.taskId||'').trim();
const action=String(body.action||'').trim().toLowerCase();
const wantFeed=Boolean(taskId)&&action!=='list';
return[{json:{task_id:taskId,want_feed:wantFeed}}];"""

LIST_CODE = r"""const req=$('Normalize hydrate request').first().json||{};
const rows=$input.all().map(i=>i.json||{}).filter(r=>r&&r.task_id);
const parse=(v,f)=>{try{const p=typeof v==='string'?JSON.parse(v):v;return p&&typeof p==='object'&&!Array.isArray(p)?p:f}catch{return f}};
const awaitingStatus=s=>String(s||'').trim().toLowerCase()==='awaiting_human';
const tasks=rows.map(row=>{
  const request=parse(row.request_json,{});
  const gateRaw=parse(row.gate_json,null);
  const gate=gateRaw&&typeof gateRaw==='object'&&!Array.isArray(gateRaw)?gateRaw:null;
  const objective=String(request.objective||request.problem_statement||request.task_description||'').trim();
  const inputFiles=Array.isArray(request.input_files)?request.input_files:[];
  const attached_files=[...new Set(inputFiles.map(f=>String((f&&(f.filename||f.name))||f||'').trim().replace(/^.*[\\/]/,'')).filter(Boolean))].slice(0,32);
  const status=String(row.status||'').trim()||null;
  const awaiting=awaitingStatus(status)&&gate&&gate.gate_id;
  return{
    task_id:String(row.task_id).trim(),
    title:objective?objective.slice(0,120):String(row.task_id).trim(),
    objective:objective?objective.slice(0,8000):null,
    attached_files:attached_files.length?attached_files:null,
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
  task_id:String(req.task_id||''),
  want_feed:Boolean(req.want_feed),
}}];"""

RETURN_LIST_CODE = r"""const x=$json||{};
return[{json:{
  contract:'mas_activity_task_list',
  contract_version:'1.0',
  source:x.source||'engineering_orchestrator_tasks_v1',
  count:Number(x.count||0),
  tasks:Array.isArray(x.tasks)?x.tasks:[],
}}];"""

FEED_CODE = r"""const req=$('Normalize hydrate request').first().json||{};
const listPart=$('Format Activity task list').first().json||{};
const list={
  contract:'mas_activity_task_list',
  contract_version:'1.0',
  source:listPart.source||'engineering_orchestrator_tasks_v1',
  count:Number(listPart.count||0),
  tasks:Array.isArray(listPart.tasks)?listPart.tasks:[],
};
const wrap=feed=>[{json:{contract:'mas_activity_hydrate',contract_version:'1.0',list,feed}}];
const taskId=String(req.task_id||'').trim();
if(!taskId) return wrap({contract:'mas_activity_feed_hydrate',contract_version:'1.0',ok:false,error:'task_id missing'});
const parse=(v,f)=>{try{const p=typeof v==='string'?JSON.parse(v):v;return p&&typeof p==='object'?p:f}catch{return f}};
const awaitingStatus=s=>String(s||'').trim().toLowerCase()==='awaiting_human';
const taskRows=$('Load task row').all().map(i=>i.json||{}).filter(r=>r&&String(r.task_id||'').trim()===taskId);
if(!taskRows.length) return wrap({contract:'mas_activity_feed_hydrate',contract_version:'1.0',ok:false,task_id:taskId,error:'task not found in Data Table'});
const taskItem=taskRows[0];
const gateRaw=parse(taskItem.gate_json,null);
const gate=gateRaw&&typeof gateRaw==='object'&&!Array.isArray(gateRaw)?gateRaw:null;
const request=parse(taskItem.request_json,{});
const objective=String(request.objective||request.problem_statement||request.task_description||'').trim();
const inputFiles=Array.isArray(request.input_files)?request.input_files:[];
const attached_files=[...new Set(inputFiles.map(f=>String((f&&(f.filename||f.name))||f||'').trim().replace(/^.*[\\/]/,'')).filter(Boolean))].slice(0,32);
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
return wrap({
  contract:'mas_activity_feed_hydrate',
  contract_version:'1.0',
  ok:true,
  task_id:taskId,
  title:objective?objective.slice(0,120):taskId,
  objective:objective?objective.slice(0,8000):null,
  attached_files:attached_files.length?attached_files:null,
  status,
  version,
  human_gate:gate&&gate.gate_id?gate:null,
  awaiting_human:Boolean(awaitingStatus(status)&&gate&&gate.gate_id),
  events:events.slice(-500),
  source:{task_table:'engineering_orchestrator_tasks_v1',trace_table:'mas_trace_events_v1',trace_rows:traceRows.length,handoff_events:Math.min(handoffCount,500),truncated},
});"""


def main() -> None:
    _wf(
        {
            "file": "mas-activity-hydrate.workflow.json",
            "id": "mas-activity-hydrate-v1",
            "name": "Activity — Hydrate (Data Tables)",
            "description": "UI-importable hydrate: one webhook for the Activity rail catalog and optional task feed. POST {action:'list'} for catalog; POST {task_id} for catalog + traces. Bind both Data Tables in UI. Webhook path mas-activity-hydrate.",
            "active": False,
            "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner", "errorWorkflow": "e1f0a7c2-9b4d-5e8f-a123-4567890abcde"},
            "nodes": [
                {
                    "parameters": {
                        "content": "## Activity — Hydrate\n1. Bind **Load recent tasks** and **Load task row** → `engineering_orchestrator_tasks_v1`.\n2. Bind **Load trace rows** → `mas_trace_events_v1`.\n3. Activate webhook. Body: `{ \"action\": \"list\" }` or `{ \"task_id\": \"eng_…\" }`.\n4. Activity derives `ACTIVITY_HYDRATE_URL` from n8n host (`/webhook/mas-activity-hydrate`).\n\nOne execution per UI refresh. Feed responses also include the rail catalog.",
                        "height": 320,
                        "width": 500,
                        "color": 4,
                    },
                    "id": "a1000003-hyd-note-0001-8000-000000000001",
                    "name": "Setup note",
                    "type": "n8n-nodes-base.stickyNote",
                    "typeVersion": 1,
                    "position": [-640, -280],
                },
                {
                    "parameters": {
                        "httpMethod": "POST",
                        "path": "mas-activity-hydrate",
                        "responseMode": "lastNode",
                        "options": {},
                    },
                    "id": "a1000003-hyd-wh-0001-8000-000000000002",
                    "name": "Webhook hydrate",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2.1,
                    "position": [-280, 0],
                    "webhookId": "a1000003-hyd-wh-0001-8000-000000000002",
                },
                {
                    "parameters": {"jsCode": NORMALIZE_CODE},
                    "id": "a1000003-hyd-norm-0001-8000-000000000003",
                    "name": "Normalize hydrate request",
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
                        "returnAll": True,
                        "limit": 200,
                    },
                    "id": "a1000003-hyd-list-0001-8000-000000000004",
                    "name": "Load recent tasks",
                    "type": "n8n-nodes-base.dataTable",
                    "typeVersion": 1.1,
                    "position": [280, 0],
                    "alwaysOutputData": True,
                },
                {
                    "parameters": {"jsCode": LIST_CODE},
                    "id": "a1000003-hyd-fmt-list-0001-8000-000000000005",
                    "name": "Format Activity task list",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [560, 0],
                },
                {
                    "parameters": {
                        "conditions": {
                            "options": {
                                "caseSensitive": True,
                                "leftValue": "",
                                "typeValidation": "strict",
                                "version": 2,
                            },
                            "conditions": [
                                {
                                    "id": "a1000003-hyd-if-0001-8000-000000000006",
                                    "leftValue": "={{ $json.want_feed }}",
                                    "rightValue": True,
                                    "operator": {"type": "boolean", "operation": "true"},
                                }
                            ],
                            "combinator": "and",
                        },
                        "options": {},
                    },
                    "id": "a1000003-hyd-if-node-0001-8000-000000000006",
                    "name": "Need feed?",
                    "type": "n8n-nodes-base.if",
                    "typeVersion": 2.3,
                    "position": [840, 0],
                },
                {
                    "parameters": {"jsCode": RETURN_LIST_CODE},
                    "id": "a1000003-hyd-ret-0001-8000-000000000007",
                    "name": "Return list hydrate",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [1120, 160],
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
                    "id": "a1000003-hyd-task-0001-8000-000000000008",
                    "name": "Load task row",
                    "type": "n8n-nodes-base.dataTable",
                    "typeVersion": 1.1,
                    "position": [1120, -80],
                    "alwaysOutputData": True,
                },
                {
                    "parameters": {
                        "jsCode": "const req=$('Normalize hydrate request').first().json||{};\nreturn[{json:{task_id:req.task_id}}];"
                    },
                    "id": "a1000003-hyd-pass-0001-8000-000000000009",
                    "name": "Pass task_id to traces",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [1400, -80],
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
                    "id": "a1000003-hyd-tr-0001-8000-00000000000a",
                    "name": "Load trace rows",
                    "type": "n8n-nodes-base.dataTable",
                    "typeVersion": 1.1,
                    "position": [1680, -80],
                    "alwaysOutputData": True,
                },
                {
                    "parameters": {"jsCode": FEED_CODE},
                    "id": "a1000003-hyd-fmt-feed-0001-8000-00000000000b",
                    "name": "Format Activity feed hydrate",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [1960, -80],
                },
            ],
            "connections": {
                "Webhook hydrate": {
                    "main": [[{"node": "Normalize hydrate request", "type": "main", "index": 0}]]
                },
                "Normalize hydrate request": {
                    "main": [[{"node": "Load recent tasks", "type": "main", "index": 0}]]
                },
                "Load recent tasks": {
                    "main": [[{"node": "Format Activity task list", "type": "main", "index": 0}]]
                },
                "Format Activity task list": {
                    "main": [[{"node": "Need feed?", "type": "main", "index": 0}]]
                },
                "Need feed?": {
                    "main": [
                        [{"node": "Load task row", "type": "main", "index": 0}],
                        [{"node": "Return list hydrate", "type": "main", "index": 0}],
                    ]
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
    for stale in STALE:
        if stale.exists():
            stale.unlink()
            print("removed", stale.relative_to(ROOT.parent))


if __name__ == "__main__":
    main()
    _relayout_written()
