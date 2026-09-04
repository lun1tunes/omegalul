"""Slim, nested case-state helpers shared by Activity and compact planner context.

Blob store ids stay stable (`excel`, `schedule_source`, `schedule_source_1`, …).
Persisted `state.artifacts` groups those ids by role so planners do not see
24 numbered keys. Flat and nested shapes both round-trip through flatten/nest.
"""

from __future__ import annotations

import json
import re
from typing import Any

PREVIEW_CAP = 3

FILE_COUNT_KEYS = (
    "excel",
    "schedule_source",
    "includes",
    "grdecl",
    "trajectories",
    "surface",
    "schedule_out",
)


def role_for_artifact_id(artifact_id: str) -> str:
    key = str(artifact_id or "")
    if key == "excel" or key.startswith("excel_"):
        return "excel"
    if key == "surface":
        return "surface"
    if key == "schedule_source":
        return "schedule_source"
    if key.startswith("schedule_source_"):
        return "schedule_include"
    if key == "schedule_out":
        return "schedule_out"
    if key == "trajectory" or key.startswith("trajectory_"):
        return "trajectory"
    if key == "diff":
        return "diff"
    return "attachment"


def _is_grdecl(item: Any) -> bool:
    name = str(item.get("filename") or "") if isinstance(item, dict) else ""
    return name.lower().endswith(".grdecl")


def is_nested_artifacts(arts: Any) -> bool:
    if not isinstance(arts, dict):
        return False
    sch = arts.get("schedule")
    if isinstance(sch, dict) and any(name in sch for name in ("source", "includes", "grdecl", "out", "diff")):
        return True
    if isinstance(arts.get("trajectories"), list):
        return True
    if isinstance(arts.get("attachments"), list):
        return True
    return False


def _as_item(artifact_id: str, value: Any, role: str | None = None) -> dict[str, Any] | None:
    if value is None or value in ("", [], {}):
        return None
    resolved_role = role or role_for_artifact_id(artifact_id)
    if resolved_role == "diff":
        return None
    if isinstance(value, str):
        if resolved_role == "schedule_out":
            return {
                "artifact_id": artifact_id or "schedule_out",
                "role": "schedule_out",
                "bytes": len(value),
                "text": value,
            }
        return {
            "artifact_id": artifact_id,
            "filename": value,
            "role": resolved_role,
        }
    if not isinstance(value, dict):
        return None
    item = dict(value)
    aid = str(item.get("artifact_id") or artifact_id or "").strip()
    if not aid:
        return None
    item["artifact_id"] = aid
    item["role"] = str(item.get("role") or resolved_role or role_for_artifact_id(aid))
    return item


def flatten_artifacts(arts: Any) -> dict[str, Any]:
    src = arts if isinstance(arts, dict) else {}
    out: dict[str, Any] = {}

    def put(value: Any, fallback_id: str = "") -> None:
        if fallback_id == "diff":
            if value is not None:
                out["diff"] = value
            return
        item = _as_item(fallback_id, value)
        if not item:
            return
        out[str(item["artifact_id"])] = item

    if is_nested_artifacts(src):
        put(src.get("excel"), "excel")
        put(src.get("surface"), "surface")
        sch = src.get("schedule") if isinstance(src.get("schedule"), dict) else {}
        put(sch.get("source"), "schedule_source")
        for inc in sch.get("includes") or []:
            if isinstance(inc, dict):
                put(inc, str(inc.get("artifact_id") or ""))
        for item in sch.get("grdecl") or []:
            if isinstance(item, dict):
                put(item, str(item.get("artifact_id") or ""))
        put(sch.get("out"), "schedule_out")
        if sch.get("diff") is not None:
            out["diff"] = sch["diff"]
        for traj in src.get("trajectories") or []:
            if isinstance(traj, dict):
                put(traj, str(traj.get("artifact_id") or "trajectory"))
        for att in src.get("attachments") or []:
            if isinstance(att, dict):
                put(att, str(att.get("artifact_id") or ""))
        for key, value in src.items():
            if key in {"excel", "surface", "schedule", "trajectories", "attachments"}:
                continue
            if key not in out:
                put(value, key)
        return out

    for key, value in src.items():
        if key in {"schedule", "trajectories", "attachments"}:
            continue
        put(value, key)
    return out


