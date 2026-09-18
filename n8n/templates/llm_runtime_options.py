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
# (CHAT_THINKING_OFF). Interpret uses the same HTTP body. Agent Chat Model also omits maxTokens.
# n8n 2.30.8 lmChatOpenAi only forwards reasoningEffort low|medium|high
# (→ modelKwargs.reasoning_effort) and cannot send OpenRouter reasoning.enabled=false.
# Setting reasoningEffort *enables* thinking and is what emptied Decision content.
# Three independent off-switches: OpenRouter (`reasoning.enabled`), Model Studio
# (`enable_thinking`), vLLM/SGLang (`chat_template_kwargs.enable_thinking`). Unknown
# keys are ignored. Qwen3.6 has no `/no_think` soft switch (plan Q2).
CHAT_THINKING_OFF = {
    "reasoning": {"enabled": False},
    "enable_thinking": False,
    "chat_template_kwargs": {"enable_thinking": False},
}

# Per-role sampling (plan 7.4). Decision/Verify/Interpret stay low-T JSON.
# Agent loop uses the model-card non-thinking profile. Revisit after `--live --repeat 3`.
SAMPLING = {
    "decision": {"temperature": 0.2, "top_p": 0.9},
    "agent": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
}

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


# Inlined into n8n Code nodes (no require). Field Set `chat_extra_params` is a JSON object
# string; it overlays SAMPLING / CHAT_THINKING_OFF (so a corp vLLM can add top_k etc.)
# but cannot replace messages / tools / tool_choice.
PARSE_CHAT_EXTRA_JS = r"""
function parseChatExtra(cfg){
  const raw=cfg&&cfg.chat_extra_params;
  let parsed={};
  if(raw&&typeof raw==='object'&&!Array.isArray(raw)) parsed=raw;
  else {
    const s=String(raw||'').trim();
    if(s){
      try{const p=JSON.parse(s); if(p&&typeof p==='object'&&!Array.isArray(p)) parsed=p;}catch(e){parsed={};}
    }
  }
  const out={...parsed};
  delete out.messages;
  delete out.tools;
  delete out.tool_choice;
  return out;
}
""".strip()

# Structured prompt_preview for trace.llm: role+clipped content, system once, later turns
# are a delta from the previous send (fromIdx). Old string previews stay valid in the UI.
PREVIEW_CHAT_MESSAGES_JS = r"""
function previewChatMessages(msgs, fromIdx){
  const CONTENT=600;
  const KEEP=10;
  const list=Array.isArray(msgs)?msgs:[];
  const slim=(m)=>{
    const raw=m&&typeof m==='object'&&!Array.isArray(m)?m:{};
    const c=raw.content;
    let text='';
    if(typeof c==='string') text=c;
    else if(c==null) text='';
    else { try{text=JSON.stringify(c);}catch(e){text=String(c);} }
    if(text.length>CONTENT) text=text.slice(0,CONTENT-1)+'…';
    const out={role:String(raw.role||''),content:text};
    if(raw.tool_call_id) out.tool_call_id=String(raw.tool_call_id);
    if(Array.isArray(raw.tool_calls)&&raw.tool_calls.length) out.tool_calls=raw.tool_calls.length;
    return out;
  };
  const all=list.map(slim);
  const start=Math.max(0,Number(fromIdx)||0);
  let picked=start>0?all.slice(start):all.slice();
  let omitted=0;
  if(start>0&&all[0]&&all[0].role==='system'){
    omitted=Math.max(0,start-1);
    picked=[all[0],...(omitted?[{role:'omitted',count:omitted}]:[]),...picked];
  }
  if(picked.length>KEEP){
    omitted += picked.length-KEEP;
    picked=[picked[0],{role:'omitted',count:omitted},...picked.slice(-(KEEP-2))];
  }
  const preview={messages:picked};
  if(omitted) preview.omitted=omitted;
  return preview;
}
""".strip()
