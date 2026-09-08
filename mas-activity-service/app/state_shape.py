"""Slim, nested case-state helpers shared by Activity and compact planner context.

Blob store ids stay stable (`excel`, `schedule_source`, `schedule_source_1`, …).
Persisted `state.artifacts` groups those ids by role so planners do not see
24 numbered keys. Flat and nested shapes both round-trip through flatten/nest.

Every artifact is a card ``{artifact_id, role, kind, producer, filename, bytes, …}``:
``kind`` is ``input`` (uploaded by the engineer), ``intermediate`` or ``deliverable``
(returned by an agent); ``producer`` is ``user`` or the ``agent_id``. The case result
is the set of deliverables — no single artifact id is "the result".
JS twin: ``n8n/templates/mas_state_utils.py`` (change both, test both).
"""

from __future__ import annotations

import json
import re
from typing import Any

PREVIEW_CAP = 3

ARTIFACT_KINDS = ("input", "intermediate", "deliverable")
USER_PRODUCER = "user"
# Roles that only agents produce: without an explicit ``kind`` they are deliverables.
AGENT_ONLY_ROLES = frozenset({"schedule_out", "diff"})
# Text artifacts live inline in state (``text``), not in the blob store.
INLINE_TEXT_ROLES = frozenset({"schedule_out", "diff"})

FILE_COUNT_KEYS = (
    "excel",
    "schedule_source",
    "includes",
    "grdecl",
    "trajectories",
    "surface",
    "schedule_out",
)
_EXCEL_NAME_SUFFIXES = (".xlsx", ".xls", ".xlsm", ".xltx", ".xltm")


def input_rank(role: str = "", filename: str = "") -> int:
    """Excel workbooks before schedule includes so the first chips are the files that matter."""
    name = str(filename or "").lower()
    if str(role or "") == "excel" or name.endswith(_EXCEL_NAME_SUFFIXES):
        return 0
    if str(role or "") == "schedule_source":
        return 1
    return 2


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
    if isinstance(value, str):
        if resolved_role in INLINE_TEXT_ROLES:
            if not value.strip():
                return None
            return {
                "artifact_id": artifact_id or resolved_role,
                "role": resolved_role,
                "bytes": len(value.encode("utf-8")),
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
        put(sch.get("diff"), "diff")
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
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or role_for_artifact_id(aid))
        if role == "diff":
            nested.setdefault("schedule", {})["diff"] = item
        elif role == "excel":
            # The first workbook (id ``excel``) owns the slot; further workbooks (``excel_1``…) stay
            # visible as attachments with role ``excel`` instead of silently overwriting the slot.
            if "excel" not in nested or (aid == "excel" and str(nested["excel"].get("artifact_id")) != "excel"):
                if "excel" in nested:
                    attachments.append(nested["excel"])
                nested["excel"] = item
            else:
                attachments.append(item)
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


