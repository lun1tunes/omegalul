"""Commissioning date retarget — Python timeline only (no Node)."""

from __future__ import annotations

from typing import Any

from .timeline_ops import commissioning_revise as python_commissioning_revise


def run_commissioning_revise(
    source_text: str,
    well_facts: list[dict[str, Any]],
    *,
    file_ref: str = "schedule.inc",
    unlisted_wells_policy: str | None = None,
    instruction_blob: str = "",
    new_well_defs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = python_commissioning_revise(
        source_text,
        well_facts,
        file_ref=file_ref,
        unlisted_wells_policy=unlisted_wells_policy,
        instruction_blob=instruction_blob,
        new_well_defs=new_well_defs or [],
    )
    result.setdefault(
        "control_semantics",
        {
            "commissioning_anchor": "first WCONPROD per well",
            "forecast_controls_preserved": True,
            "factual_wconprod_preserved": True,
        },
    )
    policy = result.get("unlisted_wells_policy")
    result["assumptions"] = [{"units": "METRIC", "unlisted_wells_policy": policy or "keep"}]
    return result
