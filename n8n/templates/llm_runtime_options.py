"""Shared Chat Model / structured-parser options for slow local LLMs (Qwen).

Omitting `timeout` is not "no timeout": n8n lmChatOpenAi defaults to 60000 ms.
`timeout: 0` is passed into LangChain `ChatOpenAI({ timeout })` and aborts the
request almost immediately (lab: Planner Agent "Request timed out" in ~2 s).
Use a large finite cap. Field UI can still clear/raise it for a slower Qwen.
maxRetries is HTTP/transport retry, not schema repair — that is the output
parser auto-fix prompt.
"""

from __future__ import annotations

# OpenAI-compatible model id (OpenRouter). Lab overlay N8N_CHAT_MODEL can still replace it.
DEFAULT_CHAT_MODEL = "qwen/qwen3.6-27b"

LLM_HTTP_MAX_RETRIES = 5
# 10 minutes — 2× the previous 5 min cap for a slower on-prem Qwen.
# Still a hang-breaker, not "wait forever". timeout: 0 aborts immediately.
LLM_HTTP_TIMEOUT_MS = 600_000
# Do not send max_tokens / maxTokens: 0 aborts like timeout:0, and raising the cap
# does not fix thinking (CASE-6aa51606-822385: 2048 and probe 8192 both left content
# empty). Decision / Verify omit the field (provider default) and turn thinking off
# (CHAT_THINKING_OFF). Agent Chat Model also omits maxTokens.
# n8n 2.30.8 lmChatOpenAi only forwards reasoningEffort low|medium|high
# (→ modelKwargs.reasoning_effort) and cannot send OpenRouter reasoning.enabled=false.
# Setting reasoningEffort *enables* thinking and is what emptied Decision content.
CHAT_THINKING_OFF = {"reasoning": {"enabled": False}, "enable_thinking": False}

STRUCTURED_FIX_PROMPT = """You are repairing structured JSON for an n8n output parser.

Instructions:
{instructions}

Previous completion (may contain markdown, <think> tags, or extra prose):
{completion}

Parser error:
{error}

Reply with ONLY one JSON object that matches the schema. No markdown fences, no <think> tags, no commentary."""


def chat_model_options(*, max_tokens: int | None = None, temperature: float = 0) -> dict:
    opts = {
        "temperature": temperature,
        "timeout": LLM_HTTP_TIMEOUT_MS,
        "maxRetries": LLM_HTTP_MAX_RETRIES,
    }
    if max_tokens is not None:
        opts["maxTokens"] = max_tokens
    return opts


def structured_parser_params(schema_json: str) -> dict:
    return {
        "schemaType": "manual",
        "inputSchema": schema_json,
        "autoFix": True,
        "customizeRetryPrompt": True,
        "prompt": STRUCTURED_FIX_PROMPT,
    }
