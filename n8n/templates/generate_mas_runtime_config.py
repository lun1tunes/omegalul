#!/usr/bin/env python3
"""Generate MAS — Runtime Config: one Set of service URLs for n8n 2.30.8 free.

Callers (Orchestrator, Excel Extractor, Schedule Builder) execute this
sub-workflow instead of duplicating Set nodes. Excel API key is a Header Auth
credential on HTTP nodes, not a field here. No $env / $vars.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/mas-runtime-config.workflow.json"
WF_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "mas-runtime-config"))
WF_NAME = "MAS — Runtime Config"
PLACEHOLDER = "REPLACE_MAS_RUNTIME_CONFIG_IN_UI"
EXCEL_KEY_CRED = {
    "httpHeaderAuth": {
        "id": "REPLACE_IN_UI",
        "name": "REPLACE: Excel Tools X-API-Key",
    }
}

# Lab Compose DNS. Field: overwrite these values in the Set after UI import.
LAB_URLS = (
    ("activity_base_url", "http://mas-activity:8200"),
    ("excel_tools_url", "http://excel-tools:8000"),
    ("schedule_service_url", "http://schedule-builder:8090"),
    ("math_url", "http://math-service:8100"),
    ("orchestrator_step_url", "http://127.0.0.1:5678/webhook/mas-orchestrator-step"),
    # Orchestrator step budget per case (not a URL). When reached with a completed result the
    # engineer is asked to accept / rework; without any result the case fails. UI-editable.
    ("max_steps", "12"),
)


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-runtime-config:{name}"))


def runtime_config_execute_params() -> dict:
    """executeWorkflow 1.3 parameters targeting this loader. No payload passthrough."""
    return {
        "source": "database",
        "workflowId": {
            "__rl": True,
            "value": PLACEHOLDER,
            "mode": "list",
            "cachedResultName": WF_NAME,
        },
        "workflowInputs": {
            "mappingMode": "defineBelow",
            "value": {},
            "matchingColumns": [],
            "schema": [],
            "attemptToConvertTypes": False,
            "convertFieldsToString": False,
        },
        "mode": "once",
        "options": {"waitForSubWorkflow": True},
    }


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


def main() -> None:
    assignments = [
        {
            "id": nid(f"url/{key}"),
            "name": key,
            "value": value,
            "type": "string",
        }
        for key, value in LAB_URLS
    ]
    nodes = [
        node(
            "Runtime Config README",
            "n8n-nodes-base.stickyNote",
            1,
            (-220, -360),
            {
                "content": (
                    "## Runtime URLs (edit in UI)\n\n"
                    "**MAS — Runtime Config** — единственный Set с URL сервисов "
                    "(n8n free: без Variables / Environment в JSON).\n\n"
                    "1. Пропишите URL в **Runtime URLs**. Lab Compose DNS уже стоит. "
                    "Поле: `http://<IP-Windows>:8200` / `:8000` / `:8090` / `:8100`.\n"
                    "2. На Orchestrator / Excel / Schedule привяжите Execute Workflow → "
                    "этот workflow (ноды **Runtime endpoints** / **Runtime configuration**).\n"
                    "3. Ключ Excel **не** сюда. Credential Header Auth "
                    "**Excel Tools X-API-Key**: header name `X-API-Key`, value = "
                    "`API_KEY` из `excel-tools.env`. Привяжите его на HTTP-нодах "
                    "Agent — Excel Extractor.\n\n"
                    "Возвращает "
                    "`activity_base_url`, `excel_tools_url`, `schedule_service_url`, "
                    "`math_url`, `orchestrator_step_url`, `max_steps`. Ничего не оркестрирует.\n\n"
                    "`max_steps` — бюджет шагов оркестратора на кейс (по умолчанию 12): при достижении "
                    "с готовым результатом инженеру предлагается принять/доработать, без результата — кейс failed.\n\n"
                    "`orchestrator_step_url` — внутренний webhook оркестратора "
                    "(loop сам себя, не через Activity `/run`). Lab: "
                    "`http://127.0.0.1:5678/webhook/mas-orchestrator-step`."
                ),
                "height": 400,
                "width": 520,
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
                "jsonExample": json.dumps({"environment": "lab"}, ensure_ascii=False),
            },
        ),
        node(
            "Runtime URLs",
            "n8n-nodes-base.set",
            3.4,
            (260, 0),
            {
                "assignments": {"assignments": assignments},
                "options": {},
                "includeOtherFields": False,
            },
        ),
    ]
    connections = {
        "When executed by another workflow": {
            "main": [[{"node": "Runtime URLs", "type": "main", "index": 0}]]
        }
    }
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
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "all",
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
