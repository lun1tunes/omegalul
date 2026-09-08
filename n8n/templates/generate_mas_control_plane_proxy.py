#!/usr/bin/env python3
"""Generate ``MAS — Control Plane Proxy`` and the control-plane SQL files from one source.

The proxy is the only way Activity (Python on Windows) reaches Postgres: one webhook, one
``operation`` per call (``schema``, ``snapshot``, ``upsert_agent`` …). Until Phase 2 its JSON was
edited by hand; now the ``agent_registry`` DDL / seed / ``list_agents`` / ``upsert_agent`` come
from ``mas_agent_registry.py`` and the same source writes:

- ``n8n/workflows/core/mas-control-plane-proxy.workflow.json``
- ``postgres-init/02-mas-control-plane.sql`` (fresh volume + lab ``psql``)
- ``postgres-init/03-schedule-builder-registry.sql``
- ``mas-activity-service/app/sql/control_plane.sql`` (= 02 + 03, checked by ``test_cases_api.py``)
- ``mas-activity-service/app/sql/agent_registry_seed.json`` (Activity's in-memory registry when no proxy is configured)
"""

from __future__ import annotations

import json
from pathlib import Path

from mas_agent_registry import (
    COLUMN_NAMES,
    JSON_COLUMNS,
    create_table_sql,
    list_agents_sql,
    seed_as_python,
    seed_rows_sql,
    upsert_agent_sql,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OUT = ROOT / "workflows/core/mas-control-plane-proxy.workflow.json"
SQL_02 = REPO / "postgres-init/02-mas-control-plane.sql"
SQL_03 = REPO / "postgres-init/03-schedule-builder-registry.sql"
SQL_ACTIVITY = REPO / "mas-activity-service/app/sql/control_plane.sql"
SEED_JSON = REPO / "mas-activity-service/app/sql/agent_registry_seed.json"

WF_ID = "mas-control-plane-proxy-v1"
WF_NAME = "MAS — Control Plane Proxy"

STICKY = (
    "## edit after import\n\n"
    "**MAS — Control Plane Proxy** — after UI import, complete bindings before activate:\n\n"
    "- Set webhook auth credentials on **MAS control-plane webhook**\n"
    "- Set Postgres credentials on **Execute control-plane SQL**\n\n"
    "Do not rely on env()/Globals for corporate UI import.\n"
    "- **Wipe checkbox:** **Operator flags** → boolean `clear`. Leave `false`. To wipe cases: set `clear` = true → Save → "
    "**Test workflow** (pinned body is `{operation:schema}`). Then set `clear` back to `false` and Save. Production `/webhook/` "
    "ignores the checkbox so Activity boot cannot wipe. HTTP wipe: `{\"operation\":\"schema\",\"clear\":true}` or `{\"operation\":\"wipe\"}`.\n"
    "- Reads: SSE shares one snapshot poller; `batch` runs several single-row SQL in one execution.\n"
    "- `schema` also upgrades `agent_registry` in place (Phase 2 columns `invoke`, `input_schema`, `output_schema`, "
    "`hitl_policy`, `enabled`, `version`) and fills them for the seeded agents without touching engineer edits.\n"
)

# --- SQL fragments shared by the proxy `schema` operation and the SQL init files -------------------

# Case statuses the orchestrator persists (Python twin: mas-activity-service/app/contracts.py CASE_STATUSES).
# `waiting_agent` = a long-running agent returned in_progress (Phase 4). The CHECK constraint is re-created on
# every `schema` run so an existing lab/field database picks up new statuses (CASE-6a9f3a76-38506f: the first
# in_progress result hit the old constraint in `Update case after agent`).
CASE_STATUSES = ("new", "running", "waiting_user", "waiting_agent", "done", "failed")
_STATUS_LIST = ",".join(f"'{s}'" for s in CASE_STATUSES)
CASES_STATUS_CHECK_SQL = (
    "ALTER TABLE cases DROP CONSTRAINT IF EXISTS cases_status_check;"
    f"ALTER TABLE cases ADD CONSTRAINT cases_status_check CHECK (status IN ({_STATUS_LIST}));"
)
CORE_TABLES_SQL = (
    "CREATE TABLE IF NOT EXISTS cases (case_id TEXT PRIMARY KEY,state JSONB NOT NULL DEFAULT '{}'::jsonb,status TEXT NOT NULL,"
    f"updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),CONSTRAINT cases_status_check CHECK (status IN ({_STATUS_LIST})));"
    + CASES_STATUS_CHECK_SQL +
    "CREATE TABLE IF NOT EXISTS events (event_id BIGSERIAL PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,task_id TEXT,"
    "kind TEXT NOT NULL,actor TEXT NOT NULL,agent_id TEXT,status TEXT,status_message TEXT,handoff_message TEXT,payload JSONB NOT NULL DEFAULT '{}'::jsonb,"
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now());"
    "CREATE INDEX IF NOT EXISTS events_case_id_event_id_idx ON events(case_id);"
    "CREATE TABLE IF NOT EXISTS error_traces (error_id BIGSERIAL PRIMARY KEY,case_id TEXT,execution_id TEXT,workflow_name TEXT,node_name TEXT,error_message TEXT,"
    "error_type TEXT,stack TEXT,input_snapshot JSONB,created_at TIMESTAMPTZ NOT NULL DEFAULT now());"
    "CREATE INDEX IF NOT EXISTS error_traces_case_id_idx ON error_traces(case_id);"
    "CREATE TABLE IF NOT EXISTS executions (execution_id TEXT PRIMARY KEY,case_id TEXT,workflow_name TEXT,started_at TIMESTAMPTZ NOT NULL DEFAULT now());"
    "CREATE INDEX IF NOT EXISTS executions_case_id_idx ON executions(case_id);"
)
ARTIFACTS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS mas_artifacts (case_id TEXT NOT NULL,artifact_id TEXT NOT NULL,filename TEXT NOT NULL,"
    "mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',content BYTEA NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(case_id,artifact_id));"
    "CREATE INDEX IF NOT EXISTS mas_artifacts_case_id_idx ON mas_artifacts(case_id);"
)
SCHEMA_SQL = CORE_TABLES_SQL + create_table_sql() + ARTIFACTS_TABLE_SQL + seed_rows_sql(mode="fill")

