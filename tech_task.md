Тебе необходимо во-первых, поднять у нас n8n локально, инфа о версии:
 n8n Version: 2.30.8
 Source Code: https://github.com/n8n-io/n8n
 Instance ID: ab650024633edbac2e231a7788ff53112b5917529ea68e5601d9e7d6c1a915af

Что должно входить:
1) Векторное хранилище (pgvector)
2) Контекстная память (PostgreSQL)
3) Эмбеддинговая модель (нода OpenAI)
3) Большие языковые модели (нода OpenAI)
Нода для агента - AI Agent.


**И первая таска - создать агента, который извлекает нужные поля из excel, используя тулзы работы с экселем локально развёрнутого сервиса на fastapi.** 

Вот указания по реализации.
> **FastAPI остается чистым сервисом инструментов.**  
> Он не вызывает Qwen, не знает про LLM, не ведет агентный цикл.  
> Он только:
>
> - принимает Excel-файл;
> - хранит сессию;
> - выполняет Python tools;
> - возвращает компактные JSON-результаты;
> - отдает артефакты.
>
> Агентный цикл живет в n8n или в вашем MAS-оркестраторе.

Ниже — подробная реализация именно этого варианта.

---

# 1. Архитектура варианта B

```text
n8n / MAS Orchestrator
   │
   │ 1. получает Excel в $binary
   │ 2. POST /api/v1/sessions
   │    multipart: file + payload
   ▼
FastAPI Tools Service
   │
   │ 3. сохраняет файл
   │ 4. создает session_id
   │ 5. отдает session_id
   │
   ▼
n8n Agent Loop
   │
   │ 6. GET /api/v1/tools
   │    получает OpenAI tool schemas
   │
   │ 7. POST Qwen /v1/chat/completions
   │    передает messages + tools
   │
   │ 8. Qwen возвращает tool_calls
   │
   │ 9. n8n вызывает FastAPI:
   │    POST /api/v1/sessions/{session_id}/tools/batch
   │
   │ 10. FastAPI выполняет Python tools
   │     возвращает результаты
   │
   │ 11. n8n добавляет tool results в messages
   │
   │ 12. снова вызывает Qwen
   │
   │ 13. цикл идет до finalize_extraction
   │
   ▼
n8n возвращает итоговый AgentOutput
```

Главное:

```text
FastAPI = deterministic tool executor + session storage
n8n = LLM orchestration + final response
Qwen = planning + tool selection
```

---

# 2. Почему вариант B хорош для MAS

Плюсы:

```text
1. FastAPI не зависит от модели.
2. Можно менять Qwen / LLM / промпты без изменения сервиса.
3. n8n управляет агентом и бизнес-логикой.
4. Другие агенты могут использовать тот же tool-сервис.
5. Легче тестировать инструменты без LLM.
6. Легче расследовать ошибки: tool logs отдельно, agent logs отдельно.
```

Минусы:

```text
1. n8n должен аккуратно вести tool loop.
2. Нужно хранить messages и состояние агента.
3. Больше сетевых вызовов.
4. Нужно обрабатывать кривые tool_calls от LLM.
```

Для MAS это нормальная цена.

---

# 3. Контракт FastAPI Tools Service

## Основные endpoints

```text
GET  /health

GET  /api/v1/tools

POST /api/v1/sessions
POST /api/v1/sessions/{session_id}/tool
POST /api/v1/sessions/{session_id}/tools/batch

GET  /api/v1/sessions/{session_id}/state

GET  /api/v1/artifacts/{session_id}/{artifact_id}
```

---

## 3.1. Загрузка файла

```http
POST /api/v1/sessions
Content-Type: multipart/form-data
X-API-Key: secret
```

Поля:

```text
file: Excel-файл
payload: JSON-строка, опционально
```

Ответ:

```json
{
  "session_id": "sess_123",
  "status": "uploaded",
  "file_size": 2097152,
  "file_hash": "sha256:..."
}
```

---

## 3.2. Получить схемы инструментов

```http
GET /api/v1/tools
X-API-Key: secret
```

Ответ:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "workbook_introspect",
        "description": "...",
        "parameters": {
          "type": "object",
          "properties": {},
          "required": []
        }
      }
    }
  ]
}
```

Эти схемы n8n передает в Qwen как `tools`.

---

## 3.3. Вызов одного инструмента

```http
POST /api/v1/sessions/sess_123/tool
Content-Type: application/json
X-API-Key: secret

{
  "name": "detect_tables",
  "args": {
    "sheet": "Заказы"
  }
}
```

Ответ:

```json
{
  "ok": true,
  "tool": "detect_tables",
  "result": {
    "tables": [
      {
        "table_id": "tbl_9f2c",
        "sheet": "Заказы",
        "header_row": 7,
        "range": "A7:K240"
      }
    ]
  }
}
```

Ошибка:

```json
{
  "ok": false,
  "tool": "detect_tables",
  "error": {
    "code": "SHEET_NOT_FOUND",
    "message": "Sheet Заказы2 not found",
    "details": {
      "available_sheets": ["Заказы", "Справочник"]
    }
  }
}
```

---

## 3.4. Пакетный вызов инструментов

Это особенно удобно, потому что Qwen может вернуть несколько `tool_calls` за раз.

```http
POST /api/v1/sessions/sess_123/tools/batch
Content-Type: application/json
X-API-Key: secret

