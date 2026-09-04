"""Load baseline SCHEDULE and Excel commissioning facts for a case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen


def _filename_base(name: Any) -> str:
    return Path(str(name or "").strip()).name.lower()


def schedule_meta(inputs: dict[str, Any]) -> dict[str, Any]:
    artifacts = inputs.get("artifacts") if isinstance(inputs.get("artifacts"), dict) else {}
    sch = artifacts.get("schedule") if isinstance(artifacts.get("schedule"), dict) else {}
    metas: list[dict[str, Any]] = []
    if isinstance(sch.get("source"), dict):
        metas.append(sch["source"])
    for bag in (sch.get("includes") or [], sch.get("grdecl") or []):
        for item in bag:
            if isinstance(item, dict):
                metas.append(item)
    for key, item in artifacts.items():
        if key in {"schedule", "trajectories", "attachments"}:
            continue
        if isinstance(item, dict):
            metas.append(item)
    root = _filename_base(inputs.get("schedule_root") or inputs.get("root_path") or "")
    if root:
        for item in metas:
            if _filename_base(item.get("filename")) == root:
                return item
    meta = artifacts.get("schedule_source") if isinstance(artifacts.get("schedule_source"), dict) else {}
    if not meta and isinstance(sch.get("source"), dict):
        meta = sch["source"]
    if not meta and isinstance(artifacts.get("schedule_files"), dict):
        meta = artifacts.get("schedule_files") or {}
    return meta if isinstance(meta, dict) else {}


def artifacts_for_tools(arts: Any) -> dict[str, Any]:
    """Flat artifact map for load_source: blob ids stay excel / schedule_source / schedule_source_N."""
    src = arts if isinstance(arts, dict) else {}
    out: dict[str, Any] = {}
    sch = src.get("schedule") if isinstance(src.get("schedule"), dict) else {}

    def put(item: Any, fallback: str = "") -> None:
        if not isinstance(item, dict):
            return
        aid = str(item.get("artifact_id") or fallback or "").strip()
        if aid:
            out[aid] = item

    put(src.get("excel") if isinstance(src.get("excel"), dict) else None, "excel")
    put(src.get("surface") if isinstance(src.get("surface"), dict) else None, "surface")
    put(sch.get("source") if isinstance(sch.get("source"), dict) else None, "schedule_source")
    for bag in (sch.get("includes") or [], sch.get("grdecl") or []):
        for item in bag:
            put(item)
    put(sch.get("out") if isinstance(sch.get("out"), dict) else None, "schedule_out")
    for traj in src.get("trajectories") or []:
        put(traj, "trajectory")
    for att in src.get("attachments") or []:
        put(att)
    for key, item in src.items():
        if key in {"schedule", "trajectories", "attachments", "excel", "surface"}:
            continue
        if key not in out and isinstance(item, dict):
            put(item, key)
    return out


def bind_case_packet(
    inputs: dict[str, Any] | None,
    context: dict[str, Any] | None,
    case_id: str,
    activity: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fill artifact metas and excel facts from GET /cases/{id}/state when the orchestrator sent refs only."""
    inputs = dict(inputs or {})
    context = dict(context) if isinstance(context, dict) else {}
    base = str(inputs.get("activity_base_url") or activity).rstrip("/")
    if not base or not str(case_id or "").strip():
        return inputs, context
    existing = inputs.get("artifacts") if isinstance(inputs.get("artifacts"), dict) else {}
    excel = excel_bucket(context)
    has_source = bool(
        existing.get("schedule_source")
        or (isinstance(existing.get("schedule"), dict) and existing["schedule"].get("source"))
    )
    has_facts = bool(excel.get("facts"))
    if has_source and has_facts:
        return inputs, context
    try:
        with urlopen(f"{base}/cases/{case_id}/state", timeout=15) as resp:
            packet = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return inputs, context
    state = packet.get("state") if isinstance(packet, dict) else None
    if not isinstance(state, dict):
        return inputs, context
    from_case = artifacts_for_tools(state.get("artifacts"))
    if from_case:
        inputs["artifacts"] = {**from_case, **existing}
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    excel_data = data.get("excel") if isinstance(data.get("excel"), dict) else {}
    if excel_data:
        inner = context.get("data") if isinstance(context.get("data"), dict) else {}
        if not isinstance(inner.get("excel"), dict):
            context["data"] = {**inner, "excel": excel_data}
        if not isinstance(context.get("excel"), dict):
            context["excel"] = excel_data
    if not inputs.get("schedule_root") and state.get("schedule_root"):
        inputs["schedule_root"] = state["schedule_root"]
    return inputs, context


def load_source(inputs: dict[str, Any], case_id: str, activity: str = "") -> str:
    if inputs.get("schedule_text"):
        return str(inputs["schedule_text"])
    path = inputs.get("schedule_source") or inputs.get("schedule_path")
    meta = schedule_meta(inputs)
    if path and Path(str(path)).is_file():
        return Path(str(path)).read_text(encoding="utf-8")
    base = str(inputs.get("activity_base_url") or activity).rstrip("/")
    artifact_id = (meta or {}).get("artifact_id") or "schedule_source"
    if base and case_id:
        url = f"{base}/cases/{case_id}/artifacts/{artifact_id}"
        with urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    return ""


def excel_bucket(context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    data = context.get("data") if isinstance(context.get("data"), dict) else {}
    excel = data.get("excel") if isinstance(data.get("excel"), dict) else context.get("excel")
    return excel if isinstance(excel, dict) else {}


def _is_well_column(key: str) -> bool:
    low = str(key or "").lower()
    return any(token in low for token in ("скважин", "well")) and "дат" not in low


def _is_new_date_column(key: str) -> bool:
    low = str(key or "").lower()
    if any(skip in low for skip in ("baseline", "старая", "old", ".inc", "исходн")):
        return False
    return any(token in low for token in ("дат", "date", "ввод", "commission"))


def _uniq_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One date per well. First fact wins (Excel specialist facts before preview)."""
    uniq: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in facts:
        well = str(fact.get("well") or fact.get("entity") or "").strip()
        date = fact.get("date") if fact.get("date") not in (None, "") else fact.get("value")
        key = well.casefold()
        if not well or date in (None, "") or key in seen:
            continue
        seen.add(key)
        uniq.append({"well": well, "date": date, "values": fact.get("values") or {}})
    return uniq


def commissioning_facts(context: dict[str, Any], inputs: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for bag in (inputs, context, excel_bucket(context)):
        raw = bag.get("facts") if isinstance(bag, dict) else None
        if isinstance(raw, list):
            facts.extend([row for row in raw if isinstance(row, dict)])
    specialist = _uniq_facts(facts)
    if specialist:
        return specialist
    excel = excel_bucket(context)
    for table in excel.get("normalized_rows") or []:
        preview = table.get("preview") if isinstance(table, dict) else []
        if not isinstance(preview, list):
            continue
        for row in preview:
            if not isinstance(row, dict):
                continue
            well = ""
            date = None
            for key, value in row.items():
                if _is_well_column(str(key)):
                    well = str(value or "").strip()
                elif _is_new_date_column(str(key)):
                    date = value
            if well and date not in (None, ""):
                facts.append({"well": well, "date": date, "values": row})
    return _uniq_facts(facts)


def file_ref(inputs: dict[str, Any]) -> str:
    meta = schedule_meta(inputs)
    return str(
        inputs.get("schedule_root")
        or (meta or {}).get("filename")
        or "schedule.inc"
    )
