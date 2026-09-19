"""CASE field Windows: Notepad BOM + ``=`` in a password must survive (Activity / cluster dotenv)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mas_agent_kit.env import load_service_env, reset_loaded_env_files


@pytest.fixture(autouse=True)
def _isolate_loaded_files() -> None:
    reset_loaded_env_files()
    yield
    reset_loaded_env_files()


def test_utf8_bom_and_equals_in_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TNAV_SSH_PASSWORD", raising=False)
    monkeypatch.delenv("TNAV_CLI_COMMAND", raising=False)
    (tmp_path / "tnav-cluster.env").write_bytes(
        b"\xef\xbb\xbfTNAV_SSH_PASSWORD=p@ss=word\n"
        b"TNAV_CLI_COMMAND=/opt/tNavigator/tNavigator-con --cpu-num=8 {model}\n"
    )
    loaded = load_service_env(tmp_path, "tnav-cluster.env")
    assert loaded and loaded[0].name == "tnav-cluster.env"
    assert os.environ["TNAV_SSH_PASSWORD"] == "p@ss=word"
    assert "{model}" in os.environ["TNAV_CLI_COMMAND"]


def test_process_environment_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TNAV_SSH_HOST", "already-set")
    (tmp_path / "tnav-cluster.env").write_text("TNAV_SSH_HOST=from-file\n", encoding="utf-8")
    load_service_env(tmp_path, "tnav-cluster.env")
    assert os.environ["TNAV_SSH_HOST"] == "already-set"
