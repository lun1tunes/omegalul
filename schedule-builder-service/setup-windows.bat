@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python Launcher "py" was not found. Install Python 3.11-3.13 and enable the launcher.
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 11), (3, 12), (3, 13)) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.11, 3.12 or 3.13 is required.
  py -3 --version
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)

echo Installing Schedule Builder dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo.
echo Ready. Copy schedule-builder.env.example to schedule-builder.env, then run start-windows.bat.
exit /b 0
