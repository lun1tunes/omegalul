"""Лабораторный ГД-кластер в контейнере: дерево моделей + SSH-сервер на фиксированном порту.

Только для лаборатории (Docker Compose): поднимает мок кластера, к которому сервис подключается тем же
транспортом, что в поле (paramiko: exec + SFTP). В поле этот файл не запускается — там настоящий кластер.

    python3 mock_cluster/lab_cluster.py            # корень /cluster, порт 2222, пользователь re

Переменные: ``TNAV_MOCK_ROOT``, ``TNAV_MOCK_SSH_PORT``, ``TNAV_SSH_USER``, ``TNAV_SSH_PASSWORD``,
``TNAV_MOCK_FORECAST_DATES`` (сколько дат в прогнозном файле расписания — столько шагов и посчитает мок).
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mock_cluster.model_tree import build_model_tree  # noqa: E402
from mock_cluster.ssh_server import MockClusterSshServer  # noqa: E402


def main() -> int:
    root = Path(os.getenv("TNAV_MOCK_ROOT") or "/cluster")
    port = int(os.getenv("TNAV_MOCK_SSH_PORT") or 2222)
    user = os.getenv("TNAV_SSH_USER") or "re"
    password = os.getenv("TNAV_SSH_PASSWORD") or "cluster-pass"
    paths = build_model_tree(root, forecast_dates=int(os.getenv("TNAV_MOCK_FORECAST_DATES") or 8), history_dates=3)
    server = MockClusterSshServer(root, user=user, password=password, host="0.0.0.0", port=port).start()  # noqa: S104 — мок внутри контейнера
    print(f"mock cluster ready: root={root} ssh=0.0.0.0:{server.port} models={sorted(paths.values())}", flush=True)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
