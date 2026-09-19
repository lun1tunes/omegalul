@echo off
setlocal
cd /d "%~dp0"

set "TNAV_CLUSTER_HOST=127.0.0.1"
set "TNAV_CLUSTER_PORT=8400"

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: curl.exe is not available.
  exit /b 1
)

curl.exe --fail --silent --show-error "http://%TNAV_CLUSTER_HOST%:%TNAV_CLUSTER_PORT%/health"
if errorlevel 1 (
  echo.
  echo ERROR: FastAPI is not reachable. Keep start-windows.bat running in another CMD window.
  exit /b 1
)
echo.
echo FastAPI health check passed.
exit /b 0
