#!/usr/bin/env python3
"""Lab-only: let Docker n8n reach FastAPI processes that listen on the host.

Corporate n8n is not in this compose file — it calls the Windows/Linux host IP
directly. This machine's compose `backend` network is `internal: true`, and
container→host TCP to :18000/:8100/:8200 times out. A unix-socket pair on the
compose backend network is the workaround that does not need sudo/iptables.

Usage:
  python3 scripts/lab_docker_host_bridge.py
  python3 scripts/lab_docker_host_bridge.py --stop
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOCKDIR = Path(os.environ.get("MAS_HOST_BRIDGE_SOCKDIR", "/tmp/mas-host-socks"))
CONTAINER = os.environ.get("MAS_HOST_BRIDGE_NAME", "mas-host-bridge")
NETWORK = os.environ.get("MAS_HOST_BRIDGE_NETWORK", "omegalul_backend")
IMAGE = os.environ.get("MAS_HOST_BRIDGE_IMAGE", "python:3.11-slim")

PORTS = (
    ("excel", 18000),
    ("math", 8100),
    ("activity", 8200),
)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _unix_to_tcp(unix_path: Path, tcp_port: int) -> None:
    if unix_path.exists():
        unix_path.unlink()

    async def on_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            remote_r, remote_w = await asyncio.open_connection("127.0.0.1", tcp_port)
        except OSError as exc:
            writer.close()
            await writer.wait_closed()
            print(f"host bridge {unix_path.name} -> 127.0.0.1:{tcp_port} failed: {exc}", flush=True)
            return
        await asyncio.gather(_pipe(reader, remote_w), _pipe(remote_r, writer))

    server = await asyncio.start_unix_server(on_client, path=str(unix_path))
    os.chmod(unix_path, 0o666)
    print(f"host {unix_path} -> 127.0.0.1:{tcp_port}", flush=True)
    async with server:
        await server.serve_forever()


def run_host_side() -> None:
    SOCKDIR.mkdir(parents=True, exist_ok=True)
    async def main() -> None:
        await asyncio.gather(*[_unix_to_tcp(SOCKDIR / f"{name}.sock", port) for name, port in PORTS])

    asyncio.run(main())


CONTAINER_SCRIPT = r"""
import asyncio, os
from pathlib import Path

PORTS = (("excel", 18000), ("math", 8100), ("activity", 8200))
SOCKDIR = Path("/socks")

async def pipe(reader, writer):
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def listen(name, port):
    sock = SOCKDIR / f"{name}.sock"
    async def on_client(reader, writer):
        try:
            remote_r, remote_w = await asyncio.open_unix_connection(str(sock))
        except OSError as exc:
            print(f"container {name}:{port} -> {sock} failed: {exc}", flush=True)
            writer.close()
            await writer.wait_closed()
            return
        await asyncio.gather(pipe(reader, remote_w), pipe(remote_r, writer))
    server = await asyncio.start_server(on_client, "0.0.0.0", port)
    print(f"container 0.0.0.0:{port} -> {sock}", flush=True)
    async with server:
        await server.serve_forever()

async def main():
    await asyncio.gather(*[listen(name, port) for name, port in PORTS])

asyncio.run(main())
"""


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, text=True, capture_output=True)


def stop() -> None:
    docker("rm", "-f", CONTAINER, check=False)
    print(f"removed {CONTAINER}")


def start_container() -> None:
    docker("rm", "-f", CONTAINER, check=False)
    cp = docker(
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--network",
        NETWORK,
        "--restart",
        "unless-stopped",
        "-v",
        f"{SOCKDIR}:/socks",
        IMAGE,
        "python",
        "-c",
        CONTAINER_SCRIPT,
    )
    print(cp.stdout.strip() or cp.stderr)
    time.sleep(1)
    probe = docker(
        "exec",
        CONTAINER,
        "python",
        "-c",
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18000/health', timeout=3).read().decode())",
        check=False,
    )
    print("container self-probe:", probe.stdout.strip() or probe.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--host-only", action="store_true")
    args = parser.parse_args()
    if args.stop:
        stop()
        return 0
    if args.host_only:
        run_host_side()
        return 0

    SOCKDIR.mkdir(parents=True, exist_ok=True)
    host_proc = subprocess.Popen([sys.executable, str(Path(__file__)), "--host-only"])
    time.sleep(0.4)
    try:
        start_container()
    except Exception:
        host_proc.terminate()
        raise
    print(f"n8n URLs: http://{CONTAINER}:18000/api/v1  http://{CONTAINER}:8100/api/v1/math  http://{CONTAINER}:8200")
    print("host bridge PID", host_proc.pid, "— keep this process running")

    def _shutdown(*_args) -> None:
        stop()
        host_proc.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    return host_proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
