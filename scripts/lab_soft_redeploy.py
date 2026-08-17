#!/usr/bin/env python3
"""Fast lab soft-redeploy: keep credentials/DTs, refresh workflows, Health Check.

Does NOT wipe docker volumes. Wipes CAS/trace rows + Activity state, reimports
full_clean_import_set, binds live IDs, syncs workflow_history, publishes,
restarts n8n, runs Form Health Check.

Usage:
  python3 scripts/lab_soft_redeploy.py
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
PROJECT_ID = "7jgDexDb7xOcVUnr"
DT_TASK = "eotasksv1"
DT_TRACE = "mastracev1"
CRED_PG = "cred-postgres-pgvector-01"
CRED_OA = "cred-openai-production-01"
CRED_HDR = "cred-orch-header-auth-01"

PLACEHOLDERS = {
    "REPLACE_CAS_PERSIST_IN_UI": "CAS — Persist Task State",
    "REPLACE_EXCEL_ADAPTER_IN_UI": "Adapter — Excel Extraction",
    "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI": "MAS — Knowledge Retrieval",
    "REPLACE_SCHEDULE_BUILDER_IN_UI": "SCHEDULE — Builder",
    "REPLACE_CALCULATION_ADAPTER_IN_UI": "Adapter — Calculation (Math Service)",
    "REPLACE_MAS_TRACE_WRITER_IN_UI": "Writer — MAS Trace",
    "REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI": "Agent — Excel Extractor",
    "REPLACE_HEALTH_ORCHESTRATOR_IN_UI": "Orchestrator — Engineering MAS",
    "REPLACE_HEALTH_TRACE_IN_UI": "Writer — MAS Trace",
    "REPLACE_ORCHESTRATOR_ID_IN_UI": "Orchestrator — Engineering MAS",
    "REPLACE_ORCHESTRATOR_ID_IN_UI_HUMAN_GATE_STATUS": "Orchestrator — Engineering MAS",
    "REPLACE_ORCHESTRATOR_ID_IN_UI_HUMAN_GATE_RESUME": "Orchestrator — Engineering MAS",
    "REPLACE_ERROR_HANDLER_IN_UI": "Error — MAS Case Handler",
}

# Optional stubs — leave REPLACE_* unless the template was imported and you want a dry bind.
OPTIONAL_PLACEHOLDERS = {
    "REPLACE_CLUSTER_CALC_ADAPTER_IN_UI": "Template — Cluster Calculation Adapter",
    "REPLACE_BINARY_RESULTS_ADAPTER_IN_UI": "Template — Binary Results Adapter",
    "REPLACE_PRESENTATION_ADAPTER_IN_UI": "Template — Presentation Assembler",
}

PUBLISH = [
    "CAS — Persist Task State",
    "Writer — MAS Trace",
    "Error — MAS Case Handler",
    "Orchestrator — Engineering MAS",
    "Adapter — Calculation (Math Service)",
    "Agent — Excel Extractor",
    "Adapter — Excel Extraction",
    "MAS — Knowledge Retrieval",
    "SCHEDULE — Builder",
    "Activity — List Tasks (Data Table)",
    "Activity — Load Feed (Data Tables)",
    "Form — MAS Deployment Health Check",
    "Form — MAS Entry",
    "Form — MAS Human Gate",
]


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
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def psql(sql: str) -> str:
    return run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "n8n", "-d", "n8n", "-t", "-A", "-c", sql]
    ).stdout.strip()


def wipe_state(env: dict[str, str]) -> None:
    print("== wipe CAS / trace / activity ==")
    psql(
        """
