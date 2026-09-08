# Demo Agent — шаблон нового агента MAS

Минимальный агент на `mas-agent-kit`, который показывает весь контракт: сессия, инструменты для LLM,
вопрос инженеру, долгая работа с результатом «позже». Скопируйте папку — и через час у вас свой агент.

```
agents-template/demo_agent/
├── app/__init__.py        # добавляет ../../mas-agent-kit в sys.path — kit это модуль репозитория, не pip-пакет
├── app/agent.py           # DemoAgent(AgentService): open_session, result, три инструмента, фоновая «долгая» работа; agent = DemoAgent()
├── app/main.py            # app = create_agent_app(agent) — /health + /agent-tools/* + /sessions/*
├── tests/                 # pytest: контракт агента через HTTP + фейковый Activity (без n8n)
├── requirements.txt       # fastapi/uvicorn/filelock — классический .venv + pip, только Windows wheels
├── demo-agent.env.example # порт, адрес Activity, длительность «расчёта»
└── setup-windows.bat / start-windows.bat / start-linux.sh
```

Вторая половина агента — его **спека** `n8n/templates/agents/demo_agent.py` (`AgentSpec`): из неё генерируются
workflow n8n (`n8n/workflows/support/demo-agent.workflow.json`), поле `demo_agent_url` в `MAS — Runtime Config`
и строка `agent_registry` (seed). Оркестратор при этом не меняется.

## Как агент разговаривает с MAS

```
n8n (Agent — Demo Agent)                      FastAPI (этот сервис)
POST /agent-tools/open_session {agent_task} → DemoAgent.open_session → {"ok":true,"session_id",...inspect}
POST /agent-tools/<tool> {session_id, args} → ToolRegistry.run → плоский ответ (ok / status / error+hint)
GET  /sessions/{id}/result                  → DemoAgent.result   → agent_result {status, message, data, requests…}
POST /sessions/{id}/close
```

`agent_result.status`:

| status | что делает оркестратор | как получить в коде |
|---|---|---|
| `completed` | пишет `data` в `state.agents.<agent_id>`, идёт дальше | `self.new_result(state, "completed", "Насчитал 3 скважины: …", data={...})` |
| `needs_input` | задаёт инженеру вопрос из `requests[0]`, ждёт ответ | инструмент `ask_engineer` → `engineer_request_from_args` |
| `in_progress` | ставит кейс в `waiting_agent` и ждёт | `in_progress(agent_id, task_id, "Расчёт запущен, около двух минут.", watch={...})` |
| `failed` | считает ошибку; 3 подряд — кейс `failed` | `self.new_result(state, "failed", "…")` |

**Долгая работа.** `start_long_job` возвращает `in_progress` и запускает поток. Поток пишет в ленту
`activity.progress("…прошло 30 секунд.", status="waiting_agent")` и по завершении отдаёт итог оркестратору:
`activity.finish_task(final_result)` → `POST /cases/{id}/run {action:resume, source:agent, agent_result}`.
Оркестратор применяет результат тем же `applyAgentResult`, что и для синхронных агентов, и продолжает план.
Файлы-результаты (xlsx, отчёты) кладите через `activity.upload(...)` — карточка вернётся в `agent_result.artifacts`.

**Ошибки аргументов — для LLM, не для инженера.** `raise ToolError("no_wells_found", "…подсказка модели…", …)`
превращается в `{"ok": false, "error": "no_wells_found", "message": "…"}`; модель исправит вызов.
Инженера спрашивает только `ask_engineer`, и только прозой: `human_text_problems` отклонит `well_column`, `key=value`, JSON.

## Сделать свой агент за 6 шагов

1. **Скопировать** `agents-template/demo_agent` → `<my-agent-service>/` (рядом с `mas-agent-kit/` — `app/__init__.py`
   ищет kit вверх по дереву, его не трогать). В `app/agent.py` переименовать класс и
   `agent_id` (snake_case), заменить инструменты. У каждого инструмента: schema для LLM (`name`, описание,
   `properties`) + функция `(ctx, args) -> dict`. Итог фиксируется `self.store_result(ctx.state, result)`.
   Когда инструментов становится много — вынести их в `app/agent_tools.py` как `@agent.tools.tool(...)`-функции
   (импорт внизу `agent.py`), как в `schedule-builder-service` и `excel-agent-tools`; `main.py` не меняется.
   Форму (класс в `agent.py`, маршруты от kit'а, инструменты через реестр) проверяет `mas-agent-kit/tests/test_service_shape.py`.
2. **Тесты** — поправить `tests/test_demo_agent.py` под свои инструменты (фейковый Activity уже есть).
3. **Спека** — скопировать `n8n/templates/agents/demo_agent.py` → `agents/<agent_id>.py`: `when_to_use` (русская
   проза — по ней Decision LLM выбирает агента), `input_required` / `output_provides`, `service_url_key`,
   `lab_url`, `system_prompt` («какой инструмент когда»), `tools` (те же имена, что маршруты `/agent-tools/<name>`),
   `texts` (фразы инженеру, когда LLM закончил без результата). Добавить в `agents/__init__.py` → `ALL`.
   Генератор — копия `generate_demo_agent.py` (для боевого агента пишите в `OUT_DIR`, не в `support`).
4. **Сгенерировать**: `cd n8n/templates && python3 generate_<agent>.py && python3 generate_mas_runtime_config.py
   && python3 generate_mas_control_plane_proxy.py && python3 generate_mas_health_check.py`. Smoke по образцу
   `n8n/tests/demo-agent-smoke.js`; `python3 scripts/mas_gate.py`.
5. **Поле (UI n8n 2.30.8 + Windows):** `setup-windows.bat`, `copy demo-agent.env.example demo-agent.env`, `start-windows.bat`;
   в n8n *Import from File* workflow агента, привязать Qwen / Runtime Config / Knowledge Retrieval (жёлтая заметка
   в workflow), вписать `<service_url_key>` = `http://<IP-Windows>:<port>` в `MAS — Runtime Config`; после UI-импорта id
   workflow новый — вписать его в `agent_workflow_ids` там же или в `invoke.workflow_id` строки агента; включить строку
   в Activity → **Агенты** (`/registry`) или `PUT /agents/<agent_id> {"enabled": true}`.
6. **Live-проверка** (lab): `python3 scripts/mas_gate.py --live --cases demo_agent` — сценарий
   `simulation-model-example/run_live_demo_agent.py` включает строку, гонит кейс через `waiting_agent` до `done`, выключает.

Чего не делать: не добавлять имя агента в промпт оркестратора; не выбирать инструмент regex'ом по тексту задачи;
не писать инженеру `snake_case`/JSON; не звать другие агенты из своего сервиса — данные предыдущих агентов уже
лежат в `packet.upstream_data()`.