WIPE_SQL = (
    "TRUNCATE TABLE events RESTART IDENTITY CASCADE;TRUNCATE TABLE error_traces RESTART IDENTITY CASCADE;"
    "TRUNCATE TABLE executions RESTART IDENTITY CASCADE;TRUNCATE TABLE mas_artifacts RESTART IDENTITY CASCADE;"
    "TRUNCATE TABLE cases RESTART IDENTITY CASCADE;"
)


def _upsert_params_js() -> str:
    """JS array of upsert parameters in COLUMN_NAMES order (strings; JSON columns serialised)."""
    parts = []
    for name in COLUMN_NAMES:
        if name == "agent_id":
            parts.append("s(r.agent_id)")
        elif name in JSON_COLUMNS:
            default = "[]" if name in ("input_required", "output_provides") else "{}"
            parts.append(f"j(r.{name}||{default})")
        elif name == "enabled":
            parts.append("(r.enabled===false||r.enabled==='false'||r.enabled===0)?'false':'true'")
        elif name == "hitl_policy":
            parts.append("s(r.hitl_policy||'agent_asks')")
        elif name == "version":
            parts.append("s(r.version||'1')")
        else:
            parts.append(f"r.{name}||null")
    return "[" + ",".join(parts) + "]"


NORMALIZE_JS = (
    r"""const incomingBase=$json.body&&typeof $json.body==='object'?$json.body:$json;const incoming=(!String(incomingBase.operation||'').trim()&&(String((typeof $execution!=='undefined'&&$execution.mode)||'')==='manual'||String($json.webhookUrl||'').includes('webhook-test'))&&($json.clear===true||$json.clear===1||$json.clear==='true'||$json.wipe_data===true))?{...incomingBase,operation:'schema'}:incomingBase;
function buildItem(raw){
const op=String(raw.operation||'').trim();
const allowed=new Set(['schema','wipe','create_case','get_case','list_cases','update_case','append_event','list_events','snapshot','append_error','list_errors','record_execution','case_id_for_execution','list_agents','upsert_agent','artifact_put','artifact_get']);
if(!allowed.has(op)||op==='batch') throw new Error('unsupported operation');
const s=v=>String(v??'');
const j=v=>JSON.stringify(v??{});
const caseId=s(raw.case_id).trim();
const artifactId=s(raw.artifact_id).trim();
if(['create_case','get_case','update_case','append_event','list_events','snapshot','list_errors','artifact_put','artifact_get'].includes(op)&&!caseId) throw new Error('case_id is required');
if(['artifact_put','artifact_get'].includes(op)&&!artifactId) throw new Error('artifact_id is required');
function truthy(v){return v===true||v===1||v==='1'||v==='true'||String(v??'').toLowerCase()==='true';}
const flagClear=truthy($json.clear)||truthy($json.wipe_data);
const bodyClear=truthy(raw.clear)||truthy(raw.wipe)||truthy(raw.clear_data);
const execMode=String((typeof $execution!=='undefined'&&$execution.mode)||'');
const editorTest=execMode==='manual'||String($json.webhookUrl||'').includes('webhook-test');
const wipe=op==='wipe'||(op==='schema'&&(bodyClear||(editorTest&&flagClear)));
const wipeSql="""
    + json.dumps(WIPE_SQL)
    + r""";
const common={operation:op,case_id:caseId,artifact_id:artifactId,wiped:wipe};
let query='',params=[];
if(op==='schema'||op==='wipe') query="""
    + json.dumps(SCHEMA_SQL, ensure_ascii=False)
    + r"""+(wipe?wipeSql:'')+`SELECT true AS schema_ok;`;
else if(op==='create_case'){query='INSERT INTO cases(case_id,state,status,updated_at) VALUES($1,$2::jsonb,$3,now()) ON CONFLICT(case_id) DO NOTHING RETURNING case_id,state,status,updated_at';params=[caseId,j(raw.state),s(raw.status||'new')];}
else if(op==='get_case'){query='SELECT case_id,state,status,updated_at FROM cases WHERE case_id=$1';params=[caseId];}
else if(op==='list_cases'){query="SELECT c.case_id,c.status,c.updated_at,c.state,(SELECT COUNT(*) FROM events e WHERE e.case_id=c.case_id) AS event_count,COALESCE((SELECT jsonb_agg(jsonb_build_object('kind',e.kind,'actor',e.actor,'agent_id',e.agent_id,'task_id',e.task_id,'status_message',e.status_message,'handoff_message',e.handoff_message) ORDER BY e.event_id) FROM events e WHERE e.case_id=c.case_id),'[]'::jsonb) AS events FROM cases c ORDER BY c.updated_at DESC LIMIT $1";params=[Number(raw.limit||200)];}
else if(op==='update_case'){query='UPDATE cases SET state=$1::jsonb,status=$2,updated_at=now() WHERE case_id=$3 RETURNING case_id,state,status,updated_at';params=[j(raw.state),s(raw.status),caseId];}
else if(op==='append_event'){query="WITH locked AS (SELECT case_id FROM cases WHERE case_id=$1 FOR UPDATE), last_event AS (SELECT e.* FROM events e JOIN locked l ON l.case_id=e.case_id WHERE e.case_id=$1 ORDER BY e.event_id DESC LIMIT 1), inserted AS (INSERT INTO events(case_id,task_id,kind,actor,agent_id,status,status_message,handoff_message,payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NOT EXISTS (SELECT 1 FROM last_event WHERE kind=$3 AND actor=$4 AND COALESCE(agent_id,'')=COALESCE($5,'') AND COALESCE(status,'')=COALESCE($6,'') AND regexp_replace(COALESCE(status_message,''),'\\s+',' ','g')=regexp_replace(COALESCE($7,''),'\\s+',' ','g') AND regexp_replace(COALESCE(handoff_message,''),'\\s+',' ','g')=regexp_replace(COALESCE($8,''),'\\s+',' ','g') AND COALESCE(task_id,'')=COALESCE($2,'')) RETURNING event_id,case_id,task_id,kind,actor,agent_id,status,status_message,handoff_message,payload,created_at) SELECT *,false AS idempotent FROM inserted UNION ALL SELECT event_id,case_id,task_id,kind,actor,agent_id,status,status_message,handoff_message,payload,created_at,true AS idempotent FROM last_event WHERE NOT EXISTS (SELECT 1 FROM inserted)";params=[caseId,s(raw.task_id),s(raw.kind),s(raw.actor),raw.agent_id||null,raw.status||null,raw.status_message||null,raw.handoff_message||null,j(raw.payload)];}
else if(op==='list_events'){query='SELECT event_id,case_id,task_id,kind,actor,agent_id,status,status_message,handoff_message,payload,created_at FROM events WHERE case_id=$1 AND event_id>$2 ORDER BY event_id ASC';params=[caseId,Number(raw.after_seq||0)];}
else if(op==='snapshot'){query=`SELECT (SELECT jsonb_build_object('case_id',case_id,'state',state,'status',status,'updated_at',updated_at) FROM cases WHERE case_id=$1) AS case_row, COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.event_id) FROM (SELECT event_id,case_id,task_id,kind,actor,agent_id,status,status_message,handoff_message,payload,created_at FROM events WHERE case_id=$1 AND event_id>$2 ORDER BY event_id ASC) e),'[]'::jsonb) AS events`;params=[caseId,Number(raw.after_seq||0)];}
else if(op==='append_error'){query='INSERT INTO error_traces(case_id,execution_id,workflow_name,node_name,error_message,error_type,stack,input_snapshot) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING error_id,case_id,execution_id,workflow_name,node_name,error_message,error_type,stack,input_snapshot,created_at';params=[raw.case_id||null,raw.execution_id||null,raw.workflow_name||null,raw.node_name||null,raw.error_message||null,raw.error_type||null,raw.stack||null,j(raw.input_snapshot)];}
else if(op==='list_errors'){query='SELECT error_id,case_id,execution_id,workflow_name,node_name,error_message,error_type,stack,input_snapshot,created_at FROM error_traces WHERE case_id=$1 ORDER BY error_id ASC';params=[caseId];}
else if(op==='record_execution'){query='INSERT INTO executions(execution_id,case_id,workflow_name) VALUES($1,$2,$3) ON CONFLICT(execution_id) DO UPDATE SET case_id=EXCLUDED.case_id,workflow_name=EXCLUDED.workflow_name RETURNING execution_id,case_id,workflow_name';params=[s(raw.execution_id),caseId,s(raw.workflow_name||'orchestrator')];}
else if(op==='case_id_for_execution'){query='SELECT case_id FROM executions WHERE execution_id=$1';params=[s(raw.execution_id)];}
else if(op==='list_agents'){query="""
    + json.dumps(list_agents_sql())
    + r""";}
else if(op==='upsert_agent'){const r=raw.row||{};if(!s(r.agent_id).trim()) throw new Error('row.agent_id is required');query="""
    + json.dumps(upsert_agent_sql())
    + r""";params="""
    + _upsert_params_js()
    + r""";}
else if(op==='artifact_put'){query="INSERT INTO mas_artifacts(case_id,artifact_id,filename,mime_type,content,updated_at) VALUES($1,$2,$3,$4,decode($5,'base64'),now()) ON CONFLICT(case_id,artifact_id) DO UPDATE SET filename=EXCLUDED.filename,mime_type=EXCLUDED.mime_type,content=EXCLUDED.content,updated_at=now() RETURNING case_id,artifact_id,filename,mime_type,octet_length(content)::bigint AS bytes";params=[caseId,artifactId,s(raw.filename||artifactId).slice(0,512),s(raw.mime_type||'application/octet-stream').slice(0,255),s(raw.content_base64)];}
else if(op==='artifact_get'){query="SELECT case_id,artifact_id,filename,mime_type,encode(content,'base64') AS content_base64,octet_length(content)::bigint AS bytes FROM mas_artifacts WHERE case_id=$1 AND artifact_id=$2";params=[caseId,artifactId];}
return {json:{...common,query,params}};
}
const topOp=String(incoming.operation||'').trim();
if(topOp==='batch'){
  const calls=Array.isArray(incoming.calls)?incoming.calls.slice(0,16):[];
  if(!calls.length) throw new Error('batch requires calls');
  const batchable=new Set(['create_case','get_case','update_case','append_event','snapshot','append_error','record_execution','case_id_for_execution','upsert_agent','artifact_put','artifact_get']);
  return calls.map((c,i)=>{
    const item=c&&typeof c==='object'?c:{};
    const innerOp=String(item.operation||'').trim();
    if(!batchable.has(innerOp)) throw new Error('batch only supports single-row operations');
    const built=buildItem(item);
    built.json.batch=true;
    built.json.batch_index=i;
    return built;
  });
}
return [buildItem(incoming)];
"""
)

