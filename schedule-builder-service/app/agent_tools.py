"""Deterministic tools for the n8n Schedule Builder LLM. Never invent wells or dates."""

from __future__ import annotations

import json
import re
from typing import Any

from .apply import apply_operations
from .commissioning import run_commissioning_revise
from .diff import unified_diff
from .emit import emit_schedule
from .group_rebind import extract_group_rebind_spec, run_group_rebind_revise, wants_group_rebind
from .io import bind_case_packet, commissioning_facts, file_ref, load_source
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
        "suggested_capability": suggested_capability(state),
    }


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
        "Агент не применил изменений. Уточните задачу.",
        issues=[{"type": "no_apply"}],
        requests=[
            {
                "question_id": "Q-apply",
                "question": "Агент не применил изменений к SCHEDULE. Уточните, что сделать со скважинами и keywords.",
                "options": [],
            }
        ],
    )


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
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    for key in ("new_wells", "new_well_defs"):
        raw = inputs.get(key)
        if isinstance(raw, list) and raw:
            return [row for row in raw if isinstance(row, dict)]
    for payload in _hitl_payloads(state):
        if isinstance(payload, dict):
            for key in ("new_wells", "new_well_defs"):
                if isinstance(payload.get(key), list):
                    rows = [row for row in payload[key] if isinstance(row, dict)]
                    if rows:
                        return rows
        if isinstance(payload, list):
            rows = [row for row in payload if isinstance(row, dict) and row.get("well")]
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


def _has_gruptree(source: str) -> bool:
    present = _compact_inspect(source).get("keywords_present") or []
    return "GRUPTREE" in present


def _gruptree_parent_options(source: str) -> list[str]:
    options: list[str] = []
    for row in _compact_inspect(source).get("gruptree_preview") or []:
        if not isinstance(row, dict):
            continue
        for key in ("parent", "child"):
            name = str(row.get(key) or "").strip()
            if name and name not in options:
                options.append(name)
    return options[:12]


def execute_tool(session_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    state = sessions.get(session_id)
    source = str(state.get("working_text") or state.get("source_text") or "")
    task_id = str(state.get("task_id") or "")
    blob = " ".join([str(state.get("objective") or ""), str(state.get("handoff_message") or "")])

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
                "Для сдвига дат ввода нужны факты скважина + дата из Excel",
                issues=[{"type": "commissioning_facts_required"}],
                requests=[
                    {
                        "question_id": "Q-facts",
                        "question": "Приложите Excel со скважинами и датами ввода",
                        "options": [],
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
        wells = well_names(parse_schedule(source))
        spec, missing = extract_group_rebind_spec(
            blob,
            wells,
            source_text=source,
            hitl=(state.get("context") or {}).get("hitl")
            if isinstance((state.get("context") or {}).get("hitl"), dict)
            else {},
            inputs=state.get("inputs") or {},
        )
        override = args.get("spec") if isinstance(args.get("spec"), dict) else args
        for key in ("parent_group", "parent_of_parent", "control", "gas_rate", "oil_rate"):
            if override.get(key) not in (None, ""):
                spec[key] = override[key]
        if override.get("wells"):
            raw_wells = override.get("wells")
            if isinstance(raw_wells, str):
                spec["wells"] = [item.strip() for item in raw_wells.replace(",", " ").split() if item.strip()]
            elif isinstance(raw_wells, list):
                spec["wells"] = [str(item).strip() for item in raw_wells if str(item).strip()]
        invented = [name for name in spec.get("wells") or [] if name not in wells]
        if invented:
            return {
                "ok": False,
                "error": "well_not_in_schedule",
                "wells": invented,
                "message": "Нельзя придумывать скважины. Берите имена из inspect_schedule.",
            }
        if not spec.get("parent_group"):
            has_tree = _has_gruptree(source)
            options = _gruptree_parent_options(source) or ["GNEW", "GINJ", "GPROD"]
            result = _agent_result(
                task_id,
                "needs_input",
                (
                    "Baseline не содержит GRUPTREE. Укажите родительскую группу для перепривязки."
                    if not has_tree
                    else "Укажите родительскую группу для перепривязки."
                ),
                data={"group_rebind": spec, "missing": missing or ["parent_group"]},
                issues=[{"type": "GROUP_REBIND_SPEC_REQUIRED", "missing": missing or ["parent_group"]}],
                requests=[
                    {
                        "question_id": "Q-parent-group",
                        "question": (
                            "Baseline не содержит GRUPTREE. Укажите родительскую группу для перепривязки."
                            if not has_tree
                            else "Укажите родительскую группу для перепривязки."
                        ),
                        "options": options,
                    }
                ],
            )
            return _store_result(state, result)
        if missing:
            result = _agent_result(
                task_id,
                "needs_input",
                "Для перепривязки групп нужны: " + ", ".join(missing),
                data={"group_rebind": spec, "missing": missing},
                issues=[{"type": "GROUP_REBIND_SPEC_REQUIRED", "missing": missing}],
                requests=[
                    {
                        "question_id": item,
                        "question": f"Уточните {item} для перепривязки групп.",
                        "options": [],
                    }
                    for item in missing
                ],
            )
            return _store_result(state, result)
        revised = run_group_rebind_revise(source, spec, file_ref=str(state.get("file_ref") or "schedule.inc"))
        status = str(revised.get("status") or "")
        text = str(revised.get("generated_schedule") or "")
        if status == "needs_input" or not text.strip():
            result = _agent_result(
                task_id,
                "needs_input",
                "Перепривязка групп требует уточнения по baseline",
                data={"findings": revised.get("findings") or [], "group_rebind": spec},
                issues=revised.get("findings") or [],
                requests=[{"question_id": "group_rebind", "question": "Уточните spec перепривязки групп", "options": []}],
            )
            return _store_result(state, result)
        state["working_text"] = text
        result = _agent_result(
            task_id,
            "completed",
            f"Перепривязал {len(spec['wells'])} скважин в {spec['parent_group']} (GCONPROD {spec['control']} {spec['gas_rate']})",
            data={
                "changed_keywords": ["WELSPECS", "GRUPTREE", "GCONPROD"],
                "findings": revised.get("findings") or [],
                "edits": revised.get("edits") or [],
                "group_rebind": spec,
            },
            artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
            issues=revised.get("findings") or [],
        )
        return _store_result(state, result)

    if name == "apply_operations":
        operations = _ops_from_model(args.get("operations"))
        if not operations:
            result = _agent_result(
                task_id,
                "needs_input",
                "Нужен список operations: массив {keyword, operation, fields}.",
                issues=[{"type": "operations_required"}],
                requests=[
                    {
                        "question_id": "Q-operations",
                        "question": "Передайте operations массивом, например [{\"keyword\":\"WCONPROD\",\"operation\":\"MODIFY\",\"fields\":{...}}].",
                        "options": [],
                    }
                ],
            )
            return _store_result(state, result)
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


def suggested_capability(state: dict[str, Any]) -> str:
    blob = " ".join([str(state.get("objective") or ""), str(state.get("handoff_message") or "")])
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    if wants_group_rebind(blob, inputs):
        return "group_rebind"
    if state.get("facts"):
        return "commissioning"
    return "operations"
