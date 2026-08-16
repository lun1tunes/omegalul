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
    """Return (filename, text) if Orchestrator/Builder response carries a SCHEDULE artifact.

    Preferred sources (full bounded draft, not the short compact preview):
      - result.deliverables[] / deliverables[] with schedule_text (kind schedule_inc_text)
      - result.release.schedule_text
      - top-level / result generated_schedule
    compact_data.generated_schedule_preview is only a last-resort short fallback.
    """
    if not isinstance(orch, dict):
        return None

    result = orch.get("result") if isinstance(orch.get("result"), dict) else {}
    # Some paths pass the specialist_result itself (no wrapping result).
    specialist = result if result.get("contract") == "specialist_result" else (
        orch if orch.get("contract") == "specialist_result" else result or orch
    )
    if not isinstance(specialist, dict):
        specialist = {}

    release = specialist.get("release") if isinstance(specialist.get("release"), dict) else {}
    if not release and isinstance(result.get("release"), dict):
        release = result["release"]
    compact = specialist.get("compact_data") if isinstance(specialist.get("compact_data"), dict) else {}
    if not compact and isinstance(result.get("compact_data"), dict):
        compact = result["compact_data"]

    top_name = orch.get("filename") if isinstance(orch.get("filename"), str) else None
    if not top_name and isinstance(specialist.get("filename"), str):
        top_name = specialist["filename"]

    def _from_deliverables(blob: dict[str, Any]) -> tuple[str, str] | None:
        items = blob.get("deliverables")
        if not isinstance(items, list):
            return None
        best: tuple[str, str] | None = None
        best_score = -1
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("schedule_text")
            if not isinstance(text, str) or not text.strip():
                continue
            kind = str(item.get("kind") or "").strip().lower()
            name = item.get("filename")
            if not isinstance(name, str) or not name.strip():
                name = top_name or "schedule.inc"
            # Prefer explicit schedule deliverables, then longer bounded text.
            preferred = kind in {"", "schedule_inc_text", "schedule_text", "schedule"}
            score = len(text) + (1_000_000_000 if preferred else 0)
            if score > best_score:
                best_score = score
                best = (name.strip() or "schedule.inc", text)
        return best

    from_deliv = _from_deliverables(specialist) or _from_deliverables(result) or _from_deliverables(orch)
    if from_deliv:
        return from_deliv

    candidates: list[tuple[Any, Any]] = [
        (release.get("filename"), release.get("schedule_text")),
        (orch.get("filename"), orch.get("schedule_text")),
        (specialist.get("filename"), specialist.get("schedule_text")),
        (compact.get("filename") or top_name, compact.get("generated_schedule")),
        (top_name, orch.get("generated_schedule")),
        (top_name, result.get("generated_schedule")),
        (top_name, specialist.get("generated_schedule")),
        # Short preview only if nothing fuller is available.
        (compact.get("filename") or top_name, compact.get("generated_schedule_preview")),
    ]
    merge = compact.get("merge_result") if isinstance(compact.get("merge_result"), dict) else {}
    if merge:
        pkg = merge.get("output_package") if isinstance(merge.get("output_package"), dict) else {}
        root = pkg.get("root_path") if isinstance(pkg.get("root_path"), str) else None
        candidates.append((root or top_name, merge.get("generated_schedule")))

    for filename, text in candidates:
        if isinstance(text, str) and text.strip():
            name = str(filename or "schedule.inc").strip() or "schedule.inc"
            return name, text
    return None