FORMAT_JS = r"""function parseMaybe(v){
  if(v==null||v==='') return v;
  if(typeof v==='string'){try{return JSON.parse(v);}catch(_){return v;}}
  return v;
}
function nonemptyRow(r){
  return !!(r && typeof r==='object' && !Array.isArray(r) && Object.keys(r).length);
}
function isEchoedRequest(r){
  return !!(r && r.query!=null && r.params!=null && r.operation);
}
function dataRows(items){
  return items.map(x=>x.json||{}).filter(r=>nonemptyRow(r)&&!isEchoedRequest(r));
}
function formatOne(req, rows){
  const op=req.operation;
  let result;
  if(op==='list_cases') result=rows;
  else if(op==='list_events') result=rows;
  else if(op==='list_errors') result=rows;
  else if(op==='list_agents') result=rows;
  else if(op==='case_id_for_execution') result=rows[0]?.case_id||null;
  else if(op==='artifact_get') result=rows[0]?{found:true,...rows[0]}:{found:false,case_id:req.case_id,artifact_id:req.artifact_id};
  else if(op==='schema'||op==='wipe') result={schema_ok:true,wiped:req.wiped===true};
  else if(op==='snapshot'){
    const row=rows[0]||{};
    result={case:parseMaybe(row.case_row)||null,events:parseMaybe(row.events)||[]};
    if(!Array.isArray(result.events)) result.events=[];
  }
  else result=rows[0]||{};
  return result;
}
function itemIndex(item, fallback){
  const p=item.pairedItem;
  if(typeof p==='number') return p;
  if(p&&typeof p.item==='number') return p.item;
  if(typeof item.json?.batch_index==='number') return item.json.batch_index;
  return fallback;
}
const prepared=$('Normalize control-plane request').all().map(i=>i.json||{});
const incoming=$input.all();
const grouped=new Map();
incoming.forEach((item,i)=>{
  const row=item.json||{};
  if(!nonemptyRow(row)||isEchoedRequest(row)) return;
  const idx=itemIndex(item, prepared.length===1?0:i);
  if(!grouped.has(idx)) grouped.set(idx, []);
  grouped.get(idx).push(row);
});
if(prepared.length===1 && !prepared[0].batch){
  const rows=dataRows(incoming);
  const result=formatOne(prepared[0], rows);
  return [{json:{ok:true,operation:prepared[0].operation,result}}];
}
const results=prepared.map((req,i)=>formatOne(req, grouped.get(i)||[]));
return [{json:{ok:true,operation:'batch',result:results}}];
"""


