@echo off
setlocal
cd /d "%~dp0"

set "MAS_ACTIVITY_HOST=127.0.0.1"
set "MAS_ACTIVITY_PORT=8200"

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: curl.exe is not available.
  exit /b 1
)

echo == /health ==
curl.exe --fail --silent --show-error "http://%MAS_ACTIVITY_HOST%:%MAS_ACTIVITY_PORT%/health"
if errorlevel 1 (
  echo.
  echo ERROR: MAS Activity is not reachable. Keep start-windows.bat running in another CMD window.
  exit /b 1
)
echo.
echo == /ready (n8n webhooks) ==
curl.exe --silent --show-error -w " HTTP %%{http_code}" "http://%MAS_ACTIVITY_HOST%:%MAS_ACTIVITY_PORT%/ready"
echo.
exit /b 0
