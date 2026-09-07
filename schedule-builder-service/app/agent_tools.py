"""Deterministic tools for the n8n Schedule Builder LLM. Never invent wells or dates."""

from __future__ import annotations

import json
import re
from typing import Any

from .apply import apply_operations
from .commissioning import run_commissioning_revise
from .diff import unified_diff
from .emit import emit_schedule
from .group_rebind import CONTROLS, gruptree_summary, normalize_group_rebind_spec, run_group_rebind_revise
from .io import bind_case_packet, commissioning_facts, excel_bucket, file_ref, load_source
from .keywords import keyword_object, search_keywords
from .parse import parse_schedule, well_names
from .well_model import build_well_objects, find_well
from . import sessions
from .validate import validate_emitted

UNITS = "METRIC"
MAX_RECORDS = 40
MAX_WELLS = 200


def _compact_inspect(source: str) -> dict[str, Any]:
    doc = parse_schedule(source)
    wells = sorted(well_names(doc))
    well_objects = build_well_objects(doc)
    dates: list[str] = []
    keywords: list[str] = []
    groups: list[dict[str, str]] = []
    for block in doc.blocks:
        keywords.append(block.keyword)
        if block.keyword == "DATES":
            for rec in block.records:
                if rec.tokens:
                    dates.append(rec.tokens[0])
        if block.keyword == "GRUPTREE":
            for rec in block.records:
                if len(rec.tokens) >= 2:
                    groups.append(
                        {
                            "child": rec.tokens[0].strip("'\""),
                            "parent": rec.tokens[1].strip("'\""),
                        }
                    )
    seen = list(dict.fromkeys(keywords))
    return {
        "well_count": len(wells),
        "wells": wells[:MAX_WELLS],
        "wells_truncated": len(wells) > MAX_WELLS,
        "well_objects": well_objects[:MAX_WELLS],
        "well_objects_truncated": len(well_objects) > MAX_WELLS,
        "keywords_present": seen,
        "date_count": len(dates),
        "dates_preview": dates[:12],
        "gruptree_preview": groups[:40],
        "source_bytes": len(source.encode("utf-8")),
        "units": UNITS,
    }


def _agent_result(
    task_id: str,
    status: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    requests: list[Any] | None = None,
    assumptions: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "agent_id": "schedule_builder",
        "status": status,
        "message": message,
        "data": data or {},
        "artifacts": artifacts or {},
        "issues": issues or [],
        "assumptions": assumptions or [{"units": UNITS}],
        "requests": requests or [],
    }


# Tools that fix a result in the session. Whole-task tools produce the final answer for the run;
# apply_operations / build_schedule may legitimately chain after a completed point edit.
RESULT_TOOLS = {"apply_commissioning", "apply_group_rebind", "ask_engineer", "apply_operations", "build_schedule"}
WHOLE_TASK_TOOLS = {"apply_commissioning", "apply_group_rebind", "ask_engineer"}


