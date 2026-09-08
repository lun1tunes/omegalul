"""``AgentService`` — the object a new agent implements; ``agent_router`` exposes it over HTTP.

The n8n workflow of an agent (generated from its ``AgentSpec``) talks to the service like this:

    POST /agent-tools/open_session   {agent_task}            → {"ok": true, "session_id", ...brief for the LLM}
                                                            or {"ok": false, "status", "result": agent_result}
    POST /agent-tools/{tool}         {session_id, ...args}   → flat tool envelope (ToolRegistry.run)
    GET  /sessions/{id}/result                               → agent_result fixed by the tools
    POST /sessions/{id}/close                                → {"ok": true, "closed": bool}

Subclass, set ``agent_id`` / ``store`` / ``tools``, implement ``open_session`` and ``result``:

    class DemoAgent(AgentService):
        agent_id = "demo_agent"
        store = SessionStore(prefix="demo")
        tools = ToolRegistry(store)

        def open_session(self, task):
            packet = self.packet(task)
            state = self.store.create({"case_id": packet.case_id, "task_id": packet.task_id, ...})
            return self.opened(state, inspect={...})

        def result(self, state):
            return state.get("result") or self.needs_input(state, "Что именно посчитать?")
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Request

from .activity import ActivityClient
from .errors import SessionNotFound
from .hitl import engineer_answers
from .packet import CasePacket
from .result import agent_result, needs_input
from .session import SessionStore
from .tools import ToolRegistry


class AgentService:
    agent_id: str = ""
    store: SessionStore
    tools: ToolRegistry
    #: Activity URL when the task does not carry ``inputs.activity_base_url`` (lab: compose env).
    activity_base_url: str = os.getenv("ACTIVITY_BASE_URL", "")

    # -- to implement ---------------------------------------------------------------------------

    def open_session(self, task: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def result(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    # -- hooks ----------------------------------------------------------------------------------

    def normalize_args(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Accept transport aliases of your LLM tools before validation (default: as is)."""
        return args

    def after_tool(self, state: dict[str, Any], tool_name: str, result: dict[str, Any]) -> None:
        """E.g. post a progress line to Activity for long tools (default: nothing)."""

    # -- helpers --------------------------------------------------------------------------------

    def activity(self, task: dict[str, Any]) -> ActivityClient:
        return ActivityClient.from_task(task, agent_id=self.agent_id, default_base_url=self.activity_base_url)

    def activity_for_state(self, state: dict[str, Any]) -> ActivityClient:
        return ActivityClient(
            str(state.get("activity_base_url") or self.activity_base_url),
            agent_id=self.agent_id,
            case_id=str(state.get("case_id") or ""),
            task_id=str(state.get("task_id") or ""),
        )

    def packet(self, task: dict[str, Any]) -> CasePacket:
        return CasePacket.load(task, self.activity(task))

    def base_state(self, packet: CasePacket) -> dict[str, Any]:
        """Common session fields; extend with what your tools need (source text, file paths…)."""
        return {
            "agent_id": self.agent_id,
            "case_id": packet.case_id,
            "task_id": packet.task_id,
            "objective": packet.objective,
            "handoff_message": packet.handoff_message,
            "activity_base_url": packet.activity.base_url if packet.activity else self.activity_base_url,
            "inputs": packet.inputs,
            "context": packet.context,
            "result": None,
        }

    def opened(self, state: dict[str, Any], **brief: Any) -> dict[str, Any]:
        """``open_session`` success: session id + what the LLM needs to pick a tool."""
        return {
            "ok": True,
            "session_id": state["session_id"],
            "task_id": str(state.get("task_id") or ""),
            "objective": str(state.get("objective") or ""),
            "handoff_message": str(state.get("handoff_message") or ""),
            "engineer_answers": engineer_answers(state.get("context")),
            "rework_reason": str((state.get("inputs") or {}).get("rework_reason") or "") if isinstance(state.get("inputs"), dict) else "",
            **brief,
        }

    @staticmethod
    def not_opened(result: dict[str, Any]) -> dict[str, Any]:
        """``open_session`` could not start (no input file, load error): the result is final."""
        return {"ok": False, "status": result.get("status", "failed"), "task_id": result.get("task_id", ""), "result": result}

    def new_result(self, state: dict[str, Any], status: str, message: str, **kw: Any) -> dict[str, Any]:
        return agent_result(self.agent_id, str(state.get("task_id") or ""), status, message, **kw)

    def needs_input(self, state: dict[str, Any], question: str, **kw: Any) -> dict[str, Any]:
        return needs_input(self.agent_id, str(state.get("task_id") or ""), question, **kw)

    def store_result(self, state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        state["result"] = result
        self.store.save(state)
        return result


def agent_router(agent: AgentService, *, dependencies: list[Any] | None = None) -> APIRouter:
    router = APIRouter(dependencies=dependencies or [])

    @router.post("/agent-tools/open_session")
    def open_session(task: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return agent.open_session(task if isinstance(task, dict) else {})

    @router.post("/agent-tools/{tool_name}")
    async def call_tool(tool_name: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="body must be an object")
        session_id = str(body.pop("session_id", "") or "")
        if not session_id:
            raise HTTPException(status_code=422, detail="session_id is required")
        # n8n tools send arguments top-level; older exports wrap them in ``input`` / ``args``.
        args = body.get("input") if isinstance(body.get("input"), dict) else body.get("args") if isinstance(body.get("args"), dict) else body
        try:
            with agent.store.lock(session_id):
                state = agent.store.load(session_id)
                result = agent.tools.run(state, tool_name, agent.normalize_args(tool_name, dict(args)))
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="session_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        agent.after_tool(state, tool_name, result)
        return result

    @router.get("/sessions/{session_id}/result")
    def session_result(session_id: str) -> dict[str, Any]:
        try:
            with agent.store.lock(session_id):
                return agent.result(agent.store.load(session_id))
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="session_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/close")
    def close_session(session_id: str) -> dict[str, Any]:
        return agent.store.close(session_id)

    return router


def create_agent_app(agent: AgentService, *, title: str = "", version: str = "0.1.0", dependencies: list[Any] | None = None) -> FastAPI:
    """FastAPI app with the agent routes and ``GET /health`` (what the Health Check form probes)."""
    app = FastAPI(title=title or f"{agent.agent_id}-service", version=version)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "agent_id": agent.agent_id, "tools": agent.tools.names}

    app.include_router(agent_router(agent, dependencies=dependencies))
    return app
