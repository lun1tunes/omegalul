#!/usr/bin/env python3
"""Lab-only verification gate for NOVATEK RE MASter — one command, one verdict.

Runs, in order, and prints a summary table (stops at the first failing stage unless --keep-going):

  regen      regenerate every n8n workflow JSON from n8n/templates and report drift vs. git
  smokes     node n8n/tests/*-smoke.js  (workflow structure + Code-node logic)
  pytest     Agent Kit, Activity, Schedule Builder, Excel Tools, Demo Agent (template) test suites
  combat     PUBLISH_ACTIVITY=0 combat-dates-revise/run_integration_cases.py (offline engine check)
  live       (only with --live) lab_soft_redeploy (--hot if lab healthy / no JSON drift) + 12 cases.
             Six commissioning in one pool (LIVE_FIVE_WORKERS=6, 9 when --repeat>1);
             then demo long-job ∥ three_agent_chain ∥ excel_datasets;
             then recovery (agent_down, then rework ∥ step_limit). Wall ~20 min including --repeat 3.
             --repeat N redeploys once, runs each of the six commissioning specs N times in that pool
             (same α2 INC / thresholds), demo/chain/datasets/recovery once, prints a repeatability summary.
  bundle     (only with --bundle) zip for field import: core workflows in runtime_import_order,
             VERSION, docs.md (field runbook), field_check, Windows bats, stamped .env.example.

Usage:
  python3 scripts/mas_gate.py                 # offline gate (≈1 min)
  python3 scripts/mas_gate.py --live          # offline gate + lab redeploy + 12 live cases (≤20 min)
  python3 scripts/mas_gate.py --live --repeat 3   # α2: 6×N commissioning + one 12-set, ≤20 min
  python3 scripts/mas_gate.py --bundle             # write dist/mas-<VERSION>.zip (field import pack)
  python3 scripts/mas_gate.py --only smokes,pytest
  python3 scripts/mas_gate.py --only live          # skip offline after a GREEN mas_gate.py; implies --live
  python3 scripts/mas_gate.py --live --cases combat_case3
  python3 scripts/mas_gate.py --live --cases demo_agent   # only the template agent cases (long job + three-agent chain)
  python3 scripts/mas_gate.py --live --cases agent_down_recovery
  python3 scripts/mas_gate.py --live --cases excel_datasets  # extract_table live: events workbook, no .INC

Field note: this script needs Node.js, Docker Compose and the lab .venv — it is developer tooling.
The field path is docs.md and the n8n Health Check form.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import defaultdict
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
    global _REGEN_DRIFT
    after = _workflow_digests()
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    _REGEN_DRIFT = bool(changed)
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


_REGEN_DRIFT = False
DEMO_CASE = "demo_agent"
DATASETS_CASE = "excel_datasets"
DEMO_NAMES = ("demo_agent_long_job", "three_agent_chain")
RECOVERY_NAMES = ("agent_down_recovery", "rework_round", "step_limit_review")


def _live_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith('{"id"') or ln.startswith("FAIL ")]


def _parse_live_reports(out: str) -> list[dict]:
    reports: list[dict] = []
    seen: set[tuple] = set()
    for ln in out.splitlines():
        if not ln.startswith('{"id"'):
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        spec_id = str(row.get("id") or "")
        if not spec_id:
            continue
        key = (spec_id, str(row.get("case_id") or ""), row.get("pass"))
        if key in seen:
            continue
        seen.add(key)
        reports.append(row)
    return reports


def _run_live_script(script: str, args: list[str], *, timeout: int, extra_env: dict[str, str] | None = None) -> tuple[bool, str, list[dict]]:
    print(f"-- {script} {' '.join(args)}".rstrip(), flush=True)
    env = {"PYTHONPATH": "mas-activity-service"}
    if extra_env:
        env.update(extra_env)
    code, out = run(
        [py(), script, *args],
        env=env,
        timeout=timeout,
        stream=True,
    )
    note = "\n".join(_live_lines(out) or [tail(out, 20)])
    return code == 0, note, _parse_live_reports(out)


def _plan_fingerprint(plan) -> str:
    if not isinstance(plan, list) or not plan:
        return "—"
    parts: list[str] = []
    for item in plan:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            parts.append(f"{item[0]}:{item[1]}:{item[2]}")
        elif isinstance(item, dict):
            parts.append(f"{item.get('id')}:{item.get('agent_id')}:{item.get('status')}")
    return ",".join(parts) or "—"


def _repeat_summary(rows: list[dict], n_pass: int) -> tuple[bool, str]:
    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cid = str(row.get("id") or "")
        if cid:
            by_id[cid].append(row)
    lines = [
        f"======== LIVE REPEAT {n_pass} ========",
        f"{'id':22s} {'ok':5s} {'steps':14s} {'plan shapes':32s} {'ms':22s} inc",
    ]
    all_ok = True
    inc_bad: list[str] = []
    for cid, group in by_id.items():
        expect = n_pass if any(r.get("pass") is not None for r in group) else 1
        ok_n = sum(1 for r in group if r.get("ok") is True)
        if ok_n != expect or len(group) != expect:
            all_ok = False
        steps = [r.get("steps") for r in group if r.get("steps") is not None]
        step_s = ",".join(str(s) for s in steps) if steps else "—"
        shapes: dict[str, int] = {}
        for r in group:
            fp = _plan_fingerprint(r.get("plan"))
            shapes[fp] = shapes.get(fp, 0) + 1
        plan_s = "; ".join(f"{k}×{v}" for k, v in shapes.items()) if shapes else "—"
        durs = [r.get("duration_ms") for r in group if r.get("duration_ms") is not None]
        dur_s = ",".join(str(d) for d in durs) if durs else "—"
        mismatches = [r.get("mismatch_count") for r in group]
        bad = any(isinstance(m, int) and m > 0 for m in mismatches)
        if bad:
            all_ok = False
            inc_bad.append(cid)
        inc_s = "mismatch" if bad else "0"
        lines.append(f"{cid:22s} {ok_n}/{expect:<3} {step_s:14s} {plan_s[:32]:32s} {dur_s:22s} {inc_s}")
    lines.append("INC mismatch across repeats: " + (", ".join(inc_bad) if inc_bad else "none"))
    return all_ok, "\n".join(lines)


def _live_pass(cases: list[str], *, five_repeat: int = 1) -> tuple[bool, str, list[dict]]:
    extra = {DEMO_CASE, DATASETS_CASE, *DEMO_NAMES, *RECOVERY_NAMES}
    five = [c for c in cases if c not in extra]
    notes: list[str] = []
    reports: list[dict] = []
    ok = True
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
        demo_sel = [n for n in DEMO_NAMES if n in demo_sel]
        rec_sel = [n for n in RECOVERY_NAMES if n in rec_sel]
    want_datasets = not cases or DATASETS_CASE in cases
    first: list[tuple[str, list[str], int, dict[str, str] | None]] = []
    if not cases or five:
        five_env = {"LIVE_REPEAT": str(max(1, five_repeat))}
        if five_repeat > 1 and not os.environ.get("LIVE_FIVE_WORKERS"):
            # 18 jobs / 9 workers = 2 waves; 6 workers would be 3 waves and blow the 20 min wall.
            five_env["LIVE_FIVE_WORKERS"] = "9"
        first.append(("simulation-model-example/run_live_five.py", five, 3600, five_env))
    if len(first) == 1:
        script, args, timeout, extra_env = first[0]
        part_ok, part_note, part_rows = _run_live_script(script, args, timeout=timeout, extra_env=extra_env)
        ok = ok and part_ok
        notes.append(part_note)
        reports.extend(part_rows)
    elif first:
        with ThreadPoolExecutor(max_workers=len(first)) as pool:
            futs = [
                pool.submit(_run_live_script, script, args, timeout=timeout, extra_env=extra_env)
                for script, args, timeout, extra_env in first
            ]
            for fut in futs:
                part_ok, part_note, part_rows = fut.result()
                ok = ok and part_ok
                notes.append(part_note)
                reports.extend(part_rows)
    second: list[tuple[str, list[str], int, dict[str, str] | None]] = []
    if demo_sel:
        second.append(("simulation-model-example/run_live_demo_agent.py", demo_sel, 3600, None))
    if want_datasets:
        second.append(("simulation-model-example/run_live_excel_datasets.py", [], 1200, None))
    if len(second) == 1:
        script, args, timeout, extra_env = second[0]
        part_ok, part_note, part_rows = _run_live_script(script, args, timeout=timeout, extra_env=extra_env)
        ok = ok and part_ok
        notes.append(part_note)
        reports.extend(part_rows)
    elif second:
        with ThreadPoolExecutor(max_workers=len(second)) as pool:
            futs = [
                pool.submit(_run_live_script, script, args, timeout=timeout, extra_env=extra_env)
                for script, args, timeout, extra_env in second
            ]
            for fut in futs:
                part_ok, part_note, part_rows = fut.result()
                ok = ok and part_ok
                notes.append(part_note)
                reports.extend(part_rows)
    if rec_sel:
        rec_ok, rec_note, rec_rows = _run_live_script("simulation-model-example/run_live_demo_agent.py", rec_sel, timeout=3600)
        ok = ok and rec_ok
        notes.append(rec_note)
        reports.extend(rec_rows)
    return ok, "\n".join(n for n in notes if n), reports


def stage_live(cases: list[str], *, repeat: int = 1) -> tuple[bool, str]:
    print("-- lab_soft_redeploy.py --skip-health" + (" --hot" if not _REGEN_DRIFT else ""), flush=True)
    cmd = [sys.executable, "scripts/lab_soft_redeploy.py", "--skip-health"]
    if not _REGEN_DRIFT:
        cmd.append("--hot")
    code, out = run(cmd, timeout=1200, stream=True)
    if code != 0:
        return False, "lab_soft_redeploy failed:\n" + tail(out, 20)
    n = max(1, int(repeat or 1))
    if n > 1:
        print(f"-- live commissioning ×{n} in one pool; demo/chain/datasets/recovery once", flush=True)
    part_ok, part_note, reports = _live_pass(cases, five_repeat=n)
    notes = [part_note]
    ok = part_ok
    if n > 1:
        summary_ok, summary = _repeat_summary(reports, n)
        ok = ok and summary_ok
        notes.append(summary)
    return ok, "\n".join(n for n in notes if n)


BUNDLE_EXTRAS = (
    "CHANGELOG.md",
    "docs.md",
    "check-all-windows.bat",
    "scripts/field_check.py",
    "scripts/mas_version.py",
    "excel-agent-tools/setup-windows.bat",
    "excel-agent-tools/start-windows.bat",
    "excel-agent-tools/check-windows.bat",
    "excel-agent-tools/excel-tools.env.example",
    "schedule-builder-service/setup-windows.bat",
    "schedule-builder-service/start-windows.bat",
    "schedule-builder-service/check-windows.bat",
    "schedule-builder-service/schedule-builder.env.example",
    "fastapi-math-service/setup-windows.bat",
    "fastapi-math-service/start-windows.bat",
    "fastapi-math-service/check-windows.bat",
    "fastapi-math-service/math-service.env.example",
    "mas-activity-service/setup-windows.bat",
    "mas-activity-service/start-windows.bat",
    "mas-activity-service/check-windows.bat",
    "mas-activity-service/mas-activity.env.example",
    "n8n/import-manifest.json",
)


def repo_version() -> str:
    path = ROOT / "VERSION"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    return ""


def runtime_import_order() -> list[str]:
    manifest = json.loads((ROOT / "n8n" / "import-manifest.json").read_text(encoding="utf-8"))
    return [str(x) for x in manifest["runtime_import_order"]]


def build_bundle(dest_dir: Path | None = None) -> Path:
    version = repo_version()
    if not version:
        raise RuntimeError("VERSION file missing or empty")
    dest_dir = dest_dir or (ROOT / "dist")
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / f"mas-{version}.zip"
    prefix = f"mas-{version}"
    order = runtime_import_order()
    env_src = (ROOT / ".env.example").read_text(encoding="utf-8")
    if f"MAS_VERSION={version}" not in env_src:
        env_src = f"# MAS_VERSION={version}  (must match VERSION and Runtime Config mas_version)\n" + env_src
    pack_txt = (
        f"NOVATEK RE MASter {version}\n"
        "Полевой пакет импорта n8n + проверка Windows-сервисов.\n"
        "Код FastAPI — из распаковки проекта (project_pack / checkout), не из этого zip.\n\n"
        "1. Импорт n8n: workflows/ в порядке IMPORT_ORDER.txt (ничего не активировать).\n"
        "2. Чек-лист: docs.md, раздел «Развёртывание».\n"
        "3. Windows: setup-windows.bat → start-windows.bat в четырёх каталогах сервисов.\n"
        "4. check-all-windows.bat (или python scripts/field_check.py) — все строки OK, версия = VERSION.\n"
        "5. Health Check форма n8n → PASS; mas_version в Runtime Config = VERSION.\n"
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefix}/VERSION", version + "\n")
        zf.writestr(
            f"{prefix}/IMPORT_ORDER.txt",
            "\n".join(f"{i:02d}  {Path(p).name}" for i, p in enumerate(order, 1)) + "\n",
        )
        zf.writestr(f"{prefix}/PACK.txt", pack_txt)
        zf.writestr(f"{prefix}/.env.example", env_src)
        for i, rel in enumerate(order, 1):
            src = ROOT / "n8n" / rel
            if not src.is_file():
                raise FileNotFoundError(rel)
            zf.write(src, f"{prefix}/workflows/{i:02d}-{Path(rel).name}")
        for rel in BUNDLE_EXTRAS:
            src = ROOT / rel
            if not src.is_file():
                raise FileNotFoundError(rel)
            zf.write(src, f"{prefix}/{rel}")
    return zip_path


def stage_bundle() -> tuple[bool, str]:
    try:
        path = build_bundle()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, str(path.relative_to(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also redeploy the lab and run the live cases")
    parser.add_argument("--repeat", type=int, default=1, metavar="N", help="run each of the six commissioning live cases N times in one pool after one redeploy (α2; default 1)")
    parser.add_argument("--cases", default="", help="comma-separated run_live_five case ids and/or demo_agent, excel_datasets, agent_down_recovery, rework_round, step_limit_review (default: all six + demo + recovery + excel_datasets)")
    parser.add_argument("--only", default="", help="comma-separated stages: regen,smokes,pytest,combat,live,bundle")
    parser.add_argument("--bundle", action="store_true", help="write dist/mas-<VERSION>.zip (not part of the default offline gate)")
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
        repeat = max(1, int(args.repeat or 1))
        stages.append(("live", lambda: stage_live(cases, repeat=repeat)))
    if args.bundle or "bundle" in only:
        stages.append(("bundle", stage_bundle))
    if only:
        stages = [(name, fn) for name, fn in stages if name in only]
    elif args.bundle and not args.live:
        stages = [("bundle", stage_bundle)]

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
