"""Datasets — the flexible data contract between agents.

An agent that reads tables (Excel Extractor) fixes what it found as a *dataset*: rows with declared
fields under ``agent_result.data[<name>]``::

    {"kind": "dataset", "name": "well_events", "title": "Мероприятия по скважинам",
     "fields": [{"name": "well", "type": "text", "source_column": "Скважина"}, …],
     "row_count": 12, "rows": [{…}, …] | "preview": [{…}, …],
     "artifact_id": "dataset_well_events", "source": {"file", "sheet", "table_id", "range"}}

Small tables travel inline (``rows``, ≤ ``INLINE_ROWS_BYTES``); every dataset is also uploaded to Activity
as a JSON artifact so the rows survive the orchestrator's state budget and the engineer can download
them. Consumers read rows with ``dataset_rows(entry, activity)`` / ``CasePacket.dataset(name)`` and never
care which of the two transports was used.

The orchestrator asks for a shape in the same vocabulary — ``agent_task.inputs.expected_output``::

    {"datasets": [{"name": "well_events", "description": "мероприятия по скважинам",
                   "fields": [{"name": "well", "description": "скважина", "type": "text"}, …]}],
     "consumers": [{"agent_id": "schedule_builder", "title": "Schedule Builder",
                    "needs": {"facts": "даты ввода «скважина — дата»"}}]}

``datasets`` is what the Decision LLM wrote from the goal; ``consumers`` is deterministic — the
``input_schema.data`` of agents with open plan items. ``expected_output(inputs)`` sanitises both.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .activity import ActivityClient

DATASET_KIND = "dataset"
FIELD_TYPES = ("text", "number", "date", "boolean")
#: Rows are kept inline in ``data[<name>]`` while their JSON stays under this size (the orchestrator keeps
#: 12 000 bytes per data key — ``AGENT_DATA_KEY_BUDGET`` in ``mas_state_utils.py``).
INLINE_ROWS_BYTES = 9000
PREVIEW_ROWS = 5
MAX_EXPECTED_DATASETS = 6
MAX_EXPECTED_FIELDS = 24
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def dataset_name(value: Any) -> str:
    """Canonical dataset / field name: latin identifier (``well_events``); '' when it cannot be one."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if _NAME_RE.fullmatch(text) else ""


def artifact_id_for(name: str) -> str:
    return f"dataset_{name}"


def is_dataset(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == DATASET_KIND and isinstance(value.get("fields"), list)


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 1 << 30


def dataset_entry(
    name: str,
    fields: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    title: str = "",
    source: dict[str, Any] | None = None,
    artifact_id: str = "",
    issues: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``data[<name>]`` value: rows inline when small, otherwise a preview plus the artifact reference."""
    entry: dict[str, Any] = {
        "kind": DATASET_KIND,
        "name": name,
        "title": str(title or ""),
        "fields": [dict(f) for f in fields],
        "row_count": len(rows),
        "source": dict(source or {}),
    }
    if artifact_id:
        entry["artifact_id"] = artifact_id
    if _json_size(rows) <= INLINE_ROWS_BYTES:
        entry["rows"] = rows
    else:
        entry["preview"] = rows[:PREVIEW_ROWS]
    if issues:
        entry["issues"] = dict(issues)
    return entry


def dataset_json_bytes(entry: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    """The artifact body: the full dataset (rows included) as UTF-8 JSON."""
    body = {k: v for k, v in entry.items() if k not in {"rows", "preview", "artifact_id"}}
    body["rows"] = rows
    return json.dumps(body, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def dataset_rows(entry: dict[str, Any], activity: ActivityClient | None = None) -> list[dict[str, Any]]:
    """All rows of a dataset entry: inline ``rows`` or the artifact downloaded from Activity.

    Raises ``FileNotFoundError`` when the rows are not inline and no Activity is configured to fetch them.
    """
    if not is_dataset(entry):
        return []
    if isinstance(entry.get("rows"), list):
        return [r for r in entry["rows"] if isinstance(r, dict)]
    artifact_id = str(entry.get("artifact_id") or "")
    if not artifact_id or activity is None or not activity.configured:
        raise FileNotFoundError(f"dataset {entry.get('name')!r}: rows are not inline and no artifact to download")
    raw, _ = activity.download(artifact_id)
    body = json.loads(raw.decode("utf-8"))
    rows = body.get("rows") if isinstance(body, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def upstream_datasets(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every dataset fixed by completed agents of the case, by name (later steps win)."""
    from .packet import upstream_agent_data  # local import: packet imports this module

    return {name: value for name, value in upstream_agent_data(state).items() if is_dataset(value)}


# -- expected_output (what the orchestrator asked for) -----------------------------------------------


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def expected_datasets(raw: Any) -> list[dict[str, Any]]:
    """Sanitised ``expected_output.datasets``: valid names, ≤ 6 datasets, ≤ 24 fields each, known types."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = dataset_name(item.get("name"))
        if not name or any(d["name"] == name for d in out):
            continue
        fields: list[dict[str, Any]] = []
        for f in item.get("fields") if isinstance(item.get("fields"), list) else []:
            fname = dataset_name(f.get("name") if isinstance(f, dict) else f)
            if not fname or any(x["name"] == fname for x in fields):
                continue
            field: dict[str, Any] = {"name": fname}
            if isinstance(f, dict):
                if _clip(f.get("description"), 160):
                    field["description"] = _clip(f.get("description"), 160)
                ftype = str(f.get("type") or "").strip().lower()
                if ftype in FIELD_TYPES:
                    field["type"] = ftype
            fields.append(field)
            if len(fields) >= MAX_EXPECTED_FIELDS:
                break
        entry: dict[str, Any] = {"name": name, "fields": fields}
        if _clip(item.get("description"), 240):
            entry["description"] = _clip(item.get("description"), 240)
        out.append(entry)
        if len(out) >= MAX_EXPECTED_DATASETS:
            break
    return out


def expected_consumers(raw: Any) -> list[dict[str, Any]]:
    """Sanitised ``expected_output.consumers``: agents with open plan items and what their input_schema.data names."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        needs = item.get("needs") if isinstance(item.get("needs"), dict) else {}
        needs = {str(k): _clip(v, 200) for k, v in needs.items() if str(k).strip()}
        if not needs:
            continue
        out.append({"agent_id": _clip(item.get("agent_id"), 64), "title": _clip(item.get("title"), 80), "needs": needs})
        if len(out) >= MAX_EXPECTED_DATASETS:
            break
    return out


def expected_output(inputs: Any) -> dict[str, Any]:
    """``agent_task.inputs.expected_output`` for the LLM brief; ``{}`` when the orchestrator asked for nothing."""
    raw = inputs.get("expected_output") if isinstance(inputs, dict) else None
    if not isinstance(raw, dict):
        return {}
    datasets = expected_datasets(raw.get("datasets"))
    consumers = expected_consumers(raw.get("consumers"))
    out: dict[str, Any] = {}
    if datasets:
        out["datasets"] = datasets
    if consumers:
        out["consumers"] = consumers
    return out


def expected_names(inputs: Any) -> list[str]:
    """Dataset names the orchestrator explicitly asked for (``datasets``), in order.

    ``consumers`` is context, not a requirement: a consumer's ``input_schema.data`` lists everything it can
    use (``facts`` *and* ``new_wells``), while the task may need only one of them.
    """
    return [d["name"] for d in expected_output(inputs).get("datasets") or []]