def node(nid: str, name: str, ntype: str, ver: float | int, pos: tuple[int, int], params: dict, **extra) -> dict:
    out = {"parameters": params, "id": nid, "name": name, "type": ntype, "typeVersion": ver, "position": list(pos)}
    out.update(extra)
    return out


def build_workflow() -> dict:
    nodes = [
        node(
            "3897f8af-b2a0-5ba7-9674-e2a3938e5def",
            "edit after import",
            "n8n-nodes-base.stickyNote",
            1,
            (-40, -380),
            {"content": STICKY, "height": 360, "width": 440, "color": 1},
        ),
        node(
            "mas-control-plane-webhook-v1",
            "MAS control-plane webhook",
            "n8n-nodes-base.webhook",
            2.1,
            (0, 0),
            {"httpMethod": "POST", "path": "mas-control-plane", "authentication": "headerAuth", "responseMode": "responseNode", "options": {}},
            webhookId="mas-control-plane-proxy-v1",
            credentials={"httpHeaderAuth": {"id": "REPLACE_IN_UI", "name": "REPLACE: MAS Control Plane Header Auth"}},
        ),
        node(
            "mas-control-plane-flags-v1",
            "Operator flags",
            "n8n-nodes-base.set",
            3.4,
            (280, 0),
            {
                "assignments": {"assignments": [{"id": "mas-control-plane-clear-flag", "name": "clear", "value": False, "type": "boolean"}]},
                "options": {},
                "includeOtherFields": True,
            },
        ),
        node("mas-control-plane-normalize-v1", "Normalize control-plane request", "n8n-nodes-base.code", 2, (560, 0), {"jsCode": NORMALIZE_JS}),
        node(
            "mas-control-plane-postgres-v1",
            "Execute control-plane SQL",
            "n8n-nodes-base.postgres",
            2.6,
            (840, 0),
            {
                "operation": "executeQuery",
                "query": "={{ $json.query }}",
                "options": {"queryReplacement": "={{ $json.params }}", "queryBatching": "independently", "largeNumbersOutput": "text", "replaceEmptyStrings": False},
            },
            alwaysOutputData=True,
            credentials={"postgres": {"id": "REPLACE_IN_UI", "name": "REPLACE: SCHEDULE PostgreSQL / PGVector credential"}},
        ),
        node("mas-control-plane-format-v1", "Format control-plane response", "n8n-nodes-base.code", 2, (1120, 0), {"jsCode": FORMAT_JS}),
        node(
            "mas-control-plane-response-v1",
            "Respond control-plane",
            "n8n-nodes-base.respondToWebhook",
            1.4,
            (1400, 0),
            {"respondWith": "json", "responseBody": "={{ $json }}", "options": {}},
        ),
    ]
    connections = {
        "MAS control-plane webhook": {"main": [[{"node": "Operator flags", "type": "main", "index": 0}]]},
        "Normalize control-plane request": {"main": [[{"node": "Execute control-plane SQL", "type": "main", "index": 0}]]},
        "Execute control-plane SQL": {"main": [[{"node": "Format control-plane response", "type": "main", "index": 0}]]},
        "Format control-plane response": {"main": [[{"node": "Respond control-plane", "type": "main", "index": 0}]]},
        "Operator flags": {"main": [[{"node": "Normalize control-plane request", "type": "main", "index": 0}]]},
    }
    return {
        "id": WF_ID,
        "name": WF_NAME,
        "active": False,
        "isArchived": False,
        "settings": {
            "executionOrder": "v1",
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "all",
            "saveManualExecutions": True,
            "saveExecutionProgress": False,
        },
        "nodes": nodes,
        "connections": connections,
        "pinData": {
            "MAS control-plane webhook": [
                {"json": {"body": {"operation": "schema"}, "webhookUrl": "http://localhost:5678/webhook-test/mas-control-plane"}}
            ]
        },
    }


