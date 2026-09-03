@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "schedule-builder.env" (
  echo ERROR: schedule-builder.env is missing.
  echo Run: copy schedule-builder.env.example schedule-builder.env
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("schedule-builder.env") do if not "%%A"=="" set "%%A=%%B"

if not defined SCHEDULE_BUILDER_HOST set "SCHEDULE_BUILDER_HOST=127.0.0.1"
if not defined SCHEDULE_BUILDER_PORT set "SCHEDULE_BUILDER_PORT=8090"

echo Starting Schedule Builder at http://%SCHEDULE_BUILDER_HOST%:%SCHEDULE_BUILDER_PORT%
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%SCHEDULE_BUILDER_HOST%" --port "%SCHEDULE_BUILDER_PORT%"
exit /b %errorlevel%
