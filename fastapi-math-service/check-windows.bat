@echo off
setlocal
cd /d "%~dp0"

set "MATH_SERVICE_HOST=127.0.0.1"
set "MATH_SERVICE_PORT=8100"
if exist "math-service.env" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("math-service.env") do if not "%%A"=="" set "%%A=%%B"
if "%MATH_SERVICE_HOST%"=="0.0.0.0" set "MATH_SERVICE_HOST=127.0.0.1"

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: curl.exe is not available.
  exit /b 1
)

curl.exe --fail --silent --show-error "http://%MATH_SERVICE_HOST%:%MATH_SERVICE_PORT%/health"
if errorlevel 1 (
  echo.
  echo ERROR: Math Service is not reachable. Keep start-windows.bat running in another CMD window.
  exit /b 1
)
echo.
echo Math Service health check passed.
exit /b 0
