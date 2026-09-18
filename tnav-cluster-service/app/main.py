"""tNav Cluster Agent FastAPI: ``create_agent_app`` даёт ``/health`` и четыре маршрута агента.

Запуск: ``python -m uvicorn app.main:app --host 0.0.0.0 --port 8400`` (см. start-windows.bat / start-linux.sh).
``/health`` дополнительно показывает настройку кластера: адрес, корень, команду расчёта и чего не хватает,
чтобы инженер правил ``tnav-cluster.env``, а не гадал.
"""

from __future__ import annotations

from typing import Any

from mas_agent_kit import create_agent_app

from .agent import agent


def cluster_health() -> dict[str, Any]:
    problems = agent.settings.problems
    return {"cluster": agent.settings.secret_free(), "cluster_ready": not problems, "cluster_problems": problems}


app = create_agent_app(agent, title="MAS tNav Cluster Agent", version="0.1.0", extra_health=cluster_health)
