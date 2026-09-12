"""Every agent service of the repo has the shape of ``agents-template/demo_agent`` (AGENTS.md §6).

    <service>/app/__init__.py   puts ../mas-agent-kit on sys.path (the kit is a repo module, not a pip package)
    <service>/app/agent.py      class <Name>Agent(AgentService) with open_session / result; module-level ``agent = <Name>Agent()``
    <service>/app/main.py       the FastAPI app comes from create_agent_app(agent) or agent_router(agent) — no hand-written
                                /agent-tools or /sessions routes, no open_session / result logic outside the class
    tools                       registered through the kit registry (``tools.tool(...)``), results via agent.store_result

The check is static (AST / text): services need their own env (API keys, session dirs) to import.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVICES = ("excel-agent-tools", "schedule-builder-service", "agents-template/demo_agent")
# Names that used to live as module functions in each service and now are AgentService methods.
PLUMBING = {"open_session", "session_result", "_store_result", "_agent_result", "emit_tool_progress", "normalize_agent_tool_args"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _agent_classes(tree: ast.Module) -> list[ast.ClassDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == "AgentService" for base in node.bases)
    ]


@pytest.mark.parametrize("service", SERVICES)
def test_init_bootstraps_the_repo_kit(service: str) -> None:
    text = (REPO / service / "app" / "__init__.py").read_text(encoding="utf-8")
    assert "mas-agent-kit" in text and "sys.path" in text, f"{service}: app/__init__.py must add ../mas-agent-kit to sys.path"


@pytest.mark.parametrize("service", SERVICES)
def test_agent_module_defines_one_agent_class_and_its_instance(service: str) -> None:
    tree = _tree(REPO / service / "app" / "agent.py")
    classes = _agent_classes(tree)
    assert len(classes) == 1, f"{service}: app/agent.py must define exactly one AgentService subclass"
    cls = classes[0]
    methods = {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}
    assert {"open_session", "result"} <= methods, f"{service}: {cls.name} must implement open_session and result"
    assigned = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "agent" for t in node.targets)
    ]
    assert assigned, f"{service}: app/agent.py must create the module-level instance ``agent = {cls.name}()``"
    call = assigned[-1].value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == cls.name


@pytest.mark.parametrize("service", SERVICES)
def test_main_serves_the_agent_through_the_kit_router(service: str) -> None:
    text = (REPO / service / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r"create_agent_app\(agent\b|agent_router\(agent\b", text), f"{service}: main.py must serve ``agent`` via create_agent_app / agent_router"
    assert not re.search(r"@(app|router)\.(get|post)\(\s*[\"']/(agent-tools|sessions)/", text), f"{service}: agent routes are the kit's — do not hand-write /agent-tools or /sessions in main.py"
    assert "AgentService" not in text, f"{service}: the agent class lives in app/agent.py, not in main.py"


@pytest.mark.parametrize("service", SERVICES)
def test_no_result_plumbing_outside_the_agent_class(service: str) -> None:
    offenders: list[str] = []
    for path in sorted((REPO / service / "app").glob("*.py")):
        for node in _tree(path).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in PLUMBING:
                offenders.append(f"{path.name}:{node.name}")
    assert not offenders, f"{service}: session/result plumbing must be AgentService methods, found module functions {offenders}"


@pytest.mark.parametrize("service", SERVICES)
def test_tools_are_registered_through_the_kit_registry(service: str) -> None:
    app_dir = REPO / service / "app"
    registrations = 0
    for path in app_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        registrations += len(re.findall(r"@(?:self\.tools|agent\.tools|TOOLS)\.tool\(|@tool\(", text))
        assert "def error_envelope(" not in text and "class SessionStore" not in text, f"{service}/{path.name}: kit code copied into the service"
    assert registrations >= 3, f"{service}: LLM tools must be registered with the kit ToolRegistry decorator"
