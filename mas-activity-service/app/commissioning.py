"""Local commissioning REVISE via schedule_timeline_runtime (Node)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PY = ROOT / "n8n" / "templates" / "schedule_timeline_runtime.py"


def _load_timeline_core() -> str:
    # Import without requiring n8n package layout on PYTHONPATH.
    import importlib.util

    spec = importlib.util.spec_from_file_location("schedule_timeline_runtime", TIMELINE_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load timeline runtime: {TIMELINE_PY}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.timeline_core_js()


def normalize_well_facts(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, dict) and isinstance(raw.get("wells"), list):
        raw = raw["wells"]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        well = str(item.get("well") or item.get("name") or "").strip()
        date = str(item.get("date") or item.get("new_date") or item.get("Дата ввода") or "").strip()
        if well and date:
            out.append({"well": well, "date": date})
    return out


def run_commissioning_revise(
    *,
    baseline_text: str,
    well_facts: list[dict[str, str]],
    instruction_blob: str = "",
    unlisted_wells_policy: str | None = None,
    new_well_defs: list[dict[str, Any]] | None = None,
    root_filename: str = "schedule.inc",
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    if not baseline_text.strip():
        raise ValueError("baseline schedule text is empty")
    if not well_facts:
        raise ValueError("well facts are required for local commissioning")
    core = _load_timeline_core()
    options: dict[str, Any] = {"instruction_blob": instruction_blob or ""}
    if unlisted_wells_policy in {"keep", "remove"}:
        options["unlisted_wells_policy"] = unlisted_wells_policy
    if new_well_defs:
        options["new_well_defs"] = new_well_defs

    script = f"""
{core}
const baseline = {json.dumps(baseline_text)};
const facts = {json.dumps(well_facts, ensure_ascii=False)};
const options = {json.dumps(options, ensure_ascii=False)};
const root = {json.dumps(root_filename)};
const result = runCommissioningRevise(baseline, facts, root, options);
process.stdout.write(JSON.stringify(result));
"""
    with tempfile.TemporaryDirectory(prefix="mas-comm-") as tmp:
        path = Path(tmp) / "run.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(ROOT),
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "node failed")[:800]
        raise RuntimeError(f"commissioning node failed: {err}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("commissioning returned non-JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("commissioning returned non-object")
    return data


def extract_schedule_from_orchestrator(orch: dict[str, Any]) -> tuple[str, str] | None:
    """Return (filename, text) if Orchestrator response carries a SCHEDULE artifact."""
    result = orch.get("result") if isinstance(orch.get("result"), dict) else {}
    release = result.get("release") if isinstance(result.get("release"), dict) else {}
    compact = result.get("compact_data") if isinstance(result.get("compact_data"), dict) else {}
    top_name = orch.get("filename") if isinstance(orch.get("filename"), str) else None
    candidates = [
        (release.get("filename"), release.get("schedule_text")),
        (orch.get("filename"), orch.get("schedule_text")),
        (compact.get("filename") or top_name, compact.get("generated_schedule")),
        (top_name, orch.get("generated_schedule")),
        (top_name, result.get("generated_schedule")),
    ]
    for filename, text in candidates:
        if isinstance(text, str) and text.strip():
            name = str(filename or "schedule.inc").strip() or "schedule.inc"
            return name, text
    return None
