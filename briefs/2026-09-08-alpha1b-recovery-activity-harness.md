# Task: let the engineer recover a failed or stuck case from the UI, and prove recovery with live cases (α1b: Activity + harness)

Read first: `AGENTS.md` (all), `MAS_REFACTORING_PLAN.md` §2.7 rows E2, E4, E5 and §4 «Альфа», `.cursor/rules/field-deployment-constraints.mdc`, `.cursor/rules/mas-activity-service.mdc` (loads when you open Activity files), `.cursor/rules/live-harness.mdc`.

Depends on brief α1a being merged (service down → `failed`, `service_unreachable`). Run `python3 scripts/mas_gate.py` first; if RED, stop and report.

## Goal
Plan item α1 (part b): «Activity: `feed.status_message` для `failed`; русские тексты в `_invoke_action`; статус `cancelled` (`CASE_STATUSES` + CHECK); кнопки «Продолжить» (`resume source=system` для зависших `running`/`waiting_agent`) и «Закрыть задачу»; перезапуск чистит `agents`/`ledger`/`plan`. Харнесс: `agent_down_recovery`, `rework_round`, `step_limit_review`.» After this, every failure path an engineer can hit in the field ends in a Russian sentence and a button that works, and the live gate proves it.

## Out of scope
Orchestrator/agent-workflow behaviour (α1a). Metrics, thresholds, `--repeat` (α2). Field scripts, VERSION, bundle (α3). UI redesign — reuse the existing composer states and pills.

## Where
- `mas-activity-service/app/contracts.py` (`CASE_STATUSES`, `RESTARTABLE_STATUSES` in `cases_api.py:50–51`): add `cancelled`; it is terminal and restartable. Twin: `n8n/templates/generate_mas_control_plane_proxy.py` `CASE_STATUSES` → `schema` recreates `cases_status_check` (K6; test `test_cases_status_check_accepts_every_case_status` must include it). Orchestrator `sanitizeState`/status pills in `static/app.js` (`:127`, `:405`, `:148`) get the new status: pill «Закрыта», neutral tone.
- `mas-activity-service/app/cases_api.py`:
  - `POST /cases/{id}/run` `action=cancel` → status `cancelled`, event `case.cancelled` («Задача закрыта инженером»), clears `hitl.pending`/`current_task`; allowed from `running|waiting_user|waiting_agent|failed`. New event kind → `EVENT_KINDS` in `contracts.py` **and** `mas-agent-kit/mas_agent_kit/activity.py`; `case_log.record_level` → `info`.
  - `action=resume source=system` from the UI for `running`/`waiting_agent` (~`:956–974`): allowed when the last event is older than `RESUME_STALE_S` (settings, default 120 s) — otherwise 409 with a Russian message («Оркестратор ещё работает, подождите…»). Event `orchestrator.resume` already exists.
  - `_prepare_case_restart` (`:924–944`): also reset `agents`, `ledger`, `plan`, `hitl.questions/answers` (keep goal, name, artifacts `kind=input`). Event text «Перезапуск задачи с исходными файлами».
  - `_feed_from_row` (`:397–419`): add `status_message` = the last `case.failed` / `case.cancelled` / `case.finished` / `agent.failed` `status_message`, so the banner never shows a code.
  - `_invoke_action` (`:525–538`): `case.failed` `status_message` «Не удалось передать задачу оркестратору. Проверьте адрес оркестратора в настройках и повторите.»; `str(exc)` → `payload.detail`.
