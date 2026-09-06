#!/usr/bin/env python3
"""Fast lab soft-redeploy: keep volumes, refresh workflows, Health Check.

Does NOT wipe docker volumes. Reimports full_clean_import_set, binds live IDs,
syncs workflow_history, publishes (Control Plane Proxy first), restarts n8n,
starts mas-activity after the proxy webhook is registered, runs Health Check.

Does not wipe MAS cases unless --wipe is passed.

Usage:
  python3 scripts/lab_soft_redeploy.py
  python3 scripts/lab_soft_redeploy.py --wipe
  python3 scripts/lab_soft_redeploy.py --skip-health
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "n8n/import-manifest.json").read_text(encoding="utf-8"))
USER_ID = "8974d698-6d13-4fc8-9693-a39e363197a2"
PROJECT_ID = "iaz2Dk8BIQ5ZolyY"
DT_TASK = "eotasksv1"
DT_TRACE = "mastracev1"
CRED_PG = "TXTEmuvI2W5Q4ckW"
CRED_OA = "hmOqhmlEN8Kxampr"
CRED_HDR = "TZWvrKzFO7hsdoZY"

PLACEHOLDERS = {
    "REPLACE_CAS_PERSIST_IN_UI": "CAS — Persist Task State",
    "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI": "MAS — Knowledge Retrieval",
    "REPLACE_SCHEDULE_BUILDER_IN_UI": "SCHEDULE — Builder",
    "REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI": "Agent — Schedule Builder",
    "REPLACE_MAS_RUNTIME_CONFIG_IN_UI": "MAS — Runtime Config",
    "REPLACE_CALCULATION_AGENT_IN_UI": "Agent — Calculation (Math Service)",
    "REPLACE_MAS_TRACE_WRITER_IN_UI": "Writer — MAS Trace",
    "REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI": "Agent — Excel Extractor",
    "REPLACE_HEALTH_ORCHESTRATOR_IN_UI": "Orchestrator — MAS",
    "REPLACE_HEALTH_TRACE_IN_UI": "Writer — MAS Trace",
    "REPLACE_ORCHESTRATOR_ID_IN_UI": "Orchestrator — MAS",
    "REPLACE_ORCHESTRATOR_ID_IN_UI_HUMAN_GATE_STATUS": "Orchestrator — MAS",
    "REPLACE_ORCHESTRATOR_ID_IN_UI_HUMAN_GATE_RESUME": "Orchestrator — MAS",
    "REPLACE_ERROR_HANDLER_IN_UI": "Error — MAS Node Traces",
}

# Optional stubs — bind when imported so Orchestrator activate/publish can succeed.
OPTIONAL_PLACEHOLDERS = {
    "REPLACE_CLUSTER_CALC_ADAPTER_IN_UI": "Template — Cluster Calculation Adapter",
    "REPLACE_BINARY_RESULTS_ADAPTER_IN_UI": "Template — Binary Results Adapter",
    "REPLACE_PRESENTATION_ADAPTER_IN_UI": "Template — Presentation Assembler",
    "REPLACE_DATA_SPECIALIST_IN_UI": "Template — Engineering Specialist",
    "REPLACE_DOCUMENT_SPECIALIST_IN_UI": "Template — Engineering Specialist",
}

# Publish/activate order: Control Plane Proxy first (Activity cannot boot
# without /webhook/mas-control-plane), then RAG/specialists, Orchestrator, forms.
PUBLISH = [
    "MAS — Control Plane Proxy",
    "MAS — Runtime Config",
    "MAS — Knowledge Retrieval",
    "MAS — Knowledge Ingestion",
    "Error — MAS Node Traces",
    "Agent — Schedule Builder",
    "Agent — Excel Extractor",
    "Orchestrator — MAS",
    "Form — MAS Deployment Health Check",
]

STALE_WORKFLOW_NAMES = (
    "Activity — List Tasks (Data Table)",
    "Activity — Load Feed (Data Tables)",
    "Activity — Hydrate (Data Tables)",
    "Orchestrator — Engineering MAS",
    "CAS — Persist Task State",
    "Writer — MAS Trace",
    "Error — MAS Case Handler",
    "Form — MAS Entry",
    "Form — MAS Human Gate",
    "Agent — Calculation (Math Service)",
    "SCHEDULE — Builder",
    "Agent — Excel Extractor (legacy webhook)",
    "MAS — Ensure Control Plane",
)

ERROR_WORKFLOW_SKIP = (
    "Error — MAS Node Traces",
    "Error — MAS Case Handler",
    "Writer — MAS Trace",
    "CAS — Persist Task State",
    "MAS — Control Plane Proxy",
    "MAS — Runtime Config",
)


def run(cmd: list[str], check: bool = True, timeout: int | None = 120) -> subprocess.CompletedProcess:
    cp = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout)
    if check and cp.returncode != 0:
        print(cp.stdout[-2000:], file=sys.stderr)
        print(cp.stderr[-2000:], file=sys.stderr)
        raise SystemExit(cp.returncode)
    return cp


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    chat_model = str(os.environ.get("N8N_CHAT_MODEL") or "").strip()
    if chat_model:
        out["N8N_CHAT_MODEL"] = chat_model
    return out


def psql(sql: str) -> str:
    return run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "n8n", "-d", "n8n", "-t", "-A", "-c", sql]
    ).stdout.strip()


def _merge_activity_env(env: dict[str, str]) -> dict[str, str]:
    """Auth for the control-plane webhook lives in mas-activity.env on the lab."""
    merged = dict(env)
    extra = ROOT / "mas-activity-service" / "mas-activity.env"
    if extra.is_file():
        for line in extra.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key and key not in merged:
                merged[key] = val.strip().strip('"').strip("'")
    return merged


def wipe_mas_via_proxy(env: dict[str, str]) -> bool:
    """Clear MAS case tables through n8n, not docker exec psql."""
    cfg = _merge_activity_env(env)
    port = cfg.get("N8N_HOST_PORT", "15678")
    url = f"http://127.0.0.1:{port}/webhook/mas-control-plane"
    header = cfg.get("CONTROL_PLANE_PROXY_AUTH_HEADER") or "Authorization"
    value = cfg.get("CONTROL_PLANE_PROXY_AUTH_VALUE") or "local-orch-inbound"
    body = json.dumps({"operation": "wipe"}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", header: value},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        print("control-plane wipe skipped HTTP", exc.code, raw[:240])
        return False
    except Exception as exc:  # noqa: BLE001
        print("control-plane wipe skipped", exc)
        return False
    if not raw.strip():
        print("control-plane wipe skipped empty response")
        return False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("control-plane wipe skipped non-JSON", status, raw[:240])
        return False
    result = payload.get("result") if isinstance(payload, dict) else None
    wiped = isinstance(result, dict) and result.get("wiped") is True
    ok = isinstance(payload, dict) and payload.get("ok") is True and wiped
    print("control-plane wipe", result or payload)
    return ok


def wipe_state(env: dict[str, str]) -> None:
    print("== wipe CAS / trace / activity ==")
    mas_wiped = wipe_mas_via_proxy(env)
    mas_sql = "" if mas_wiped else """
  IF to_regclass('public.events') IS NOT NULL THEN TRUNCATE events CASCADE; END IF;
  IF to_regclass('public.error_traces') IS NOT NULL THEN TRUNCATE error_traces CASCADE; END IF;
  IF to_regclass('public.executions') IS NOT NULL THEN TRUNCATE executions CASCADE; END IF;
  IF to_regclass('public.mas_artifacts') IS NOT NULL THEN TRUNCATE mas_artifacts CASCADE; END IF;
  IF to_regclass('public.cases') IS NOT NULL THEN TRUNCATE cases CASCADE; END IF;