def nest_artifacts(arts: Any) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    includes: list[dict[str, Any]] = []
    grdecl: list[dict[str, Any]] = []
    trajectories: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    for aid, item in flatten_artifacts(arts).items():
        if aid == "diff":
            nested.setdefault("schedule", {})["diff"] = item
            continue
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or role_for_artifact_id(aid))
        if role == "excel":
            nested["excel"] = item
        elif role == "surface":
            nested["surface"] = item
        elif role == "schedule_source":
            nested.setdefault("schedule", {})["source"] = item
        elif role == "schedule_include":
            if _is_grdecl(item):
                grdecl.append(item)
            else:
                includes.append(item)
        elif role == "schedule_out":
            nested.setdefault("schedule", {})["out"] = item
        elif role == "trajectory":
            trajectories.append(item)
        else:
            attachments.append(item)
    if includes:
        nested.setdefault("schedule", {})["includes"] = includes
    if grdecl:
        nested.setdefault("schedule", {})["grdecl"] = grdecl
    if trajectories:
        nested["trajectories"] = trajectories
    if attachments:
        nested["attachments"] = attachments
    return nested


def artifacts_from_indexed(binary: dict[str, tuple[str, bytes, str]]) -> dict[str, Any]:
    items = []
    for key, (filename, content, mime) in binary.items():
        items.append(
            {
                "filename": filename,
                "mime_type": mime,
                "bytes": len(content),
                "artifact_id": key,
                "role": role_for_artifact_id(key),
            }
        )
    return nest_artifacts({item["artifact_id"]: item for item in items})


def artifact_file_counts(arts: Any) -> dict[str, int]:
    counts = {key: 0 for key in FILE_COUNT_KEYS}
    for aid, item in flatten_artifacts(arts).items():
        if aid == "diff":
            continue
        role = item.get("role") if isinstance(item, dict) else role_for_artifact_id(aid)
        if role == "schedule_include":
            if _is_grdecl(item):
                counts["grdecl"] += 1
            else:
                counts["includes"] += 1
        elif role in counts:
            counts[role] += 1
    return counts


def has_schedule_out(arts: Any) -> bool:
    item = flatten_artifacts(arts).get("schedule_out")
    if not item:
        return False
    if isinstance(item, str):
        return bool(item.strip())
    if isinstance(item, dict):
        text = item.get("text") or item.get("content") or ""
        return bool(str(text).strip() or item.get("filename") or item.get("artifact_id"))
    return True


