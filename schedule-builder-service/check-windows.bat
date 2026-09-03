@echo off
setlocal
cd /d "%~dp0"

set "SCHEDULE_BUILDER_HOST=127.0.0.1"
set "SCHEDULE_BUILDER_PORT=8090"
if exist "schedule-builder.env" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("schedule-builder.env") do if not "%%A"=="" set "%%A=%%B"
if "%SCHEDULE_BUILDER_HOST%"=="0.0.0.0" set "SCHEDULE_BUILDER_HOST=127.0.0.1"

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: curl.exe is not available.
  exit /b 1
)

curl.exe --fail --silent --show-error "http://%SCHEDULE_BUILDER_HOST%:%SCHEDULE_BUILDER_PORT%/health"
if errorlevel 1 (
  echo.
  echo ERROR: Schedule Builder is not reachable. Keep start-windows.bat running in another CMD window.
  exit /b 1
)
echo.
echo Schedule Builder health check passed.
exit /b 0
