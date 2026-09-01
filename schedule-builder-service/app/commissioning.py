"""Commissioning date retarget via the shared timeline JS (same algorithm as combat/golden)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .js_timeline import run_timeline_fn
from .timeline_ops import commissioning_revise as python_commissioning_revise

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
    options: dict[str, Any] = {"instruction_blob": instruction_blob or ""}
    if unlisted_wells_policy in {"keep", "remove"}:
        options["unlisted_wells_policy"] = unlisted_wells_policy
    if new_well_defs:
        options["new_well_defs"] = new_well_defs
    if shutil.which("node") is None:
        result = python_commissioning_revise(
            source_text,
            well_facts,
            file_ref=file_ref,
        )
    else:
        try:
            result = run_timeline_fn(
                "runCommissioningRevise",
                source_text,
                well_facts,
                file_ref=file_ref,
                options=options,
            )
        except RuntimeError as exc:
            message = str(exc).lower()
            if "node failed" not in message and "winerror 2" not in message and "no such file" not in message:
                raise
            result = python_commissioning_revise(
                source_text,
                well_facts,
                file_ref=file_ref,
            )
    result["unlisted_wells_policy"] = unlisted_wells_policy or "keep"
    result.setdefault(
        "control_semantics",
        {
            "commissioning_anchor": "first WCONPROD per well",
            "forecast_controls_preserved": True,
            "factual_wconprod_preserved": True,
        },
    )
    result["assumptions"] = [{"units": "METRIC", "unlisted_wells_policy": result["unlisted_wells_policy"]}]
    return result
