"""Настройки кластерного агента (pydantic v2): один корень на кластере, один SSH-доступ, одна команда расчёта.

Всё, что инженер меняет в поле, лежит в ``tnav-cluster.env`` — см. ``tnav-cluster.env.example``.
Код читает окружение один раз: ``ClusterSettings.from_env()``.

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║  КОМАНДА ЗАПУСКА РАСЧЁТА МЕНЯЕТСЯ В ДВУХ МЕСТАХ (одно из них — по выбору):               ║
║   1) переменная окружения ``TNAV_CLI_COMMAND`` в ``tnav-cluster.env`` (обычный путь);    ║
║   2) константа ``DEFAULT_CLI_COMMAND`` ниже — значение по умолчанию этого файла.          ║
║  Формат: «<путь к tNavigator-con> <опции> {model}». Подстановка ``{model}`` — путь к      ║
║  входному ``.data`` файлу, его подставляет агент; остальное — ваше.                       ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from mas_agent_kit.env import load_service_env


SERVICE_ROOT = Path(__file__).resolve().parents[1]

# ``app/__init__`` already loaded the same files; a second call is idempotent (override=False).
LOADED_ENV_FILES = load_service_env(SERVICE_ROOT, "tnav-cluster.env")

#: Команда расчёта по умолчанию: путь к исполняемому файлу, опции, затем модель.
#: Опции — из мануала tNavigator, раздел 8.1 («Опции командной строки»):
#:   --cpu-num=<N>   количество потоков (8.1.5),
#:   --log-lang=ru   язык лог-файлов (8.1.8),
#:   --dump-res      писать res-файлы результатов (8.1.8).
DEFAULT_CLI_COMMAND = "/opt/tNavigator/tNavigator-con --cpu-num=8 --log-lang=ru --dump-res {model}"

#: Подкаталог рядом с моделью, куда tNavigator кладёт ``<имя>.log`` / ``.err`` / ``.end`` / ``.lock``
#: (мануал, раздел 9.1.1 «Файлы формата tNavigator»).
DEFAULT_RESULTS_DIRNAME = "RESULTS"

TRUE_WORDS = {"1", "true", "yes", "on", "да"}


def _env(env: dict[str, str], name: str, default: str = "") -> str:
    return str(env.get(name, default) or "").strip()


def _env_float(env: dict[str, str], name: str, default: float) -> float:
    raw = _env(env, name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    return int(_env_float(env, name, float(default)))


class SshCredentials(BaseModel):
    """Доступ к ГД-кластеру. Пароль и ключ — только из окружения, в логи не попадают (``SecretStr``)."""

    model_config = ConfigDict(extra="forbid")

    host: str = ""
    port: int = Field(default=22, ge=1, le=65535)
    user: str = ""
    password: SecretStr | None = None
    #: Приватный ключ вместо пароля (если в организации так принято).
    key_path: str = ""
    key_passphrase: SecretStr | None = None
    #: ``auto_add`` — принять ключ хоста при первом подключении (типично для внутреннего кластера);
    #: ``strict`` — требовать запись в ``known_hosts``.
    host_key_policy: Literal["auto_add", "strict"] = "auto_add"
    known_hosts: str = ""
    connect_timeout_s: float = Field(default=20.0, gt=0)
    keepalive_s: int = Field(default=30, ge=0)

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and (self.password is not None or self.key_path))

    def secret_free(self) -> dict[str, Any]:
        """Для лога разработчика: адрес и пользователь, без пароля и пути к ключу."""
        return {"host": self.host, "port": self.port, "user": self.user, "auth": "key" if self.key_path else "password"}


class ClusterSettings(BaseModel):
    """Полная конфигурация сервиса. Неизменяемая: создаётся из окружения и передаётся по ссылке."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Слушатель FastAPI. Поле: ``TNAV_CLUSTER_HOST=0.0.0.0``, чтобы корпоративный n8n достучался.
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=8400, ge=1, le=65535)
    #: ``ssh`` — поле (paramiko); ``local`` — лаборатория и тесты (та же оболочка в песочнице).
    transport: Literal["ssh", "local"] = "ssh"
    #: Единственный базовый каталог, внутри которого агент вообще умеет работать.
    root: str = "/data/models"
    ssh: SshCredentials = Field(default_factory=SshCredentials)
    cli_command: str = DEFAULT_CLI_COMMAND
    results_dirname: str = DEFAULT_RESULTS_DIRNAME
    #: Таймаут одной короткой команды (``ls``, ``grep``, ``cp``).
    command_timeout_s: float = Field(default=120.0, gt=0)
    #: Как часто агент опрашивает состояние расчёта и как часто пишет строку в ленту инженеру.
    poll_seconds: float = Field(default=60.0, gt=0)
    progress_every_s: float = Field(default=900.0, gt=0)
    #: Предохранитель: дольше этого агент не ждёт расчёт (расчёт на кластере продолжается).
    max_wait_hours: float = Field(default=72.0, gt=0)
    #: Границы поиска, чтобы не обходить дерево модели целиком (модели весят гигабайты).
    scan_depth: int = Field(default=4, ge=1, le=8)
    max_models: int = Field(default=40, ge=1)
    max_schedule_candidates: int = Field(default=25, ge=1)
    #: Сколько байт лога читать за один опрос (хвост файла).
    log_tail_bytes: int = Field(default=20000, ge=1000)

    @field_validator("root")
    @classmethod
    def _root_is_absolute_posix(cls, value: str) -> str:
        text = str(value or "").strip().rstrip("/")
        if not text.startswith("/"):
            raise ValueError("базовый каталог кластера должен быть абсолютным unix-путём, например /data/models")
        if ".." in PurePosixPath(text).parts:
            raise ValueError("базовый каталог кластера не может содержать ..")
        return text

    @field_validator("cli_command")
    @classmethod
    def _command_has_model_placeholder(cls, value: str) -> str:
        text = str(value or "").strip()
        if "{model}" not in text:
            raise ValueError("в команде расчёта нужна подстановка {model} — путь к входному .data файлу")
        try:
            shlex.split(text.replace("{model}", "MODEL"))
        except ValueError as exc:
            raise ValueError(f"команда расчёта не разбирается как командная строка: {exc}") from exc
        return text

    @field_validator("results_dirname")
    @classmethod
    def _results_dirname_is_a_name(cls, value: str) -> str:
        text = str(value or "").strip().strip("/")
        if not text or "/" in text:
            raise ValueError("каталог результатов — одно имя без слэшей, например RESULTS")
        return text

    # -- готовность -------------------------------------------------------------------------------

    @property
    def problems(self) -> list[str]:
        """Чего не хватает, чтобы работать с кластером. Пусто — можно подключаться.

        Сервис запускается и с неполной настройкой: инженер видит причину в ``/health`` и правит
        ``tnav-cluster.env``, а не гадает, почему процесс не поднялся.
        """
        out: list[str] = []
        if self.transport == "ssh":
            if not self.ssh.host:
                out.append("не задан адрес гидродинамического кластера")
            if not self.ssh.user:
                out.append("не задано имя пользователя для подключения к кластеру")
            if self.ssh.password is None and not self.ssh.key_path:
                out.append("не задан пароль или путь к ключу для подключения к кластеру")
        return out

    @property
    def ready(self) -> bool:
        return not self.problems

    # -- пути -------------------------------------------------------------------------------------

    @property
    def root_path(self) -> PurePosixPath:
        return PurePosixPath(self.root)

    def simulator_program(self) -> str:
        """Путь к исполняемому файлу tNavigator из команды расчёта (первое слово)."""
        parts = shlex.split(self.cli_command.replace("{model}", "MODEL"))
        return parts[0] if parts else ""

    def simulator_options(self) -> list[str]:
        """Опции команды расчёта без программы и без модели — для инвентаря и лога."""
        parts = shlex.split(self.cli_command.replace("{model}", "MODEL"))
        return [p for p in parts[1:] if p != "MODEL"]

    # -- окружение --------------------------------------------------------------------------------

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ClusterSettings":
        src = dict(env if env is not None else os.environ)
        password = _env(src, "TNAV_SSH_PASSWORD")
        passphrase = _env(src, "TNAV_SSH_KEY_PASSPHRASE")
        ssh = SshCredentials(
            host=_env(src, "TNAV_SSH_HOST"),
            port=_env_int(src, "TNAV_SSH_PORT", 22),
            user=_env(src, "TNAV_SSH_USER"),
            password=SecretStr(password) if password else None,
            key_path=_env(src, "TNAV_SSH_KEY_PATH"),
            key_passphrase=SecretStr(passphrase) if passphrase else None,
            host_key_policy="strict" if _env(src, "TNAV_SSH_STRICT_HOST_KEY").lower() in TRUE_WORDS else "auto_add",
            known_hosts=_env(src, "TNAV_SSH_KNOWN_HOSTS"),
            connect_timeout_s=_env_float(src, "TNAV_SSH_CONNECT_TIMEOUT_S", 20.0),
            keepalive_s=_env_int(src, "TNAV_SSH_KEEPALIVE_S", 30),
        )
        return cls(
            listen_host=_env(src, "TNAV_CLUSTER_HOST", "127.0.0.1") or "127.0.0.1",
            listen_port=_env_int(src, "TNAV_CLUSTER_PORT", 8400),
            transport="local" if _env(src, "TNAV_TRANSPORT", "ssh").lower() == "local" else "ssh",
            root=_env(src, "TNAV_CLUSTER_ROOT", "/data/models"),
            ssh=ssh,
            cli_command=_env(src, "TNAV_CLI_COMMAND", DEFAULT_CLI_COMMAND),
            results_dirname=_env(src, "TNAV_RESULTS_DIRNAME", DEFAULT_RESULTS_DIRNAME),
            command_timeout_s=_env_float(src, "TNAV_COMMAND_TIMEOUT_S", 120.0),
            poll_seconds=_env_float(src, "TNAV_POLL_SECONDS", 60.0),
            progress_every_s=_env_float(src, "TNAV_PROGRESS_EVERY_S", 900.0),
            max_wait_hours=_env_float(src, "TNAV_MAX_WAIT_HOURS", 72.0),
            scan_depth=_env_int(src, "TNAV_SCAN_DEPTH", 4),
            max_models=_env_int(src, "TNAV_MAX_MODELS", 40),
            max_schedule_candidates=_env_int(src, "TNAV_MAX_SCHEDULE_CANDIDATES", 25),
            log_tail_bytes=_env_int(src, "TNAV_LOG_TAIL_BYTES", 20000),
        )

    def secret_free(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "root": self.root,
            "simulator": self.simulator_program(),
            "options": self.simulator_options(),
            "results_dirname": self.results_dirname,
            "ssh": self.ssh.secret_free() if self.transport == "ssh" else {},
        }
