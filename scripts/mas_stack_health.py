#!/usr/bin/env python3
"""Host-side stack health: ping compose services the Health Check form also probes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE from .env into os.environ without overriding existing exports."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")[:300]
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc)[:300]
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:300]


def compose_exec(service: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", service, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=45,
    )


def main() -> int:
    load_dotenv(ROOT / ".env")
    # Match docker-compose.yml defaults: N8N_HOST_PORT:-5678, MAS_ACTIVITY_HOST_PORT:-8200
    n8n_port = os.getenv("N8N_HOST_PORT", "5678")
    activity_port = os.getenv("MAS_ACTIVITY_HOST_PORT", "8200")
    checks: list[dict] = []

    def push(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})
        print(("PASS" if ok else "FAIL"), name, "—", detail)

    code, body = http_get(f"http://127.0.0.1:{n8n_port}/healthz")
    push("host n8n /healthz", code == 200 and "ok" in body.lower(), f"{code} {body}")

    code, body = http_get(f"http://127.0.0.1:{activity_port}/health")
    push("host mas-activity /health", code == 200 and "ok" in body.lower(), f"{code} {body}")

    orch_url = os.getenv(
        "ORCHESTRATOR_WEBHOOK_URL",
        f"http://127.0.0.1:{n8n_port}/webhook/mas-orchestrator-step",
    )
    try:
        req = urllib.request.Request(
            orch_url,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            orch_code = int(resp.status)
            orch_body = resp.read().decode("utf-8", errors="replace")[:180]
    except urllib.error.HTTPError as exc:
        orch_code = int(exc.code)
        orch_body = exc.read().decode("utf-8", errors="replace")[:180]
    except Exception as exc:  # noqa: BLE001
        orch_code = 0
        orch_body = str(exc)[:180]
    push(
        "host orchestrator webhook registered (unauth 403)",
        orch_code == 403 and "Authorization data is wrong" in orch_body,
        f"{orch_code} {orch_body.replace(chr(10), ' ')}",
    )

    for name, url in [
        ("compose excel-tools via n8n DNS", "http://excel-tools:8000/health"),
        ("compose n8n-runners via n8n DNS", "http://n8n-runners:5680/healthz"),
        ("compose mas-activity via n8n DNS", "http://mas-activity:8200/health"),
        ("compose n8n self via n8n DNS", "http://n8n:5678/healthz"),
    ]:
        r = compose_exec("n8n", "wget", "-q", "-O", "-", url)
        ok = r.returncode == 0 and bool((r.stdout or "").strip())
        push(name, ok, ((r.stdout or r.stderr) or "")[:300])

    r = compose_exec("postgres", "pg_isready", "-U", os.getenv("POSTGRES_USER", "n8n"))
    push("compose postgres pg_isready", r.returncode == 0, ((r.stdout or r.stderr) or "")[:200])

    failed = [c for c in checks if not c["ok"]]
    report = {"overall": "FAIL" if failed else "PASS", "fail_count": len(failed), "checks": checks}
    out = ROOT / "simulation-model-example" / "combat-dates-revise" / "stack_health_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"overall": report["overall"], "fail_count": report["fail_count"]}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
