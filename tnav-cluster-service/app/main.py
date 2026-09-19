"""tNav Cluster Agent FastAPI: ``create_agent_app`` даёт ``/health`` и четыре маршрута агента.

Запуск: ``python -m app`` (host/port из ``tnav-cluster.env`` через kit ``load_service_env``, как Activity).
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
