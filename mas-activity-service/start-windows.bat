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

echo Starting MAS Activity. Env is loaded from mas-activity.env by Python, not by this .bat.
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m app
exit /b %errorlevel%
