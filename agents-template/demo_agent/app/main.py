"""Demo Agent FastAPI app: ``create_agent_app`` gives ``/health`` and the four agent routes over ``agent``.

Run: ``python -m app`` (host/port from ``demo-agent.env`` via kit ``load_service_env``, not CMD ``for /f``).
"""

from __future__ import annotations

from mas_agent_kit import create_agent_app

from .agent import agent

app = create_agent_app(agent, title="MAS Demo Agent", version="0.1.0")