{
  "calls": [
    {
      "call_id": "call_1",
      "name": "workbook_introspect",
      "args": {}
    },
    {
      "call_id": "call_2",
      "name": "sheet_preview",
      "args": {
        "sheet": "Заказы",
        "max_rows": 10
      }
    }
  ]
}
```

Ответ:

```json
{
  "session_id": "sess_123",
  "results": [
    {
      "call_id": "call_1",
      "name": "workbook_introspect",
      "ok": true,
      "result": {
        "sheets": [
          {
            "name": "Заказы",
            "max_row": 240,
            "max_column": 11
          }
        ]
      }
    },
    {
      "call_id": "call_2",
      "name": "sheet_preview",
      "ok": true,
      "result": {
        "sheet": "Заказы",
        "rows": []
      }
    }
  ]
}
```

---

# 4. Структура проекта для варианта B

```text
excel-agent-tools/
├── Dockerfile
├── requirements.txt
└── app/
    ├── main.py
    ├── sessions.py
    ├── tools.py
    ├── excel_tools.py
    └── state_tools.py
```

Важно:

```text
Здесь нет agent.py
Здесь нет openai
Здесь нет вызовов Qwen
```

---

# 5. requirements.txt

```text
fastapi
uvicorn
python-multipart
pydantic
pandas
openpyxl
python-calamine
xlrd
rapidfuzz
python-dateutil
pyarrow
```

Для 200 МБ позже можно добавить:

```text
duckdb
polars
xlsx2csv
```

---

# 6. app/sessions.py

Можно использовать тот же код, что и раньше.

```python
import os
import re
import json
import uuid
from pathlib import Path
from typing import Any

SESSION_DIR = Path(os.getenv("SESSION_DIR", "/data/sessions"))
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def new_session_id() -> str:
    return uuid.uuid4().hex


def session_dir(session_id: str) -> Path:
    if not SESSION_RE.match(session_id):
        raise ValueError("Invalid session_id")

    path = SESSION_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(session_id: str) -> Path:
    return session_dir(session_id) / "state.json"


def load_state(session_id: str) -> dict[str, Any]:
    path = state_path(session_id)

    if not path.exists():
        raise ValueError("Session not found")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(session_id: str, state: dict[str, Any]) -> None:
    path = state_path(session_id)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def init_state(
    session_id: str,
    file_path: str,
    file_name: str,
    file_hash: str,
    file_size: int,
    payload: dict,
) -> dict[str, Any]:
    state = {
        "session_id": session_id,
        "file_path": file_path,
        "file_name": file_name,
        "file_hash": file_hash,
        "file_size": file_size,
        "status": "uploaded",
        "payload": payload,
        "workbook_meta": {},
        "tables": {},
        "result_sets": {},
        "artifacts": {},
        "clarifications": {},
        "plan": {},
        "assumptions": [],
        "warnings": [],
        "final_output": None,
    }

    save_state(session_id, state)
    return state
```

---

# 7. app/tools.py

Это реестр инструментов.

```python
from typing import Callable, Any

TOOL_FUNCS: dict[str, Callable] = {}
TOOL_SCHEMAS: list[dict] = []


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def tool(schema: dict):
    def decorator(fn: Callable):
        name = schema["function"]["name"]
        TOOL_FUNCS[name] = fn
        TOOL_SCHEMAS.append(schema)
        return fn

    return decorator


def execute_tool(state: dict, tool_name: str, args: dict) -> dict[str, Any]:
    from sessions import save_state

    if tool_name not in TOOL_FUNCS:
        return {
            "ok": False,
            "tool": tool_name,
            "error": {
                "code": "UNKNOWN_TOOL",
                "message": f"Tool {tool_name} not found",
                "details": {
                    "available_tools": list(TOOL_FUNCS.keys())
                },
            },
        }

    ctx = {
        "state": state,
        "session_id": state["session_id"],
    }

    try:
        result = TOOL_FUNCS[tool_name](ctx, args)

        save_state(state["session_id"], state)

        return {
            "ok": True,
            "tool": tool_name,
            "result": result,
        }

    except ToolError as e:
        return {
            "ok": False,
            "tool": tool_name,
            "error": {
                "code": e.code,
                "message": e.message,
                "details": e.details,
            },
        }

    except Exception as e:
        return {
            "ok": False,
            "tool": tool_name,
            "error": {
                "code": "TOOL_EXECUTION_ERROR",
                "message": str(e),
            },
        }
```

---

# 8. app/excel_tools.py

Основные Excel-инструменты можно взять из предыдущего каркаса:

```text
workbook_introspect
sheet_preview
detect_tables
describe_table
list_column_values
query_table
validate_result
export_result
submit_clarification
```

Для варианта B они остаются такими же.

Но для чистоты варианта B важно добавить отдельные state-инструменты:

```text
get_session_state
save_agent_plan
resolve_clarification
finalize_extraction
```

Они позволяют n8n-агенту управлять состоянием без FastAPI-магии.

---

# 9. app/state_tools.py

Это критичные инструменты для варианта B.

```python
import uuid
from tools import tool, ToolError


