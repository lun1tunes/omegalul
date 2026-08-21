"""Artifact persistence through the unified n8n control-plane proxy."""

from __future__ import annotations

import base64
import asyncio
from typing import Any

from app import control_plane


def configured() -> bool:
    return control_plane.configured()


async def put(
    case_id: str,
    artifact_id: str,
    filename: str,
    content: bytes,
    mime_type: str,
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        control_plane.proxy_call,
        "artifact_put",
        case_id=case_id,
        artifact_id=artifact_id,
        filename=filename,
        mime_type=mime_type or "application/octet-stream",
        content_base64=base64.b64encode(content).decode("ascii"),
    )
    return dict(result or {})


async def get(case_id: str, artifact_id: str) -> tuple[str, bytes, str]:
    result = await asyncio.to_thread(
        control_plane.proxy_call,
        "artifact_get",
        case_id=case_id,
        artifact_id=artifact_id,
    )
    if not isinstance(result, dict) or result.get("found") is not True:
        raise FileNotFoundError("artifact not found")
    return (
        str(result.get("filename") or artifact_id),
        base64.b64decode(str(result.get("content_base64") or "")),
        str(result.get("mime_type") or "application/octet-stream"),
    )