DO $$
BEGIN
  IF to_regclass('public.execution_data') IS NOT NULL THEN TRUNCATE execution_data CASCADE; END IF;
  IF to_regclass('public.execution_entity') IS NOT NULL THEN TRUNCATE execution_entity CASCADE; END IF;
  IF to_regclass('public.data_table_user_eotasksv1') IS NOT NULL THEN TRUNCATE data_table_user_eotasksv1; END IF;
  IF to_regclass('public.data_table_user_mastracev1') IS NOT NULL THEN TRUNCATE data_table_user_mastracev1; END IF;
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
            "from pathlib import Path; p=Path('/data/activity_state.json'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{\"tasks\":{}}')",
        ],
        check=False,
    )
    key = env.get("MAS_ACTIVITY_KEY", "")
    if key:
        req = urllib.request.Request(
            f"http://127.0.0.1:{env.get('MAS_ACTIVITY_HOST_PORT', '8200')}/v1/tasks",
            headers={"X-MAS-Activity-Key": key},
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


def patch_nodes(nodes, name_to_id: dict[str, str], env: dict[str, str]) -> int:
    changed = 0
    activity_url = "http://mas-activity:8200"
    activity_key = env.get("MAS_ACTIVITY_KEY", "dev-local")
    excel_url = "http://excel-tools:8000/api/v1"
    excel_key = env.get("EXCEL_TOOLS_API_KEY") or env.get("excel_tools_api_key") or ""
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
                    if "qwen" in cname.lower():
                        meta["id"] = "cred-qwen-compatible-01"
                        meta["name"] = "Qwen OpenAI-compatible"
                    else:
                        meta["id"] = CRED_OA
                        meta["name"] = "OpenAI production"
                    changed += 1
                elif ctype == "httpHeaderAuth":
                    meta["id"] = CRED_HDR
                    meta["name"] = "Engineering orchestrator inbound key"
                    changed += 1
        params = node.get("parameters")
        if not isinstance(params, dict):
            continue
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
                pass
            else:
                target = placeholders.get(val) or (cached if cached in name_to_id else None)
                if cached == "SCHEDULE — Knowledge Retrieval":
                    target = "MAS — Knowledge Retrieval"
                if target and target in name_to_id:
                    wid["value"] = name_to_id[target]
                    wid["mode"] = "list"
                    wid["cachedResultName"] = target
                    changed += 1
        if node.get("name") == "Runtime configuration":
            for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                key = assignment.get("name")
                if key == "excel_tools_url":
                    assignment["value"] = "={{ " + json.dumps(excel_url) + " }}"
                    changed += 1
                elif key == "excel_tools_api_key":
                    assignment["value"] = "={{ " + json.dumps(excel_key) + " }}"
                    changed += 1
        js = params.get("jsCode")
        if isinstance(js, str) and "ACTIVITY_BASE_URL" in js:
            js2 = re.sub(
                r"const ACTIVITY_BASE_URL=['\"][^'\"]*['\"]",
                f"const ACTIVITY_BASE_URL={json.dumps(activity_url)}",
                js,
            )
            js2 = re.sub(
                r"const ACTIVITY_KEY=['\"][^'\"]*['\"]",
                f"const ACTIVITY_KEY={json.dumps(activity_key)}",
                js2,
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
  AND h.nodes::text IS DISTINCT FROM w.nodes::text;
"""
    )


def publish(name_to_id: dict[str, str]) -> None:
    print("== publish ==")
    for name in PUBLISH:
        wid = name_to_id.get(name)
        if not wid:
            print(f"publish skip missing {name}")
            continue
        cp = run(["docker", "compose", "exec", "-T", "n8n", "n8n", "publish:workflow", f"--id={wid}"], check=False)
        print(f"publish {name}: {'OK' if cp.returncode == 0 else 'FAIL'}")


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


def ensure_compose_up() -> None:
    print("== compose up ==")
    # Prefer recreate-without-rebuild when images exist (fast path).
    run(["docker", "compose", "up", "-d"], timeout=300)
    env = load_env()
    n8n_port = env.get("N8N_HOST_PORT", "15678")
    activity_port = env.get("MAS_ACTIVITY_HOST_PORT", "8200")
    wait_url(f"http://127.0.0.1:{n8n_port}/healthz")
    wait_url(f"http://127.0.0.1:{activity_port}/health")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--skip-import", action="store_true", help="only rebind/publish existing workflows")
    parser.add_argument("--skip-wipe", action="store_true", help="do not wipe CAS/trace/Activity state")
    args = parser.parse_args()
    t0 = time.time()
    env = load_env()
    ensure_compose_up()
    if not args.skip_wipe:
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
    wait_url(f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '5678')}/healthz")
    # Forms/webhooks register after active workflows finish loading.
    time.sleep(8)
    overall = None
    if not args.skip_health:
        overall = run_health_form()
        if overall is None:
            print("health form did not return overall — retry once after activate settle")
            time.sleep(10)
            overall = run_health_form()
    # final activity emptiness
    key = env.get("MAS_ACTIVITY_KEY", "")
    tasks = -1
    if key:
        req = urllib.request.Request(
            f"http://127.0.0.1:{env.get('MAS_ACTIVITY_HOST_PORT', '8200')}/v1/tasks",
            headers={"X-MAS-Activity-Key": key},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            tasks = len(json.loads(resp.read().decode()).get("tasks") or [])
    elapsed = round(time.time() - t0, 1)
    print(json.dumps({"overall": overall, "activity_tasks": tasks, "elapsed_s": elapsed}, ensure_ascii=False))
    if overall == "FAIL" or overall is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