def _store_result(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    state["result"] = result
    sessions.save(state)
    compact = {k: v for k, v in result.items() if k != "artifacts"}
    arts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    compact["artifacts"] = {
        "schedule_out": bool(str(arts.get("schedule_out") or "").strip()),
        "diff_bytes": len(str(arts.get("diff") or "").encode("utf-8")),
    }
    return compact


def open_session(task: dict[str, Any], *, activity: str = "") -> dict[str, Any]:
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    context = task.get("context") if isinstance(task.get("context"), dict) else {}
    case_id = str(task.get("case_id") or "")
    task_id = str(task.get("task_id") or "")
    inputs, context = bind_case_packet(inputs, context, case_id, activity)
    try:
        source = load_source(inputs, case_id, activity)
    except Exception as exc:
        return {
            "ok": False,
            "status": "needs_input",
            "result": _agent_result(
                task_id,
                "needs_input",
                f"Нет исходного SCHEDULE: {exc}",
                issues=[{"type": "missing_schedule_source"}],
                requests=[{"question_id": "Q-sched", "question": "Приложите baseline .inc", "options": []}],
                assumptions=[],
            ),
        }
    if not str(source).strip():
        return {
            "ok": False,
            "status": "needs_input",
            "result": _agent_result(
                task_id,
                "needs_input",
                "Нет исходного SCHEDULE",
                issues=[{"type": "missing_schedule_source"}],
                requests=[{"question_id": "Q-sched", "question": "Приложите baseline .inc", "options": []}],
                assumptions=[],
            ),
        }
    facts = commissioning_facts(context, inputs)
    inspect = _compact_inspect(source)
    state = sessions.put(
        {
            "session_id": sessions.new_session_id(),
            "case_id": case_id,
            "task_id": task_id,
            "objective": str(task.get("objective") or ""),
            "handoff_message": str(task.get("handoff_message") or ""),
            "source_text": source,
            "working_text": source,
            "file_ref": file_ref(inputs),
            "inputs": inputs,
            "context": context,
            "facts": facts,
            "result": None,
        }
    )
    return {
        "ok": True,
        "session_id": state["session_id"],
        "task_id": task_id,
        "inspect": inspect,
        "fact_count": len(facts),
        "facts_preview": [
            {"well": row.get("well"), "date": row.get("date")} for row in facts[:40]
        ],
        "objective": state["objective"],
        "handoff_message": state["handoff_message"],
        # What the engineer already answered / asked to rework — the LLM must see it, not re-ask.
        "engineer_answers": _engineer_answers(state),
        "rework_reason": str(inputs.get("rework_reason") or ""),
    }


NO_APPLY_QUESTION = (
    "Schedule Builder не смог определить, что именно изменить в SCHEDULE. "
    "Опишите задачу подробнее: какие скважины, какие даты или режимы работы и откуда взять значения "
    "(Excel, текст, baseline)."
)


def session_result(session_id: str) -> dict[str, Any]:
    state = sessions.get(session_id)
    result = state.get("result")
    if not (isinstance(result, dict) and result.get("status")):
        working = str(state.get("working_text") or "")
        source = str(state.get("source_text") or "")
        if working.strip() and working != source:
            execute_tool(session_id, "build_schedule", {})
            state = sessions.get(session_id)
            result = state.get("result")
    if isinstance(result, dict) and result.get("status"):
        return result
    return _agent_result(
        str(state.get("task_id") or ""),
        "needs_input",
        "Schedule Builder не внёс изменений: не хватает данных, чтобы понять задачу.",
        issues=[{"type": "no_apply"}],
        requests=[
            {
                "question_id": "Q-apply",
                "question": NO_APPLY_QUESTION,
                "options": [],
                "accepts": {"free_text": True, "files": ["xlsx", ".inc"]},
            }
        ],
    )


def _engineer_answers(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact view of HITL answers for the LLM: question id, chosen option, text (no raw JSON blobs)."""
    context = state.get("context") if isinstance(state.get("context"), dict) else {}
    hitl = context.get("hitl") if isinstance(context.get("hitl"), dict) else {}
    answers = hitl.get("answers") if isinstance(hitl.get("answers"), dict) else {}
    out: list[dict[str, Any]] = []
    for key, value in answers.items():
        parsed = _parse_jsonish(value) if not isinstance(value, dict) else value
        row: dict[str, Any] = {"question_id": str(key)}
        if isinstance(parsed, dict):
            choice = str(parsed.get("choice") or "").strip()
            label = str(parsed.get("label") or "").strip()
            text = str(parsed.get("text") or parsed.get("answer") or "").strip()
            if choice:
                row["choice"] = choice
            if label:
                row["label"] = label
            if text:
                row["text"] = text[:400]
            for facts_key in ("new_wells", "new_well_defs"):
                if isinstance(parsed.get(facts_key), list):
                    row["attached_facts"] = f"{facts_key}: {len(parsed[facts_key])} записей"
            for policy_key in ("unlisted_wells_policy",):
                if parsed.get(policy_key):
                    row[policy_key] = str(parsed[policy_key])
        elif isinstance(value, str) and value.strip():
            row["text"] = value.strip()[:400]
        else:
            continue
        out.append(row)
    return out[:12]


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip()[:1] in "{[":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _hitl_payloads(state: dict[str, Any]) -> list[Any]:
    context = state.get("context") if isinstance(state.get("context"), dict) else {}
    hitl = context.get("hitl") if isinstance(context.get("hitl"), dict) else {}
    answers = hitl.get("answers") if isinstance(hitl.get("answers"), dict) else {}
    out: list[Any] = []
    for value in answers.values():
        parsed = _parse_jsonish(value)
        if parsed is not None:
            out.append(parsed)
        elif isinstance(value, str) and value.strip():
            out.append(value)
    return out


def _wells_phrase(wells: list[str], limit: int = 6) -> str:
    shown = ", ".join(wells[:limit])
    if len(wells) > limit:
        shown += f" и ещё {len(wells) - limit}"
    return shown


def _plural_wells(count: int, case: str = "gen") -> str:
    """Russian agreement for 'скважина': gen — '(даты) 1 скважины / 3 скважин', acc — 'добавил 1 скважину / 3 скважины / 5 скважин'."""
    rem10, rem100 = count % 10, count % 100
    one = rem10 == 1 and rem100 != 11
    few = 2 <= rem10 <= 4 and not 12 <= rem100 <= 14
    if case == "acc":
        word = "скважину" if one else ("скважины" if few else "скважин")
    elif case == "nom":
        word = "скважина" if one else ("скважины" if few else "скважин")
    else:
        word = "скважины" if one else "скважин"
    return f"{count} {word}"


def summarize_commissioning_result(revised: dict[str, Any]) -> dict[str, Any]:
    """Engineer-facing summary built from what was actually changed (not a template count).

    Uses the revise result: records really retargeted (``moved``), wells added from
    definitions, wells removed as unlisted, wells kept as in baseline.
    """
    moved = [row for row in (revised.get("moved") or []) if isinstance(row, dict)]
    shifted_wells = sorted({str(row.get("well") or "") for row in moved} - {""})
    shift_dates = {str(row.get("well") or ""): str(row.get("to") or "") for row in moved}
    added = sorted({
        str(row.get("well") or "")
        for row in (revised.get("new_wells_applied") or [])
        if isinstance(row, dict)
    } - {""})
    removed = sorted({
        str(row.get("well") or "")
        for row in (revised.get("removed") or [])
        if isinstance(row, dict)
    } - {""})
    unlisted = [str(w) for w in (revised.get("unlisted_wells") or [])]
    policy = str(revised.get("unlisted_wells_policy") or "keep")
    kept = unlisted if policy == "keep" else []
    unchanged = sorted(
        {str(row.get("well") or "") for row in (revised.get("shifts") or []) if isinstance(row, dict)}
        - set(shifted_wells) - set(added) - {""}
    )

    keywords: list[str] = []
    for row in moved:
        kw = str(row.get("keyword") or "").upper()
        if kw and kw not in keywords:
            keywords.append(kw)
    if added:
        for kw in ("WELSPECS", "COMPDATMD", "WCONPROD"):
            if kw not in keywords:
                keywords.append(kw)
    if removed:
        for row in revised.get("removed") or []:
            kw = str(row.get("keyword") or "").upper() if isinstance(row, dict) else ""
            if kw and kw not in keywords:
                keywords.append(kw)
    if moved or added or removed:
        keywords.insert(0, "DATES")

    parts: list[str] = []
    if shifted_wells:
        sample = ", ".join(
            f"{w} → {shift_dates[w]}" if shift_dates.get(w) else w for w in shifted_wells[:4]
        )
        tail = f" и ещё {len(shifted_wells) - 4}" if len(shifted_wells) > 4 else ""
        parts.append(f"Сдвинул даты ввода {_plural_wells(len(shifted_wells))}: {sample}{tail}.")
    if unchanged:
        parts.append(f"Даты {_plural_wells(len(unchanged))} из Excel уже совпадали с baseline ({_wells_phrase(unchanged)}).")
    if added:
        parts.append(
            f"Добавил {_plural_wells(len(added), 'acc')} ({_wells_phrase(added)}) — WELSPECS, COMPDATMD и WCONPROD на датах ввода."
        )
    if removed:
        parts.append(f"Убрал из прогноза {_plural_wells(len(removed), 'acc')} вне Excel: {_wells_phrase(removed)}.")
    if kept:
        parts.append(f"{_plural_wells(len(kept), 'acc').capitalize()} вне Excel оставил как в baseline: {_wells_phrase(kept)}.")
    status = str(revised.get("status") or "")
    if not parts:
        parts.append("SCHEDULE без изменений: даты из Excel совпадают с baseline." if status == "noop" else "SCHEDULE без изменений.")
    return {
        "message": " ".join(parts),
        "changed_keywords": keywords,
        "wells_shifted": shifted_wells,
        "wells_added": added,
        "wells_removed": removed,
        "wells_kept_unlisted": kept,
    }


def _unlisted_policy(state: dict[str, Any]) -> str | None:
    """HITL / inputs enum is authority. Prose in the objective is not.

    Empty means commissioning_revise decides: silent keep, or HITL when prose
    suggests remove. Do not default to keep here — that skips the HITL gate.
    """
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    raw = str(inputs.get("unlisted_wells_policy") or "").strip().lower()
    if raw in {"keep", "remove"}:
        return raw
    # Activity option button: answers[<unlisted question id>] = {"choice": "keep"|"remove", "text": label}
    context = state.get("context") if isinstance(state.get("context"), dict) else {}
    hitl = context.get("hitl") if isinstance(context.get("hitl"), dict) else {}
    answers = hitl.get("answers") if isinstance(hitl.get("answers"), dict) else {}
    for key, value in answers.items():
        parsed = _parse_jsonish(value) if not isinstance(value, dict) else value
        if "unlisted" in str(key).lower() and isinstance(parsed, dict):
            choice = str(parsed.get("choice") or "").strip().lower()
            if choice in {"keep", "remove"}:
                return choice
    for payload in _hitl_payloads(state):
        if isinstance(payload, dict):
            pol = str(payload.get("unlisted_wells_policy") or "").strip().lower()
            if pol in {"keep", "remove"}:
                return pol
        blob = str(payload).lower()
        if (
            "unlisted_wells_policy=keep" in blob
            or re.search(r"остав|сохран", blob)
            or re.search(r"(^|\s)keep(\s|$)", blob)
        ) and "unlisted_wells_policy=remove" not in blob:
            return "keep"
        if "unlisted_wells_policy=remove" in blob or re.search(r"(^|\s)remove(\s|$)", blob):
            return "remove"
    return None


def _new_well_defs(state: dict[str, Any]) -> list[dict[str, Any]]:
    """New wells as engineering facts (``new_wells``: group, MD interval, control, rate, …).

    Schedule Builder renders WELSPECS / COMPDATMD / WCONPROD from these facts
    (``timeline_ops.compose_new_well_lines``).  Legacy ``new_well_defs`` with typed lines
    is still accepted for compatibility.
    """
    def rows_of(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        return [row for row in raw if isinstance(row, dict) and str(row.get("well") or row.get("entity") or "").strip()]

    # 1. The engineer's answer wins: these facts were given in reply to the agent's own question.
    for payload in _hitl_payloads(state):
        if isinstance(payload, dict):
            for key in ("new_wells", "new_well_defs"):
                rows = rows_of(payload.get(key))
                if rows:
                    return rows
        elif isinstance(payload, list):
            rows = rows_of(payload)
            if rows:
                return rows
    # 2. Excel Extractor already read a parameters table from the attached workbook
    #    (``extract_well_parameters`` → state.data.excel.new_wells). Engineers attach tables, not JSON.
    context = state.get("context") if isinstance(state.get("context"), dict) else {}
    rows = rows_of(excel_bucket(context).get("new_wells"))
    if rows:
        return rows
    # 3. Orchestrator inputs. The Decision LLM may echo a bare list of well names here
    #    (["N001", "N002"]) — that is not a definition and must not hide the engineer's facts.
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    for key in ("new_wells", "new_well_defs"):
        rows = rows_of(inputs.get(key))
        if rows:
            return rows
    return []


def _ops_from_model(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict) and (row.get("keyword") or row.get("operation"))]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return _ops_from_model(parsed)
    if isinstance(raw, dict):
        if raw.get("keyword") or raw.get("operation"):
            return [raw]
        keys = sorted(raw, key=lambda k: int(k) if str(k).isdigit() else str(k))
        out: list[dict[str, Any]] = []
        for key in keys:
            item = raw[key]
            if isinstance(item, dict) and (item.get("keyword") or item.get("operation")):
                out.append(item)
        return out
    return []


_MACHINE_TOKEN_RE = re.compile(r"[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]<>]|\w\|\w")


def human_text_problems(text: str) -> list[str]:
    """Why a string is not fit for an engineer: snake_case ids, key=value, JSON braces, a|b enums.

    Cyrillic prose with well names (1601, H_304R — uppercase) and keywords (WCONPROD) passes.
    """
    problems: list[str] = []
    stripped = str(text or "").strip()
    if len(stripped) < 12:
        problems.append("слишком коротко для вопроса инженеру")
    if not re.search(r"[А-Яа-яЁё]{3,}", stripped):
        problems.append("вопрос должен быть по-русски")
    tokens = sorted({m.group(0) for m in _MACHINE_TOKEN_RE.finditer(stripped)})
    if tokens:
        problems.append("машинные токены: " + ", ".join(tokens[:6]))
    return problems


def _options_for_human(raw: Any) -> list[dict[str, str]]:
    items = raw
    if isinstance(raw, str):
        parsed = _parse_jsonish(raw)
        items = parsed if isinstance(parsed, list) else [part.strip() for part in raw.split(";") if part.strip()]
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("text") or item.get("value") or "").strip()
            value = str(item.get("value") or label).strip()
            hint = str(item.get("hint") or "").strip()
        else:
            label = str(item).strip()
            value = label
            hint = ""
        if not label:
            continue
        row = {"value": value, "label": label}
        if hint:
            row["hint"] = hint
        out.append(row)
    return out[:8]


def _spec_incomplete(
    spec: dict[str, Any],
    missing: list[str],
    baseline: dict[str, Any],
    assumptions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Tool-level answer for the LLM (not a human question): what is missing and where to take it from."""
    where = {
        "wells": "имена скважин из текста задачи; проверь по inspect_schedule.wells",
        "parent_group": "имя новой/целевой группы из текста задачи (в кавычках у инженера)",
        "parent_of_parent": "родитель группы: корень baseline GRUPTREE (см. baseline.roots) или группа из задачи",
        "control": "тип группового контроля из задачи: газ → GRAT, нефть → ORAT, вода → WRAT, жидкость → LRAT",
        "gas_rate": "целевой дебит числом в м3/сут (например «200 тыс. м3 газа в сут.» → 200000)",
    }
    return {
        "ok": False,
        "error": "spec_incomplete",
        "missing": missing,
        "spec": spec,
        "assumptions": assumptions,
        "baseline": baseline,
        "controls": list(CONTROLS),
        "where_to_find": {key: where[key] for key in missing if key in where},
        "message": (
            "Спецификация перепривязки не полная. Заполни недостающие поля из текста задачи и inspect_schedule "
            "и вызови apply_group_rebind ещё раз. Если в задаче этих данных действительно нет — спроси инженера "
            "через ask_engineer одним вопросом по-русски (варианты групп — из baseline.groups)."
        ),
    }


def execute_tool(session_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    state = sessions.get(session_id)
    source = str(state.get("working_text") or state.get("source_text") or "")
    task_id = str(state.get("task_id") or "")
    blob = " ".join([str(state.get("objective") or ""), str(state.get("handoff_message") or "")])

    # Protocol guard: one stored result per session. Once apply_* / ask_engineer fixed a result
    # (completed or a question to the engineer), further result-producing calls would overwrite it
    # — e.g. the LLM re-wording a deterministic question. Tell the LLM to stop instead.
    stored = state.get("result") if isinstance(state.get("result"), dict) else None
    stored_status = str(stored.get("status") or "") if stored else ""
    blocked = bool(stored) and name in RESULT_TOOLS and (
        stored_status == "needs_input" or name in WHOLE_TASK_TOOLS
    )
    if blocked:
        return {
            "ok": False,
            "error": "result_already_stored",
            "status": str(stored.get("status") or ""),
            "message": (
                "Результат уже зафиксирован в сессии (" + str(stored.get("status") or "") + "): "
                + str(stored.get("message") or "")[:300]
                + " Больше инструменты не вызывай — заверши ответ одним предложением."
            ),
        }

    if name == "inspect_schedule":
        return {"ok": True, "inspect": _compact_inspect(source), "fact_count": len(state.get("facts") or [])}

    if name == "inspect_well":
        objects = build_well_objects(parse_schedule(source))
        well = find_well(objects, args.get("well"))
        if well is None:
            return {
                "ok": False,
                "error": "well_not_found",
                "well": str(args.get("well") or ""),
                "message": "Скважина не найдена в baseline SCHEDULE.",
            }
        return {"ok": True, "well": well}

    if name == "analyze_forecast_controls":
        objects = build_well_objects(parse_schedule(source))
        well = find_well(objects, args.get("well"))
        if well is None:
            return {
                "ok": False,
                "error": "well_not_found",
                "well": str(args.get("well") or ""),
                "message": "Скважина не найдена в baseline SCHEDULE.",
            }
        controls = well.get("control_events") or []
        factual = [event for event in controls if event.get("factual")]
        forecast = [
            event for event in controls
            if event.get("keyword") == "WCONPROD" and not event.get("factual")
        ]
        boundary_candidates = sorted({
            event.get("date")
            for event in controls
            if event.get("date") and event.get("keyword") in {"WCONHIST", "WCONPROD"}
        })
        explicit_forecast_comment = any(
            any(word in str(event.get("comment") or "").casefold() for word in ("forecast", "прогноз"))
            for event in forecast
        )
        return {
            "ok": True,
            "well": well.get("well"),
            "identity": well.get("identity"),
            "commissioning_anchor": well.get("commissioning_wconprod"),
            "latest_control": well.get("latest_control"),
            "factual_history": factual,
            "forecast_controls": forecast,
            "control_overrides": well.get("override_events") or [],
            "economic_limits": well.get("economic_events") or [],
            "reopen_policies": well.get("test_events") or [],
            "status_events": well.get("status_events") or [],
            "efficiency_events": well.get("efficiency_events") or [],
            "connection_multipliers": well.get("connection_events") or [],
            "forecast_boundary_candidates": boundary_candidates,
            "decision_rules": {
                "full_control_change": "WCONPROD",
                "single_value_change": "WELTARG",
                "economic_limit": "WECON",
                "reopen_policy": "WTEST",
                "well_status": "WELOPEN",
                "uptime_factor": "WEFAC",
                "connection_factor": "WPIMULT",
                "group_control": "GCONPROD",
            },
            "needs_input": (
                ["forecast/history boundary"]
                if factual and forecast and not explicit_forecast_comment
                else []
            ),
        }

    if name == "search_keywords":
        intent = str(args.get("intent") or blob)
        hits = search_keywords(intent)
        return {
            "ok": True,
            "intent": intent,
            "keywords": [
                {
                    "keyword": item["keyword"],
                    "description": item.get("description"),
                    "fields": [f.get("name") for f in item.get("fields") or []],
                    "methods": [m.get("name") if isinstance(m, dict) else m for m in item.get("methods") or []],
                }
                for item in hits[:20]
            ],
        }

    if name == "get_keyword":
        item = keyword_object(str(args.get("keyword") or ""))
        if item is None:
            return {"ok": False, "error": "unknown_keyword", "keyword": args.get("keyword")}
        return {"ok": True, "keyword": item}

    if name == "render_ir":
        from .schema_models import coerce_ir_events
        from .schema_renderer import validate_and_render
        from .schema_store import load_catalogue

        catalogue = args.get("schema_catalogue") if isinstance(args.get("schema_catalogue"), dict) else None
        raw_events = args.get("ir_events")
        events = raw_events if isinstance(raw_events, list) else coerce_ir_events(raw_events)
        result = validate_and_render(
            mode=str(args.get("mode") or "CREATE"),
            schema_catalogue=catalogue or load_catalogue(),
            ir_events=events,
        )
        return {"ok": result.get("status") == "rendered", **result}

    if name == "list_records":
        keyword = str(args.get("keyword") or "").strip().upper()
        well = str(args.get("well") or "").strip().strip("'\"")
        doc = parse_schedule(source)
        rows: list[dict[str, Any]] = []
        for block in doc.blocks:
            if keyword and block.keyword != keyword:
                continue
            for rec in block.records:
                rec_well = rec.tokens[0].strip("'\"") if rec.tokens else ""
                if well and rec_well != well:
                    continue
                rows.append({"keyword": block.keyword, "well": rec_well, "tokens": rec.tokens[:16]})
                if len(rows) >= MAX_RECORDS:
                    break
            if len(rows) >= MAX_RECORDS:
                break
        return {"ok": True, "count": len(rows), "truncated": len(rows) >= MAX_RECORDS, "records": rows}

    if name == "apply_commissioning":
        facts = list(state.get("facts") or [])
        if not facts:
            result = _agent_result(
                task_id,
                "needs_input",
                "Чтобы сдвинуть даты ввода, нужна таблица «скважина — дата ввода», а в задаче её нет.",
                issues=[{"type": "commissioning_facts_required"}],
                requests=[
                    {
                        "question_id": "Q-facts",
                        "question": (
                            "Чтобы сдвинуть даты ввода, нужна таблица «скважина — новая дата ввода». "
                            "Приложите Excel с этими колонками или перечислите пары «скважина — дата» текстом."
                        ),
                        "options": [],
                        "accepts": {"free_text": True, "files": ["xlsx"]},
                    }
                ],
                assumptions=[],
            )
            return _store_result(state, result)
        revised = run_commissioning_revise(
            source,
            facts,
            file_ref=str(state.get("file_ref") or "schedule.inc"),
            unlisted_wells_policy=_unlisted_policy(state),
            new_well_defs=_new_well_defs(state),
            instruction_blob=blob,
        )
        status = str(revised.get("status") or "")
        if status == "needs_input":
            questions = revised.get("questions") or []
            result = _agent_result(
                task_id,
                "needs_input",
                (
                    questions[0].get("question")
                    if questions and isinstance(questions[0], dict)
                    else "Нужно уточнение по скважинам вне Excel"
                ),
                data={"findings": revised.get("findings") or [], "unlisted_wells": revised.get("unlisted_wells") or []},
                issues=revised.get("findings") or [],
                requests=[
                    {**q, "question_id": q.get("question_id") or q.get("id") or "Q-sched"}
                    if isinstance(q, dict)
                    else q
                    for q in (questions if isinstance(questions, list) else [])
                ],
            )
            return _store_result(state, result)
        text = str(revised.get("generated_schedule") or "")
        state["working_text"] = text or source
        ok = bool(text.strip()) and status in {"applied", "noop"}
        summary = summarize_commissioning_result(revised)
        result = _agent_result(
            task_id,
            "completed" if ok else "failed",
            summary["message"],
            data={
                "changed_keywords": summary["changed_keywords"],
                "summary_for_human": summary["message"],
                "wells_shifted": summary["wells_shifted"],
                "wells_added": summary["wells_added"],
                "wells_removed": summary["wells_removed"],
                "wells_kept_unlisted": summary["wells_kept_unlisted"],
                "findings": revised.get("findings") or [],
                "edits": revised.get("edits") or [],
                "shifts": revised.get("shifts") or [],
                "records_applied": len(revised.get("edits") or []),
            },
            artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
            issues=revised.get("findings") or [],
            assumptions=[{"units": UNITS, "unlisted_wells_policy": revised.get("unlisted_wells_policy") or "keep"}],
        )
        return _store_result(state, result)

    if name == "apply_group_rebind":
        # LLM-first: the spec is what the LLM passed (plus a structured inputs.group_rebind from the
        # task, if the orchestrator forwarded one). Nothing is read from prose here; an incomplete
        # spec goes back to the LLM as a tool error, never to the engineer as "Уточните <поле>".
        wells = well_names(parse_schedule(source))
        baseline = gruptree_summary(source)
        inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
        seed = inputs.get("group_rebind") if isinstance(inputs.get("group_rebind"), dict) else {}
        llm_spec = args.get("spec") if isinstance(args.get("spec"), dict) else args
        merged = {**seed, **{k: v for k, v in llm_spec.items() if v not in (None, "", [], {})}}
        spec, missing, assumptions = normalize_group_rebind_spec(merged, baseline=baseline)
        invented = [name for name in spec.get("wells") or [] if name not in wells]
        if invented:
            return {
                "ok": False,
                "error": "well_not_in_schedule",
                "wells": invented,
                "message": "Нельзя придумывать скважины. Берите имена из inspect_schedule.",
            }
        if missing:
            return _spec_incomplete(spec, missing, baseline, assumptions)
        revised = run_group_rebind_revise(source, spec, file_ref=str(state.get("file_ref") or "schedule.inc"))
        status = str(revised.get("status") or "")
        text = str(revised.get("generated_schedule") or "")
        if status == "needs_input" or not text.strip():
            findings = revised.get("findings") or []
            codes = [str(f.get("code") or "") for f in findings if isinstance(f, dict)]
            if "GROUP_REBIND_COMMISSIONING_DATE_MISSING" in codes:
                return {
                    "ok": False,
                    "error": "effective_date_unknown",
                    "spec": spec,
                    "findings": findings,
                    "message": (
                        "У этих скважин в baseline нет даты ввода, с которой начать групповой контроль. "
                        "Передай effective_at (дата в формате 1 JAN 2026) из задачи или спроси инженера через ask_engineer."
                    ),
                }
            return {
                "ok": False,
                "error": "group_rebind_not_applied",
                "spec": spec,
                "findings": findings,
                "message": "Перепривязка не применена к baseline; см. findings и исправь спецификацию.",
            }
        state["working_text"] = text
        count = len(spec["wells"])
        rate = spec["gas_rate"]
        rate_text = f"{int(rate)}" if float(rate).is_integer() else f"{rate}"
        summary = (
            f"Перепривязал {_plural_wells(count, 'acc')} ({_wells_phrase(spec['wells'])}) "
            f"в группу {spec['parent_group']} под {spec['parent_of_parent']}; "
            f"групповой контроль {spec['control']} {rate_text} м3/сут с даты ввода этих скважин."
        )
        result = _agent_result(
            task_id,
            "completed",
            summary,
            data={
                "changed_keywords": ["WELSPECS", "GRUPTREE", "GCONPROD"],
                "summary_for_human": summary,
                "findings": revised.get("findings") or [],
                "edits": revised.get("edits") or [],
                "group_rebind": spec,
            },
            artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
            issues=revised.get("findings") or [],
            assumptions=[{"units": UNITS}, *assumptions],
        )
        return _store_result(state, result)

    if name == "ask_engineer":
        # The only way the LLM asks a human. Prose in Russian, optional option buttons — never
        # field names, enums or JSON. Machine-looking text is bounced back to the LLM to rephrase.
        question = str(args.get("question") or "").strip()
        problems = human_text_problems(question)
        options = _options_for_human(args.get("options"))
        for opt in options:
            problems.extend(f"вариант «{opt['label']}»: {p}" for p in human_text_problems(opt["label"]) if "коротко" not in p)
        if problems:
            return {
                "ok": False,
                "error": "question_not_human",
                "problems": problems,
                "message": (
                    "Переформулируй вопрос для инженера: обычная русская фраза, что именно нужно и зачем, "
                    "варианты — как их называет инженер (имена групп, «оставить»/«убрать»), без имён полей и JSON."
                ),
            }
        question_id = re.sub(r"[^a-z0-9_]+", "_", str(args.get("topic") or "engineer").strip().lower()).strip("_") or "engineer"
        accepts: dict[str, Any] = {"free_text": True}
        files = args.get("accepts_files")
        if isinstance(files, str) and files.strip():
            accepts["files"] = [part.strip() for part in files.split(",") if part.strip()]
        elif isinstance(files, list) and files:
            accepts["files"] = [str(part).strip() for part in files if str(part).strip()]
        result = _agent_result(
            task_id,
            "needs_input",
            question,
            data={"asked_by": "schedule_builder_llm"},
            issues=[{"type": "engineer_input_required", "topic": question_id}],
            requests=[
                {
                    "question_id": f"Q-{question_id}",
                    "question": question,
                    "required": True,
                    "type": "choice" if options else "text",
                    "options": options,
                    "accepts": accepts,
                }
            ],
            assumptions=[],
        )
        return _store_result(state, result)

    if name == "apply_operations":
        operations = _ops_from_model(args.get("operations"))
        if not operations:
            return {
                "ok": False,
                "error": "operations_required",
                "message": (
                    "Передай operations JSON-массивом [{\"keyword\":\"WCONPROD\",\"operation\":\"MODIFY\",\"fields\":{...}}]. "
                    "Это ошибка вызова инструмента, инженера спрашивать не нужно."
                ),
            }
        wells = well_names(parse_schedule(source))
        bad = []
        for op in operations:
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
            well = str(fields.get("well") or op.get("well") or "").strip().strip("'\"")
            if well and well not in wells:
                bad.append(well)
        if bad:
            return {"ok": False, "error": "well_not_in_schedule", "wells": bad}
        built_doc = parse_schedule(source)
        semantic_findings: list[dict[str, Any]] = []
        for op in operations:
            keyword = str(op.get("keyword") or "").upper()
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
            well_name = str(fields.get("well") or op.get("well") or "").strip().strip("'\"")
            if keyword == "WCONPROD":
                factual = any(
                    block.keyword == "WCONPROD"
                    and any(
                        rec.tokens
                        and rec.tokens[0].strip("'\"") == well_name
                        and ("факт" in str(rec.comment).casefold() or "fact" in str(rec.comment).casefold())
                        for rec in block.records
                    )
                    for block in built_doc.blocks
                )
                if factual:
                    semantic_findings.append({
                        "code": "FACTUAL_WCONPROD_PROTECTED",
                        "keyword": keyword,
                        "well": well_name,
                        "severity": "error",
                        "message": "Фактический WCONPROD нельзя изменять generic operation.",
                    })
            if keyword == "WELTARG":
                has_base = any(
                    block.keyword in {"WCONPROD", "WCONHIST"}
                    and any(rec.tokens and rec.tokens[0].strip("'\"") == well_name for rec in block.records)
                    for block in built_doc.blocks
                )
                if not has_base:
                    semantic_findings.append({
                        "code": "WELTARG_BASE_CONTROL_MISSING",
                        "keyword": keyword,
                        "well": well_name,
                        "severity": "error",
                        "message": "WELTARG требует предшествующий WCONPROD или WCONHIST.",
                    })
        if semantic_findings:
            result = _agent_result(
                task_id,
                "failed",
                "Операция нарушает семантику SCHEDULE",
                data={"findings": semantic_findings},
                issues=semantic_findings,
                artifacts={"schedule_out": source, "diff": ""},
            )
            return _store_result(state, result)
        applied, findings = apply_operations(built_doc, operations)
        text = emit_schedule(applied)
        findings.extend(validate_emitted(text, applied))
        hard = [f for f in findings if f.get("severity") == "error"]
        state["working_text"] = text
        result = _agent_result(
            task_id,
            "completed" if not hard else "failed",
            "SCHEDULE обновлён" if not hard else "Ошибка сборки SCHEDULE",
            data={
                "changed_keywords": sorted(
                    {str(op.get("keyword") or "").upper() for op in operations if op.get("keyword")}
                ),
                "findings": findings,
                "records_applied": len(operations),
            },
            artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
            issues=findings,
        )
        return _store_result(state, result)

    if name == "build_schedule":
        text = source
        findings = validate_emitted(text, parse_schedule(text))
        hard = [f for f in findings if f.get("severity") == "error"]
        result = _agent_result(
            task_id,
            "completed" if not hard else "failed",
            "SCHEDULE собран" if not hard else "Ошибка валидации SCHEDULE",
            data={"findings": findings, "changed_keywords": []},
            artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
            issues=findings,
        )
        return _store_result(state, result)

    if name == "validate_result":
        text = str(state.get("working_text") or "")
        findings = validate_emitted(text, parse_schedule(text))
        return {"ok": True, "findings": findings, "error_count": len([f for f in findings if f.get("severity") == "error"])}

    raise KeyError(name)
