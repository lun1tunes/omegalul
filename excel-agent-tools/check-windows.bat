@echo off
setlocal
cd /d "%~dp0"

set "EXCEL_TOOLS_HOST=127.0.0.1"
set "EXCEL_TOOLS_PORT=8000"
if exist "excel-tools.env" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("excel-tools.env") do if not "%%A"=="" set "%%A=%%B"
if "%EXCEL_TOOLS_HOST%"=="0.0.0.0" set "EXCEL_TOOLS_HOST=127.0.0.1"

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: curl.exe is not available.
  exit /b 1
)

curl.exe --fail --silent --show-error "http://%EXCEL_TOOLS_HOST%:%EXCEL_TOOLS_PORT%/health"
if errorlevel 1 (
  echo.
  echo ERROR: FastAPI is not reachable. Keep start-windows.bat running in another CMD window.
  exit /b 1
)
echo.
echo FastAPI health check passed.
exit /b 0
