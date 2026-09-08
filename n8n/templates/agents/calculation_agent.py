"""Calculation Agent — HTTP-only agent (FastAPI ``/agent/run`` on the Math service, no n8n workflow)."""

from __future__ import annotations

from mas_agent_spec import AgentSpec

SPEC = AgentSpec(
    agent_id="calculation_agent",
    title="Calculation Agent",
    when_to_use=(
        "Если есть структурная поверхность и траектория скважины, находит их пересечение и начало интервала "
        "перфорации (глубина по стволу). Геометрия, не SCHEDULE."
    ),
    input_required=["surface", "trajectory"],
    output_provides=["top_perforation_md"],
    input_schema={"artifacts": {"surface": "поверхность CPS3", "trajectory": "траектория .dev"}},
    output_schema={"data": {"top_perforation_md": "глубина начала перфорации по стволу"}},
    hitl_policy="never",
    service_url_key="math_url",
    lab_url="http://math-service:8100",
    service_title="Math",
    # ``{math_url}`` is a Runtime Config field: the orchestrator substitutes it before the HTTP call.
    invoke_override={"kind": "http", "url": "{math_url}/agent/run"},
)
