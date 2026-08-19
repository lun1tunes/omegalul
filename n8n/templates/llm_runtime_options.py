"""Shared Chat Model / structured-parser options for slow local LLMs (Qwen).

Omitting `timeout` is not "no timeout": n8n lmChatOpenAi defaults to 60000 ms.
`timeout: 0` is passed into LangChain `ChatOpenAI({ timeout })` and aborts the
request almost immediately (lab: Planner Agent "Request timed out" in ~2 s).
Use a large finite cap. Field UI can still clear/raise it for a slower Qwen.
maxRetries is HTTP/transport retry, not schema repair — that is the output
parser auto-fix prompt.
"""

from __future__ import annotations

LLM_HTTP_MAX_RETRIES = 5
# 5 minutes — above n8n's 60 s default, below "hang forever".
LLM_HTTP_TIMEOUT_MS = 300_000

STRUCTURED_FIX_PROMPT = """You are repairing structured JSON for an n8n output parser.

Instructions:
{instructions}

Previous completion (may contain markdown, <think> tags, or extra prose):
{completion}

Parser error:
{error}

Reply with ONLY one JSON object that matches the schema. No markdown fences, no <think> tags, no commentary."""


def chat_model_options(*, max_tokens: int, temperature: float = 0) -> dict:
    return {
        "maxTokens": max_tokens,
        "temperature": temperature,
        "timeout": LLM_HTTP_TIMEOUT_MS,
        "maxRetries": LLM_HTTP_MAX_RETRIES,
    }


def structured_parser_params(schema_json: str) -> dict:
    return {
        "schemaType": "manual",
        "inputSchema": schema_json,
        "autoFix": True,
        "customizeRetryPrompt": True,
        "prompt": STRUCTURED_FIX_PROMPT,
    }