@tool({
    "type": "function",
    "function": {
        "name": "get_session_state",
        "description": "Returns current session state: tables, results, clarifications, plan.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_tables": {
                    "type": "boolean",
                    "default": True,
                },
                "include_results": {
                    "type": "boolean",
                    "default": True,
                },
                "include_clarifications": {
                    "type": "boolean",
                    "default": True,
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
})
def get_session_state(ctx: dict, args: dict) -> dict:
    state = ctx["state"]

    include_tables = args.get("include_tables", True)
    include_results = args.get("include_results", True)
    include_clarifications = args.get("include_clarifications", True)

    safe_state = {
        "session_id": state["session_id"],
        "file_name": state["file_name"],
        "file_hash": state["file_hash"],
        "file_size": state["file_size"],
        "status": state["status"],
        "workbook_meta": state.get("workbook_meta", {}),
        "plan": state.get("plan", {}),
        "assumptions": state.get("assumptions", []),
        "warnings": state.get("warnings", []),
        "final_output": state.get("final_output"),
    }

    if include_tables:
        safe_state["tables"] = state.get("tables", {})

    if include_results:
        safe_state["result_sets"] = state.get("result_sets", {})

    if include_clarifications:
        safe_state["clarifications"] = state.get("clarifications", {})

    return safe_state


@tool({
    "type": "function",
    "function": {
        "name": "save_agent_plan",
        "description": "Saves agent plan, selected tables, field mapping, filters, assumptions.",
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "planning",
                        "executing",
                        "clarifying",
                        "finalizing",
                    ],
                },
                "selected_table_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "field_mapping": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "filters": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["plan"],
            "additionalProperties": False,
        },
    },
})
def save_agent_plan(ctx: dict, args: dict) -> dict:
    state = ctx["state"]

    state["plan"] = {
        "plan": args.get("plan"),
        "status": args.get("status"),
        "selected_table_ids": args.get("selected_table_ids", []),
        "field_mapping": args.get("field_mapping", {}),
        "filters": args.get("filters", []),
    }

    if "assumptions" in args:
        state["assumptions"] = args["assumptions"]

    if "warnings" in args:
        state["warnings"] = args["warnings"]

    return {
        "saved": True,
    }


@tool({
    "type": "function",
    "function": {
        "name": "resolve_clarification",
        "description": "Applies user's clarification answers to session state.",
        "parameters": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "answers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_id": {"type": "string"},
                            "answer": {},
                        },
                        "required": ["question_id", "answer"],
                    },
                },
            },
            "required": ["token", "answers"],
            "additionalProperties": False,
        },
    },
})
def resolve_clarification(ctx: dict, args: dict) -> dict:
    state = ctx["state"]

    token = args["token"]
    answers = args["answers"]

    if token not in state["clarifications"]:
        raise ToolError(
            code="CLARIFICATION_NOT_FOUND",
            message=f"Clarification token {token} not found",
        )

    state["clarifications"][token]["answers"] = answers
    state["clarifications"][token]["status"] = "resolved"

    return {
        "token": token,
        "status": "resolved",
    }


def normalize_final_output(args: dict) -> dict:
    status = args.get("status", "error")

    data = args.get("data") or {}

    data.setdefault("result_id", None)
    data.setdefault("artifact_ref", None)
    data.setdefault("columns", [])
    data.setdefault("records", [])
    data.setdefault("row_count", 0)
    data.setdefault("returned_count", 0)
    data.setdefault("truncated", False)
    data.setdefault("provenance", [])

    output = {
        "status": status,
        "message": args.get("message"),
        "next_action": args.get("next_action", "none"),
        "data": data,
        "filters_applied": args.get("filters_applied", []),
        "field_mapping": args.get("field_mapping", {}),
        "assumptions": args.get("assumptions", []),
        "warnings": args.get("warnings", []),
        "errors": args.get("errors", []),
        "clarification": args.get("clarification"),
        "meta": args.get("meta", {}),
    }

    return output


@tool({
    "type": "function",
    "function": {
        "name": "finalize_extraction",
        "description": "Final structured output of the Excel extraction agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "success",
                        "partial",
                        "clarification_needed",
                        "error",
                    ],
                },
                "message": {"type": ["string", "null"]},
                "next_action": {
                    "type": "string",
                    "enum": [
                        "none",
                        "ask_user",
                        "retry_with_clarification",
                        "download_artifact",
                        "handle_error",
                    ],
                    "default": "none",
                },
                "data": {"type": "object"},
                "filters_applied": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "field_mapping": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "errors": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "clarification": {"type": ["object", "null"]},
                "meta": {"type": "object"},
            },
            "required": ["status"],
            "additionalProperties": False,
        },
    },
})
def finalize_extraction(ctx: dict, args: dict) -> dict:
    state = ctx["state"]

    output = normalize_final_output(args)

    state["status"] = output["status"]
    state["final_output"] = output

    return {
        "final": True,
        "output": output,
    }
