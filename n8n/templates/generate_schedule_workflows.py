"""Generate live SCHEDULE RAG workflows for n8n 2.30.8.

Emits Knowledge Ingestion + Hybrid Retrieval to core/.
Live emit is FastAPI Agent — Schedule Builder.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from schedule_rag_workflows import build_ingestion, build_retrieval

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "n8n" / "workflows"
CORE = WORKFLOWS / "core"


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "omegalul/schedule-foundation/" + name))


def node(name, type_, version, pos, parameters, **extra):
    v = {
        "parameters": parameters,
        "id": uid(name),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": list(pos),
    }
    v.update(extra)
    return v


def note(name, pos, content, w=440, h=300):
    return node(
        name,
        "n8n-nodes-base.stickyNote",
        1,
        pos,
        {"content": content, "width": w, "height": h, "color": 5},
    )


def code(name, pos, js, **extra):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js.strip()}, **extra)


def set_fields(name, pos, fields):
    return node(
        name,
        "n8n-nodes-base.set",
        3.4,
        pos,
        {
            "assignments": {
                "assignments": [
                    {
                        "id": uid(name + "/field/" + str(i)),
                        "name": field,
                        "value": value,
                        "type": type_,
                    }
                    for i, (field, value, type_) in enumerate(fields, 1)
                ]
            },
            "options": {},
            "includeOtherFields": True,
        },
    )


def trigger(name, pos, example):
    return node(
        name,
        "n8n-nodes-base.executeWorkflowTrigger",
        1.2,
        pos,
        {"inputSource": "jsonExample", "jsonExample": json.dumps(example, ensure_ascii=False)},
    )


def ifnode(name, pos, left, right=True, value_type="boolean"):
    return node(
        name,
        "n8n-nodes-base.if",
        2.2,
        pos,
        {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "strict",
                    "version": 2,
                },
                "conditions": [
                    {
                        "id": uid(name + "/condition"),
                        "leftValue": left,
                        "rightValue": right,
                        "operator": {"type": value_type, "operation": "equals"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
    )


def connect(c, s, t, out="main", idx=0, itype="main", target_idx=0):
    a = c.setdefault(s, {}).setdefault(out, [])
    while len(a) <= idx:
        a.append([])
    a[idx].append({"node": t, "type": itype, "index": target_idx})


def workflow(name, description, nodes, c, contract):
    settings = {
        "executionOrder": "v1",
        "saveManualExecutions": True,
        "callerPolicy": "workflowsFromSameOwner",
        "errorWorkflow": "e1f0a7c2-9b4d-5e8f-a123-4567890abcde",
    }
    return {
        "id": uid(name),
        "name": name,
        "description": description,
        "nodes": nodes,
        "pinData": {},
        "connections": c,
        "active": False,
        "settings": settings,
        "versionId": uid(name + "/version"),
        "meta": {
            "templateCredsSetupCompleted": False,
            "targetN8nVersion": "2.30.8",
            "contractVersion": contract,
        },
        "tags": [],
    }


def build_all():
    helpers = dict(
        node=node,
        note=note,
        code=code,
        trigger=trigger,
        ifnode=ifnode,
        connect=connect,
        workflow=workflow,
    )
    return {
        "tnavigator-schedule-knowledge-ingestion.workflow.json": build_ingestion(
            set_fields=set_fields, **helpers
        ),
        "tnavigator-schedule-hybrid-retrieval.workflow.json": build_retrieval(**helpers),
    }


def main():
    CORE.mkdir(parents=True, exist_ok=True)
    for fn, w in build_all().items():
        dest = CORE / fn
        dest.write_text(json.dumps(w, ensure_ascii=False, indent=2) + "\n")
        print(dest.relative_to(ROOT), len(w["nodes"]))
    import subprocess
    import sys

    subprocess.check_call(
        [sys.executable, str(Path(__file__).resolve().parent / "relayout_core_workflows.py")]
    )


if __name__ == "__main__":
    main()
