# Schedule Builder FastAPI

Parse / apply / emit файлов `SCHEDULE` (`.inc` / `.data`). LLM в n8n **не** пишет `.INC`.

**На работе:** только Windows CMD. Полный порядок — [`../docs.md`](../docs.md) §2.

Inspect и `apply_operations` — Python. **Commissioning и group-rebind** эмитят через **Node.js** (`n8n/templates/schedule_timeline_runtime.py`) — тот же код, что golden/combat. Без `node` в PATH сервис может отвечать `/health`, а REVISE дат — нет. В Compose `nodejs` ставится в контейнер.

## Windows CMD

```bat
cd schedule-builder-service
setup-windows.bat
copy schedule-builder.env.example schedule-builder.env
notepad schedule-builder.env
start-windows.bat
```

Проверка: `check-windows.bat` (есть `node` + `/health`).

`ACTIVITY_BASE_URL=http://127.0.0.1:8200` (или IP этого ПК). Файлы: `GET {activity}/cases/{id}/artifacts/{id}`. INCLUDE-пути не переписываются; Petrel `../../INCLUDE/…` оставляют `KEEP`, если тело не приложено.

Порт канона: **8090**. Runtime configuration в n8n: `schedule_service_url=http://<IP>:8090`, `activity_base_url=http://<IP>:8200`.
