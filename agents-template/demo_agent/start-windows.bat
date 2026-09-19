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

echo Starting Demo Agent. Env is loaded from demo-agent.env by Python, not by this .bat.
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m app
exit /b %errorlevel%
