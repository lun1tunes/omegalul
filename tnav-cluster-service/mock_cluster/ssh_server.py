"""SSH-сервер мока кластера: тот же транспорт, что в поле, но на локальном каталоге.

Нужен, чтобы проверять реальный путь агента (paramiko → exec + SFTP), а не только локальную песочницу:
команды выполняются в каталоге-песочнице, SFTP пишет файлы туда же, удаление через SFTP запрещено —
как и в политике агента.

    server = MockClusterSshServer(root="/tmp/cluster", user="re", password="secret")
    server.start()
    ...            # TNAV_SSH_HOST=127.0.0.1 TNAV_SSH_PORT=server.port
    server.stop()

Только для тестов и лаборатории: аутентификация по одному паролю, ключ хоста генерируется на старте.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import paramiko

logger = logging.getLogger(__name__)

#: Сколько ждать команду в канале и сколько живёт клиентская сессия без активности.
CHANNEL_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 600.0


class _SandboxSftp(paramiko.SFTPServerInterface):
    """SFTP внутри песочницы: чтение, запись, каталоги, переименование. Удаление — запрещено."""

    root: Path = Path("/")

    def _real(self, path: str) -> Path:
        cleaned = str(path or ".").lstrip("/")
        target = (self.root / cleaned).resolve()
        if target != self.root and self.root not in target.parents:
            raise PermissionError(path)
        return target

    def list_folder(self, path):  # noqa: ANN001, ANN201 — сигнатуры paramiko
        try:
            target = self._real(path)
            out = []
            for entry in target.iterdir():
                attr = paramiko.SFTPAttributes.from_stat(entry.stat())
                attr.filename = entry.name
                out.append(attr)
            return out
        except (OSError, PermissionError) as exc:
            return paramiko.SFTPServer.convert_errno(getattr(exc, "errno", 13))

    def stat(self, path):  # noqa: ANN001, ANN201
        try:
            return paramiko.SFTPAttributes.from_stat(self._real(path).stat())
        except (OSError, PermissionError) as exc:
            return paramiko.SFTPServer.convert_errno(getattr(exc, "errno", 2))

    def lstat(self, path):  # noqa: ANN001, ANN201
        return self.stat(path)

    def open(self, path, flags, attr):  # noqa: ANN001, ANN201, A003
        try:
            target = self._real(path)
        except PermissionError:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            fd = os.open(str(target), flags | getattr(os, "O_BINARY", 0), 0o644)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT))
        mode = "r+b" if writing else "rb"
        try:
            fobj = os.fdopen(fd, mode)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        handle = paramiko.SFTPHandle(flags)
        handle.filename = str(target)
        handle.readfile = fobj
        handle.writefile = fobj if writing else None
        return handle

    def mkdir(self, path, attr):  # noqa: ANN001, ANN201
        try:
            self._real(path).mkdir(parents=True, exist_ok=True)
            return paramiko.SFTP_OK
        except (OSError, PermissionError) as exc:
            return paramiko.SFTPServer.convert_errno(getattr(exc, "errno", 13))

    def rename(self, oldpath, newpath):  # noqa: ANN001, ANN201
        try:
            self._real(oldpath).rename(self._real(newpath))
            return paramiko.SFTP_OK
        except (OSError, PermissionError) as exc:
            return paramiko.SFTPServer.convert_errno(getattr(exc, "errno", 13))

    def remove(self, path):  # noqa: ANN001, ANN201
        return paramiko.SFTP_PERMISSION_DENIED

    def rmdir(self, path):  # noqa: ANN001, ANN201
        return paramiko.SFTP_PERMISSION_DENIED


class _Server(paramiko.ServerInterface):
    def __init__(self, user: str, password: str):
        self.user = user
        self.password = password
        self.command = ""
        self.exec_event = threading.Event()

    def check_auth_password(self, username: str, password: str) -> int:
        ok = username == self.user and password == self.password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command) -> bool:  # noqa: ANN001
        self.command = command.decode("utf-8", errors="replace") if isinstance(command, bytes) else str(command)
        self.exec_event.set()
        return True

    def check_channel_pty_request(self, *args, **kwargs) -> bool:  # noqa: ANN002, ANN003
        return True


class MockClusterSshServer:
    """SSH-сервер над каталогом-песочницей. ``start`` занимает свободный порт, ``stop`` закрывает всё."""

    def __init__(self, root: str | Path, *, user: str = "re", password: str = "cluster-pass", host: str = "127.0.0.1", port: int = 0):
        self.root = Path(root).resolve()
        self.user = user
        self.password = password
        self.host = host
        #: 0 — свободный порт (тесты); в контейнере лаборатории порт фиксированный.
        self.port = int(port)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._clients: list[paramiko.Transport] = []
        self._stop = threading.Event()
        self._key = paramiko.RSAKey.generate(2048)

    # -- жизненный цикл -----------------------------------------------------------------------

    def start(self) -> "MockClusterSshServer":
        self.root.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(8)
        listener.settimeout(0.5)
        self.port = listener.getsockname()[1]
        self._socket = listener
        self._thread = threading.Thread(target=self._accept_loop, name="mock-cluster-ssh", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for transport in list(self._clients):
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockClusterSshServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- обслуживание клиентов ----------------------------------------------------------------

    def _accept_loop(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        transport.add_server_key(self._key)
        sftp_class = type("_Sandbox", (_SandboxSftp,), {"root": self.root})
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, sftp_class)
        server = _Server(self.user, self.password)
        self._clients.append(transport)
        try:
            transport.start_server(server=server)
            channel = transport.accept(CHANNEL_TIMEOUT_S)
            if channel is None:
                return
            if server.exec_event.wait(CHANNEL_TIMEOUT_S) and server.command:
                self._run_command(channel, server.command)
                channel.close()
                return
            while transport.is_active() and not self._stop.is_set():
                time.sleep(0.05)  # SFTP обслуживает сам paramiko
        except Exception as exc:  # noqa: BLE001 — мок не должен падать из-за клиента
            logger.debug("mock ssh client failed: %s", exc)
        finally:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass
            if transport in self._clients:
                self._clients.remove(transport)

    def _run_command(self, channel: paramiko.Channel, command: str) -> None:
        """Команда исполняется в песочнице, как на кластере: своя оболочка, свой рабочий каталог."""
        try:
            done = subprocess.run(  # noqa: S602 — мок кластера: оболочка здесь и есть предмет теста
                command,
                shell=True,
                cwd=str(self.root),
                capture_output=True,
                timeout=COMMAND_TIMEOUT_S,
                env={**os.environ, "HOME": str(self.root)},
            )
            if done.stdout:
                channel.sendall(done.stdout)
            if done.stderr:
                channel.sendall_stderr(done.stderr)
            channel.send_exit_status(done.returncode)
        except subprocess.TimeoutExpired:
            channel.sendall_stderr(b"command timed out\n")
            channel.send_exit_status(124)
        except Exception as exc:  # noqa: BLE001
            channel.sendall_stderr(str(exc).encode("utf-8"))
            channel.send_exit_status(1)