"""
    psql(
        f"""
DO $$
BEGIN
  IF to_regclass('public.execution_data') IS NOT NULL THEN TRUNCATE execution_data CASCADE; END IF;
  IF to_regclass('public.execution_entity') IS NOT NULL THEN TRUNCATE execution_entity CASCADE; END IF;
  IF to_regclass('public.data_table_user_eotasksv1') IS NOT NULL THEN TRUNCATE data_table_user_eotasksv1; END IF;
  IF to_regclass('public.data_table_user_mastracev1') IS NOT NULL THEN TRUNCATE data_table_user_mastracev1; END IF;
{mas_sql}
END$$;
"""
    )
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "mas-activity",
            "python",
            "-c",
            "from pathlib import Path; p=Path('/app/data/activity_state.json'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{\"tasks\":{}}')",
        ],
        check=False,
    )
    req = urllib.request.Request(
        f"http://127.0.0.1:{env.get('MAS_ACTIVITY_HOST_PORT', '8200')}/v1/tasks",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
        print("activity tasks", len(body.get("tasks") or []))
    except Exception as exc:  # noqa: BLE001
        print("activity probe", exc)


def import_workflows() -> None:
    print("== import workflows ==")
    files = [ROOT / "n8n" / rel for rel in MANIFEST["full_clean_import_set"]]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise SystemExit(f"missing workflows: {missing}")
    for path in files:
        remote = f"/tmp/import-{path.name}"
        run(["docker", "compose", "cp", str(path), f"n8n:{remote}"])
        cp = run(
            ["docker", "compose", "exec", "-T", "n8n", "n8n", "import:workflow", f"--input={remote}"],
            check=False,
            timeout=180,
        )
        status = "OK" if cp.returncode == 0 else "FAIL"
        print(f"import {path.name}: {status}")
        if cp.returncode != 0:
            print((cp.stderr or cp.stdout)[-500:])


def wf_map() -> dict[str, str]:
    raw = psql("SELECT name || E'\\t' || id FROM workflow_entity ORDER BY name;")
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        name, wid = line.split("\t", 1)
        out[name] = wid
    return out


def fetch_nodes(wid: str):
    raw = psql(f"SELECT nodes::text FROM workflow_entity WHERE id = '{wid}';")
    return json.loads(raw)


def save_nodes(wid: str, nodes) -> None:
    payload = json.dumps(nodes, ensure_ascii=False)
    tag = "masnodes"
    while f"${tag}$" in payload:
        tag += "x"
    sql_path = Path(tempfile.mkstemp(prefix="wf-nodes-", suffix=".sql")[1])
    remote = f"/tmp/wf-nodes-{wid}.sql"
    try:
        sql_path.write_text(
            f"UPDATE workflow_entity SET nodes = ${tag}${payload}${tag}$::json WHERE id = '{wid}';\n",
            encoding="utf-8",
        )
        run(["docker", "compose", "cp", str(sql_path), f"postgres:{remote}"])
        run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "n8n",
                "-d",
                "n8n",
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                remote,
            ]
        )
    finally:
        sql_path.unlink(missing_ok=True)


def apply_control_plane_sql() -> None:
    print("== apply MAS control-plane SQL ==")
    for name in ("02-mas-control-plane.sql", "03-schedule-builder-registry.sql"):
        path = ROOT / "postgres-init" / name
        remote = f"/tmp/{name}"
        run(["docker", "compose", "cp", str(path), f"postgres:{remote}"])
        run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "n8n",
                "-d",
                "n8n",
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                remote,
            ]
        )
        print(f"applied {name}")


def ensure_qwen_credential(env: dict[str, str]) -> str:
    """Create or reuse OpenAI-compatible Qwen credential. Returns live n8n id."""
    existing = psql("SELECT name || E'\\t' || id FROM credentials_entity;")
    for line in existing.splitlines():
        if "\t" not in line:
            continue
        name, cid = line.split("\t", 1)
        if name.strip() == "Qwen OpenAI-compatible":
            print(f"qwen credential exists id={cid.strip()}")
            return cid.strip()
    key = (env.get("QWEN_API_KEY") or "").strip()
    url = (env.get("QWEN_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if not key:
        print("qwen credential skip: QWEN_API_KEY missing")
        return ""
    import http.cookiejar

    user = env.get("N8N_USERNAME") or env.get("N8N_USER")
    password = env.get("N8N_PASSWORD")
    if not user or not password:
        print("qwen credential skip: no n8n login")
        return ""
    base = f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '15678')}"
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    login = urllib.request.Request(
        base + "/rest/login",
        data=json.dumps({"emailOrLdapLoginId": user, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener.open(login, timeout=30)
    payload = {
        "name": "Qwen OpenAI-compatible",
        "type": "openAiApi",
        "data": {"apiKey": key, "url": url},
    }
    req = urllib.request.Request(
        base + "/rest/credentials",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=60) as resp:
        body = json.loads(resp.read().decode() if resp.length != 0 else b"{}")
    cred = body.get("data") or body
    cid = str(cred.get("id") or "").strip()
    if not cid:
        raise SystemExit(f"qwen credential create missing id: {list(body)[:8]}")
    if PROJECT_ID:
        psql(
            "INSERT INTO shared_credentials (\"credentialsId\", \"projectId\", role) "
            f"VALUES ('{cid}', '{PROJECT_ID}', 'credential:owner') ON CONFLICT DO NOTHING;"
        )
    print(f"qwen credential created id={cid}")
    return cid


def ensure_excel_tools_header_auth(env: dict[str, str]) -> str:
    """Header Auth credential for Excel FastAPI (X-API-Key). Not the inbound webhook key."""
    wanted = "Excel Tools X-API-Key"
    existing = psql("SELECT name || E'\\t' || id FROM credentials_entity;")
    found = ""
    for line in existing.splitlines():
        if "\t" not in line:
            continue
        name, cid = line.split("\t", 1)
        if name.strip() == wanted:
            found = cid.strip()
            print(f"excel header auth exists id={found}")
            break
    key = (env.get("EXCEL_TOOLS_API_KEY") or env.get("excel_tools_api_key") or "").strip()
    user = env.get("N8N_USERNAME") or env.get("N8N_USER")
    password = env.get("N8N_PASSWORD")
    if not user or not password:
        print("excel header auth skip: no n8n login")
        return found
    import http.cookiejar

    base = f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '15678')}"
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    login = urllib.request.Request(
        base + "/rest/login",
        data=json.dumps({"emailOrLdapLoginId": user, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener.open(login, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print("excel header auth login failed", exc)
        return found
    payload = {
        "name": wanted,
        "type": "httpHeaderAuth",
        "data": {"name": "X-API-Key", "value": key or "local-dev-excel-tools-api-key"},
    }
    if found:
        for method, path in (
            ("PATCH", f"/rest/credentials/{found}"),
            ("PUT", f"/rest/credentials/{found}"),
        ):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method=method,
            )
            try:
                opener.open(req, timeout=60)
                print(f"excel header auth synced id={found}")
                return found
            except urllib.error.HTTPError:
                continue
        return found
    req = urllib.request.Request(
        base + "/rest/credentials",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=60) as resp:
        body = json.loads(resp.read().decode() if resp.length != 0 else b"{}")
    cred = body.get("data") or body
    cid = str(cred.get("id") or "").strip()
    if not cid:
        raise SystemExit(f"excel header auth create missing id: {list(body)[:8]}")
    if PROJECT_ID:
        psql(
            "INSERT INTO shared_credentials (\"credentialsId\", \"projectId\", role) "
            f"VALUES ('{cid}', '{PROJECT_ID}', 'credential:owner') ON CONFLICT DO NOTHING;"
        )
    print(f"excel header auth created id={cid}")
    return cid


def bind_error_workflow(name_to_id: dict[str, str]) -> None:
    """Point Settings → Error workflow at Error — MAS Node Traces (live n8n IDs)."""
    hid = name_to_id.get("Error — MAS Node Traces") or name_to_id.get("Error — MAS Case Handler")
    if not hid:
        print("bind errorWorkflow skip: handler missing")
        return
    print("== bind n8n Error workflow ==")
    for name, wid in sorted(name_to_id.items()):
        target = "" if name in ERROR_WORKFLOW_SKIP else hid
        escaped = target.replace("'", "''")
        psql(
            "UPDATE workflow_entity "
            "SET settings = jsonb_set(COALESCE(settings::jsonb, '{}'::jsonb), "
            f"'{{errorWorkflow}}', to_jsonb('{escaped}'::text)) "
            f"WHERE id = '{wid}';"
        )
        print(f"errorWorkflow {name}: {target or '(empty)'}")


def patch_nodes(nodes, name_to_id: dict[str, str], env: dict[str, str]) -> int:
    changed = 0
    # Compose DNS: n8n + agent containers reach Activity on the backend network.
    # Field Windows: set MAS_ACTIVITY_URL=http://<PC-IP>:8200 (corporate n8n is not in compose).
    activity_url = env.get("MAS_ACTIVITY_URL", "http://mas-activity:8200")
    # Same port as field Windows (8000). Compose excel-tools listens on 8000 too.
    excel_url = env.get("EXCEL_TOOLS_URL", "http://excel-tools:8000")
    math_url = env.get("MATH_SERVICE_URL", "http://math-service:8100")
    schedule_url = env.get("SCHEDULE_BUILDER_URL", "http://schedule-builder:8090")
    placeholders = dict(PLACEHOLDERS)
    for ph, target in OPTIONAL_PLACEHOLDERS.items():
        if target in name_to_id:
            placeholders[ph] = target

    for node in nodes:
        creds = node.get("credentials")
        if isinstance(creds, dict):
            for ctype, meta in creds.items():
                if not isinstance(meta, dict):
                    continue
                cname = str(meta.get("name") or "")
                if ctype == "postgres":
                    meta["id"] = CRED_PG
                    meta["name"] = "Postgres pgvector"
                    changed += 1
                elif ctype == "openAiApi":
                    qwen_id = str(env.get("_N8N_QWEN_CRED_ID") or "").strip()
                    if "qwen" in cname.lower() and qwen_id:
                        meta["id"] = qwen_id
                        meta["name"] = "Qwen OpenAI-compatible"
                    else:
                        meta["id"] = CRED_OA
                        meta["name"] = "OpenAI production"
                    changed += 1
                elif ctype == "httpHeaderAuth":
                    if "excel" in cname.lower() or "x-api-key" in cname.lower():
                        excel_hdr = str(env.get("_N8N_EXCEL_HDR_CRED_ID") or "").strip()
                        if excel_hdr:
                            meta["id"] = excel_hdr
                            meta["name"] = "Excel Tools X-API-Key"
                            changed += 1
                    else:
                        meta["id"] = CRED_HDR
                        meta["name"] = "Engineering orchestrator inbound key"
                        changed += 1
        params = node.get("parameters")
        if not isinstance(params, dict):
            continue
        chat_model = str(env.get("N8N_CHAT_MODEL") or "").strip()
        if chat_model and node.get("type") == "@n8n/n8n-nodes-langchain.lmChatOpenAi":
            model = params.get("model")
            if isinstance(model, dict):
                model["value"] = chat_model
                model["mode"] = "id"
                changed += 1
            elif model is None or isinstance(model, str):
                params["model"] = {"mode": "id", "value": chat_model}
                changed += 1
        dt = params.get("dataTableId")
        if isinstance(dt, dict) and dt.get("__rl"):
            cached = str(dt.get("cachedResultName") or "").lower()
            name = str(node.get("name") or "").lower()
            if "trace" in cached or "trace" in name:
                dt.update({"value": DT_TRACE, "mode": "list", "cachedResultName": "mas_trace_events_v1"})
            else:
                dt.update({"value": DT_TASK, "mode": "list", "cachedResultName": "engineering_orchestrator_tasks_v1"})
            changed += 1
        wid = params.get("workflowId")
        if isinstance(wid, dict) and wid.get("__rl"):
            val = str(wid.get("value") or "")
            cached = str(wid.get("cachedResultName") or "")
            if "Data Specialist" in cached or "Document Specialist" in cached:
                target = placeholders.get(val) or "Template — Engineering Specialist"
            else:
                target = placeholders.get(val) or (cached if cached in name_to_id else None)
                if cached == "SCHEDULE — Knowledge Retrieval":
                    target = "MAS — Knowledge Retrieval"
            if target and target in name_to_id:
                wid["value"] = name_to_id[target]
                wid["mode"] = "list"
                wid["cachedResultName"] = target
                changed += 1
        # n8n 2.30 production webhooks require a stable webhookId; missing id → 404 "not registered".
        if node.get("type") == "n8n-nodes-base.webhook" and not node.get("webhookId"):
            path = str((params.get("path") or "")).strip()
            stable = {
                "engineering-orchestrator": "a1000003-orch-wh-0001-8000-000000000002",
                "engineering-orchestrator-form": "a1000003-orch-form-0001-8000-000000000002",
                "mas-activity-hydrate": "a1000003-hyd-wh-0001-8000-000000000002",
                "mas-orchestrator-step": "a1000003-mas-orch-wh-0001-800000000001",
                "schedule-builder-agent": "a1000003-sched-agent-wh-0001-8000000001",
            }.get(path)
            if stable:
                node["webhookId"] = stable
                changed += 1
        if node.get("name") == "Runtime URLs":
            for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                key = assignment.get("name")
                if key == "excel_tools_url":
                    assignment["value"] = excel_url.rstrip("/")
                    changed += 1
                elif key == "schedule_service_url":
                    assignment["value"] = schedule_url.rstrip("/")
                    changed += 1
                elif key == "math_url":
                    assignment["value"] = math_url.rstrip("/").removesuffix("/agent/run")
                    changed += 1
                elif key == "activity_base_url":
                    assignment["value"] = activity_url.rstrip("/")
                    changed += 1
                elif key == "orchestrator_step_url":
                    assignment["value"] = env.get(
                        "ORCHESTRATOR_INTERNAL_WEBHOOK_URL",
                        "http://127.0.0.1:5678/webhook/mas-orchestrator-step",
                    )
                    changed += 1
        if node.get("name") in {"Runtime configuration", "Runtime endpoints"}:
            for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                key = assignment.get("name")
                if key == "excel_extractor_url":
                    assignment["value"] = excel_url.rstrip("/") + "/agent/run"
                    changed += 1
                elif key == "excel_tools_url":
                    assignment["value"] = excel_url.rstrip("/")
                    changed += 1
                elif key == "calculation_agent_url":
                    assignment["value"] = math_url.rstrip("/") + "/agent/run"
                    changed += 1
                elif key == "schedule_builder_url":
                    assignment["value"] = env.get(
                        "SCHEDULE_BUILDER_AGENT_URL",
                        "http://n8n:5678/webhook/schedule-builder-agent",
                    )
                    changed += 1
                elif key == "schedule_service_url":
                    assignment["value"] = schedule_url.rstrip("/")
                    changed += 1
                elif key == "orchestrator_step_url":
                    assignment["value"] = env.get(
                        "ORCHESTRATOR_INTERNAL_WEBHOOK_URL",
                        "http://127.0.0.1:5678/webhook/mas-orchestrator-step",
                    )
                    changed += 1
                elif key == "activity_base_url":
                    assignment["value"] = activity_url
                    changed += 1
        if node.get("name") == "Math Service Configuration":
            for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                key = assignment.get("name")
                if key == "math_service_url":
                    assignment["value"] = math_url
                    changed += 1
        if node.get("name") == "Activity connection":
            for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                key = assignment.get("name")
                if key == "activity_base_url":
                    assignment["value"] = activity_url
                    changed += 1
        js = params.get("jsCode")
        if isinstance(js, str) and "ACTIVITY_BASE_URL" in js and "Activity connection" not in js:
            js2 = re.sub(
                r"const ACTIVITY_BASE_URL=['\"][^'\"]*['\"]",
                f"const ACTIVITY_BASE_URL={json.dumps(activity_url)}",
                js,
            )
            if js2 != js:
                params["jsCode"] = js2
                changed += 1
    return changed


def sync_history() -> None:
    print("== sync workflow_history ==")
    psql(
        """