SQL_HEADER_02 = """-- Additive MAS control plane. Safe on a live n8n database: CREATE IF NOT EXISTS only.
-- Never DROP n8n tables. postgres-init runs only on a fresh volume. Lab also applies this via psql.
-- Generated by n8n/templates/generate_mas_control_plane_proxy.py (agent_registry from mas_agent_registry.py) — do not edit by hand.
"""

SQL_02_TABLES = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cases_status_check CHECK (
        status IN (__CASE_STATUSES__)
    )
);

-- Existing databases: re-create the CHECK so new statuses (waiting_agent) are accepted.
ALTER TABLE cases DROP CONSTRAINT IF EXISTS cases_status_check;
ALTER TABLE cases ADD CONSTRAINT cases_status_check CHECK (
    status IN (__CASE_STATUSES__)
);

CREATE TABLE IF NOT EXISTS events (
    event_id BIGSERIAL PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    task_id TEXT,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    agent_id TEXT,
    status TEXT,
    status_message TEXT,
    handoff_message TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_case_id_event_id_idx ON events (case_id, event_id);

CREATE TABLE IF NOT EXISTS error_traces (
    error_id BIGSERIAL PRIMARY KEY,
    case_id TEXT,
    execution_id TEXT,
    workflow_name TEXT,
    node_name TEXT,
    error_message TEXT,
    error_type TEXT,
    stack TEXT,
    input_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS error_traces_case_id_idx ON error_traces (case_id);
CREATE INDEX IF NOT EXISTS error_traces_execution_id_idx ON error_traces (execution_id);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    case_id TEXT,
    workflow_name TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS executions_case_id_idx ON executions (case_id);

""".replace("__CASE_STATUSES__", ", ".join(f"'{s}'" for s in CASE_STATUSES))


def sql_02() -> str:
    registry = create_table_sql(pretty=True)
    seed = seed_rows_sql(("excel_extractor", "calculation_agent"), mode="update", pretty=True)
    return SQL_HEADER_02 + SQL_02_TABLES + registry + "\n\n" + seed + "\n"


def sql_03() -> str:
    return (
        "-- Register Schedule Builder after the Python service exists.\n"
        "-- Generated by n8n/templates/generate_mas_control_plane_proxy.py — do not edit by hand.\n\n"
        + seed_rows_sql(("schedule_builder",), mode="update", pretty=True)
        + "\n"
    )


def sql_activity() -> str:
    return (
        "-- MAS control plane schema for mas-activity-service (contract copy).\n"
        "-- Executed through MAS — Control Plane Proxy (`schema`), never directly from Python.\n"
        "-- Generated from postgres-init/02-mas-control-plane.sql + 03-schedule-builder-registry.sql.\n\n"
        + sql_02()
        + "\n"
        + sql_03()
    )


def main() -> None:
    wf = build_workflow()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SQL_02.write_text(sql_02(), encoding="utf-8")
    SQL_03.write_text(sql_03(), encoding="utf-8")
    SQL_ACTIVITY.write_text(sql_activity(), encoding="utf-8")
    # Activity's in-memory registry (no Control Plane Proxy configured — tests, offline harness) must be
    # the same rows the database gets, so the seed is exported as data, not retyped in Python.
    SEED_JSON.write_text(json.dumps(seed_as_python(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT} ({len(wf['nodes'])} nodes) + {SQL_02.name}, {SQL_03.name}, "
        f"{SQL_ACTIVITY.relative_to(REPO)}, {SEED_JSON.relative_to(REPO)}"
    )


if __name__ == "__main__":
    main()
