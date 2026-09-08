"""Excel Tools FastAPI app: the agent routes (``mas_agent_kit.agent_router`` over ``agent``) behind an API key,
plus the direct ``/api/v1`` API for debugging a workbook without n8n. No LLM calls live here.

The agent itself is the n8n workflow ``Agent — Excel Extractor`` (LLM) + ``app/agent.py`` (session,
inventory, result) + ``app/agent_tools.py`` / ``app/excel_tools.py`` (tools).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status

# Native Windows/CMD launches often start Uvicorn directly instead of the .bat launcher: load the
# service-local configuration before any module-level setting is read. A real process environment
# always wins; ``excel-tools.env`` is preferred, ``.env`` stays for older deployments.
SERVICE_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(SERVICE_ROOT / "excel-tools.env", override=False)
load_dotenv(SERVICE_ROOT / ".env", override=False)

from mas_agent_kit import agent_router  # noqa: E402

from . import legacy_api  # noqa: E402
from .agent import agent  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_KEY", "")
if not API_KEY:  # fail fast rather than exposing an unauthenticated tools API by mistake
    raise RuntimeError("API_KEY must be configured")
DOCS_ENABLED = os.getenv("EXCEL_TOOLS_ENABLE_DOCS", "false").strip().casefold() == "true"


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if not isinstance(x_api_key, str) or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


app = FastAPI(
    title="Excel Tools Service",
    version="1.0.0",
    docs_url="/docs" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
    redoc_url=None,
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "agent_id": agent.agent_id, "tools": agent.tools.names}


app.include_router(agent_router(agent, dependencies=[Depends(require_api_key)]))
app.include_router(legacy_api.router, dependencies=[Depends(require_api_key)])
