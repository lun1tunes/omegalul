# Schedule Builder FastAPI

Parse / apply / emit файлов `SCHEDULE` (`.inc` / `.data`). LLM в n8n **не** пишет `.INC`.

**На работе:** Windows CMD, Python 3.11–3.13, `.venv`. Docker и Node.js **не нужны**. Полный порядок — [`../docs.md`](../docs.md) §2.

Commissioning и group-rebind — Python (`app/timeline_ops.py`).

## Windows CMD

```bat
cd schedule-builder-service
setup-windows.bat
copy schedule-builder.env.example schedule-builder.env
notepad schedule-builder.env
start-windows.bat
```

Проверка: `check-windows.bat`.

`ACTIVITY_BASE_URL=http://127.0.0.1:8200` (или IP этого ПК). Файлы: `GET {activity}/cases/{id}/artifacts/{id}`. INCLUDE-пути не переписываются; Petrel `../../INCLUDE/…` оставляют `KEEP`, если тело не приложено.

Порт канона: **8090**. URL в n8n задаётся один раз в `MAS — Runtime Config` (`schedule_service_url`, `activity_base_url`).