def artifacts_from_indexed(
    binary: dict[str, tuple[str, bytes, str]],
    *,
    producer: str = USER_PRODUCER,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Cards for engineer uploads: ``kind=input``, ``producer=user``."""
    items = []
    for key, (filename, content, mime) in binary.items():
        item: dict[str, Any] = {
            "filename": filename,
            "mime_type": mime,
            "bytes": len(content),
            "artifact_id": key,
            "role": role_for_artifact_id(key),
            "kind": "input",
            "producer": producer,
        }
        if created_at:
            item["created_at"] = created_at
        items.append(item)
    return nest_artifacts({item["artifact_id"]: item for item in items})


def artifact_file_counts(arts: Any) -> dict[str, int]:
    counts = {key: 0 for key in FILE_COUNT_KEYS}
    for aid, item in flatten_artifacts(arts).items():
        role = item.get("role") if isinstance(item, dict) else role_for_artifact_id(aid)
        if role == "schedule_include":
            if _is_grdecl(item):
                counts["grdecl"] += 1
            else:
                counts["includes"] += 1
        elif role in counts:
            counts[role] += 1
    return counts


def artifact_kind(item: Any) -> str:
    """``kind`` with a deterministic default: agent-only roles are deliverables, the rest inputs."""
    if not isinstance(item, dict):
        return "input"
    kind = str(item.get("kind") or "").strip().lower()
    if kind in ARTIFACT_KINDS:
        return kind
    role = str(item.get("role") or role_for_artifact_id(str(item.get("artifact_id") or "")))
    return "deliverable" if role in AGENT_ONLY_ROLES else "input"


def artifact_producer(item: Any) -> str:
    """``producer`` with a default: inputs come from the engineer; agent cards keep their agent_id."""
    if not isinstance(item, dict):
        return USER_PRODUCER
    producer = str(item.get("producer") or "").strip()
    if producer:
        return producer
    return USER_PRODUCER if artifact_kind(item) == "input" else ""


def _card_filename(item: dict[str, Any]) -> str:
    name = str(item.get("filename") or "").strip()
    if name:
        return name
    role = str(item.get("role") or "")
    aid = str(item.get("artifact_id") or role or "artifact")
    if role == "schedule_out":
        return "schedule_result.inc"
    if role == "diff":
        return "schedule_changes.diff"
    return aid


def _card_mime(item: dict[str, Any]) -> str:
    mime = str(item.get("mime_type") or item.get("mime") or "").strip()
    if mime:
        return mime
    role = str(item.get("role") or "")
    if role == "diff":
        return "text/x-diff; charset=utf-8"
    if role in INLINE_TEXT_ROLES:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def artifact_cards(arts: Any, *, producers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Flat list of artifact cards (no inline ``text``), inputs first, then agent results.

    ``producers`` maps artifact_id → agent_id for legacy states whose agent artifacts were
    merged before ``producer`` existed (Activity derives it from ``agent.result`` events).
    """
    inputs: list[dict[str, Any]] = []
    produced: list[dict[str, Any]] = []
    fallback = producers or {}
    for aid, item in flatten_artifacts(arts).items():
        if not isinstance(item, dict):
            continue
        kind = artifact_kind(item)
        producer = artifact_producer(item) or fallback.get(aid, "")
        text = item.get("text") if isinstance(item.get("text"), str) else None
        size = item.get("bytes")
        if size is None and text is not None:
            size = len(text.encode("utf-8"))
        card = {
            "artifact_id": aid,
            "role": str(item.get("role") or role_for_artifact_id(aid)),
            "kind": kind,
            "producer": producer,
            "filename": _card_filename(item),
            "mime_type": _card_mime(item),
            "bytes": int(size) if isinstance(size, (int, float)) else None,
            "summary": str(item.get("summary") or "").strip(),
            "created_at": item.get("created_at"),
        }
        (inputs if kind == "input" else produced).append(card)
    inputs.sort(key=lambda c: (input_rank(c["role"], c["filename"]), c["artifact_id"]))
    return inputs + produced


def deliverables(arts: Any, *, producers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    return [card for card in artifact_cards(arts, producers=producers) if card["kind"] == "deliverable"]


def artifact_text(arts: Any, artifact_id: str) -> str | None:
    """Inline text of a text artifact (``schedule_out`` / ``diff``) or ``None``."""
    item = flatten_artifacts(arts).get(str(artifact_id or ""))
    if isinstance(item, str):
        return item if item.strip() else None
    if isinstance(item, dict):
        inner = item.get("text") or item.get("content")
        if isinstance(inner, str) and inner.strip():
            return inner
    return None


def artifact_filenames(arts: Any) -> list[str]:
    """Names of the engineer's inputs (what the case started from). Excel first."""
    rows: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for i, item in enumerate(flatten_artifacts(arts).values()):
        if not isinstance(item, dict) or artifact_kind(item) != "input":
            continue
        name = str(item.get("filename") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        role = str(item.get("role") or role_for_artifact_id(str(item.get("artifact_id") or "")))
        rows.append((input_rank(role, name), i, name))
    rows.sort()
    return [name for _rank, _i, name in rows]


# Phase 2: agent results live in state.agents[<agent_id>] = {status, summary, task_id, step, data, data_keys}.
# The orchestrator does not know what an agent's data means; it only keeps it within a byte budget
# (JS twin: mas_state_utils.slimAgentData / sanitizeAgents — keep the numbers identical).
AGENT_DATA_BUDGET = 24000
AGENT_DATA_KEY_BUDGET = 12000


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return 1 << 62


def slim_agent_data(data: Any) -> dict[str, Any]:
    """Cap one agent's ``data`` by size: smaller keys first, oversized keys dropped and listed in ``omitted_keys``."""
    if not isinstance(data, dict):
        return {}
    entries = [(key, value, _json_size(value)) for key, value in data.items() if key != "omitted_keys"]
    keep: set[str] = set()
    omitted: list[str] = []
    total = 0
    for key, _value, size in sorted(entries, key=lambda e: e[2]):
        if size > AGENT_DATA_KEY_BUDGET or total + size > AGENT_DATA_BUDGET:
            omitted.append(key)
            continue
        keep.add(key)
        total += size
    out: dict[str, Any] = {key: value for key, value, _size in entries if key in keep}
    prev = data.get("omitted_keys")
    prev_omitted = [str(k) for k in prev] if isinstance(prev, list) else []
    all_omitted = list(dict.fromkeys([*prev_omitted, *omitted]))
    if all_omitted:
        out["omitted_keys"] = all_omitted
    return out


def sanitize_agents(agents: Any) -> dict[str, dict[str, Any]]:
    src = agents if isinstance(agents, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for agent_id, raw in src.items():
        if not isinstance(raw, dict):
            continue
        data = slim_agent_data(raw.get("data"))
        try:
            step = int(raw.get("step") or 0)
        except (TypeError, ValueError):
            step = 0
        out[str(agent_id)] = {
            "status": str(raw.get("status") or ""),
            "summary": str(raw.get("summary") or "")[:400],
            "task_id": str(raw.get("task_id") or ""),
            "step": step,
            "data": data,
            "data_keys": [key for key in data.keys() if key != "omitted_keys"],
        }
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


def is_unlisted_wells_gate(qid: Any, question: Any = "") -> bool:
    """Same rules as n8n ``isUnlistedWellsGate``."""
    ident = str(qid or "").lower()
    q = str(question or "").lower()
    return "unlisted" in ident or "unlisted" in q or "не из excel" in q or "лишн" in q


def compact_unlisted_policy(answers: Any) -> str | None:
    """Same rules as n8n ``readUnlistedWellsPolicy``: only explicit choice / policy fields.

    Free-text interpretation is the orchestrator's LLM pass (Phase 1.3) — this helper
    never regex-parses the engineer's words.
    """
    src = answers if isinstance(answers, dict) else {}
    for key, val in src.items():
        if not isinstance(val, dict):
            continue
        direct = str(val.get("unlisted_wells_policy") or "").lower()
        if direct in {"keep", "remove"}:
            return direct
        choice = str(val.get("choice") or "").lower()
        if is_unlisted_wells_gate(key, "") and choice in {"keep", "remove"}:
            return choice
    return None


def hitl_answer_text(answer: Any) -> str:
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict):
        for key in ("text", "label", "answer", "value", "choice"):
            if answer.get(key) not in (None, ""):
                return str(answer[key])
        return json.dumps(answer, ensure_ascii=False)[:200]
    if answer is None:
        return ""
    return str(answer)


def slim_current_task(task: Any, artifacts: Any = None, agents: Any = None) -> dict[str, Any] | None:
    """``data_keys`` = agents that already produced data (JS twin: slimCurrentTask)."""
    if not isinstance(task, dict):
        return None
    artifact_ids = task.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        artifact_ids = [key for key in flatten_artifacts(artifacts).keys() if key != "diff"]
    data_keys = task.get("data_keys")
    if not isinstance(data_keys, list):
        data_keys = list(agents.keys()) if isinstance(agents, dict) else []
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
    # Phase 2: agent results live in state.agents; state.data keeps the case result and, for cases from
    # before Phase 2, legacy buckets — slimmed with the same byte budget (JS twin: sanitizeState).
    src["agents"] = sanitize_agents(src.get("agents"))
    data = dict(src["data"]) if isinstance(src.get("data"), dict) else {}
    data.pop("facts", None)
    for key, value in list(data.items()):
        if key != "result" and isinstance(value, dict):
            data[key] = slim_agent_data(value)
    src["data"] = data
    hitl = dict(src["hitl"]) if isinstance(src.get("hitl"), dict) else {"pending": False, "questions": [], "answers": {}}
    hitl["answers"] = decode_hitl_answers(hitl.get("answers") or {})
    src["hitl"] = hitl
    cur = src.get("current_task")
    src["current_task"] = slim_current_task(cur, src.get("artifacts"), src.get("agents")) if isinstance(cur, dict) else None
    if src.get("last_error") is not None:
        src["last_error"] = slim_error(src.get("last_error"))
    return src


COMPACT_INPUTS_MAX = 12


def compact_decision_context(state: dict[str, Any]) -> dict[str, Any]:
    """What the Decision LLM sees about the case (JS twin: mas_state_utils.buildCompact).

    Domain-free: files by role, the engineer's inputs by name, agent results by ``agent_id``
    (status / summary / data_keys), plan, HITL. No per-agent fields (``excel_facts`` and the like).
    """
    src = sanitize_case_state(state)
    artifacts = src.get("artifacts") if isinstance(src.get("artifacts"), dict) else {}
    plan = src.get("plan") if isinstance(src.get("plan"), list) else []
    hitl = src.get("hitl") if isinstance(src.get("hitl"), dict) else {}
    questions = hitl.get("questions") if isinstance(hitl.get("questions"), list) else []
    q0 = questions[0] if questions and isinstance(questions[0], dict) else {}
    pending = bool(hitl.get("pending"))
    counts = artifact_file_counts(artifacts)
    answers = hitl.get("answers") if isinstance(hitl.get("answers"), dict) else {}
    input_cards = [card for card in artifact_cards(artifacts) if card.get("kind") == "input"]
    agents = src.get("agents") if isinstance(src.get("agents"), dict) else {}
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
        "inputs": [
            {"artifact_id": card["artifact_id"], "role": card["role"], "filename": card["filename"]}
            for card in input_cards[:COMPACT_INPUTS_MAX]
        ],
        "inputs_total": len(input_cards),
        "deliverables": [
            {"producer": card["producer"], "artifact_id": card["artifact_id"], "filename": card["filename"]}
            for card in deliverables(artifacts)
        ],
        "agents": {
            agent_id: {
                "status": slot["status"],
                "step": slot["step"],
                "summary": slot["summary"][:240],
                "data_keys": slot["data_keys"],
            }
            for agent_id, slot in agents.items()
        },
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
