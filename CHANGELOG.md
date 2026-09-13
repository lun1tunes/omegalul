# Changelog

Версия — одна строка в файле `VERSION`. Её же отдают `/health` сервисов (`mas_version`) и поле `mas_version` в `MAS — Runtime Config`. Пакет импорта: `python3 scripts/mas_gate.py --bundle` → `dist/mas-<версия>.zip`.

## 0.8.0 — alpha-1 — 2026-09-12

Состав альфы. F9: журнал ответа инженера только через `resume`; сборщик не читает `state.data.excel`. Interpret — HTTP thinking off. Тег `alpha-1` на коммите заморозки.

## 0.8.0+ — 2026-09-13

Excel Tools без `API_KEY` и без Header Auth на агенте (как Schedule Builder). Старый ключ в `excel-tools.env` уберите, иначе сервис снова потребует `X-API-Key`.

Лента: `agent.progress` `running` и parking `waiting_agent` с одним текстом больше не схлопываются — `watch` долгого агента виден в `GET /cases`. `ask_engineer` снимает имя файла с вопроса до проверки «машинности».

## 0.8.0+ — 2026-09-12

6.0: сборщик читает именованный набор (`CasePacket.datasets`), `search_keywords` ищет по каталогу (без `INTENT_ALIASES`), `apply_dataset` пишет keyword по `field_map` модели и схеме. Дырка в таблице — вопрос инженеру, не выдуманные записи. Live: `excel_datasets` = extract + apply + gap.

Удалён retired-контур n8n (старые оркестратор/CAS/формы/JS schedule-intake, support-заглушки кроме demo-agent). Полевой zip по-прежнему 9 core JSON. Activity: удалены in-memory `/v1/tasks*` / `/v1/turns` / `/v1/sync` / `/v1/hydrate` / `/v1/demo/seed`; живой вход — `/cases`. Оркестратор: `question_id` только `Q-${step}`, leftover плана закрывается по `expected_output` (`asked`). Interpret free-text — HTTP thinking off, без Structured Output. Ingest RAG сверяет карточки этого прогона. Excel `/api/v1` — opt-in `EXCEL_LEGACY_API`. План §2.8 и `docs.md` — одним языком: очередь, что умеет система, свободный текст на HITL. α4 принята: полевой пилот упёрся в thinking/SOP — закрыто HTTP thinking off на Decision/Verify/Interpret.

## 0.8.0 — 2026-09-11

α3 полевой пакет (E6): `scripts/field_check.py` + `check-all-windows.bat`, `VERSION` / `CHANGELOG.md`, `mas_gate.py --bundle`, `docs.md` §2 как чек-лист с ожидаемыми ответами.
