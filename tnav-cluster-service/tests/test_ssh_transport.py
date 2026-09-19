"""Полевой транспорт агента: paramiko поверх мока ГД-кластера (exec + SFTP), а не локальная песочница.

Эти тесты проверяют то, что в лаборатории обходится транспортом ``local``: подключение по паролю,
выполнение команд в корне, загрузку файла через SFTP, запуск расчёта и чтение его состояния по SSH.

Запуск: из venv сервиса (``paramiko`` в ``requirements.txt``) —
``cd tnav-cluster-service && PYTHONPATH=. .venv/bin/python -m pytest tests/test_ssh_transport.py``.
В lab-venv Activity библиотеки нет, поэтому файл пропускается целиком.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.cluster import ClusterWorkspace
from app.settings import ClusterSettings, SshCredentials
from app.shell import ClusterError, SshShell
from mock_cluster import build_model_tree

paramiko = pytest.importorskip("paramiko", reason="полевой SSH-путь проверяется в venv сервиса")

from mock_cluster.ssh_server import MockClusterSshServer  # noqa: E402 — после importorskip

MOCK_CON = Path(__file__).resolve().parents[1] / "mock_cluster" / "tnav_con_mock.py"
NEW_SCHEDULE = "DATES\n  1 FEB 2027 /\n/\n\nDATES\n  1 MAR 2027 /\n/\n"


@pytest.fixture
def cluster(tmp_path):
    root = tmp_path / "cluster"
    paths = build_model_tree(root, forecast_dates=4, history_dates=1)
    with MockClusterSshServer(root, user="re", password="cluster-pass") as server:
        yield server, paths


@pytest.fixture
def settings(cluster, tmp_path, monkeypatch) -> ClusterSettings:
    server, _ = cluster
    monkeypatch.setenv("TNAV_MOCK_SECONDS", "0.3")
    return ClusterSettings(
        transport="ssh",
        root=str(tmp_path / "cluster"),
        ssh=SshCredentials(host=server.host, port=server.port, user="re", password="cluster-pass"),
        cli_command=f"{sys.executable} {MOCK_CON} --cpu-num=2 --log-lang=ru {{model}}",
        poll_seconds=0.05,
        command_timeout_s=30.0,
    )


def test_commands_and_uploads_go_over_ssh(settings, cluster) -> None:
    _, paths = cluster
    with SshShell(settings) as shell:
        assert shell.is_file(paths["sever"])
        assert "RUNSPEC" in shell.read_text(paths["sever"], limit=4000)
        assert shell.count_lines(paths["sever_schedule"], r"^[[:space:]]*DATES([[:space:]]|$)") == 4
        written = shell.write_bytes("MODELS/SEVER/INCLUDE/SCHEDULE/FROM_SFTP.INC", NEW_SCHEDULE.encode("utf-8"))
        assert written.endswith("FROM_SFTP.INC")
        assert shell.read_text("MODELS/SEVER/INCLUDE/SCHEDULE/FROM_SFTP.INC", limit=4000) == NEW_SCHEDULE


def test_one_ssh_session_runs_several_commands(settings) -> None:
    """CASE-6aad611c-732bd3: mock used to close the transport after the first exec (EOFError on hostname)."""
    with SshShell(settings) as shell:
        assert shell.is_dir(".")
        shell.run(["hostname"])
        assert "cluster" in shell.pwd()


def test_bad_password_is_a_cluster_error(settings) -> None:
    broken = settings.model_copy(update={"ssh": settings.ssh.model_copy(update={"password": None, "key_path": "", "user": "re"})})
    assert broken.ready is False, "без пароля и ключа сервис сам сообщает, чего не хватает"
    wrong = settings.model_copy(update={"ssh": settings.ssh.model_copy(update={"password": SecretStr("wrong-pass")})})
    with pytest.raises(ClusterError, match="подключиться"):
        SshShell(wrong).open()


def test_full_version_and_run_over_ssh(settings, cluster) -> None:
    _, paths = cluster
    workspace = ClusterWorkspace(settings)
    assert workspace.check_connection()["transport"] == "ssh"
    layout = workspace.layout(paths["sever"])
    assert layout.main_schedule is not None and layout.main_schedule.path == paths["sever_schedule"]

    version = workspace.create_version(layout, "ssh_case", NEW_SCHEDULE.encode("utf-8"), schedule_filename="FORECAST_SSH.INC")
    assert version.model_path == "MODELS/SEVER/SEVER_ssh_case.data"
    assert version.dates_total == 2

    handle = workspace.launch(version.model_path, dates_total=version.dates_total)
    deadline = time.time() + 60
    status = workspace.status(handle)
    while not status.done and time.time() < deadline:
        time.sleep(0.1)
        status = workspace.status(handle)
    assert status.state == "finished"
    assert status.digest.steps == 3, "1 дата истории + 2 даты нового расписания"
