#!/usr/bin/env python3
"""Generate MAS — Ensure Control Plane: CREATE IF NOT EXISTS from n8n UI."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OUT = ROOT / "workflows/core/mas-ensure-control-plane.workflow.json"
ACTIVITY_SQL = REPO / "mas-activity-service/app/sql/control_plane.sql"
WF_ID = "b7c4e2a1-5d80-5c11-9f4a-0e8d3a6b1c72"
WF_NAME = "MAS — Ensure Control Plane"
PG = {"postgres": {"id": "REPLACE_IN_UI", "name": "REPLACE: SCHEDULE PostgreSQL / PGVector credential"}}
SQL_DIR = REPO / "postgres-init"

VERIFY_SQL = """SELECT
  to_regclass('public.cases') IS NOT NULL AS cases_ok,
  to_regclass('public.events') IS NOT NULL AS events_ok,
  to_regclass('public.error_traces') IS NOT NULL AS error_traces_ok,
  to_regclass('public.executions') IS NOT NULL AS executions_ok,
  to_regclass('public.agent_registry') IS NOT NULL AS agent_registry_ok,
  (SELECT COUNT(*) FROM agent_registry) AS agent_count
"""


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-ensure-cp:{name}"))


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


def postgres(name, pos, query):
    return node(
        name,
        "n8n-nodes-base.postgres",
        2.6,
        pos,
        {
            "operation": "executeQuery",
            "query": query.strip() + "\n",
            "options": {
                "queryReplacement": "={{ [] }}",
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


def sql_statements(sql_text: str) -> list[str]:
    """Split on ';' outside of SQL string literals. Full-line `--` comments are dropped first."""
    cleaned = "\n".join(
        line for line in sql_text.splitlines() if not line.strip().startswith("--")
    )
    stmts: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "'" and in_string:
            if i + 1 < len(cleaned) and cleaned[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_string = False
            buf.append(ch)
            i += 1
            continue
        if ch == "'" and not in_string:
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and not in_string:
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts



def init_sql() -> str:
    return "\n\n".join(
        [
            (SQL_DIR / "02-mas-control-plane.sql").read_text(encoding="utf-8").strip(),
            (SQL_DIR / "03-schedule-builder-registry.sql").read_text(encoding="utf-8").strip(),
        ]
    )


def bundled_sql() -> str:
    header = (
        "-- Additive MAS control plane. Safe on a live n8n database: CREATE IF NOT EXISTS only.\n"
        "-- Never DROP n8n tables. postgres-init runs only on a fresh volume. Lab also applies this via psql.\n"
        "-- Generated from postgres-init/02-mas-control-plane.sql + 03-schedule-builder-registry.sql.\n"
    )
    return header + "\n" + init_sql() + "\n"


def ddl_statements() -> list[str]:
    return sql_statements(init_sql())


def stmt_node_name(stmt: str, index: int) -> str:
    compact = " ".join(stmt.split())
    if compact.upper().startswith("CREATE TABLE IF NOT EXISTS "):
        table = compact.split()[5]
        return f"Ensure table {table}"
    if compact.upper().startswith("CREATE INDEX IF NOT EXISTS "):
        index_name = compact.split()[5]
        return f"Ensure index {index_name}"
    if "schedule_builder" in compact:
        return "Seed agent_registry schedule_builder"
    if "excel_extractor" in compact:
        return "Seed agent_registry excel+calc"
    return f"Ensure statement {index}"


def main() -> None:
    stmts = ddl_statements()
    if len(stmts) < 8:
        raise SystemExit(f"expected control-plane DDL statements, got {len(stmts)}")
    names = [stmt_node_name(stmt, i) for i, stmt in enumerate(stmts, 1)]
    if len(names) != len(set(names)):
        raise SystemExit(f"duplicate ensure node names: {names}")

    ACTIVITY_SQL.parent.mkdir(parents=True, exist_ok=True)
    ACTIVITY_SQL.write_text(bundled_sql(), encoding="utf-8")

    nodes = [
        node(
            "edit after import",
            "n8n-nodes-base.stickyNote",
            1,
            (-220, -440),
            {
                "content": (
                    "## edit after import\n\n"
                    "**MAS — Ensure Control Plane** — once after UI import "
                    "when you cannot psql the n8n database:\n\n"
                    "1. Bind the same Postgres credential as Orchestrator — MAS\n"
                    "2. Open this workflow → **Execute Workflow**\n"
                    "3. Last node: `ok: true`, `agent_count` ≥ 3\n\n"
                    "CREATE IF NOT EXISTS only. Never DROP n8n tables.\n"
                    "The DB role used by n8n must be allowed to CREATE TABLE.\n"
                    "pgvector (`CREATE EXTENSION vector`) is a DBA step for RAG, not this workflow."
                ),
                "height": 400,
                "width": 520,
                "color": 1,
            },
        ),
        node("When clicking Execute workflow", "n8n-nodes-base.manualTrigger", 1, (0, 0), {}),
    ]
    x = 260
    for name, stmt in zip(names, stmts):
        nodes.append(postgres(name, (x, 0), stmt))
        x += 220
    nodes.append(postgres("Verify control plane", (x, 0), VERIFY_SQL))
    x += 240
    nodes.append(
        node(
            "Format ensure ack",
            "n8n-nodes-base.code",
            2,
            (x, 0),
            {
                "jsCode": (
                    "const x=$json||{};\n"
                    "const flag=k=>x[k]===true||x[k]==='t'||x[k]==='true'||x[k]===1||x[k]==='1';\n"
                    "const count=Number(x.agent_count||0);\n"
                    "const ok=flag('cases_ok')&&flag('events_ok')&&flag('error_traces_ok')"
                    "&&flag('executions_ok')&&flag('agent_registry_ok')&&count>=3;\n"
                    "return [{json:{contract:'mas_control_plane_ready',ok,"
                    "cases_ok:flag('cases_ok'),events_ok:flag('events_ok'),"
                    "error_traces_ok:flag('error_traces_ok'),executions_ok:flag('executions_ok'),"
                    "agent_registry_ok:flag('agent_registry_ok'),agent_count:count}}];"
                )
            },
        )
    )

    connections = {}
    chain = ["When clicking Execute workflow", *names, "Verify control plane", "Format ensure ack"]
    for src, dst in zip(chain, chain[1:]):
        connect(connections, src, dst)

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
    print(f"wrote {ACTIVITY_SQL}")


if __name__ == "__main__":
    main()
