# Retired smokes

Тесты retired-контура (Universal Engineering Orchestrator, `excel-extraction-agent`,
`tnavigator-schedule-builder` JS-алгоритмы, CAS persist, Error Handler, handoff-контракты).
Проверяют замороженные JSON в `n8n/workflows/retired/` и `n8n/templates/retired/`.

Не входят в основной гейт `for f in n8n/tests/*-smoke.js`. Запуск при правке retired-контура:

```bash
for f in n8n/tests/retired/*.js; do node "$f" || exit 1; done
```

Живые дымы (импортируемый контур) — `n8n/tests/*-smoke.js`.
