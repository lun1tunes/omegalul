"""Group membership rebind via the shared timeline JS (same algorithm as combat/golden)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .js_timeline import run_timeline_fn

TEMPLATES = Path(os.getenv("SCHEDULE_TEMPLATES") or "/templates")
if not (TEMPLATES / "schedule_timeline_runtime.py").is_file():
    TEMPLATES = Path(__file__).resolve().parents[2] / "n8n" / "templates"

GROUP_INTENT = (
    "групп",
    "gruptree",
    "gconprod",
    "перепривяз",
    "group_rebind",
    "group_membership",
    "отдельн",
)


def wants_group_rebind(blob: str, inputs: dict[str, Any] | None = None) -> bool:
    text = (blob or "").lower()
    if any(token in text for token in GROUP_INTENT):
        return True
    caps = (inputs or {}).get("requested_capability_scope") if isinstance(inputs, dict) else None
    if isinstance(caps, list) and any(
        "group" in str(item).lower() or "rebind" in str(item).lower() for item in caps
    ):
        return True
    spec = (inputs or {}).get("group_rebind") if isinstance(inputs, dict) else None
    return isinstance(spec, dict) and bool(spec)


def _hitl_blob(hitl: dict[str, Any] | None) -> str:
    if not isinstance(hitl, dict):
        return ""
    parts: list[str] = []
    answers = hitl.get("answers") or {}
    if isinstance(answers, dict):
        parts.extend(str(v) for v in answers.values())
    elif isinstance(answers, list):
        parts.extend(str(item) for item in answers)
    return "\n".join(parts)


def _parse_rate(blob: str) -> float | None:
    text = blob.replace("\u00a0", " ")
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*тыс", text, re.I)
    if match:
        return float(match.group(1).replace(",", ".")) * 1000.0
    match = re.search(r"(\d{1,3}(?:[ \u00a0]\d{3})+)\s*(?:м3|m3|газ)", text, re.I)
    if match:
        return float(match.group(1).replace(" ", "").replace("\u00a0", ""))
    match = re.search(r"(\d{4,7})\s*(?:м3|m3|газ|сут|grat)", text, re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:grat|gconprod|дебит|контроль)\D{0,12}(\d{4,7})", text, re.I)
    if match:
        return float(match.group(1))
    return None


def extract_group_rebind_spec(
    blob: str,
    source_wells: set[str],
    source_text: str = "",
    hitl: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build a timeline spec from task text + baseline names. Missing fields are listed, not invented from golden fixtures."""
    merged = "\n".join([blob or "", _hitl_blob(hitl)])
    incoming = {}
    if isinstance(inputs, dict) and isinstance(inputs.get("group_rebind"), dict):
        incoming = dict(inputs["group_rebind"])
    wells: list[str] = list(incoming.get("wells") or [])
    if not wells:
        ordered = sorted((str(w) for w in source_wells), key=len, reverse=True)
        found: list[str] = []
        for well in ordered:
            if not well or well in found:
                continue
            if re.search(rf"(?<![\w.]){re.escape(well)}(?![\w.])", merged):
                found.append(well)
        wells = found
    quoted = re.findall(r"[«\"']([A-Za-z][A-Za-z0-9_]{1,16})[»\"']", merged)
    parent = str(incoming.get("parent_group") or (quoted[0] if quoted else "")).strip().upper()
    parent_of_parent = str(incoming.get("parent_of_parent") or "").strip().upper()
    if not parent_of_parent and re.search(r"\bFIELD\b", source_text or ""):
        parent_of_parent = "FIELD"
    control = str(incoming.get("control") or "").strip().upper()
    if not control and re.search(r"газ|grat|м3|m3", merged, re.I):
        control = "GRAT"
    elif not control and re.search(r"\borat\b|нефть", merged, re.I):
        control = "ORAT"
    rate = incoming.get("rate", incoming.get("gas_rate"))
    if rate in (None, ""):
        rate = _parse_rate(merged)
    try:
        rate_n = float(rate) if rate not in (None, "") else None
    except (TypeError, ValueError):
        rate_n = None
    groups_in = incoming.get("well_groups") if isinstance(incoming.get("well_groups"), dict) else {}
    well_groups = {str(k): str(v) for k, v in groups_in.items() if str(v).strip()}
    if not well_groups and wells and parent:
        well_groups = {well: f"G{well}" for well in wells}
    spec = {
        "wells": wells,
        "parent_group": parent,
        "parent_of_parent": parent_of_parent,
        "well_groups": well_groups,
        "gas_rate": rate_n,
        "control": control,
    }
    missing: list[str] = []
    if not wells:
        missing.append("wells")
    if not parent:
        missing.append("parent_group")
    if not parent_of_parent:
        missing.append("parent_of_parent")
    if not control:
        missing.append("control")
    if not rate_n or rate_n <= 0:
        missing.append("rate")
    if wells and any(not well_groups.get(well) for well in wells):
        missing.append("well_groups")
    return spec, missing


def run_group_rebind_revise(
    source_text: str,
    spec: dict[str, Any],
    *,
    file_ref: str = "schedule.inc",
) -> dict[str, Any]:
    if not (TEMPLATES / "schedule_timeline_runtime.py").is_file():
        raise RuntimeError(f"timeline templates missing at {TEMPLATES}")
    return run_timeline_fn(
        "runGroupRebindRevise",
        source_text,
        spec,
        file_ref=file_ref,
    )
