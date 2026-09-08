"""LLM tools of the Schedule Builder agent — deterministic; never invent wells or dates.

Registered on ``agent.tools`` (``app/agent.py``); the n8n workflow calls them through
``POST /agent-tools/{name}``. Results are fixed with ``agent.store_result(state, agent.new_result(...))``.
Session, registry, error envelope and the engineer-question gate come from ``mas_agent_kit``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mas_agent_kit import (
    ToolContext,
    ToolError,
    engineer_answers,
    engineer_request_from_args,
    hitl_payloads,
    human_text_problems,  # noqa: F401  (re-exported: tests import it from here)
    list_preview,
    parse_jsonish,
    plural_ru,
)

from .agent import UNITS, agent, compact_inspect
from .apply import apply_operations
from .commissioning import run_commissioning_revise
from .diff import unified_diff
from .emit import emit_schedule
from .group_rebind import CONTROLS, gruptree_summary, normalize_group_rebind_spec, run_group_rebind_revise
from .io import excel_bucket
from .keywords import keyword_object, search_keywords
from .parse import parse_schedule, well_names
from .validate import validate_emitted
from .well_model import build_well_objects, find_well

MAX_RECORDS = 40

# Tools that fix a result in the session. Whole-task tools produce the final answer for the run;
# apply_operations / build_schedule may legitimately chain after a completed point edit.
RESULT_TOOLS = {"apply_commissioning", "apply_group_rebind", "ask_engineer", "apply_operations", "build_schedule"}
WHOLE_TASK_TOOLS = {"apply_commissioning", "apply_group_rebind", "ask_engineer"}


def _engineer_answers(state: dict[str, Any]) -> list[dict[str, Any]]:
    return engineer_answers(state.get("context"))


def _hitl_payloads(state: dict[str, Any]) -> list[Any]:
    return hitl_payloads(state.get("context"))


def _parse_jsonish(value: Any) -> Any:
    return parse_jsonish(value)


def _wells_phrase(wells: list[str], limit: int = 6) -> str:
    return list_preview(list(wells), limit)


def _plural_wells(count: int, case: str = "gen") -> str:
    """Russian agreement for 'скважина': gen — '(даты) 1 скважины / 3 скважин', acc — 'добавил 1 скважину / 3 скважины / 5 скважин'."""
    if case == "acc":
        return plural_ru(count, "скважину", "скважины", "скважин")
    if case == "nom":
        return plural_ru(count, "скважина", "скважины", "скважин")
    return plural_ru(count, "скважины", "скважин", "скважин")


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
    #    (``extract_well_parameters`` → ``new_wells`` in that agent's slot of ``state.agents``). Engineers attach tables, not JSON.
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


def _spec_incomplete(
    spec: dict[str, Any],
    missing: list[str],
    baseline: dict[str, Any],
    assumptions: list[dict[str, Any]],
) -> ToolError:
    """Tool-level answer for the LLM (not a human question): what is missing and where to take it from."""
    where = {
        "wells": "имена скважин из текста задачи; проверь по inspect_schedule.wells",
        "parent_group": "имя новой/целевой группы из текста задачи (в кавычках у инженера)",
        "parent_of_parent": "родитель группы: корень baseline GRUPTREE (см. baseline.roots) или группа из задачи",
        "control": "тип группового контроля из задачи: газ → GRAT, нефть → ORAT, вода → WRAT, жидкость → LRAT",
        "gas_rate": "целевой дебит числом в м3/сут (например «200 тыс. м3 газа в сут.» → 200000)",
    }
    return ToolError(
        "spec_incomplete",
        (
            "Спецификация перепривязки не полная. Заполни недостающие поля из текста задачи и inspect_schedule "
            "и вызови apply_group_rebind ещё раз. Если в задаче этих данных действительно нет — спроси инженера "
            "через ask_engineer одним вопросом по-русски (варианты групп — из baseline.groups)."
        ),
        missing=missing,
        spec=spec,
        assumptions=assumptions,
        baseline=baseline,
        controls=list(CONTROLS),
        where_to_find={key: where[key] for key in missing if key in where},
    )


# --------------------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------------------

tool = agent.tools.tool


def _source(state: dict[str, Any]) -> str:
    return str(state.get("working_text") or state.get("source_text") or "")


def _instruction_blob(state: dict[str, Any]) -> str:
    return " ".join([str(state.get("objective") or ""), str(state.get("handoff_message") or "")])


def _guard_result(state: dict[str, Any], name: str) -> None:
    """Protocol guard: one stored result per session.

    Once apply_* / ask_engineer fixed a result (completed or a question to the engineer), further
    result-producing calls would overwrite it — e.g. the LLM re-wording a deterministic question.
    apply_operations / build_schedule may still chain after a *completed* point edit.
    """
    if name not in RESULT_TOOLS:
        return
    agent.tools.result_guard(state, when=lambda stored: str(stored.get("status") or "") == "needs_input" or name in WHOLE_TASK_TOOLS)


def _well_or_error(source: str, name: Any) -> dict[str, Any]:
    well = find_well(build_well_objects(parse_schedule(source)), name)
    if well is None:
        raise ToolError("well_not_found", "Скважина не найдена в baseline SCHEDULE.", well=str(name or ""))
    return well


@tool("inspect_schedule", "Объектная инвентаризация baseline без полного .INC.")
def inspect_schedule(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return {"inspect": compact_inspect(_source(ctx.state)), "fact_count": len(ctx.state.get("facts") or [])}


@tool("inspect_well", "Подробно осмотреть одну скважину.", {"well": {"type": "string"}}, required=["well"])
def inspect_well(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return {"well": _well_or_error(_source(ctx.state), args.get("well"))}


@tool("analyze_forecast_controls", "Разобрать timeline режимов одной скважины.", {"well": {"type": "string"}}, required=["well"])
def analyze_forecast_controls(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    well = _well_or_error(_source(ctx.state), args.get("well"))
    controls = well.get("control_events") or []
    factual = [event for event in controls if event.get("factual")]
    forecast = [event for event in controls if event.get("keyword") == "WCONPROD" and not event.get("factual")]
    boundary_candidates = sorted({
        event.get("date") for event in controls if event.get("date") and event.get("keyword") in {"WCONHIST", "WCONPROD"}
    })
    explicit_forecast_comment = any(
        any(word in str(event.get("comment") or "").casefold() for word in ("forecast", "прогноз")) for event in forecast
    )
    return {
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
        "needs_input": ["forecast/history boundary"] if factual and forecast and not explicit_forecast_comment else [],
    }


@tool("search_keywords", "Найти keywords по intent.", {"intent": {"type": "string"}})
def search_keywords_tool(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    intent = str(args.get("intent") or _instruction_blob(ctx.state))
    hits = search_keywords(intent)
    return {
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


@tool("get_keyword", "Объект keyword с параметрами.", {"keyword": {"type": "string"}}, required=["keyword"])
def get_keyword(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    item = keyword_object(str(args.get("keyword") or ""))
    if item is None:
        raise ToolError("unknown_keyword", "Такого keyword нет в каталоге; имена — из search_keywords.", keyword=args.get("keyword"))
    return {"keyword": item}


@tool(
    "render_ir",
    "Собрать текст keyword по schema_catalogue из IR-событий.",
    {"mode": {"type": "string"}, "schema_catalogue": {"type": "object"}, "ir_events": {"type": ["array", "object", "string"]}},
)
def render_ir(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from .schema_models import coerce_ir_events
    from .schema_renderer import validate_and_render
    from .schema_store import load_catalogue

    catalogue = args.get("schema_catalogue") if isinstance(args.get("schema_catalogue"), dict) else None
    raw_events = args.get("ir_events")
    events = raw_events if isinstance(raw_events, list) else coerce_ir_events(raw_events)
    result = validate_and_render(mode=str(args.get("mode") or "CREATE"), schema_catalogue=catalogue or load_catalogue(), ir_events=events)
    return {**result, "ok": result.get("status") == "rendered"}


@tool("list_records", "Компактные records keyword (не больше 40).", {"keyword": {"type": "string"}, "well": {"type": "string"}})
def list_records(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    keyword = str(args.get("keyword") or "").strip().upper()
    well = str(args.get("well") or "").strip().strip("'\"")
    rows: list[dict[str, Any]] = []
    for block in parse_schedule(_source(ctx.state)).blocks:
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
    return {"count": len(rows), "truncated": len(rows) >= MAX_RECORDS, "records": rows}


@tool("apply_commissioning", "Сдвинуть даты ввода по фактам «скважина — дата» из сессии.")
def apply_commissioning(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    state = ctx.state
    _guard_result(state, "apply_commissioning")
    source = _source(state)
    facts = list(state.get("facts") or [])
    if not facts:
        result = agent.new_result(state,
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
        return agent.store_result(state, result)
    revised = run_commissioning_revise(
        source,
        facts,
        file_ref=str(state.get("file_ref") or "schedule.inc"),
        unlisted_wells_policy=_unlisted_policy(state),
        new_well_defs=_new_well_defs(state),
        instruction_blob=_instruction_blob(state),
    )
    status = str(revised.get("status") or "")
    if status == "needs_input":
        questions = revised.get("questions") or []
        result = agent.new_result(state,
            "needs_input",
            questions[0].get("question") if questions and isinstance(questions[0], dict) else "Нужно уточнение по скважинам вне Excel",
            data={"findings": revised.get("findings") or [], "unlisted_wells": revised.get("unlisted_wells") or []},
            issues=revised.get("findings") or [],
            requests=[
                {**q, "question_id": q.get("question_id") or q.get("id") or "Q-sched"} if isinstance(q, dict) else q
                for q in (questions if isinstance(questions, list) else [])
            ],
        )
        return agent.store_result(state, result)
    text = str(revised.get("generated_schedule") or "")
    state["working_text"] = text or source
    ok = bool(text.strip()) and status in {"applied", "noop"}
    summary = summarize_commissioning_result(revised)
    result = agent.new_result(state,
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
    return agent.store_result(state, result)


@tool(
    "apply_group_rebind",
    "Поместить скважины в группу с групповым контролем по spec от LLM.",
    {
        "spec": {"type": ["object", "string"]},
        "wells": {"type": ["array", "string"]},
        "parent_group": {"type": "string"},
        "parent_of_parent": {"type": "string"},
        "control": {"type": "string"},
        "gas_rate": {"type": ["number", "string"]},
        "effective_at": {"type": "string"},
    },
)
def apply_group_rebind(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    # LLM-first: the spec is what the LLM passed (plus a structured inputs.group_rebind from the
    # task, if the orchestrator forwarded one). Nothing is read from prose here; an incomplete
    # spec goes back to the LLM as a tool error, never to the engineer as "Уточните <поле>".
    state = ctx.state
    _guard_result(state, "apply_group_rebind")
    source = _source(state)
    wells = well_names(parse_schedule(source))
    baseline = gruptree_summary(source)
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    seed = inputs.get("group_rebind") if isinstance(inputs.get("group_rebind"), dict) else {}
    llm_spec = args.get("spec") if isinstance(args.get("spec"), dict) else args
    merged = {**seed, **{k: v for k, v in llm_spec.items() if v not in (None, "", [], {})}}
    spec, missing, assumptions = normalize_group_rebind_spec(merged, baseline=baseline)
    invented = [name for name in spec.get("wells") or [] if name not in wells]
    if invented:
        raise ToolError("well_not_in_schedule", "Нельзя придумывать скважины. Берите имена из inspect_schedule.", wells=invented)
    if missing:
        raise _spec_incomplete(spec, missing, baseline, assumptions)
    revised = run_group_rebind_revise(source, spec, file_ref=str(state.get("file_ref") or "schedule.inc"))
    status = str(revised.get("status") or "")
    text = str(revised.get("generated_schedule") or "")
    if status == "needs_input" or not text.strip():
        findings = revised.get("findings") or []
        codes = [str(f.get("code") or "") for f in findings if isinstance(f, dict)]
        if "GROUP_REBIND_COMMISSIONING_DATE_MISSING" in codes:
            raise ToolError(
                "effective_date_unknown",
                "У этих скважин в baseline нет даты ввода, с которой начать групповой контроль. "
                "Передай effective_at (дата в формате 1 JAN 2026) из задачи или спроси инженера через ask_engineer.",
                spec=spec,
                findings=findings,
            )
        raise ToolError("group_rebind_not_applied", "Перепривязка не применена к baseline; см. findings и исправь спецификацию.", spec=spec, findings=findings)
    state["working_text"] = text
    count = len(spec["wells"])
    rate = spec["gas_rate"]
    rate_text = f"{int(rate)}" if float(rate).is_integer() else f"{rate}"
    summary = (
        f"Перепривязал {_plural_wells(count, 'acc')} ({_wells_phrase(spec['wells'])}) "
        f"в группу {spec['parent_group']} под {spec['parent_of_parent']}; "
        f"групповой контроль {spec['control']} {rate_text} м3/сут с даты ввода этих скважин."
    )
    result = agent.new_result(state,
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
    return agent.store_result(state, result)


@tool(
    "ask_engineer",
    "Задать инженеру один вопрос по-русски.",
    {"question": {"type": "string"}, "options": {"type": ["array", "string"]}, "topic": {"type": "string"}, "accepts_files": {"type": ["array", "string"]}},
    required=["question"],
)
def ask_engineer(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    # The only way the LLM asks a human. Prose in Russian, optional option buttons — never
    # field names, enums or JSON. Machine-looking text is bounced back to the LLM to rephrase.
    state = ctx.state
    _guard_result(state, "ask_engineer")
    request = engineer_request_from_args(args, default_topic="engineer", default_files=())
    result = agent.new_result(state,
        "needs_input",
        request["question"],
        data={"asked_by": "schedule_builder_llm"},
        issues=[{"type": "engineer_input_required", "topic": request["question_id"][2:]}],
        requests=[request],
        assumptions=[],
    )
    return agent.store_result(state, result)


@tool("apply_operations", "Применить operations [{keyword, operation, fields}].", {"operations": {"type": ["array", "object", "string"]}}, required=["operations"])
def apply_operations_tool(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    state = ctx.state
    _guard_result(state, "apply_operations")
    source = _source(state)
    operations = _ops_from_model(args.get("operations"))
    if not operations:
        raise ToolError(
            "operations_required",
            "Передай operations JSON-массивом [{\"keyword\":\"WCONPROD\",\"operation\":\"MODIFY\",\"fields\":{...}}]. "
            "Это ошибка вызова инструмента, инженера спрашивать не нужно.",
        )
    wells = well_names(parse_schedule(source))
    bad = []
    for op in operations:
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        well = str(fields.get("well") or op.get("well") or "").strip().strip("'\"")
        if well and well not in wells:
            bad.append(well)
    if bad:
        raise ToolError("well_not_in_schedule", "Нельзя придумывать скважины. Берите имена из inspect_schedule.", wells=bad)
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
        result = agent.new_result(state,
            "failed",
            "Операция нарушает семантику SCHEDULE",
            data={"findings": semantic_findings},
            issues=semantic_findings,
            artifacts={"schedule_out": source, "diff": ""},
        )
        return agent.store_result(state, result)
    applied, findings = apply_operations(built_doc, operations)
    text = emit_schedule(applied)
    findings.extend(validate_emitted(text, applied))
    hard = [f for f in findings if f.get("severity") == "error"]
    state["working_text"] = text
    result = agent.new_result(state,
        "completed" if not hard else "failed",
        "SCHEDULE обновлён" if not hard else "Ошибка сборки SCHEDULE",
        data={
            "changed_keywords": sorted({str(op.get("keyword") or "").upper() for op in operations if op.get("keyword")}),
            "findings": findings,
            "records_applied": len(operations),
        },
        artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
        issues=findings,
    )
    return agent.store_result(state, result)


@tool("build_schedule", "Собрать текущий working SCHEDULE.")
def build_schedule(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    state = ctx.state
    _guard_result(state, "build_schedule")
    text = _source(state)
    findings = validate_emitted(text, parse_schedule(text))
    hard = [f for f in findings if f.get("severity") == "error"]
    result = agent.new_result(state,
        "completed" if not hard else "failed",
        "SCHEDULE собран" if not hard else "Ошибка валидации SCHEDULE",
        data={"findings": findings, "changed_keywords": []},
        artifacts={"schedule_out": text, "diff": unified_diff(str(state.get("source_text") or ""), text)},
        issues=findings,
    )
    return agent.store_result(state, result)


@tool("validate_result", "Проверить текущий working text.")
def validate_result(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    text = str(ctx.state.get("working_text") or "")
    findings = validate_emitted(text, parse_schedule(text))
    return {"findings": findings, "error_count": len([f for f in findings if f.get("severity") == "error"])}
