"""``python -m app`` — host/port come from demo-agent.env via kit dotenv, not from CMD ``for /f``."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("DEMO_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw = os.environ.get("DEMO_AGENT_PORT", "8300").strip() or "8300"
    try:
        port = int(raw)
    except ValueError:
        port = 8300
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
