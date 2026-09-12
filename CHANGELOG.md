# Changelog

Версия — одна строка в файле `VERSION`. Её же отдают `/health` сервисов (`mas_version`) и поле `mas_version` в `MAS — Runtime Config`. Пакет импорта: `python3 scripts/mas_gate.py --bundle` → `dist/mas-<версия>.zip`.

## 0.8.0+ — 2026-09-12

Удалён retired-контур n8n (старые оркестратор/CAS/формы/JS schedule-intake, support-заглушки кроме demo-agent). Полевой zip по-прежнему 9 core JSON. Activity: удалены in-memory `/v1/tasks*` / `/v1/turns` / `/v1/sync` / `/v1/hydrate` / `/v1/demo/seed`; живой вход — `/cases`.

## 0.8.0 — 2026-09-11

α3 полевой пакет (E6): `scripts/field_check.py` + `check-all-windows.bat`, `VERSION` / `CHANGELOG.md`, `mas_gate.py --bundle`, `docs.md` §2 как чек-лист с ожидаемыми ответами.
