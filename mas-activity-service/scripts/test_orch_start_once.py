import asyncio
from pathlib import Path

from app.orchestrator import OrchestratorError, invoke_orchestrator

CASE = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1")
desc = (CASE / "Описание задачи.txt").read_text().strip()
xlsx = (CASE / "MONITORING_well_commissioning_dates.xlsx").read_bytes()
inc_path = next(p for p in CASE.iterdir() if p.suffix.lower() == ".inc" and "_MAS_result" not in p.name)
inc = inc_path.read_bytes()

payload = {
    "entrypoint": "activity_ui",
    "action": "start",
    "task_id": "act_debug_planner",
    "task_description": desc,
    "request_text": desc,
    "request": {
        "objective": desc,
        "problem_statement": desc,
        "task_description": desc,
        "input_files": [
            {
                "field": "file",
                "filename": "MONITORING_well_commissioning_dates.xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            {
                "field": "schedule_files",
                "filename": inc_path.name,
                "mime_type": "application/octet-stream",
            },
        ],
        "build_mode": "AUTO",
    },
    "requested_by": "Debug Planner",
}
files = {
    "file": (
        "MONITORING_well_commissioning_dates.xlsx",
        xlsx,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "schedule_files": (inc_path.name, inc, "application/octet-stream"),
}


async def main() -> None:
    try:
        orch = await invoke_orchestrator(payload, files=files, timeout_s=240)
        print("OK status", orch.get("status"), "task", orch.get("task_id"))
        print("msg", str(orch.get("message") or "")[:500])
        print("gate", bool(orch.get("human_gate")), "version", orch.get("version"))
        print("activity_n", len(orch.get("activity") or []))
    except OrchestratorError as exc:
        print("ERR", exc.status_code, exc)
        detail = getattr(exc, "detail", None)
        print("detail", str(detail)[:800] if detail is not None else None)


if __name__ == "__main__":
    asyncio.run(main())
