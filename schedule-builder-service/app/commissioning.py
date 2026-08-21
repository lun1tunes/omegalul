"""Commissioning date retarget via the shared timeline JS (same algorithm as combat/golden)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .timeline_ops import commissioning_revise

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
    result = commissioning_revise(source_text, well_facts, file_ref=file_ref)
    result["unlisted_wells_policy"] = unlisted_wells_policy or "keep"
    result["assumptions"] = [{"units": "METRIC", "unlisted_wells_policy": result["unlisted_wells_policy"]}]
    return result
