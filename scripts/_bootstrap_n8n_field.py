#!/usr/bin/env python3
"""Lab/field bootstrap: create n8n credentials, rebind URLs, publish workflows.

Does not import workflows (UI import is the field canon). Safe to re-run.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / ".env",):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def n8n_session(env: dict[str, str]) -> tuple[str, str]:
    base = f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '15678')}"
    body = json.dumps(
        {"emailOrLdapLoginId": env["N8N_USERNAME"], "password": env["N8N_PASSWORD"]}
    ).encode()
    req = urllib.request.Request(
        base + "/rest/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        cookie = ""
        raw = resp.headers.get("Set-Cookie") or ""
        for part in raw.split(","):
            part = part.strip()
            if part.startswith("n8n-auth="):
                cookie = part.split(";", 1)[0]
                break
        if not cookie:
            raise SystemExit("no n8n-auth cookie")
        return base, cookie


def api(base: str, cookie: str, method: str, path: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Cookie": cookie, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body[:800]}
        return exc.code, parsed


def create_credential(base: str, cookie: str, name: str, ctype: str, data: dict) -> str:
    status, body = api(
        base,
        cookie,
        "POST",
        "/rest/credentials",
        {"name": name, "type": ctype, "data": data},
    )
    if status >= 400:
        raise SystemExit(f"create credential {name} failed {status} {body}")
    cred = body.get("data") or body
    cid = cred.get("id")
    if not cid:
        raise SystemExit(f"credential {name} missing id: {body}")
    print(f"credential {name} id={cid} type={ctype}")
    return str(cid)


def main() -> int:
    env = load_env()
    base, cookie = n8n_session(env)

    status, existing = api(base, cookie, "GET", "/rest/credentials")
    items = (existing.get("data") if isinstance(existing, dict) else existing) or []
    by_name = {c.get("name"): c for c in items if isinstance(c, dict)}

    def ensure(name: str, ctype: str, data: dict) -> str:
        if name in by_name and by_name[name].get("id"):
            print(f"credential exists {name} id={by_name[name]['id']}")
            return str(by_name[name]["id"])
        return create_credential(base, cookie, name, ctype, data)

    pg_id = ensure(
        "Postgres pgvector",
        "postgres",
        {
            "host": "postgres",
            "database": env.get("POSTGRES_DB", "n8n"),
            "user": env.get("POSTGRES_USER", "n8n"),
            "password": env["POSTGRES_PASSWORD"],
            "port": 5432,
            "ssl": "disable",
        },
    )
    oa_id = ensure(
        "OpenAI production",
        "openAiApi",
        {"apiKey": env["OPENAI_API_KEY"]},
    )
    hdr_id = ensure(
        "Engineering orchestrator inbound key",
        "httpHeaderAuth",
        {"name": "Authorization", "value": "local-orch-inbound"},
    )
    excel_hdr_id = ensure(
        "Excel Tools X-API-Key",
        "httpHeaderAuth",
        {"name": "X-API-Key", "value": env.get("EXCEL_TOOLS_API_KEY", "local-dev-excel-tools-api-key")},
    )

    ids = {
        "cred-postgres-pgvector-01": pg_id,
        "cred-openai-production-01": oa_id,
        "cred-orch-header-auth-01": hdr_id,
        "cred-qwen-compatible-01": oa_id,
    }

    status, wfs = api(base, cookie, "GET", "/rest/workflows?limit=250")
    rows = (wfs.get("data") if isinstance(wfs, dict) else wfs) or []
    if isinstance(rows, dict):
        rows = rows.get("data") or []

    excel_url = "http://host.docker.internal:8000/api/v1"
    activity_url = "http://host.docker.internal:8200"
    math_url = "http://host.docker.internal:8100/api/v1/math"
    excel_key = env.get("EXCEL_TOOLS_API_KEY", "local-dev-excel-tools-api-key")

    for row in rows:
        wid = row.get("id")
        name = row.get("name")
        if not wid:
            continue
        status, wf = api(base, cookie, "GET", f"/rest/workflows/{wid}")
        data = wf.get("data") or wf
        nodes = data.get("nodes") or []
        changed = 0
        blob = json.dumps(nodes)
        for old, new in ids.items():
            if old in blob and old != new:
                blob = blob.replace(old, new)
                changed += 1
        nodes = json.loads(blob)
        for node in nodes:
            nname = node.get("name")
            params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
            creds = node.get("credentials") if isinstance(node.get("credentials"), dict) else {}
            for ctype, meta in creds.items():
                if not isinstance(meta, dict):
                    continue
                if ctype == "postgres":
                    meta["id"] = pg_id
                    meta["name"] = "Postgres pgvector"
                    changed += 1
                elif ctype == "openAiApi":
                    meta["id"] = oa_id
                    meta["name"] = "OpenAI production"
                    changed += 1
                elif ctype == "httpHeaderAuth":
                    cname = str(meta.get("name") or "")
                    if "excel" in cname.lower() or "x-api-key" in cname.lower():
                        meta["id"] = excel_hdr_id
                        meta["name"] = "Excel Tools X-API-Key"
                    else:
                        meta["id"] = hdr_id
                        meta["name"] = "Engineering orchestrator inbound key"
                    changed += 1
            if nname == "Runtime URLs":
                for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                    key = assignment.get("name")
                    if key == "excel_tools_url":
                        assignment["value"] = "http://host.docker.internal:8000"
                        changed += 1
                    elif key == "activity_base_url":
                        assignment["value"] = activity_url
                        changed += 1
                    elif key == "schedule_service_url":
                        assignment["value"] = "http://host.docker.internal:8090"
                        changed += 1
                    elif key == "math_url":
                        assignment["value"] = "http://host.docker.internal:8100"
                        changed += 1
                    elif key == "orchestrator_step_url":
                        assignment["value"] = "http://127.0.0.1:5678/webhook/mas-orchestrator-step"
                        changed += 1
            if nname == "Runtime configuration":
                for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                    key = assignment.get("name")
                    if key == "excel_tools_url":
                        assignment["value"] = "http://host.docker.internal:8000"
                        changed += 1
            if nname == "Math Service Configuration":
                for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                    if assignment.get("name") == "math_service_url":
                        assignment["value"] = math_url
                        changed += 1
            if nname == "Activity connection":
                for assignment in (((params.get("assignments") or {}).get("assignments")) or []):
                    if assignment.get("name") == "activity_base_url":
                        assignment["value"] = activity_url
                        changed += 1
            url = params.get("url")
            if isinstance(url, str):
                repl = {
                    "http://excel-tools:8000/health": "http://host.docker.internal:8000/health",
                    "http://mas-activity:8200/health": "http://host.docker.internal:8200/health",
                    "http://excel-tools:8000": "http://host.docker.internal:8000",
                    "http://mas-activity:8200": "http://host.docker.internal:8200",
                }
                for src, dst in repl.items():
                    if src in url:
                        params["url"] = url.replace(src, dst)
                        changed += 1
                        break
        if not changed:
            print(f"unchanged {name}")
            continue
        data["nodes"] = nodes
        # n8n 2.30 PUT wants the workflow settings payload.
        put_body = {
            "name": data.get("name"),
            "nodes": nodes,
            "connections": data.get("connections"),
            "settings": data.get("settings") or {},
            "staticData": data.get("staticData"),
        }
        st, body = api(base, cookie, "PUT", f"/rest/workflows/{wid}", put_body)
        print(f"patched {name} changed={changed} put={st}")
        if st >= 400:
            print(body)

    print("bootstrap credentials done", {"postgres": pg_id, "openai": oa_id, "header": hdr_id, "excel_header": excel_hdr_id})
    Path("/tmp/mas-n8n-bootstrap-ids.json").write_text(
        json.dumps(
            {"postgres": pg_id, "openai": oa_id, "header": hdr_id, "excel_header": excel_hdr_id, "ts": time.time()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