```

---

# 10. app/main.py для Tools-only FastAPI

```python
import os
import json
import hashlib
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sessions import (
    new_session_id,
    session_dir,
    init_state,
    load_state,
    save_state,
)

from tools import execute_tool, TOOL_SCHEMAS

# Регистрируем инструменты
import excel_tools  # noqa: F401
import state_tools  # noqa: F401


app = FastAPI(title="Excel Tools Service")

API_KEY = os.getenv("API_KEY", "")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "200"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024


def check_api_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/v1/tools")
def get_tools(x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    return {
        "tools": TOOL_SCHEMAS,
    }


@app.post("/api/v1/sessions")
async def create_session(
    file: UploadFile = File(...),
    payload: str = Form("{}"),
    x_api_key: Optional[str] = Header(default=None),
):
    check_api_key(x_api_key)

    session_id = new_session_id()
    sdir = session_dir(session_id)

    file_path = sdir / "input.xlsx"

    total_size = 0
    sha256 = hashlib.sha256()

    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)

            if not chunk:
                break

            total_size += len(chunk)

            if total_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail="File too large")

            f.write(chunk)
            sha256.update(chunk)

    try:
        payload_json = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")

    state = init_state(
        session_id=session_id,
        file_path=str(file_path),
        file_name=file.filename or "input.xlsx",
        file_hash=f"sha256:{sha256.hexdigest()}",
        file_size=total_size,
        payload=payload_json,
    )

    return {
        "session_id": session_id,
        "status": "uploaded",
        "file_size": total_size,
        "file_hash": state["file_hash"],
    }


class SingleToolRequest(BaseModel):
    name: str
    args: dict[str, Any] = {}


