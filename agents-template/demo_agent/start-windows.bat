@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "demo-agent.env" (
  echo ERROR: demo-agent.env is missing.
  echo Run: copy demo-agent.env.example demo-agent.env
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("demo-agent.env") do if not "%%A"=="" set "%%A=%%B"

if not defined DEMO_AGENT_HOST set "DEMO_AGENT_HOST=127.0.0.1"
if not defined DEMO_AGENT_PORT set "DEMO_AGENT_PORT=8300"

echo Starting Demo Agent at http://%DEMO_AGENT_HOST%:%DEMO_AGENT_PORT%
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%DEMO_AGENT_HOST%" --port "%DEMO_AGENT_PORT%"
exit /b %errorlevel%
