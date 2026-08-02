"""Explicit session-state tools for the external n8n/MAS agent loop."""
from __future__ import annotations

from typing import Any

from .tools import ToolError, tool


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}}


@tool(
    _schema(
        "get_session_state",
        "Returns the safe current session state: table metadata, results, plan and clarifications. It never exposes server paths.",
        {"include_tables": {"type": "boolean", "default": True}, "include_results": {"type": "boolean", "default": True}, "include_clarifications": {"type": "boolean", "default": True}},
    )
)
def get_session_state(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    safe_state: dict[str, Any] = {key: state.get(key) for key in ("session_id", "file_name", "file_hash", "file_size", "status", "workbook_meta", "plan", "assumptions", "warnings", "final_output")}
    if args.get("include_tables", True):
        safe_state["tables"] = state.get("tables", {})
    if args.get("include_results", True):
        safe_state["result_sets"] = {key: {item_key: item_value for item_key, item_value in value.items() if item_key != "path"} for key, value in state.get("result_sets", {}).items()}
    if args.get("include_clarifications", True):
        safe_state["clarifications"] = state.get("clarifications", {})
    return safe_state


@tool(
    _schema(
        "save_agent_plan",
        "Saves external agent's selected tables, field mapping, filters and assumptions for recovery/observability.",
        {"plan": {"type": "string", "minLength": 1}, "status": {"type": "string", "enum": ["planning", "executing", "clarifying", "finalizing"]}, "selected_table_ids": {"type": "array", "items": {"type": "string"}}, "field_mapping": {"type": "object", "additionalProperties": {"type": "string"}}, "filters": {"type": "array", "items": {"type": "object"}}, "assumptions": {"type": "array", "items": {"type": "string"}}, "warnings": {"type": "array", "items": {"type": "string"}}},
        ["plan"],
    )
)
def save_agent_plan(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    plan = args.get("plan")
    if not isinstance(plan, str) or not plan.strip():
        raise ToolError("INVALID_ARGUMENTS", "plan must be a non-empty string")
    for field in ("selected_table_ids", "assumptions", "warnings"):
        if field in args and (not isinstance(args[field], list) or not all(isinstance(value, str) for value in args[field])):
            raise ToolError("INVALID_ARGUMENTS", f"{field} must be an array of strings")
    if "field_mapping" in args and not isinstance(args["field_mapping"], dict):
        raise ToolError("INVALID_ARGUMENTS", "field_mapping must be an object")
    if "filters" in args and not isinstance(args["filters"], list):
        raise ToolError("INVALID_ARGUMENTS", "filters must be an array")
    state["plan"] = {"plan": plan, "status": args.get("status", "planning"), "selected_table_ids": args.get("selected_table_ids", []), "field_mapping": args.get("field_mapping", {}), "filters": args.get("filters", [])}
    if "assumptions" in args:
        state["assumptions"] = args["assumptions"]
    if "warnings" in args:
        state["warnings"] = args["warnings"]
    return {"saved": True}


@tool(
    _schema(
        "resolve_clarification",
        "Persists the user's answers to a previously submitted clarification token.",
        {"token": {"type": "string", "minLength": 1}, "answers": {"type": "array", "items": {"type": "object"}}},
        ["token", "answers"],
    )
)
def resolve_clarification(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    token = args.get("token")
    answers = args.get("answers")
    clarification = state.get("clarifications", {}).get(token)
    if not clarification:
        raise ToolError("CLARIFICATION_NOT_FOUND", f"Clarification token {token} not found")
    if clarification.get("status") == "resolved":
        raise ToolError("CLARIFICATION_ALREADY_RESOLVED", f"Clarification token {token} is already resolved")
    if not isinstance(answers, list):
        raise ToolError("INVALID_ARGUMENTS", "answers must be an array")
    question_ids = {question["id"] for question in clarification["questions"]}
    supplied_ids: set[str] = set()
    for answer in answers:
        if not isinstance(answer, dict) or not isinstance(answer.get("question_id"), str) or "answer" not in answer:
            raise ToolError("INVALID_ARGUMENTS", "Every answer needs question_id and answer")
        if answer["question_id"] not in question_ids:
            raise ToolError("UNKNOWN_QUESTION", f"Question {answer['question_id']} does not belong to clarification", {"available_question_ids": sorted(question_ids)})
        supplied_ids.add(answer["question_id"])
    if supplied_ids != question_ids:
        raise ToolError("INCOMPLETE_CLARIFICATION", "Answers are required for every question", {"missing_question_ids": sorted(question_ids - supplied_ids)})
    clarification["answers"] = answers
    clarification["status"] = "resolved"
    state["status"] = "clarification_resolved"
    return {"token": token, "status": "resolved"}


def normalize_final_output(args: dict[str, Any]) -> dict[str, Any]:
    data = dict(args.get("data") or {})
    for key, default in {"result_id": None, "artifact_ref": None, "columns": [], "records": [], "row_count": 0, "returned_count": 0, "truncated": False, "provenance": []}.items():
        data.setdefault(key, default)
    return {"status": args["status"], "message": args.get("message"), "next_action": args.get("next_action", "none"), "data": data, "filters_applied": args.get("filters_applied", []), "field_mapping": args.get("field_mapping", {}), "assumptions": args.get("assumptions", []), "warnings": args.get("warnings", []), "errors": args.get("errors", []), "clarification": args.get("clarification"), "meta": args.get("meta", {})}


@tool(
    _schema(
        "finalize_extraction",
        "Required terminal call. Saves and returns the structured Excel extraction result for n8n to send to the user.",
        {"status": {"type": "string", "enum": ["success", "partial", "clarification_needed", "error"]}, "message": {"type": ["string", "null"]}, "next_action": {"type": "string", "enum": ["none", "ask_user", "retry_with_clarification", "download_artifact", "handle_error"], "default": "none"}, "data": {"type": "object"}, "filters_applied": {"type": "array", "items": {"type": "object"}}, "field_mapping": {"type": "object", "additionalProperties": {"type": "string"}}, "assumptions": {"type": "array", "items": {"type": "string"}}, "warnings": {"type": "array", "items": {"type": "string"}}, "errors": {"type": "array", "items": {"type": "object"}}, "clarification": {"type": ["object", "null"]}, "meta": {"type": "object"}},
        ["status"],
    )
)
def finalize_extraction(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    output = normalize_final_output(args)
    status = output["status"]
    clarification = output["clarification"]
    if status == "clarification_needed":
        if not isinstance(clarification, dict) or not isinstance(clarification.get("token"), str):
            raise ToolError("INVALID_FINAL_OUTPUT", "clarification_needed requires clarification.token")
        stored = state.get("clarifications", {}).get(clarification["token"])
        if not stored:
            raise ToolError("CLARIFICATION_NOT_FOUND", "Final output references an unknown clarification token")
        output["clarification"] = {"token": stored["token"], "questions": stored["questions"]}
    if status in {"success", "partial"}:
        result_id = output["data"].get("result_id")
        artifact_ref = output["data"].get("artifact_ref")
        if result_id and result_id not in state.get("result_sets", {}):
            raise ToolError("RESULT_NOT_FOUND", f"Result {result_id} not found")
        if artifact_ref and artifact_ref not in state.get("artifacts", {}):
            raise ToolError("ARTIFACT_NOT_FOUND", f"Artifact {artifact_ref} not found")
    state["status"] = status
    state["final_output"] = output
    return {"final": True, "output": output}
