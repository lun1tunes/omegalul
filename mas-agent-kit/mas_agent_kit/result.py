"""``agent_result`` — the one contract every agent returns to the orchestrator.

    {task_id, agent_id, status, message, data, artifacts, issues, assumptions, requests}

``status``: ``completed`` | ``needs_input`` (``requests`` holds the question) | ``failed`` |
``in_progress`` (long job; the agent later calls ``ActivityClient.finish_task``).
``message`` is prose for the engineer — what actually happened, with counts and names.
"""

from __future__ import annotations

from typing import Any

from .text import human_text_problems

STATUSES = ("completed", "needs_input", "failed", "in_progress")


def agent_result(
    agent_id: str,
    task_id: str,
    status: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    requests: list[Any] | None = None,
    assumptions: list[Any] | None = None,
    watch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"unknown agent_result status: {status!r}")
    out = {
        "task_id": str(task_id or ""),
        "agent_id": str(agent_id or ""),
        "status": status,
        "message": str(message or ""),
        "data": dict(data or {}),
        "artifacts": dict(artifacts or {}),
        "issues": list(issues or []),
        "assumptions": list(assumptions or []),
        "requests": list(requests or []),
    }
    if status == "in_progress":
        out["watch"] = dict(watch or {})
    return out


def in_progress(agent_id: str, task_id: str, message: str, *, watch: dict[str, Any] | None = None) -> dict[str, Any]:
    """Long job started: the orchestrator parks the case (``waiting_agent``) until ``ActivityClient.finish_task``.

    ``watch`` is free-form for the feed / a monitor: ``{"kind": "poll", "ref": "job-17", "poll_hint": "20s"}``.
    ``message`` tells the engineer what is running and roughly how long ("Расчёт запущен, около двух минут.").
    """
    return agent_result(agent_id, task_id, "in_progress", message, watch=watch)


def engineer_request(
    question: str,
    *,
    question_id: str = "Q-engineer",
    options: list[dict[str, str]] | None = None,
    accepts_files: list[str] | tuple[str, ...] | None = None,
    required: bool = True,
) -> dict[str, Any]:
    """One HITL request row. ``options`` are ``[{value, label}]`` buttons; free text is always allowed."""
    opts = list(options or [])
    accepts: dict[str, Any] = {"free_text": True}
    if accepts_files:
        accepts["files"] = [str(part).strip() for part in accepts_files if str(part).strip()]
    return {
        "question_id": question_id,
        "question": str(question),
        "required": bool(required),
        "type": "choice" if opts else "text",
        "options": opts,
        "accepts": accepts,
    }


def needs_input(
    agent_id: str,
    task_id: str,
    question: str,
    *,
    question_id: str = "Q-engineer",
    options: list[dict[str, str]] | None = None,
    accepts_files: list[str] | tuple[str, ...] | None = None,
    issue_type: str = "engineer_input_required",
    data: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shortcut: a ``needs_input`` result carrying exactly one question."""
    return agent_result(
        agent_id,
        task_id,
        "needs_input",
        question,
        data=data,
        artifacts=artifacts,
        issues=[{"type": issue_type, "topic": question_id}],
        requests=[engineer_request(question, question_id=question_id, options=options, accepts_files=accepts_files)],
    )


def result_text_problems(result: dict[str, Any]) -> list[str]:
    """Audit of the human-facing strings of a result (``message``, questions, option labels)."""
    problems = [f"message: {p}" for p in human_text_problems(result.get("message", ""), min_length=1)]
    for req in result.get("requests") or []:
        if not isinstance(req, dict):
            continue
        problems += [f"question: {p}" for p in human_text_problems(req.get("question", ""))]
        for opt in req.get("options") or []:
            label = opt.get("label", "") if isinstance(opt, dict) else str(opt)
            problems += [f"option «{label}»: {p}" for p in human_text_problems(label, min_length=1) if "по-русски" not in p]
    return problems
