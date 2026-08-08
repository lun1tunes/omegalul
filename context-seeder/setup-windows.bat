@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python Launcher "py" was not found. Install Python 3.11-3.13.
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 11), (3, 12), (3, 13)) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.11, 3.12 or 3.13 is required.
  py -3 --version
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo Ready. Copy context-seeder.env.example to context-seeder.env and edit it.
exit /b 0
