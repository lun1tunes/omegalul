@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "excel-tools.env" (
  echo ERROR: excel-tools.env is missing.
  echo Run: copy excel-tools.env.example excel-tools.env
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("excel-tools.env") do if not "%%A"=="" set "%%A=%%B"

if not defined API_KEY (
  echo ERROR: API_KEY is not configured in excel-tools.env.
  exit /b 1
)
if /I "%API_KEY%"=="change-me-long-random-excel-tools-key" (
  echo ERROR: Replace the example API_KEY in excel-tools.env.
  exit /b 1
)
if not defined EXCEL_TOOLS_HOST set "EXCEL_TOOLS_HOST=127.0.0.1"
if not defined EXCEL_TOOLS_PORT set "EXCEL_TOOLS_PORT=8000"

if not exist "data\sessions" mkdir "data\sessions"

echo Starting Excel tools at http://%EXCEL_TOOLS_HOST%:%EXCEL_TOOLS_PORT%
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%EXCEL_TOOLS_HOST%" --port "%EXCEL_TOOLS_PORT%"
exit /b %errorlevel%
