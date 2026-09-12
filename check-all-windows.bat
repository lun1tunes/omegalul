@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 scripts\field_check.py %*
  exit /b %errorlevel%
)
where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python not found. Install Python 3.11-3.13.
  exit /b 1
)
python scripts\field_check.py %*
exit /b %errorlevel%
