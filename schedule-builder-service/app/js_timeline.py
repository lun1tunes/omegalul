"""Run shared n8n timeline JS (parse → edit → emit) via Node."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TEMPLATES = Path(os.getenv("SCHEDULE_TEMPLATES") or "/templates")
if not (TEMPLATES / "schedule_timeline_runtime.py").is_file():
    TEMPLATES = Path(__file__).resolve().parents[2] / "n8n" / "templates"

TIMELINE_PY = TEMPLATES / "schedule_timeline_runtime.py"


def _load_timeline_core() -> str:
    import importlib.util
    import sys

    templates = str(TIMELINE_PY.parent)
    if templates not in sys.path:
        sys.path.insert(0, templates)
    spec = importlib.util.spec_from_file_location("schedule_timeline_runtime", TIMELINE_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load timeline runtime: {TIMELINE_PY}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.timeline_core_js()


def run_timeline_fn(
    fn: str,
    source_text: str,
    payload: Any,
    *,
    file_ref: str = "schedule.inc",
    options: dict[str, Any] | None = None,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    if not TIMELINE_PY.is_file():
        raise RuntimeError(f"timeline templates missing at {TEMPLATES}")
    if fn not in {"runCommissioningRevise", "runGroupRebindRevise"}:
        raise ValueError(f"unsupported timeline fn: {fn}")
    core = _load_timeline_core()
    call = (
        "const result = runCommissioningRevise(baseline, payload, root, options);"
        if fn == "runCommissioningRevise"
        else "const result = runGroupRebindRevise(baseline, payload, root);"
    )
    script = f"""
{core}
const fs = require('fs');
const baseline = fs.readFileSync(process.argv[2], 'utf8');
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const meta = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const root = String(meta.file_ref || 'schedule.inc');
const options = meta.options && typeof meta.options === 'object' ? meta.options : {{}};
{call}
process.stdout.write(JSON.stringify(result));
"""
    with tempfile.TemporaryDirectory(prefix="sched-tl-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "baseline.inc"
        body = tmp_path / "payload.json"
        meta = tmp_path / "meta.json"
        js = tmp_path / "run.js"
        src.write_text(source_text, encoding="utf-8")
        body.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        meta.write_text(
            json.dumps({"file_ref": file_ref, "options": options or {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        js.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(js), str(src), str(body), str(meta)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "node failed")[:800]
        raise RuntimeError(f"timeline node failed: {err}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("timeline returned non-JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("timeline returned non-object")
    return data
