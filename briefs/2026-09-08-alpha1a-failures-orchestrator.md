# Task: make failures of the agent call and of the Decision LLM honest and recoverable (α1a: orchestrator + agent workflow)

Read first: `AGENTS.md` (all), `MAS_REFACTORING_PLAN.md` §2.7 rows E1–E3 and §4 «Альфа», `.cursor/rules/mas-llm-first-no-domain-hardcode.mdc`, `.cursor/rules/field-deployment-constraints.mdc`.

## Goal
Plan item α1 (part a): «Агентный workflow: недоступный сервис → `failed` + `service_unreachable`, а не ложный HITL. Оркестратор: `Q-parse` → `continue` с записью в журнал, второй раз → `failed` по-русски; smokes на `Q-parse` и обе ветки лимита шагов.» After this, an engineer whose Excel Tools / Schedule Builder service is not running sees «Сервис агента „Excel Extractor“ не отвечает…» and the case is `failed` (restartable), not a question «Приложите Excel» about a file that is attached; an unparseable LLM decision costs one step, not an engineer's attention.

## Out of scope
Activity UI/API (buttons, `cancelled`, feed banner) and the live harness scenarios — that is brief α1b. Do not touch the Decision prompt beyond what `Q-parse` needs; no domain words. Do not touch `retired/`.

## Where
- `n8n/templates/mas_agent_workflow.py` (`open_session` HTTP node ~132–154, `missing` branch ~334–351, `js_format_result`): distinguish «`open_session` returned `{ok:false, status:'needs_input', result}`» (real missing input → keep today's behaviour) from «no response / HTTP error / non-JSON after retries» → `agent_result {status:'failed', message:'Сервис агента „<title>“ не отвечает по адресу из настроек среды. Проверьте, что он запущен, и перезапустите задачу.', issues:[{code:'service_unreachable', url:<from Runtime Config>}]}`. The title comes from the `AgentSpec` (already in the generator), the URL key is `spec.service_url_key`. Keep English/HTTP details in `issues`, never in `message`.
- `n8n/templates/generate_mas_orchestrator.py` `PARSE_DECISION` (~804: `Q-parse`; ~992: `Q-unknown`): unparseable/unknown action → `type='continue'`, `guard='decision_unparsed'`, `ledgerPush(state,{kind:'verification',verdict:'rejected',uncovered:['решение оркестратора не разобрано']})`-style journal line in Russian so the next Decision sees it; `state.ledger.parse_failures += 1`; second consecutive failure → `case.failed` with `status_message` «Оркестратор дважды не смог сформулировать следующий шаг. Перезапустите задачу или уточните формулировку.» Both branches log the raw output in `orchestrator.decision.payload.decision` (bounded) for the developer log.
- `n8n/templates/generate_mas_orchestrator.py` `MERGE` step-limit block (~1134–1149): no behaviour change; add smokes for both branches (with a completed summary → `result_review_*` question «Оркестратор исчерпал лимит шагов…»; without → `case.failed` «Превышен лимит шагов…»).
- `n8n/templates/mas_state_utils.py`: `sanitizeLedger` gets `parse_failures` (default 0) like `verify_rejections`.
- `n8n/tests/mas-orchestrator-smoke.js`: three new blocks — `Q-parse` first time → `continue` + journal line, second time → `failed` (Russian, no `[a-z]+_[a-z_]+` in `status_message`); step limit both branches; `Q-unknown` same path as `Q-parse`.
- `n8n/tests/excel-extractor-agent-smoke.js`, `schedule-builder-agent-smoke.js` (or `demo-agent-smoke.js` if the shared generator is what you test): `open_session` node error / empty body → `status:'failed'`, `issues[0].code==='service_unreachable'`, `message` passes the machine-text gate; `{ok:false,status:'needs_input',result}` still → `needs_input` with the spec question.
- regenerate: `cd n8n/templates && python3 generate_mas_orchestrator.py && python3 generate_excel_extractor_agent.py && python3 generate_schedule_builder_agent.py && python3 generate_demo_agent.py` (or `python3 ../../scripts/mas_gate.py --only regen`).
- `docs.md` §1.3 (guards list: add `decision_unparsed`), §4 «Диагностика» (row for «Сервис агента не отвечает»), `AGENTS.md` §4 events if a new `issues.code` is introduced.

## Contracts to respect
- `agent_result {status: completed|needs_input|in_progress|failed, message, data, artifacts, issues, assumptions, requests[], watch?}`; `failed` is applied by `applyAgentResult` → `agent.failed`, `error_count` (3 → `case.failed`). A `service_unreachable` failure should count like any failure (no special casing by agent id).
- Tool/agent envelopes never carry a top-level `error` key (A15).
- Human-facing text: Russian prose, no snake_case / `key=value` / JSON / `a|b`; file names and URLs go to `issues`/`payload`, not to `message`/`status_message`.
- Orchestrator prompt knows no domain; `Q-parse` handling is generic.
- n8n 2.30.8 nodes only; no new node types (`test_workflow_contracts.py` registry).

## Evidence to start from
- `AGENTS.md` §7 «Сервис не поднялся — live-гейт показывает 6/6 одинаковый ложный HITL» and `docs.md` §4 (L1): the fake «К задаче не приложен исходный SCHEDULE» when `schedule-builder` was down.
- `MAS_REFACTORING_PLAN.md` §2.7 E1–E3 (ревизия 17) — file:line map of today's behaviour.
- Reproduce E1 in lab before editing: `docker compose stop excel-tools`, create golden 1 via Activity (`simulation-model-example/run_live_five.py --cases golden_case_1` will fail fast on «прикрепите Excel»), `python3 scripts/mas_trace_case.py CASE-… --n8n --node "Format result"`; quote the node output in the report. `docker compose start excel-tools` afterwards.

## Done when
- [ ] With `excel-tools` stopped, a new case reaches `failed` within one orchestrator step; chat shows the Russian «Сервис агента … не отвечает…»; developer log shows `issues[0].code = service_unreachable`; after `docker compose start excel-tools` and «Перезапустить с теми же файлами» the case is `done` with `schedule_out` (manual check now; the harness scenario is α1b).
- [ ] Smokes: `Q-parse`/`Q-unknown` → `continue` then `failed`; step limit both branches; agent workflow `service_unreachable` vs real `needs_input`.
- [ ] `python3 scripts/mas_gate.py` GREEN (regen without drift, 16+ smokes, 5 pytest suites, combat).
- [ ] `python3 scripts/mas_gate.py --live` GREEN 8/8, `mismatch_count: 0`, `warnings: 0` (the change touches agent workflows and the orchestrator).
- [ ] `MAS_REFACTORING_PLAN.md`: E1, E3 → ✅ with case ids; «Ревизия 18» entry; `docs.md` updated.

## Forbidden
- regex/keyword heuristics instead of an LLM decision; agent names or domain facts in the orchestrator prompt or guards
- hand-editing workflow JSON; `toolHttpRequest`; `$env`, `require`, community nodes; Docker DNS names in runtime logic
- machine or English text to the engineer; engineer writing `.INC`/JSON
- rewriting goldens; wiping cases; relaxing harness assertions; changing `CASE_STATUSES` (that is α1b)

## Report format
Files changed, counts, case ids of the live run (including the E1 reproduction case before and after), gate table, what is not done and why.
