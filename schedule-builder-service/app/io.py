"""Load baseline SCHEDULE and Excel commissioning facts for a case."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.request import urlopen


def _filename_base(name: Any) -> str:
    return Path(str(name or "").strip()).name.lower()


def schedule_meta(inputs: dict[str, Any]) -> dict[str, Any]:
    artifacts = inputs.get("artifacts") if isinstance(inputs.get("artifacts"), dict) else {}
    root = _filename_base(inputs.get("schedule_root") or inputs.get("root_path") or "")
    if root:
        for item in artifacts.values():
            if isinstance(item, dict) and _filename_base(item.get("filename")) == root:
                return item
    meta = artifacts.get("schedule_source") if isinstance(artifacts.get("schedule_source"), dict) else {}
    if not meta and isinstance(artifacts.get("schedule_files"), dict):
        meta = artifacts.get("schedule_files") or {}
    return meta if isinstance(meta, dict) else {}


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