@app.post("/api/v1/sessions/{session_id}/tool")
def call_tool(
    session_id: str,
    body: SingleToolRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    check_api_key(x_api_key)

    try:
        state = load_state(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    result = execute_tool(
        state=state,
        tool_name=body.name,
        args=body.args,
    )

    save_state(session_id, state)

    return result


class ToolCallItem(BaseModel):
    call_id: str
    name: str
    args: dict[str, Any] = {}


class BatchToolRequest(BaseModel):
    calls: list[ToolCallItem]


@app.post("/api/v1/sessions/{session_id}/tools/batch")
def call_tools_batch(
    session_id: str,
    body: BatchToolRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    check_api_key(x_api_key)

    try:
        state = load_state(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    results = []

    for call in body.calls:
        tool_result = execute_tool(
            state=state,
            tool_name=call.name,
            args=call.args,
        )

        results.append({
            "call_id": call.call_id,
            "name": call.name,
            **tool_result,
        })

    save_state(session_id, state)

    return {
        "session_id": session_id,
        "results": results,
    }


@app.get("/api/v1/sessions/{session_id}/state")
def get_session_state_endpoint(
    session_id: str,
    x_api_key: Optional[str] = Header(default=None),
):
    check_api_key(x_api_key)

    try:
        state = load_state(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Не возвращаем file_path наружу, если сервис внешний
    safe_state = dict(state)
    safe_state.pop("file_path", None)

    return safe_state


@app.get("/api/v1/artifacts/{session_id}/{artifact_id}")
def download_artifact(
    session_id: str,
    artifact_id: str,
    x_api_key: Optional[str] = Header(default=None),
):
    check_api_key(x_api_key)

    try:
        state = load_state(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    artifact = state.get("artifacts", {}).get(artifact_id)

    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    path = Path(artifact["path"])

    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found")

    return FileResponse(path)
```

---

# 11. Что важно: FastAPI здесь не вызывает Qwen

В этом сервисе нет:

```python
openai
langchain
qwen
agent loop
chat.completions
```

Это хорошо.

Сервис остается переиспользуемым.

Его могут вызывать:

```text
n8n agent
другой MAS-агент
CLI
тесты
внутренний сервис
```

---

# 12. Агентный цикл в n8n

Теперь вся ответственность за LLM-цикл лежит на n8n.

## Что должен делать n8n

```text
1. Загрузить файл в FastAPI.
2. Получить session_id.
3. Получить tools из FastAPI.
4. Сформировать messages.
5. Отправить messages + tools в Qwen.
6. Если Qwen вернул tool_calls:
   - вызвать FastAPI tools/batch;
   - добавить tool results в messages;
   - повторить вызов Qwen.
7. Если Qwen вызвал finalize_extraction:
   - вернуть final output.
8. Если превышен лимит итераций:
   - вернуть ошибку.
```

---

# 13. Системный промпт для Qwen в варианте B

Этот промпт задается в n8n.

```text
Ты — агент извлечения данных из Excel.

У тебя есть доступ к инструментам сервиса Excel Tools.

Твоя задача:
1. Изучить workbook через workbook_introspect.
2. При необходимости посмотреть листы через sheet_preview.
3. Найти таблицу через detect_tables.
4. Изучить колонки через describe_table.
5. Проверить уникальные значения через list_column_values.
6. Применить фильтры через query_table.
7. Проверить результат через validate_result.
8. Если результат большой, использовать export_result.
9. Если есть неоднозначность, использовать submit_clarification.
10. В конце обязательно вызвать finalize_extraction.

Правила:
- Не выдумывай данные.
- Не возвращай большие таблицы в текстовом виде.
- Если данных нет, верни status=error или partial.
- Если нужно уточнение, верни status=clarification_needed.
- Всегда фиксируй допущения в assumptions.
- Всегда завершай работу вызовом finalize_extraction.
```

---

# 14. Формат пользовательского сообщения для Qwen

n8n отправляет Qwen не сырой Excel, а компактный JSON.

Пример:

```json
{
  "session_id": "sess_123",
  "file_name": "orders.xlsx",
  "request": {
    "fields": ["order_id", "customer", "amount"],
    "prompt": "Нужны оплаченные заказы за март 2026",
    "output_mode": "records",
    "max_records": 200
  },
  "clarification_response": null
}
```

Если пользователь ответил на уточнение:

```json
{
  "session_id": "sess_123",
  "file_name": "orders.xlsx",
  "request": {
    "fields": ["order_id", "customer", "amount"],
    "prompt": "Нужны оплаченные заказы за март 2026",
    "output_mode": "records",
    "max_records": 200
  },
  "clarification_response": {
    "token": "clr_9f2c",
    "answers": [
      {
        "question_id": "amount_column",
        "answer": "Сумма итого"
      }
    ]
  }
}
```

---

# 15. Вызов Qwen из n8n

Qwen должен быть доступен как OpenAI-compatible endpoint.

Например:

```text
POST http://qwen-server:8001/v1/chat/completions
Authorization: Bearer qwen-key
Content-Type: application/json
```

Тело:

```json
{
  "model": "qwen3.6-27b-instruct",
  "temperature": 0,
  "messages": [],
  "tools": [],
  "tool_choice": "auto"
}
```

---

# 16. Псевдокод агентного цикла для n8n Code node

Это псевдокод для понимания логики.

В зависимости от версии n8n и настроек sandbox, вместо `fetch` можно использовать HTTP Request nodes или доступный HTTP-helper.

```javascript
const TOOLS_API = "http://excel-agent:8000/api/v1";
const TOOLS_API_KEY = "secret";

const QWEN_URL = "http://qwen-server:8001/v1/chat/completions";
const QWEN_API_KEY = "qwen-secret";
const QWEN_MODEL = "qwen3.6-27b-instruct";

const MAX_ITERATIONS = 12;

const sessionId = $json.session_id;
const request = $json.request;
const clarificationResponse = $json.clarification_response || null;

// 1. Получаем инструменты из FastAPI
const toolsResponse = await fetch(`${TOOLS_API}/tools`, {
  headers: {
    "X-API-Key": TOOLS_API_KEY,
  },
});

const toolsPayload = await toolsResponse.json();
const tools = toolsPayload.tools;

// 2. Если есть ответ на уточнение, сначала применяем его
if (clarificationResponse) {
  await fetch(`${TOOLS_API}/sessions/${sessionId}/tools/batch`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": TOOLS_API_KEY,
    },
    body: JSON.stringify({
      calls: [
        {
          call_id: "resolve_clarification",
          name: "resolve_clarification",
          args: clarificationResponse,
        },
      ],
    }),
  });
}

// 3. Формируем стартовые messages
let messages = [
  {
    role: "system",
    content: `
Ты — агент извлечения данных из Excel.
Используй инструменты.
Всегда завершай работу вызовом finalize_extraction.
Если нужно уточнение, вызови submit_clarification, затем finalize_extraction со status=clarification_needed.
    `.trim(),
  },
  {
    role: "user",
    content: JSON.stringify({
      session_id: sessionId,
      request,
      clarification_response: clarificationResponse,
    }),
  },
];

let finalOutput = null;

for (let i = 0; i < MAX_ITERATIONS; i++) {

  // 4. Вызываем Qwen
  const qwenResponse = await fetch(QWEN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${QWEN_API_KEY}`,
    },
    body: JSON.stringify({
      model: QWEN_MODEL,
      temperature: 0,
      messages,
      tools,
      tool_choice: "auto",
    }),
  });

  const qwenPayload = await qwenResponse.json();

  const assistantMessage = qwenPayload.choices[0].message;

  // Обязательно сохраняем assistant message как есть,
  // включая tool_calls.
  messages.push(assistantMessage);

  const toolCalls = assistantMessage.tool_calls || [];

  // 5. Если нет tool_calls, пытаемся распарсить финальный JSON
  if (toolCalls.length === 0) {
    try {
      finalOutput = JSON.parse(assistantMessage.content);
      break;
    } catch (e) {
      finalOutput = {
        status: "error",
        message: "Model returned invalid JSON and no tool calls",
        raw: assistantMessage.content,
      };
      break;
    }
  }

  // 6. Собираем вызовы инструментов для FastAPI
  const calls = [];

  for (const toolCall of toolCalls) {
    let args = {};

    try {
      args = JSON.parse(toolCall.function.arguments || "{}");
    } catch (e) {
      args = {
        error: "Invalid tool arguments JSON",
        raw: toolCall.function.arguments,
      };
    }

    calls.push({
      call_id: toolCall.id,
      name: toolCall.function.name,
      args,
    });
  }

  // 7. Вызываем FastAPI batch
  const toolResponse = await fetch(
    `${TOOLS_API}/sessions/${sessionId}/tools/batch`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": TOOLS_API_KEY,
      },
      body: JSON.stringify({ calls }),
    }
  );

  const toolPayload = await toolResponse.json();

  // 8. Добавляем результаты инструментов в messages
  for (const toolResult of toolPayload.results) {
    messages.push({
      role: "tool",
      tool_call_id: toolResult.call_id,
      content: JSON.stringify(toolResult),
    });

    // 9. Если был finalize_extraction, забираем итог
    if (toolResult.name === "finalize_extraction" && toolResult.ok) {
      finalOutput = toolResult.result.output;
    }
  }

  if (finalOutput) {
    break;
  }
}

if (!finalOutput) {
  finalOutput = {
    status: "error",
    message: "Agent loop exceeded max iterations",
  };
}

return [{ json: finalOutput }];
```

---

# 17. Если Code node с fetch недоступен

Тогда цикл собирают из нод.

Примерная схема:

```text
Webhook / Form Trigger
   ↓
Set session_id
   ↓
HTTP Request: POST /api/v1/sessions
   ↓
HTTP Request: GET /api/v1/tools
   ↓
Set initial messages
   ↓
Loop Start
   ↓
HTTP Request: POST Qwen /v1/chat/completions
   ↓
Code: normalize Qwen response
   ↓
IF has_tool_calls
   ├── true
   │     ↓
   │   Split tool_calls
   │     ↓
   │   HTTP Request: POST /sessions/{id}/tools/batch
   │     ↓
   │   Code: append tool messages
   │     ↓
   │   back to Loop Start
   │
   └── false
         ↓
      Code: parse final JSON
         ↓
      Respond to Webhook
```

На практике удобнее вынести агентный цикл в отдельный n8n subworkflow.

---

# 18. Порядок успешного взаимодействия

Пример по шагам.

## Шаг 1. n8n загружает файл

```http
POST /api/v1/sessions
```

Ответ:

```json
{
  "session_id": "sess_123",
  "status": "uploaded"
}
```

---

## Шаг 2. n8n получает tools

```http
GET /api/v1/tools
```

---

## Шаг 3. n8n вызывает Qwen

Qwen видит:

```json
{
  "session_id": "sess_123",
  "request": {
    "fields": ["order_id", "customer", "amount"],
    "prompt": "Нужны оплаченные заказы за март 2026"
  }
}
```

---

## Шаг 4. Qwen вызывает workbook_introspect

n8n вызывает FastAPI:

```json
{
  "calls": [
    {
      "call_id": "call_1",
      "name": "workbook_introspect",
      "args": {}
    }
  ]
}
```

FastAPI:

```json
{
  "results": [
    {
      "call_id": "call_1",
      "name": "workbook_introspect",
      "ok": true,
      "result": {
        "sheets": [
          {
            "name": "Заказы",
            "max_row": 240,
            "max_column": 11
          }
        ]
      }
    }
  ]
}
```

---

## Шаг 5. Qwen вызывает detect_tables

```json
{
  "calls": [
    {
      "call_id": "call_2",
      "name": "detect_tables",
      "args": {
        "sheet": "Заказы"
      }
    }
  ]
}
```

FastAPI:

```json
{
  "results": [
    {
      "call_id": "call_2",
      "name": "detect_tables",
      "ok": true,
      "result": {
        "tables": [
          {
            "table_id": "tbl_9f2c",
            "sheet": "Заказы",
            "header_row": 7,
            "range": "A7:K240"
          }
        ]
      }
    }
  ]
}
```

---

## Шаг 6. Qwen вызывает describe_table

```json
{
  "calls": [
    {
      "call_id": "call_3",
      "name": "describe_table",
      "args": {
        "table_id": "tbl_9f2c"
      }
    }
  ]
}
```

FastAPI возвращает колонки и примеры.

---

## Шаг 7. Qwen вызывает list_column_values

Например, для статуса:

```json
{
  "calls": [
    {
      "call_id": "call_4",
      "name": "list_column_values",
      "args": {
        "table_id": "tbl_9f2c",
        "column": "статус",
        "limit": 50
      }
    }
  ]
}
```

---

## Шаг 8. Qwen вызывает query_table

```json
{
  "calls": [
    {
      "call_id": "call_5",
      "name": "query_table",
      "args": {
        "table_id": "tbl_9f2c",
        "select": ["заказ №", "контрагент", "сумма итого", "статус"],
        "filters": [
          {
            "field": "статус",
            "operator": "in",
            "value": ["Оплачен", "Отгружен"]
          }
        ],
        "limit": 200
      }
    }
  ]
}
```

FastAPI:

```json
{
  "results": [
    {
      "call_id": "call_5",
      "name": "query_table",
      "ok": true,
      "result": {
        "result_id": "res_5512",
        "row_count": 2,
        "stored_rows": 2,
        "preview_rows": [
          {
            "заказ №": "Z-1045",
            "контрагент": "ООО Ромашка",
            "сумма итого": 15800.5,
            "статус": "Оплачен"
          }
        ],
        "truncated": false
      }
    }
  ]
}
```

---

## Шаг 9. Qwen вызывает finalize_extraction

```json
{
  "calls": [
    {
      "call_id": "call_6",
      "name": "finalize_extraction",
      "args": {
        "status": "success",
        "message": "Извлечено 2 заказа",
        "next_action": "none",
        "data": {
          "result_id": "res_5512",
          "columns": [],
          "records": [],
          "row_count": 2,
          "returned_count": 2,
          "truncated": false
        },
        "field_mapping": {
          "order_id": "заказ №",
          "customer": "контрагент",
          "amount": "сумма итого"
        },
        "assumptions": [
          "Колонка 'контрагент' использована как customer"
        ]
      }
    }
  ]
}
```

FastAPI:

```json
{
  "results": [
    {
      "call_id": "call_6",
      "name": "finalize_extraction",
      "ok": true,
      "result": {
        "final": true,
        "output": {
          "status": "success",
          "message": "Извлечено 2 заказа",
          "next_action": "none",
          "data": {
            "result_id": "res_5512",
            "columns": [],
            "records": [],
            "row_count": 2,
            "returned_count": 2,
            "truncated": false
          },
          "field_mapping": {
            "order_id": "заказ №",
            "customer": "контрагент",
            "amount": "сумма итого"
          },
          "assumptions": [
            "Колонка 'контрагент' использована как customer"
          ]
        }
      }
    }
  ]
}
```

n8n забирает:

```json
results[0].result.output
```

и возвращает его как ответ агента.

---

# 19. Сценарий уточнения в варианте B

## Первый запуск

Qwen понимает, что есть неоднозначность.

Он вызывает:

```json
{
  "name": "submit_clarification",
  "args": {
    "questions": [
      {
        "id": "amount_column",
        "question": "Какую колонку использовать как сумму?",
        "type": "choice",
        "options": ["Сумма", "Сумма итого", "Сумма НДС"]
      }
    ]
  }
}
```

FastAPI сохраняет:

```json
{
  "token": "clr_9f2c",
  "status": "clarification_needed"
}
```

Затем Qwen вызывает:

```json
{
  "name": "finalize_extraction",
  "args": {
    "status": "clarification_needed",
    "message": "Нужно уточнить колонку суммы",
    "next_action": "ask_user",
    "clarification": {
      "token": "clr_9f2c",
      "questions": [
        {
          "id": "amount_column",
          "question": "Какую колонку использовать как сумму?",
          "type": "choice",
          "options": ["Сумма", "Сумма итого", "Сумма НДС"]
        }
      ]
    }
  }
}
```

n8n возвращает это пользователю.

---

## Ответ пользователя

Пользователь отвечает:

```json
{
  "session_id": "sess_123",
  "clarification_response": {
    "token": "clr_9f2c",
    "answers": [
      {
        "question_id": "amount_column",
        "answer": "Сумма итого"
      }
    ]
  }
}
```

n8n делает:

```text
1. Не загружает файл заново.
2. Вызывает resolve_clarification.
3. Вызывает get_session_state.
4. Передает это Qwen.
5. Qwen продолжает с того же места.
```

Пример первого batch после ответа:

```json
{
  "calls": [
    {
      "call_id": "resolve_1",
      "name": "resolve_clarification",
      "args": {
        "token": "clr_9f2c",
        "answers": [
          {
            "question_id": "amount_column",
            "answer": "Сумма итого"
          }
        ]
      }
    },
    {
      "call_id": "state_1",
      "name": "get_session_state",
      "args": {}
    }
  ]
}
```

---

# 20. Как n8n должен обрабатывать ошибки инструментов

FastAPI всегда должен возвращать структурированную ошибку:

```json
{
  "ok": false,
  "tool": "query_table",
  "error": {
    "code": "COLUMN_NOT_FOUND",
    "message": "Column amount not found",
    "details": {
      "available_columns": ["Сумма", "Сумма итого"]
    }
  }
}
```

n8n не должен падать при `ok: false`.

Он должен передать этот JSON обратно Qwen как `tool` message.

Тогда Qwen может исправить запрос:

```text
было:
  field: amount

стало:
  field: сумма итого
```

---

# 21. Что n8n должен считать финалом

Рекомендую жесткое правило:

> **Финал — это вызов инструмента `finalize_extraction`.**

Если Qwen просто вернул текст без tool call, n8n может:

1. попробовать распарсить JSON;
2. если не получилось — отправить Qwen повторное сообщение:

```text
You must finish by calling finalize_extraction tool with valid structured output.
```

3. если снова не получилось — вернуть ошибку.

Пример retry-сообщения:

```json
{
  "role": "user",
  "content": "You did not call finalize_extraction. Call finalize_extraction now with valid JSON output."
}
```

---

# 22. Лимиты агентного цикла в n8n

Обязательно ограничьте:

```text
MAX_ITERATIONS = 12
MAX_TOOL_CALLS = 30
MAX_QWEN_TIMEOUT = 120 sec
MAX_TOOL_TIMEOUT = 120 sec
```

Если лимит превышен:

```json
{
  "status": "error",
  "message": "Agent exceeded max iterations",
  "next_action": "handle_error"
}
```

---

# 23. Как возвращать большие результаты

Если `query_table` вернул:

```json
{
  "row_count": 15230,
  "stored_rows": 100,
  "truncated": true
}
```

Qwen должен вызвать:

```json
{
  "name": "export_result",
  "args": {
    "result_id": "res_5512",
    "format": "csv"
  }
}
```

FastAPI:

```json
{
  "ok": true,
  "tool": "export_result",
  "result": {
    "artifact_id": "art_88cd",
    "format": "csv",
    "row_count": 15230
  }
}
```

Затем Qwen вызывает:

```json
{
  "name": "finalize_extraction",
  "args": {
    "status": "success",
    "next_action": "download_artifact",
    "data": {
      "artifact_ref": "art_88cd",
      "row_count": 15230,
      "returned_count": 0,
      "truncated": true,
      "columns": [],
      "records": []
    }
  }
}
```

n8n потом может скачать:

```http
GET /api/v1/artifacts/sess_123/art_88cd
```

и положить файл в `$binary.result`.

---

# 24. Что важно для 200 МБ в варианте B

Для n8n ничего принципиально не меняется:

```text
n8n все равно вызывает tools.
```

Но FastAPI внутри должен быть умнее.

Для 200 МБ:

```text
upload → stream to disk
detect_tables → streaming/openpyxl/calamine
read_table_df → не читать весь xlsx в pandas
query_table → DuckDB/Polars/SQL
export_result → CSV/Parquet artifact
```

n8n по-прежнему получает только:

```json
{
  "result_id": "res_5512",
  "row_count": 15230,
  "preview_rows": []
}
```

---

# 25. Безопасность варианта B

## FastAPI

Должен:

```text
проверять X-API-Key
валидировать session_id
не принимать произвольные пути
не выполнять произвольный Python
не иметь доступа в интернет, если не нужно
чистить сессии по TTL
логировать только метаданные
```

---

## n8n

Должен:

```text
хранить Qwen API key в credentials
хранить FastAPI API key в credentials
не логировать base64 Excel
не передавать файл в Qwen
не передавать большие таблицы в Qwen
ограничивать число итераций
обрабатывать ошибки Qwen
обрабатывать ошибки FastAPI
```

---

# 26. Чем вариант B лучше для тестирования

Вы можете тестировать FastAPI вообще без LLM.

Например:

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "X-API-Key: secret" \
  -F "file=@orders.xlsx" \
  -F 'payload={}'
```

Потом:

```bash
curl -X POST http://localhost:8000/api/v1/sessions/sess_123/tool \
  -H "X-API-Key: secret" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "workbook_introspect",
    "args": {}
  }'
```

Потом:

```bash
curl -X POST http://localhost:8000/api/v1/sessions/sess_123/tool \
  -H "X-API-Key: secret" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "detect_tables",
    "args": {
      "sheet": "Заказы"
    }
  }'
```

Это огромное преимущество.

Вы можете прогнать весь Excel-пайплайн без Qwen.

---

# 27. Рекомендуемый контракт между n8n и FastAPI

## n8n отвечает за:

```text
binary
session upload
Qwen messages
Qwen tool loop
final response to user
clarification UX
business routing
```

## FastAPI отвечает за:

```text
file storage
session state
Excel parsing
table detection
filters
results
artifacts
tool schemas
tool errors
```

---

# 28. Итоговый TL;DR по варианту B

Если FastAPI должен быть просто сервисом инструментов, делайте так:

```text
1. FastAPI не вызывает Qwen.
2. FastAPI хранит Excel и session state.
3. FastAPI отдает schemas: GET /api/v1/tools.
4. FastAPI выполняет tools:
   POST /api/v1/sessions/{session_id}/tool
   POST /api/v1/sessions/{session_id}/tools/batch
5. n8n получает tools.
6. n8n вызывает Qwen с messages + tools.
7. Qwen возвращает tool_calls.
8. n8n вызывает FastAPI tools/batch.
9. n8n возвращает tool results в Qwen.
10. Цикл повторяется.
11. Qwen обязан вызвать finalize_extraction.
12. n8n возвращает result.output пользователю.
```

Минимальный набор инструментов:

```text
workbook_introspect
sheet_preview
detect_tables
describe_table
list_column_values
query_table
validate_result
export_result
get_session_state
save_agent_plan
submit_clarification
resolve_clarification
finalize_extraction
```

Главная архитектурная мысль:

```text
FastAPI = надежные детерминированные руки.
n8n = оркестратор.
Qwen = мозг.
Excel не попадает в LLM-контекст.
```
