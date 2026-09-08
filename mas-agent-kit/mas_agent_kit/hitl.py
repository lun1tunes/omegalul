"""What the engineer already answered, and how an agent asks one more question.

The orchestrator passes HITL answers in ``agent_task.context.hitl.answers`` as
``{question_id: {"choice": ..., "label": ..., "text": ...} | "<free text>"}`` (values may arrive as
JSON strings). Agents show the LLM a compact view (``engineer_answers``) so it does not re-ask,
and read facts from them (``hitl_payloads``).
"""

from __future__ import annotations

from typing import Any

from .errors import ToolError
from .result import engineer_request
from .text import human_text_problems, option_problems, options_for_human, parse_jsonish, slug

FACT_LIST_KEYS = ("new_wells", "new_well_defs")


def _answers(task_or_context: dict[str, Any] | None) -> dict[str, Any]:
    node = task_or_context if isinstance(task_or_context, dict) else {}
    context = node.get("context") if isinstance(node.get("context"), dict) else node
    hitl = context.get("hitl") if isinstance(context.get("hitl"), dict) else {}
    answers = hitl.get("answers") if isinstance(hitl.get("answers"), dict) else {}
    return answers


def engineer_answers(task_or_context: dict[str, Any] | None, *, limit: int = 12) -> list[dict[str, Any]]:
    """Compact view for the LLM: question id, chosen option, text — never raw JSON blobs."""
    out: list[dict[str, Any]] = []
    for key, value in _answers(task_or_context).items():
        parsed = value if isinstance(value, dict) else parse_jsonish(value)
        row: dict[str, Any] = {"question_id": str(key)}
        if isinstance(parsed, dict):
            for field in ("choice", "label"):
                if str(parsed.get(field) or "").strip():
                    row[field] = str(parsed[field]).strip()
            text = str(parsed.get("text") or parsed.get("answer") or "").strip()
            if text:
                row["text"] = text[:400]
            for facts_key in FACT_LIST_KEYS:
                if isinstance(parsed.get(facts_key), list):
                    row["attached_facts"] = f"{facts_key}: {len(parsed[facts_key])} записей"
            if parsed.get("unlisted_wells_policy"):
                row["unlisted_wells_policy"] = str(parsed["unlisted_wells_policy"])
        elif isinstance(value, str) and value.strip():
            row["text"] = value.strip()[:400]
        else:
            continue
        out.append(row)
    return out[:limit]


def hitl_payloads(task_or_context: dict[str, Any] | None) -> list[Any]:
    """Parsed answers in order: dicts / lists as given, plain strings as strings."""
    out: list[Any] = []
    for value in _answers(task_or_context).values():
        parsed = parse_jsonish(value) if not isinstance(value, dict) else value
        if parsed is not None:
            out.append(parsed)
        elif isinstance(value, str) and value.strip():
            out.append(value)
    return out


def engineer_request_from_args(
    args: dict[str, Any],
    *,
    default_topic: str,
    default_files: list[str] | tuple[str, ...] = ("xlsx",),
    question_prefix: str = "Q-",
) -> dict[str, Any]:
    """Validate an ``ask_engineer`` tool call and build the HITL request row.

    ``args``: ``question`` (Russian prose), optional ``options`` (``"a; b"`` or list), ``topic``
    (latin slug), ``accepts_files`` (``"xlsx, .inc"`` or list). Machine-looking text raises
    ``ToolError("question_not_human")`` back to the LLM — the engineer never sees it.
    """
    question = str(args.get("question") or "").strip()
    options = options_for_human(args.get("options"))
    problems = human_text_problems(question) + option_problems(options)
    if problems:
        raise ToolError(
            "question_not_human",
            "Переформулируй вопрос для инженера: обычная русская фраза, что именно нужно и зачем; "
            "варианты — как их называет инженер (имена групп, листов, колонок; «оставить»/«убрать»), без имён полей, JSON и enum.",
            problems=problems,
        )
    files_raw = args.get("accepts_files")
    if isinstance(files_raw, str) and files_raw.strip():
        files = [part.strip() for part in files_raw.split(",") if part.strip()]
    elif isinstance(files_raw, list) and files_raw:
        files = [str(part).strip() for part in files_raw if str(part).strip()]
    else:
        files = list(default_files)
    topic = slug(args.get("topic"), default_topic)
    return engineer_request(question, question_id=f"{question_prefix}{topic}", options=options, accepts_files=files)
