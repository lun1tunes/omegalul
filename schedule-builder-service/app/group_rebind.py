"""Group membership rebind — the spec is a structure the agent LLM fills from the task.

No prose parsing lives here any more (no intent keywords, no rate regex): the LLM reads the
engineer's task and ``inspect_schedule`` and passes ``wells / parent_group / parent_of_parent /
control / gas_rate`` explicitly.  This module only coerces that structure, applies two explicit
rendering conventions (reported back as assumptions) and lists what is still missing.
"""

from __future__ import annotations

from typing import Any

from .parse import parse_schedule
from .timeline_ops import group_rebind_revise as python_group_rebind_revise

CONTROLS = ("ORAT", "WRAT", "GRAT", "LRAT", "RESV")
SPEC_FIELDS = ("wells", "parent_group", "parent_of_parent", "well_groups", "control", "gas_rate", "effective_at")


def gruptree_summary(source_text: str) -> dict[str, Any]:
    """Groups already present in baseline GRUPTREE: all names, and roots (parents that are nobody's child)."""
    children: list[str] = []
    parents: list[str] = []
    for block in parse_schedule(source_text or "").blocks:
        if block.keyword != "GRUPTREE":
            continue
        for rec in block.records:
            if len(rec.tokens) < 2:
                continue
            child = rec.tokens[0].strip("'\"")
            parent = rec.tokens[1].strip("'\"")
            if child:
                children.append(child)
            if parent:
                parents.append(parent)
    ordered = list(dict.fromkeys([*parents, *children]))
    child_set = set(children)
    roots = [name for name in dict.fromkeys(parents) if name not in child_set]
    return {"has_gruptree": bool(ordered), "groups": ordered[:60], "roots": roots[:12]}


def _wells_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        items = raw.replace(",", " ").split()
    elif isinstance(raw, list):
        items = [str(item) for item in raw]
    else:
        items = []
    out: list[str] = []
    for item in items:
        name = item.strip().strip("'\"")
        if name and name not in out:
            out.append(name)
    return out


def _number(raw: Any) -> float | None:
    if raw in (None, "", False):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def normalize_group_rebind_spec(
    raw: dict[str, Any] | None,
    *,
    baseline: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Coerce the LLM's structured spec. Returns (spec, missing_fields, assumptions).

    Conventions applied when the LLM leaves them out (both reported as assumptions):
    * ``well_groups`` — one sub-group per well named ``G<well>`` under ``parent_group``;
    * ``parent_of_parent`` — the single root of baseline GRUPTREE, if the tree has exactly one.
    Anything else that is missing goes back to the LLM, never guessed from prose.
    """
    raw = raw if isinstance(raw, dict) else {}
    nested = raw.get("spec") if isinstance(raw.get("spec"), dict) else {}
    merged = {**nested, **{k: v for k, v in raw.items() if k != "spec"}}
    baseline = baseline if isinstance(baseline, dict) else {}
    assumptions: list[dict[str, Any]] = []

    wells = _wells_list(merged.get("wells"))
    parent = str(merged.get("parent_group") or "").strip().strip("'\"").upper()
    parent_of_parent = str(merged.get("parent_of_parent") or "").strip().strip("'\"").upper()
    control = str(merged.get("control") or "").strip().upper()
    rate = _number(merged.get("gas_rate", merged.get("rate", merged.get("oil_rate"))))
    effective_at = str(merged.get("effective_at") or "").strip()

    groups_in = merged.get("well_groups") if isinstance(merged.get("well_groups"), dict) else {}
    well_groups = {str(k).strip().strip("'\""): str(v).strip().strip("'\"") for k, v in groups_in.items() if str(v).strip()}
    if wells and any(not well_groups.get(well) for well in wells):
        for well in wells:
            well_groups.setdefault(well, f"G{well}")
        assumptions.append({"well_groups": "по одной подгруппе на скважину: G<скважина> под parent_group"})

    roots = [str(r) for r in (baseline.get("roots") or []) if str(r).strip()]
    if not parent_of_parent and len(roots) == 1:
        parent_of_parent = roots[0].upper()
        assumptions.append({"parent_of_parent": f"корень baseline GRUPTREE: {parent_of_parent}"})

    spec: dict[str, Any] = {
        "wells": wells,
        "parent_group": parent,
        "parent_of_parent": parent_of_parent,
        "well_groups": {well: well_groups[well] for well in wells} if wells else {},
        "control": control,
        "gas_rate": rate,
    }
    if effective_at:
        spec["effective_at"] = effective_at

    missing: list[str] = []
    if not wells:
        missing.append("wells")
    if not parent:
        missing.append("parent_group")
    if not parent_of_parent:
        missing.append("parent_of_parent")
    if not control:
        missing.append("control")
    elif control not in CONTROLS:
        missing.append("control")
    if rate is None or rate <= 0:
        missing.append("gas_rate")
    return spec, missing, assumptions


def run_group_rebind_revise(
    source_text: str,
    spec: dict[str, Any],
    *,
    file_ref: str = "schedule.inc",
) -> dict[str, Any]:
    return python_group_rebind_revise(source_text, spec, file_ref=file_ref)
