# Task: Оркестратор без домена — исполняемый реестр (Фаза 2, ядро: п.1–4, 6-lite, 7, 8)

Read first: `AGENTS.md` (весь), `MAS_REFACTORING_PLAN.md` §2.1 O1/O3/O4/O5, §3 п.1–2, §4 Фаза 2; правила `mas-llm-first-no-domain-hardcode.mdc`, `n8n-templates.mdc`, `mas-activity-service.mdc`.

## Goal

Оркестратор не знает ни одного агента по имени. `agent_registry` становится исполняемым: строка агента говорит, *как* его вызвать (`invoke`: n8n workflow по id или HTTP URL с плейсхолдерами `{math_url}` из Runtime Config), *что* ему нужно и *что* он даёт (`input_required` / `output_provides`, `input_schema` / `output_schema`), может ли он спрашивать инженера (`hitl_policy`), включён ли (`enabled`), версия. Один универсальный узел `Call agent (n8n)` + один `Call agent (HTTP)` вместо `Call Excel Extractor` / `Call Schedule Builder` / `Call Calculation Agent`; `Merge agent result` кладёт результат в `state.agents[<agent_id>]` без bucket'ов `excel/calc/schedule`. Промпт `SYSTEM` — только протокол (журнал → progress → действие, правила завершения); описания агентов и политика декомпозиции приходят из реестра и RAG. Новый агент = строка реестра (`upsert_agent`) + импорт его workflow — без правки/регенерации JSON оркестратора.

## Out of scope

- O13 `plan_update` со схемой/персистом/показом в Activity (следующий пункт Фазы 2).
- Activity UI «Агенты» (Фаза 4) — только API `PUT /agents/{agent_id}` для привязки.
- `unlisted_wells_policy` / `isUnlistedWellsGate` в оркестраторе — отдельный долг (O20), не трогать.
- `n8n/templates/retired/`, goldens, wipe кейсов.

## Where

- `n8n/templates/generate_mas_orchestrator.py` — `SYSTEM`, `Load agent registry` (новые колонки, `enabled`), `Prepare agent call` (резолв `invoke`), `Call agent (n8n)` (executeWorkflow 1.3, `workflowId` mode=id из выражения — проверено на lab 2.30.8), `Call agent (HTTP)`, `Agent not bound`, `MERGE` → `state.agents`, sticky note.
- `n8n/templates/mas_state_utils.py` — `buildCompact` без Excel-полей, `slimAgentData`, `resolveInvoke`, `sanitizeState` (`agents`), удалить `infer*` regex-теги.
- `n8n/templates/generate_mas_runtime_config.py` — поле `agent_workflow_ids` (JSON, пусто; полевая привязка id после UI-импорта).
- `n8n/templates/generate_mas_control_plane_proxy.py` — **новый генератор** (JSON прокси раньше правился руками): `schema` DDL `ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS …`, `list_agents`/`upsert_agent` с новыми колонками; добавить в `scripts/mas_gate.py` GENERATORS.
- `postgres-init/02-mas-control-plane.sql`, `03-schedule-builder-registry.sql`, `mas-activity-service/app/sql/control_plane.sql` — DDL + seed `invoke`.
- `mas-activity-service/app/cases_api.py` — `GET /agents` с новыми полями, `PUT /agents/{agent_id}`; `control_plane.py` memory-mode реестр.
- `schedule-builder-service/app/io.py` — факты из `state.agents[*].data` по форме (`facts`, `new_wells`), fallback `state.data.excel`.
- `n8n/rag/excel-agent-operating-guide.documents.json` — 8 карточек `orchestrator_routing` как политики (без legacy-имён и «типичных путей»), bump `revision`.
- Тесты: `mas-orchestrator-smoke.js` (промпт без имён агентов; echo_agent из реестра → route workflow с его id; merge в `state.agents`), `mas-control-plane-proxy-smoke.js`, `test_cases_api.py`, `test_state_shape.py`, Schedule Builder pytest.
- `docs.md` §1.3, §2 шаг 3, §6; `MAS_REFACTORING_PLAN.md` O1/O3/O4/O5 + Ревизия 8.

## Contracts to respect

- Инвариант 2 и 3 `AGENTS.md`: ни имён агентов, ни маршрутов, ни regex по цели в оркестраторе.
- `agent_task` / `agent_result` не меняются; агенты берут карточки из `GET /cases/{id}/state`.
- Поле: n8n 2.30.8 UI-only — привязка id агента после импорта через Set `agent_workflow_ids` в `MAS — Runtime Config` **или** `PUT /agents/{id}` (FastAPI `/docs` на Windows). Lab: CLI-импорт сохраняет id из JSON — seed реестра уже с ними.
- Детерминированное детерминированным: резолв `invoke`, лимиты, слияние.

## Evidence to start from

- `SYSTEM` строки 101–110: список агентов, «Типичный путь…», маппинг `*_specialist`.
- `ROUTE`: `if(id==='excel_extractor')…`; `MERGE`: `bucket=…`, `slimExcel`, fallbackMessage «Schedule Builder …».
- Lab-проба: workflow с `workflowId:{__rl:true,mode:'id',value:'={{ $json.target_wf }}'}` вызвал `MAS — Runtime Config` и вернул его вывод; несуществующий id → `{"error":"Workflow does not exist."}` (`continueRegularOutput`).

## Done when

- [ ] `grep -n "excel_extractor\|schedule_builder\|calculation_agent" n8n/templates/generate_mas_orchestrator.py n8n/templates/mas_state_utils.py` пуст (кроме комментариев с id кейсов).
- [ ] Smoke: `echo_agent` в `Load agent registry` с `invoke.workflow_id` → `Prepare agent call` даёт `route=workflow`, `invoke_workflow_id`; Merge кладёт `state.agents.echo_agent`.
- [ ] `python3 scripts/mas_gate.py` GREEN; `--live` GREEN 6/6, `mismatch_count: 0`, без повторных HITL/review.
- [ ] Live: `echo_agent` через `PUT /agents` + импорт шаблона вызывается оркестратором без регенерации JSON.
- [ ] План: O1/O3/O4/O5 статусы, Ревизия 8; `docs.md`.

## Forbidden

- regex вместо решения LLM; домен в промпте; правка JSON руками; `toolHttpRequest`; `$env`.
- ослаблять live-гейт; переписывать goldens.
