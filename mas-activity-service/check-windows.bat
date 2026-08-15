@echo off
setlocal
cd /d "%~dp0"

set "MAS_ACTIVITY_HOST=127.0.0.1"
set "MAS_ACTIVITY_PORT=8200"
if exist "mas-activity.env" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("mas-activity.env") do if not "%%A"=="" set "%%A=%%B"
if "%MAS_ACTIVITY_HOST%"=="0.0.0.0" set "MAS_ACTIVITY_HOST=127.0.0.1"

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: curl.exe is not available.
  exit /b 1
)

curl.exe --fail --silent --show-error "http://%MAS_ACTIVITY_HOST%:%MAS_ACTIVITY_PORT%/health"
if errorlevel 1 (
  echo.
  echo ERROR: MAS Activity is not reachable. Keep start-windows.bat running in another CMD window.
  exit /b 1
)
echo.
echo MAS Activity health check passed.
exit /b 0
