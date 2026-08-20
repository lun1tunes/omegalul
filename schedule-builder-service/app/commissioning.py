"""Commissioning date retarget via the shared timeline JS (same algorithm as combat/golden)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TEMPLATES = Path(os.getenv("SCHEDULE_TEMPLATES") or "/templates")
if not (TEMPLATES / "schedule_timeline_runtime.py").is_file():
    TEMPLATES = Path(__file__).resolve().parents[2] / "n8n" / "templates"


def run_commissioning_revise(
    source_text: str,
    well_facts: list[dict[str, Any]],
    *,
    file_ref: str = "schedule.inc",
    unlisted_wells_policy: str | None = "keep",
    instruction_blob: str = "",
    new_well_defs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not (TEMPLATES / "schedule_timeline_runtime.py").is_file():
        raise RuntimeError(f"timeline templates missing at {TEMPLATES}")
    sys.path.insert(0, str(TEMPLATES))
    from schedule_timeline_runtime import timeline_core_js

    payload = {
        "text": source_text,
        "facts": well_facts,
        "file_ref": file_ref,
        "options": {
            "unlisted_wells_policy": unlisted_wells_policy,
            "instruction_blob": instruction_blob or "",
            "new_well_defs": new_well_defs or [],
        },
    }
    js = (
        timeline_core_js()
        + """
const fs = require('fs');
const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const result = runCommissioningRevise(payload.text, payload.facts, payload.file_ref || 'schedule.inc', payload.options || {});
const slim = {
  contract: result.contract,
  contract_version: result.contract_version,
  status: result.status,
  generated_schedule: result.generated_schedule || '',
  edits: result.edits || [],
  moved: result.moved || [],
  shifts: result.shifts || [],
  findings: result.findings || [],
  questions: result.questions || [],
  unlisted_wells_policy: result.unlisted_wells_policy,
  unlisted_wells: result.unlisted_wells || [],
  new_wells: result.new_wells || [],
  monthly_dates_check: result.monthly_dates_check || null,
};
process.stdout.write(JSON.stringify(slim));
"""
    )
    with tempfile.TemporaryDirectory(prefix="sched-comm-") as tmp:
        tmp_path = Path(tmp)
        script = tmp_path / "revise.js"
        data = tmp_path / "payload.json"
        script.write_text(js, encoding="utf-8")
        data.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(script), str(data)],
            capture_output=True,
            check=False,
            timeout=120,
        )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")[:800]
        raise RuntimeError(err or "commissioning revise failed")
    return json.loads(proc.stdout.decode("utf-8"))
