#!/usr/bin/env python3
"""Lab-only verification gate for NOVATEK RE MASter — one command, one verdict.

Runs, in order, and prints a summary table (stops at the first failing stage unless --keep-going):

  regen      regenerate every n8n workflow JSON from n8n/templates and report drift vs. git
  smokes     node n8n/tests/*-smoke.js  (workflow structure + Code-node logic)
  pytest     Agent Kit, Activity, Schedule Builder, Excel Tools, Demo Agent (template) test suites
  combat     PUBLISH_ACTIVITY=0 combat-dates-revise/run_integration_cases.py (offline engine check)
  live       (only with --live) scripts/lab_soft_redeploy.py --skip-health + run_live_five.py [6 cases]
             + run_live_demo_agent.py [template agent: in_progress → resume source=agent;
               three_agent_chain: excel → builder → demo with state.plan]  (~15 min)

Usage:
  python3 scripts/mas_gate.py                 # offline gate (≈1 min)
  python3 scripts/mas_gate.py --live          # offline gate + lab redeploy + 6 live cases + 2 demo agent cases
  python3 scripts/mas_gate.py --only smokes,pytest
  python3 scripts/mas_gate.py --live --cases combat_case3
  python3 scripts/mas_gate.py --live --cases demo_agent   # only the template agent cases (long job + three-agent chain)

Field note: this script needs Node.js, Docker Compose and the lab .venv — it is developer tooling.
The field path is docs.md §5 (commands) and the n8n Health Check form.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY_PY = ROOT / "mas-activity-service" / ".venv" / "bin" / "python"
# Order matters: generate_schedule_workflows.py ends with relayout_core_workflows.py, which normalises node
# positions of *every* core workflow — so it runs last and the result is a fixed point (idempotent regen).
GENERATORS = [
    "generate_mas_control_plane_proxy.py",
    "generate_mas_runtime_config.py",
    "generate_mas_error_traces.py",
    "generate_mas_health_check.py",
    "generate_excel_extractor_agent.py",
    "generate_schedule_builder_agent.py",
    "generate_demo_agent.py",
    "generate_mas_orchestrator.py",
    "generate_schedule_workflows.py",
]


def py() -> str:
    return str(ACTIVITY_PY) if ACTIVITY_PY.is_file() else sys.executable


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, timeout: int = 1800) -> tuple[int, str]:
    merged = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=merged, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def tail(text: str, n: int = 25) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


VOLATILE_TOP_LEVEL_KEYS = ("versionId", "updatedAt", "createdAt")  # generators mint a fresh versionId per run


def _workflow_digests() -> dict[str, str]:
    import hashlib
    import json

    out: dict[str, str] = {}
    for path in sorted((ROOT / "n8n" / "workflows").rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            out[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            continue
        if isinstance(data, dict):
            for key in VOLATILE_TOP_LEVEL_KEYS:
                data.pop(key, None)
        canon = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        out[str(path.relative_to(ROOT))] = hashlib.sha256(canon).hexdigest()
    return out


def stage_regen() -> tuple[bool, str]:
    """Regenerate every workflow; report files whose content changed (= templates edited without regen, or JSON hand-edited)."""
    before = _workflow_digests()
    notes: list[str] = []
    for gen in GENERATORS:
        code, out = run([sys.executable, gen], cwd=ROOT / "n8n" / "templates")
        if code != 0:
            return False, f"{gen} failed:\n{tail(out)}"
        notes.append(f"{gen}: ok")
    after = _workflow_digests()
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    if changed:
        notes.append("regenerated JSON differed from what was on disk (now refreshed — commit these too):\n  " + "\n  ".join(changed))
    else:
        notes.append("generated JSON in sync with templates")
    return True, "\n".join(notes)


def stage_smokes() -> tuple[bool, str]:
    failed: list[str] = []
    for smoke in sorted((ROOT / "n8n" / "tests").glob("*-smoke.js")):
        code, out = run(["node", str(smoke)], timeout=300)
        if code != 0:
            failed.append(f"{smoke.name}:\n{tail(out, 15)}")
    total = len(list((ROOT / "n8n" / "tests").glob("*-smoke.js")))
    if failed:
        return False, f"{len(failed)}/{total} smokes failed\n" + "\n".join(failed)
    return True, f"{total} smokes ok"


def stage_pytest() -> tuple[bool, str]:
    suites = [
        ("Agent Kit", [py(), "-m", "pytest", "-q", "tests"], ROOT / "mas-agent-kit", {"PYTHONPATH": "."}),
        ("Activity", [py(), "-m", "pytest", "-q", "tests"], ROOT / "mas-activity-service", {"PYTHONPATH": "."}),
        ("Schedule Builder", [py(), "-m", "pytest", "-q", "tests"], ROOT / "schedule-builder-service", {"PYTHONPATH": "."}),
        ("Excel Tools", [py(), "-m", "pytest", "-q", "excel-agent-tools/tests"], ROOT, {"PYTHONPATH": "excel-agent-tools", "API_KEY": "test-key"}),
        ("Demo Agent (template)", [py(), "-m", "pytest", "-q", "tests"], ROOT / "agents-template" / "demo_agent", {"PYTHONPATH": "."}),
    ]
    notes: list[str] = []
    ok = True
    for name, cmd, cwd, env in suites:
        code, out = run(cmd, cwd=cwd, env=env, timeout=900)
        summary = tail(out, 1)
        notes.append(f"{name}: {'ok' if code == 0 else 'FAIL'} — {summary}")
        if code != 0:
            ok = False
            notes.append(tail(out, 30))
    return ok, "\n".join(notes)


def stage_combat() -> tuple[bool, str]:
    code, out = run(
        [sys.executable, "simulation-model-example/combat-dates-revise/run_integration_cases.py"],
        env={"PUBLISH_ACTIVITY": "0"},
        timeout=600,
    )
    return code == 0, tail(out, 12)


DEMO_CASE = "demo_agent"


def stage_live(cases: list[str]) -> tuple[bool, str]:
    code, out = run([sys.executable, "scripts/lab_soft_redeploy.py", "--skip-health"], timeout=1200)
    if code != 0:
        return False, "lab_soft_redeploy failed:\n" + tail(out, 20)
    ok = True
    lines: list[str] = []
    five = [c for c in cases if c != DEMO_CASE]
    if not cases or five:
        code, out = run(
            [py(), "simulation-model-example/run_live_five.py", *five],
            env={"PYTHONPATH": "mas-activity-service"},
            timeout=3600,
        )
        ok = ok and code == 0
        lines += [ln for ln in out.splitlines() if ln.startswith('{"id"') or ln.startswith("FAIL ")] or [tail(out, 20)]
    if not cases or DEMO_CASE in cases:
        # Template agent (agents-template/demo_agent): enables its registry row, runs the text-only long-job case
        # (in_progress → waiting_agent → resume source=agent) and the three-agent chain (plan on excel → builder →
        # demo), disables the row again.
        code, out = run(
            [py(), "simulation-model-example/run_live_demo_agent.py"],
            env={"PYTHONPATH": "mas-activity-service"},
            timeout=900,
        )
        ok = ok and code == 0
        lines += [ln for ln in out.splitlines() if ln.startswith('{"id"') or ln.startswith("FAIL ")] or [tail(out, 20)]
    return ok, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also redeploy the lab and run the live cases")
    parser.add_argument("--cases", default="", help="comma-separated run_live_five case ids and/or demo_agent (default: all six + demo_agent)")
    parser.add_argument("--only", default="", help="comma-separated stages: regen,smokes,pytest,combat,live")
    parser.add_argument("--keep-going", action="store_true", help="run every stage even after a failure")
    args = parser.parse_args()

    stages: list[tuple[str, object]] = [
        ("regen", stage_regen),
        ("smokes", stage_smokes),
        ("pytest", stage_pytest),
        ("combat", stage_combat),
    ]
    if args.live:
        cases = [c for c in args.cases.split(",") if c.strip()]
        stages.append(("live", lambda: stage_live(cases)))
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        stages = [(name, fn) for name, fn in stages if name in only]

    results: list[tuple[str, bool, float, str]] = []
    for name, fn in stages:
        started = time.time()
        print(f"== {name} ...", flush=True)
        ok, note = fn()  # type: ignore[operator]
        results.append((name, ok, time.time() - started, note))
        print(note, flush=True)
        print(f"== {name}: {'OK' if ok else 'FAIL'} ({time.time() - started:.0f}s)\n", flush=True)
        if not ok and not args.keep_going:
            break

    print("======== MAS GATE ========")
    for name, ok, secs, _ in results:
        print(f"{name:8s} {'OK  ' if ok else 'FAIL'} {secs:6.0f}s")
    verdict = all(ok for _, ok, _, _ in results) and len(results) == len(stages)
    print("verdict:", "GREEN" if verdict else "RED")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
