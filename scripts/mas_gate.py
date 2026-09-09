#!/usr/bin/env python3
"""Lab-only verification gate for NOVATEK RE MASter — one command, one verdict.

Runs, in order, and prints a summary table (stops at the first failing stage unless --keep-going):

  regen      regenerate every n8n workflow JSON from n8n/templates and report drift vs. git
  smokes     node n8n/tests/*-smoke.js  (workflow structure + Code-node logic)
  pytest     Agent Kit, Activity, Schedule Builder, Excel Tools, Demo Agent (template) test suites
  combat     PUBLISH_ACTIVITY=0 combat-dates-revise/run_integration_cases.py (offline engine check)
  live       (only with --live) scripts/lab_soft_redeploy.py --skip-health + 12 cases.
             Six run_live_five in parallel (LIVE_FIVE_WORKERS=3); demo long-job + excel_datasets overlap;
             then three_agent_chain; then recovery serial (mutates excel-tools / max_steps).
             Streamed to the terminal (~20 min, not a silent 40).

Usage:
  python3 scripts/mas_gate.py                 # offline gate (≈1 min)
  python3 scripts/mas_gate.py --live          # offline gate + lab redeploy + 12 live cases
  python3 scripts/mas_gate.py --only smokes,pytest
  python3 scripts/mas_gate.py --only live          # skip offline after a GREEN mas_gate.py; implies --live
  python3 scripts/mas_gate.py --live --cases combat_case3
  python3 scripts/mas_gate.py --live --cases demo_agent   # only the template agent cases (long job + three-agent chain)
  python3 scripts/mas_gate.py --live --cases agent_down_recovery
  python3 scripts/mas_gate.py --live --cases excel_datasets  # extract_table live: events workbook, no .INC

Field note: this script needs Node.js, Docker Compose and the lab .venv — it is developer tooling.
The field path is docs.md §5 (commands) and the n8n Health Check form.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, timeout: int = 1800, stream: bool = False) -> tuple[int, str]:
    merged = {**os.environ, **(env or {})}
    if not stream:
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), env=merged, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=merged, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)
    chunks: list[str] = []
    started = time.time()
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if line:
            sys.stdout.write(line)
            sys.stdout.flush()
            chunks.append(line)
        elif proc.poll() is not None:
            rest = proc.stdout.read()
            if rest:
                sys.stdout.write(rest)
                sys.stdout.flush()
                chunks.append(rest)
            break
        if time.time() - started > timeout:
            proc.kill()
            return 124, "".join(chunks) + f"\ntimeout after {timeout}s: {' '.join(cmd)}"
    return int(proc.returncode or 0), "".join(chunks)


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
DATASETS_CASE = "excel_datasets"
DEMO_NAMES = ("demo_agent_long_job", "three_agent_chain")
RECOVERY_NAMES = ("agent_down_recovery", "rework_round", "step_limit_review")


def _live_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith('{"id"') or ln.startswith("FAIL ")]


def _run_live_script(script: str, args: list[str], *, timeout: int) -> tuple[bool, str]:
    print(f"-- {script} {' '.join(args)}".rstrip(), flush=True)
    code, out = run(
        [py(), script, *args],
        env={"PYTHONPATH": "mas-activity-service"},
        timeout=timeout,
        stream=True,
    )
    return code == 0, "\n".join(_live_lines(out) or [tail(out, 20)])


def stage_live(cases: list[str]) -> tuple[bool, str]:
    print("-- lab_soft_redeploy.py --skip-health", flush=True)
    code, out = run([sys.executable, "scripts/lab_soft_redeploy.py", "--skip-health"], timeout=1200, stream=True)
    if code != 0:
        return False, "lab_soft_redeploy failed:\n" + tail(out, 20)
    extra = {DEMO_CASE, DATASETS_CASE, *DEMO_NAMES, *RECOVERY_NAMES}
    five = [c for c in cases if c not in extra]
    notes: list[str] = []
    ok = True
    if not cases or five:
        five_ok, five_note = _run_live_script("simulation-model-example/run_live_five.py", five, timeout=3600)
        ok = ok and five_ok
        notes.append(five_note)
    demo_sel: list[str] = []
    rec_sel: list[str] = []
    if not cases:
        demo_sel = list(DEMO_NAMES)
        rec_sel = list(RECOVERY_NAMES)
    else:
        if DEMO_CASE in cases:
            demo_sel.extend(DEMO_NAMES)
        demo_sel.extend(c for c in cases if c in DEMO_NAMES)
        rec_sel.extend(c for c in cases if c in RECOVERY_NAMES)
        # unique, stable order
        demo_sel = [n for n in DEMO_NAMES if n in demo_sel]
        rec_sel = [n for n in RECOVERY_NAMES if n in rec_sel]
    want_datasets = not cases or DATASETS_CASE in cases
    # Datasets do not mutate Runtime Config / excel-tools: overlap with the demo-agent cases.
    # Recovery stops excel-tools and patches max_steps — always last, alone.
    overlap: list[tuple[str, list[str], int]] = []
    if demo_sel:
        overlap.append(("simulation-model-example/run_live_demo_agent.py", demo_sel, 3600))
    if want_datasets:
        overlap.append(("simulation-model-example/run_live_excel_datasets.py", [], 1200))
    if len(overlap) == 1:
        script, args, timeout = overlap[0]
        part_ok, part_note = _run_live_script(script, args, timeout=timeout)
        ok = ok and part_ok
        notes.append(part_note)
    elif overlap:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(_run_live_script, script, args, timeout=timeout) for script, args, timeout in overlap]
            for fut in futs:
                part_ok, part_note = fut.result()
                ok = ok and part_ok
                notes.append(part_note)
    if rec_sel:
        rec_ok, rec_note = _run_live_script("simulation-model-example/run_live_demo_agent.py", rec_sel, timeout=3600)
        ok = ok and rec_ok
        notes.append(rec_note)
    return ok, "\n".join(n for n in notes if n)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also redeploy the lab and run the live cases")
    parser.add_argument("--cases", default="", help="comma-separated run_live_five case ids and/or demo_agent, excel_datasets, agent_down_recovery, rework_round, step_limit_review (default: all six + demo + recovery + excel_datasets)")
    parser.add_argument("--only", default="", help="comma-separated stages: regen,smokes,pytest,combat,live")
    parser.add_argument("--keep-going", action="store_true", help="run every stage even after a failure")
    args = parser.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    stages: list[tuple[str, object]] = [
        ("regen", stage_regen),
        ("smokes", stage_smokes),
        ("pytest", stage_pytest),
        ("combat", stage_combat),
    ]
    if args.live or "live" in only:
        cases = [c.strip() for c in args.cases.split(",") if c.strip()]
        stages.append(("live", lambda: stage_live(cases)))
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
