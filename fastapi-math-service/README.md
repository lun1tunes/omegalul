# FastAPI Math Service (MVP)

Геометрический сервис MAS: batch intersection `.dev` × ASCII CPS3/ZMAP.  
**На работе:** только Windows CMD. Полная установка — [`docs.md`](../docs.md) §3; n8n Agent — UI-импорт `calculation-specialist-agent.workflow.json`.

## Windows CMD (канон)

```bat
cd fastapi-math-service
setup-windows.bat
copy math-service.env.example math-service.env
start-windows.bat
```

Проверка во втором CMD: `check-windows.bat`.

Локально: `http://127.0.0.1:8100/health`. Для корпоративного n8n: `MATH_SERVICE_HOST=0.0.0.0` и в Adapter — `http://<IP-Windows>:8100/api/v1/math`.

## Endpoint

`POST /api/v1/math/trajectory-intersection` (multipart):

- `trajectory_files` — одно или несколько `.dev` (`MD X Y Z`, ≥2 станции);
- `surface_file` — ASCII CPS3 (`FSNROW`/`FSLIMI`/`->GRID`) или совместимый ZMAP `@GRID`.

Calculation Agent n8n `2.30.8` шлёт до 256 DEV за вызов; пустые reserved slots сервис игнорирует. Ответ — первое пересечение по MD на каждый файл. CRS/единицы/datum/знак Z должны совпадать на входе; сервис координаты не пересчитывает.

Auth и tNavigator runner в MVP отсутствуют: только математика → JSON.

Docker/ручной uvicorn — лаборатория, не полевой канон.
