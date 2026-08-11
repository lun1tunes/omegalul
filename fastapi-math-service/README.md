# FastAPI Math Service (MVP)

Геометрический сервис MAS: batch intersection `.dev` × ASCII CPS3/ZMAP. Полная установка — корневой [`README.md`](../README.md); n8n Adapter — `calculation-specialist-adapter.workflow.json`.

## Windows CMD

```bat
cd fastapi-math-service
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Проверка: `http://127.0.0.1:8100/health`.

## Endpoint

`POST /api/v1/math/trajectory-intersection` (multipart):

- `trajectory_files` — одно или несколько `.dev` (`MD X Y Z`, ≥2 станции);
- `surface_file` — ASCII CPS3 (`FSNROW`/`FSLIMI`/`->GRID`) или совместимый ZMAP `@GRID`.

Calculation Adapter n8n `2.30.8` шлёт до 256 DEV за вызов; пустые reserved slots сервис игнорирует. Ответ — первое пересечение по MD на каждый файл. CRS/единицы/datum/знак Z должны совпадать на входе; сервис координаты не пересчитывает.

Auth и tNavigator runner в MVP отсутствуют: только математика → JSON.