- `mas-activity-service/static/app.js`, `index.html`, `app.css`: buttons «Продолжить» (visible for `running`/`waiting_agent` when stale) and «Закрыть задачу» (with confirm) next to «Перезапустить с теми же файлами» (`index.html:252–254`, `app.js:652–658`, `:1848–1864`); banner uses `feed.status_message`; idle text for `cancelled`. Bump `?v=` asset versions (tests check them — `test_cases_api.py`).
- `mas-activity-service/tests/test_cases_api.py`: cancel from each allowed status; resume-stale 409 then 200; restart clears journal/plan; feed `status_message` for `failed`; `_invoke_action` Russian text. `tests/test_case_log.py`: `case.cancelled` level/title.
- `simulation-model-example/run_live_demo_agent.py` `SCENARIOS` (reuse `run_live_five` helpers; no new harness file):
  - `agent_down_recovery`: `docker compose stop excel-tools` (lab-only helper, clearly labelled; skip with a message if Docker is unavailable) → golden 1 case → wait `failed` with `service_unreachable` in the log and a Russian `status_message` → `docker compose start excel-tools` + wait `/health` → `POST /run {action:"retry"}` → `done`, `schedule_out` semantically equal to golden 1. **Always restart the service in `finally`.**
  - `rework_round`: the only way to reach a review gate deterministically today is the step limit, so temporarily set Runtime Config `max_steps` = 2 (lab: the same Set-node value `lab_soft_redeploy.py` already writes; field-neutral) and run golden 1: after Excel the limit is hit → `result_review_*` question → answer `{choice:'rework', text:'Обновите schedule по извлечённым датам ввода'}` → the Builder is called with `rework_reason` (assert `agent_task.inputs.rework_reason` in the log and the ledger row) → `done` with `schedule_out` equal to golden 1. Restore `max_steps` in `finally`. Do not invent new API for this.
  - `step_limit_review`: same `max_steps=2` on golden 1 → review question text starts with «Оркестратор исчерпал лимит шагов» → answer `accept` → case `done` with the deliverables that exist (no `schedule_out` expected here — set `expects: []`); assert `summary.warnings` ≤ 1 (the guard is expected) and every human text passes the machine-text gate.
  - Every scenario: `human_text_problems` on all `status_message`/`hitl.request` texts — English or codes = failure.
- `scripts/mas_gate.py`: the three scenarios run in `--live` after `three_agent_chain`; `--cases` accepts them individually.
- `docs.md` §1.3 (statuses incl. `cancelled`), §1.5 (event `case.cancelled`), §4 «Диагностика» (what the engineer does on «Сбой»: перезапустить / продолжить / закрыть), §5 (harness list: 11 live cases). `AGENTS.md` §4 events line and map row for `simulation-model-example/`.

## Contracts to respect
- Statuses: `CASE_STATUSES` in Activity `contracts.py` ↔ `generate_mas_control_plane_proxy.py` (CHECK recreated by `schema`); a new status must appear in both and in `test_cases_status_check_accepts_every_case_status`.
- Write-path: the human answers only via `POST /cases/{id}/answer`; `POST /run action=resume source=human` stays 400. `source=system` is what the «Продолжить» button sends.
- Events: new kinds in both `EVENT_KINDS`; payloads from Activity carry no `execution_id` (that is fine — `execRef` is n8n-side).
- Human-facing text: Russian prose, no snake_case / `key=value` / JSON / English; technical detail → `payload.detail`.
- Field: everything the button does must be reachable through Activity → n8n webhook with Header Auth; no Docker names in runtime code. The harness's `docker compose` calls are lab-only and must be labelled so.

## Evidence to start from
- `MAS_REFACTORING_PLAN.md` §2.7 E2, E4, E5 (ревизия 17) — today's file:line map.
- Before editing, reproduce E4 in lab: stop Activity's n8n URL (`ORCHESTRATOR_URL` wrong in `.env`, restart `mas-activity`) → create a case → banner shows `failed`/English; `GET /cases/{id}` shows no `status_message`. Restore `.env`.
- Live gate baseline from ревизия 16: 8/8 GREEN, `warnings: 0` (`CASE-6aa01323-c73ff1` … `CASE-6aa01577-887fc5`).

## Done when
- [ ] `agent_down_recovery`, `rework_round`, `step_limit_review` are in `mas_gate.py --live` and GREEN; the service is running again after the gate regardless of outcome.
- [ ] UI: «Продолжить» wakes a stale `waiting_agent` (check with `demo_agent_long_job` by killing the demo service mid-job — manual), «Закрыть задачу» → `cancelled` pill; banner for `failed` shows the Russian sentence from `feed.status_message`.
- [ ] `python3 scripts/mas_gate.py` GREEN; `python3 scripts/mas_gate.py --live` GREEN 11/11, `mismatch_count: 0`, `warnings: 0` on the eight original cases.
- [ ] `MAS_REFACTORING_PLAN.md`: E2, E4, E5 → ✅ with case ids, α1 marked ✅; «Ревизия 18/19» entry; `docs.md` and `AGENTS.md` updated.

## Forbidden
- new heuristics in the orchestrator; changing Decision/Verify prompts (α1a already did the `Q-parse` part)
- hand-editing workflow JSON; new n8n node types; `$env`; Docker names in runtime code
- machine or English text to the engineer; a «cancel» that leaves the Postgres CHECK behind
- rewriting goldens; wiping cases; relaxing existing harness assertions to make the new scenarios pass

## Report format
Files changed, counts, case ids of the live run (11), gate table, what is not done and why.
