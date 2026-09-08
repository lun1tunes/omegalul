"""Demo Agent FastAPI app: ``create_agent_app`` gives ``/health`` and the four agent routes over ``agent``.

Run: ``python -m uvicorn app.main:app --host 0.0.0.0 --port 8300`` (see start-windows.bat / start-linux.sh).
"""

from __future__ import annotations

from mas_agent_kit import create_agent_app

from .agent import agent

app = create_agent_app(agent, title="MAS Demo Agent", version="0.1.0")