UPDATE workflow_history h
SET nodes = w.nodes,
    connections = w.connections,
    name = w.name,
    "updatedAt" = NOW()
FROM workflow_entity w
WHERE h."versionId" = w."versionId"
  AND h."workflowId" = w.id
  AND (
    h.nodes::text IS DISTINCT FROM w.nodes::text
    OR h.connections::text IS DISTINCT FROM w.connections::text
  );
"""
    )


def publish(name_to_id: dict[str, str]) -> None:
    """Publish+activate via REST. CLI `n8n publish:workflow` is a no-op for workflow_published_version in 2.30."""
    print("== publish/activate (REST) ==")
    env = load_env()
    user = env.get("N8N_USERNAME") or env.get("N8N_USER")
    password = env.get("N8N_PASSWORD")
    if not user or not password:
        print("missing N8N_USERNAME / N8N_PASSWORD — falling back to CLI publish")
        for name in PUBLISH:
            wid = name_to_id.get(name)
            if not wid:
                print(f"publish skip missing {name}")
                continue
            cp = run(["docker", "compose", "exec", "-T", "n8n", "n8n", "publish:workflow", f"--id={wid}"], check=False)
            print(f"publish {name}: {'OK' if cp.returncode == 0 else 'FAIL'}")
        return

    import http.cookiejar

    base = f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '15678')}"
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def rest(method: str, path: str, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            base + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with opener.open(req, timeout=180) as resp:
                raw = resp.read()
                return int(resp.status), json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw.decode("utf-8", errors="replace")[:500]
            return int(exc.code), parsed

    code, body = rest("POST", "/rest/login", {"emailOrLdapLoginId": user, "password": password})
    if code != 200:
        raise SystemExit(f"n8n REST login failed: {code} {body}")

    orch_header = env.get("ORCHESTRATOR_AUTH_HEADER") or "Authorization"
    orch_value = env.get("ORCHESTRATOR_AUTH_VALUE") or "local-orch-inbound"
    code, cred_body = rest(
        "PATCH",
        f"/rest/credentials/{CRED_HDR}",
        {
            "name": "Engineering orchestrator inbound key",
            "type": "httpHeaderAuth",
            "data": {"name": orch_header, "value": orch_value},
        },
    )
    if code >= 400:
        code, cred_body = rest(
            "PUT",
            f"/rest/credentials/{CRED_HDR}",
            {
                "name": "Engineering orchestrator inbound key",
                "type": "httpHeaderAuth",
                "data": {"name": orch_header, "value": orch_value},
            },
        )
    print(
        f"sync orchestrator inbound key: {'OK' if code < 400 else f'FAIL {code}'} "
        f"(header={orch_header}, value_len={len(orch_value)})"
    )

    for name in PUBLISH:
        wid = name_to_id.get(name)
        if not wid:
            print(f"publish skip missing {name}")
            continue
        code, wf = rest("GET", f"/rest/workflows/{wid}")
        if code != 200 or not isinstance(wf, dict):
            print(f"activate {name}: GET FAIL {code}")
            continue
        data = wf.get("data") or {}
        vid = data.get("versionId") or data.get("activeVersionId")
        if not vid:
            print(f"activate {name}: no versionId")
            continue
        if data.get("active"):
            # Force republish of current version so webhook registration is restored.
            rest("POST", f"/rest/workflows/{wid}/deactivate", {"versionId": vid})
        code, body = rest("POST", f"/rest/workflows/{wid}/activate", {"versionId": vid})
        ok = code == 200
        msg = ""
        if not ok and isinstance(body, dict):
            msg = str(body.get("message") or body)[:220]
        print(f"activate {name}: {'OK' if ok else f'FAIL {code} {msg}'}")

    for name in STALE_WORKFLOW_NAMES:
        wid = name_to_id.get(name)
        if not wid:
            print(f"retire skip missing {name}")
            continue
        code, wf = rest("GET", f"/rest/workflows/{wid}")
        data = wf.get("data") if isinstance(wf, dict) and isinstance(wf.get("data"), dict) else (wf or {})
        vid = data.get("versionId") or data.get("activeVersionId")
        if data.get("active") and vid:
            rest("POST", f"/rest/workflows/{wid}/deactivate", {"versionId": vid})
        code, body = rest("POST", f"/rest/workflows/{wid}/archive", {})
        if code >= 400:
            print(f"archive {name}: FAIL {code} {str(body)[:180]}")
            continue
        code, body = rest("DELETE", f"/rest/workflows/{wid}")
        print(f"retire {name}: {'OK' if code < 400 else f'FAIL {code} {str(body)[:180]}'}")


def wait_healthy(service: str, url: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        if service == "mas-activity":
            cp = run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "mas-activity",
                    "python",
                    "-c",
                    f"import urllib.request; urllib.request.urlopen({url!r}, timeout=3)",
                ],
                check=False,
            )
        else:
            cp = run(
                ["docker", "compose", "exec", "-T", service, "wget", "-q", "-O", "/dev/null", url],
                check=False,
            )
        if cp.returncode == 0:
            return
        time.sleep(1)
    raise SystemExit(f"{service} not healthy: {url}")


def run_health_form() -> str | None:
    """POST Health Check form using a real n8n session cookie (REST login)."""
    print("== health check form ==")
    env = load_env()
    user = env.get("N8N_USERNAME") or env.get("N8N_USER")
    password = env.get("N8N_PASSWORD")
    if not user or not password:
        print("missing N8N_USERNAME / N8N_PASSWORD in .env")
        return None
    base = f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '5678')}"

    def http_req(method: str, path: str, data: bytes | None = None, content_type: str | None = None, cookie: str = ""):
        headers = {
            "Accept": "text/html,application/json,*/*",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        if cookie:
            headers["Cookie"] = cookie
        if data is not None:
            headers["Content-Type"] = content_type or "application/json"
            headers["Content-Length"] = str(len(data))
        request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=300) as resp:
                return int(resp.status), resp.read().decode("utf-8", errors="replace"), resp.headers
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read().decode("utf-8", errors="replace"), exc.headers

    login_body = json.dumps({"emailOrLdapLoginId": user, "password": password}).encode()
    status, body, headers = http_req("POST", "/rest/login", login_body, "application/json")
    if status != 200:
        print("login failed", status, body[:200])
        return None
    cookie = ""
    set_cookies = []
    if hasattr(headers, "get_all"):
        set_cookies = headers.get_all("Set-Cookie") or []
    elif headers.get("Set-Cookie"):
        set_cookies = [headers.get("Set-Cookie")]
    for sc in set_cookies:
        if "n8n-auth=" in sc:
            cookie = sc.split(";", 1)[0].strip()
            if not cookie.startswith("n8n-auth="):
                # cookie may be in the middle of a combined header
                for piece in sc.split(","):
                    piece = piece.strip()
                    if piece.startswith("n8n-auth="):
                        cookie = piece.split(";", 1)[0].strip()
                        break
            break
    if not cookie:
        print("no n8n-auth cookie")
        return None

    status, body, _ = http_req("GET", "/form/mas-deployment-health-check", cookie=cookie)
    print("GET form", status, "bytes", len(body))
    if status != 200:
        print(body[:300])
        return None

    boundary = f"----mashealth{int(time.time())}"
    parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="operator_note"',
        "",
        "lab-soft-redeploy",
        f"--{boundary}--",
        "",
    ]
    post_body = "\r\n".join(parts).encode()
    status, body, _ = http_req(
        "POST",
        "/form/mas-deployment-health-check",
        post_body,
        f"multipart/form-data; boundary={boundary}",
        cookie=cookie,
    )
    print("POST form", status, "bytes", len(body))
    try:
        waiting = json.loads(body)
        waiting_path = re.sub(r"^https?://[^/]+", "", waiting["formWaitingUrl"])
        exec_id = None
        m_exec = re.search(r"/form-waiting/(\d+)", waiting_path)
        if m_exec:
            exec_id = m_exec.group(1)
    except Exception:
        print("no formWaitingUrl", body[:200])
        return None

    def overall_from_execution(eid: str) -> str | None:
        raw = psql(f'SELECT data::text FROM execution_data WHERE "executionId"={eid};')
        m = re.search(r"Overall:\s*<strong>(FAIL|PASS_WITH_TODO|PASS)</strong>", raw)
        return m.group(1) if m else None

    for i in range(60):
        if exec_id:
            overall = overall_from_execution(exec_id)
            if overall:
                fail_count = "0"
                raw = psql(f'SELECT data::text FROM execution_data WHERE "executionId"={exec_id};')
                fm = re.search(r"FAIL — fix these first \((\d+)\)", raw)
                if fm:
                    fail_count = fm.group(1)
                print(json.dumps({"overall": overall, "fail_first": fail_count, "execution_id": exec_id}))
                return overall
            st = psql(f'SELECT status FROM execution_entity WHERE id={exec_id};')
            if st in {"error", "crashed", "canceled"}:
                print("health execution", st)
                return "FAIL"
        time.sleep(2)
    print("health wait timeout")
    return None


def wait_url(url: str, attempts: int = 90) -> None:
    """Host-side health wait — avoids depending on wget inside images."""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if int(resp.status) == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit(f"not healthy: {url}")


def wait_control_plane_webhook(env: dict[str, str], attempts: int = 60) -> None:
    """Activity cannot boot until production /webhook/mas-control-plane is registered."""
    port = env.get("N8N_HOST_PORT", "15678")
    url = f"http://127.0.0.1:{port}/webhook/mas-control-plane"
    body = json.dumps({"operation": "schema"}).encode()
    header = env.get("CONTROL_PLANE_PROXY_AUTH_HEADER") or "Authorization"
    value = env.get("CONTROL_PLANE_PROXY_AUTH_VALUE") or "local-orch-inbound"
    print("== wait control-plane webhook ==")
    for _ in range(attempts):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", header: value},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                data = json.loads(raw) if raw else {}
                if data.get("ok") is True:
                    print("control-plane webhook ok")
                    return
                print(f"control-plane unexpected body {str(data)[:180]}")
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            text = exc.read().decode("utf-8", errors="replace")[:180]
            print(f"control-plane HTTP {code} {text.replace(chr(10), ' ')}")
            if code != 404:
                print("control-plane webhook registered")
                return
        except Exception as exc:  # noqa: BLE001
            print(f"control-plane wait {exc}")
        time.sleep(2)
    raise SystemExit("control-plane webhook not registered")


def wait_activity_health(env: dict[str, str]) -> None:
    port = env.get("MAS_ACTIVITY_HOST_PORT", "8200")
    host = f"http://127.0.0.1:{port}/health"
    print(f"== wait mas-activity {host} ==")
    try:
        wait_url(host, attempts=180)
        return
    except SystemExit:
        pass
    for _ in range(90):
        cp = run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "mas-activity",
                "python3",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/health', timeout=3).read()",
            ],
            check=False,
            timeout=20,
        )
        if cp.returncode == 0:
            print("mas-activity healthy via docker exec")
            return
        time.sleep(2)
    raise SystemExit(f"not healthy: {host}")


def ensure_compose_up() -> None:
    print("== compose up ==")
    # Do not start mas-activity yet: it requires an active Control Plane Proxy webhook.
    run(["docker", "compose", "stop", "mas-activity"], check=False, timeout=60)
    run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "postgres",
            "n8n",
            "n8n-runners",
            "excel-tools",
            "math-service",
            "schedule-builder",
        ],
        timeout=300,
    )
    env = load_env()
    n8n_port = env.get("N8N_HOST_PORT", "15678")
    wait_url(f"http://127.0.0.1:{n8n_port}/healthz")
    for service, inner in (
        ("math-service", "http://127.0.0.1:8100/health"),
        ("schedule-builder", "http://127.0.0.1:8090/health"),
        ("excel-tools", "http://127.0.0.1:8000/health"),
    ):
        print(f"wait {service} {inner}")
        for _ in range(90):
            cp = run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    service,
                    "python3",
                    "-c",
                    f"import urllib.request; urllib.request.urlopen({inner!r}, timeout=3).read()",
                ],
                check=False,
                timeout=20,
            )
            if cp.returncode == 0:
                break
            time.sleep(2)
        else:
            print(f"warn: {service} not healthy yet")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--skip-import", action="store_true", help="only rebind/publish existing workflows")
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="truncate MAS cases/events/artifacts and Activity state (keeps agent_registry)",
    )
    parser.add_argument(
        "--skip-wipe",
        action="store_true",
        help="deprecated: wipe is off unless --wipe (kept so old runbooks still work)",
    )
    args = parser.parse_args()
    t0 = time.time()
    env = load_env()
    ensure_compose_up()
    apply_control_plane_sql()
    qwen_id = ensure_qwen_credential(env)
    if qwen_id:
        env["_N8N_QWEN_CRED_ID"] = qwen_id
    excel_hdr = ensure_excel_tools_header_auth(env)
    if excel_hdr:
        env["_N8N_EXCEL_HDR_CRED_ID"] = excel_hdr
    if args.wipe:
        wipe_state(env)
    if not args.skip_import:
        import_workflows()
    name_to_id = wf_map()
    print(f"workflows {len(name_to_id)}")
    for name, wid in sorted(name_to_id.items()):
        nodes = fetch_nodes(wid)
        n = patch_nodes(nodes, name_to_id, env)
        if n:
            save_nodes(wid, nodes)
        print(f"patched {name}: {n}")
    bind_error_workflow(name_to_id)
    # share with project if missing
    if PROJECT_ID:
        for wid in name_to_id.values():
            psql(
                f"INSERT INTO shared_workflow (\"workflowId\", \"projectId\", role) "
                f"VALUES ('{wid}', '{PROJECT_ID}', 'workflow:owner') ON CONFLICT DO NOTHING;"
            )
    sync_history()
    publish(name_to_id)
    run(["docker", "compose", "restart", "n8n"])
    env = load_env()
    wait_url(f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '15678')}/healthz")
    # Forms/webhooks register after active workflows finish loading.
    time.sleep(8)
    wait_control_plane_webhook(env)
    run(["docker", "compose", "up", "-d", "--no-deps", "mas-activity"], timeout=180)
    # Volume-mounted Activity keeps running across up -d; restart loads new Python.
    run(["docker", "compose", "restart", "mas-activity"], timeout=120)
    wait_activity_health(env)
    overall = None
    if not args.skip_health:
        overall = run_health_form()
        if overall is None:
            print("health form did not return overall — retry once after activate settle")
            time.sleep(10)
            overall = run_health_form()
    # final activity emptiness
    tasks = -1
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{env.get('MAS_ACTIVITY_HOST_PORT', '8200')}/v1/tasks",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            tasks = len(json.loads(resp.read().decode()).get("tasks") or [])
    except Exception as exc:  # noqa: BLE001
        print("activity probe", exc)
    elapsed = round(time.time() - t0, 1)
    print(json.dumps({"overall": overall, "activity_tasks": tasks, "elapsed_s": elapsed}, ensure_ascii=False))
    if args.skip_health:
        return 0
    if overall == "FAIL" or overall is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
