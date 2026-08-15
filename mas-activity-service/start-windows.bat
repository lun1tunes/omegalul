@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "mas-activity.env" (
  echo ERROR: mas-activity.env is missing.
  echo Run: copy mas-activity.env.example mas-activity.env
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("mas-activity.env") do if not "%%A"=="" set "%%A=%%B"

if not defined MAS_ACTIVITY_KEY (
  echo ERROR: MAS_ACTIVITY_KEY is not configured in mas-activity.env.
  exit /b 1
)
if /I "%MAS_ACTIVITY_KEY%"=="change-me-activity-key" (
  echo ERROR: Replace the example MAS_ACTIVITY_KEY in mas-activity.env.
  exit /b 1
)
if not defined MAS_ACTIVITY_HOST set "MAS_ACTIVITY_HOST=127.0.0.1"
if not defined MAS_ACTIVITY_PORT set "MAS_ACTIVITY_PORT=8200"
if not defined HITL_MODE set "HITL_MODE=local"

echo Starting MAS Activity at http://%MAS_ACTIVITY_HOST%:%MAS_ACTIVITY_PORT%
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%MAS_ACTIVITY_HOST%" --port "%MAS_ACTIVITY_PORT%"
exit /b %errorlevel%