def artifact_filenames(arts: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for aid, item in flatten_artifacts(arts).items():
        if aid in {"diff", "schedule_out"}:
            continue
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or role_for_artifact_id(aid)) == "schedule_out":
            continue
        name = str(item.get("filename") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def slim_excel_data(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    facts = out.get("facts")
    if isinstance(facts, list):
        out["facts"] = [
            {"well": row.get("well"), "date": row.get("date")}
            for row in facts
            if isinstance(row, dict)
        ]
    rows = out.get("normalized_rows")
    if isinstance(rows, list):
        slim_rows = []
        for table in rows:
            if not isinstance(table, dict):
                continue
            preview = table.get("preview") if isinstance(table.get("preview"), list) else []
            count = table.get("preview_count")
            if count is None:
                count = table.get("row_count")
            if count is None:
                count = len(preview)
            slim_rows.append(
                {
                    "table_id": table.get("table_id"),
                    "columns": table.get("columns") or [],
                    "row_count": table.get("row_count") if table.get("row_count") is not None else count,
                    "preview_count": int(count),
                    "preview": preview[:PREVIEW_CAP],
                }
            )
        out["normalized_rows"] = slim_rows
    return out


def decode_hitl_answer(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
        or (text.startswith('"') and text.endswith('"'))
    ):
        return value
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return value
    if isinstance(parsed, str):
        inner = parsed.strip()
        if (inner.startswith("{") and inner.endswith("}")) or (inner.startswith("[") and inner.endswith("]")):
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                return parsed
        return parsed
    return parsed


def decode_hitl_answers(answers: Any) -> dict[str, Any]:
    src = answers if isinstance(answers, dict) else {}
    return {str(key): decode_hitl_answer(val) for key, val in src.items()}


def compact_unlisted_policy(answers: Any) -> str | None:
    src = answers if isinstance(answers, dict) else {}
    for key, val in src.items():
        blob = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        text = f"{key} {blob}".lower()
        match = re.search(r"unlisted_wells_policy\s*[:=]\s*(keep|remove)", text)
        if match:
            return match.group(1)
        if "unlisted" in text:
            word = re.search(r"\b(keep|remove)\b", text)
            if word:
                return word.group(1)
    return None


def hitl_answer_text(answer: Any) -> str:
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict):
        for key in ("text", "answer", "value"):
            if answer.get(key) not in (None, ""):
                return str(answer[key])
        return json.dumps(answer, ensure_ascii=False)[:200]
    if answer is None:
        return ""
    return str(answer)


def slim_current_task(task: Any, artifacts: Any = None, data: Any = None) -> dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    artifact_ids = task.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        artifact_ids = [key for key in flatten_artifacts(artifacts).keys() if key != "diff"]
    data_keys = task.get("data_keys")
    if not isinstance(data_keys, list):
        if isinstance(data, dict):
            data_keys = list(data.keys())
        else:
            context = task.get("context") if isinstance(task.get("context"), dict) else {}
            inner = context.get("data") if isinstance(context.get("data"), dict) else {}
            data_keys = list(inner.keys())
    if not task.get("task_id") and not task.get("agent_id"):
        return None
    return {
        "task_id": task.get("task_id"),
        "agent_id": task.get("agent_id"),
        "artifact_ids": artifact_ids,
        "data_keys": data_keys,
    }


def slim_error(err: Any) -> dict[str, Any] | None:
    if not isinstance(err, dict):
        return None if err in (None, "", {}, []) else {"message": str(err), "agent_id": None}
    return {"message": err.get("message") or "", "agent_id": err.get("agent_id")}


def bump_version(state: dict[str, Any]) -> dict[str, Any]:
    state["version"] = int(state.get("version") or 0) + 1
    return state


def sanitize_case_state(state: Any) -> dict[str, Any]:
    src = dict(state) if isinstance(state, dict) else {}
    arts = src.get("artifacts") if isinstance(src.get("artifacts"), dict) else {}
    if arts.get("file") and not arts.get("excel"):
        arts = {**arts, "excel": arts["file"]}
    if arts.get("schedule_files") and not arts.get("schedule_source") and not is_nested_artifacts(arts):
        arts = {**arts, "schedule_source": arts["schedule_files"]}
    src["artifacts"] = nest_artifacts(arts)
    data = dict(src["data"]) if isinstance(src.get("data"), dict) else {}
    data.pop("facts", None)
    if isinstance(data.get("excel"), dict):
        data["excel"] = slim_excel_data(data["excel"])
    src["data"] = data
    hitl = dict(src["hitl"]) if isinstance(src.get("hitl"), dict) else {"pending": False, "questions": [], "answers": {}}
    hitl["answers"] = decode_hitl_answers(hitl.get("answers") or {})
    src["hitl"] = hitl
    cur = src.get("current_task")
    src["current_task"] = slim_current_task(cur, src.get("artifacts"), src.get("data")) if isinstance(cur, dict) else None
    if src.get("last_error") is not None:
        src["last_error"] = slim_error(src.get("last_error"))
    return src


def compact_decision_context(state: dict[str, Any]) -> dict[str, Any]:
    src = sanitize_case_state(state)
    artifacts = src.get("artifacts") if isinstance(src.get("artifacts"), dict) else {}
    data = src.get("data") if isinstance(src.get("data"), dict) else {}
    excel = data.get("excel") if isinstance(data.get("excel"), dict) else {}
    plan = src.get("plan") if isinstance(src.get("plan"), list) else []
    hitl = src.get("hitl") if isinstance(src.get("hitl"), dict) else {}
    questions = hitl.get("questions") if isinstance(hitl.get("questions"), list) else []
    q0 = questions[0] if questions and isinstance(questions[0], dict) else {}
    pending = bool(hitl.get("pending"))
    counts = artifact_file_counts(artifacts)
    answers = hitl.get("answers") if isinstance(hitl.get("answers"), dict) else {}
    facts = excel.get("facts") if isinstance(excel.get("facts"), list) else []
    flat = flatten_artifacts(artifacts)
    excel_meta = flat.get("excel") if isinstance(flat.get("excel"), dict) else {}
    source_meta = flat.get("schedule_source") if isinstance(flat.get("schedule_source"), dict) else {}
    err = src.get("last_error")
    cur = src.get("current_task")
    current = None
    if isinstance(cur, dict) and (cur.get("task_id") or cur.get("agent_id")):
        current = {"task_id": cur.get("task_id"), "agent_id": cur.get("agent_id")}
    return {
        "goal": str(src.get("goal") or "")[:500],
        "task_name": str(src.get("task_name") or "").strip(),
        "status": src.get("status") or "",
        "files": counts,
        "has_excel": counts["excel"] > 0,
        "has_schedule_source": counts["schedule_source"] > 0,
        "has_schedule_out": counts["schedule_out"] > 0,
        "excel_filename": excel_meta.get("filename") or None,
        "schedule_source_filename": source_meta.get("filename") or None,
        "excel_facts": len(facts),
        "wells_in_excel": [str(row.get("well") or "") for row in facts if isinstance(row, dict)][:20],
        "schedule_root": src.get("schedule_root") or "",
        "plan": [
            {"id": item.get("id"), "status": item.get("status")}
            for item in plan
            if isinstance(item, dict)
        ],
        "current_task": current,
        "hitl_pending": pending,
        "hitl_question": (str(q0.get("question") or "")[:200] or None) if pending else None,
        "hitl_answer_ids": [str(key) for key in answers.keys()],
        "unlisted_wells_policy": compact_unlisted_policy(answers),
        "step_count": int(src.get("step_count") or 0),
        "version": int(src.get("version") or 0),
        "last_error": (err.get("message") if isinstance(err, dict) else None) or None,
    }
