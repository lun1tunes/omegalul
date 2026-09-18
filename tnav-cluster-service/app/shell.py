"""Оболочка кластера: две реализации одного интерфейса и жёсткие правила безопасности.

    shell = open_shell(settings)          # ssh → SshShell (paramiko), local → LocalShell (песочница)
    with shell:
        shell.run(["ls", "-la", shell.path("MODELS")])
        text = shell.read_text("MODELS/SEVER/SEVER.data", limit=200_000)
        shell.copy("MODELS/SEVER/SEVER.data", "MODELS/SEVER/SEVER_prognoz.data")

Правила (нарушение = исключение, до кластера команда не доходит):

* **Удаление запрещено.** ``rm``, ``rmdir``, ``shred``, ``dd``, ``mkfs``, ``truncate`` — в чёрном списке.
  Разрешены только команды из ``ALLOWED_PROGRAMS`` (``cp``, ``mv``, ``mkdir``, ``ls``, ``cat``, ``pwd``,
  ``grep``, ``head``, ``tail``, ``stat``, ``find``, ``wc``, ``test``, ``du``, ``ps``). Нужна новая команда —
  добавьте её имя в ``ALLOWED_PROGRAMS`` и напишите тест.
* **Один корень.** Любой путь приводится к абсолютному внутри ``settings.root``; выход наружу (``..``,
  чужой абсолютный путь, симлинк-обход по строке) — ``PathOutsideRoot``.
* **Без склейки строк.** Команды собираются списком аргументов и экранируются ``shlex.quote``; отдельный
  путь ``run_script`` существует ровно для команды расчёта из настроек (её пишет инженер, не модель).
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict

from .settings import ClusterSettings

logger = logging.getLogger(__name__)

#: Что агенту разрешено выполнять на кластере. Расширяется осознанно, вместе с тестом.
ALLOWED_PROGRAMS = frozenset(
    {"ls", "cat", "cp", "mv", "mkdir", "pwd", "stat", "du", "df", "find", "grep", "head", "tail", "wc", "test", "readlink", "date", "ps", "hostname", "nproc"}
)
#: Что запрещено всегда — даже если кто-то добавит это в ALLOWED_PROGRAMS.
FORBIDDEN_PROGRAMS = frozenset({"rm", "rmdir", "unlink", "shred", "dd", "mkfs", "truncate", "chmod", "chown", "kill", "pkill", "reboot", "shutdown"})


class ClusterError(RuntimeError):
    """Кластер недоступен, команда запрещена или путь вне корня."""


class PathOutsideRoot(ClusterError):
    pass


class CommandNotAllowed(ClusterError):
    pass


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def lines(self) -> list[str]:
        return [line for line in self.stdout.splitlines() if line.strip()]

    def check(self) -> "CommandResult":
        if not self.ok:
            detail = (self.stderr or self.stdout or "").strip().splitlines()
            raise ClusterError(f"{self.argv[0]}: код {self.exit_code}" + (f", {detail[-1][:200]}" if detail else ""))
        return self


def check_argv(argv: list[str]) -> list[str]:
    """Проверка команды до отправки на кластер: программа из белого списка, аргументы без переводов строк."""
    if not argv or not str(argv[0] or "").strip():
        raise CommandNotAllowed("пустая команда")
    program = PurePosixPath(str(argv[0])).name
    if program in FORBIDDEN_PROGRAMS:
        raise CommandNotAllowed(f"команда {program} запрещена в этом сервисе (удаление и правка прав не выполняются)")
    if program not in ALLOWED_PROGRAMS:
        raise CommandNotAllowed(f"команда {program} не входит в список разрешённых: {', '.join(sorted(ALLOWED_PROGRAMS))}")
    out: list[str] = []
    for arg in argv:
        text = str(arg)
        if "\n" in text or "\r" in text or "\x00" in text:
            raise CommandNotAllowed("аргумент команды содержит перевод строки")
        out.append(text)
    return out


class ClusterShell(ABC):
    """Общая часть обоих транспортов: пути, белый список, удобные операции с файлами."""

    def __init__(self, settings: ClusterSettings):
        self.settings = settings

    # -- жизненный цикл ---------------------------------------------------------------------------

    def open(self) -> "ClusterShell":
        return self

    def close(self) -> None:
        return None

    def __enter__(self) -> "ClusterShell":
        return self.open()

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- пути -------------------------------------------------------------------------------------

    @property
    def root(self) -> PurePosixPath:
        return self.settings.root_path

    def path(self, relative: str) -> str:
        """Относительный (или уже абсолютный внутри корня) путь → абсолютный путь на кластере."""
        text = str(relative or "").strip().strip('"').strip("'")
        if not text or text in (".", "./"):
            return str(self.root)
        candidate = PurePosixPath(text)
        absolute = candidate if candidate.is_absolute() else self.root / candidate
        normalised = _normalise(absolute)
        if normalised != self.root and self.root not in normalised.parents:
            raise PathOutsideRoot(f"путь вне рабочего каталога кластера: {text}")
        return str(normalised)

    def rel(self, absolute: str) -> str:
        """Абсолютный путь на кластере → путь относительно корня (то, что видит агент и модель)."""
        try:
            return str(PurePosixPath(str(absolute)).relative_to(self.root))
        except ValueError:
            return str(absolute)

    # -- выполнение -------------------------------------------------------------------------------

    def run(self, argv: list[str], *, cwd: str | None = None, timeout: float | None = None) -> CommandResult:
        safe = check_argv(argv)
        command = " ".join(shlex.quote(part) for part in safe)
        return self._timed(safe, command, cwd=cwd, timeout=timeout)

    def run_script(self, script: str, *, cwd: str | None = None, timeout: float | None = None) -> CommandResult:
        """Единственный путь для команды расчёта из настроек (``cli_command``): её задаёт инженер в env.

        Модель (LLM) сюда ничего не передаёт: подставляется только путь к ``.data`` файлу, уже проверенный
        ``path()`` и экранированный ``shlex.quote``.
        """
        return self._timed(["sh", "-c"], f"sh -c {shlex.quote(script)}", cwd=cwd, timeout=timeout)

    def _timed(self, argv: list[str], command: str, *, cwd: str | None, timeout: float | None) -> CommandResult:
        started = time.monotonic()
        code, out, err = self._exec(command, cwd=cwd, timeout=timeout or self.settings.command_timeout_s)
        return CommandResult(
            argv=argv,
            exit_code=int(code),
            stdout=out,
            stderr=err,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    @abstractmethod
    def _exec(self, command: str, *, cwd: str | None, timeout: float) -> tuple[int, str, str]:
        """Выполнить готовую (уже экранированную) командную строку."""

    @abstractmethod
    def write_bytes(self, relative: str, payload: bytes) -> str:
        """Положить файл на кластер (новый файл schedule). Возвращает абсолютный путь."""

    # -- операции с файлами -----------------------------------------------------------------------

    def exists(self, relative: str) -> bool:
        return self.run(["test", "-e", self.path(relative)]).ok

    def is_file(self, relative: str) -> bool:
        return self.run(["test", "-f", self.path(relative)]).ok

    def is_dir(self, relative: str) -> bool:
        return self.run(["test", "-d", self.path(relative)]).ok

    def pwd(self) -> str:
        return self.run(["pwd"], cwd=str(self.root)).check().stdout.strip()

    def makedirs(self, relative: str) -> str:
        target = self.path(relative)
        self.run(["mkdir", "-p", target]).check()
        return target

    def list_dir(self, relative: str = ".", *, limit: int = 200) -> list[str]:
        result = self.run(["ls", "-1A", self.path(relative)])
        return result.lines[:limit] if result.ok else []

    def file_size(self, relative: str) -> int:
        result = self.run(["stat", "-c", "%s", self.path(relative)])
        if not result.ok:
            return 0
        try:
            return int(result.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            return 0

    def read_text(self, relative: str, *, limit: int = 200_000, tail: bool = False) -> str:
        """Текст файла с ограничением по размеру: модель никогда не тянет гигабайтные массивы."""
        target = self.path(relative)
        argv = ["tail", "-c", str(int(limit)), target] if tail else ["head", "-c", str(int(limit)), target]
        result = self.run(argv)
        if not result.ok:
            raise ClusterError(f"не читается файл на кластере: {self.rel(target)}")
        return result.stdout

    def count_lines(self, relative: str, pattern: str) -> int:
        """Сколько строк файла соответствует расширенному регулярному выражению (``grep -c -E``).

        Считает кластер, а не сервис: файлы schedule бывают на сотни мегабайт.
        """
        result = self.run(["grep", "-c", "-E", pattern, self.path(relative)])
        if result.exit_code not in (0, 1):
            raise ClusterError(f"не удалось посчитать строки в файле: {self.rel(self.path(relative))}")
        try:
            return int(result.stdout.strip().splitlines()[0]) if result.stdout.strip() else 0
        except (ValueError, IndexError):
            return 0

    def find_files(self, pattern: str, *, relative: str = ".", depth: int | None = None, limit: int = 100) -> list[str]:
        """Пути (относительно корня) файлов по маске имени — обход только по метаданным."""
        argv = ["find", self.path(relative), "-maxdepth", str(int(depth or self.settings.scan_depth)), "-type", "f", "-iname", pattern]
        result = self.run(argv)
        if not result.ok and not result.stdout:
            return []
        return [self.rel(line.strip()) for line in result.lines][:limit]

    def copy(self, source: str, target: str) -> str:
        src, dst = self.path(source), self.path(target)
        self.run(["cp", "-p", src, dst]).check()
        return dst

    def copy_tree(self, source: str, target: str) -> str:
        src, dst = self.path(source), self.path(target)
        self.run(["cp", "-a", src, dst]).check()
        return dst

    def move(self, source: str, target: str) -> str:
        src, dst = self.path(source), self.path(target)
        self.run(["mv", "-n", src, dst]).check()
        return dst

    def process_alive(self, pid: int) -> bool:
        if int(pid or 0) <= 0:
            return False
        return self.run(["ps", "-p", str(int(pid))]).ok


class LocalShell(ClusterShell):
    """Лаборатория и тесты: те же команды, но в локальной песочнице (``/bin/sh``), без сети.

    Нужна, чтобы мок ГД-кластера отвечал ровно как кластер: настоящие ``cp``/``mv``/``grep`` и настоящий
    (заглушечный) исполняемый файл tNavigator. В поле используется ``SshShell``.
    """

    def _exec(self, command: str, *, cwd: str | None, timeout: float) -> tuple[int, str, str]:
        workdir = cwd or str(self.root)
        try:
            proc = subprocess.run(  # noqa: S602 — команда уже экранирована shlex.quote
                command,
                shell=True,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8")},
            )
        except subprocess.TimeoutExpired:
            raise ClusterError(f"команда не ответила за {int(timeout)} с") from None
        except OSError as exc:
            raise ClusterError(f"песочница кластера недоступна: {exc}") from exc
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def write_bytes(self, relative: str, payload: bytes) -> str:
        target = Path(self.path(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return str(target)


class SshShell(ClusterShell):
    """Поле: одна SSH-сессия на вызов инструмента (paramiko: exec для команд, SFTP для загрузки файлов)."""

    def __init__(self, settings: ClusterSettings):
        super().__init__(settings)
        self._client: Any = None

    # paramiko импортируется лениво: в тестах и в лаборатории транспорт local, зависимости может не быть.
    @staticmethod
    def _paramiko() -> Any:
        try:
            import paramiko  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — в поле библиотека стоит из requirements.txt
            raise ClusterError("на рабочей станции не установлена библиотека SSH — выполните установку зависимостей сервиса") from exc
        return paramiko

    def open(self) -> "SshShell":
        if self._client is not None:
            return self
        paramiko = self._paramiko()
        creds = self.settings.ssh
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if creds.known_hosts:
            client.load_host_keys(creds.known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy() if creds.host_key_policy == "strict" else paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": creds.host,
            "port": creds.port,
            "username": creds.user,
            "timeout": creds.connect_timeout_s,
            "banner_timeout": creds.connect_timeout_s,
            "auth_timeout": creds.connect_timeout_s,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if creds.key_path:
            kwargs["key_filename"] = creds.key_path
            if creds.key_passphrase is not None:
                kwargs["passphrase"] = creds.key_passphrase.get_secret_value()
        if creds.password is not None:
            kwargs["password"] = creds.password.get_secret_value()
        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001 — paramiko бросает несколько разных типов
            raise ClusterError(f"не удалось подключиться к кластеру {creds.host}: {type(exc).__name__}") from exc
        if creds.keepalive_s:
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(creds.keepalive_s)
        self._client = client
        return self

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                logger.debug("ssh close failed", exc_info=True)

    def _exec(self, command: str, *, cwd: str | None, timeout: float) -> tuple[int, str, str]:
        self.open()
        full = f"cd {shlex.quote(cwd or str(self.root))} && {command}"
        try:
            _stdin, stdout, stderr = self._client.exec_command(full, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
        except Exception as exc:  # noqa: BLE001
            raise ClusterError(f"команда на кластере не выполнилась: {type(exc).__name__}") from exc
        return int(code), out, err

    def write_bytes(self, relative: str, payload: bytes) -> str:
        target = self.path(relative)
        parent = str(PurePosixPath(target).parent)
        self.run(["mkdir", "-p", parent]).check()
        self.open()
        try:
            sftp = self._client.open_sftp()
            try:
                with sftp.open(target, "wb") as handle:
                    handle.write(payload)
            finally:
                sftp.close()
        except Exception as exc:  # noqa: BLE001
            raise ClusterError(f"не удалось записать файл на кластер: {type(exc).__name__}") from exc
        return target


def open_shell(settings: ClusterSettings) -> ClusterShell:
    """Оболочка по настройкам: ``ssh`` — кластер, ``local`` — песочница лаборатории и тестов."""
    if not settings.ready:
        raise ClusterError("доступ к кластеру не настроен: " + "; ".join(settings.problems))
    shell: ClusterShell = LocalShell(settings) if settings.transport == "local" else SshShell(settings)
    return shell


def _normalise(path: PurePosixPath) -> PurePosixPath:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts and parts[-1] not in ("/", ""):
                parts.pop()
            continue
        if part in (".",):
            continue
        parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath("/")
